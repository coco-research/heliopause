"""atlas adopt --apply backs up, writes the lab, and uninstalls by manifest."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

import pytest

from atlas import adopt, chats
from atlas.__main__ import main
from atlas.collect import load_lab
from atlas.init import _DIRS

BEGIN = "<!-- atlas:begin v1 -->"
END = "<!-- atlas:end -->"
GLOBAL_MD = b"global-claude-canary\n"
SETTINGS = b'{"theme":"dark","model":"opus","env":{"CLAUDE_CODE_SUBAGENT_MODEL":"haiku"}}\n'
CURSOR = b"cursor-rule-canary\n"


def _home(tmp_path, monkeypatch):
    root = tmp_path / "home"
    root.mkdir()
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", lambda: root)
    claude = root / ".claude"
    claude.mkdir()
    (claude / "CLAUDE.md").write_bytes(GLOBAL_MD)
    (claude / "settings.json").write_bytes(SETTINGS)
    rules = root / ".cursor" / "rules"
    rules.mkdir(parents=True)
    (rules / "local.mdc").write_bytes(CURSOR)
    return root


def _snap(root: Path) -> dict:
    found = {}
    for path in root.rglob("*"):
        if path.is_file():
            found[path.relative_to(root)] = (path.stat().st_mtime_ns, path.read_bytes())
    return found


def _apply(home: Path, lab: Path, *extra: str) -> int:
    return adopt.run(["--apply", "--yes", "--home", str(home), "--lab", str(lab), *extra])


def _globals(home: Path) -> tuple:
    return (
        (home / ".claude" / "CLAUDE.md").read_bytes(),
        (home / ".claude" / "settings.json").read_bytes(),
        (home / ".cursor" / "rules" / "local.mdc").read_bytes(),
    )


def test_apply_keeps_global_files_and_second_apply_is_a_noop(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    assert _apply(home, lab, "--lead-name", "Ada") == 0
    assert _globals(home) == (GLOBAL_MD, SETTINGS, CURSOR)
    assert sorted(p.name for p in (home / ".cursor" / "rules").iterdir()) == ["local.mdc"]
    assert (lab / ".cursor" / "rules" / "atlas.mdc").is_file()
    data = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    assert data["lead"] == "Ada" and data["owner"] == "Ada"
    manifest = (lab / ".atlas" / "adopt-manifest.tsv").read_text(encoding="utf-8").splitlines()
    assert manifest
    for line in manifest:
        kind, stored, digest, _backup = line.split("\t")
        assert kind in {"created", "merged"}
        path = Path(stored) if stored.startswith("/") else lab / stored
        assert hashlib.sha256(path.read_bytes()).hexdigest() == digest
    before = _snap(lab)
    home_before = _snap(home)
    assert _apply(home, lab, "--lead-name", "Ada") == 0
    assert _snap(lab) == before
    assert _snap(home) == home_before
    assert _globals(home) == (GLOBAL_MD, SETTINGS, CURSOR)


def test_existing_lab_keeps_its_files(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "board.md").write_bytes(b"keep-board\n")
    (lab / "feed.md").write_bytes(b"keep-feed\n")
    (lab / "notes.txt").write_bytes(b"keep-notes\n")
    assert _apply(home, lab, "--lead-name", "Ada") == 0
    assert (lab / "board.md").read_bytes() == b"keep-board\n"
    assert (lab / "feed.md").read_bytes() == b"keep-feed\n"
    assert (lab / "notes.txt").read_bytes() == b"keep-notes\n"
    assert (lab / "START-HERE.md").is_file()
    assert _globals(home) == (GLOBAL_MD, SETTINGS, CURSOR)


def test_marker_blocks_round_trip(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    original = "Keep this line.\n\nAnd this.\n"
    (lab / "CLAUDE.md").write_text(original, encoding="utf-8")
    assert _apply(home, lab, "--lead-name", "Ada") == 0
    text = (lab / "CLAUDE.md").read_text(encoding="utf-8")
    assert text.startswith(original)
    assert text.count(BEGIN) == 1 and text.count(END) == 1
    assert "The Lead is Ada." in text
    saved = list((lab / ".atlas" / "backup").rglob("CLAUDE.md"))
    assert len(saved) == 1 and saved[0].read_text(encoding="utf-8") == original
    capsys.readouterr()
    assert adopt.run(["--uninstall", "--lab", str(lab)]) == 0
    assert (lab / "CLAUDE.md").read_text(encoding="utf-8") == original
    assert BEGIN not in (lab / "CLAUDE.md").read_text(encoding="utf-8")


def test_uninstall_lists_an_edited_file_and_does_not_restore(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "CLAUDE.md").write_text("user preface\n", encoding="utf-8")
    assert _apply(home, lab, "--lead-name", "Ada") == 0
    start = lab / "START-HERE.md"
    start.write_text(start.read_text(encoding="utf-8") + "user added this\n", encoding="utf-8")
    decoy = lab / ".atlas" / "backup" / "decoy" / "START-HERE.md"
    decoy.parent.mkdir(parents=True)
    decoy.write_text("RESTORED\n", encoding="utf-8")
    feed = lab / "feed.md"
    assert feed.is_file()
    capsys.readouterr()
    assert adopt.run(["--uninstall", "--lab", str(lab)]) == 0
    out = capsys.readouterr().out
    assert not feed.exists()
    assert "user added this" in start.read_text(encoding="utf-8")
    assert "RESTORED" not in start.read_text(encoding="utf-8")
    assert "START-HERE.md" in out and "edited since apply" in out
    assert (lab / "CLAUDE.md").read_text(encoding="utf-8") == "user preface\n"
    assert BEGIN not in (lab / "CLAUDE.md").read_text(encoding="utf-8")
    assert "restore CLAUDE.md" in out


def test_any_valid_owner_can_be_the_lead(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    for name in ("Ada", "Lin", "Ada/Lovelace"):
        lab = tmp_path / name.replace("/", "_")
        assert _apply(home, lab, "--lead-name", name) == 0
        data = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
        assert data["lead"] == name
    lab = tmp_path / "bad"
    before = _snap(home)
    for name in ("", "Alice · Bob"):
        code = _apply(home, lab, "--lead-name", name)
        err = capsys.readouterr().err
        assert code == 2 and "invalid" in err
    assert _snap(home) == before
    assert not lab.exists()


def test_no_model_key_is_written(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    assert _apply(home, lab, "--lead-name", "Ada") == 0
    blob = b"".join(path.read_bytes() for path in lab.rglob("*") if path.is_file())
    assert b"CLAUDE_CODE_SUBAGENT_MODEL" not in blob
    assert b'"model"' not in blob
    assert _globals(home) == (GLOBAL_MD, SETTINGS, CURSOR)
    assert sorted(p.name for p in (home / ".claude").iterdir()) == ["CLAUDE.md", "settings.json"]


def test_update_option_is_unrecognized(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    with pytest.raises(SystemExit) as exc:
        _apply(home, lab, "--lead-name", "Ada", "--update", "notify")
    err = capsys.readouterr().err
    assert exc.value.code == 2 and "unrecognized arguments" in err
    assert not lab.exists()


def test_adopt_help_has_no_update_option(capsys):
    with pytest.raises(SystemExit) as exc:
        adopt.run(["--help"])
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--update" not in out and "notify" not in out
    assert re.search(r"\bauto\b", out) is None


def test_apply_rewrites_stored_update_to_off(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    nodes = [
        {"id": "lead", "label": "Lead", "role": "lab"},
        {"id": "worker", "label": "Worker", "role": "factory"},
    ]
    cases = (
        ("personal-notify", {"profile": "personal", "update": "notify"}, False),
        ("personal-auto", {"profile": "personal", "update": "auto"}, False),
        ("work-notify", {"profile": "work", "update": "notify"}, True),
        ("work-auto", {"profile": "work", "update": "auto"}, True),
    )
    for name, extra, work in cases:
        lab = tmp_path / name
        lab.mkdir()
        doc = {"v": 1, "owner": "Owner", "tz": "UTC", "nodes": nodes}
        doc.update(extra)
        (lab / "atlas.json").write_text(json.dumps(doc, indent=2) + "\n", encoding="utf-8")
        assert _apply(home, lab, "--lead-name", "Ada") == 0
        data = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
        assert data["update"] == "off" and data["lead"] == "Ada"
        assert data["profile"] == extra["profile"]
        out = capsys.readouterr().out
        assert "model" not in data
        if work:
            assert data["memory"] is False
            assert "Managed machine: memory off, updates off, no login." in out
            assert "sign in" not in out.lower() and "gh auth" not in out.lower()


def test_scan_folder_map_replaces_chats_json(tmp_path, monkeypatch):
    monkeypatch.setattr(chats, "LAB", True)
    monkeypatch.setattr(chats, "LAB_LEAD", "lead")
    lab = tmp_path / "lab"
    assert chats.chat_activity(lab) == {}
    folder = lab / ".atlas"
    folder.mkdir(parents=True)
    payload = {
        "folders": {
            str(tmp_path / "proj"): {"last_active": "2026-10-19T15:04:00Z"},
            str(tmp_path / "old"): {"last_active": "2030-01-01T00:00:00Z"},
        },
        "suggest_ignore": [{"path": str(tmp_path / "old"), "reason": "inactive"}],
    }
    (folder / "adopt-scan.json").write_text(json.dumps(payload), encoding="utf-8")
    got = chats.chat_activity(lab)
    assert got == {"lead": datetime(2026, 10, 19, 15, 4, tzinfo=timezone.utc).timestamp()}


def test_sparse_work_atlas_becomes_a_complete_lab(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "atlas.json").write_text(
        json.dumps({"profile": "work", "update": "off"}) + "\n", encoding="utf-8"
    )
    assert _apply(home, lab, "--lead-name", "Lead A") == 0
    data = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    assert data["v"] == 1
    assert data["owner"] == "Lead A"
    assert isinstance(data["tz"], str) and data["tz"].strip()
    assert [node["id"] for node in data["nodes"]] == ["lead", "worker"]
    assert data["lead"] == "Lead A"
    assert data["profile"] == "work"
    assert data["update"] == "off"
    assert data["memory"] is False
    loaded = load_lab(lab)
    assert loaded["owner"] == "Lead A"


def test_complete_atlas_keeps_custom_owner_tz_nodes(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    nodes = [
        {"id": "lead", "label": "Desk", "role": "lab"},
        {"id": "worker", "label": "Bench", "role": "factory"},
    ]
    (lab / "atlas.json").write_text(
        json.dumps(
            {
                "v": 1,
                "owner": "Pat",
                "tz": "America/New_York",
                "profile": "work",
                "update": "off",
                "memory": False,
                "lead": "Ada",
                "nodes": nodes,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    assert _apply(home, lab, "--lead-name", "Lead A") == 0
    data = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    assert data["owner"] == "Pat"
    assert data["tz"] == "America/New_York"
    assert data["nodes"] == nodes
    assert data["lead"] == "Lead A"
    assert data["profile"] == "work"
    assert data["update"] == "off"
    assert data["memory"] is False


def test_second_apply_on_completed_atlas_changes_nothing(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "atlas.json").write_text(
        json.dumps({"profile": "work", "update": "off"}) + "\n", encoding="utf-8"
    )
    assert _apply(home, lab, "--lead-name", "Lead A") == 0
    capsys.readouterr()
    before = (lab / "atlas.json").read_bytes()
    assert _apply(home, lab, "--lead-name", "Lead A") == 0
    assert "Nothing to change" in capsys.readouterr().out
    assert (lab / "atlas.json").read_bytes() == before


def test_uninstall_never_removes_a_file_outside_the_lab(tmp_path, capsys):
    # The manifest sits in the lab, where agents can edit it: a row naming an outside file is not acted on.
    lab = tmp_path / "lab"
    outside = tmp_path / "outside.txt"
    outside.write_text("x\n", encoding="utf-8")
    digest = hashlib.sha256(b"x\n").hexdigest()
    man = lab / ".atlas" / "adopt-manifest.tsv"
    man.parent.mkdir(parents=True)
    man.write_text(f"created\t{outside.resolve()}\t{digest}\t-\n", encoding="utf-8")
    assert adopt.run(["--uninstall", "--lab", str(lab)]) == 0
    assert outside.read_text(encoding="utf-8") == "x\n"
    assert "outside the lab; not touched" in capsys.readouterr().out


def test_uninstall_restores_merged_atlas_json_and_removes_the_pointer(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    raw = b'{ "profile": "work", "update": "off" }\n'
    (lab / "atlas.json").write_bytes(raw)
    assert _apply(home, lab, "--lead-name", "Lead A") == 0
    pointer = home / ".atlas" / "lab"
    assert pointer.is_file()
    assert pointer.read_text(encoding="utf-8").strip() == str(lab.resolve())
    assert (lab / "atlas.json").read_bytes() != raw
    capsys.readouterr()
    assert adopt.run(["--uninstall", "--lab", str(lab)]) == 0
    out = capsys.readouterr().out
    assert (lab / "atlas.json").read_bytes() == raw
    assert "restore atlas.json" in out
    assert "edited since apply" not in out
    assert not pointer.exists()
    assert not (lab / "inbox").exists()
    assert not (lab / "queue").exists()


def test_changed_merged_file_is_kept_with_its_backup_path(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    raw = b'{"profile":"work","update":"off"}\n'
    (lab / "atlas.json").write_bytes(raw)
    assert _apply(home, lab, "--lead-name", "Lead A") == 0
    path = lab / "atlas.json"
    changed = path.read_bytes() + b"\nuser-edit\n"
    path.write_bytes(changed)
    capsys.readouterr()
    assert adopt.run(["--uninstall", "--lab", str(lab)]) == 0
    out = capsys.readouterr().out
    assert path.read_bytes() == changed
    line = next(item for item in out.splitlines() if item.startswith("keep    atlas.json"))
    assert "changed since apply" in line and "your original is at " in line
    shown = Path(line.split("your original is at ", 1)[1].rstrip(")"))
    assert shown.is_file() and shown.read_bytes() == raw
    assert "edited since apply" not in line


def test_changed_marker_file_is_stripped(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    (lab / "CLAUDE.md").write_text("user preface\n", encoding="utf-8")
    assert _apply(home, lab, "--lead-name", "Ada") == 0
    path = lab / "CLAUDE.md"
    path.write_text(path.read_text(encoding="utf-8") + "user after\n", encoding="utf-8")
    capsys.readouterr()
    assert adopt.run(["--uninstall", "--lab", str(lab)]) == 0
    text = path.read_text(encoding="utf-8")
    assert text.startswith("user preface\n") and "user after\n" in text
    assert BEGIN not in text and END not in text
    assert "strip   CLAUDE.md" in capsys.readouterr().out


def test_scan_banner_names_the_report(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    assert adopt.run(["--scan", "--home", str(home), "--lab", str(lab)]) == 0
    report = lab / ".atlas" / "adopt-scan.json"
    assert report.is_file()
    out = capsys.readouterr().out
    assert out.splitlines()[0] == (
        f"Your setup is unchanged. Wrote one report: {report}. Nothing left this machine."
    )


def test_apply_plan_lists_manifest_folders_and_lab_pointer(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    assert adopt.run(["--scan", "--home", str(home), "--lab", str(lab)]) == 0
    capsys.readouterr()
    code = adopt.run(["--apply", "--home", str(home), "--lab", str(lab), "--lead-name", "Ada"])
    out = capsys.readouterr().out
    assert code == 1
    assert out.splitlines()[-1] == "Nothing changed yet."
    assert "created .atlas/adopt-manifest.tsv" in out
    assert "created folders: " + ", ".join(_DIRS) in out
    assert ".atlas/lab" in out
    assert "first board line on board.md" in out
    assert (
        f"Backup: a new folder under {lab}/.atlas/backup/ (the exact path is printed when applied)"
        in out
    )
    assert not (lab / "board.md").exists()
    assert not (home / ".atlas" / "lab").exists()


def test_reapply_then_uninstall_returns_to_the_original(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    original = '{"profile": "work", "update": "off"}\n'
    (lab / "atlas.json").write_text(original, encoding="utf-8")
    (lab / "CLAUDE.md").write_text("my own rules\n", encoding="utf-8")
    assert _apply(home, lab, "--lead-name", "Ada") == 0
    assert _apply(home, lab, "--lead-name", "Lin") == 0
    assert adopt.run(["--uninstall", "--lab", str(lab)]) == 0
    assert (lab / "atlas.json").read_text(encoding="utf-8") == original
    assert (lab / "CLAUDE.md").read_text(encoding="utf-8") == "my own rules\n"


def test_sparse_file_from_an_older_adopt_is_completed(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    sparse = {"profile": "work", "update": "off", "lead": "Lead A", "memory": False}
    (lab / "atlas.json").write_text(json.dumps(sparse), encoding="utf-8")
    assert _apply(home, lab, "--lead-name", "Lead A") == 0
    data = json.loads((lab / "atlas.json").read_text(encoding="utf-8"))
    assert {"v", "owner", "tz", "nodes"} <= set(data) and data["lead"] == "Lead A"


def test_created_then_edited_then_reapplied_keeps_the_users_edits(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    assert _apply(home, lab, "--lead-name", "Ada") == 0
    claude = lab / "CLAUDE.md"
    claude.write_text("my notes\n" + claude.read_text(encoding="utf-8"), encoding="utf-8")
    assert _apply(home, lab, "--lead-name", "Lin") == 0
    assert adopt.run(["--uninstall", "--lab", str(lab)]) == 0
    assert claude.is_file() and "my notes" in claude.read_text(encoding="utf-8")


def test_lead_rename_updates_cursor_rule_and_uninstall_removes_created_files(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    assert _apply(home, lab, "--lead-name", "Old") == 0
    assert _apply(home, lab, "--lead-name", "New") == 0
    rule = lab / ".cursor" / "rules" / "atlas.mdc"
    assert rule.read_text(encoding="utf-8") == adopt._cursor_text("New")
    assert "The Lead is New." in (lab / "CLAUDE.md").read_text(encoding="utf-8")
    assert json.loads((lab / "atlas.json").read_text(encoding="utf-8"))["lead"] == "New"
    assert adopt.run(["--uninstall", "--yes", "--lab", str(lab)]) == 0
    assert not rule.exists()
    assert not (lab / "CLAUDE.md").exists()
    assert not (lab / "atlas.json").exists()


def test_hand_edited_cursor_rule_is_unchanged_by_lead_rename(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    assert _apply(home, lab, "--lead-name", "Old") == 0
    rule = lab / ".cursor" / "rules" / "atlas.mdc"
    rule.write_text(rule.read_text(encoding="utf-8") + "user added this\n", encoding="utf-8")
    edited = rule.read_text(encoding="utf-8")
    assert _apply(home, lab, "--lead-name", "New") == 0
    text = rule.read_text(encoding="utf-8")
    assert text == edited
    assert "user added this" in text and "The Lead is Old." in text


def test_user_text_outside_the_block_survives_a_lead_rename_uninstall(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    assert _apply(home, lab, "--lead-name", "Old") == 0
    claude = lab / "CLAUDE.md"
    claude.write_text("my notes\n" + claude.read_text(encoding="utf-8"), encoding="utf-8")
    edited = claude.read_text(encoding="utf-8")
    assert edited.startswith("my notes\n") and "The Lead is Old." in edited
    assert _apply(home, lab, "--lead-name", "New") == 0
    assert "The Lead is New." in claude.read_text(encoding="utf-8")
    assert adopt.run(["--uninstall", "--yes", "--lab", str(lab)]) == 0
    # The merged row's backup is the edited file, so undo restores it, Old block included.
    assert claude.read_text(encoding="utf-8") == edited


def test_remember_lab_rewrites_an_undecodable_pointer(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    pointer = tmp_path / ".atlas" / "lab"
    pointer.parent.mkdir()
    pointer.write_bytes(b"\xff\xfe bad")
    lab = tmp_path / "lab"
    adopt._remember_lab(lab)
    assert pointer.read_text(encoding="utf-8") == str(lab.resolve()) + "\n"


def test_ensure_failure_keeps_a_manifest_row_for_uninstall(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    original = "Keep this line.\n"
    (lab / "CLAUDE.md").write_text(original, encoding="utf-8")

    def fail(_dest, owner="Owner"):
        raise OSError("disk full")

    monkeypatch.setattr("atlas.init.ensure", fail)
    assert _apply(home, lab, "--lead-name", "Ada") == 1
    err = capsys.readouterr().err
    assert "Partly applied" in err and "Undo:" in err
    manifest = (lab / ".atlas" / "adopt-manifest.tsv").read_text(encoding="utf-8").splitlines()
    assert any(line.split("\t")[1] == "CLAUDE.md" for line in manifest)
    assert adopt.run(["--uninstall", "--lab", str(lab)]) == 0
    assert (lab / "CLAUDE.md").read_text(encoding="utf-8") == original


def test_manifest_write_failure_says_restore_from_the_backup(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    original = "Keep this line.\n"
    (lab / "CLAUDE.md").write_text(original, encoding="utf-8")

    def fail(_lab, _rows):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr("atlas.adopt._write_manifest", fail)
    assert _apply(home, lab, "--lead-name", "Ada") == 1
    err = capsys.readouterr().err
    assert "Restore by hand from the backup" in err
    assert "Undo:" not in err
    backup = Path(err.split("backup: ", 1)[1].splitlines()[0].strip())
    assert (backup / "CLAUDE.md").read_bytes() == original.encode("utf-8")


def test_non_utf8_lab_file_refuses_apply(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    raw = b"\xff\xfe not utf-8"
    for name in ("CLAUDE.md", "atlas.json"):
        lab = tmp_path / name
        lab.mkdir()
        (lab / name).write_bytes(raw)
        code = _apply(home, lab, "--lead-name", "Ada")
        err = capsys.readouterr().err
        assert code != 0
        assert "Traceback" not in err
        assert "atlas adopt:" in err
        assert (lab / name).read_bytes() == raw


def _home_with_chat(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    chat = home / ".claude" / "projects" / "-tmp-widget"
    chat.mkdir(parents=True)
    (chat / "a.jsonl").write_text('{"type":"custom-title","title":"private chat title"}\n', encoding="utf-8")
    return home, chat / "a.jsonl"


def _spy_open(monkeypatch):
    opened = []
    real = open

    def spy(path, *a, **k):
        opened.append(str(path))
        return real(path, *a, **k)

    monkeypatch.setattr(adopt, "open", spy, raising=False)
    return opened


def _raise(exc):
    def ask(prompt=""):
        raise exc
    return ask


@pytest.mark.parametrize(
    "tty, answer",
    [(False, None), (True, "n"), (True, ""), (True, EOFError), (True, KeyboardInterrupt)],
    ids=["no-tty", "n", "enter", "eof", "ctrl-c"],
)
def test_apply_without_a_scan_asks_before_reading_transcripts(tmp_path, monkeypatch, capsys, tty, answer):
    home, transcript = _home_with_chat(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    before_home, before_lab = _snap(home), _snap(lab)
    opened = _spy_open(monkeypatch)
    monkeypatch.setattr("sys.stdin.isatty", lambda: tty)
    if isinstance(answer, type):
        monkeypatch.setattr("builtins.input", _raise(answer))
    elif answer is not None:
        monkeypatch.setattr("builtins.input", lambda prompt="": answer)
    assert adopt.run(["--apply", "--home", str(home), "--lab", str(lab), "--lead-name", "Ada"]) == 1
    out = capsys.readouterr().out
    assert "--apply scans first" in out and "chat transcripts" in out
    assert out.splitlines()[-1] == "Nothing changed yet."
    assert not any(p.endswith(".jsonl") for p in opened)
    assert not (lab / ".atlas").exists()
    assert _snap(lab) == before_lab
    assert _snap(home) == before_home


def test_apply_scan_yes_then_apply_no_keeps_only_the_report(tmp_path, monkeypatch, capsys):
    home, transcript = _home_with_chat(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    answers = iter(["y", "n"])
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert adopt.run(["--apply", "--home", str(home), "--lab", str(lab), "--lead-name", "Ada"]) == 1
    files = sorted(p.relative_to(lab) for p in lab.rglob("*") if p.is_file())
    assert files == [Path(".atlas/adopt-scan.json")]


def test_apply_yes_scans_and_applies_without_asking(tmp_path, monkeypatch):
    home, transcript = _home_with_chat(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    opened = _spy_open(monkeypatch)

    def no_prompt(prompt=""):
        raise AssertionError(f"asked: {prompt}")

    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    monkeypatch.setattr("builtins.input", no_prompt)
    assert _apply(home, lab, "--lead-name", "Ada") == 0
    assert not any(p.endswith(".jsonl") for p in opened)
    assert (lab / ".atlas" / "adopt-scan.json").is_file() and (lab / "atlas.json").is_file()


def test_main_apply_refusal_exits_nonzero(tmp_path, monkeypatch):
    home, _ = _home_with_chat(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    with pytest.raises(SystemExit) as exc:
        main(["adopt", "--apply", "--home", str(home), "--lab", str(lab)])
    assert exc.value.code == 1
    assert not lab.exists()
