"""`fix/scripts/fetch-pr-comments.sh` の `--strict`（#542 の AC12a）。

スナップショットの取り直し（`start-round`）は、3 つの取得元のどれか 1 つでも失敗すれば前のスナップショットを残す。
一部だけのスナップショットで上書きすると、前のラウンドの指摘が重複の検出から消えるためである。
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


@pytest.mark.parametrize("args", [
    (),
    ("acme/demo",),
    ("--strict", "acme/demo"),
    ("", "5"),
])
def test_missing_arguments_exit_before_calling_gh(args):
    """現状固定: 必須引数が欠けた場合は gh を探す前に Usage を出して終了する。"""
    import os

    result = _RUN(
        ["/bin/bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        env=dict(os.environ, PATH=""),
    )
    assert result.returncode == 1
    assert "Usage:" in result.stderr


# --- stdout のタグ付き行の形（現状固定 / #542） ---------------------------------
# 既存のテストは偽の gh が `[]` を返すため終了コードしか見ておらず、出力の形を 1 度も
# 通していない。ここでは API パスごとに固定の JSON を返し、現状の出力そのものを期待値に
# する（jq は本物を使う）。cross-review の既存コメントのスナップショットはこの形をそのまま読む。
#
# 固定する分岐:
# - インライン: `line` あり / `line` が null で `original_line` だけ / path・line とも無い
# - 本文に改行と ``` を含む（`\n` エスケープと `` ` ` ` `` への置換）
# - レビュー: body が空（出ない）/ body あり
# - PR レベルコメント: 1 件

_INLINE_JSON = """[
  {"path":"src/a.py","line":10,"original_line":8,"user":{"login":"alice"},"body":"first\\nsecond ```code```"},
  {"path":"src/b.py","line":null,"original_line":22,"user":{"login":"bob"},"body":"only original_line"},
  {"path":null,"line":null,"original_line":null,"user":{"login":"carol"},"body":"no path no line"}
]"""

_REVIEWS_JSON = """[
  {"state":"COMMENTED","body":"","user":{"login":"dave"}},
  {"state":"CHANGES_REQUESTED","body":"please fix\\nthis ```block```","user":{"login":"erin"}}
]"""

_ISSUES_JSON = """[
  {"user":{"login":"frank"},"body":"pr level\\ncomment"}
]"""


def _run_with_payloads(tmp_path) -> subprocess.CompletedProcess:
    """本物の jq を使いつつ、gh だけを偽物にして API パスごとに固定の JSON を返す。"""
    import os

    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    payload_dir = tmp_path / "payload"
    payload_dir.mkdir(exist_ok=True)
    (payload_dir / "inline.json").write_text(_INLINE_JSON, encoding="utf-8")
    (payload_dir / "reviews.json").write_text(_REVIEWS_JSON, encoding="utf-8")
    (payload_dir / "issues.json").write_text(_ISSUES_JSON, encoding="utf-8")

    gh = bin_dir / "gh"
    gh.write_text(
        "#!/usr/bin/env bash\n"
        "case \"$2\" in\n"
        f'  *"pulls/5/comments"*) cat "{payload_dir}/inline.json" ;;\n'
        f'  *"pulls/5/reviews"*) cat "{payload_dir}/reviews.json" ;;\n'
        f'  *"issues/5/comments"*) cat "{payload_dir}/issues.json" ;;\n'
        "  *) echo '[]' ;;\n"
        "esac\n",
        encoding="utf-8",
    )
    gh.chmod(0o755)
    env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}")
    return _RUN(
        ["bash", str(SCRIPT), "acme/demo", "5"],
        capture_output=True,
        text=True,
        env=env,
    )


def test_stdout_tagged_lines_current_output(tmp_path):
    r = _run_with_payloads(tmp_path)
    assert r.returncode == 0, r.stderr
    expected = (
        "src/a.py:10 [alice] first\\nsecond ` ` `code` ` `\n"
        "src/b.py:22 [bob] only original_line\n"
        "?:? [carol] no path no line\n"
        "[REVIEW-BODY] [erin] state=CHANGES_REQUESTED please fix\\nthis ` ` `block` ` `\n"
        "[PR-COMMENT] [frank] pr level\\ncomment\n"
    )
    assert r.stdout == expected


def test_no_warning_on_success(tmp_path):
    r = _run_with_payloads(tmp_path)
    assert "WARNING" not in r.stderr, r.stderr
