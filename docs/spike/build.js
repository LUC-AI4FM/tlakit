const esbuild = require('esbuild');
const path = require('path');

// Alias the `vscode` module to our Node shim so the extension code can be bundled
// and run outside VSCode.
const vscodeShim = {
    name: 'vscode-shim',
    setup(build) {
        build.onResolve({ filter: /^vscode$/ }, () => ({
            path: path.resolve(__dirname, 'vscode-stub.js'),
        }));
        build.onResolve({ filter: /^vscode-languageclient(\/.*)?$/ }, () => ({
            path: path.resolve(__dirname, 'lsp-stub.js'),
        }));
    },
};

esbuild.build({
    entryPoints: [path.resolve(__dirname, 'bootstrap.ts')],
    bundle: true,
    platform: 'node',
    target: 'node20',
    format: 'cjs',
    outfile: path.resolve(__dirname, '../out-spike/server.js'),
    plugins: [vscodeShim],
    external: ['react', 'react-dom', 'chart.js', 'react-chartjs-2',
        '@vscode-elements/elements', '@vscode-elements/react-elements'],
    logLevel: 'info',
    logOverride: { 'require-resolve-not-external': 'silent' },
}).catch(() => process.exit(1));
