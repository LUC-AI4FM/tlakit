// Bundle vscode-tlaplus's MCP server into one runnable file.
//
// Run from inside the extension checkout, because that is where `esbuild` and
// the extension's own sources resolve from -- `tlakit.mcp.serve` copies this
// directory in and invokes it there.
//
// The output goes to `out/server.js` beside this file rather than into the
// extension's own `out/`, and that placement is load-bearing: the extension
// resolves its jars as `path.resolve(__dirname, '../tools/tla2tools.jar')`, so
// a bundle in `<here>/out/` reads `<here>/tools/`, which is where
// `tlakit.mcp.serve` puts the jar tlakit itself pins. Without that the server
// would silently run the older TLC the extension happens to ship, and any
// disagreement with CliRunner would look like a tlakit bug.
const path = require('path');
const esbuild = require('esbuild');

const HERE = __dirname;

// Alias `vscode` and the TLAPS language client to the shims next to this file,
// so extension code can be bundled and run outside VSCode at all.
const shims = {
    name: 'tlakit-shims',
    setup(build) {
        build.onResolve({ filter: /^vscode$/ }, () => ({
            path: path.resolve(HERE, 'vscode-stub.js'),
        }));
        build.onResolve({ filter: /^vscode-languageclient(\/.*)?$/ }, () => ({
            path: path.resolve(HERE, 'lsp-stub.js'),
        }));
    },
};

esbuild.build({
    entryPoints: [path.resolve(HERE, 'bootstrap.ts')],
    bundle: true,
    platform: 'node',
    target: 'node20',
    format: 'cjs',
    outfile: path.resolve(HERE, 'out', 'server.js'),
    plugins: [shims],
    // The webview half of the extension. Reachable from the import graph, never
    // reached at runtime by the MCP path -- and `react` in a headless bundle
    // would be 100 kB to answer no requests.
    external: [
        'react', 'react-dom', 'chart.js', 'react-chartjs-2',
        '@vscode-elements/elements', '@vscode-elements/react-elements',
    ],
    logLevel: 'warning',
    logOverride: { 'require-resolve-not-external': 'silent' },
}).catch(() => process.exit(1));
