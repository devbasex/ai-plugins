"""plugins/ndf/scripts のテストに共通する差し替え。

`new sprint` は --design に無い課題の本文を `gh issue view <n> --json body` で読む（#1767）。
`issue_bodies` は PATH の先頭へ偽の `gh` を置き、その呼び出しだけを見本の本文で答える。ほかの呼び出しは
差し替える前の PATH の `gh` へ渡す（無ければ失敗する）。既定の本文は受け入れ条件を持つ。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from pathlib import Path

import pytest

ACCEPTED = "## 目的\n\n例\n\n## 受け入れ条件\n\n- [ ] 1. 動く\n"

_FAKE = """#!{py}
import json, os, sys
from pathlib import Path
here = Path(__file__).resolve().parent
a = sys.argv[1:]
if len(a) >= 5 and a[:2] == ["issue", "view"] and a[-2:] == ["--json", "body"]:
    log = here / "calls.txt"
    with log.open("a") as f:
        f.write(a[2] + "\\n")
    rows = json.loads((here / "bodies.json").read_text())
    row = rows.get(a[2], rows.get("*"))
    if row.get("rc", 0):
        sys.stderr.write(row.get("err", ""))
        sys.exit(row["rc"])
    print(json.dumps({{"body": row["body"]}}))
    sys.exit(0)
real = {real!r}
if not real:
    sys.stderr.write("gh が無い（テスト）\\n")
    sys.exit(1)
os.execv(real, [real, *a])
"""


class IssueBodies:
    """偽の `gh` が返す本文。番号ごとに本文か失敗を決め、呼ばれた番号を読める。"""

    def __init__(self, bindir: Path):
        self.bindir = bindir
        self.rows: dict[str, dict] = {"*": {"body": ACCEPTED}}
        self._save()

    def _save(self) -> None:
        (self.bindir / "bodies.json").write_text(json.dumps(self.rows, ensure_ascii=False))

    def body(self, n: int, text: str) -> IssueBodies:
        self.rows[str(n)] = {"body": text}
        self._save()
        return self

    def fail(self, n: int, rc: int = 1, err: str = "HTTP 404: Not Found\n") -> IssueBodies:
        self.rows[str(n)] = {"rc": rc, "err": err}
        self._save()
        return self

    def calls(self) -> list[int]:
        log = self.bindir / "calls.txt"
        return [int(x) for x in log.read_text().split()] if log.exists() else []


@pytest.fixture()
def issue_bodies(tmp_path, monkeypatch) -> IssueBodies:
    bindir = tmp_path / "issue-bodies-gh"
    bindir.mkdir()
    gh = bindir / "gh"
    gh.write_text(_FAKE.format(py=sys.executable, real=shutil.which("gh") or ""))
    gh.chmod(0o755)
    monkeypatch.setenv("PATH", f"{bindir}{os.pathsep}{os.environ.get('PATH', '')}")
    return IssueBodies(bindir)


@pytest.fixture(autouse=True)
def _isolated_issue_locks(tmp_path_factory, monkeypatch):
    """課題ごとの錠（`gh_parts.issue_lock_target`）を利用者の `~/.claude/ndf/locks` へ作らない。"""
    monkeypatch.setenv("NDF_LOCK_DIR", str(tmp_path_factory.mktemp("locks")))


@pytest.fixture(autouse=True)
def _isolated_mvv_state(tmp_path_factory, monkeypatch):
    """差し戻し・覆し・MVV 判定の記録（`NDF_MVV_STATE_DIR`）を利用者の `~/.local/state/ndf` へ書かない（#1833）。"""
    monkeypatch.setenv("NDF_MVV_STATE_DIR", str(tmp_path_factory.mktemp("mvv-state")))


@pytest.fixture(autouse=True)
def _isolated_estimate_records(tmp_path_factory, monkeypatch):
    """supervise の実行が書く見積もりの実績の記録（#1856）を、リポジトリの git の共通ディレクトリと利用者の状態へ書かない。"""
    monkeypatch.setenv("NDF_ESTIMATE_DIR", str(tmp_path_factory.mktemp("estimate")))


@pytest.fixture(autouse=True)
def _isolated_whole_runs(tmp_path_factory, monkeypatch):
    """全体テストの実測の記録（#1631）を、リポジトリの git の共通ディレクトリと利用者の状態へ書かない。"""
    monkeypatch.setenv("NDF_WHOLE_RUNS_DIR", str(tmp_path_factory.mktemp("whole-runs")))
