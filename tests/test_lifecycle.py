"""Round-trip init, hooks, the echo job, and uninstall."""

from __future__ import annotations

import json, socket, sys

import pytest

from atlas import hooks, init
from atlas.__main__ import main
from atlas.lifecycle import echo_job, uninstall

_SETTINGS = json.dumps({
    "theme": "dark",
    "env": {"A": "1"},
    "hooks": {"PreToolUse": [{"hooks": [{"type": "command", "command": "/bin/true"}]}]},
}, indent=2) + "\n"


def _files(root):
    found = {}
    if root.exists():
        for path in root.rglob("*"):
            if path.is_file():
                found[str(path.relative_to(root))] = (path.read_bytes(), path.stat().st_mode & 0o777)
    return found


def _home(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    (home / "keep.txt").write_text("keep\n", encoding="utf-8")
    (home / "keep.txt").chmod(0o644)
    return home


@pytest.mark.parametrize("settings,decision", [(True, "stay\n"), (True, None), (False, None)])
def test_roundtrip(tmp_path, monkeypatch, settings, decision):
    home = _home(tmp_path, monkeypatch)
    if settings:
        path = home / ".claude" / "settings.json"
        path.parent.mkdir()
        path.write_text(_SETTINGS, encoding="utf-8")
        path.chmod(0o644)
    before = _files(home)
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    hooks.run(["--lab", str(lab), "--yes"])
    hooked = home / ".claude" / "settings.json"
    start = hooks.hook_commands(lab)[0]
    assert start.startswith(str(home / ".atlas" / "hooks" / "atlas-hook-start-"))
    assert hooked.is_file() and start in hooked.read_text(encoding="utf-8")
    assert str(lab / "bin" / "atlas-hook-start") not in hooked.read_text(encoding="utf-8")
    if settings:
        assert hooked.stat().st_mode & 0o777 == 0o644
        # L7: uninstall restores the settings file at 0600, whatever mode the lab backup carries.
        before[".claude/settings.json"] = (before[".claude/settings.json"][0], 0o600)
    assert echo_job(lab) is True
    if decision is not None:
        (lab / "decisions" / "ruling.md").write_text(decision, encoding="utf-8")
    assert uninstall(["--lab", str(lab), "--yes"]) == 0
    assert _files(home) == before and not lab.exists()
    kept = _kept(lab)
    if decision is None:
        assert not kept.exists()
    else:
        assert (kept / "ruling.md").read_text(encoding="utf-8") == decision
    if not settings:
        assert not (home / ".claude").exists()


def _kept(lab):
    return lab.parent / (lab.name + ".decisions-kept")


def test_doctor_fresh_ok_and_broken_json(tmp_path, monkeypatch, capsys):
    _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    with pytest.raises(SystemExit) as exc:
        main(["doctor", "--lab", str(lab), "--port", str(port)])
    out = capsys.readouterr().out
    assert exc.value.code == 0 and "FIX:" not in out and "not connected: run atlas hooks" in out
    (lab / "atlas.json").write_text("{", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        main(["doctor", "--lab", str(lab), "--port", str(port)])
    assert exc.value.code == 1 and "FIX:" in capsys.readouterr().out


def test_echo_job_once(tmp_path, monkeypatch):
    _home(tmp_path, monkeypatch)
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    assert echo_job(lab) is True
    done = list((lab / "queue" / "done").glob("echo-*.md"))
    assert len(done) == 1
    text = done[0].read_text(encoding="utf-8")
    assert "id: echo-hello\n" in text and "from: Owner\n" in text and "to: Worker\n" in text
    assert "type: job\n" in text and "\ncreated: " in text and text.endswith("echo: hello\n")
    board = (lab / "board.md").read_text(encoding="utf-8")
    assert board.count(" · Worker · working · Echo job started") == 1
    assert board.count(" · Worker · done · Echo job delivered") == 1
    assert echo_job(lab) is False and (lab / "board.md").read_text(encoding="utf-8") == board
    assert list((lab / "queue" / "inbox").glob("echo-*.md")) == []
    assert list((lab / "queue" / "running").glob("echo-*.md")) == []


def test_uninstall_without_yes_changes_nothing(tmp_path, monkeypatch):
    home = _home(tmp_path, monkeypatch)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    hooks.run(["--lab", str(lab), "--yes"])
    (lab / "decisions" / "ruling.md").write_text("stay\n", encoding="utf-8")
    before_home, before_lab = _files(home), _files(lab)
    assert uninstall(["--lab", str(lab)]) == 0
    assert _files(home) == before_home and _files(lab) == before_lab
    assert not _kept(lab).exists()


def test_uninstall_refuses_home_and_non_labs(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / "stuff").mkdir(parents=True)
    (home / "stuff" / "keep.txt").write_text("mine", encoding="utf-8")
    monkeypatch.setenv("HOME", str(home))
    for target in (home, tmp_path, home / "stuff"):
        with pytest.raises(SystemExit) as raised:
            uninstall(["--lab", str(target), "--yes"])
        assert raised.value.code == 2
    assert (home / "stuff" / "keep.txt").read_text(encoding="utf-8") == "mine"


def test_up_serves_the_lab_and_leaves_home_alone(tmp_path):
    import os, subprocess, time, urllib.request
    from pathlib import Path

    home = tmp_path / "home"
    home.mkdir()
    # WHY: macOS's python3 keeps its bytecode cache in ~/Library/Caches/com.apple.python
    # (sys.pycache_prefix). That is Python's cache, not Atlas's, so keep it out of HOME here.
    env = {**os.environ, "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("ATLAS_LAB", None)
    lab = tmp_path / "lab"
    repo = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-m", "atlas", "init", "--yes", "--dir", str(lab)], cwd=repo, env=env, check=True, capture_output=True)
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    proc = subprocess.Popen([sys.executable, "-m", "atlas", "up", "--lab", str(lab), "--port", str(port)], cwd=repo, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        state = None
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/state", timeout=1) as res:
                    state = json.load(res)
                break
            except OSError:
                time.sleep(0.2)
        assert state is not None
        assert sorted(state["nodes"]) == ["lead", "owner", "worker"]
    finally:
        proc.terminate()
        proc.wait(timeout=10)
    assert list(home.iterdir()) == []


def test_uninstall_stops_when_settings_unreadable(tmp_path, monkeypatch):
    home = tmp_path / "home"
    (home / ".claude").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    lab = tmp_path / "lab"
    init.run(["--yes", "--dir", str(lab)])
    (home / ".claude" / "settings.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(SystemExit) as raised:
        uninstall(["--lab", str(lab), "--yes"])
    assert raised.value.code == 1
    assert (lab / "atlas.json").is_file()


def test_uninstall_through_a_symlinked_lab(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    real = tmp_path / "real-lab"
    init.run(["--yes", "--dir", str(real)])
    link = tmp_path / "lab-link"
    link.symlink_to(real)
    assert uninstall(["--lab", str(link), "--yes"]) == 0
    assert not real.exists() and not link.exists() and not link.is_symlink()


def test_up_with_broken_config_exits_2(tmp_path):
    import os, subprocess
    from pathlib import Path

    home = tmp_path / "home"
    home.mkdir()
    # WHY: macOS's python3 keeps its bytecode cache in ~/Library/Caches/com.apple.python
    # (sys.pycache_prefix). That is Python's cache, not Atlas's, so keep it out of HOME here.
    env = {**os.environ, "HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
    env.pop("ATLAS_LAB", None)
    lab = tmp_path / "lab"
    repo = Path(__file__).resolve().parents[1]
    subprocess.run([sys.executable, "-m", "atlas", "init", "--yes", "--dir", str(lab)], cwd=repo, env=env, check=True, capture_output=True)
    (lab / "atlas.json").write_text('{"v": 1}', encoding="utf-8")
    res = subprocess.run([sys.executable, "-m", "atlas", "up", "--lab", str(lab), "--port", "0"], cwd=repo, env=env, capture_output=True, text=True, timeout=30)
    assert res.returncode == 2
    assert "Traceback" not in res.stderr and "atlas.json" in res.stderr


def test_atlas_turns_off_bytecode_caching(tmp_path):
    # macOS python3 would cache Atlas's compiled code under ~/Library/Caches; Atlas switches that off.
    import os, subprocess
    from pathlib import Path

    env = {k: v for k, v in os.environ.items() if k != "PYTHONDONTWRITEBYTECODE"}
    repo = Path(__file__).resolve().parents[1]
    res = subprocess.run([sys.executable, "-c", "import atlas, sys; print(sys.dont_write_bytecode)"], cwd=repo, env=env, capture_output=True, text=True, check=True)
    assert res.stdout.strip() == "True"
