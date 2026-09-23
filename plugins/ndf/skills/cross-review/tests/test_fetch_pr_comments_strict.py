"""`fix/scripts/fetch-pr-comments.sh` の `--strict`（#542 の AC12a）。

控えの取り直し（`start-round`）は、3 つの取得元のどれか 1 つでも失敗すれば前の控えを残す。
一部だけの控えで上書きすると、前のラウンドの指摘が重複の検出から消えるためである。
`--strict` 無しの振る舞い（3 つとも失敗したときだけ 1）は `init` が使うため変えない。
"""
from __future__ import annotations

import pathlib
import subprocess

import pytest

SCRIPT = pathlib.Path(__file__).resolve().parents[2] / "fix" / "scripts" / "fetch-pr-comments.sh"
_RUN = subprocess.run


def _run(tmp_path, failing: set[str], *args: str) -> subprocess.CompletedProcess:
    """`gh` を偽物にして実行する。`failing` の API パスの末尾（comments / reviews）は失敗させる。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    gh = bin_dir / "gh"
    cases = "\n".join(f'  *"{f}"*) exit 1 ;;' for f in failing)
    gh.write_text("#!/usr/bin/env bash\ncase \"$2\" in\n" + cases + "\n  *) echo '[]' ;;\nesac\n",
                  encoding="utf-8")
    gh.chmod(0o755)
    import os
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    return _RUN(["bash", str(SCRIPT), *args, "acme/demo", "5"],
                capture_output=True, text=True, env=env)


@pytest.mark.parametrize("strict, rc", [(False, 0), (True, 1)])
def test_one_failed_source(tmp_path, strict, rc):
    r = _run(tmp_path, {"pulls/5/reviews"}, *(["--strict"] if strict else []))
    assert r.returncode == rc, r.stderr


def test_all_sources_fail_without_strict(tmp_path):
    r = _run(tmp_path, {"pulls/5/comments", "pulls/5/reviews", "issues/5/comments"})
    assert r.returncode == 1


def test_strict_succeeds_when_every_source_answers(tmp_path):
    assert _run(tmp_path, set(), "--strict").returncode == 0
