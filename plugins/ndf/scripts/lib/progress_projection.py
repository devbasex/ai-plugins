"""進捗記録の生成（#3092 の F7・決定 14・受け入れ条件 6）。

課題の `## 進行`・ボード・スプリント PR の「通した工程」を、Skill の呼び出しではなく進行の事象から作る。
工程の開始でボードと課題の `## 進行` を、終了でスプリント PR の「通した工程」の行を書く。書けない先は結果に
`failed` で返し、工程は止めない（`progress_activities.act_record`）。
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path
from typing import Optional

import progress_model as pm

HERE = Path(__file__).resolve().parent
SYNC = HERE.parent / "projects-sync.sh"
PR_STEPS = HERE.parent / "pr-steps.py"
PR_STAGE = "Pull Request"


def finished_stage_names(snap, rows: dict) -> list[str]:
    """終了の印がある工程（工程の並びの順）。無効の行は入れない。"""
    return [s.name for s in snap.stages if (r := rows.get(s.name)) is not None and r.state == pm.FINISHED]


def render_passed(snap, rows: dict) -> str:
    """「通した工程」の行（`pr_mode.mode_line` の形）と、工程ごとのチェックの一覧を事象から作る。"""
    import pr_mode

    lines = [pr_mode.mode_line(snap.mode, finished_stage_names(snap, rows)), ""]
    for s in snap.stages:
        r = rows.get(s.name)
        if r is not None and r.state == pm.FINISHED:
            lines.append(f"- [x] {s.name} — {r.at}")
        elif r is not None and r.state == pm.DISABLED:
            lines.append(f"- [ ] {s.name}（無効: {r.reason or '理由なし'}）")
        else:
            lines.append(f"- [ ] {s.name}")
    return "\n".join(lines)


def _run_quiet(cmd: list[str], cwd: Optional[Path] = None) -> str:
    p = subprocess.run(cmd, capture_output=True, text=True, cwd=cwd)
    return "ok" if p.returncode == 0 else "failed"


def _sprint_pr(rows: dict) -> Optional[int]:
    r = rows.get(PR_STAGE)
    pr = ((r.payload or {}).get("metrics") or {}).get("pr") if r else None
    return int(pr) if isinstance(pr, (int, str)) and str(pr).isdigit() else None


def _update_pr(pr: int, line: str, cwd: Optional[Path]) -> str:
    import pr_mode

    p = subprocess.run(["gh", "pr", "view", str(pr), "--json", "body"], capture_output=True, text=True, cwd=cwd)
    if p.returncode != 0:
        return "failed"
    body = json.loads(p.stdout).get("body") or ""
    kept = [ln for ln in body.splitlines() if not (ln.startswith(pr_mode.MODE_LINE_PREFIX) and " / 通した工程: " in ln)]
    with tempfile.NamedTemporaryFile("w", suffix=".md", delete=False) as f:
        f.write("\n".join(kept + [line]) + "\n")
    return _run_quiet(["python3", str(PR_STEPS), "update", "--body-file", f.name, "--pr", str(pr)], cwd)


def project_stage(store, table_id: str, row: str, phase: str, cwd: Optional[Path] = None) -> dict:
    """工程の開始でボードと課題の `## 進行` を、終了でスプリント PR の「通した工程」を書く。書いた先ごとの結果を返す。"""
    import progress_db as pdb
    import workflow_def as wd

    with pdb.transaction(store) as conn:
        events = pdb.read_progress_events(conn, table_id)
        rev, raw = pdb.definition(conn, table_id)
    snap = wd.Snapshot.from_dict(raw["snapshot"])
    rows = pm.fold(events, rev)
    stage = row.split("#", 1)[0].split("/", 1)[0]
    out: dict[str, str] = {}
    if phase == "start":
        for n in raw.get("issues") or []:
            out[f"issue#{n}"] = _run_quiet(["bash", str(SYNC), str(n), "stage", stage, "--mode", snap.mode], cwd)
        return out
    pr = _sprint_pr(rows)
    if pr is None:
        out["pr"] = "skipped"
        return out
    import pr_mode

    out["pr"] = _update_pr(pr, pr_mode.mode_line(snap.mode, finished_stage_names(snap, rows)), cwd)
    return out
