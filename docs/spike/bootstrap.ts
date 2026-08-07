// Spike: start the extension's TLA+ MCP server as a standalone Node process.
import * as path from 'path';
import * as vscode from 'vscode';
import { MCPServer } from '../src/lm/MCPServer';
import * as main from '../src/main';

const PORT = Number(process.env.TLA_MCP_PORT || 8931);
const EXT_ROOT = path.resolve(__dirname, '..');

const fakeContext: any = {
    subscriptions: [],
    extensionPath: EXT_ROOT,
    extensionUri: vscode.Uri.file(EXT_ROOT),
    asAbsolutePath: (p: string) => path.join(EXT_ROOT, p),
    globalState: { get: () => undefined, update: async () => {}, keys: () => [], setKeysForSync: () => {} },
    workspaceState: { get: () => undefined, update: async () => {}, keys: () => [] },
    storageUri: vscode.Uri.file(path.join(EXT_ROOT, '.spike-storage')),
    globalStorageUri: vscode.Uri.file(path.join(EXT_ROOT, '.spike-storage')),
    logUri: vscode.Uri.file(path.join(EXT_ROOT, '.spike-storage')),
    extensionMode: 2,
    environmentVariableCollection: { replace: () => {}, append: () => {}, prepend: () => {}, clear: () => {} },
    secrets: { get: async () => undefined, store: async () => {}, delete: async () => {} },
};

async function run() {
    // activate() is what creates the diagnostic collection that MCPServer's
    // handlers rely on via getDiagnostic(). Registrations are no-ops under the stub.
    try {
        await (main as any).activate(fakeContext);
        console.error('[spike] activate() completed');
    } catch (err) {
        console.error('[spike] activate() failed (continuing):', (err as Error).message);
    }

    try {
        const server = new MCPServer(PORT);
        console.error(`[spike] MCPServer constructed on port ${PORT}`);
        process.on('SIGINT', () => { (server as any).dispose?.(); process.exit(0); });
        process.on('SIGTERM', () => { (server as any).dispose?.(); process.exit(0); });
    } catch (err) {
        console.error('[spike] MCPServer construction FAILED:', err);
        process.exit(1);
    }
}

run();
