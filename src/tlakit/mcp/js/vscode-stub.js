// Minimal `vscode` API shim so the extension's MCPServer can run in plain Node.
//
// vscode-tlaplus ships its MCP server as an express app inside a VSCode
// extension, so importing it drags in the TLAPS language client and the React
// webviews. This covers the surface `activate()` and `MCPServer.ts` actually
// touch -- no more, so that a version of the extension which reaches for
// something new fails loudly here rather than half-activating.
//
// The surface was taken from the extension rather than guessed, over all of
// `src/` and not just `main.ts` -- `activate()` constructs objects whose own
// constructors register things, which is how the spike came to be missing
// `window.onDidChangeTextEditorSelection`:
//
//     grep -rhoE 'vscode\.(window|workspace|languages|commands|debug|tasks|extensions|env|lm)\.[a-zA-Z]+' src | sort -u
//
// `tests/test_mcp_serve.py` runs that comparison as a test, so a new extension
// version that reaches for something new fails there rather than at startup.
//
// One name is deliberately absent: `lm.registerMcpServerDefinitionProvider`.
// `MCPServer.ts` feature-detects it to announce itself to VSCode's own MCP
// registry, and there is no VSCode here to announce to. `lm.registerTool` *is*
// here, because `activate()` calls it unguarded and would otherwise throw --
// which is what "activate() failed (continuing)" was in the spike.
//
// The editor-only surface (`showTextDocument`, `showOpenDialog`, `findFiles`,
// `activeDebugSession`) is stubbed to "there is no UI here" rather than left
// out. All of it lives in the go-to-declaration provider, the webview panels
// and the debugger -- none of which the MCP request path reaches -- but
// `activate()` constructs the objects that own them, so a missing name is a
// startup failure rather than a feature nobody called.
const fs = require('fs');
const path = require('path');
const { URI } = require('vscode-uri');

const WORKSPACE = process.env.TLA_WORKSPACE || process.cwd();

class Position {
    constructor(line, character) { this.line = line; this.character = character; }
}
class Range {
    constructor(a, b, c, d) {
        if (typeof a === 'number') { this.start = new Position(a, b); this.end = new Position(c, d); }
        else { this.start = a; this.end = b; }
    }
}
class Location {
    constructor(uri, range) { this.uri = uri; this.range = range; }
}
class Diagnostic {
    constructor(range, message, severity) { this.range = range; this.message = message; this.severity = severity; }
}
class Disposable {
    constructor(fn) { this._fn = fn; }
    dispose() { if (this._fn) { this._fn(); } }
}
Disposable.from = (...items) => new Disposable(() => items.forEach((i) => i && i.dispose && i.dispose()));

class EventEmitter {
    constructor() { this._listeners = []; }
    get event() { return (l) => { this._listeners.push(l); return new Disposable(() => {}); }; }
    fire(e) { this._listeners.forEach((l) => l(e)); }
    dispose() { this._listeners = []; }
}
class CancellationTokenSource {
    constructor() { this.token = { isCancellationRequested: false, onCancellationRequested: () => new Disposable() }; }
    cancel() { this.token.isCancellationRequested = true; }
    dispose() {}
}

function makeTextDocument(uri) {
    const text = fs.readFileSync(uri.fsPath, 'utf8');
    const lines = text.split('\n');
    return {
        uri,
        fileName: uri.fsPath,
        languageId: uri.fsPath.endsWith('.cfg') ? 'tlaplus_cfg' : 'tlaplus',
        version: 1,
        lineCount: lines.length,
        getText: (range) => {
            if (!range) { return text; }
            return lines.slice(range.start.line, range.end.line + 1).join('\n');
        },
        lineAt: (l) => {
            const n = typeof l === 'number' ? l : l.line;
            return { text: lines[n] || '', lineNumber: n, range: new Range(n, 0, n, (lines[n] || '').length) };
        },
        positionAt: (offset) => {
            const before = text.slice(0, offset).split('\n');
            return new Position(before.length - 1, before[before.length - 1].length);
        },
        offsetAt: () => 0,
        save: async () => true,
        isDirty: false,
        isUntitled: false,
        isClosed: false,
    };
}

const outputChannel = () => ({
    name: 'stub',
    appendLine: (m) => process.env.TLA_VERBOSE && console.error('[out]', m),
    append: (m) => process.env.TLA_VERBOSE && process.stderr.write(m),
    clear: () => {}, show: () => {}, hide: () => {}, dispose: () => {}, replace: () => {},
});

const config = {
    get: (key, dflt) => {
        // Defaults the extension would otherwise read from VSCode settings.
        const overrides = {
            'tlaplus.java.home': process.env.JAVA_HOME || undefined,
            'tlaplus.java.options': undefined,
            'tlaplus.tlc.statisticsSharing': 'doNotShare',
            // What makes `activate()` start the MCP server, and on which port.
            // 0 means "any free port", which the bootstrap reports back.
            'tlaplus.mcp.port': Number(process.env.TLA_MCP_PORT || 8931),
            'tlaplus.mcp.enableFilesystemTools': false,
            'tlaplus.mcp.enableKnowledgeBaseTools': true,
        };
        if (key in overrides && overrides[key] !== undefined) { return overrides[key]; }
        return dflt;
    },
    update: async () => {},
    has: () => false,
    inspect: () => undefined,
};

const vscode = {
    Uri: {
        file: (p) => URI.file(p),
        parse: (s) => URI.parse(s),
        joinPath: (base, ...segs) => URI.file(path.join(base.fsPath, ...segs)),
    },
    Position, Range, Location, Diagnostic, Disposable, EventEmitter, CancellationTokenSource,
    DiagnosticSeverity: { Error: 0, Warning: 1, Information: 2, Hint: 3 },
    FileType: { Unknown: 0, File: 1, Directory: 2, SymbolicLink: 64 },
    ViewColumn: { Active: -1, Beside: -2, One: 1, Two: 2 },
    SymbolKind: { File: 0, Module: 1, Namespace: 2, Function: 11, Variable: 12, Constant: 13, Operator: 24 },
    DocumentSymbol: class { constructor(name, detail, kind, range, selectionRange) {
        Object.assign(this, { name, detail, kind, range, selectionRange, children: [] }); } },
    SymbolInformation: class { constructor(name, kind, containerName, location) {
        Object.assign(this, { name, kind, containerName, location }); } },
    ThemeColor: class { constructor(id) { this.id = id; } },
    env: { appName: 'Node', appHost: 'node', machineId: 'tlakit', openExternal: async () => true },
    workspace: {
        workspaceFolders: [{ uri: URI.file(WORKSPACE), name: path.basename(WORKSPACE), index: 0 }],
        name: path.basename(WORKSPACE),
        getConfiguration: () => config,
        openTextDocument: async (u) => makeTextDocument(typeof u === 'string' ? URI.file(u) : u),
        textDocuments: [],
        onDidChangeConfiguration: () => new Disposable(),
        onDidSaveTextDocument: () => new Disposable(),
        onDidChangeTextDocument: () => new Disposable(),
        onDidOpenTextDocument: () => new Disposable(),
        onDidCloseTextDocument: () => new Disposable(),
        // `activate()` registers these two to keep its diagnostics in step with
        // renames and deletions. Nothing renames a file under this server, but
        // their absence is what made `activate()` throw.
        onDidDeleteFiles: () => new Disposable(),
        onDidRenameFiles: () => new Disposable(),
        onDidCreateFiles: () => new Disposable(),
        onDidChangeWorkspaceFolders: () => new Disposable(),
        createFileSystemWatcher: () => ({
            onDidCreate: () => new Disposable(), onDidChange: () => new Disposable(),
            onDidDelete: () => new Disposable(), dispose: () => {},
        }),
        registerFileSystemProvider: () => new Disposable(),
        asRelativePath: (p) => path.relative(WORKSPACE, typeof p === 'string' ? p : p.fsPath),
        // Both belong to the go-to-declaration provider, which needs an editor
        // to be invoked from. One workspace folder is all this server has.
        findFiles: async () => [],
        getWorkspaceFolder: () => ({
            uri: URI.file(WORKSPACE), name: path.basename(WORKSPACE), index: 0,
        }),
        fs: {
            stat: async (u) => { const s = fs.statSync(u.fsPath);
                return { type: s.isDirectory() ? 2 : 1, ctime: s.ctimeMs, mtime: s.mtimeMs, size: s.size }; },
            readFile: async (u) => new Uint8Array(fs.readFileSync(u.fsPath)),
            writeFile: async (u, c) => fs.writeFileSync(u.fsPath, Buffer.from(c)),
            readDirectory: async (u) => fs.readdirSync(u.fsPath, { withFileTypes: true })
                .map((d) => [d.name, d.isDirectory() ? 2 : 1]),
            createDirectory: async (u) => fs.mkdirSync(u.fsPath, { recursive: true }),
            delete: async (u) => fs.rmSync(u.fsPath, { recursive: true, force: true }),
        },
    },
    window: {
        createOutputChannel: outputChannel,
        showErrorMessage: async (m) => { console.error('[error]', m); return undefined; },
        showWarningMessage: async (m) => { console.error('[warn]', m); return undefined; },
        showInformationMessage: async (m) => { console.error('[info]', m); return undefined; },
        showQuickPick: async () => undefined,
        showInputBox: async () => undefined,
        createTerminal: () => ({ show: () => {}, sendText: () => {}, dispose: () => {} }),
        createWebviewPanel: () => ({
            webview: { html: '', postMessage: async () => true, onDidReceiveMessage: () => new Disposable(),
                asWebviewUri: (u) => u, cspSource: '' },
            onDidDispose: () => new Disposable(), reveal: () => {}, dispose: () => {},
        }),
        activeTextEditor: undefined,
        visibleTextEditors: [],
        // Views and terminals `activate()` registers. Headless there is nothing
        // to show them in, so registering is the whole behaviour.
        registerTreeDataProvider: () => new Disposable(),
        registerWebviewViewProvider: () => new Disposable(),
        registerTerminalProfileProvider: () => new Disposable(),
        registerFileDecorationProvider: () => new Disposable(),
        onDidChangeActiveTextEditor: () => new Disposable(),
        // TlapsClient's constructor registers this, so `activate()` needs it.
        onDidChangeTextEditorSelection: () => new Disposable(),
        onDidChangeTextEditorVisibleRanges: () => new Disposable(),
        createTextEditorDecorationType: () => ({ key: 'stub', dispose: () => {} }),
        // No UI to show or ask anything in. Answering "the user picked nothing"
        // is the truthful result rather than a pretence of success.
        showTextDocument: async () => undefined,
        showOpenDialog: async () => undefined,
        showSaveDialog: async () => undefined,
        withProgress: async (_o, task) => task({ report: () => {} }, { isCancellationRequested: false }),
        setStatusBarMessage: () => new Disposable(),
        createStatusBarItem: () => ({ show: () => {}, hide: () => {}, dispose: () => {}, text: '' }),
    },
    languages: {
        createDiagnosticCollection: () => {
            const store = new Map();
            return {
                name: 'tlaplus',
                set: (u, d) => { if (u && u.toString) { store.set(u.toString(), d); } },
                get: (u) => store.get(u.toString()),
                delete: (u) => store.delete(u.toString()),
                clear: () => store.clear(), dispose: () => store.clear(),
                forEach: (cb) => store.forEach((v, k) => cb(URI.parse(k), v)),
            };
        },
        registerDocumentSymbolProvider: () => new Disposable(),
        registerDocumentFormattingEditProvider: () => new Disposable(),
        registerCompletionItemProvider: () => new Disposable(),
        registerOnTypeFormattingEditProvider: () => new Disposable(),
        registerCodeActionsProvider: () => new Disposable(),
        registerDeclarationProvider: () => new Disposable(),
        registerDefinitionProvider: () => new Disposable(),
        registerEvaluatableExpressionProvider: () => new Disposable(),
        registerHoverProvider: () => new Disposable(),
        setTextDocumentLanguage: async (doc) => doc,
    },
    commands: {
        registerCommand: () => new Disposable(),
        registerTextEditorCommand: () => new Disposable(),
        executeCommand: async () => undefined,
        getCommands: async () => [],
    },
    extensions: {
        getExtension: () => ({
            extensionPath: path.resolve(__dirname, '..'),
            extensionUri: URI.file(path.resolve(__dirname, '..')),
            packageJSON: { version: '0.0.0-spike' },
            isActive: true,
        }),
        all: [],
    },
    // `registerTool` is called unguarded by `activate()`, so it has to exist.
    // `registerMcpServerDefinitionProvider` is deliberately absent:
    // `MCPServer.ts` feature-detects it to register itself with VSCode's own MCP
    // registry, and there is no VSCode here to register with.
    lm: { registerTool: () => new Disposable() },
    debug: {
        activeDebugSession: undefined,
        registerDebugAdapterDescriptorFactory: () => new Disposable(),
        registerDebugAdapterTrackerFactory: () => new Disposable(),
        startDebugging: async () => false,
        onDidStartDebugSession: () => new Disposable(),
        onDidTerminateDebugSession: () => new Disposable(),
    },
    tasks: { registerTaskProvider: () => new Disposable() },
    ProgressLocation: { Notification: 15, Window: 10 },
    ConfigurationTarget: { Global: 1, Workspace: 2 },
    TextEdit: class { constructor(range, newText) { this.range = range; this.newText = newText; } },
    CompletionItem: class { constructor(label, kind) { this.label = label; this.kind = kind; } },
    CompletionItemKind: { Text: 0, Function: 2, Keyword: 13, Operator: 23 },
    MarkdownString: class { constructor(v) { this.value = v || ''; } appendMarkdown(v) { this.value += v; return this; } },
    RelativePattern: class { constructor(base, pattern) { this.base = base; this.pattern = pattern; } },

    // Remaining surface referenced by the bundle (enums + value classes).
    StatusBarAlignment: { Left: 1, Right: 2 },
    TreeItemCollapsibleState: { None: 0, Collapsed: 1, Expanded: 2 },
    TreeItem: class { constructor(label, state) { this.label = label; this.collapsibleState = state; } },
    ThemeIcon: class { constructor(id, color) { this.id = id; this.color = color; } },
    Selection: class extends Range {},
    CancellationError: class extends Error { constructor() { super('Canceled'); this.name = 'Canceled'; } },
    FileSystemError: class extends Error {
        static FileNotFound(m) { return new Error(`FileNotFound: ${m}`); }
        static FileExists(m) { return new Error(`FileExists: ${m}`); }
        static NoPermissions(m) { return new Error(`NoPermissions: ${m}`); }
    },
    CodeAction: class { constructor(title, kind) { this.title = title; this.kind = kind; } },
    CodeActionKind: {
        QuickFix: { value: 'quickfix' },
        Refactor: { value: 'refactor' },
        Source: { value: 'source' },
    },
    CompletionList: class { constructor(items, isIncomplete) { this.items = items || []; this.isIncomplete = !!isIncomplete; } },
    EvaluatableExpression: class { constructor(range, expression) { this.range = range; this.expression = expression; } },
    DebugAdapterServer: class { constructor(port, host) { this.port = port; this.host = host; } },
    DecorationRangeBehavior: { ClosedClosed: 1, OpenOpen: 0 },
    OverviewRulerLane: { Left: 1, Center: 2, Right: 4, Full: 7 },
    TextEditorRevealType: { Default: 0, InCenter: 1, AtTop: 3 },
    NotebookCellKind: { Markup: 1, Code: 2 },
    LanguageModelTextPart: class { constructor(value) { this.value = value; } },
    LanguageModelToolResult: class { constructor(content) { this.content = content || []; } },
    McpHttpServerDefinition: class { constructor(label, uri) { this.label = label; this.uri = uri; } },
};

module.exports = vscode;
