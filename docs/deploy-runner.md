# Deploying the runner

Two deploy paths exist and they are easy to confuse. Getting this wrong is how a
tested, committed, documented CORS fix stayed absent from production on
2026-08-08: the browser could not preflight `POST /check`, and the notebook
failed with `NetworkError: Failed to execute 'send' on 'XMLHttpRequest'`.

| What | Where it lives | How it updates |
|---|---|---|
| The library (`pip install tlakit`) | PyPI / a git checkout | normal packaging |
| **The public runner** | `/usr/local/tlakit/src` on the Mac Mini | `rsync`, then restart |
| The browser notebook | Cloudflare Pages project `tlakit` | `wrangler pages deploy` |

A change to `src/tlakit/serve/**` reaches users only through the middle row.

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
