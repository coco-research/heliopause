from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_scaffold_files_exist():
    assert (ROOT / "README.md").is_file()
    assert (ROOT / "LICENSE").is_file()
    assert (ROOT / "AGENTS.md").is_file()
