import shutil

import pytest

from node_run import node_run


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_args_keep_their_argv_slots_and_can_pass_128_kb():
    big = "x" * 200_000
    script = "console.log(JSON.stringify([process.argv.length, process.argv[1].length, process.argv[2]]));"
    done = node_run(script, big, "two", capture_output=True, text=True, check=True)
    assert done.stdout.strip() == '[3,200000,"two"]'
