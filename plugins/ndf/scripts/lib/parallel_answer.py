"""並列の計画の問いへ答えた後に、だれを起こすか（#2436 の 6c・6f）と、答えの後に残る依頼の一覧（#2507 の答えの後の整理）。
`parallel.py answer` だけが使う。

`parallel_run`（耐久ワークフローの本体）を読むため、レーンの側の `parallel_lane` から分けて置く（レーンの側の入口が
`parallel_run` の環境変数を読まないように）。
"""

from __future__ import annotations

from pathlib import Path

import parallel_hold as ph
import parallel_lane as pl


def questions(main: Path, name: str) -> tuple[int, list[dict]]:
    """耐久ワークフローの今の依頼の一覧 (seq, items)。読めなければ (0, [])。

    耐久ワークフローがまだ受け取っていない問い（失われた計画へ届いた問い・知らせの前の問い）は計画の台帳から足す（#2647）。"""
    import durable
    import parallel_ledger as plg

    try:
        ident, wid = pl.record_identity(main, name), pl.workflow_id(name)
        got = durable.event_from(pl.KIND, ident, wid, "questions", 0, start=pl.record_start(main))
    except durable.DurableError:
        got = None
    items = list((got or {}).get("items") or [])
    seen = {x for q in items for x in (q.get("id"), *(q.get("also") or []))}
    return int((got or {}).get("seq") or 0), items + [q for q in plg.pending_questions(main, name) if q.get("id") not in seen]


MVV_VERDICTS = ("unknown", "unreadable")  # レーンの判定が決まらなかった（判定できない・判定を読めない）


def not_listed(rid: str, items: list[dict]) -> dict:
    """今の問いの一覧に無い依頼への答えなら、結果へ足す `warning` を返す（#2617）。"""
    if any(q.get("id") == rid for q in items):
        return {}
    return {"warning": f"依頼 {rid} は今の問いの一覧に無い。答えは保留され、その依頼が届いたら当たる（答え済みなら何もしない）"}


def mvv_mark(q: dict) -> dict:
    """MVV 判定でオーガナイザーが自分で答えてよい依頼か（#2507 の決定 8）。設計の承認で、レーンの判定が決まらず、
    レッドラインが空で、利用者へ戻した依頼（印 `returned`。#2507 の決定 9）でなく、判定を打ち直す材料（レーンのループの 1 回のスプリント状態）が残るときだけ true。"""
    if q.get("kind") != "gate1":
        return {"eligible": False, "why": "設計の承認の依頼でない"}
    if q.get("boundary"):
        return {"eligible": False, "why": "レッドラインに当たる"}
    if q.get("returned"):
        return {"eligible": False, "why": "マージできずに利用者へ戻した依頼（直す・やめるは利用者が選ぶ）"}
    if q.get("verdict") not in MVV_VERDICTS:
        return {"eligible": False, "why": f"レーンの判定が決まっている（{q.get('verdict')}）"}
    if not q.get("sprint_state") or not Path(q["sprint_state"]).is_file():
        return {"eligible": False, "why": "判定を打ち直す材料（スプリント状態）が無い"}
    return {"eligible": True, "why": f"レーンの判定が決まらなかった（{q.get('verdict')}）"}


SENT = "answers-sent.json"  # 送ったが耐久ワークフローがまだ一覧から外していない答え（計画のディレクトリに置く）


def sent_answers(main: Path, name: str, items: list[dict]) -> list[dict]:
    """前に送った答えのうち、今の一覧にまだ残る依頼への答え。`send_answer` は受け手の処理を待たないため、続けて答えると
    一覧に答え済みの依頼が残る。一覧から消えた依頼（受け手が処理した）の答えは落とす。"""
    import jsonio

    live = {i for q in items for i in (q.get("id"), *(q.get("also") or []))}
    got = jsonio.read(pl.pp.plan_dir(main, name) / SENT, missing=[], broken=[], want=list)
    return [s for s in got if isinstance(s, dict) and s.get("id") in live]


def remember_answers(main: Path, name: str, sent: list[dict]) -> None:
    """送った答えの依頼（id・kind・lane・pr）を書き残す。次の `answer` の `metrics.after` から除くため。"""
    import jsonio

    keep = [{k: s.get(k) for k in ("id", "kind", "lane", "pr")} for s in sent]
    jsonio.write_atomic(pl.pp.plan_dir(main, name) / SENT, keep)


def after_items(items: list[dict], answered: dict, earlier: list[dict] | None = None) -> list[dict]:
    """答えの後に残る依頼の一覧（`metrics.after`）。今答えた依頼と、前に送ったが受け手がまだ外していない答えの依頼（`earlier`）を除き、
    機械で決まる除外の印（drop）と MVV 判定の印（mvv）を付ける。
    drop: settled（送った答えと同じ設計 PR の依頼）/ resend（同じレーンの同じ設計 PR に、より新しい先端の依頼がある）/ None。"""
    done = [answered, *(earlier or [])]
    ids = {d.get("id") for d in done}
    rest = [q for q in items if q.get("id") not in ids and not ids & set(q.get("also") or [])]
    key = lambda q: (q.get("lane"), q.get("pr"))  # noqa: E731
    settled = {key(d) for d in done if d.get("kind") == "gate1"}

    def drop(q: dict) -> str | None:
        if q.get("kind") != "gate1":
            return None
        if key(q) in settled:
            return "settled"
        if any(
            o is not q and o.get("kind") == "gate1" and key(o) == key(q) and str(o.get("at") or "") > str(q.get("at") or "") for o in rest
        ):
            return "resend"
        return None

    return [{**q, "drop": drop(q), "mvv": mvv_mark(q)} for q in rest]


def _restart_lines(lanes: list[str]) -> str | None:
    import parallel_run as pr_run

    return "; ".join(f"python3 {pr_run.RELAY} restart {lane}" for lane in lanes) or None


def owner_lanes(main: Path, name: str, rid: str, q: dict) -> list[str]:
    """答えを待って止まった持ち主のレーン。起こす要否はレーンの側の待ちの記録で決める（`parallel_lane.lane_waits`）。同じ原因としてまとめた
    問い（`merged`）は、代表だけでなく全持ち主をそれぞれの依頼の id で調べる（異常系 5）。"""
    import parallel_run as pr_run

    ls = lambda: pr_run.relay(main, "ls", "--json")  # noqa: E731
    owners = [(q["lane"], rid), *((m.get("lane"), m.get("id")) for m in q.get("merged") or [] if m.get("lane"))]
    return list(dict.fromkeys(lane for lane, each in owners if pl.lane_waits(main, name, lane, str(each or rid), ls)))


def owner_next(
    main: Path, name: str, rid: str, choice: list[str] | None = None, items: list[dict] | None = None
) -> tuple[str | None, list[str]]:
    """答えの後の (1 行, 答え待ちの停止の範囲にある持ち主)。自分で立てた問い（stall・helper・spawn・observe）は選んだ答えの commands、
    ほかは止まった持ち主の restart を `; ` でつなぐ。停止の範囲にある計画のレーンは起こさず、再開の印を置く（#2707 の I11）。
    リリースの一時レーンは計画全体の停止の間も restart の 1 行で起こす（再開の印を突き合わせるのは計画のレーンだけのため）。"""
    items = questions(main, name)[1] if items is None else items
    q = next((q for q in items if q.get("id") == rid), {})
    if q.get("kind") in ("stall", "helper", "spawn", "observe") or not q.get("lane"):
        return next((q["commands"][c] for c in choice or [] if (q.get("commands") or {}).get(c)), None), []
    lanes, hold = owner_lanes(main, name, rid, q), ph.read_hold(main, name)
    held = [lane for lane in lanes if ph.plan_held(main, name, hold, lane)]
    for lane in held:
        ph.place_mark(main, name, lane, "answered")
    return _restart_lines([lane for lane in lanes if lane not in held]), held


def answer_arg_error(revise: bool, note: str | None, approved: bool, by: str) -> str | None:
    """`parallel.py answer` の引数の組み合わせの誤り。誤りが無ければ None。"""
    if revise and not note:
        return "--revise には --note <指摘ファイル> が要る"
    if by == "mvv" and not approved:
        return "--by mvv は --approved とだけ組む"
    return None


def lane_repo(main: Path, name: str, lane: str | None) -> Path:
    """計画の宣言から導いたレーンのリポジトリ（宣言に無い・読めなければ計画の置き場 `main`。#2733 の I6・I8）。"""
    try:
        hit = pl.pp.load_valid_plan(main, name).lane(lane or "")
    except (OSError, ValueError):
        hit = None
    return hit.repo_dir(main) if hit is not None else Path(main)


def repo_lane(q: dict) -> str | None:
    """問いのリポジトリを決めるレーン: レーンの問いは `lane`、別のリポジトリのレーンが送った前のループの待ちは `via`（#2733）。"""
    return q.get("lane") or q.get("via")


def with_repo(main: Path, name: str, items: list[dict]) -> list[dict]:
    """問いの一覧の各行へ、計画の宣言から導いたレーンのリポジトリ `repo` を足す（依頼の本文から読まない。#2733 の I8）。"""
    return [{**q, "repo": str(lane_repo(main, name, repo_lane(q)))} if isinstance(q, dict) else q for q in items]


def merge_design(main: Path, q: dict, name: str | None = None) -> dict | None:
    """設計の承認（gate1）の問いなら、承認した設計 PR をマージしてみる（#2507）。それ以外の問いは None。
    マージはレーンのリポジトリで行う（別のリポジトリのレーンの PR を計画の置き場で探さない。#2733 の I6）。"""
    if q.get("kind") != "gate1" or not q.get("pr"):
        return None
    import design_pr_merge

    repo = lane_repo(main, name, repo_lane(q)) if name else Path(main)
    return design_pr_merge.merge_approved(repo, int(q["pr"]), q.get("stamp"))


def answer_with_merge(main: Path, name: str, q: dict, result: str, items: list[dict], **kw) -> tuple[str, dict]:
    """依頼 `q` へ答えを送る。設計の承認なら、承認した設計 PR をマージしてから送る（成否によらず送る。#2507）。
    返すのは (要約へ足す 1 文, 出力へ足す `merge`（`merge_note`）と `after`)。`after` は今の一覧 `items` から、今の答えと、
    前に送ったが受け手がまだ外していない答えの依頼を除いたもの（`after_items`）。"""
    earlier = sent_answers(main, name, items)
    merge = merge_design(main, q, name) if result == "approved" else None
    pl.send_answer(main, name, q["id"], result, merge=merge, **kw)
    remember_answers(main, name, [*earlier, q])
    tail, extra = merge_note(merge)
    return tail, {**extra, "after": after_items(items, q, earlier)}


def merge_note(merge: dict | None) -> tuple[str, dict]:
    """答えの要約へ足すマージの結果の 1 文と、出力へ足す `merge`。マージを試していなければ ("", {})。"""
    if not merge:
        return "", {}
    import design_pr_merge

    if merge["result"] in design_pr_merge.MERGED_RESULTS:
        return "。設計 PR をマージした", {"merge": merge}
    return f"。設計 PR をマージできない（{merge['result']}: {merge['reason']}）", {"merge": merge}
