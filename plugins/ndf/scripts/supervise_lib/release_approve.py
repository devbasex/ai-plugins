"""リリース承認ゲートの承認の記録（approve）と助言の MVV 判定（mvv・mvv-note）のステップ（`release_templates` から分けた）。"""

from __future__ import annotations

import shlex
from pathlib import Path

from supervise_lib.paths import MVV_PY, STEPS_PY
from supervise_lib.release_cd import MVV_NOTE, material_path


def approve_step(a, repo: str | None, approval: str, facts_from: str | None = None, by: str = "user", next_id: str = "bump") -> dict:
    """本番のプラン（MVV 判定なし）の先頭: リリース承認ゲートの承認の記録を書く（#1426 の決定 7）。提示した SHA は開発版の
    facts の出力（--dev-state か、同じディレクトリの開発版のプランの状態）から読み、利用者の承認の記録は --state から読む。
    証跡が無ければ承認ゲート（10）で終え、`next_id` へ進まない。trunk の本番（#3703）は自分のプランの facts の出力
    （`facts_from` に `{state_dir}`）を読み、担い手 `by` で書いて release へ進む。"""
    dev_state = getattr(a, "dev_state", None)
    facts = facts_from or (shlex.quote(str(Path(dev_state).resolve())) if dev_state else "'{queue_state?:release}'")
    state = getattr(a, "state", None)
    answer = f" --answer-from {shlex.quote(str(Path(state).resolve()))}" if state else ""
    return {
        "id": "approve",
        "type": "run",
        "stage": "デプロイ",
        "timeout": 120,
        **({"cwd": repo} if repo else {}),
        "cmd": f"{STEPS_PY} approve --approval {approval} --facts-from {facts}{answer} --by {by}",
        "next": next_id,
        "gate_next": "end",
    }


def advise_steps(state: str, gate: str, args: str, note: dict, head: dict | None = None) -> list[dict]:
    """助言の MVV 判定（#1400）の mvv・mvv-note のステップ。判定によらず、想定外の失敗でも mvv-note へ進む。
    `head` は両ステップの type の後（stage）、`note` は mvv-note の timeout から後（載せ先と後続）である。"""
    return [
        {
            "id": "mvv",
            "type": "run",
            **(head or {}),
            "timeout": 900,
            # 前の実行の判定の記録を載せないよう先に消す（MVV なしのときは記録を書かない）
            "cmd": f"rm -f {MVV_NOTE} && {MVV_PY} check --sprint {state} --gate {gate} {args} --note {MVV_NOTE} --advise",
            "on_fail": "mvv-note",
            "next": "mvv-note",
        },
        {"id": "mvv-note", "type": "run", **(head or {}), **note},
    ]


def advise_release_steps(a, repo: str | None, approval: str, prs: str) -> list[dict]:
    """開発版の explain の後の助言の MVV 判定（#1400）。承認資料と出す版の PR を材料にし、判定の記録を承認資料の末尾へ足す。
    判定によらず、想定外の失敗でも mvv-note へ進み、プランの結果は facts の承認ゲートのままである。"""
    material = material_path(repo, approval)
    state = shlex.quote(str(Path(a.advise).resolve()))
    args = f"--material {shlex.quote(material)} --pr {prs} --mode {a.mode}" + (f" --root {shlex.quote(repo)}" if repo else "")
    note = {
        "timeout": 120,
        **({"cwd": repo} if repo else {}),
        "cmd": f"sh -c '[ ! -f {MVV_NOTE} ] || {{ printf \"\\n\"; cat {MVV_NOTE}; }} >> {approval}'",
        "next": "end",
    }
    return advise_steps(state, "release", args, note)
