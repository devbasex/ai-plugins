"""並列の計画のレーンの側（#2436）: 自分が属する計画の判定（I12）・すでにリリース済みかの判定（I15）・リリースの依頼を送る・
答えを読む（待たずに読むのと待つのの両方）・プランの本数を返す。

環境変数 `NDF_ORGANIZER_DIR` があれば、外のオーガナイザーのレーンとして判定し、計画名の代わりに `--external` を返す
（#2439。依頼と答えは置き場のファイルで受け渡す。`parallel_external.py`）。外のレーンは設計の承認と承認ゲートの外の問いを
依頼にせず、今どおり止まって人へ戻す。

耐久の記録は外のプロセスとして `durable.py` の `send_to`・`event_of`・`status_of` で開く（DBOS を起動しない）。
DBOS を読まずに済む判定（計画の有無・本数）は、耐久の記録のファイルが無ければ DBOS を import しない。
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import time
from pathlib import Path
from typing import Optional

import parallel_external as pe
import parallel_plan as pp
from parallel_files import helper_plan_of, lane_name_in_plans, plans_in  # noqa: F401  ファイルだけで読む判定（問いの hook も使う）

KIND = "parallel"
RUNNING = ("PENDING", "ENQUEUED")


def workflow_id(name: str) -> str:
    """耐久ワークフローの ID（計画名から決まる）。"""
    return f"parallel-{name}"


def record_identity(main: Path, name: str) -> str:
    """耐久の記録の識別（計画の置き場の絶対パス）。"""
    return str(pp.plan_dir(main, name).resolve())


def record_exists(main: Path, name: str) -> bool:
    from durable_keys import record_path

    return record_path(KIND, record_identity(main, name)).exists()


def live_run_pid(main: Path, name: str) -> Optional[int]:
    """`run.pid` の run が動いていればその pid。"""
    try:
        pid = int((pp.plan_dir(main, name) / "run.pid").read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return pid


def run_status(main: Path, name: str) -> Optional[str]:
    """計画の耐久ワークフローの状態（記録が無ければ None）。"""
    if not record_exists(main, name):
        return None
    import durable

    return durable.status_from(KIND, record_identity(main, name), workflow_id(name))


def external_lane() -> Optional[tuple[str, pp.Lane]]:
    """外のオーガナイザーのレーンなら (`--external`, レーン)。名前は `NDF_ORGANIZER_LANE`（#2439）。"""
    if pe.organizer_dir() is None:
        return None
    name = pe.organizer_lane() or "external"
    lane = pp.Lane.model_construct(
        name=name, input=pp.SprintInput.model_construct(kind="sprint", issues=[], args=[]), plans=QUEUE_MAX, touches=[]
    )
    return pe.EXTERNAL, lane


def lane_of(main: Path, lane: Optional[str] = None, status=run_status) -> Optional[tuple[str, pp.Lane]]:
    """`NDF_RELAY_NAME` の名前が、耐久ワークフローが終わっていない計画のレーンにあれば (計画名, レーン)。無ければ None（I12）。
    外のオーガナイザーのレーン（`NDF_ORGANIZER_DIR`）なら、名前を渡さない判定で (`--external`, レーン) を返す（#2439）。"""
    if lane is None and (ext := external_lane()):
        return ext
    lane = lane if lane is not None else os.environ.get("NDF_RELAY_NAME")
    if not lane:
        return None
    for name in plans_in(main):
        try:
            plan = pp.load_valid_plan(main, name)
        except (OSError, ValueError):
            continue
        hit = plan.lane(lane)
        if hit is not None and status(main, name) in RUNNING:
            return name, hit
    return None


def lane_waits(main: Path, name: str, lane: str, rid: str, relay_ls) -> bool:
    """答えの後に持ち主のレーンを起こすかを、レーンの側の待ちの記録で決める（6f）。ラッパーの状態では決めない（止め方の
    食い違いで running が残っても起こし損ねないため）。ループのレーンは記録が `waiting_only` で止まっている、1 スプリントの
    レーンは引継ぎ文書に依頼の id がある（保留の手順が書く）。記録を持たない一時レーンは、ラッパーが止まっているか
    （`relay_ls()` の統一 JSON レポートで running / awaiting_approval でない）で決める（保留の手順の最後に自分で止める）。"""
    from sprint_loop_names import LOOP_DIR

    try:
        hit = pp.load_valid_plan(main, name).lane(lane)
    except (OSError, ValueError):
        hit = None
    if hit is None:
        code, got = relay_ls()
        state = next((i.get("state") for i in got.get("items") or [] if i.get("name") == lane), None) if code == 0 else None
        return state not in ("running", "awaiting_approval")
    try:
        if isinstance(hit.input, pp.LoopInput):
            rec = json.loads((Path(main) / LOOP_DIR / hit.input.loop / "loop.json").read_text(encoding="utf-8"))
            return rec.get("status") == "stopped" and (rec.get("stop") or {}).get("condition") == "waiting_only"
        return rid in (Path(main) / ".ndf" / "handoff" / f"{hit.handoff_name()}.md").read_text(encoding="utf-8")
    except (OSError, ValueError, AttributeError):
        return False


def lane_plans(main: Path, lane: Optional[str] = None) -> Optional[int]:
    """レーンなら、そのレーンの同時に流すプランの本数。レーンでなければ None（`queue --max` の既定）。"""
    got = lane_of(main, lane)
    return got[1].plans if got else None


QUEUE_MAX = 3  # 並列の計画のレーンでないときの `queue --max` の既定


def queue_max(cwd: Optional[str] = None) -> int:
    """`queue` の同時に流す本数。並列の計画のレーンならレーンのプランの本数、そうでなければ 3（#2436）。

    `queue` の `--max` の既定と、スプリント・周回の続きが組む queue の 1 行が同じ値を使う（レーンの本数を超えない）。"""
    import sys

    import decl

    root = cwd or os.getcwd()
    try:
        n = lane_plans(decl.main_dir(root) or Path(root))
    except Exception as exc:  # noqa: BLE001  判定できなければ今どおりの 3 で流す（理由は標準エラーへ）
        print(f"[supervise] レーンの判定に失敗したため --max の既定 {QUEUE_MAX} で流す: {exc}", file=sys.stderr)
        n = None
    return n or QUEUE_MAX


def merged_on_production(root: Path, prs: list[int], run=subprocess.run) -> bool:
    """`prs` のマージのコミットがすべて本番チャネル（`repo.production_branch`）の `origin/<本番>` にあるか（I15）。

    本番チャネルが決まらないか、1 本でも分からなければ False。"""
    import repo

    if not prs:
        return False
    prod = repo.production_branch(root)[0]
    if not prod:
        return False
    for n in prs:
        got = run(["gh", "pr", "view", str(n), "--json", "mergeCommit", "-q", ".mergeCommit.oid"], cwd=root, capture_output=True, text=True)
        sha = (got.stdout or "").strip()
        if got.returncode != 0 or not sha:
            return False
        anc = run(["git", "merge-base", "--is-ancestor", sha, f"origin/{prod}"], cwd=root, capture_output=True, text=True)
        if anc.returncode != 0:
            return False
    return True


def released_version(main: Path, name: str, prs: list[int]) -> Optional[str]:
    """`prs` を含む範囲のイベント `range-<連番>` の版（無ければ None）。

    `range-<連番>` は出せたリリースにだけあり、差し戻し・失敗のリリースの連番は欠ける。欠番で打ち切らず、リリースの置き場
    `releases/<連番>/` のある連番をすべて読む。"""
    import durable

    rel = pp.plan_dir(main, name) / "releases"
    try:
        seqs = sorted(int(d.name) for d in rel.iterdir() if d.is_dir() and d.name.isdigit())
    except OSError:
        seqs = []
    if not seqs:
        return None
    found = None
    with durable.open_client(KIND, record_identity(main, name)) as c:
        for n in seqs:
            ev = c.get_event(workflow_id(name), f"range-{n}", timeout_seconds=0)
            if isinstance(ev, dict) and set(prs) & set(ev.get("prs") or []):
                found = ev.get("version")
    return found


def lane_request_id(state: Path, lane: str) -> str:
    """依頼の ID。最初の実行で `<レーン名>-r<乱数 12 桁>` を作って `work/request-id.txt` へ書き、打ち直しでは読む。"""
    p = Path(state) / "work" / "request-id.txt"
    if p.is_file() and p.read_text(encoding="utf-8").strip():
        return p.read_text(encoding="utf-8").strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    rid = f"{lane}-r{secrets.randbelow(10**12):012d}"
    (Path(state) / "work" / "request-id.txt").write_text(rid + "\n", encoding="utf-8")
    return rid


REQUEST_ID_RE = re.compile(r"^([a-z0-9][a-z0-9_-]{0,31})-r\d{12}$")


def request_errors(msg: object) -> list[str]:
    """リリースの依頼のメッセージの形の誤り（`contracts/forms/parallel-request.json`）。空なら形に合う。"""
    if not isinstance(msg, dict):
        return ["オブジェクトでない"]
    errs = []
    if msg.get("kind") != "request":
        errs.append("kind が request でない")
    m = REQUEST_ID_RE.match(str(msg.get("id") or ""))
    if not m:
        errs.append("id が <レーン名>-r<数字 12 桁> でない")
    elif m.group(1) != msg.get("lane"):
        errs.append("id のレーン名が lane と違う")
    for k in ("prs", "issues"):
        v = msg.get(k)
        if not isinstance(v, list) or not all(isinstance(x, int) and not isinstance(x, bool) for x in v):
            errs.append(f"{k} が整数の配列でない")
    for k in ("sprint_state", "at"):
        if not isinstance(msg.get(k), str) or not msg.get(k):
            errs.append(f"{k} が空でない文字列でない")
    return errs


def send_release_request(main: Path, name: str, lane: str, prs: list[int], issues: list[int], state: Path) -> str:
    """リリースの依頼を話題 `inbox` へ送る。冪等の鍵は `<ID>:inbox:<依頼>`。依頼の ID を返す。"""
    import durable

    rid = lane_request_id(state, lane)
    wid = workflow_id(name)
    msg = {
        "kind": "request",
        "id": rid,
        "lane": lane,
        "prs": list(prs),
        "issues": list(issues),
        "sprint_state": str(state),
        "at": pe.utc_stamp(),
    }
    durable.send_to(KIND, record_identity(main, name), wid, msg, "inbox", f"{wid}:inbox:{rid}")
    return rid


def read_answer(main: Path, name: str, rid: str, timeout: float = 0) -> tuple[Optional[dict], Optional[dict]]:
    """(答え `answer-<依頼>`, 知らせ `defer-<依頼>`)。`timeout` 秒まで答えを待つ（0 は待たない）。知らせでは返らない（I10）。"""
    import durable

    ident, wid = record_identity(main, name), workflow_id(name)
    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        ans = durable.event_from(KIND, ident, wid, f"answer-{rid}", 0)
        if isinstance(ans, dict):
            return ans, None
        if time.monotonic() >= deadline:
            dfr = durable.event_from(KIND, ident, wid, f"defer-{rid}", 0)
            return None, dfr if isinstance(dfr, dict) else None
        time.sleep(min(5.0, max(0.2, deadline - time.monotonic())))


def write_request(state: Path, data: dict) -> Path:
    """依頼の記録をプランの状態ディレクトリの `work/request.txt` へ書き、そのパスを返す（`parallel.py await --from` が読む）。"""
    (Path(state) / "work").mkdir(parents=True, exist_ok=True)
    (Path(state) / "work" / "request.txt").write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    return Path(state) / "work" / "request.txt"


def answers_of(main: Path, name: str, ids: list[str]) -> dict[str, dict]:
    """依頼ごとの {"answer": `answer-<依頼>`, "defer": `defer-<依頼>`} を待たずに読む（周回の境で読む。#2436 の 6a・6e）。
    耐久の記録が無ければ、どれも答えの無いものとして返す（DBOS を import しない）。"""
    if pe.is_external(name):
        d = pe.organizer_dir()
        return pe.answer_files_of(d, ids) if d else {i: {"answer": None, "defer": None} for i in ids}
    out = {i: {"answer": None, "defer": None} for i in ids}
    if not ids or not record_exists(main, name):
        return out
    import durable

    with durable.open_client(KIND, record_identity(main, name)) as c:
        for i in ids:
            ans = c.get_event(workflow_id(name), f"answer-{i}", timeout_seconds=0)
            dfr = c.get_event(workflow_id(name), f"defer-{i}", timeout_seconds=0)
            out[i] = {"answer": ans if isinstance(ans, dict) else None, "defer": dfr if isinstance(dfr, dict) else None}
    return out


# ---- 問いの依頼と答え（#2436 の 6c・6d・6f。確定仕様 docs/specifications/ndf-parallel-organizer.md） ----


def sender_of(main: Path, name: Optional[str], relay_dir: Optional[str], status=run_status) -> Optional[tuple[str, str]]:
    """問いを送ってよい送り手か: 動いている計画のレーン（`lane_of`）か、役割のファイルが `helper` で、その計画の
    耐久ワークフローが終わっていない一時レーン。(計画名, 送り手の名前) か None。外のオーガナイザーのレーンは問いを送らない（#2439）。"""
    if pe.organizer_dir() is not None:
        return None
    got = lane_of(main, name, status) if name else None
    if got:
        return got[0], name
    plan = helper_plan_of(relay_dir)
    if name and plan and status(main, plan) in RUNNING:
        return plan, name
    return None


def _send_inbox(main: Path, name: str, msg: dict, key: str) -> None:
    if pe.is_external(name):
        # 外のオーガナイザーのレーンは設計の承認と問いを依頼にしない（今どおり止まって人へ戻す。#2439）
        raise ValueError("外のオーガナイザーのレーンは問いを依頼にしない")
    import durable

    wid = workflow_id(name)
    durable.send_to(KIND, record_identity(main, name), wid, msg, "inbox", f"{wid}:inbox:{key}")


def _head_sha(cwd: str) -> str:
    got = subprocess.run(["git", "-C", cwd, "rev-parse", "HEAD"], capture_output=True, text=True)
    return got.stdout.strip() if got.returncode == 0 else ""


def request_ask(main: Path, name: str, lane: str, tool_input: dict, cwd: str, ledger_id: str) -> str:
    """承認ゲートの外の区分つきの問い（`kind: ask`）をオーガナイザーへ送る。id は `<送り手>-q<判定の記録の id>`。"""
    qs = (tool_input or {}).get("questions") or []
    first = qs[0] if qs and isinstance(qs[0], dict) else {}
    rid = f"{lane}-q{ledger_id}"
    msg = {
        "kind": "ask",
        "id": rid,
        "lane": lane,
        "source": ((tool_input or {}).get("metadata") or {}).get("source"),
        "question": first.get("question"),
        "options": [o.get("label") for o in first.get("options") or [] if isinstance(o, dict)],
        "questions": qs,
        "cwd": cwd,
        "ledger_id": ledger_id,
        "stamp": _head_sha(cwd),
        "at": pe.utc_stamp(),
    }
    _send_inbox(main, name, msg, rid)
    return rid


def request_gate1(
    main: Path, name: str, lane: str, issue: int, pr: int, report: str, state: str, stamp: str, suffix: str = "", **extra
) -> str:
    """設計の承認の依頼（`kind: gate1`）。id は `<レーン>-g<設計 PR>-<先頭の SHA の 12 桁>`（同じ先頭の打ち直しは同じ id）。
    承認した設計をマージできずに利用者へ戻すとき（#2507）は、同じ先端でも別の依頼にするため `suffix` を足す。"""
    rid = f"{lane}-g{pr}-{stamp[:12]}{suffix}"
    msg = {"kind": "gate1", "id": rid, "lane": lane, "issue": issue, "pr": pr, "report": report, "sprint_state": state, "stamp": stamp}
    _send_inbox(main, name, {**msg, **extra, "at": pe.utc_stamp()}, rid)
    return rid


def request_gate2(main: Path, name: str, n: int, k: int, report: str) -> str:
    """一時レーンが提示のプランのリリース承認ゲートで止まったときの依頼（`kind: gate2`）。id は `<計画名>-p<連番>-<提示>`、
    stamp は提示したコミット（`presented.json`）。"""
    import parallel_release as prl

    try:
        sha = json.loads((prl.release_dir(main, name, n, k) / "presented.json").read_text(encoding="utf-8")).get("sha") or ""
    except (OSError, ValueError):
        sha = ""
    rid = f"{name}-p{n}-{k}"
    lane = prl.helper_name(name, n)
    _send_inbox(
        main, name, {"kind": "gate2", "id": rid, "lane": lane, "n": n, "k": k, "report": report, "stamp": sha, "at": pe.utc_stamp()}, rid
    )
    return rid


def send_answer(
    main: Path,
    name: str,
    rid: str,
    result: str,
    note: Optional[str] = None,
    choice: Optional[list[str]] = None,
    stamp: Optional[str] = None,
    merge: Optional[dict] = None,
    by: str = "user",
) -> None:
    """オーガナイザーの答えを送る。耐久ワークフローが `answer-<依頼>` を 1 回だけ立て、問いの一覧から外す。
    設計の承認では、答えの前に試みた承認した設計のマージの結果（`merge`）と、だれが承認したか（`by`: user / mvv）を載せる（#2507）。"""
    msg = {
        "kind": "answer", "id": rid, "result": result, "note": note, "choice": list(choice or []), "stamp": stamp,
        "merge": merge, "by": by, "at": pe.utc_stamp(),
    }  # fmt: skip
    _send_inbox(main, name, msg, f"answer:{rid}")


def requests_for(main: Path, name: str) -> list[dict]:
    """オーガナイザーが読める依頼（`releases/requests.json`。耐久ワークフローがリリースを組むたびに書く。無ければ空）。"""
    p = pp.plan_dir(main, name) / "releases" / "requests.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def resume_plan(main: Path, name: str, msg: dict, key: str) -> None:
    import durable

    durable.send_to(KIND, record_identity(main, name), workflow_id(name), msg, "resume", key)
