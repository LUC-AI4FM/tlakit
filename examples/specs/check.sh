#!/bin/sh
# Check a spec against the public runner:  sh check.sh LostUpdate.tla LostUpdate.cfg

# Cloudflare can block curl's default user-agent, so send a custom User-Agent.
# --fail-with-body ensures curl returns non-zero on HTTP errors (e.g. 422, 503)
# while still displaying the error message.
if [ -n "$2" ]; then
  curl -s -m 90 --fail-with-body -A "tlakit-check" \
    -F spec=@"$1" \
    -F cfg=@"$2" \
    https://tla-runner.ericspencer.us/check
else
  curl -s -m 90 --fail-with-body -A "tlakit-check" \
    -F spec=@"$1" \
    https://tla-runner.ericspencer.us/check
fi
