"""行数の検査の振る舞いを固定する（#354 / #399）。

**基準（501 行以上）は `markdown-writing` のルール 9 が定める。** この検査はそれを機械で
見る。走査の対象は git が追跡する `.md` で、記録は外す。書式が 1 ファイルであることを
前提にする文書は `EXEMPT` で外し、理由を値に持つ。
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
CHECK = REPO / "scripts" / "check-doc-line-limit.py"


def run(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["python3", str(CHECK), "--root", str(root), *args],
        capture_output=True, text=True,
    )


def output_of(result: subprocess.CompletedProcess) -> str:
    return result.stdout + result.stderr


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """git が追跡する `.md` を持つ最小のリポジトリ。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "issues").mkdir()
    (tmp_path / "docs" / "short.md").write_text("x\n" * 10, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(tmp_path), "add", "-A"], check=True, capture_output=True
    )
    return tmp_path


def track(root: Path, rel: str, lines: int) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("x\n" * lines, encoding="utf-8")
    subprocess.run(
        ["git", "-C", str(root), "add", rel], check=True, capture_output=True
    )
    return path


def test_a_document_within_the_limit_passes(repo: Path) -> None:
    track(repo, "docs/ok.md", 500)
    result = run(repo)
    assert result.returncode == 0, output_of(result)


def test_a_document_over_the_limit_fails(repo: Path) -> None:
    track(repo, "docs/long.md", 501)
    result = run(repo)
    assert result.returncode == 1
    out = output_of(result)
    assert "docs/long.md" in out and "501" in out


def test_records_are_not_scanned(repo: Path) -> None:
    """記録は起きたことをそのまま残すもので、分割の対象ではない。"""
    track(repo, "issues/plan.md", 900)
    result = run(repo)
    assert result.returncode == 0, output_of(result)


def test_untracked_files_are_not_scanned(repo: Path) -> None:
    """追跡していないファイルを入れると、実行した環境で結果が変わる。"""
    (repo / "docs" / "scratch.md").write_text("x\n" * 900, encoding="utf-8")
    result = run(repo)
    assert result.returncode == 0, output_of(result)


def test_an_exempt_document_over_the_limit_passes(repo: Path) -> None:
    """書式が 1 ファイルを前提にする文書は外す（`CHANGELOG.md`）。"""
    track(repo, "CHANGELOG.md", 900)
    result = run(repo)
    assert result.returncode == 0, output_of(result)


def test_an_exempt_document_under_the_limit_fails(repo: Path) -> None:
    """外したのに基準を下回っているなら、除外そのものが要らない。"""
    track(repo, "CHANGELOG.md", 10)
    result = run(repo)
    assert result.returncode == 1
    assert "EXEMPT" in output_of(result)


def test_an_empty_scan_is_a_failure(tmp_path: Path) -> None:
    """走査対象が 0 件のまま通ると、検査が働いていないことに気づけない。"""
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    result = run(tmp_path)
    assert result.returncode == 2


def test_the_report_lists_the_longest_documents(repo: Path) -> None:
    track(repo, "docs/mid.md", 400)
    result = run(repo, "--report")
    assert result.returncode == 0, output_of(result)
    assert "docs/mid.md" in result.stdout


def test_the_check_is_wired_into_the_validation() -> None:
    """既存の検査から呼ばれていなければ、継続的統合では実行されない。"""
    body = (REPO / "scripts" / "validate-runtime-plugins.sh").read_text(encoding="utf-8")
    lines = [
        line for line in body.splitlines()
        if "scripts/check-doc-line-limit.py" in line
        and not line.lstrip().startswith("#")
    ]
    assert lines, "validate-runtime-plugins.sh から呼ばれていない"


def test_every_exemption_carries_a_reason() -> None:
    import importlib.util

    spec = importlib.util.spec_from_file_location("check_doc_line_limit", CHECK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    assert module.EXEMPT
    assert all(reason.strip() for reason in module.EXEMPT.values())


def test_the_repository_satisfies_the_limit() -> None:
    result = run(REPO)
    assert result.returncode == 0, output_of(result)
