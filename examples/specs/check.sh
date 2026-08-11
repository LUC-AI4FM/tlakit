#!/bin/sh
# Check a spec against the public runner:  sh check.sh LostUpdate.tla [LostUpdate.cfg]

# Cloudflare can block curl's default user-agent; --fail-with-body keeps non-2xx visible.
curl -s -m 90 --fail-with-body -A "tlakit-check" \
  -F spec=@"$1" \
  ${2:+-F cfg=@"$2"} \
  https://tla-runner.ericspencer.us/check

