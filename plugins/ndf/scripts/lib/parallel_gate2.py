"""リリース承認ゲートの依頼（`kind: gate2`。#3597）: 提示のプランが止まったときの依頼（`request_gate2`）と、確定の後に本番のプランが
承認ゲートで終わったときの依頼（`request_confirmed`）を組んで送る。確定の後の依頼への答え「打ち直す」「差し戻す」は、耐久ワークフロー
（`parallel_run._Run` が継ぐ `ConfirmedGate`）が当てる。

| 依頼 | id | 中身 |
| --- | --- | --- |
| 提示 | `<計画名>-p<連番>-<提示>` | `n`・`k`・`report`・`stamp`（提示したコミット） |
| 確定の後 | `<計画名>-p<連番>-<提示>-c<確定の起動の seq>` | 提示の欄に `after: confirmed`・`reason`・`pr`・`options`・`question` |

確定の起動の seq（イベント `pause` の `seq`）を id に入れるため、確定の後の依頼は提示の依頼とも、打ち直した後の依頼とも重ならない（決定 1）。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import durable
import parallel_external as pe
import parallel_lane as pl
import parallel_ledger as plg
import parallel_release as prl
from parallel_helper import is_confirmed_launch, last_launch
from step_read import last_json

CONFIRMED_OPTIONS = ("打ち直す", "差し戻す")  # 確定の後の依頼の選択肢。依頼の `options` と答えの当て方が読む（決定 2）


class NotConfirmed(Exception):
    """確定の後の依頼を送らない（最後の起動が同じ提示の確定でない・本番のプランが承認ゲートで止まっていない）。"""


def _presented_sha(main: Path, name: str, n: int, k: int) -> str:
    try:
        return json.loads((prl.release_dir(main, name, n, k) / "presented.json").read_text(encoding="utf-8")).get("sha") or ""
    except (OSError, ValueError):
        return ""


def request_gate2(main: Path, name: str, n: int, k: int, report: str) -> plg.Sent:
    """一時レーンが提示のプランのリリース承認ゲートで止まったときの依頼。stamp は提示したコミット（`presented.json`）。"""
    rid = f"{name}-p{n}-{k}"
    msg = {"kind": "gate2", "id": rid, "lane": prl.helper_name(name, n), "n": n, "k": k, "report": report,
           "stamp": _presented_sha(main, name, n, k), "at": pe.utc_stamp()}  # fmt: skip
    return pl.send_inbox(main, name, msg, rid)


def gate_detail(done: str) -> Optional[tuple[str, Optional[int], str]]:
    """本番のプランの done から (承認ゲートの理由, 本番の PR の番号, 承認資料のパス)。結果が `関門` の項目が無ければ None。
    done を読めなければ `OSError`・`ValueError`。理由と番号は、その項目の状態の `progress.jsonl` の終了コード 10 のステップの出力を
    末尾から読み、`metrics.reason`（無ければ最後のステップの `summary`）と `metrics.main_pr` を取る。読めなければ理由はパスを示す。"""
    got = json.loads(Path(done).read_text(encoding="utf-8"))
    item = next((i for i in got.get("items") or [] if isinstance(i, dict) and i.get("result") == "関門"), None)
    if item is None:
        return None
    report = str(item.get("report") or "")
    state = Path(report).parent
    reason, pr, summary = _gate_reason(state)
    return reason or summary or f"承認ゲートの理由を読めない（{state / 'progress.jsonl'}）", pr, report


def _gate_steps(state: Path) -> list[dict]:
    """状態 `state` の `progress.jsonl` のうち、終了コード 10 のステップ（`kind: step`・`exit: 10`）の行。読めなければ空。"""
    try:
        rows = [json.loads(x) for x in (state / "progress.jsonl").read_text(encoding="utf-8").splitlines() if x.strip().startswith("{")]
    except (OSError, ValueError):
        rows = []
    return [r for r in rows if isinstance(r, dict) and r.get("kind") == "step" and r.get("exit") == 10]


def _step_out(state: Path, row: dict) -> dict:
    """1 ステップの出力（`out` のファイルの最後の JSON）。読めなければ空。"""
    try:
        return last_json((state / str(row.get("out") or "")).read_text(encoding="utf-8")) or {}
    except (OSError, ValueError):
        return {}


def _gate_reason(state: Path) -> tuple[str, Optional[int], str]:
    """終了コード 10 のステップの出力を末尾から読み、(`metrics.reason`, `metrics.main_pr`, `summary`) をそれぞれ最初に見つけた値で畳む。"""
    reason, pr, summary = "", None, ""
    for row in reversed(_gate_steps(state)):
        out = _step_out(state, row)
        m = out.get("metrics") or {}
        summary = summary or str(out.get("summary") or "")
        reason = reason or str(m.get("reason") or "")
        pr = pr if pr is not None else (m.get("main_pr") if isinstance(m.get("main_pr"), int) else None)
        if reason and pr is not None:
            break
    return reason, pr, summary


def request_confirmed(main: Path, name: str, n: int, k: int, done: str) -> plg.Sent:
    """確定の後に本番のプランが承認ゲートで終わったときの依頼を組んで送る（決定 1・決定 2）。最後の起動が同じ連番・提示の確定
    （`carried` を持たない）でなければ、また done が承認ゲートで止まっていなければ `NotConfirmed`。"""
    launch = last_launch(main, name)
    if not is_confirmed_launch(launch, n, k):
        raise NotConfirmed("最後の起動が確定でない（送らない）")
    got = gate_detail(done)
    if got is None:
        raise NotConfirmed("本番のプランが承認ゲートで止まっていない（settle を打つ）")
    reason, pr, report = got
    rid = f"{name}-p{n}-{k}-c{launch.get('seq')}"
    where = f"本番の PR #{pr}" if pr else "本番の PR の番号は読めない"
    text = f"リリース {n} の承認の後に本番のプランが承認ゲートで止まった（{reason}。{where}）。打ち直すか差し戻すか"
    msg = {"kind": "gate2", "id": rid, "lane": prl.helper_name(name, n), "n": n, "k": k, "after": "confirmed", "reason": reason, "pr": pr,
           "options": list(CONFIRMED_OPTIONS), "question": text, "report": report, "stamp": _presented_sha(main, name, n, k),
           "at": pe.utc_stamp()}  # fmt: skip
    return pl.send_inbox(main, name, msg, rid)


@durable.step(name="parallel.close-release-pr")
def step_close_release_pr(main: str, pr: int, comment: str) -> dict:
    """差し戻した本番の PR を閉じ、理由と依頼の id をコメントに残す。失敗は例外にせず `closed: false` と標準エラーの 1 行目で返す。"""
    from gh_call import gh

    got = gh(["pr", "close", str(int(pr)), "--comment", comment], cwd=main)
    first = next((x.strip() for x in (got.stderr or "").splitlines() if x.strip()), "") or f"exit {got.returncode}"
    return {"closed": got.returncode == 0, "reason": None if got.returncode == 0 else first}


class ConfirmedGate:
    """`_Run` が継ぐ。`_Run` は `main`・`current`・`launch`・`helper_asked`・`pause`・`launch_helper`・`settle` を持つ。"""

    def apply_confirmed(self, q: dict, choice: list[str]) -> bool:
        """確定の後の依頼への答えを当てる。確定の後の依頼でなければ False（ほかの当て方へ進む）。流れているリリースと連番が
        違う・選択肢が「打ち直す」「差し戻す」のどちらでもない・最後の起動の記録が無いときは何もしない（True を返す）。

        - 打ち直す: 同じ承認のまま、答えた問いの id を添えた新しい目的で確定の起動を流し直す（新しい seq）
        - 差し戻す: 本番の PR を閉じてから、`parallel.py settle --rejected` と同じ締め（`settle` の `rejected`）を通す"""
        if q.get("kind") != "gate2" or q.get("after") != "confirmed":
            return False
        rel = self.current
        if rel is None or q.get("n") != rel["n"]:
            return True
        if CONFIRMED_OPTIONS[0] in choice and self.launch and self.launch.get("purpose"):
            fields = {k: v for k, v in self.launch.items() if k not in ("seq", "action")}
            self.pause("confirmed", **{**fields, "purpose": f"{fields['purpose']}-a{q.get('id')}"})
            self.helper_asked = None
            self.launch_helper()
        elif CONFIRMED_OPTIONS[1] in choice:
            reason = f"承認の後の承認ゲートで差し戻した（依頼 {q.get('id')}: {q.get('reason') or '-'}）"
            if q.get("pr"):
                got = step_close_release_pr(self.main, int(q["pr"]), f"{reason}。直しは次のリリースの提示から新しい PR で承認を取り直す")
                reason += "" if got.get("closed") else f"。本番の PR #{q['pr']} を閉じられない（{got.get('reason')}）"
            else:
                reason += "。本番の PR の番号を読めないため閉じていない"
            self.settle(rel, {"result": "rejected", "reason": reason})
        return True
