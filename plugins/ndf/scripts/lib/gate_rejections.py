"""gate_rejections.py: 承認ゲートの差し戻しの記録（承認の行も書く。#1833・#2183）。標準ライブラリと lib/ だけで書く。

差し戻しの記録は `<NDF_MVV_STATE_DIR>/gate-rejections.jsonl`（覆し・MVV 判定の記録と同じ置き場）に、
利用者の答え 1 回（差し戻しか承認）を 1 行で追記する。pace と状態ファイルの有無によらず、差し戻しの置き場はここだけである。
覆しの記録（`project_mvv_signals`）へは書かず、覆しの集計はこの記録を読まない。

1 行の形: `at`・`repo`・`gate`・`what`・`sprint`（状態ファイルの絶対パスか null）・`issues`・`prs`・`by`（常に user）・
`outcome`（`rejected` か `approved`。`outcome` の無い行は #2183 より前に書いた差し戻し）。
`sprint`・`issues`・`prs` のうち 1 つ以上は空でない。

範囲に答えの行が 1 つも無く、状態ファイルにも承認ゲートの記録が無ければ、差し戻しの件数は 0 ではなく「未記録」である。
"""

from __future__ import annotations

from pathlib import Path

import jsonio
import project_mvv as pm
import project_mvv_signals as pms
import repo as repo_lib

LEDGER_NAME = "gate-rejections.jsonl"
COUNT_KEYS = ("gate_1", "gate_2", "other", "total")
REJECTED, APPROVED = "rejected", "approved"
OUTCOMES = (REJECTED, APPROVED)
_GATE_COUNT_KEY = {"design": "gate_1", "release": "gate_2"}


def ledger_path() -> Path:
    return pm.mvv_state_base() / LEDGER_NAME


def ledger_repo(root, repo: str | None = None) -> str:
    """記録の `repo`。`repo` → `origin` の `owner/repo` → メインディレクトリの絶対パス → 根の絶対パス。

    worktree で書いた行とメインディレクトリで数える行が同じ値になるよう、根ではなくメインディレクトリを使う。
    """
    if repo:
        return repo
    found = repo_lib.owner_repo(root)
    if found:
        return found
    main = repo_lib.main_dir(root)
    return str(main if main is not None else Path(root).resolve())


def make_row(gate: str, what: str, *, repo: str, at: str, sprint=None, issues=(), prs=(), outcome: str = REJECTED) -> dict:
    """1 行を作る。承認ゲートの名前・要約が空か、範囲（sprint・issues・prs）が 1 つも無いか、答えが OUTCOMES に無ければ ValueError。"""
    if outcome not in OUTCOMES:
        raise ValueError(f"答えは {' / '.join(OUTCOMES)} のどれか: {outcome!r}")
    if not (gate or "").strip():
        raise ValueError("承認ゲートの名前が空")
    if not (what or "").strip():
        raise ValueError("何を差し戻したかの要約が空")
    issues, prs = sorted({int(n) for n in issues}), sorted({int(n) for n in prs})
    if not sprint and not issues and not prs:
        raise ValueError("範囲（スプリントの状態ファイル・課題・PR）が 1 つも無い")
    return {
        "at": at,
        "repo": repo,
        "gate": gate,
        "what": what,
        "sprint": str(Path(sprint).resolve()) if sprint else None,
        "issues": issues,
        "prs": prs,
        "by": "user",
        "outcome": outcome,
    }


def append(row: dict, path=None) -> Path:
    """1 行を追記して記録のパスを返す。書けなければ OSError。"""
    target = Path(path) if path else ledger_path()
    jsonio.append_jsonl(target, row)
    return target


def write_sprint_answer(a, manifest: dict, outcome: str, *, repo: str, at: str) -> str | None:
    """`sprint-state.py gate` の答え（`a` はその引数）を 1 行書く。範囲は状態ファイル・その課題・`--pr`。
    書けなければ理由（置き場と OSError）を返す。"""
    issues, prs = manifest.get("issues") or [], [a.pr] if a.pr else []
    try:
        append(make_row(a.name, a.what, repo=repo, at=at, sprint=a.sprint, issues=issues, prs=prs, outcome=outcome))
    except OSError as e:
        return f"{ledger_path()}: {e}"
    return None


def read_rejections(path=None) -> list[dict]:
    """記録を読む。無ければ空。読めない・壊れた行があれば `jsonio.JsonReadError`（件数を「不明」にする）。"""
    return jsonio.read_jsonl(Path(path) if path else ledger_path(), missing=[])


def in_scope(row: dict, *, repo: str, sprint=None, issues=(), prs=()) -> bool:
    """`repo` が同じで、渡した範囲のどれかに当たるか。範囲を 1 つも渡さなければ `repo` だけで決める。"""
    if row.get("repo") != repo:
        return False
    if not sprint and not issues and not prs:
        return True
    if sprint and row.get("sprint") == str(Path(sprint).resolve()):
        return True
    if set(issues) & set(row.get("issues") or []):
        return True
    return bool(set(prs) & set(row.get("prs") or []))


def is_rejection(row: dict) -> bool:
    """差し戻しの行か。`outcome` の無い行（#2183 より前の記録）は差し戻しとして読む。"""
    return row.get("outcome", REJECTED) == REJECTED


def state_answered(sprint) -> bool:
    """状態ファイルの `gates` に承認ゲート（`関門 1` / `関門 2`）の記録（利用者の承認か MVV 判定の通過）があるか。
    状態ファイルが無ければ False、読めなければ `jsonio.JsonReadError`。"""
    if not sprint:
        return False
    m = jsonio.read(sprint, missing={}, want=dict)
    return any(isinstance(g, dict) and g.get("name") in pms.GATE_KEYS for g in m.get("gates") or [])


def count_by_gate(rows: list[dict]) -> dict:
    """差し戻しを承認ゲート 1・2・ほかに分けた件数（承認の行は数えない）。名前は `sprint-state.py` の記録の名前
    （`関門 1` / `関門 2`）で仕分ける。"""
    rejected = [r for r in rows if is_rejection(r)]
    counts = dict.fromkeys(COUNT_KEYS, 0)
    for r in rejected:
        counts[_GATE_COUNT_KEY.get(pms.GATE_KEYS.get(r.get("gate")), "other")] += 1
    counts["total"] = len(rejected)
    return counts
