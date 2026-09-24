"""`refactor.py assess` を、**実際の git リポジトリ**と入口の CLI で確かめる（#494 の AC12〜AC14）。

起点のコミットに対して差分を 1 つ積み、終了コード（0 = 通す / 3 = 飛ばしてよい /
2 = 判定できない）と、出力の 3 行（`判定:` / `理由:` / `本番コード:`）を見る。
"""
from __future__ import annotations

import pathlib
import subprocess
import sys

import pytest

from crossref_helpers import run_git

_SCRIPT = pathlib.Path(__file__).resolve().parents[1] / "scripts" / "refactor.py"


def _lines(n: int, prefix: str = "x") -> str:
    return "".join(f"{prefix}{i} = {i}\n" for i in range(n))


@pytest.fixture
def repo(tmp_path):
    """起点のコミット（ブランチ `base`）だけがある作業ディレクトリ。"""
    work = tmp_path / "repo"
    work.mkdir()
    run_git("init", "-q", "-b", "main", cwd=work)
    run_git("config", "user.email", "t@e.st", cwd=work)
    run_git("config", "user.name", "test", cwd=work)
    (work / "src").mkdir()
    (work / "src" / "a.py").write_text(_lines(12, "a"))
    (work / "README.md").write_text("# readme\n")
    run_git("add", "-A", cwd=work)
    run_git("commit", "-qm", "init", cwd=work)
    run_git("branch", "base", cwd=work)
    return work


def _commit(work: pathlib.Path, files: dict[str, str]) -> None:
    for rel, body in files.items():
        path = work / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body)
    run_git("add", "-A", cwd=work)
    run_git("commit", "-qm", "change", cwd=work)


def _assess(work: pathlib.Path, *extra: str) -> tuple[int, list[str]]:
    r = subprocess.run([sys.executable, str(_SCRIPT), "assess", *extra],
                       cwd=work, capture_output=True, text=True)
    return r.returncode, r.stdout.splitlines()


@pytest.mark.parametrize("files", [
    {"docs/guide.md": "# guide\n" + _lines(30)},
    {"tests/test_a.py": _lines(30, "t")},
    {"config/settings.json": "{\n" + "".join(f'"k{i}": {i},\n' for i in range(30)) + "}\n"},
], ids=["md-only", "tests-only", "json-only"])
def test_no_production_code_may_be_skipped(repo, files):
    _commit(repo, files)
    rc, out = _assess(repo, "--base", "base")
    assert rc == 3
    assert out == [
        "判定: 飛ばしてよい",
        "理由: 本番コードの差分がありません",
        "本番コード: 0 ファイル・0 行",
    ]


def test_ten_lines_of_production_code_may_be_skipped(repo):
    _commit(repo, {"src/b.py": _lines(10, "b")})
    rc, out = _assess(repo, "--base", "base")
    assert rc == 3
    assert out == [
        "判定: 飛ばしてよい",
        "理由: 本番コードの変更が 10 行で、上限 10 行以下です",
        "本番コード: 1 ファイル・10 行（src/b.py）",
    ]


def test_eleven_lines_of_production_code_pass(repo):
    _commit(repo, {"src/b.py": _lines(11, "b"), "README.md": _lines(50)})
    rc, out = _assess(repo, "--base", "base")
    assert rc == 0
    assert out == [
        "判定: 通す",
        "理由: 本番コードの変更が 11 行です",
        "本番コード: 1 ファイル・11 行（src/b.py）",
    ]


def test_rename_counts_both_paths(repo):
    """rename は旧パスの削除と新パスの追加として、両方のパスで判定する。"""
    run_git("mv", "src/a.py", "src/b.py", cwd=repo)
    _commit(repo, {"src/b.py": _lines(12, "a") + "extra = 1\n"})
    rc, out = _assess(repo, "--base", "base")
    assert rc == 0
    assert out == [
        "判定: 通す",
        "理由: 本番コードの変更が 25 行です",
        "本番コード: 2 ファイル・25 行（src/a.py、src/b.py）",
    ]


def test_max_lines_changes_the_limit(repo):
    _commit(repo, {"src/b.py": _lines(11, "b")})
    rc, out = _assess(repo, "--base", "base", "--max-lines", "20")
    assert rc == 3
    assert out[1] == "理由: 本番コードの変更が 11 行で、上限 20 行以下です"


def test_unresolvable_base_exits_2(repo):
    rc, out = _assess(repo, "--base", "no-such-ref")
    assert rc == 2
    assert out == []


def test_binary_production_file_counts_as_zero_lines(repo):
    """numstat が `-` を返すコード拡張子のバイナリも、現状どおり 0 行と数える。"""
    path = repo / "src" / "binary.py"
    path.write_bytes(b"before\0after")
    run_git("add", "-A", cwd=repo)
    run_git("commit", "-qm", "change", cwd=repo)

    rc, out = _assess(repo, "--base", "base")

    assert rc == 3
    assert out == [
        "判定: 飛ばしてよい",
        "理由: 本番コードの変更が 0 行で、上限 10 行以下です",
        "本番コード: 1 ファイル・0 行（src/binary.py）",
    ]
