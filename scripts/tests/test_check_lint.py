"""`scripts/check-lint.sh` の範囲の受け取り（#464）。

範囲テスト（`check-lint.sh -- <パス>...`）は、渡したパスのうち git が追跡し作業ディレクトリに残る
ファイルだけを、引数なしと同じ規則で振り分けて検査する。検査は一時の git リポジトリで走らせる。
ツールの起動は差し替えた `uv` が受ける。記録だけの `uv` で振り分けを、実物のツールへ渡す `uv` で
違反の検出を見る。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
LINT = ROOT / "scripts" / "check-lint.sh"

# 記録だけの uv: 起動の引数を 1 行ずつ書き、違反なしで返す
RECORD_UV = """#!/usr/bin/env bash
printf '%s\\n' "$*" >> "$UV_LOG"
exit 0
"""

# 実物の uv へ渡す。--project だけを、このリポジトリ（lint の依存を持つ）へ向ける
REAL_UV = """#!/usr/bin/env bash
args=()
skip=0
for a in "$@"; do
  if [ "$skip" -eq 1 ]; then args+=("$REAL_ROOT"); skip=0; continue; fi
  [ "$a" = "--project" ] && skip=1
  args+=("$a")
done
exec "$REAL_UV_BIN" "${args[@]}"
"""

GOOD_PY = 'VALUE = "ok"\n'
FORMAT_BAD_PY = "VALUE = {'a':1}\n"
CHECK_BAD_PY = "import os\n"
GOOD_SH = '#!/usr/bin/env bash\necho "ok"\n'
SHELLCHECK_BAD_SH = "#!/usr/bin/env bash\nunused=1\n"


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=str(repo), check=True, capture_output=True)


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    files = {
        "pkg/good.py": GOOD_PY,
        "pkg/tool.sh": GOOD_SH,
        "bin/hook": GOOD_SH,
        "bin/notes": "plain text\n",
        "README.md": "# r\n",
        "conf.json": "{}\n",
    }
    for rel, body in files.items():
        path = repo / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")
    git(repo, "add", "-A")
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "commit", "-q", "-m", "init")
    return repo


def make_uv(tmp_path: Path, body: str) -> Path:
    bindir = tmp_path / "bin"
    bindir.mkdir(exist_ok=True)
    uv = bindir / "uv"
    uv.write_text(body, encoding="utf-8")
    uv.chmod(0o755)
    return bindir


def run_lint(repo: Path, bindir: Path, *args: str, **env: str) -> subprocess.CompletedProcess:
    full_env = {"PATH": f"{bindir}:/usr/bin:/bin", "HOME": str(repo.parent), **env}
    return subprocess.run(["bash", str(LINT), *args], cwd=str(repo), capture_output=True, text=True, env=full_env)


def recorded(tmp_path: Path, repo: Path, *args: str) -> tuple[subprocess.CompletedProcess, list[str]]:
    log = tmp_path / "uv.log"
    log.write_text("", encoding="utf-8")
    proc = run_lint(repo, make_uv(tmp_path, RECORD_UV), *args, UV_LOG=str(log))
    return proc, log.read_text(encoding="utf-8").splitlines()


def counts(proc: subprocess.CompletedProcess) -> str:
    return proc.stderr.strip().splitlines()[-1]


def test_all_tracked_paths_match_no_arguments(tmp_path: Path, repo: Path) -> None:
    """全ファイルを渡したときと引数なしで、対象の本数が同じ（I5・AC4）。"""
    whole, _ = recorded(tmp_path, repo)
    tracked = subprocess.run(["git", "ls-files"], cwd=str(repo), capture_output=True, text=True).stdout.split()
    scoped, _ = recorded(tmp_path, repo, "--", *tracked)
    assert whole.returncode == scoped.returncode == 0
    assert counts(whole) == counts(scoped) == "check-lint: 違反なし（Python 1 本・sh 2 本）"


def test_out_of_scope_files_start_no_tool(tmp_path: Path, repo: Path) -> None:
    """対象外・追跡していない・消えたファイルだけなら、ツールを起動せずに 0（I6・AC3）。"""
    (repo / "pkg" / "untracked.py").write_text(FORMAT_BAD_PY, encoding="utf-8")
    (repo / "pkg" / "good.py").unlink()
    proc, calls = recorded(tmp_path, repo, "--", "README.md", "conf.json", "bin/notes", "pkg/untracked.py", "pkg/good.py", "gone.py")
    assert proc.returncode == 0
    assert calls == []
    assert counts(proc) == "check-lint: 違反なし（Python 0 本・sh 0 本）"


def test_scoped_files_are_sorted_by_kind(tmp_path: Path, repo: Path) -> None:
    """渡したファイルは引数なしと同じ規則で振り分けられ、`.sh` を ruff へ渡さない。"""
    proc, calls = recorded(tmp_path, repo, "--", "pkg/tool.sh", "bin/hook", "README.md")
    assert proc.returncode == 0
    assert all("ruff" not in call for call in calls)
    shellcheck = [call for call in calls if "shellcheck" in call]
    assert len(shellcheck) == 1
    assert sorted(shellcheck[0].split()[-2:]) == ["bin/hook", "pkg/tool.sh"]


def test_directory_expands_to_tracked_files(tmp_path: Path, repo: Path) -> None:
    """ディレクトリを渡すと配下の追跡ファイルへ展開される。"""
    proc, _ = recorded(tmp_path, repo, "pkg")
    assert counts(proc) == "check-lint: 違反なし（Python 1 本・sh 1 本）"


def test_unknown_option_before_separator_is_rejected(tmp_path: Path, repo: Path) -> None:
    proc, calls = recorded(tmp_path, repo, "--bogus")
    assert proc.returncode == 2
    assert calls == []


def test_dash_path_after_separator_is_a_path(tmp_path: Path, repo: Path) -> None:
    """`--` の後ろは `-` で始まっても引数でなくパスとして読む。"""
    proc, calls = recorded(tmp_path, repo, "--", "--bogus")
    assert proc.returncode == 0
    assert calls == []


REAL_UV_BIN = shutil.which("uv")


@pytest.mark.skipif(REAL_UV_BIN is None, reason="uv が無い")
@pytest.mark.parametrize(
    ("rel", "body", "failed"),
    [
        ("pkg/good.py", FORMAT_BAD_PY, "ruff format --check"),
        ("pkg/good.py", CHECK_BAD_PY, "ruff check"),
        ("pkg/tool.sh", SHELLCHECK_BAD_SH, "shellcheck"),
        ("bin/hook", SHELLCHECK_BAD_SH, "shellcheck"),
    ],
)
def test_scoped_run_detects_each_violation(tmp_path: Path, repo: Path, rel: str, body: str, failed: str) -> None:
    """範囲テストは 3 検査それぞれの違反を終了コード 1 で返す（AC1）。"""
    (repo / rel).write_text(body, encoding="utf-8")
    bindir = make_uv(tmp_path, REAL_UV)
    proc = run_lint(repo, bindir, "--", rel, "README.md", REAL_ROOT=str(ROOT), REAL_UV_BIN=str(REAL_UV_BIN))
    assert proc.returncode == 1, proc.stderr
    assert counts(proc).startswith("check-lint: 違反あり:")
    assert failed in counts(proc)
