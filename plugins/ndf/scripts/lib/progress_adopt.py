"""実行中のスプリントと計画の取り込み（#3092 の F11・決定 12・19）。`progress.py adopt` だけが使う。

今の目録 `sprint.json` と `done-<番号>.json`、計画の `plan.json` と計画の台帳から、進行表へ書く事象の下書きを作る。
書くのは `progress.py` が 1 つのトランザクションで行い、失敗すれば何も残らない。元のファイルは読むだけで消さない。
"""

from __future__ import annotations

import json
from pathlib import Path

import progress_model as pm

# 今の目録のステージ → 工程表の行名（設計の runtime 文書の「移行」）
STAGE_MAP = {
    "設計": ["設計", "ドキュメント再構成", "ドキュメントレビュー"],
    "設計承認ゲート": ["ドキュメントレビュー"],
    "設計 PR のマージ": ["ドキュメントレビュー"],
    "設計の結果": ["ドキュメントレビュー"],
    "スプリントブランチ": ["作業場所の用意"],
    "実装": ["実装"],
    "検査": ["構造改善", "実装レビュー", "完了判定"],
    "実装レビュー": ["実装レビュー"],
    "最終の検査": ["実装レビュー"],
    "開発版": ["Pull Request", "後片付け", "デプロイ"],
    "デプロイ": ["Pull Request", "後片付け", "デプロイ"],
    "リリース承認ゲート": ["デプロイ"],
    "リリース": ["デプロイ"],
    "本番": ["デプロイ"],
    "まとめ": ["確定仕様化", "リリース後テスト", "振り返り", "組み換え", "棚卸し"],
}
DONE_RESULTS = ("完了", "done", "ok")


def _done_stages(manifest: dict, base: Path) -> set[str]:
    """`done-<番号>.json` が終わりを示すステージ。承認ゲートは、後ろのプランのステージが終わっていれば終わったとする。"""
    items: dict[str, dict] = {}
    for f in sorted(base.glob("done-*.json")):
        for it in (_read(f) or {}).get("items") or []:
            if it.get("plan"):
                items[str(it["plan"])] = it
    done, later = set(), False
    for st in reversed(manifest.get("ステージ") or []):
        plans = st.get("plans") or []
        if plans and all((items.get(str(p)) or {}).get("result") in DONE_RESULTS for p in plans):
            done.add(st["name"])
            later = True
        elif not plans and later:
            done.add(st["name"])
    return done


def _read(path: Path) -> dict:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as e:
        raise ValueError(f"{path} を読めない（{e}）") from None


def sprint_drafts(m: dict, path: Path, snap) -> tuple[list, list[int]]:
    """目録から、スプリントの進行表の最初の事象と課題の一覧を作る。対応の無いステージは ValueError（決定 19）。"""
    names = [st.get("name") for st in m.get("ステージ") or []]
    unknown = [n for n in names if n not in STAGE_MAP]
    if unknown:
        raise ValueError(f"対応の無いステージ: {', '.join(unknown)}")
    drafts = [pm.EventDraft("created", "adopt", None, 1, payload={"adopted_from": str(path)})]
    drafts += pm.adopt_rows(snap, _done_stages(m, path.parent), {k: v for k, v in STAGE_MAP.items() if k in names})
    issues = sorted({int(n) for st in m.get("ステージ") or [] for n in st.get("issues") or []})
    return drafts, issues


def plan_drafts(root: Path, name: str) -> tuple[list, dict[str, list]]:
    """計画の `plan.json` と台帳から、計画の進行表とレーンの進行表の最初の事象を作る。"""
    import parallel_ledger as pl
    import parallel_plan as pp

    plan = pp.load_valid_plan(root, name)
    cp, _ = pl.load_checkpoint(root, name)
    lane_state = ((cp or {}).get("state") or {}).get("lanes") or {}
    head = [pm.EventDraft("created", "adopt", None, payload={"max_lanes": len(plan.lanes), "adopted_from": name})]
    head += [
        pm.EventDraft("lane_added", "adopt", None, payload={"lane": ln.name, "touches": ln.touches, "after": ln.after}) for ln in plan.lanes
    ]
    head += [pm.EventDraft("plan_started", "adopt", None)]
    head += [
        pm.EventDraft("request_opened", "adopt", None, reason="取り込んだ答えていない問い", payload=q)
        for q in pl.pending_questions(root, name)
    ]
    lanes: dict[str, list] = {}
    for ln in plan.lanes:
        issues = list(getattr(ln.input, "issues", []) or [])
        row = {"row": 1, "issues": issues, "touches": ln.touches, "after": ln.after, "args": list(ln.input.args)}
        rows = [pm.EventDraft("created", "adopt", None), pm.EventDraft("created", "adopt", "1", payload=row)]
        st = lane_state.get(ln.name) or {}
        if st.get("stopped"):
            rows.append(pm.EventDraft("adopted", "adopt", "1", payload={"state": pm.COMPLETED}))
        elif st.get("started"):
            rows.append(pm.EventDraft("row_started", "adopt", "1", payload={"row": 1, "sprint": None}))
        lanes[ln.name] = rows
    return head, lanes
