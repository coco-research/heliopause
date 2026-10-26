"""Evidence for Legal: gh is GET-only, and no sign verb exists."""

import ast
import os
import subprocess
import sys
from pathlib import Path

from atlas.github import Poller, allowed
from atlas.repocheck import check

ROOT = Path(__file__).resolve().parents[1]
ATLAS = ROOT / "atlas"
_BANNED = {"--method", "-X", "--field", "--raw-field", "--input"}


def _files():
    return sorted(path for path in ATLAS.rglob("*.py") if path.is_file())


def _value(node):
    return node.value if isinstance(node, ast.Constant) else None


def _gh_lists():
    found = []
    for path in _files():
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            arg = node.args[0]
            if isinstance(arg, (ast.List, ast.Tuple)) and arg.elts and _value(arg.elts[0]) == "gh":
                found.append((path, arg))
    return found


def _strings(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def _parser_names(path):
    names = []
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        func = getattr(node, "func", None)
        if isinstance(node, ast.Call) and isinstance(func, ast.Attribute) and func.attr == "add_parser" and node.args:
            value = _value(node.args[0])
            if isinstance(value, str):
                names.append(value)
    return names


def _is_args0(node):
    return (
        isinstance(node, ast.Subscript)
        and isinstance(node.value, ast.Name)
        and node.value.id == "args_list"
        and _value(node.slice) == 0
    )


def _dispatches_sign(path):
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare) or not _is_args0(node.left):
            continue
        for op, comp in zip(node.ops, node.comparators):
            if isinstance(op, ast.Eq) and _value(comp) == "sign":
                return True
            elts = getattr(comp, "elts", ())
            if isinstance(op, ast.In) and any(_value(elt) == "sign" for elt in elts):
                return True
    return False


def test_kb_github_calls_are_get_only(tmp_path, monkeypatch):
    calls = _gh_lists()
    assert {path.resolve() for path, _arg in calls} == {(ATLAS / "github.py").resolve()}
    for _path, arg in calls:
        assert len(arg.elts) == 3 and _value(arg.elts[1]) == "api"
    for text in _strings(ATLAS / "github.py"):
        assert text not in _BANNED and "merge" not in text

    recorded = []

    def spy(argv, **_kwargs):
        recorded.append(list(argv))
        path = argv[2] if len(argv) >= 3 else ""
        body = "[]" if any(bit in path for bit in ("pulls?", "check-runs", "/contents/")) else "{}"
        return subprocess.CompletedProcess(argv, 0, body, "")

    monkeypatch.setattr("atlas.github.subprocess.run", spy)
    config = {"org": "acme", "repos": {"widget": "lead", "gadget": "lead"}}
    monkeypatch.setattr("atlas.github.load_config", lambda: config)
    monkeypatch.setattr("atlas.repocheck.load_config", lambda: config)
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "atlas.json").write_text('{"github": {"enabled": true}}\n', encoding="utf-8")
    check(lab)
    Poller(config).poll_once()
    paths = []
    assert recorded
    for argv in recorded:
        assert argv == ["gh", "api", argv[2]]
        assert allowed(argv[2], config) is True
        paths.append(argv[2])
    assert any("/pulls?" in path for path in paths)
    assert any(path.endswith("/readme") for path in paths)


def test_o4_no_sign_command(tmp_path):
    assert "sign" not in _parser_names(ATLAS / "cli.py")
    assert _dispatches_sign(ATLAS / "__main__.py") is False
    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    for module in ("atlas", "atlas.cli"):
        done = subprocess.run(
            [sys.executable, "-m", module, "sign"],
            cwd=ROOT,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert done.returncode != 0
