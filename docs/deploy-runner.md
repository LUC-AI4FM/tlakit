# Deploying the runner

Two deploy paths exist and they are easy to confuse. Getting this wrong is how a
tested, committed, documented CORS fix stayed absent from production on
2026-08-08: the browser could not preflight `POST /check`, and the notebook
failed with `NetworkError: Failed to execute 'send' on 'XMLHttpRequest'`.

| What | Where it lives | How it updates |
|---|---|---|
| The library (`pip install tlakit`) | PyPI / a git checkout | normal packaging |
| **The public runner** | `/usr/local/tlakit/src` on the Mac Mini | `rsync`, then restart |
| **The edge Worker** | Cloudflare Worker `tla-runner` | `wrangler deploy` in `worker/` |
| The browser notebook | Cloudflare Pages project `tlakit` | `wrangler pages deploy` |

A change to `src/tlakit/serve/**` reaches users only through the second row.

A **new endpoint needs the second and third rows together**, and in that order.
The Worker refuses any path outside its own `ALLOWED_PATHS`, so an endpoint the
origin serves perfectly is still a 404 from the outside until the Worker ships
too — and the 404 comes back as the Worker's own JSON, which looks nothing like
an origin problem. `/parse` (#67) is the first endpoint added since the Worker
existed, and it also introduced a new rate-limit binding (`PARSE_LIMITER`), so
`wrangler deploy` is what creates that namespace as well.

## Update the origin

`/usr/local/tlakit/src` is owned by `ericspencer`, so the copy needs no
password. The venv installs it editable, so replacing the files is the deploy.

```bash
rsync -av --delete --exclude __pycache__ --exclude '*.pyc' \
  src/tlakit/ mac-mini:/usr/local/tlakit/src/src/tlakit/
```

`--delete` matters: without it a file that moved (for example
`kernel/routing.py` becoming `routing.py`) lingers and shadows the new layout.

## Check before restarting

Never restart on faith — an earlier `--reload` change that had never been run
took the service down and launchd unloaded it.

```bash
ssh mac-mini '/usr/local/tlakit/venv/bin/python -c "
from tlakit.serve.app import create_app
app = create_app()
print([m.cls.__name__ for m in app.user_middleware])
"'
```

`create_app()` runs `startup_checks`, so this also proves the jar is still
isolated and readable.

## Restart

The daemon runs as root in the system domain, so this step needs a password and
cannot be done for you:

```bash
sudo launchctl kickstart -k system/com.ericspencer.tlarunner
```

## Verify from outside

A preflight is the part a browser needs and `curl` will not exercise by
accident. Send a `User-Agent`: Cloudflare answers 403 to urllib's default agent.

```bash
curl -s -i -X OPTIONS https://tla-runner.ericspencer.us/check \
  -H 'Origin: https://tlakit.pages.dev' \
  -H 'Access-Control-Request-Method: POST' \
  -H 'Access-Control-Request-Headers: content-type' \
  -A tlakit-check | grep -iE '^HTTP|access-control'
```

Expect `200`, `access-control-allow-origin: *`, and **no**
`access-control-allow-credentials` — a wildcard origin with credentials is
illegal and the browser would refuse the response.

Then prove each endpoint is actually routed, which the preflight above does not
tell you — the Worker answers `OPTIONS` for anything before it checks the path:

```bash
curl -s -X POST https://tla-runner.ericspencer.us/parse \
  -H 'content-type: application/json' -A tlakit-check \
  -d '{"spec":"---- MODULE M ----\nVARIABLE x\nInit == x = 0\n===="}'
```

Expect `{"outcome":"ok",...}`. `{"error":"not found"}` means the origin has the
endpoint but the Worker was never redeployed, which is the failure the table
above is about. `/health` reports `parses_per_minute` once the origin is
current, so it distinguishes the two halves:

```bash
curl -s https://tla-runner.ericspencer.us/health -A tlakit-check
```
