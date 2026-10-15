"""supervise の記録（`progress.jsonl`）から実績の記録を作る取り込み（#1856 の移行）。

材料はプランの状態ディレクトリごとの `progress.jsonl` と、プランの JSON の `課題`・`フェーズ` である。
既存の記録に終わり方が無いため `run_end` は作らず、`run_start` の `imported` を真にする（中断に数えない）。
"""

from __future__ import annotations

import json
from pathlib import Path

from estimate_records import DeclError, actual_run_id, append_actual, estimate_dir, read_actuals


def plan_beside_state(state: Path) -> tuple[str, dict]:
    """状態ディレクトリの隣のプラン（`<名前>-state/` なら `<名前>.json`）か、中の写し（plan.json）を読む。

    supervise が一時プランの状態を置き場所へ移した実体（`<stem>-<hash>/`）は `-state` で終わらず、写しだけを持つ。"""
    beside = [state.with_name(state.name[: -len("-state")] + ".json")] if state.name.endswith("-state") else []
    for plan in (*beside, state / "plan.json"):
        try:
            data = json.loads(plan.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict):
            return str(plan), data
    return "", {}


def _same_records(data: dict, directory: Path | str) -> bool:
    """プランの実行を supervise が書く先（`actuals.py` と同じ `リポジトリ` → `作業場所`）が `directory` か。

    どちらも無いプランは持ち主のリポジトリを確かめられないため偽にする。"""
    root = data.get("リポジトリ") or data.get("作業場所")
    if not root:
        return False
    try:
        return estimate_dir(str(root)).resolve() == Path(directory).resolve()
    except (OSError, DeclError):
        return False


def import_supervise_progress(sv_dir: Path | str, directory: Path | str, only_own: bool = False) -> dict:
    """supervise の状態ディレクトリの下の `progress.jsonl` から `run_start`・`step` の行を作る。

    `run_end` は作らず、`run_start` の `imported` を真にする。`run` の識別子で重複を除く（何度流しても同じ）。
    `only_own` が真なら、実行の記録の書き先が `directory` になるプランだけを取り込み、他は `foreign` に数える
    （全プロジェクト共通の置き場所から、別のリポジトリの実績を混ぜない）。"""
    existing = {r.get("run") for r in read_actuals(directory) if r.get("kind") == "run_start"}
    added = skipped = foreign = 0
    for progress in sorted(Path(sv_dir).rglob("progress.jsonl")):
        state = progress.parent
        plan, data = plan_beside_state(state)
        if not plan:
            continue
        if only_own and not _same_records(data, directory):
            foreign += 1
            continue
        try:
            lines = progress.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        issues = [int(i) for i in data.get("課題") or [] if isinstance(i, int) or str(i).isdigit()]
        phase = data.get("フェーズ") or None
        attempt, cur, buf = 0, None, []
        batches: list[tuple[dict, list[dict]]] = []
        for raw in lines:
            try:
                d = json.loads(raw)
            except ValueError:
                continue
            if not isinstance(d, dict):
                continue
            if d.get("kind") == "run" and d.get("at"):
                if cur:
                    batches.append((cur, buf))
                attempt += 1
                rid = actual_run_id(plan, d["at"])
                cur = {
                    "kind": "run_start",
                    "at": d["at"],
                    "run": rid,
                    "plan": plan,
                    "issues": issues,
                    "phase": phase,
                    "from": d.get("from"),
                    "attempt": attempt,
                    "imported": True,
                }
                buf = []
            elif d.get("kind") == "step" and cur and d.get("step"):
                buf.append(
                    {
                        "kind": "step",
                        "at": d.get("at"),
                        "run": cur["run"],
                        "step": str(d["step"]),
                        "type": str(d.get("type") or ""),
                        "exit": d.get("exit") if isinstance(d.get("exit"), int) else None,
                        "seconds": d.get("seconds") if isinstance(d.get("seconds"), (int, float)) else None,
                        "cost_usd": 0.0
                        if d.get("type") in ("run", "pr")
                        else (d.get("cost") if isinstance(d.get("cost"), (int, float)) else None),
                        "retry": 0,
                    }
                )
        if cur:
            batches.append((cur, buf))
        for start, steps in batches:
            if start["run"] in existing:
                skipped += 1
                continue
            for row in (start, *steps):
                err = append_actual(directory, row)
                if err:
                    raise OSError(err)
            existing.add(start["run"])
            added += 1
    return {"added": added, "skipped": skipped, "foreign": foreign}
