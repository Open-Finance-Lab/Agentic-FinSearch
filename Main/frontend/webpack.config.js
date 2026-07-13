const path = require('path');
const fs = require('fs');
const CopyPlugin = require('copy-webpack-plugin');
const webpack = require('webpack');
const TerserPlugin = require('terser-webpack-plugin');

const { RawSource } = webpack.sources;
const isBun = Boolean(process.versions && process.versions.bun);

// Resolve the coarse-gate FINGPT_API_KEY that gets baked into the bundle.
// Precedence, highest first:
//   1. process.env.FINGPT_API_KEY — an inline `FINGPT_API_KEY=… bun run build:full`,
//      a CI-injected secret, or a value bun auto-loaded from a .env file.
//   2. A `FINGPT_API_KEY=…` line in a gitignored `.env.local` (then `.env`) beside
//      this config — so a release build picks the key up automatically and you never
//      have to paste it on the command line again.
// A machine with none of these still builds a valid KEYLESS dev bundle (no
// Authorization header), which is correct for an auth-open local backend.
function resolveApiKey() {
    if (process.env.FINGPT_API_KEY) {
        return process.env.FINGPT_API_KEY;
    }
    for (const name of ['.env.local', '.env']) {
        let contents;
        try {
            contents = fs.readFileSync(path.join(__dirname, name), 'utf8');
        } catch {
            continue; // file absent — try the next candidate
        }
        const match = contents.match(/^\s*(?:export\s+)?FINGPT_API_KEY\s*=\s*(.*)$/m);
        if (match) {
            // strip trailing whitespace/CR and a single pair of surrounding quotes
            return match[1].trim().replace(/^(['"])(.*)\1$/, '$2');
        }
    }
    return '';
}

const COARSE_GATE_API_KEY = resolveApiKey();
console.log(
    COARSE_GATE_API_KEY
        ? '[webpack] Baking coarse-gate FINGPT_API_KEY into the bundle (KEYED release build).'
        : '[webpack] No FINGPT_API_KEY found (env or .env.local) — building a KEYLESS dev '
          + 'bundle; backend calls will 401 against a key-gated backend.'
);

const escapeNonAscii = (input) => {
    let modified = false;
    let output = '';

    for (const char of input) {
        const codePoint = char.codePointAt(0);
        if (codePoint > 0x7f) {
            modified = true;
            if (codePoint <= 0xffff) {
                output += `\\u${codePoint.toString(16).padStart(4, '0')}`;
            } else {
                const code = codePoint - 0x10000;
                const high = 0xd800 + (code >> 10);
                const low = 0xdc00 + (code & 0x3ff);
                output += `\\u${high.toString(16).padStart(4, '0')}\\u${low.toString(16).padStart(4, '0')}`;
            }
        } else {
            output += char;
        }
    }

    return { output, modified };
};

class EnsureUTF8Plugin {
    constructor(filterFn) {
        this.filterFn = filterFn;
    }

    apply(compiler) {
        compiler.hooks.thisCompilation.tap('EnsureUTF8Plugin', (compilation) => {
            compilation.hooks.processAssets.tap(
                {
                    name: 'EnsureUTF8Plugin',
                    stage: webpack.Compilation.PROCESS_ASSETS_STAGE_OPTIMIZE_TRANSFER,
                },
                (assets) => {
                    Object.keys(assets).forEach((filename) => {
                        if (!this.filterFn(filename)) {
                            return;
                        }
                        const asset = assets[filename];
                        const source = asset.source().toString();
                        const { output, modified } = escapeNonAscii(source);
                        if (modified) {
                            compilation.updateAsset(filename, new RawSource(output));
                            console.log(`[EnsureUTF8Plugin] Sanitized non-ASCII characters in ${filename}`);
                        }
                    });
                }
            );
        });
    }
}

module.exports = {
    entry: './src/main.js',
    mode: 'production',
    output: {
        filename: '[name].js',
        path: path.resolve(__dirname, 'dist'),
    },
    devtool: false,  // Disable source maps to avoid encoding issues
    module: {
        rules: [
            {
                test: /\.js$/,
                exclude: /node_modules/,
                use: {
                    loader: 'babel-loader',
                },
            },
            {
                test: /\.css$/,
                use: [
                    'style-loader',
                    {
                        loader: 'css-loader',
                        options: {
                            url: false // Disable processing of font URLs
                        }
                    }
                ],
            },
            {
                test: /\.(woff|woff2|ttf|eot)$/,
                type: 'asset/inline',
                generator: {
                    dataUrl: () => '', // Return empty string for fonts
                },
            },
        ],
    },
    resolve: {
        extensions: ['.js'],
    },
    optimization: {
        minimize: true,
        minimizer: [
            new TerserPlugin({
                parallel: !isBun, // Bun lacks Worker stdout/stderr/resourceLimits support
            }),
        ],
        splitChunks: false
    },
    performance: {
        hints: "warning"
    },
    plugins: [
        // Bake the resolved coarse-gate API key (see resolveApiKey above) into the
        // bundle. Empty for keyless dev builds -> getAuthHeaders() returns {}.
        new webpack.DefinePlugin({
            'process.env.FINGPT_API_KEY': JSON.stringify(COARSE_GATE_API_KEY),
        }),
        new webpack.BannerPlugin({
            banner: '// @charset "UTF-8";',
            raw: true
        }),
        new EnsureUTF8Plugin((filename) => /\.js$/.test(filename)),
        new CopyPlugin({
          patterns: [
            { from: 'src/manifest.json', to: '.' },
            { from: 'src/assets/', to: 'assets/' },
            { from: 'src/vendor/', to: 'vendor/' },
          ],
        }),
      ],
};
