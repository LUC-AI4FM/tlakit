# Releasing

A release is a `v*` tag. Everything else is automated, and the pipeline is built
to fail *before* it uploads anything rather than half-publish.

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

## Cutting a release

1. Set the version in `pyproject.toml`. There is one version and it lives there;
   the tag is checked against it.
2. Merge that to `main`.
3. Tag and push:

```bash
git tag -a v0.1.0 -m "tlakit 0.1.0" && git push origin v0.1.0
```

4. The `release` workflow builds, verifies, and attests. Then it waits: the
   `pypi` environment has a required reviewer, so **publishing needs a human
   click** on the run page. This is deliberate — pushing a tag should not be
   able to publish on its own.
5. Approving it publishes to PyPI and then creates the GitHub Release with the
   sdist, wheel, and generated notes attached.

`workflow_dispatch` runs the same build and attestation without publishing, for
checking the pipeline itself.

## What the pipeline refuses

| Check | Why |
|---|---|
| tag matches `pyproject` version | a mistagged release would ship the wrong version under the right name |
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
