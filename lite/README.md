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

The notebooks link to each other with **root-absolute** app URLs of that shape,
and they have to. JupyterLab's markdown renderer rewrites any relative href
into `/files/<the whole thing, percent-encoded>`, so `[x](examples.ipynb)`
serves raw JSON as a download and `[x](../lab/index.html?path=examples.ipynb)`
404s with the `?` encoded away — measured in both apps on 2026-08-08 and
guarded by `test_links_between_notebooks_open_an_app`. The cost is that these
links assume the site is served from a domain root. It is (`tlakit.pages.dev`,
and `lite/serve.py` locally); deploy it under a subpath and they will need a
prefix.

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
python tools/lite_wheel.py --sync
cd lite && ../.lite-venv/bin/jupyter lite build
python3 lite/serve.py --port 8780      # threaded and no-store on purpose; see serve.py
```

The wheel in `wheels/` is what `%pip install tlakit` resolves inside the page,
so the site needs no PyPI at runtime. Rebuild the wheel whenever tlakit changes
or the notebook installs a stale copy — a library fix does not reach a visitor
until it does.

`jupyter_lite_config.json` names that wheel by filename, which used to make a
version bump a three-step manual edit: build, delete the old wheel, retype the
name in `piplite_urls`. `tools/lite_wheel.py --sync` does all three, and is
safe to re-run — on an up-to-date tree it produces no diff.

Its `--check` half runs in the test suite (`tests/test_lite_wheel.py`), because
this is the one version that fails *quietly*. A stale wheel builds fine and
serves an old tlakit to every visitor. That is not hypothetical: the 0.1.0
wheel was committed, the branch was then rebased onto a main that had gained
two features, and the committed wheel had neither.

`overrides.json` carries JupyterLab settings the site needs changed from their
defaults. The build copies it into the site and patches it into
`jupyter-lite.json` as `settingsOverrides`, so it is the only place a static
build can set something like `autoStartDefaultKernel` — which, with the
`kernelspec` each notebook pins, is what decides whether a visitor is shown a
kernel picker before anything has registered (#70). Both halves are guarded by
`tests/test_lite_notebooks.py`.

## Deploy

```bash
wrangler pages deploy lite-dist --project-name tlakit --branch main
```

## Known issue

The Pyodide kernel does not appear in the launcher when driven through an
automated browser — see issue #53. This reproduces on JupyterLite's own demo
deployment in the same browser, so it is not this build. Verify in an ordinary
browser window.

Re-measured 2026-08-08 while working on #70, and it is wider than the paragraph
above suggests. On `jupyterlite.github.io/demo`, whose config lists
`@jupyterlite/pyodide-kernel-extension` and sets `defaultKernelName: python`,
the launcher offered `JavaScript (Web Worker)` and `p5.js` and no Python at
all, stable across 25 seconds of polling — in a headless browser *and* in an
ordinary desktop Chrome driven through an extension. No console error
accompanies it. Nothing registers, so `refreshSpecs()` does not help either.

The practical cost: **anything about kernel selection has to be checked by
hand.** A build with the picker fixed and a build without it look identical to
any browser we can drive, because in both there is no Python kernel to select.
Do not read a passing automated check as evidence here.
