const path = require('path');
const CopyPlugin = require('copy-webpack-plugin');
const webpack = require('webpack');
const TerserPlugin = require('terser-webpack-plugin');

const { RawSource } = webpack.sources;
const isBun = Boolean(process.versions && process.versions.bun);

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
        // Bake the coarse-gate API key from the build env into the bundle. Empty when
        // FINGPT_API_KEY is unset (local/dev builds) -> getAuthHeaders() returns {}.
        new webpack.DefinePlugin({
            'process.env.FINGPT_API_KEY': JSON.stringify(process.env.FINGPT_API_KEY || ''),
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
