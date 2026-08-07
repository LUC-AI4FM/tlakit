"""Run the service: `python -m tlakit.serve --port 8901`.

Binds localhost by default. Public exposure belongs to a reverse proxy that can
rate limit; this process should never be the thing facing the internet.
"""
from __future__ import annotations

import argparse

from .app import create_app


def main(argv: list[str] | None = None) -> int:
    import uvicorn

    parser = argparse.ArgumentParser(prog="python -m tlakit.serve")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8901)
    args = parser.parse_args(argv)

    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
