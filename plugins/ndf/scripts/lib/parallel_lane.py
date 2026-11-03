"""並列の計画のレーンの側（#2436）: 自分が属する計画の判定（I12）・すでに配布済みかの判定（I15）・配布の依頼を送る・
答えを読む（待たずに読むのと待つのの両方）・プランの本数を返す。

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

import parallel_plan as pp

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


def run_status(main: Path, name: str) -> Optional[str]:
    """計画の耐久ワークフローの状態（記録が無ければ None）。"""
    if not record_exists(main, name):
        return None
    import durable

    return durable.status_from(KIND, record_identity(main, name), workflow_id(name))


def plans_in(main: Path) -> list[str]:
    root = Path(main) / pp.PARALLEL_DIR
    return sorted(p.name for p in root.iterdir() if (p / "plan.json").is_file()) if root.is_dir() else []


def lane_of(main: Path, lane: Optional[str] = None, status=run_status) -> Optional[tuple[str, pp.Lane]]:
    """`NDF_RELAY_NAME` の名前が、耐久ワークフローが終わっていない計画のレーンにあれば (計画名, レーン)。無ければ None（I12）。"""
    lane = lane if lane is not None else os.environ.get("NDF_RELAY_NAME")
    if not lane:
        return None
    for name in plans_in(main):
        try:
            plan = pp.ParallelPlan.model_validate(pp.load_plan(main, name), context={"max_plans": 10**6, "max_named": 10**6})
        except (OSError, ValueError):
            continue
        hit = plan.lane(lane)
        if hit is not None and status(main, name) in RUNNING:
            return name, hit
    return None


def lane_plans(main: Path, lane: Optional[str] = None) -> Optional[int]:
    """レーンなら、そのレーンの同時に流すプランの本数。レーンでなければ None（`queue --max` の既定）。"""
    got = lane_of(main, lane)
    return got[1].plans if got else None


def merged_on_main(root: Path, prs: list[int], run=subprocess.run) -> bool:
    """`prs` のマージのコミットがすべて `origin/main` にあるか（I15）。1 本でも分からなければ False。"""
    if not prs:
        return False
    for n in prs:
        got = run(["gh", "pr", "view", str(n), "--json", "mergeCommit", "-q", ".mergeCommit.oid"], cwd=root, capture_output=True, text=True)
        sha = (got.stdout or "").strip()
        if got.returncode != 0 or not sha:
            return False
        anc = run(["git", "merge-base", "--is-ancestor", sha, "origin/main"], cwd=root, capture_output=True, text=True)
        if anc.returncode != 0:
            return False
    return True


def released_version(main: Path, name: str, prs: list[int]) -> Optional[str]:
    """`prs` を含む範囲のイベント `range-<連番>` の版（無ければ None）。

    `range-<連番>` は出せた配布にだけあり、差し戻し・失敗の配布の連番は欠ける。欠番で打ち切らず、配布の置き場
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
    """配布の依頼のメッセージの形の誤り（`contracts/forms/parallel-request.json`）。空なら形に合う。"""
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
    """配布の依頼を話題 `inbox` へ送る。冪等の鍵は `<ID>:inbox:<依頼>`。依頼の ID を返す。"""
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
        "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
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


# ---- 問いの依頼と答え（#2436 の 6c・6d・6f。設計 issue-2436-design-approvals.md） ----

ROLE_FILE = "role.json"  # relay_lib.hub.ROLE_FILE（統括役の作業ディレクトリの役割のファイル。統括役を import しない）


def helper_plan_of(relay_dir: Optional[str]) -> Optional[str]:
    """作業ディレクトリ（`NDF_RELAY_DIR`）の役割のファイルが `helper` なら、その計画名。"""
    if not relay_dir:
        return None
    try:
        got = json.loads((Path(relay_dir) / ROLE_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return got.get("plan") if isinstance(got, dict) and got.get("role") == "helper" and got.get("plan") else None


def lane_name_in_plans(main: Path, name: Optional[str], relay_dir: Optional[str]) -> Optional[str]:
    """レーンか一時レーンかをファイルだけで見る（問いの hook の 10 秒の上限の中で DBOS を読まない）。計画名か None。"""
    if not name:
        return None
    for plan in plans_in(main):
        try:
            lanes = json.loads((pp.plan_dir(main, plan) / "plan.json").read_text(encoding="utf-8")).get("lanes") or []
        except (OSError, ValueError, AttributeError):
            continue
        if any(isinstance(ln, dict) and ln.get("name") == name for ln in lanes):
            return plan
    return helper_plan_of(relay_dir)


def sender_of(main: Path, name: Optional[str], relay_dir: Optional[str], status=run_status) -> Optional[tuple[str, str]]:
    """問いを送ってよい送り手か: 動いている計画のレーン（`lane_of`）か、役割のファイルが `helper` で、その計画の
    耐久ワークフローが終わっていない一時レーン。(計画名, 送り手の名前) か None。"""
    got = lane_of(main, name, status) if name else None
    if got:
        return got[0], name
    plan = helper_plan_of(relay_dir)
    if name and plan and status(main, plan) in RUNNING:
        return plan, name
    return None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _send_inbox(main: Path, name: str, msg: dict, key: str) -> None:
    import durable

    wid = workflow_id(name)
    durable.send_to(KIND, record_identity(main, name), wid, msg, "inbox", f"{wid}:inbox:{key}")


def _head(cwd: str) -> str:
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
        "stamp": _head(cwd),
        "at": _now(),
    }
    _send_inbox(main, name, msg, rid)
    return rid


def request_gate1(main: Path, name: str, lane: str, issue: int, pr: int, report: str, state: str, stamp: str, **extra) -> str:
    """設計の承認の依頼（`kind: gate1`）。id は `<レーン>-g<設計 PR>-<先頭の SHA の 12 桁>`（同じ先頭の打ち直しは同じ id）。"""
    rid = f"{lane}-g{pr}-{stamp[:12]}"
    msg = {"kind": "gate1", "id": rid, "lane": lane, "issue": issue, "pr": pr, "report": report, "sprint_state": state, "stamp": stamp}
    _send_inbox(main, name, {**msg, **extra, "at": _now()}, rid)
    return rid


def request_gate2(main: Path, name: str, n: int, k: int, report: str) -> str:
    """一時レーンが提示のプランの承認ゲート 2 で止まったときの依頼（`kind: gate2`）。id は `<計画名>-p<連番>-<提示>`、
    stamp は提示したコミット（`presented.json`）。"""
    import parallel_release as prl

    try:
        sha = json.loads((prl.release_dir(main, name, n, k) / "presented.json").read_text(encoding="utf-8")).get("sha") or ""
    except (OSError, ValueError):
        sha = ""
    rid = f"{name}-p{n}-{k}"
    lane = prl.helper_name(name, n)
    _send_inbox(main, name, {"kind": "gate2", "id": rid, "lane": lane, "n": n, "k": k, "report": report, "stamp": sha, "at": _now()}, rid)
    return rid


def send_answer(
    main: Path, name: str, rid: str, result: str, note: Optional[str] = None, choice: Optional[list[str]] = None, stamp: Optional[str] = None
) -> None:
    """オーガナイザーの答えを送る。耐久ワークフローが `answer-<依頼>` を 1 回だけ立て、問いの一覧から外す。"""
    msg = {"kind": "answer", "id": rid, "result": result, "note": note, "choice": list(choice or []), "stamp": stamp, "at": _now()}
    _send_inbox(main, name, msg, f"answer:{rid}")
