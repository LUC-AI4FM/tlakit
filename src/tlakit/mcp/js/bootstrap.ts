// Start vscode-tlaplus's MCP server as a standalone Node process.
//
// Bundled by `build.js` against the `vscode` shim next to this file, and
// launched by `tlakit.mcp.serve`. Everything the extension needs from VSCode
// that is not a stub lives here: a fake ExtensionContext, and the two
// departures below.
//
// Note what this does *not* do: construct `MCPServer`. `activate()` already
// does, when `tlaplus.mcp.port` is a number -- so the shim's configuration
// returns the port and the extension starts its own server exactly as it does
// inside VSCode. The spike constructed one here and appeared to work only
// because its `activate()` threw before reaching that line; with activate
// fixed, doing both binds two ports and registers every tool twice.
import * as net from 'net';
import * as path from 'path';
import * as vscode from 'vscode';
import * as main from '../src/main';

// The extension calls `app.listen(port)`, with no host. Inside VSCode that is
// a developer's own machine; headless it means every interface, and these tools
// run TLC on any path they are handed. So the bind address is pinned rather
// than trusted to the caller's firewall, and binding wider has to be said out
// loud.
//
// `tlakit.mcp.serve` always sets TLAKIT_MCP_HOST, so in practice its own
// `--host` is what governs and this default only applies to someone running the
// bundle with `node` directly. Both default to loopback: whichever way in, the
// answer is the same.
const HOST = process.env.TLAKIT_MCP_HOST || '127.0.0.1';

let announced = false;
const originalListen = net.Server.prototype.listen;
// eslint-disable-next-line @typescript-eslint/no-explicit-any
(net.Server.prototype as any).listen = function (...args: any[]) {
    // `listen(port, cb)` -- the only shape the extension uses. Anything else
    // already names its own host, or is a pipe, so leave it alone.
    if (typeof args[0] === 'number' && (args.length === 1 || typeof args[1] === 'function')) {
        const callback = args[1];
        // eslint-disable-next-line @typescript-eslint/no-this-alias
        const server = this;
        return originalListen.call(server, args[0], HOST, function (this: unknown) {
            if (!announced) {
                announced = true;
                // One machine-readable line on stdout, carrying the port the
                // socket actually got. `tlakit.mcp.serve` waits for it instead
                // of sleeping and hoping -- and with `--port 0` it is the only
                // way to learn which port that was.
                const address = server.address();
                const port = address && typeof address === 'object' ? address.port : args[0];
                console.log(`[tlakit] mcp ready on ${HOST}:${port}`);
            }
            if (callback) {
                callback.apply(this, arguments as never);
            }
        });
    }
    return originalListen.apply(this, args as never);
};

const EXT_ROOT = path.resolve(__dirname, '..');
const STORAGE = path.join(EXT_ROOT, 'storage');

// eslint-disable-next-line @typescript-eslint/no-explicit-any
const fakeContext: any = {
    subscriptions: [],
    extensionPath: EXT_ROOT,
    extensionUri: vscode.Uri.file(EXT_ROOT),
    asAbsolutePath: (p: string) => path.join(EXT_ROOT, p),
    globalState: { get: () => undefined, update: async () => {}, keys: () => [], setKeysForSync: () => {} },
    workspaceState: { get: () => undefined, update: async () => {}, keys: () => [] },
    storageUri: vscode.Uri.file(STORAGE),
    globalStorageUri: vscode.Uri.file(STORAGE),
    logUri: vscode.Uri.file(STORAGE),
    extensionMode: 2,
    environmentVariableCollection: { replace: () => {}, append: () => {}, prepend: () => {}, clear: () => {} },
    secrets: { get: async () => undefined, store: async () => {}, delete: async () => {} },
};

async function run() {
    // `activate()` creates the diagnostic collection that MCPServer's handlers
    // read through `getDiagnostic()`, and starts the server. The spike logged a
    // failure here and carried on, and the tools appeared to work -- but that is
    // the path by which diagnostics come back silently empty. A shim that
    // half-activates is worse than one that refuses, so this exits.
    try {
        // eslint-disable-next-line @typescript-eslint/no-explicit-any
        await (main as any).activate(fakeContext);
    } catch (err) {
        console.error(
            '[tlakit] activate() failed, so diagnostics would come back empty. '
            + 'The vscode shim is missing something this version of the extension '
            + 'needs:\n  '
            + ((err as Error).stack || (err as Error).message),
        );
        process.exit(1);
    }

    if (!announced) {
        console.error(
            '[tlakit] activate() completed but nothing started listening. '
            + 'The extension starts its MCP server only when tlaplus.mcp.port is a '
            + 'number; check that the vscode shim still answers that setting.',
        );
        process.exit(1);
    }

    const shutdown = () => {
        for (const disposable of fakeContext.subscriptions) {
            try {
                disposable?.dispose?.();
            } catch {
                // Shutting down; a disposable that objects is not worth failing over.
            }
        }
        process.exit(0);
    };
    process.on('SIGINT', shutdown);
    process.on('SIGTERM', shutdown);
}

run();
