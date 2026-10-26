from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "public-ci-check.sh"
REPO_ROOT = SCRIPT.parents[1]
REPO_CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

HOSTED_CI = """\
jobs:
  test:
    runs-on: ${{ matrix.os }}
    strategy:
      matrix:
        os: [ubuntu-latest, macos-latest]
"""


def run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["sh", str(SCRIPT), *args],
        capture_output=True,
        text=True,
    )


def make_tree(root: Path, ci: str | None = None, extra: dict[str, str] | None = None) -> Path:
    workflows = root / ".github" / "workflows"
    workflows.mkdir(parents=True)
    if ci is not None:
        (workflows / "ci.yml").write_text(ci)
    for name, text in (extra or {}).items():
        (workflows / name).write_text(text)
    return root


def test_no_ci_yml_stops(tmp_path: Path) -> None:
    (tmp_path / ".github").mkdir()
    result = run(str(tmp_path))
    assert result.returncode == 3
    assert "no public ci.yml" in result.stderr


def test_empty_ci_yml_stops(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, ci="")
    result = run(str(tree))
    assert result.returncode == 3
    assert "no public ci.yml" in result.stderr


def test_self_hosted_in_ci_stops(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, ci="jobs:\n  test:\n    runs-on: [self-hosted, macOS]\n")
    result = run(str(tree))
    assert result.returncode == 3
    assert "self-hosted" in result.stderr


def test_self_hosted_in_another_workflow_stops(tmp_path: Path) -> None:
    tree = make_tree(
        tmp_path,
        ci=HOSTED_CI,
        extra={"release.yml": "jobs:\n  release:\n    runs-on: self-hosted\n"},
    )
    result = run(str(tree))
    assert result.returncode == 3
    assert "self-hosted" in result.stderr


def test_clean_hosted_tree_passes(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, ci=HOSTED_CI)
    result = run(str(tree))
    assert result.returncode == 0
    assert "hosted runners only" in result.stdout


def test_self_hosted_any_case_stops(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, ci="jobs:\n  test:\n    runs-on: [Self-Hosted, Linux]\n")
    result = run(str(tree))
    assert result.returncode == 3


def test_custom_label_alone_stops(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, ci="jobs:\n  test:\n    runs-on: atlas\n")
    result = run(str(tree))
    assert result.returncode == 3
    assert "atlas" in result.stderr


def test_matrix_with_custom_label_stops(tmp_path: Path) -> None:
    ci = (
        "jobs:\n"
        "  test:\n"
        "    runs-on: ${{ matrix.os }}\n"
        "    strategy:\n"
        "      matrix:\n"
        "        os: [ubuntu-latest, atlas]\n"
    )
    tree = make_tree(tmp_path, ci=ci)
    result = run(str(tree))
    assert result.returncode == 3
    assert "atlas" in result.stderr


def test_hosted_variants_pass(tmp_path: Path) -> None:
    ci = (
        "jobs:\n"
        "  a:\n"
        "    runs-on: ubuntu-22.04\n"
        "  b:\n"
        "    runs-on: macos-14\n"
        "    strategy:\n"
        "      matrix:\n"
        '        os: [ubuntu-latest, "macos-latest", windows-2022]\n'
    )
    tree = make_tree(tmp_path, ci=ci)
    result = run(str(tree))
    assert result.returncode == 0
    assert "hosted runners only" in result.stdout


def test_block_list_runs_on_stops(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, ci="jobs:\n  test:\n    runs-on:\n      - ubuntu-latest\n")
    result = run(str(tree))
    assert result.returncode == 3
    assert "(block)" in result.stderr


def test_other_matrix_key_stops(tmp_path: Path) -> None:
    ci = (
        "jobs:\n"
        "  test:\n"
        "    runs-on: ${{ matrix.platform }}\n"
        "    strategy:\n"
        "      matrix:\n"
        "        platform: [atlas]\n"
    )
    tree = make_tree(tmp_path, ci=ci)
    result = run(str(tree))
    assert result.returncode == 3
    assert "not a GitHub-hosted label" in result.stderr


def test_block_list_os_stops(tmp_path: Path) -> None:
    ci = (
        "jobs:\n"
        "  test:\n"
        "    runs-on: ${{ matrix.os }}\n"
        "    strategy:\n"
        "      matrix:\n"
        "        os:\n"
        "          - my-runner\n"
    )
    tree = make_tree(tmp_path, ci=ci)
    result = run(str(tree))
    assert result.returncode == 3
    assert "(block)" in result.stderr


def test_matrix_include_item_stops(tmp_path: Path) -> None:
    ci = (
        "jobs:\n"
        "  test:\n"
        "    runs-on: ${{ matrix.os }}\n"
        "    strategy:\n"
        "      matrix:\n"
        "        os: [ubuntu-latest]\n"
        "      include:\n"
        "        - os: atlas\n"
    )
    tree = make_tree(tmp_path, ci=ci)
    result = run(str(tree))
    assert result.returncode == 3
    assert "atlas" in result.stderr


def test_made_up_version_label_stops(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, ci="jobs:\n  test:\n    runs-on: ubuntu-9\n")
    result = run(str(tree))
    assert result.returncode == 3


def test_made_up_suffix_label_stops(tmp_path: Path) -> None:
    tree = make_tree(tmp_path, ci="jobs:\n  test:\n    runs-on: ubuntu-24.04-large\n")
    result = run(str(tree))
    assert result.returncode == 3


def test_hosted_suffix_label_passes(tmp_path: Path) -> None:
    ci = (
        "jobs:\n"
        "  a:\n"
        "    runs-on: ubuntu-24.04-arm\n"
        "  b:\n"
        "    runs-on: macos-14-large\n"
    )
    tree = make_tree(tmp_path, ci=ci)
    result = run(str(tree))
    assert result.returncode == 0


def test_trailing_comment_passes(tmp_path: Path) -> None:
    ci = "jobs:\n  test:\n    runs-on: ubuntu-latest  # 22.04\n"
    tree = make_tree(tmp_path, ci=ci)
    result = run(str(tree))
    assert result.returncode == 0
    assert "hosted runners only" in result.stdout


def test_bad_usage(tmp_path: Path) -> None:
    no_args = run()
    assert no_args.returncode == 2
    assert "usage: public-ci-check.sh <tree>" in no_args.stderr

    a_file = tmp_path / "not-a-dir"
    a_file.write_text("")
    file_arg = run(str(a_file))
    assert file_arg.returncode == 2
    assert "usage: public-ci-check.sh <tree>" in file_arg.stderr


@pytest.mark.skipif(
    not REPO_CI.is_file() or "self-hosted" not in REPO_CI.read_text(),
    reason="repo workflows are not self-hosted",
)
def test_repo_workflows_would_be_stopped() -> None:
    result = run(str(REPO_ROOT))
    assert result.returncode == 3
    assert "self-hosted" in result.stderr
