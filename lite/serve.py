"""Serve the built JupyterLite site for local checking.

Threaded on purpose. JupyterLite routes `/api/*` through a service worker that
talks back to the page, so the page and the worker have requests in flight at
the same time. `python -m http.server` handles one request at a time, which
deadlocks that exchange: `/api/kernelspecs` never resolves, the kernel list
stays empty, and the app sits on its splash screen forever with no error in the
console -- measured 2026-08-08, and it happens with a stock build too, so it is
easy to mistake for a broken site.
"""
from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self) -> None:
        # A stale worker serves the previous build's assets, which looks exactly
        # like a broken rebuild. Never cache during local checking.
        self.send_header("Cache-Control", "no-store, must-revalidate")
        super().end_headers()

    def log_message(self, fmt: str, *args: object) -> None:
        if "/api/" in str(args[0] if args else ""):
            super().log_message(fmt, *args)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8778)
    parser.add_argument(
        "--directory", default=str(Path(__file__).resolve().parent.parent / "lite-dist")
    )
    args = parser.parse_args()

    handler = partial(Handler, directory=args.directory)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), handler)
    print(f"serving {args.directory} on http://127.0.0.1:{args.port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
