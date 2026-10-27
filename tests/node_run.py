"""Run `node -e SCRIPT ARG...` with SCRIPT and the ARGs sent on stdin.

WHY: Linux caps one argv string at 128 KB (MAX_ARG_STRLEN) and graph.js is bigger. The script still
finds its arguments in process.argv[1:], so callers only swap the subprocess call.
"""

from __future__ import annotations

import json
import subprocess

_BOOT = (
    'const __nodeRunInput = JSON.parse(require("fs").readFileSync(0, "utf8"));'
    "process.argv.push(...__nodeRunInput.args);"
    "eval(__nodeRunInput.script);"
)


def node_run(script: str, *args: str, **kwargs) -> subprocess.CompletedProcess:
    """subprocess.run for node. Pass text=True or encoding= as before; never pass input=."""
    payload = json.dumps({"script": script, "args": list(args)})
    return subprocess.run(["node", "-e", _BOOT], input=payload, **kwargs)
