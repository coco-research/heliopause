"""Tests for the generic public demo and lab mode budget/roadmap."""

from __future__ import annotations

import importlib.util
import os
import re
import subprocess
import sys
from pathlib import Path

import private_names

ROOT = Path(__file__).resolve().parents[1]

# ponytail: public vendor names the generic demo must not show. Internal and personal names live in the
# private names file (tests/private_names.py) so this list names nobody.
BANNED_WORDS = (
    "Grok", "Gemini", "Antigravity", "Z Code", "ZCode", "GLM", "DeepSeek", "Claude", "Anthropic",
    "OpenAI", "Cursor", "NVIDIA", "xAI",
)


def _serve():
    path = ROOT / "scripts" / "demo_serve.py"
    spec = importlib.util.spec_from_file_location("demo_serve", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_banned_words_scan():
    patterns = [re.compile(rf"\b{re.escape(w)}\b") for w in BANNED_WORDS]
    generic_dir = ROOT / "demo" / "generic"
    files = [path for path in generic_dir.rglob("*") if path.is_file()]
    assert files, "demo/generic must contain files"
    for path in files:
        text = path.read_text(encoding="utf-8")
        for word, pat in zip(BANNED_WORDS, patterns):
            assert pat.search(text) is None, f"Banned word {word!r} found in {path}"
    module = _serve()
    for text in (*module.GENERIC_POSTERS, *module.GENERIC_HEADLINES):
        for word, pat in zip(BANNED_WORDS, patterns):
            assert pat.search(text) is None, f"Banned word {word!r} found in {text!r}"


def test_generic_demo_has_no_private_names():
    banned = private_names.names()
    for path in (ROOT / "demo" / "generic").rglob("*"):
        if path.is_file():
            assert private_names.hits(path.read_text(encoding="utf-8"), banned) == [], f"banned name in {path}"
    module = _serve()
    for text in (*module.GENERIC_POSTERS, *module.GENERIC_HEADLINES):
        assert private_names.hits(text, banned) == [], "banned name in a generic demo line"


def test_filled_template_parses(tmp_path):
    dest = tmp_path / "generic"
    module = _serve()
    module.prepare(dest, generic=True)

    for path in dest.rglob("*"):
        if path.is_file():
            assert "{{" not in path.read_text(encoding="utf-8")

    sub_code = """
import json, threading
from http.client import HTTPConnection
from atlas.__main__ import make_server
from atlas.collect import LAB_DIR

server = make_server(LAB_DIR, 0, watch=False)
threading.Thread(target=server.serve_forever, daemon=True).start()

def get(path):
    conn = HTTPConnection("127.0.0.1", server.server_address[1], timeout=5)
    conn.request("GET", path)
    return json.loads(conn.getresponse().read().decode("utf-8"))

try:
    state = get("/api/state")
    nodes = state["graph"]["nodes"]
    expected = {"owner", "hq-assistant", "product-a", "product-b", "product-c", "product-d", "product-e", "dept-design", "dept-research", "dept-ops", "factory-a", "factory-b", "factory-c"}
    assert set(nodes.keys()) == expected and len(nodes) == 13
    problems = get("/api/problems")
    assert len(problems) == 1
    bud = get("/api/budget")
    assert [p["name"] for p in bud.get("pools", [])] == ["Pool 1", "Pool 2", "Pool 3", "Pool 4", "Pool 5"]
    road = get("/api/roadmap")
    assert len(road.get("rows", [])) > 0
finally:
    server.shutdown()
    server.server_close()
print("OK_GENERIC_PARSED")
"""
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path), "ATLAS_LAB": str(dest)}
    proc = subprocess.run([sys.executable, "-c", sub_code], cwd=ROOT, capture_output=True, text=True, env=env, timeout=30)
    assert proc.returncode == 0, f"Subprocess failed:\nSTDOUT:\n{proc.stdout}\nSTDERR:\n{proc.stderr}"
    assert "OK_GENERIC_PARSED" in proc.stdout


def test_generic_lines_never_address_their_sender():
    from datetime import datetime

    demo_serve = _serve()
    for index in range(200):
        line = demo_serve._line(index, datetime.now(demo_serve.NY), generic=True)
        _, poster, _, headline, _ = line.split(demo_serve.SEP)
        assert poster not in headline


def test_guard_refuses_both_template_trees():
    demo_serve = _serve()
    for tree in (demo_serve.SRC, demo_serve.GENERIC_SRC):
        try:
            demo_serve._guard(tree)
        except SystemExit:
            continue
        raise AssertionError(f"guard accepted {tree}")
