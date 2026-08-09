# M0 feasibility spike (2026-08-07)

> **Superseded by `src/tlakit/mcp/` (#23, #22).** Kept as the record of what the
> spike established and when. Do not build on the files here — three of the
> findings below were wrong, and the shim in this directory is the one that
> fails to activate:
>
> - `vscode-stub.js` is missing `workspace.onDidDeleteFiles` and
>   `window.onDidChangeTextEditorSelection`, so `activate()` throws. The spike
>   logged that and carried on, which is how it appeared to work while the
>   diagnostic collection its handlers read was never created.
> - `bootstrap.ts` constructs `MCPServer` itself. `activate()` already does, so
>   with activate fixed this binds two ports. The spike only got away with it
>   because its `activate()` threw first.
> - The server here runs the extension's own TLC (2026.03.19), not the one
>   tlakit pins (2026.07.31).
>
> The maintained version fixes all three: `python -m tlakit.mcp.serve --install`.

Proves the `vscode-tlaplus` MCP server (`src/lm/MCPServer.ts`) can run outside
VSCode, and that Python can drive SANY and TLC through it.

Reproduce:

    git clone --depth 1 https://github.com/tlaplus/vscode-tlaplus.git
    cd vscode-tlaplus && npm install
    cp -r /path/to/docs/spike spike/
    node spike/build.js
    TLA_WORKSPACE=/path/to/specs TLA_MCP_PORT=8931 node out-spike/server.js &
    python3 mcp_client.py /path/to/specs

Result: all 9 MCP tools respond; SANY reports parse errors with line numbers;
TLC reports invariant violations with counterexamples.

Findings that shaped the design (see the design doc):

- MCP tool results are unstructured text; structure comes from `-dumpTrace json`.
- `-dumpTrace json` passes through the `extraOpts` parameter.
- CommunityModules resolve from the bundled jar with no search-path setup.
- `MCPServer.ts` transitively pulls in the TLAPS language client and React
  webview code, which is why the `vscode-languageclient` stub is needed. This is
  the basis of the proposed upstream headless entry point.
