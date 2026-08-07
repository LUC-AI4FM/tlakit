"""Register the TLA+ kernel with Jupyter: `python -m tlakit.kernel.install`."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path

KERNEL_NAME = "tlakit"
DISPLAY_NAME = "TLA⁺ (tlakit)"


def kernel_spec() -> dict:
    return {
        "argv": [
            sys.executable,
            "-m",
            "tlakit.kernel.launch",
            "-f",
            "{connection_file}",
        ],
        "display_name": DISPLAY_NAME,
        "language": "tlaplus",
        "metadata": {"debugger": False},
    }


def install(user: bool = True, prefix: str | None = None) -> str:
    from jupyter_client.kernelspec import KernelSpecManager

    with tempfile.TemporaryDirectory() as staging:
        Path(staging, "kernel.json").write_text(json.dumps(kernel_spec(), indent=1))
        # Jupyter copies the directory, so 0700 would leave it unreadable.
        Path(staging).chmod(0o755)
        return KernelSpecManager().install_kernel_spec(
            staging, KERNEL_NAME, user=user, prefix=prefix
        )


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(
        prog="python -m tlakit.kernel.install",
        description="Register the TLA+ kernel with Jupyter.",
    )
    parser.add_argument("--user", action="store_true", default=None)
    parser.add_argument("--sys-prefix", action="store_true")
    parser.add_argument("--prefix")
    args = parser.parse_args(argv)

    prefix = sys.prefix if args.sys_prefix else args.prefix
    user = True if (args.user or prefix is None) else False

    path = install(user=user, prefix=prefix)
    print(f"Installed {DISPLAY_NAME} to {path}")
    print("Pick it from Jupyter's kernel list, or use --kernel tlakit.")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
