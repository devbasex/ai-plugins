"""project_mvv_signals.py: 改訂の兆候の記録と集計（#1366 の I13・I18）。標準ライブラリだけで書く。

覆し（`project-mvv-signals.jsonl`）は `sprint-state.py gate --by user` が、同じ承認ゲートの直前の MVV 判定と食い違うときだけ書く。
「判定できない」（`mvv-gate.jsonl`）と流出不具合（検査の記録の `kind: escape`）は既存の記録を読む。
"""

from __future__ import annotations

import datetime
import json
from pathlib import Path

import legacy_names
import project_mvv as pm


def read_jsonl(path) -> list[dict]:
    p = Path(path)
    if not p.is_file():
        return []
    rows = []
    for ln in p.read_text(encoding="utf-8").splitlines():
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if isinstance(d, dict):
            rows.append(d)
    return rows


def append_jsonl(path, row: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _parse_at(s) -> datetime.datetime | None:
    if not isinstance(s, str) or not s:
        return None
    try:
        d = datetime.datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=datetime.timezone.utc)


def _after(row: dict, since: datetime.datetime | None) -> bool:
    at = _parse_at(row.get("at"))
    return since is None or (at is not None and at > since)


def unknown_streak(gate_rows: list[dict], sha: str | None) -> int:
    """同じプロジェクト MVV の sha256 のもとで、末尾から続いた「判定できない」の回数（I13）。"""
    n = 0
    for r in reversed(gate_rows):
        ref = r.get("project_mvv") or {}
        if ref.get("sha256") != sha:
            break
        if r.get("verdict") in ("machine",):
            continue  # 機械の検査で戻したものは判定ではない
        if r.get("verdict") != "unknown":
            break
        n += 1
    return n


def _collect_signals(key, since, sha, approved, log) -> list[dict]:
    """signals ログのうち、同じリポジトリで承認の日時より後の行（承認済みなら現行の版の行だけ）。"""
    sig = [r for r in read_jsonl(log) if r.get("repo") == key and _after(r, since)]
    if approved:
        sig = [r for r in sig if r.get("project_sha256") == sha]
    return sig


def _collect_gate_rows(key, sha, since, approved, log) -> list[dict]:
    """gate ログのうち、現行の版の判定で承認の日時より後の行（承認済みなら同じリポジトリか repo の無い行だけ）。"""
    rows = [r for r in read_jsonl(log) if (r.get("project_mvv") or {}).get("sha256") == sha and _after(r, since)]
    if approved:
        rows = [r for r in rows if r.get("repo") in (None, key)]
    return rows


def _count_signals(sig: list[dict], gate_rows: list[dict], esc: list[dict], sha) -> dict:
    return {
        "overrides": len(sig),
        "override_reject": sum(1 for r in sig if r.get("kind") == "override_reject"),
        "override_pass": sum(1 for r in sig if r.get("kind") == "override_pass"),
        "unknowns": sum(1 for r in gate_rows if r.get("verdict") == "unknown"),
        "escapes": len(esc),
        "unknown_streak": unknown_streak(gate_rows, sha),
    }


def _over_thresholds(counts: dict, th: dict, st: dict) -> list[str]:
    over = [k for k in ("overrides", "unknowns", "escapes") if counts[k] >= th[k]]
    if counts["unknown_streak"] >= st["unknown_streak"]:
        over.append("unknown_streak")
    return over


def signals(root, mvv: pm.ProjectMvv, *, signals_log=None, gate_log=None, escapes: list[dict] | None = None) -> dict:
    """改訂の兆候の集計（I18）。現行の版の sha256 と承認の日時より後の記録だけを数え、閾値と比べる。"""
    st = pm.resolve_settings(mvv.settings)
    th = st["revise_after"]
    key = pm.mvv_repo_key(root)
    since = _parse_at(mvv.approved_at) if mvv.approved else None
    sha = mvv.sha256 if mvv.approved else None
    sig = _collect_signals(key, since, sha, mvv.approved, signals_log or pm.signals_log_path())
    gate_rows = _collect_gate_rows(key, sha, since, mvv.approved, gate_log or pm.gate_log_path())
    esc = [e for e in (escapes or []) if e.get("kind") == "escape" and _after(e, since)]
    counts = _count_signals(sig, gate_rows, esc, sha)
    over = _over_thresholds(counts, th, st)
    return {"counts": counts, "thresholds": {**th, "unknown_streak": st["unknown_streak"]}, "over": over, "version": mvv.version}


def revise_suggestion(sig: dict) -> dict | None:
    """閾値を超えていれば改訂の提案の 1 件（`items` に載せる）。"""
    if not sig["over"]:
        return None
    parts = [f"{k} {sig['counts'][k]} 件（閾値 {sig['thresholds'][k]}）" for k in sig["over"]]
    return {
        "kind": "revise",
        "result": "suggest",
        "name": "MVV の改訂を提案する",
        "reason": "現行の版のもとで " + "・".join(parts) + " に届いた。`references/project-mvv.md` の改訂の手順へ入る",
    }


GATE_KEYS = {"関門 1": "design", "関門 2": "release"}  # mvv-gate.py の --gate


def last_mvv_verdict(state: dict, sprint: str, gate: str, gate_log, pr: int | None = None) -> str | None:
    """同じ承認ゲートの直前の MVV 判定（状態の `by: mvv` の記録か、mvv-gate.jsonl の同じスプリントの最後の行の新しい方）。
    それより新しい取り消し（`withdrawals`）があれば None。`pr` を渡すと、mvv-gate.jsonl の行は `pr` にその番号を含むものだけを
    見る（1 つのスプリントの複数の設計 PR の判定を取り違えない。#1400 の I8）。"""
    found = []
    g = next((g for g in state.get("gates", []) if g.get("name") == gate and g.get("by") == "mvv"), None)
    if g:
        found.append((g.get("at") or "", g.get("verdict")))
    me = str(Path(sprint).resolve())
    rows = [
        r
        for r in read_jsonl(Path(gate_log).expanduser())
        if r.get("gate") == GATE_KEYS.get(gate)
        and str(Path(str(legacy_names.read_key(r, "sprint") or "")).resolve()) == me
        and (pr is None or pr in (r.get("pr") or []))
    ]
    if rows:
        found.append((rows[-1].get("at") or "", rows[-1].get("verdict")))
    # 自動の通過を取り消した（sprint-state.py gate --withdraw）なら、それより前の判定は直前の判定として読まない
    found += [(w.get("at") or "", "withdrawn") for w in state.get("withdrawals", []) if w.get("name") == gate]
    if not found:
        return None
    verdict = max(found, key=lambda x: (x[0], x[1] == "withdrawn"))[1]  # 同じ時刻なら取り消しを後とみなす
    return verdict if verdict in ("follow", "not_follow", "unknown") else None


def record_override(
    state: dict, sprint: str, gate: str, outcome: str | None, at: str, root, gate_log, pr: int | None = None
) -> dict | None:
    """利用者の答え（`outcome`。省くと approved）が直前の MVV 判定と食い違えば、覆しを 1 行書いて返す（I18）。"""
    verdict = last_mvv_verdict(state, sprint, gate, gate_log, pr)
    if outcome == "rejected" and verdict == "follow":
        kind = "override_reject"
    elif outcome != "rejected" and verdict in ("not_follow", "unknown"):
        kind = "override_pass"
    else:
        return None
    ref = state.get("project_mvv") or {}
    row = {
        "at": at,
        "repo": pm.mvv_repo_key(root),
        "sprint": str(Path(sprint).resolve()),
        "gate": gate,
        "kind": kind,
        "mvv_verdict": verdict,
        "project_sha256": ref.get("sha256") or pm.load_mvv(root).sha256 or "",
    }
    append_jsonl(pm.signals_log_path(), row)
    return row
