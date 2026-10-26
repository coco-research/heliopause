"""Public zip: lab paths dropped, config stubbed, Atlas still boots."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

import pytest

import private_names

ROOT = Path(__file__).resolve().parents[1]


def _need_tools() -> bool:
    return any(
        shutil.which(cmd) is None
        for cmd in ("git", "zip", "unzip", "shasum", "ssh-keygen", "ssh-agent", "ssh-add")
    )


def _agent_env(key: Path) -> dict[str, str]:
    proc = subprocess.run(["ssh-agent", "-s"], check=True, capture_output=True, text=True)
    env: dict[str, str] = {}
    for line in proc.stdout.splitlines():
        item = line.split(";", 1)[0].strip()
        if "=" not in item:
            continue
        name, value = item.split("=", 1)
        if name in ("SSH_AUTH_SOCK", "SSH_AGENT_PID"):
            env[name] = value
    if "SSH_AUTH_SOCK" not in env or "SSH_AGENT_PID" not in env:
        raise AssertionError(proc.stdout)
    add = subprocess.run(
        ["ssh-add", str(key)],
        check=False,
        capture_output=True,
        text=True,
        env={**os.environ, **env},
    )
    if add.returncode != 0:
        _kill_agent(env)
        raise AssertionError(add.stderr or add.stdout)
    return env


def _kill_agent(env: dict[str, str]) -> None:
    if not env:
        return
    subprocess.run(
        ["ssh-agent", "-k"],
        env={**os.environ, **env},
        capture_output=True,
        text=True,
    )


def _exclude(text: str) -> tuple[list[str], list[str]]:
    drops: list[str] = []
    stubs: list[str] = []
    for raw in text.splitlines():
        line = raw.split("#", 1)[0].strip().rstrip("/")
        if not line:
            continue
        if line.endswith("=> {}"):
            stubs.append(line[: -len("=> {}")].strip().rstrip("/"))
        else:
            drops.append(line)
    return drops, stubs


def _allowed(rel: str) -> bool:
    if rel.startswith("atlas/static/"):
        return True
    parent, _, name = rel.rpartition("/")
    return parent == "atlas" and name.endswith(".py")


def test_public_tree_drops_private_workflows() -> None:
    # The private workflows use the self-hosted Mac runner. In a public repo a fork PR would run on it,
    # so .github/ never ships; the public repo brings its own hosted-runner CI.
    drops, _ = _exclude((ROOT / ".public-exclude").read_text(encoding="utf-8"))
    assert ".github" in drops


def test_public_tree_drops_connect() -> None:
    # connect.py writes Cursor/Codex/Grok hook files with no undo yet, so it must not ship.
    drops, _ = _exclude((ROOT / ".public-exclude").read_text(encoding="utf-8"))
    assert "atlas/connect.py" in drops
    assert "tests/test_connect.py" in drops


@pytest.mark.skipif(_need_tools(), reason="git, zip, unzip, shasum, or ssh-keygen missing")
def test_release_zip(tmp_path: Path) -> None:
    key = tmp_path / "release-key"
    subprocess.run(
        ["ssh-keygen", "-t", "ed25519", "-N", "", "-q", "-f", str(key), "-C", "release-test"],
        check=True,
        capture_output=True,
    )
    agent = _agent_env(key)
    try:
        _release_zip_body(tmp_path, key, agent)
    finally:
        _kill_agent(agent)


def _poison_path(env: dict[str, str], tmp_path: Path) -> tuple[Path, Path]:
    """Tools the release script must not look up on PATH. touch records -h and runs the real one."""
    bindir = tmp_path / "fake-bin"
    bindir.mkdir()
    fake_log = tmp_path / "fake-log"
    touch_log = tmp_path / "touch-log"
    for name in ("zip", "ssh-keygen", "tar", "shasum", "sha256sum", "ssh-add"):
        exe = bindir / name
        exe.write_text(
            "#!/bin/sh\nprintf '%s\\n' \"$0\" >> \"$ATLAS_FAKE_LOG\"\nexit 97\n",
            encoding="utf-8",
        )
        exe.chmod(0o755)
    touch = bindir / "touch"
    touch.write_text(
        "#!/bin/sh\nprintf '%s\\n' \"$*\" >> \"$ATLAS_TOUCH_LOG\"\nexec /usr/bin/touch \"$@\"\n",
        encoding="utf-8",
    )
    touch.chmod(0o755)
    env["ATLAS_FAKE_LOG"] = str(fake_log)
    env["ATLAS_TOUCH_LOG"] = str(touch_log)
    env["PATH"] = str(bindir) + os.pathsep + env["PATH"]
    return fake_log, touch_log


def _release_zip_body(tmp_path: Path, key: Path, agent: dict[str, str]) -> None:
    env = os.environ.copy()
    env.update(agent)
    env["ATLAS_SIGNING_PUBKEY"] = str(key) + ".pub"
    env.pop("ATLAS_SIGNING_KEY", None)
    fake_log, touch_log = _poison_path(env, tmp_path)
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--quiet", str(ROOT), str(clone)], check=True)
    # clone has committed files only. The pack reads this checkout's rules.
    shutil.copy(ROOT / ".public-exclude", clone / ".public-exclude")
    shutil.copy(ROOT / "scripts" / "release-zip.sh", clone / "scripts" / "release-zip.sh")
    subprocess.run(["git", "tag", "t-test"], cwd=clone, check=True)

    out = tmp_path / "out"
    out.mkdir()
    secret = tmp_path / "secret-not-the-zip"
    secret.write_bytes(b"SECRET")
    for planted in ("heliopause-t-test.zip", "heliopause-t-test.zip.sha256", "heliopause-t-test.manifest", "heliopause-t-test.manifest.sig"):
        (out / planted).symlink_to(secret)
    script = clone / "scripts" / "release-zip.sh"

    def pack() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["bash", str(script), "t-test", str(out)],
            cwd=clone,
            check=True,
            capture_output=True,
            text=True,
            env=env,
        )

    first = pack()
    assert not fake_log.exists()
    assert "-h" in touch_log.read_text(encoding="utf-8")
    assert secret.read_bytes() == b"SECRET"
    zip_path = out / "heliopause-t-test.zip"
    manifest_path = out / "heliopause-t-test.manifest"
    sig_path = out / "heliopause-t-test.manifest.sig"
    assert zip_path.is_file() and not zip_path.is_symlink()
    assert manifest_path.is_file() and not manifest_path.is_symlink()
    assert sig_path.is_file() and not sig_path.is_symlink()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    digest = hashlib.sha256(zip_path.read_bytes()).hexdigest()
    assert manifest["filename"] == "heliopause-t-test.zip"
    assert manifest["version"] == "t-test"
    assert manifest["size"] == zip_path.stat().st_size
    assert manifest["sha256"] == digest
    assert manifest["date"].endswith("Z")
    lines = first.stdout.splitlines()
    assert lines[0] == str(zip_path)
    assert lines[1] == digest
    sum_path = out / "heliopause-t-test.zip.sha256"
    assert not sum_path.is_symlink()
    assert sum_path.read_text(encoding="utf-8") == f"{digest}  heliopause-t-test.zip\n"
    fp = subprocess.run(
        ["ssh-keygen", "-lf", str(key) + ".pub"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()[1]
    assert lines[2] == f"release notes: signing key {fp} (ED25519). See SECURITY.md."
    signature = (out / "heliopause-t-test.manifest.sig").read_text(encoding="utf-8")
    assert signature.startswith("-----BEGIN SSH SIGNATURE-----")
    assert not (out / "heliopause-t-test.zip.sig").exists()
    zip_bytes = zip_path.read_bytes()
    pack()
    assert zip_path.read_bytes() == zip_bytes

    unpacked = tmp_path / "unpacked"
    subprocess.run(["unzip", "-q", str(out / "heliopause-t-test.zip"), "-d", str(unpacked)], check=True)
    tree = unpacked / "heliopause-t-test"
    drops, stubs = _exclude((ROOT / ".public-exclude").read_text(encoding="utf-8"))
    paths = {item.relative_to(tree).as_posix() for item in tree.rglob("*")}
    for drop in drops:
        assert drop not in paths
        assert not any(item.startswith(drop + "/") for item in paths)
    for stub in stubs:
        assert (tree / stub).read_text(encoding="utf-8") == "{}\n"
    for rel in ("atlas/aliases.json", "atlas/chats.json", "atlas/repos.json"):
        assert (tree / rel).read_text(encoding="utf-8") == "{}\n"
    assert any(item == "demo/generic" or item.startswith("demo/generic/") for item in paths)
    assert (tree / "demo" / "generic" / "atlas.json").is_file()
    assert not any(item == "demo/home" or item.startswith("demo/home/") for item in paths)
    assert (tree / "atlas").is_dir()
    assert (tree / "README.md").is_file()
    # No empty folder ships, so an excluded vendor/ leaves no entry behind.
    assert not any(path.is_dir() and not any(path.iterdir()) for path in tree.rglob("*"))
    assert "vendor" not in paths

    home = tmp_path / "home"
    home.mkdir()
    lab = tmp_path / "lab"
    env = os.environ.copy()
    for key in ("PYTHONDONTWRITEBYTECODE", "PYTHONPATH", "ATLAS_LAB"):
        env.pop(key, None)
    env["HOME"] = str(home)
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    init = subprocess.run(
        [sys.executable, "-B", "-m", "atlas", "init", "--yes", "--dir", str(lab)],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
    )
    assert init.returncode == 0, init.stderr
    doctor = subprocess.run(
        [sys.executable, "-B", "-m", "atlas", "doctor", "--lab", str(lab), "--port", str(port)],
        cwd=tree,
        env=env,
        capture_output=True,
        text=True,
    )
    assert doctor.returncode == 0, doctor.stdout + doctor.stderr
    assert not (tree / "atlas" / "memory_lite.py").exists()
    for args in (["memory", "--lab", str(lab)], ["init", "--yes", "--dir", str(tmp_path / "lab2"), "--memory"]):
        mem = subprocess.run([sys.executable, "-B", "-m", "atlas", *args], cwd=tree, env=env, capture_output=True, text=True)
        assert mem.returncode == 0 and "memory lite is not included in this build" in mem.stderr, mem.stderr


def _git(repo: Path, *args: str) -> None:
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "t"
    env["GIT_AUTHOR_EMAIL"] = "t@example.com"
    env["GIT_COMMITTER_NAME"] = "t"
    env["GIT_COMMITTER_EMAIL"] = "t@example.com"
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, env=env)


def _plant_script(repo: Path, exclude: str) -> None:
    (repo / "scripts").mkdir(parents=True, exist_ok=True)
    shutil.copy(ROOT / "scripts" / "release-zip.sh", repo / "scripts" / "release-zip.sh")
    (repo / ".public-exclude").write_text(exclude, encoding="utf-8")


def _run_release(tmp_path: Path, repo: Path, out: Path) -> subprocess.CompletedProcess[str]:
    key = tmp_path / "release-key"
    subprocess.run(
        ["/usr/bin/ssh-keygen", "-t", "ed25519", "-N", "", "-q", "-f", str(key), "-C", "t"],
        check=True,
        capture_output=True,
    )
    agent = _agent_env(key)
    env = os.environ.copy()
    env.update(agent)
    env["ATLAS_SIGNING_PUBKEY"] = str(key) + ".pub"
    env.pop("ATLAS_SIGNING_KEY", None)
    try:
        return subprocess.run(
            ["bash", str(repo / "scripts" / "release-zip.sh"), "v1", str(out)],
            cwd=repo,
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
    finally:
        _kill_agent(env)


@pytest.mark.skipif(_need_tools(), reason="git, zip, unzip, shasum, or ssh-keygen missing")
def test_release_zip_refuses_symlink(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    private = tmp_path / "private"
    private.write_bytes(b"PRIVATE")
    real_docs = tmp_path / "real-docs"
    real_docs.mkdir()
    (real_docs / "DECISIONS.md").write_text("keep\n", encoding="utf-8")
    _plant_script(repo, "docs/DECISIONS.md\natlas/aliases.json => {}\n")
    (repo / "docs").symlink_to(real_docs)
    (repo / "atlas").mkdir()
    (repo / "atlas" / "aliases.json").symlink_to(private)
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "core.symlinks", "true")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", "t")
    listed = subprocess.check_output(["git", "-C", str(repo), "ls-tree", "-r", "HEAD"], text=True)
    assert "120000" in listed
    _git(repo, "tag", "v1")
    out = tmp_path / "out"
    proc = _run_release(tmp_path, repo, out)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "symlink" in proc.stderr
    assert private.read_bytes() == b"PRIVATE"
    assert (real_docs / "DECISIONS.md").read_text(encoding="utf-8") == "keep\n"
    assert not out.exists()
    assert proc.stdout == ""


@pytest.mark.skipif(_need_tools(), reason="git, zip, unzip, shasum, or ssh-keygen missing")
def test_release_zip_refuses_newline_name(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _plant_script(repo, "# none\n")
    blob = subprocess.check_output(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=b"hello\n",
    ).decode().strip()
    tree = subprocess.check_output(
        ["git", "-C", str(repo), "mktree", "-z"],
        input=f"100644 blob {blob}\tbad\nname\0".encode(),
    ).decode().strip()
    env = os.environ.copy()
    env["GIT_AUTHOR_NAME"] = "t"
    env["GIT_AUTHOR_EMAIL"] = "t@example.com"
    env["GIT_COMMITTER_NAME"] = "t"
    env["GIT_COMMITTER_EMAIL"] = "t@example.com"
    commit = subprocess.check_output(
        ["git", "-C", str(repo), "commit-tree", tree, "-m", "nl"],
        env=env,
    ).decode().strip()
    subprocess.run(["git", "-C", str(repo), "tag", "v1", commit], check=True, capture_output=True)
    out = tmp_path / "out"
    proc = _run_release(tmp_path, repo, out)
    assert proc.returncode != 0, proc.stdout + proc.stderr
    assert "newline" in proc.stderr
    assert not out.exists()
    assert proc.stdout == ""
