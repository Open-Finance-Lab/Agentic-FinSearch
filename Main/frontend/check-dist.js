// check-dist.js
const fs = require('fs');
const path = require('path');

const distDir = path.resolve(__dirname, 'dist');
const requiredFiles = [
  'manifest.json',
  'main.js',
  'styles.css',
  'vendor/katex.min.js',
  'vendor/marked.min.js',
  'vendor/katex-auto-render.min.js',
  'assets/16x16_icon.png',
  'assets/32x32_icon.png'
]; 

let allGood = true;

console.log(`\nValidating extension build in: ${distDir}\n`);

for (const file of requiredFiles) {
  const filePath = path.join(distDir, file);
  if (!fs.existsSync(filePath)) {
    console.error(`Missing: ${file}`);
    allGood = false;
  } else {
    console.log(`Found: ${file}`);
  }
}

// Report whether the built bundle carries the coarse-gate key, so a silent
// KEYLESS build (which 401s against a key-gated backend) is impossible to miss.
// Informational only — a keyless dev bundle is a legitimate build, not an error.
const mainJsPath = path.join(distDir, 'main.js');
if (fs.existsSync(mainJsPath)) {
  const bundle = fs.readFileSync(mainJsPath, 'utf8');
  const baked = bundle.match(/FINGPT_API_KEY\)?\s*\|\|\s*"((?:[^"\\]|\\.)*)"/);
  const keyed = Boolean(baked && baked[1]);
  console.log(
    keyed
      ? 'Bundle auth: KEYED — sends Authorization: Bearer (ready to publish).'
      : 'Bundle auth: KEYLESS — no Authorization header (dev build; will 401 against a key-gated backend).'
  );
}

if (allGood) {
  console.log(`\nAll required files are present in dist/. Update the plugin in your browser to verify the updates!\n`);
  process.exit(0);
} else {
  console.error(`\nSome files are missing. Make sure Webpack and copy-webpack-plugin ran correctly.\n`);
  process.exit(1);
}
