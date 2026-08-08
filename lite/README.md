# The browser build

A static JupyterLite site. The Python kernel runs in the visitor's own tab
(Pyodide), and TLC runs on the public runner — so this page needs no Java, no
install, and no account, and nothing a visitor types executes on our machines.

## The pages

Everything in `files/` is copied into the site and shows up in the file browser.

| Notebook | What it is |
| --- | --- |
| `tla-in-your-browser.ipynb` | The tutorial. A lost update, then the atomic fix. |
| `examples.ipynb` | A real deadlock, a six-state graph, and a liveness property that fails. |
| `scratch.ipynb` | An empty page with the setup cell written. No prose. |

`scratch.ipynb` is the one to link when someone just wants to check a spec.
Open it through the single-document app rather than Lab and there is no
sidebar, no launcher, and nothing to read:

```
https://<site>/notebooks/index.html?path=scratch.ipynb
```

`tests/test_lite_notebooks.py` guards these in two layers. Structurally: a
config cell that names no module, `%pip` sharing a cell with code that needs
what it installs, an option the service refuses, and a terminating spec checked
without `CHECK_DEADLOCK FALSE` — which reports a correct specification as
deadlocked. Then, against a local TLC with CommunityModules refused: every
module through SANY, and every config cell compared against the outcome the
notebook's prose promises, which is written down in `EXPECTED_OUTCOMES`. Add a
config cell and that table has to grow with it.

What none of that covers is the Python cells and the network path — the
`spec.check(...)` calls, and whether the live service agrees with a local TLC.
Run all three notebooks end to end against the real runner before a deploy that
changes a spec.

## Build and serve

```bash
uv venv .lite-venv && uv pip install --python .lite-venv/bin/python -r lite/requirements-build.txt
uv build --wheel -o dist && cp dist/tlakit-*.whl lite/wheels/
cd lite && ../.lite-venv/bin/jupyter lite build
python3 lite/serve.py --port 8780      # threaded and no-store on purpose; see serve.py
```

The wheel in `wheels/` is what `%pip install tlakit` resolves inside the page,
so the site needs no PyPI at runtime. Rebuild the wheel whenever tlakit changes
or the notebook installs a stale copy — a library fix does not reach a visitor
until it does.

`jupyter_lite_config.json` names that wheel by filename, so bumping the version
in `pyproject.toml` means deleting the old wheel and editing `piplite_urls` to
match. Leave the stale one in place and the build keeps shipping it, silently.

## Deploy

```bash
wrangler pages deploy lite-dist --project-name tlakit --branch main
```

## Known issue

The Pyodide kernel does not appear in the launcher when driven through an
automated browser — see issue #53. This reproduces on JupyterLite's own demo
deployment in the same browser, so it is not this build. Verify in an ordinary
browser window.
