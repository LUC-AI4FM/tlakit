# M0 feasibility spike (2026-08-07)

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
