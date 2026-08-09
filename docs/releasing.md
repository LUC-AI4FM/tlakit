# Releasing

A release is a `v*` tag. Everything else is automated, and the pipeline is built
to fail *before* it uploads anything rather than half-publish.

A push to `main` runs the same pipeline with a computed version and sends the
result to TestPyPI instead, so the release path is exercised on every commit
rather than only on the day it matters.

| Trigger | Version | Goes to |
|---|---|---|
| tag `v0.1.0` | `0.1.0`, checked against the files | PyPI, then a GitHub Release |
| push to `main` | `0.1.1.dev47` — last tag with the patch bumped, `.dev` plus the commit count | TestPyPI |

Both run the whole verification chain below. The only difference is where the
artifacts land and whether a human has to approve it.

## One-time setup on PyPI

This is the only step that cannot be done from here, because it happens in
PyPI's own account settings. Until it exists, the `publish` job will fail with
`invalid-publisher`.

Go to <https://pypi.org/manage/account/publishing/> and add a **pending trusted
publisher**:

| Field | Value |
|---|---|
| PyPI project name | `tlakit` |
| Owner | `LUC-AI4FM` |
| Repository name | `tlakit` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

"Pending" is correct: the project does not exist yet, and the first successful
publish creates it. Nothing is stored in GitHub secrets — PyPI verifies a
short-lived OIDC token from the workflow, so there is no long-lived API token
that could be exfiltrated.

The name was free as of 2026-08-08 (`pypi.org/pypi/tlakit/json` → 404). Claim it
before announcing anything.

## One-time setup on TestPyPI

The dev stream needs the same thing again on the other index, because TestPyPI
is a separate site with separate accounts — a publisher configured on PyPI means
nothing there. Until it exists, `publish-dev` fails on every push to `main` with
`invalid-publisher`.

Go to <https://test.pypi.org/manage/account/publishing/> and add a pending
trusted publisher with the same five values, except the environment:

| Field | Value |
|---|---|
| PyPI project name | `tlakit` |
| Owner | `LUC-AI4FM` |
| Repository name | `tlakit` |
| Workflow name | `release.yml` |
| Environment name | `testpypi` |

Then create the `testpypi` environment in the repository's settings and leave it
with **no required reviewer**. That is the one deliberate asymmetry with `pypi`:
a dev build that waits for a click is a dev build nobody gets.

## The dev stream

Every push to `main` publishes to TestPyPI:

```bash
pip install --index-url https://test.pypi.org/simple/ \
            --extra-index-url https://pypi.org/simple/ --pre tlakit
```

The extra index is not optional — TestPyPI does not carry `platformdirs` or
anything else tlakit depends on, so without it the install fails on
dependencies rather than on tlakit.

`tools/version.py --write-dev` computes the version, from the last release tag
reachable from `HEAD` and `git rev-list --count HEAD`:

```
v0.1.0 + 47 commits  ->  0.1.1.dev47
```

It writes that into `pyproject.toml` and `src/tlakit/__init__.py` inside the
runner's checkout and nothing is pushed back, so `main` never carries a version
bump commit and the two files stay in agreement — which is the invariant
`tests/test_version.py` guards.

Two properties are worth stating because they are the reason for this shape.
The commit count only goes up, so a later push always sorts above an earlier
one. And `.dev` is a PEP 440 developmental release, so `pip install tlakit`
never resolves to one by accident; it takes `--pre` to ask for it.

## Cutting a release

1. Set the version in `pyproject.toml` **and** `src/tlakit/__init__.py`. Both
   are checked against the tag, and against each other by
   `tests/test_version.py`.
2. Update `lite/` to match: build the wheel, replace the one in `lite/wheels/`,
   and point `piplite_urls` in `lite/jupyter_lite_config.json` at the new
   filename. A stale wheel there is shipped silently — see `lite/README.md`.
3. Merge that to `main`.
4. Tag and push:

```bash
git tag -a v0.1.0 -m "tlakit 0.1.0" && git push origin v0.1.0
```

5. The `release` workflow builds, verifies, and attests. Then it waits: the
   `pypi` environment has a required reviewer, so **publishing needs a human
   click** on the run page. This is deliberate — pushing a tag should not be
   able to publish on its own.
6. Approving it publishes to PyPI and then creates the GitHub Release with the
   sdist, wheel, and generated notes attached.

`workflow_dispatch` runs the same build and attestation without publishing, for
checking the pipeline itself.

## What the pipeline refuses

| Check | Why |
|---|---|
| the tag is an ancestor of `main` | tagging the release PR's own branch builds a release missing whatever merged since it was cut — and because merging is squash-only, that commit never joins main's history, so `git describe` stops seeing the tag and dev versions keep counting from the release before it |
| tag matches `pyproject` version *and* `__version__` | a mistagged release would ship the wrong version under the right name |
| `twine check --strict` | PyPI rejects a bad long_description *after* upload starts, leaving a half-published release |
| install the wheel, import it from outside the source tree | a missing module or missing package data passes the test suite and fails only here |
| rebuild the wheel from the sdist | a sdist that cannot rebuild is not a source distribution |

That fourth one is not hypothetical: building the sdist for the first time swept
in the JupyterLite build venv and its 64 MB of output.

## Verifying a release afterwards

Artifacts carry signed provenance tying them to this workflow and commit:

```bash
gh attestation verify tlakit-0.1.0-py3-none-any.whl --repo LUC-AI4FM/tlakit
```

Release tags are covered by a ruleset that forbids deletion and
non-fast-forward updates, so a published tag cannot be moved under a release
that already shipped.
