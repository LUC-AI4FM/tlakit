#!/bin/sh
# Check a spec against the public runner:  sh check.sh LostUpdate.tla LostUpdate.cfg
UA="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36"
python3 -c "
import json,sys,pathlib
print(json.dumps({'spec': pathlib.Path(sys.argv[1]).read_text(),
                  'config': pathlib.Path(sys.argv[2]).read_text()}))
" "$1" "$2" | curl -s -m 90 -A "$UA" -X POST https://tla-runner.ericspencer.us/check \
  -H "content-type: application/json" --data-binary @- | python3 -m json.tool
