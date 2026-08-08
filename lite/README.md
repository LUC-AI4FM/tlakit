# The browser build

A static JupyterLite site. The Python kernel runs in the visitor's own tab
(Pyodide), and TLC runs on the public runner — so this page needs no Java, no
install, and no account, and nothing a visitor types executes on our machines.

## Build and serve

```bash
uv venv .lite-venv && uv pip install --python .lite-venv/bin/python -r lite/requirements-build.txt
uv build --wheel -o dist && cp dist/tlakit-*.whl lite/wheels/
cd lite && ../.lite-venv/bin/jupyter lite build
python3 lite/serve.py --port 8780      # threaded and no-store on purpose; see serve.py
```

The wheel in `wheels/` is what `%pip install tlakit` resolves inside the page,
so the site needs no PyPI at runtime. Rebuild the wheel whenever tlakit changes
or the notebook installs a stale copy.

## Deploy

```bash
wrangler pages deploy lite-dist --project-name tlakit --branch main
```

## Known issue

The Pyodide kernel does not appear in the launcher when driven through an
automated browser — see issue #53. This reproduces on JupyterLite's own demo
deployment in the same browser, so it is not this build. Verify in an ordinary
browser window.
