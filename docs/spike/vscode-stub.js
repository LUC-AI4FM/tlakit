// Minimal `vscode` API shim so the extension's MCPServer can run in plain Node.
// Spike only: covers the surface MCPServer.ts + its transitive imports actually touch.
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
            'tlaplus.mcp.port': 0,
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
    env: { appName: 'Node', appHost: 'node', machineId: 'spike', openExternal: async () => true },
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
        createFileSystemWatcher: () => ({
            onDidCreate: () => new Disposable(), onDidChange: () => new Disposable(),
            onDidDelete: () => new Disposable(), dispose: () => {},
        }),
        registerFileSystemProvider: () => new Disposable(),
        asRelativePath: (p) => path.relative(WORKSPACE, typeof p === 'string' ? p : p.fsPath),
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
        registerCompletionItemProvider: () => new Disposable(),
        registerOnTypeFormattingEditProvider: () => new Disposable(),
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
    // Guarded feature-detected in MCPServer.ts; empty object makes the guard fail safe.
    lm: {},
    debug: { registerDebugAdapterDescriptorFactory: () => new Disposable(), startDebugging: async () => false },
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
    CodeActionKind: { QuickFix: { value: 'quickfix' }, Refactor: { value: 'refactor' } },
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
