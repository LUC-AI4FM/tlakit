"""vscode-tlaplus's MCP server as a tlakit backend.

Two halves, and they are useful separately:

- `tlakit.mcp.serve` runs the extension's MCP server without VSCode. It is the
  awkward half -- the server ships inside an extension, so running it headless
  takes a `vscode` shim and an esbuild pass over the extension's own sources.
- `tlakit.mcp.runner.McpRunner` speaks to one, with the same interface as
  `CliRunner`.

Why bother, when `CliRunner` already runs the same TLC: the server does PlusCal
transpilation and the extension's own diagnostic post-processing, and it is what
an editor or agent already talks to. What it is not is a better source of
structure -- its results are prose wrapped around TLC's console output, so
`McpRunner` asks for `-dumpTrace json` through `extraOpts` and reads the file,
exactly as `CliRunner` does.
"""
from __future__ import annotations

from .runner import McpRunner, McpUnavailable
from .serve import McpServeError, McpServer

__all__ = ["McpRunner", "McpServeError", "McpServer", "McpUnavailable"]
