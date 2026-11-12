"""並列の計画のレーンの側（#2436）: 自分が属する計画の判定（I12）・すでにリリース済みかの判定（I15）・リリースの依頼を送る・
答えを読む（待たずに読むのと待つのの両方）・プランの本数を返す。

環境変数 `NDF_ORGANIZER_DIR` があれば、外のオーガナイザーのレーンとして判定し、計画名の代わりに `--external` を返す
（#2439。依頼と答えは置き場のファイルで受け渡す。`parallel_external.py`）。外のレーンは設計の承認と承認ゲートの外の問いを
依頼にせず、今どおり止まって人へ戻す。

別のリポジトリのレーン（計画の宣言のレーンの `repo`。#2733）は、自分のメインディレクトリと計画の所在の一覧
（`parallel_homes`）の置き場を探して判定する。判定は `LaneRef`（(計画名, レーン) の 2 つ組として読め、計画の置き場 `home` と
レーンのリポジトリ `repo` を持つ）を返し、依頼・答え・止める指示は `home` の側で読み書きする（I5）。

耐久の記録は外のプロセスとして `durable.py` の `send_to`・`event_of`・`status_of` で開く（DBOS を起動しない）。
DBOS を読まずに済む判定（計画の有無・本数）は、耐久の記録のファイルが無ければ DBOS を import しない。
"""

from __future__ import annotations

import json
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import parallel_external as pe
import parallel_ledger as plg
import parallel_plan as pp
from parallel_request_form import REQUEST_ID_RE, request_errors  # noqa: F401  依頼の形（契約の検査と外のレーンが読む）
from parallel_files import helper_plan_of, lane_name_in_plans, plan_home, plans_in, resolved_path  # noqa: F401  ファイルだけで読む判定（問いの hook も使う）

KIND = "parallel"
RUNNING = ("PENDING", "ENQUEUED")


def workflow_id(name: str) -> str:
    """耐久ワークフローの ID（計画名から決まる）。"""
    return f"parallel-{name}"


def record_identity(main: Path, name: str) -> str:
    """耐久の記録の識別（計画の置き場の絶対パス）。"""
    return str(pp.plan_dir(main, name).resolve())


def record_start(main: Path) -> Path:
    """耐久の記録の置き場を決めるチェックアウト（計画の置き場）。別のリポジトリのレーンの作業ディレクトリからでも、
    オーガナイザーが書いた計画の置き場の記録を読む（#2733）。`durable` の `start` へ渡す。"""
    return Path(main)


def record_exists(main: Path, name: str) -> bool:
    from durable_keys import record_path

    return record_path(KIND, record_identity(main, name), record_start(main)).exists()


def live_run_pid(main: Path, name: str) -> Optional[int]:
    """`run.pid` の run が動いていればその pid。

    生きていても起動の引数が `parallel.py run <計画名>` でなければ、使い回された別のプロセスとして動いていないと読む
    （#2647 の決定 9）。引数を読めないときは二重起動を避けて動いていると読む（重なっても `launch` の排他が止める）。"""
    import procs

    try:
        pid = int((pp.plan_dir(main, name) / "run.pid").read_text().strip())
        os.kill(pid, 0)
    except (OSError, ValueError):
        return None
    return None if procs.cmdline_contains(pid, f"parallel.py run {name}") is False else pid


def run_status(main: Path, name: str) -> Optional[str]:
    """計画の耐久ワークフローの状態（記録が無ければ None）。"""
    if not record_exists(main, name):
        return None
    import durable

    return durable.status_from(KIND, record_identity(main, name), workflow_id(name), start=record_start(main))


DONE_FILE = "run.done"


def settled_status(main: Path, name: str) -> Optional[str]:
    """`run_status` と同じ答えを、終わった計画ではファイルだけで返す（計画の外の判定がループの 1 回ごとに DBOS を開かないため）。
    終わった状態（`RUNNING` でも None でもない状態）は変わらない（`run_plan` は終わった耐久ワークフローを始め直さない）ため、
    一度読んだら計画の置き場の `run.done` へ書き、次からはそれを読む。`start` と `run` は始める前に消す（`clear_settled`）。"""
    done = pp.plan_dir(main, name) / DONE_FILE
    try:
        got = done.read_text(encoding="utf-8").strip()
    except OSError:
        got = ""
    if got:
        return got
    st = run_status(main, name)
    if st is not None and st not in RUNNING:
        try:
            done.write_text(f"{st}\n", encoding="utf-8")
        except OSError:
            pass  # 書けない置き場でも答えは同じ。次も DBOS で読む
    return st


def clear_settled(main: Path, name: str) -> None:
    (pp.plan_dir(main, name) / DONE_FILE).unlink(missing_ok=True)


def external_lane() -> Optional[tuple[str, pp.Lane]]:
    """外のオーガナイザーのレーンなら (`--external`, レーン)。名前は `NDF_ORGANIZER_LANE`（#2439）。"""
    if pe.organizer_dir() is None:
        return None
    name = pe.organizer_lane() or "external"
    lane = pp.Lane.model_construct(
        name=name, input=pp.SprintInput.model_construct(kind="sprint", issues=[], args=[]), plans=QUEUE_MAX, touches=[]
    )
    return pe.EXTERNAL, lane


class LaneRef(tuple):
    """レーンの判定の結果。(計画名, レーン) の 2 つ組として読め、計画の置き場 `home` とレーンのリポジトリ `repo` を持つ（#2733）。"""

    def __new__(cls, plan: str, lane, home: Path, repo: Optional[Path] = None):
        obj = super().__new__(cls, (plan, lane))
        obj.plan, obj.lane, obj.home = plan, lane, Path(home)
        obj.repo = Path(repo) if repo is not None else Path(home)
        return obj

    def own_release(self) -> bool:
        """別のリポジトリのレーンか（自分のリポジトリの設定で本番を出す。I7）。"""
        return resolved_path(self.repo) != resolved_path(self.home)


def home_of(got, main: Path) -> Path:
    """判定の結果の計画の置き場（外のオーガナイザーのレーンの 2 つ組は置き場を持たないため `main`）。"""
    return getattr(got, "home", None) or Path(main)


def lane_of(main: Path, lane: Optional[str] = None, status=run_status) -> Optional[tuple[str, pp.Lane]]:
    """`NDF_RELAY_NAME` の名前が、耐久ワークフローが終わっていない計画のレーンにあれば `LaneRef`（(計画名, レーン)）。
    無ければ None（I12）。自分のメインディレクトリ `main` と計画の所在の一覧の置き場を探し、レーンのリポジトリが `main` と
    一致する計画だけを当てる（#2733 の I5）。外のオーガナイザーのレーン（`NDF_ORGANIZER_DIR`）なら、名前を渡さない判定で
    (`--external`, レーン) を返す（#2439。所在の一覧より先に見る）。"""
    if lane is None and (ext := external_lane()):
        return ext
    lane = lane if lane is not None else os.environ.get("NDF_RELAY_NAME")
    if not lane:
        return None
    import parallel_homes

    me, lost = resolved_path(Path(main)), None
    for home in parallel_homes.search_homes(Path(main)):
        for name in plans_in(home):
            try:
                plan = pp.load_valid_plan(home, name)
            except (OSError, ValueError):
                continue
            hit = plan.lane(lane)
            if hit is None or resolved_path(hit.repo_dir(home)) != me:
                continue
            st = plg.plan_state(home, name, status(home, name))  # 動いている計画を先に、失われた計画は後で当てる（決定 8）
            if st == "running":
                return LaneRef(name, hit, home, hit.repo_dir(home))
            lost = lost or (LaneRef(name, hit, home, hit.repo_dir(home)) if st == "lost" else None)
    return lost


def lane_waits(main: Path, name: str, lane: str, rid: str, relay_ls) -> bool:
    """答えの後に持ち主のレーンを起こすかを、レーンの側の待ちの記録で決める（6f）。ラッパーの状態では決めない（止め方の
    食い違いで running が残っても起こし損ねないため）。ループのレーンは記録が `waiting_only` か `answer_hold` で止まっている、1 スプリントの
    レーンは引継ぎ文書に依頼の id がある（保留の手順が書く）。記録を持たない一時レーンは、ラッパーが止まっているか
    （`relay_ls()` の統一 JSON レポートで running / awaiting_approval でない）で決める（保留の手順の最後に自分で止める）。"""
    from parallel_hold import WAIT_STOPS
    from sprint_loop_names import LOOP_DIR

    try:
        hit = pp.load_valid_plan(main, name).lane(lane)
    except (OSError, ValueError):
        hit = None
    if hit is None:
        code, got = relay_ls()
        state = next((i.get("state") for i in got.get("items") or [] if i.get("name") == lane), None) if code == 0 else None
        return state not in ("running", "awaiting_approval")
    repo = hit.repo_dir(main)  # ループの記録と引継ぎ文書はレーンのリポジトリにある（#2733 の I6）
    try:
        if isinstance(hit.input, pp.LoopInput):
            rec = json.loads((repo / LOOP_DIR / hit.input.loop / "loop.json").read_text(encoding="utf-8"))
            return rec.get("status") == "stopped" and (rec.get("stop") or {}).get("condition") in WAIT_STOPS
        return rid in (repo / ".ndf" / "handoff" / f"{hit.handoff_name()}.md").read_text(encoding="utf-8")
    except (OSError, ValueError, AttributeError):
        return False


def lane_plans(main: Path, lane: Optional[str] = None) -> Optional[int]:
    """レーンなら、そのレーンの同時に流すプランの本数。レーンでなければ None（`queue --max` の既定）。"""
    got = lane_of(main, lane)
    return got[1].plans if got else None


QUEUE_MAX = 3  # 並列の計画のレーンでないときの `queue --max` の既定


def queue_max(cwd: Optional[str] = None) -> int:
    """`queue` の同時に流す本数。並列の計画のレーンならレーンのプランの本数、そうでなければ 3（#2436）。

    `queue` の `--max` の既定と、スプリント・ループの 1 回の続きが組む queue の 1 行が同じ値を使う（レーンの本数を超えない）。"""
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
    rel = pp.plan_dir(main, name) / "releases"
    try:
        seqs = sorted(int(d.name) for d in rel.iterdir() if d.is_dir() and d.name.isdigit())
    except OSError:
        seqs = []
    if not seqs:
        return None
    found = None
    for n, ev in zip(seqs, events_of(main, name, [f"range-{n}" for n in seqs])):
        if isinstance(ev, dict) and set(prs) & set(ev.get("prs") or []):
            found = ev.get("version")
    return found


def events_of(main: Path, name: str, keys: list[str]) -> list:
    """イベントを待たずに読む。耐久の記録に無い（作り直しの前に立てた・記録が無い）ものは台帳のチェックポイントの控えを読む（決定 6）。"""
    import durable

    got = [None] * len(keys)
    if record_exists(main, name):
        try:
            with durable.open_client(KIND, record_identity(main, name), record_start(main)) as c:
                got = [c.get_event(workflow_id(name), k, timeout_seconds=0) for k in keys]
        except durable.DurableError:
            pass
    kept = plg.published_events(main, name) if None in got else {}  # チェックポイントは 1 回だけ読む
    return [ev if ev is not None else kept.get(k) for ev, k in zip(got, keys)]


def lane_request_id(state: Path, lane: str) -> str:
    """依頼の ID。最初の実行で `<レーン名>-r<乱数 12 桁>` を作って `work/request-id.txt` へ書き、打ち直しでは読む。"""
    p = Path(state) / "work" / "request-id.txt"
    if p.is_file() and p.read_text(encoding="utf-8").strip():
        return p.read_text(encoding="utf-8").strip()
    p.parent.mkdir(parents=True, exist_ok=True)
    rid = f"{lane}-r{secrets.randbelow(10**12):012d}"
    (Path(state) / "work" / "request-id.txt").write_text(rid + "\n", encoding="utf-8")
    return rid


def send_release_request(
    main: Path, name: str, lane: str, prs: list[int], issues: list[int], state: Path, verify_plan: Optional[str] = None
) -> str:
    """リリースの依頼を台帳へ書いて話題 `inbox` へ送る（`deliver_message`）。依頼の ID を返す。`verify_plan` は版の要るまとめの
    プラン（1 スプリントのレーンの依頼の経路。`released` の後に一時レーンが流す。#3058 の決定 4）。"""
    rid = lane_request_id(state, lane)
    msg = {
        "kind": "request",
        "id": rid,
        "lane": lane,
        "prs": list(prs),
        "issues": list(issues),
        "sprint_state": str(state),
        "at": pe.utc_stamp(),
        **({"verify_plan": str(verify_plan)} if verify_plan else {}),
    }
    deliver_message(main, name, msg, "inbox", rid)
    return rid


def read_answer(main: Path, name: str, rid: str, timeout: float = 0) -> tuple[Optional[dict], Optional[dict]]:
    """(答え `answer-<依頼>`, 知らせ `defer-<依頼>`)。`timeout` 秒まで答えを待つ（0 は待たない）。知らせでは返らない（I10）。"""

    deadline = time.monotonic() + max(0.0, timeout)
    while True:
        ans, dfr = events_of(main, name, [f"answer-{rid}", f"defer-{rid}"])
        if isinstance(ans, dict):
            return ans, None
        if time.monotonic() >= deadline:
            return None, dfr if isinstance(dfr, dict) else None
        time.sleep(min(5.0, max(0.2, deadline - time.monotonic())))


def write_request(state: Path, data: dict) -> Path:
    """依頼の記録をプランの状態ディレクトリの `work/request.txt` へ書き、そのパスを返す（`parallel.py await --from` が読む）。"""
    (Path(state) / "work").mkdir(parents=True, exist_ok=True)
    (Path(state) / "work" / "request.txt").write_text(json.dumps(data, ensure_ascii=False) + "\n", encoding="utf-8")
    return Path(state) / "work" / "request.txt"


def answers_of(main: Path, name: str, ids: list[str]) -> dict[str, dict]:
    """依頼ごとの {"answer": `answer-<依頼>`, "defer": `defer-<依頼>`} を待たずに読む（ループの 1 回の境で読む。#2436 の 6a・6e）。
    耐久の記録が無ければ、どれも答えの無いものとして返す（DBOS を import しない）。"""
    if pe.is_external(name):
        d = pe.organizer_dir()
        return pe.answer_files_of(d, ids) if d else {i: {"answer": None, "defer": None} for i in ids}
    out = {i: {"answer": None, "defer": None} for i in ids}
    if not ids or not (record_exists(main, name) or plg.checkpoint_path(main, name).exists()):
        return out
    got = events_of(main, name, [k for i in ids for k in (f"answer-{i}", f"defer-{i}")])
    for i, ans, dfr in zip(ids, got[::2], got[1::2]):
        out[i] = {"answer": ans if isinstance(ans, dict) else None, "defer": dfr if isinstance(dfr, dict) else None}
    return out


# ---- 問いの依頼と答え（#2436 の 6c・6d・6f。確定仕様 docs/specifications/ndf-parallel-organizer.md） ----


def sender_of(main: Path, name: Optional[str], relay_dir: Optional[str], status=run_status) -> Optional[tuple[str, str]]:
    """問いを送ってよい送り手か: 動いている計画のレーン（`lane_of`）か、役割のファイルが `helper` で、その計画の
    耐久ワークフローが終わっていない一時レーン。`LaneRef`（(計画名, 送り手の名前)。`home` は計画の置き場）か None。
    外のオーガナイザーのレーンは問いを送らない（#2439）。"""
    if pe.organizer_dir() is not None:
        return None
    got = lane_of(main, name, status) if name else None
    if got:
        return LaneRef(got[0], name, got.home, got.repo)
    plan = helper_plan_of(relay_dir)
    if name and plan and plg.accepts(plg.plan_state(main, plan, status(main, plan))):
        return LaneRef(plan, name, Path(main))
    return None


def _send_inbox(main: Path, name: str, msg: dict, key: str) -> None:
    if pe.is_external(name):
        # 外のオーガナイザーのレーンは設計の承認と問いを依頼にしない（今どおり止まって人へ戻す。#2439）
        raise ValueError("外のオーガナイザーのレーンは問いを依頼にしない")
    deliver_message(main, name, msg, "inbox", key)


def deliver_message(main: Path, name: str, msg: dict, topic: str, key: str) -> None:
    """台帳へ書いてから耐久ワークフローへ知らせる（#2647 の I1）。台帳を書けなければ例外で返し、知らせない。

    知らせの失敗（記録が無い・開けない）は標準エラーへ出すだけにする。耐久ワークフローは次の観測で台帳から読む。
    冪等の鍵は `<ID>:<話題>:<key>`。"""
    import durable

    kind, lkey = plg.message_key(msg, topic, key)
    plg.append_ledger_row(main, name, kind, lkey, msg, by=msg.get("lane") or ("organizer" if kind == "answer" else None))
    if not record_exists(main, name):
        return
    wid = workflow_id(name)
    try:
        durable.send_to(KIND, record_identity(main, name), wid, msg, topic, f"{wid}:{topic}:{key}", start=record_start(main))
    except durable.DurableError as exc:
        print(f"parallel: 耐久ワークフローへ知らせられない（台帳には書いた）: {exc}", file=sys.stderr, flush=True)


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


def send_prior(main: Path, name: str, msg: dict) -> None:
    """前のループの待ちの依頼（`kind: gate1` と `loop`）を送る。id は行から決まり、同じ行の打ち直しは 1 つにまとまる。"""
    _send_inbox(main, name, msg, msg["id"])


def send_unmatched(main: Path, name: str, lane: str, answered: str, reason: str) -> None:
    """当たらなかった答えの知らせ（`kind: unmatched`）を送る。"""
    rid = f"{answered}-unmatched"
    text = f"依頼 {answered} への答えを当てられなかった: {reason}"
    msg = {"kind": "unmatched", "id": rid, "answered": answered, "lane": lane, "reason": reason, "question": text, "options": ["確かめた"]}
    _send_inbox(main, name, {**msg, "at": pe.utc_stamp()}, rid)


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


def send_left_stopped(main: Path, name: str, lane: str) -> None:
    """人が止めたままにするレーンを耐久ワークフローへ知らせる（`parallel.py park --clear`）。次の停止で打った分も届くよう、鍵に時刻を入れる。"""
    _send_inbox(main, name, {"kind": "left_stopped", "lane": lane}, f"left-stopped:{lane}:{time.time_ns()}")


def requests_for(main: Path, name: str) -> list[dict]:
    """オーガナイザーが読める依頼（`releases/requests.json`。耐久ワークフローがリリースを組むたびに書く。無ければ空）。"""
    p = pp.plan_dir(main, name) / "releases" / "requests.json"
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []


def resume_plan(main: Path, name: str, msg: dict, key: str) -> None:
    """リリースの承認と結果を送る（`key` は話題の中の冪等の鍵）。"""
    deliver_message(main, name, msg, "resume", key)
