"""adopt never writes, copies or deletes through a link planted in the lab, with or without --yes.

Lab agents can write anywhere in the lab. Every case here plants a symlink or hard link there and checks that
apply, mark and uninstall refuse it, leave the target untouched, and write nothing else.
"""

import hashlib
import json
import os
import time
from pathlib import Path

import pytest

from atlas import adopt

ROW = "/tmp/x$(touch /tmp/pwned-by-scan)"


def _home(tmp_path, monkeypatch) -> Path:
    root = tmp_path / "home"
    (root / ".claude").mkdir(parents=True)
    (root / ".claude" / "settings.json").write_text('{"theme": "dark"}\n', encoding="utf-8")
    monkeypatch.setenv("HOME", str(root))
    monkeypatch.setattr(Path, "home", lambda: root)
    return root


def _lab(tmp_path, home: Path) -> Path:
    lab = tmp_path / "lab"
    (lab / ".atlas").mkdir(parents=True)
    scan = lab / ".atlas" / "adopt-scan.json"
    scan.write_text(
        json.dumps(
            {
                "folders": {},
                "suggest_ignore": [{"path": ROW, "reason": "inactive"}],
                "schema": 1,
                "created": int(time.time()),
                "home": str(home.resolve()),
            }
        ),
        encoding="utf-8",
    )
    scan.chmod(0o600)
    return lab


def _apply(home: Path, lab: Path, monkeypatch, interactive: bool) -> int:
    argv = ["--apply", "--home", str(home), "--lab", str(lab), "--lead-name", "Ada"]
    if interactive:
        monkeypatch.setattr("sys.stdin.isatty", lambda: True)
        monkeypatch.setattr("builtins.input", lambda prompt="": "y")
        return adopt.run(argv)
    return adopt.run([*argv, "--yes"])


def _tree(root: Path) -> dict:
    found = {}
    for path in sorted(root.rglob("*")):
        if path.is_symlink():
            found[str(path)] = ("link", os.readlink(path))
        elif path.is_file():
            found[str(path)] = ("file", path.read_bytes())
        elif path.is_dir():
            found[str(path)] = ("dir",)
    return found


def _refused(capsys) -> str:
    err = capsys.readouterr().err
    assert "refusing" in err and "Nothing changed." in err, err
    assert "Traceback" not in err
    return err


@pytest.fixture(params=[False, True], ids=["yes", "interactive"])
def interactive(request):
    return request.param


def test_dangling_ignore_link_cannot_create_a_shell_rc_file(tmp_path, monkeypatch, capsys, interactive):
    home = _home(tmp_path, monkeypatch)
    lab = _lab(tmp_path, home)
    rc = home / ".zshrc-new"
    (lab / ".atlas" / "ignore").symlink_to(rc)
    before = _tree(lab)
    assert _apply(home, lab, monkeypatch, interactive) == 1
    _refused(capsys)
    assert not rc.exists()
    assert _tree(lab) == before


def test_claude_md_link_cannot_merge_into_claude_settings(tmp_path, monkeypatch, capsys, interactive):
    home = _home(tmp_path, monkeypatch)
    lab = _lab(tmp_path, home)
    settings = home / ".claude" / "settings.json"
    raw = settings.read_bytes()
    (lab / "CLAUDE.md").symlink_to(settings)
    before = _tree(lab)
    assert _apply(home, lab, monkeypatch, interactive) == 1
    _refused(capsys)
    assert settings.read_bytes() == raw
    json.loads(settings.read_text(encoding="utf-8"))
    assert _tree(lab) == before


def test_start_here_link_cannot_create_a_file_outside_home(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = _lab(tmp_path, home)
    far = tmp_path / "elsewhere" / "planted.md"
    far.parent.mkdir()
    (lab / "START-HERE.md").symlink_to(far)
    assert _apply(home, lab, monkeypatch, False) == 1
    _refused(capsys)
    assert not far.exists()


def test_symlinked_bin_outside_the_lab_gets_nothing(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = _lab(tmp_path, home)
    outside = tmp_path / "outside-bin"
    outside.mkdir()
    (lab / "bin").symlink_to(outside, target_is_directory=True)
    assert _apply(home, lab, monkeypatch, False) == 1
    _refused(capsys)
    assert list(outside.iterdir()) == []
    assert not (lab / "atlas.json").exists()


@pytest.mark.parametrize("sub", ["nodes", "queue", ".cursor"])
def test_other_symlinked_lab_folders_are_refused(tmp_path, monkeypatch, capsys, sub):
    home = _home(tmp_path, monkeypatch)
    lab = _lab(tmp_path, home)
    outside = tmp_path / "outside"
    outside.mkdir()
    (lab / sub).symlink_to(outside, target_is_directory=True)
    assert _apply(home, lab, monkeypatch, False) == 1
    _refused(capsys)
    assert list(outside.iterdir()) == []


def test_symlinked_atlas_folder_is_refused_by_load_scan_and_apply(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    real = _lab(tmp_path, home) / ".atlas"
    lab = tmp_path / "lab2"
    lab.mkdir()
    elsewhere = tmp_path / "atlas-dir"
    real.rename(elsewhere)
    (lab / ".atlas").symlink_to(elsewhere, target_is_directory=True)
    data, why = adopt.load_scan(lab / ".atlas" / "adopt-scan.json", home)
    assert data is None and why == "in a symlinked folder"
    before = _tree(elsewhere)
    assert _apply(home, lab, monkeypatch, False) == 1
    err = capsys.readouterr().err
    assert ".atlas/ is a symlink" in err and "Traceback" not in err
    assert _tree(elsewhere) == before
    assert not (lab / "atlas.json").exists()


def test_hard_linked_claude_md_is_refused(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = _lab(tmp_path, home)
    rc = home / ".bashrc"
    rc.write_text("# mine\n", encoding="utf-8")
    os.link(rc, lab / "CLAUDE.md")
    assert _apply(home, lab, monkeypatch, False) == 1
    err = _refused(capsys)
    assert "hard links" in err
    assert rc.read_text(encoding="utf-8") == "# mine\n"
    assert not (lab / ".atlas" / "backup").exists()


def test_linked_claude_md_is_not_copied_into_the_backup(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = _lab(tmp_path, home)
    secret = home / "secret-key"
    secret.write_text("PRIVATE\n", encoding="utf-8")
    (lab / "CLAUDE.md").symlink_to(secret)
    assert _apply(home, lab, monkeypatch, False) == 1
    _refused(capsys)
    for path in lab.rglob("*"):
        if path.is_file() and not path.is_symlink():
            assert b"PRIVATE" not in path.read_bytes()


def test_symlinked_home_atlas_folder_gets_no_pointer(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = _lab(tmp_path, home)
    outside = tmp_path / "dotfiles-atlas"
    outside.mkdir()
    (home / ".atlas").symlink_to(outside, target_is_directory=True)
    assert _apply(home, lab, monkeypatch, False) == 0
    assert "not writing the lab pointer" in capsys.readouterr().err
    assert list(outside.iterdir()) == []


def test_uninstall_does_not_delete_through_a_linked_folder(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    (lab / ".atlas").mkdir(parents=True)
    victim_dir = tmp_path / "victim"
    victim_dir.mkdir()
    victim = victim_dir / "notes.md"
    victim.write_bytes(b"keep me\n")
    (lab / "docs").symlink_to(victim_dir, target_is_directory=True)
    digest = hashlib.sha256(b"keep me\n").hexdigest()
    (lab / ".atlas" / "adopt-manifest.tsv").write_text(f"created\tdocs/notes.md\t{digest}\t-\n", encoding="utf-8")
    assert adopt.run(["--uninstall", "--lab", str(lab)]) == 0
    assert victim.read_bytes() == b"keep me\n"
    assert "not touched" in capsys.readouterr().out


def test_uninstall_does_not_strip_through_a_hard_link(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    (lab / ".atlas").mkdir(parents=True)
    rc = home / ".profile"
    raw = b"# mine\n<!-- atlas:begin v1 -->\nblock\n<!-- atlas:end -->\n"
    rc.write_bytes(raw)
    os.link(rc, lab / "CLAUDE.md")
    (lab / ".atlas" / "adopt-manifest.tsv").write_text("merged\tCLAUDE.md\t" + "0" * 64 + "\t-\n", encoding="utf-8")
    assert adopt.run(["--uninstall", "--lab", str(lab)]) == 0
    assert rc.read_bytes() == raw
    assert "hard links; not touched" in capsys.readouterr().out


def test_mark_refuses_a_symlinked_projects_folder(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = _lab(tmp_path, home)
    (lab / "atlas.json").write_text('{"v": 1}\n', encoding="utf-8")
    proj = tmp_path / "widget"
    proj.mkdir()
    outside = tmp_path / "outside-projects"
    outside.mkdir()
    (lab / "projects").symlink_to(outside, target_is_directory=True)
    monkeypatch.setenv("PATH", "/nonexistent")
    assert adopt.run(["--mark", str(proj), "--lab", str(lab)]) == 1
    assert "projects/ is a symlink" in capsys.readouterr().err
    assert list(outside.iterdir()) == []


def test_mark_refuses_a_linked_atlas_json(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = _lab(tmp_path, home)
    target = home / ".claude" / "settings.json"
    raw = target.read_bytes()
    (lab / "atlas.json").symlink_to(target)
    proj = tmp_path / "widget"
    proj.mkdir()
    monkeypatch.setenv("PATH", "/nonexistent")
    assert adopt.run(["--mark", str(proj), "--lab", str(lab)]) == 1
    assert "refusing" in capsys.readouterr().err
    assert target.read_bytes() == raw


def test_scan_refuses_a_symlinked_atlas_folder(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    lab.mkdir()
    outside = tmp_path / "outside-atlas"
    outside.mkdir()
    (lab / ".atlas").symlink_to(outside, target_is_directory=True)
    assert adopt.run(["--scan", "--home", str(home), "--lab", str(lab)]) == 1
    assert ".atlas/ is a symlink" in capsys.readouterr().err
    assert list(outside.iterdir()) == []


def test_only_the_lab_pointer_is_an_allowed_home_file():
    assert adopt.HOME_FILES == (".atlas/lab",)


def test_without_hard_links_apply_still_works(tmp_path, monkeypatch, capsys):
    # L5: exFAT, some SMB and FUSE mounts refuse hard links; labfs then creates each new file exclusively.
    home = _home(tmp_path, monkeypatch)
    lab = _lab(tmp_path, home)
    from atlas import labfs

    def no_link(*a, **k):
        raise OSError(1, "Operation not permitted")

    monkeypatch.setattr(labfs.os, "link", no_link)
    assert _apply(home, lab, monkeypatch, False) == 0
    assert (lab / "CLAUDE.md").is_file() and (lab / "START-HERE.md").is_file()
    assert not [p for p in lab.rglob("*") if ".atlas-" in p.name]


def test_a_failed_apply_names_the_path_and_leaves_no_empty_backup_folder(tmp_path, monkeypatch, capsys):
    home = _home(tmp_path, monkeypatch)
    lab = _lab(tmp_path, home)
    from atlas import labfs

    real_open = os.open

    def no_link(*a, **k):
        raise OSError(1, "Operation not permitted")

    def no_create(path, flags, *a, **k):
        if flags & os.O_EXCL and not str(path).startswith("."):
            raise OSError(1, "Operation not permitted")
        return real_open(path, flags, *a, **k)

    monkeypatch.setattr(labfs.os, "link", no_link)
    monkeypatch.setattr(labfs.os, "open", no_create)
    assert _apply(home, lab, monkeypatch, False) == 1
    err = capsys.readouterr().err
    line = next(row for row in err.splitlines() if row.startswith("atlas adopt: "))
    assert str(lab) in line and "Operation not permitted" in line
    assert not (lab / ".atlas" / "backup").exists()
