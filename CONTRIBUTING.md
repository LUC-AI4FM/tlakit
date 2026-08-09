# Contributing to tlakit

This guide explains how to set up your environment, run the test suite, and build the documentation for tlakit.

> [!NOTE]
> This contributor setup is derived directly from the automated checks in [.github/workflows/ci.yml](file:///c:/Users/avira/tlakit/.github/workflows/ci.yml). Refer to the workflow file for the authoritative environment requirements.

## Development Setup

### 1. Prerequisites

- **Python 3.10+**
- **Java Runtime Environment (JRE) / JDK (v11 or newer)**: Required to run SANY, TLC, and other TLA+ Java tools. tlakit pins TLA+ tools v1.8.0+, which cannot run on older Java runtimes (such as Java 8 / class file version 52).
- **Node.js**: (Optional) Required only if you are working on the vscode-tlaplus integration or MCP server backend.

### 2. Install Development Dependencies

tlakit uses `pyproject.toml` to define its package and optional dependency groups. For development and testing, install the package in editable mode with the `dev` and `notebook` extras:

```bash
pip install --upgrade pip
pip install -e ".[dev,notebook]"
```

### 3. Fetching the Pinned Jars

tlakit executes the TLA+ Java tools using pinned releases of `tla2tools.jar` and `CommunityModules-deps.jar`. Rather than configuring environment variables manually, the easiest way to fetch and cache these jars is by running the installation script:

```bash
python -m tlakit.install
```

This download is secure, checksummed, and version-isolated (see [install.py](file:///c:/Users/avira/tlakit/src/tlakit/install.py) and [jar.py](file:///c:/Users/avira/tlakit/src/tlakit/jar.py)). tlakit will resolve the cached jars automatically without additional configuration.

If you have multiple Java runtimes installed and need to specify a particular Java executable (or override the one on your `PATH`), set the `TLAKIT_JAVA` environment variable:
```bash
# Windows
set TLAKIT_JAVA="C:\path\to\java.exe"

# Linux / macOS
export TLAKIT_JAVA="/path/to/java"
```

---

## Running the Test Suite

Run the tests using `pytest`:

```bash
python -m pytest -q
```

### Understanding Skip Markers

tlakit defines several custom test markers in [pyproject.toml](file:///c:/Users/avira/tlakit/pyproject.toml):
- `java`: For tests that execute Java commands or depend on `tla2tools.jar`.
- `apalache`: For model-checking tests targeting the Apalache symbolic model checker.
- `tlaps`: For proof-checking tests using the TLAPS proof assistant.
- `mcp`: For VS Code MCP server integrations.

> [!IMPORTANT]
> **Avoid Silent Skips!**
> If you run `pytest -q` without a JRE or the pinned jars in place, the Java-marked tests will be skipped. Pytest will report a green status (`passed`), which proves nothing about the code changes.
>
> To verify that the Java integration tests are genuinely running, execute:
> ```bash
> python -m pytest -q -m java
> ```
> This command forces execution of the Java-marked tests. It must pass with no skipped tests to ensure changes to Java-dependent features are verified.

### Optional Markers (Apalache & TLAPS)
- The `apalache` and `tlaps` markers require separate external binaries installed on your system (configured via `TLAKIT_APALACHE` and `TLAKIT_TLAPM` respectively).
- A local contributor's run is expected to skip these unless they have installed and configured those specific tools.

---

## Building the Documentation

The documentation site is built using MkDocs. The build dependencies are defined under the `docs`, `notebook`, `widget`, and `serve` extras.

1. Install documentation dependencies:
   ```bash
   pip install -e ".[docs,notebook,widget,serve]"
   ```

2. Build and preview the site locally:
   ```bash
   python -m mkdocs serve
   ```

3. Validate the site before proposing changes (strict mode turns broken links into failures):
   ```bash
   python -m mkdocs build --strict
   ```

*(Note: Design spikes and architecture documents located in `docs/superpowers/` and `docs/spike/` are excluded from the main documentation site on purpose.)*
