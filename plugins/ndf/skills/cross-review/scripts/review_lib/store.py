"""レビューの状態ファイルと一時ディレクトリ（#1142 の C2）。

`cross-review-pr<N>-state.json` を読み書きするのはこのモジュールだけである（I12）。書くときは
`_write_state` を通し、保存のたびに実行の要約（`run_metrics`）を書き直す。
"""
from __future__ import annotations

import json
import os
import pathlib
import subprocess
import sys
from typing import Any

import review_lib  # noqa: E402
import jsonio  # noqa: E402
import run_metrics  # noqa: E402


def _git_toplevel() -> str | None:
    """git worktree root を取得する。失敗時は None を返す。"""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except OSError:
        pass
    return None


def _tmp_dir(workspace: str | None = None) -> pathlib.Path:
    """cross-review 用 tmp ディレクトリを決定する。

    優先順位:
      1. 環境変数 `CROSS_REVIEW_TMP_DIR` (明示)
      2. `<workspace>/.cross_review/` (worktree 内。作業領域を 1 つに保つため)

    `workspace` 未指定なら `git rev-parse --show-toplevel` で worktree root を
    取得する。サブディレクトリから実行してもパス不一致が発生しない。
    git コマンドが失敗した場合のみ `os.getcwd()` にフォールバックする。
    """
    env = os.environ.get("CROSS_REVIEW_TMP_DIR")
    if env:
        d = pathlib.Path(env).resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d
    ws = pathlib.Path(workspace or _git_toplevel() or os.getcwd()).resolve()
    d = ws / ".cross_review"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_tmp_dir(pr: int | None = None) -> pathlib.Path:
    """state.json に保存された tmp_dir を優先し、_tmp_dir() をフォールバックとする。

    init 時に確定した tmp_dir を再利用することで、CWD や環境変数の変化による
    パス不一致リスクを回避する。

    注意: この関数は成果物パスの解決にのみ使用する。state ファイル自体のパス解決
    には _tmp_dir() を直接使うこと（循環参照を防ぐため）。
    副作用: フォールバック時に _tmp_dir() を呼ぶため、ディレクトリ作成 (mkdir) が
    発生する可能性がある。
    """
    if pr is not None:
        # state.json から tmp_dir を読み出す (存在する場合)
        candidate = _tmp_dir() / f"cross-review-pr{pr}-state.json"
        if candidate.exists():
            try:
                st = json.loads(candidate.read_text(encoding="utf-8"))
                saved = st.get("tmp_dir")
                if saved:
                    p = pathlib.Path(saved)
                    if p.exists():
                        return p
            except (OSError, json.JSONDecodeError):
                pass
    return _tmp_dir()


def _state_path(pr: int) -> pathlib.Path:
    return _tmp_dir() / f"cross-review-pr{pr}-state.json"


def _payload_path(agent: str, pr: int, round_: int) -> pathlib.Path:
    return _resolve_tmp_dir(pr) / f"{agent}-review-pr{pr}-round{round_}-payload.json"


def _existing_comments_path(pr: int) -> pathlib.Path:
    return _resolve_tmp_dir(pr) / f"cross-review-pr{pr}-existing-comments.txt"


def _load(pr: int) -> dict[str, Any]:
    p = _state_path(pr)
    if not p.exists():
        review_lib.die(f"state.json not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _save(pr: int, state: dict[str, Any]) -> None:
    _write_state(_state_path(pr), state)


def _write_state(p: pathlib.Path, state: dict[str, Any]) -> None:
    """状態ファイルを書く唯一の経路。`init` と再開の入口もここを通す（AC8）。"""
    jsonio.write_atomic(p, state)
    # **保存のたびに実行の要約を書き直す**（#662 の決定 5）。失敗しても進行は止めない。
    run_metrics.after_save(p, state, "cross-review", _summary_extra)


def _summary_extra(path: pathlib.Path, state: dict[str, Any],
                   launches: list[dict[str, Any]]) -> dict[str, Any]:
    """cross-review の要約だけが持つ鍵。`measure.py` の出力をそのまま置く（決定 8）。"""
    scripts = str(pathlib.Path(__file__).resolve().parents[1])
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import measure
    return {"measure": measure.measure(state)}


def _find_resumable_state(
    pr: object, worktree: str,
) -> tuple[dict[str, Any], pathlib.Path] | None:
    """既存 state を探し、再開できるものだけを (state, path) で返す。

    再開に該当しなければ `None`。**探索の入口は `_tmp_dir()` を使わない**（mkdir の
    副作用でパスが作られてしまう）。`CROSS_REVIEW_TMP_DIR` があればそれを、無ければ
    `<worktree>/.cross_review/` を直接組む。`final` が確定した state は再開しない。
    """
    env_tmp = os.environ.get("CROSS_REVIEW_TMP_DIR")
    if env_tmp:
        resume_dir = pathlib.Path(env_tmp).resolve()
    else:
        resume_dir = pathlib.Path(worktree) / ".cross_review"
    resume_state_file = resume_dir / f"cross-review-pr{pr}-state.json"
    if not resume_state_file.exists():
        return None
    st = json.loads(resume_state_file.read_text(encoding="utf-8"))
    if st.get("final") is not None:
        return None
    return st, resume_state_file


def _critique_path(agent: str, pr: int, round_: int) -> pathlib.Path:
    return _resolve_tmp_dir(pr) / f"{agent}-critique-pr{pr}-round{round_}.json"
