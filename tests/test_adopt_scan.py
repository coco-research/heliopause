"""atlas adopt --scan reads metadata only and writes adopt-scan.json."""

import ast
import builtins
import io
import json
import mmap
import os
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from atlas import adopt
from atlas.__main__ import main

FIX = Path(__file__).parent / "fixtures" / "adopt"
SECRET = "adopt-scan-secret-value-9f3c"
DAY = 86400
CANARIES = (
    SECRET,
    "example.invalid",
    "userID",
    "claude-md-body-canary",
    "cursor-local-body-canary",
    "cursor-rule-body-canary",
    "plugin-file-body-canary",
    "project-claude-body-canary",
    "project-agents-body-canary",
    "adopt-scan-message-body",
    "adopt-scan-last-prompt-text",
    "adopt-scan-old-title",
    "adopt-scan-fake-title",
    "subagent-body-canary",
    "/usr/local/bin/start-a",
    "/usr/local/bin/only-start",
    "/opt/hooks/start-b",
    "/tmp/check.py",
)
TRANSCRIPT = (
    '{"type":"custom-title","title":"adopt-scan-old-title","extra":"' + SECRET + '"}\n'
    '{"type":"user","message":{"content":"adopt-scan-message-body"}}\n'
    '{"type":"last-prompt","lastPrompt":"adopt-scan-last-prompt-text"}\n'
    '{"type":"custom-title","title":"Adopt Scan Custom Title","note":"adopt-scan-fake-title"}\n'
)
CONFLICT_MARKS = (
    "More than one SessionStart hook",
    "configured in two places",
    "over 40 KB",
    "More than 20 chat folders",
    "no longer exists",
    "same name as a Claude rule",
)


@pytest.fixture(autouse=True)
def no_real_home(monkeypatch, tmp_path):
    # A scan that ignores --home must not fall through to the real home directory.
    monkeypatch.setattr(Path, "home", lambda: tmp_path / "blocked-home")


def claude_dir(path: Path) -> str:
    encoded = re.sub(r"[^A-Za-z0-9]", "-", str(path))
    assert encoded == adopt.encode_project_dir(str(path))
    return encoded


def copy_home(name: str, tmp_path: Path) -> Path:
    dest = tmp_path / name
    shutil.copytree(FIX / name, dest)
    return dest


def add_chat(home: Path, project: Path, mtime: float, body: str = "{}\n", name: str = "s.jsonl") -> Path:
    project.mkdir(parents=True, exist_ok=True)
    folder = home / ".claude" / "projects" / claude_dir(project)
    folder.mkdir(parents=True, exist_ok=True)
    dest = folder / name
    dest.write_text(body, encoding="utf-8")
    os.utime(dest, (mtime, mtime))
    return dest


def snapshot(root: Path) -> dict:
    found = {}
    if not root.exists():
        return found
    for path in root.rglob("*"):
        if path.is_file():
            st = path.stat()
            found[path.relative_to(root)] = (st.st_size, st.st_mtime_ns, st.st_mode)
    return found


def section(text: str, start: str, end: str) -> str:
    lines = text.splitlines()
    return "\n".join(lines[lines.index(start) : lines.index(end)])


def scan(home: Path, lab: Path, capsys):
    code = adopt.run(["--scan", "--home", str(home), "--lab", str(lab)])
    out = capsys.readouterr().out
    report_path = lab / ".atlas" / "adopt-scan.json"
    data = json.loads(report_path.read_text(encoding="utf-8")) if report_path.is_file() else None
    return code, out, data


def assert_clean_secrets(out: str, data: dict) -> None:
    blob = out + "\n" + json.dumps(data)
    for canary in CANARIES:
        assert canary not in blob


def test_clean_scan_metadata_only(tmp_path, capsys):
    home = copy_home("clean", tmp_path)
    project = tmp_path / "My Project"
    mtime = int(time.time()) - 3600
    dest = add_chat(home, project, mtime, TRANSCRIPT)
    sub = dest.parent / "subagents"
    sub.mkdir()
    hidden = sub / "hidden.jsonl"
    hidden.write_text('{"type":"user","message":{"content":"subagent-body-canary"}}\n', encoding="utf-8")
    os.utime(hidden, (time.time(), time.time()))
    (project / "CLAUDE.md").write_text("project-claude-body-canary\n", encoding="utf-8")
    (project / "AGENTS.md").write_text("project-agents-body-canary\n", encoding="utf-8")
    lab = tmp_path / "lab"
    before = snapshot(home)
    old = os.umask(0)
    try:
        code, out, data = scan(home, lab, capsys)
    finally:
        os.umask(old)
    assert code == 0
    assert snapshot(home) == before
    assert not (tmp_path / "blocked-home").exists()
    files = sorted(path.relative_to(lab) for path in lab.rglob("*") if path.is_file())
    assert files == [Path(".atlas/adopt-scan.json")]
    assert (lab / ".atlas" / "adopt-scan.json").stat().st_mode & 0o777 == 0o600

    lines = out.splitlines()
    report = lab / ".atlas" / "adopt-scan.json"
    assert lines[0] == f"Your setup is unchanged. Wrote one report: {report}. Nothing left this machine."
    assert lines[-1] == "Next: atlas adopt --apply shows a backup path and undo before it changes anything."
    assert lines[1] == "1 folders, 1 chats, 1 active in the last 30 days."
    for mark in CONFLICT_MARKS:
        assert mark not in out
    assert "Folders Heliopause would ignore (nothing is moved)" in out
    assert "one-plugin" in out
    assert "only-start" in out
    assert "alpha" in out and "beta" in out
    assert "Adopt Scan Custom Title" not in out
    folders_text = section(out, "Folders", "Rules")
    assert folders_text.splitlines()[1] == "path  chats  last active  size"
    assert "(approximate)" not in folders_text

    folder = data["folders"][str(project)]
    assert folder["chats"] == 1 and folder["approximate"] is False and folder["exists"] is True
    assert "title" not in folder
    assert all("title" not in row for row in data["recent"])
    assert folder["size"] == dest.stat().st_size
    parsed = datetime.strptime(folder["last_active"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert abs(parsed.timestamp() - mtime) < 2
    assert data["summary"] == {"active_30d": 1, "chats": 1, "folders": 1}
    assert data["mcp_servers"] == {"claude": ["alpha"], "cursor": ["beta"]}
    assert data["plugins"] == ["one-plugin"]
    assert data["conflicts"] == [] and data["suggest_ignore"] == []
    assert data["hooks"] == [{"event": "SessionStart", "count": 1, "commands": ["only-start"]}]
    rules = {item["path"]: item for item in data["rules"]}
    assert rules[str(home / ".claude" / "CLAUDE.md")]["size"] == (home / ".claude" / "CLAUDE.md").stat().st_size
    assert rules[str(project / "CLAUDE.md")]["name"] == "CLAUDE.md"
    assert rules[str(project / "AGENTS.md")]["size"] == (project / "AGENTS.md").stat().st_size
    assert rules[str(home / ".cursor" / "rules" / "local.mdc")]["source"] == "cursor"
    assert_clean_secrets(out, data)


def _transcript(path) -> bool:
    return str(path).endswith(".jsonl")


def test_scan_never_opens_jsonl(tmp_path, monkeypatch, capsys):
    home = copy_home("clean", tmp_path)
    project = tmp_path / "My Project"
    now = int(time.time())
    add_chat(home, project, now - 120, TRANSCRIPT, name="older.jsonl")
    add_chat(home, project, now - 60, TRANSCRIPT, name="newer.jsonl")
    real_open = builtins.open
    real_path_open = Path.open
    real_read_text = Path.read_text
    real_read_bytes = Path.read_bytes
    real_os_open = os.open
    real_io_open = io.open

    def guarded_open(file, *args, **kwargs):
        if _transcript(file):
            raise AssertionError("transcript opened")
        return real_open(file, *args, **kwargs)

    def guarded_path_open(self, *args, **kwargs):
        if _transcript(self):
            raise AssertionError("transcript Path.open")
        return real_path_open(self, *args, **kwargs)

    def guarded_text(self, *args, **kwargs):
        if _transcript(self):
            raise AssertionError("transcript read_text")
        return real_read_text(self, *args, **kwargs)

    def guarded_bytes(self, *args, **kwargs):
        if _transcript(self):
            raise AssertionError("transcript read_bytes")
        return real_read_bytes(self, *args, **kwargs)

    def guarded_os_open(path, *args, **kwargs):
        if _transcript(path):
            raise AssertionError("transcript os.open")
        return real_os_open(path, *args, **kwargs)

    def guarded_io_open(file, *args, **kwargs):
        if _transcript(file):
            raise AssertionError("transcript io.open")
        return real_io_open(file, *args, **kwargs)

    def guarded_mmap(*_args, **_kwargs):
        raise AssertionError("mmap.mmap")

    def guarded_popen(*_args, **_kwargs):
        raise AssertionError("subprocess.Popen")

    monkeypatch.setattr(builtins, "open", guarded_open)
    monkeypatch.setattr(Path, "open", guarded_path_open)
    monkeypatch.setattr(Path, "read_text", guarded_text)
    monkeypatch.setattr(Path, "read_bytes", guarded_bytes)
    monkeypatch.setattr(os, "open", guarded_os_open)
    monkeypatch.setattr(io, "open", guarded_io_open)
    monkeypatch.setattr(mmap, "mmap", guarded_mmap)
    monkeypatch.setattr(subprocess, "Popen", guarded_popen)
    # Each guard has to fire once. A patch nothing calls does not prove the scan is covered.
    with pytest.raises(AssertionError, match=r"transcript os\.open"):
        os.open("chat.jsonl", os.O_RDONLY)
    with pytest.raises(AssertionError, match=r"transcript io\.open"):
        io.open("chat.jsonl", "r", encoding="utf-8")
    with pytest.raises(AssertionError, match=r"mmap\.mmap"):
        mmap.mmap(-1, 0)
    with pytest.raises(AssertionError, match="subprocess.Popen"):
        subprocess.Popen(["atlas-scan-guard"])
    code, out, data = scan(home, tmp_path / "lab", capsys)
    assert code == 0
    assert data["summary"]["chats"] == 2
    rows = list(data["folders"].values()) + data["recent"]
    assert rows and all("title" not in row for row in rows)
    assert "Adopt Scan Custom Title" not in out
    assert lines_ok(out)


def test_adopt_py_does_not_import_mmap():
    tree = ast.parse(Path(adopt.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert all(alias.name != "mmap" and not alias.name.startswith("mmap.") for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            assert node.module != "mmap"


def lines_ok(out: str) -> bool:
    lines = out.splitlines()
    assert lines[0].startswith("Your setup is unchanged. Wrote one report: ")
    assert lines[0].endswith("adopt-scan.json. Nothing left this machine.")
    assert lines[-1] == "Next: atlas adopt --apply shows a backup path and undo before it changes anything."
    return True


def test_every_conflict_fires_and_clean_stays_quiet(tmp_path, capsys):
    home = copy_home("conflicts", tmp_path)
    # settings.local.json is gitignored everywhere, so the fixture cannot carry it.
    (home / ".claude" / "settings.local.json").write_text(
        json.dumps(
            {
                "hooks": {
                    "PreToolUse": [{"hooks": [{"type": "command", "command": "python3 /tmp/check.py"}]}],
                    "SessionStart": [
                        {
                            "hooks": [
                                {
                                    "type": "command",
                                    "command": "SUPER=adopt-scan-secret-value-9f3c /opt/hooks/start-b --token adopt-scan-secret-value-9f3c",
                                }
                            ]
                        }
                    ],
                }
            }
        ),
        encoding="utf-8",
    )
    (home / ".claude" / "CLAUDE.md").write_bytes(b"x" * (40 * 1024 + 1))
    now = time.time()
    batch = tmp_path / "batch"
    for index in range(21):
        add_chat(home, batch / f"c{index:02d}", now - 3600)
    missing = home / ".claude" / "projects" / "-atlasadoptmissingzz9"
    missing.mkdir(parents=True)
    gone = missing / "s.jsonl"
    gone.write_text("{}\n", encoding="utf-8")
    os.utime(gone, (now - 3600, now - 3600))
    old = tmp_path / "dusty"
    add_chat(home, old, now - 90 * DAY)
    code, out, data = scan(home, tmp_path / "lab", capsys)
    assert code == 0
    for mark in CONFLICT_MARKS:
        assert mark in out
    kinds = {item["kind"] for item in data["conflicts"]}
    assert kinds == {
        "session_start",
        "mcp_duplicate",
        "claude_md_size",
        "too_many_active",
        "missing_path",
        "rule_name",
    }
    by_kind = {}
    for item in data["conflicts"]:
        by_kind.setdefault(item["kind"], []).append(item)
    assert "Keep one SessionStart hook" in by_kind["session_start"][0]["line"]
    assert "remove the duplicate" in by_kind["mcp_duplicate"][0]["line"]
    assert "Trim ~/.claude/CLAUDE.md" in by_kind["claude_md_size"][0]["line"]
    assert "ignore list" in by_kind["too_many_active"][0]["line"]
    assert "nothing is deleted" in by_kind["missing_path"][0]["line"]
    assert "Rename one of them" in by_kind["rule_name"][0]["line"]
    for item in data["conflicts"]:
        assert item["line"] in out
        assert item["fix"] in item["line"]
    reasons = {item["path"]: item["reason"] for item in data["suggest_ignore"]}
    assert reasons[str(old)] == "inactive"
    assert reasons["/atlasadoptmissingzz9"] == "missing"
    for index in range(21):
        assert str(batch / f"c{index:02d}") not in reasons
    assert data["hooks"] == [
        {"event": "PreToolUse", "count": 1, "commands": ["python3"]},
        {"event": "SessionStart", "count": 2, "commands": ["start-a", "start-b"]},
    ]
    assert data["mcp_servers"] == {"claude": ["alpha", "shared"], "cursor": ["beta", "shared"]}
    blob = out + "\n" + json.dumps(data)
    for leaked in (SECRET, "example.invalid", "/usr/local/bin/start-a", "/opt/hooks/start-b", "/tmp/check.py", "cursor-rule-body-canary"):
        assert leaked not in blob
    assert "x" * 80 not in blob


def test_claude_md_size_boundary(tmp_path, capsys):
    home = copy_home("clean", tmp_path)
    target = home / ".claude" / "CLAUDE.md"
    lab = tmp_path / "lab"
    target.write_bytes(b"y" * (40 * 1024))
    _code, out, data = scan(home, lab, capsys)
    assert "over 40 KB" not in out and data["conflicts"] == []
    target.write_bytes(b"y" * (40 * 1024 + 1))
    _code, out, data = scan(home, lab, capsys)
    assert "over 40 KB" in out
    assert any(item["kind"] == "claude_md_size" for item in data["conflicts"])


def test_age_boundaries(tmp_path, monkeypatch, capsys):
    fixed = 1_800_000_000
    monkeypatch.setattr(adopt.time, "time", lambda: fixed)
    home = copy_home("clean", tmp_path)
    active = tmp_path / "edge-active"
    just_old = tmp_path / "just-old"
    edge_keep = tmp_path / "edge-keep"
    stale = tmp_path / "just-stale"
    add_chat(home, active, fixed - 30 * DAY)
    add_chat(home, just_old, fixed - 30 * DAY - 1)
    add_chat(home, edge_keep, fixed - 60 * DAY)
    add_chat(home, stale, fixed - 60 * DAY - 1)
    _code, out, data = scan(home, tmp_path / "lab", capsys)
    assert data["summary"]["active_30d"] == 1
    assert data["suggest_ignore"] == [{"path": str(stale), "reason": "inactive"}]
    assert "inactive for more than 60 days" in out
    assert str(just_old) not in out.split("Folders Heliopause would ignore")[1]
    assert "no longer exists" not in out


def test_approximate_when_two_paths_encode_the_same(tmp_path, capsys):
    home = copy_home("clean", tmp_path)
    base = tmp_path / "ambig"
    dashed = base / "foo-bar"
    nested = base / "foo" / "bar"
    assert claude_dir(dashed) == claude_dir(nested)
    add_chat(home, dashed, time.time() - 60)
    nested.mkdir(parents=True)
    _code, out, data = scan(home, tmp_path / "lab", capsys)
    assert len(data["folders"]) == 1
    path, folder = next(iter(data["folders"].items()))
    assert folder["approximate"] is True
    assert path in {str(dashed), str(nested)}
    assert "(approximate)" in section(out, "Folders", "Rules")
    assert "no longer exists" not in out


def test_recent_table_keeps_fifteen(tmp_path, capsys):
    home = copy_home("clean", tmp_path)
    now = time.time()
    batch = tmp_path / "batch"
    projects = []
    for index in range(16):
        project = batch / f"p{index:02d}"
        add_chat(home, project, now - (index + 1) * 60)
        projects.append(project)
    _code, out, data = scan(home, tmp_path / "lab", capsys)
    assert len(data["recent"]) == 15
    assert len(data["folders"]) == 16
    shown = section(out, "Folders", "Rules")
    assert str(projects[0]) in shown
    assert str(projects[15]) not in shown
    assert str(projects[15]) in data["folders"]
    assert "More than 20 chat folders" not in out


def test_missing_sources_are_skipped(tmp_path, capsys):
    empty = tmp_path / "empty"
    empty.mkdir()
    code, out, data = scan(empty, tmp_path / "lab-empty", capsys)
    assert code == 0 and data["summary"] == {"active_30d": 0, "chats": 0, "folders": 0}
    assert data["conflicts"] == [] and data["hooks"] == [] and data["plugins"] == []
    assert "none" in section(out, "Hooks", "MCP servers")
    lines_ok(out)

    sparse = copy_home("sparse", tmp_path)
    code, out, data = scan(sparse, tmp_path / "lab-sparse", capsys)
    assert code == 0 and data["summary"]["folders"] == 0 and data["hooks"] == []
    lines_ok(out)

    missing = tmp_path / "does-not-exist"
    code, _out, data = scan(missing, tmp_path / "lab-missing", capsys)
    assert code == 0 and data["summary"]["folders"] == 0


def test_apply_without_yes_leaves_the_lab_unwritten(tmp_path, capsys, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", lambda: home)
    lab = tmp_path / "lab"
    settings = home / ".claude" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_bytes(b'{"theme":"dark"}\n')
    before = settings.read_bytes()
    code = adopt.run(["--apply", "--lab", str(lab), "--home", str(home)])
    out = capsys.readouterr().out
    assert code == 1 and "Nothing changed yet." in out
    assert settings.read_bytes() == before
    assert not lab.exists()
    with pytest.raises(SystemExit) as exc:
        main(["adopt", "--apply", "--home", str(home), "--lab", str(lab)])
    assert exc.value.code == 1
    assert not lab.exists()
