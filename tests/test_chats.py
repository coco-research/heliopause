import builtins
import os
from pathlib import Path

from atlas.chats import chat_activity

BASE = "-Users-someone-Alex-Doe-Coco-Research"


def _jsonl(root: Path, folder: str, name: str, mtime: float) -> None:
    path = root / ".claude" / "projects" / folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{}", encoding="utf-8")
    os.utime(path, (mtime, mtime))


def test_mapping_newest_wins_and_ignores(tmp_path, monkeypatch):
    _jsonl(tmp_path, BASE, "a.jsonl", 1000)
    _jsonl(tmp_path, BASE, "b.jsonl", 2000)
    _jsonl(tmp_path, BASE, "notes.txt", 9000)
    _jsonl(tmp_path, BASE + "-products-coco-atlas", "a.jsonl", 3000)
    _jsonl(tmp_path, BASE + "-products-coco", "a.jsonl", 4000)
    _jsonl(tmp_path, BASE + "-ops-departments-design", "a.jsonl", 5000)
    _jsonl(tmp_path, BASE + "-products-coco", "subagents/x.jsonl", 9999)
    _jsonl(tmp_path, "-Users-someone-elsewhere", "a.jsonl", 8000)

    def banned(*_args, **_kwargs):
        raise AssertionError("a transcript was opened")

    monkeypatch.setattr(builtins, "open", banned)
    monkeypatch.setattr(Path, "read_text", banned)
    monkeypatch.setattr(Path, "read_bytes", banned)
    assert chat_activity(tmp_path) == {
        "vp": 2000,
        "brain-atlas": 3000,
        "brain-si": 4000,
        "dept-design": 5000,
    }


def test_missing_projects_dir(tmp_path):
    assert chat_activity(tmp_path) == {}
