"""答え待ちの停止（#2707）: 答えを待つ依頼が人の読める量を超えたら、レーンに新しい作業を始めさせず、閾値以下へ戻ったら再開する。

レーンと問いの hook からも読むため、DBOS を import しない。`AnswerHold` の耐久ステップと耐久ワークフローのイベントは `parallel_run` が渡す。

- 数え方と停止の範囲: `decide_hold` は今の問いの一覧と閾値だけから決まる（累計・時刻・止めた履歴に依らない）
- 止める指示のファイル `answer-hold.json`: 今の停止の範囲。停止が無ければファイルも無い。読めなければ止める指示なしと読む
- 履歴 `answer-hold-log.jsonl`: 止めた・解けたの 1 変化 1 行。止まった回数は `change: on` の行で数える
- 再開の印 `resume/<レーン>.mark.json`: 停止が解けたら起こすレーン。止まる前に置く（観測は一覧の後に読むため、止まって見えたレーンの印を読み逃さない）

置き場はメインディレクトリの `.ndf/parallel/<計画名>/` で、コミットしない。
"""

from __future__ import annotations

import json
import os
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional

import jsonio
import pace
import parallel_files as pf

HOLD_FILE = "answer-hold.json"
LOG_FILE = "answer-hold-log.jsonl"
RESUME_DIR = "resume"
LANE_KINDS = ("gate1", "ask")  # レーンが送った依頼。耐久ワークフローの問いとリリース承認ゲートはレーンの数へ入らない
CONDITION = "answer_hold"  # 開発ループの止まる条件と `parallel.py wait` の action
WAIT_STOPS = ("waiting_only", CONDITION)  # 答えを待って止まったループの止まる条件。答えを受けたら起こす
SKIP_REASON = "答え待ちの停止のため流さなかった"  # プランの開始で流さなかった理由の頭
DESIGN_KIND = "設計"  # 設計を書くステップの kind（`design_steps.writing_steps` が置く）。このステップへ行けるプランは新しい承認を生む
RAN_FILE = "answer-hold-ran.json"  # 範囲の中で流したプランの範囲の値（プランの状態ディレクトリに置く）
REASONS = {
    ("on", "plan"): "答え待ちの依頼が計画の閾値を超えた",
    ("off", "plan"): "答え待ちの依頼が計画の閾値以下へ戻った",
    ("on", "lane"): "レーンの答え待ちの依頼がレーンの閾値を超えた",
    ("off", "lane"): "レーンの答え待ちの依頼がレーンの閾値以下へ戻った",
}


@dataclass(frozen=True)
class Limits:
    """計画の閾値とレーンの閾値。`warnings` は宣言の誤りを既定へ戻した知らせ。"""

    plan: int = pace.PARALLEL_DEFAULTS["plan"]
    lane: int = pace.PARALLEL_DEFAULTS["lane"]
    warnings: tuple = ()


@dataclass(frozen=True)
class Hold:
    """答え待ちの停止の範囲（計画全体か、レーンの並び）と数えた件数。"""

    plan: bool = False
    lanes: tuple = ()
    count: int = 0
    by_lane: dict = field(default_factory=dict)

    @property
    def empty(self) -> bool:
        return not self.plan and not self.lanes

    def scope(self) -> tuple:
        return self.plan, self.lanes

    def covers(self, lane: Optional[str]) -> bool:
        return self.plan or lane in self.lanes

    def diff(self, before: "Hold", limits: Limits) -> list[dict]:
        """`before` からの変化（履歴の行の元）。計画全体で 1 行、レーンは 1 本ごとに 1 行。"""
        out = []
        if self.plan != before.plan:
            out.append(_change("on" if self.plan else "off", "plan", [], self.count, limits.plan))
        for n in [x for x in self.lanes if x not in before.lanes]:
            out.append(_change("on", "lane", [n], self.by_lane.get(n, 0), limits.lane))
        for n in [x for x in before.lanes if x not in self.lanes]:
            out.append(_change("off", "lane", [n], self.by_lane.get(n, 0), limits.lane))
        return out

    def data(self, limits: Limits) -> dict:
        """止める指示のファイルとイベントの中身。"""
        return {
            "version": 1, "plan": self.plan, "lanes": list(self.lanes), "count": self.count, "by_lane": self.by_lane,
            "limits": {"plan": limits.plan, "lane": limits.lane},
        }  # fmt: skip


def _change(change: str, scope: str, lanes: list[str], count: int, limit: int) -> dict:
    return {"change": change, "scope": scope, "lanes": lanes, "count": count, "limit": limit, "reason": REASONS[change, scope]}


def _senders(q: dict) -> set:
    """依頼を送ったレーン（まとめた問いは送った各レーン）。"""
    return {q.get("lane"), *(m.get("lane") for m in q.get("merged") or [])}


def decide_hold(questions: list[dict], order: list[str], limits: Limits) -> Hold:
    """今の問いの一覧と閾値から停止の範囲を導く。同じレーンの同じ設計 PR の設計の承認は 1 件と数える（人が 1 回で答えるため）。
    リリースの依頼は問いの一覧に入らないため数えない。"""
    seen, by_lane = set(), {}
    for q in questions:
        key = (q.get("lane"), q.get("pr")) if q.get("kind") == "gate1" and q.get("pr") else q.get("id")
        if key in seen:
            continue
        seen.add(key)
        if q.get("kind") in LANE_KINDS:
            for lane in _senders(q) & set(order):
                by_lane[lane] = by_lane.get(lane, 0) + 1
    lanes = tuple(n for n in order if by_lane.get(n, 0) > limits.lane)
    return Hold(len(seen) > limits.plan, lanes, len(seen), by_lane)


# ---- 閾値 ----


def read_limits(main: Path) -> Limits:
    got = pace.read_parallel(main)
    return Limits(got["plan"], got["lane"], tuple(got["warnings"]))


def limit_values(main: Path) -> dict:
    """観測へ載せる閾値。"""
    limits = read_limits(main)
    return {"plan": limits.plan, "lane": limits.lane}


def limit_items(main: Path) -> list[dict]:
    """起動の前の検査の警告。閾値の値が正の整数でないとき。"""
    return [{"level": "warning", "rule": "answer-limit", "at": "parallel.answer_limit", "message": w} for w in read_limits(main).warnings]


# ---- 止める指示のファイルと履歴 ----


def _plan_root(main: Path, name: str) -> Path:
    return Path(main) / pf.PARALLEL_DIR / name


def _stamp() -> str:
    """マイクロ秒まで持つ時刻。再開の印を `at` で見分ける（同じ秒に置き直しても別の印と分かる）。"""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def read_hold(main: Path, name: str) -> Optional[dict]:
    """止める指示のファイル。無い・読めなければ None（止める指示なし。読み取りの失敗でレーンを止めない）。"""
    return jsonio.read(_plan_root(main, name) / HOLD_FILE, missing=None, broken=None, want=dict)


def hold_of(data: Optional[dict]) -> Hold:
    """止める指示のファイルの中身から停止の範囲を作る。無い・形の違う値は停止なし。"""
    d = data if isinstance(data, dict) else {}
    lanes = d.get("lanes") if isinstance(d.get("lanes"), list) else []
    by_lane = d.get("by_lane") if isinstance(d.get("by_lane"), dict) else {}
    count = d.get("count") if isinstance(d.get("count"), int) else 0
    return Hold(d.get("plan") is True, tuple(str(n) for n in lanes), count, dict(by_lane))


def covered(hold: Optional[dict], lane: Optional[str]) -> bool:
    return bool(hold) and (hold.get("plan") is True or lane in (hold.get("lanes") or []))


def read_log(main: Path, name: str) -> list[dict]:
    """履歴の行。無ければ空。読めない行は飛ばす。ファイルを読めなければ OSError。"""
    try:
        text = (_plan_root(main, name) / LOG_FILE).read_text(encoding="utf-8")
    except FileNotFoundError:
        return []
    rows = []
    for raw in text.splitlines():
        try:
            row = json.loads(raw)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def change_line(row: dict) -> str:
    """利用者へ出す 1 行。"""
    where = "計画全体" if row.get("scope") == "plan" else "レーン " + "・".join(row.get("lanes") or [])
    act = "止めた" if row.get("change") == "on" else "解いた"
    return f"答え待ちの停止: {where}を{act}（{row.get('count')} 件 / 閾値 {row.get('limit')}）。{row.get('reason')}"


def write_state(main: Path, name: str, data: dict, changes: list[dict]) -> dict:
    """止める指示のファイルと履歴を書き、最後の行の番号と 1 行を返す。停止の範囲が空ならファイルを消す。書けなければ OSError。"""
    at = _stamp()
    if data["plan"] or data["lanes"]:
        jsonio.write_atomic(_plan_root(main, name) / HOLD_FILE, {**data, "at": at})
    else:
        (_plan_root(main, name) / HOLD_FILE).unlink(missing_ok=True)
    start = max([int(r.get("n") or 0) for r in read_log(main, name)], default=0)
    rows = [{"n": start + i, **c, "at": at} for i, c in enumerate(changes, 1)]
    with open(_plan_root(main, name) / LOG_FILE, "a", encoding="utf-8") as f:
        f.write("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    return {"n": rows[-1]["n"], "line": change_line(rows[-1])}


def wait_changes(main: Path, name: str, after: int, event: dict) -> dict:
    """`parallel.py wait` の `changes`: 履歴の `n` が `after` より大きい全行と 1 行。履歴を読めなければイベントの最新の変化 1 件。"""
    try:
        rows = read_log(main, name)
    except OSError:
        return {"changes": [{"n": event.get("n"), "line": event.get("line")}], "changes_unread": True}
    return {"changes": [{**r, "line": change_line(r)} for r in rows if int(r.get("n") or 0) > after]}


def render_hold(hold: Optional[dict]) -> list[str]:
    """`plan.md` の答え待ちの停止の節。停止が無ければ空。"""
    if not hold or not (hold.get("plan") or hold.get("lanes")):
        return []
    limits = hold.get("limits") or {}
    lines = ["", "## 答え待ちの停止", ""]
    if hold.get("plan"):
        lines += [
            f"計画全体を止めている（答え待ちの依頼 {hold.get('count')} 件、計画の閾値 {limits.get('plan')}）。答えて閾値以下へ戻ると再開する",
            "",
        ]
    if hold.get("lanes"):
        lines += ["| レーン | 答え待ちの依頼 | 理由 |", "| --- | --- | --- |"]
        by_lane = hold.get("by_lane") or {}
        lines += [f"| {n} | {by_lane.get(n, 0)} | レーンの閾値 {limits.get('lane')} を超えた |" for n in hold["lanes"]]
    return lines


# ---- 再開の印 ----


def _mark_dir(main: Path, name: str) -> Path:
    return _plan_root(main, name) / RESUME_DIR


def place_mark(main: Path, name: str, lane: str, why: str) -> dict:
    """再開の印を置く。`why` は boundary（区切りで止まった）か answered（停止の間に答えを受けた持ち主）。"""
    _mark_dir(main, name).mkdir(parents=True, exist_ok=True)
    mark = {"lane": lane, "why": why, "at": _stamp()}
    jsonio.write_atomic(_mark_dir(main, name) / f"{lane}.mark.json", mark)
    return mark


def read_marks(main: Path, name: str) -> dict[str, dict]:
    """レーン名 → 再開の印。"""
    d = _mark_dir(main, name)
    got = [jsonio.read(p, missing=None, broken=None, want=dict) for p in sorted(d.glob("*.mark.json"))] if d.is_dir() else []
    return {m["lane"]: m for m in got if m and m.get("lane")}


def drop_mark(main: Path, name: str, lane: str) -> bool:
    """再開の印を消す。消したら True、無ければ False。"""
    p = _mark_dir(main, name) / f"{lane}.mark.json"
    there = p.exists()
    p.unlink(missing_ok=True)
    return there


def clear_plan(main: Path, name: str) -> None:
    """計画の終わりに、止める指示のファイルと再開の印を消す（履歴は残す）。"""
    (_plan_root(main, name) / HOLD_FILE).unlink(missing_ok=True)
    shutil.rmtree(_mark_dir(main, name), ignore_errors=True)


def resume_marks(main: Path, name: str, lanes: list[str], drop: list[str], relay: Callable) -> dict:
    """`lanes` を `relay.py restart` で起こし、`drop` の再開の印を消す。起こす前に印を読み直し、人が消したレーンは起こさない。"""
    out: dict = {"restarted": [], "refused": {}, "gone": []}
    for n in lanes:
        if n not in read_marks(main, name):
            out["gone"].append(n)
            continue
        code, res = relay(main, "restart", n, "--json")
        if code == 0:
            drop_mark(main, name, n)
            out["restarted"].append(n)
        else:
            out["refused"][n] = ((res.get("items") or [{}])[0] or {}).get("result") or res.get("summary") or f"exit {code}"
    for n in drop:
        drop_mark(main, name, n)
        out["gone"].append(n)
    return out


# ---- レーンの側 ----


def lane_input(main: Path, name: str, lane: str) -> Optional[dict]:
    """計画 `name` のレーン `lane` の入力（`{kind, issues, ...}`）。レーンでなければ None。"""
    plan = jsonio.read(_plan_root(main, name) / "plan.json", missing={}, broken={}, want=dict)
    found = [ln for ln in plan.get("lanes") or [] if isinstance(ln, dict) and ln.get("name") == lane]
    return (found[0].get("input") if isinstance(found[0].get("input"), dict) else {}) if found else None


def _lane_kind(main: Path, name: str, lane: str) -> Optional[str]:
    """計画 `name` のレーン `lane` の入力の種類（loop / sprint）。レーンでなければ None。"""
    got = lane_input(main, name, lane)
    return (got.get("kind") or "") if got is not None else None


def plan_held(main: Path, name: str, hold: Optional[dict], lane: Optional[str]) -> bool:
    """計画 `name` の停止の範囲にある計画のレーンか。計画に載らないレーン（リリースの一時レーン）は停止の範囲に入れない。
    耐久ワークフローの再開の印の突き合わせ（`govern_hold`）が計画のレーンだけを扱うため、印を置いても起こされない。"""
    return covered(hold, lane) and _lane_kind(main, name, lane) is not None


def lane_hold(main: Path, lane: Optional[str], kind: str) -> Optional[dict]:
    """レーン `lane`（入力の種類 `kind`）を覆う答え待ちの停止 `{plan, scope, count, limit}`。ファイルだけで見る。
    別のリポジトリのレーンも数えるよう、計画の所在の一覧の置き場も探す（計画の置き場が自分のリポジトリと違えば `home` を足す。#2733）。"""
    if not lane:
        return None
    for home, name in pf.lane_homes_in(main, lane, None):
        hold = read_hold(home, name)
        if covered(hold, lane) and _lane_kind(home, name, lane) == kind:
            scope = "plan" if hold.get("plan") is True else "lane"
            count = hold.get("count") if scope == "plan" else (hold.get("by_lane") or {}).get(lane)
            limit = (hold.get("limits") or {}).get(scope)
            other = {"home": str(home)} if pf.resolved_path(home) != pf.resolved_path(Path(main)) else {}  # 別のリポジトリのレーンだけ
            return {"plan": name, "scope": scope, "count": count, "limit": limit, **other}
    return None


def loop_stop(main: Path) -> Optional[dict]:
    """ループのレーンのループの 1 回の境の判定。自分が停止の範囲にあれば止まる理由を返す。"""
    got = lane_hold(main, os.environ.get("NDF_RELAY_NAME"), "loop")
    return {"condition": CONDITION, "values": got} if got else None


def self_stop(main: Path, stop: dict) -> Optional[str]:
    """止まったレーンが自分のラッパーを止める 1 行。答え待ちの停止では、止まる前に再開の印を置く。"""
    if stop["condition"] == CONDITION and (lane := os.environ.get("NDF_RELAY_NAME")):
        place_mark(Path(stop["values"].get("home") or main), stop["values"]["plan"], lane, "boundary")
    return pf.stop_self(main) if stop["condition"] in WAIT_STOPS else None


def needs_approval(plan: dict, start: Optional[str] = None) -> bool:
    """新しい承認を生むプランか: 開始のステップ（無ければ最初）から設計を書くステップへ行けるか（#3000 の決定 2。`フェーズ` では決めない）。"""
    from supervise_lib import limits

    return any(s.get("type") == "work" and s.get("kind") == DESIGN_KIND for s in limits.reachable(plan, start))


def plan_hold(lane: Optional[str], plan: Optional[dict] = None, start: Optional[str] = None) -> tuple[Optional[str], Optional[dict]]:
    """キューから流す 1 スプリントのレーンのプランの (流さない理由, 範囲の中で流すときの範囲の値 = `lane_hold` + `lane`)。範囲の外なら
    (None, None)。範囲の中では新しい承認を生むプラン（プランが無ければそう読む）だけを流さない（#3000）。"""
    import decl

    got = lane_hold(decl.main_dir(os.getcwd()) or Path.cwd(), lane, "sprint") if lane else None
    if not got:
        return None, None
    if plan is None or needs_approval(plan, start):
        return f"{SKIP_REASON}: {got['plan']} {lane}", None
    return None, {**got, "lane": lane}


def plan_skip(lane: Optional[str], plan: Optional[dict] = None, start: Optional[str] = None) -> Optional[str]:
    """キューから流す 1 スプリントのレーンのプランを流さない理由。流すなら None。"""
    return plan_hold(lane, plan, start)[0]


def note_ran(state_dir: Path, ran: Optional[dict]) -> None:
    """範囲の中で流したプランの範囲の値をプランの状態ディレクトリへ書き、範囲の外なら消す（落ちた後の続きでも読めるようファイルで持つ）。"""
    if ran:
        jsonio.write_atomic(Path(state_dir) / RAN_FILE, ran)
    else:
        (Path(state_dir) / RAN_FILE).unlink(missing_ok=True)


def read_ran(state_dir: Path) -> Optional[dict]:
    """`note_ran` の値（キューの結果の `answer_hold_ran`）。無い・読めなければ None。"""
    return jsonio.read(Path(state_dir) / RAN_FILE, missing=None, broken=None, want=dict)


def skip_of(reason: Optional[str]) -> Optional[dict]:
    """`plan_skip` の理由から `{plan, lane}` を読む。別の理由なら None。"""
    head, _, rest = str(reason or "").partition(": ")
    words = rest.split()
    return {"plan": words[0], "lane": words[1]} if head == SKIP_REASON and len(words) == 2 else None


# ---- 耐久ワークフローの突き合わせ ----


class AnswerHold:
    """`_Run` が継ぐ。耐久ワークフローの 1 周の終わりに、問いの一覧・閾値・再開の印を突き合わせる。

    `_Run` は耐久ステップ `write_hold`・`resume_lanes` と、`questions`・`lanes`・`wake`・`revive` などの値を持つ。"""

    def init_answer_hold(self) -> None:
        self.hold = Hold()
        self.marks: dict[str, dict] = {}  # 最後の観測で読んだ再開の印
        self.seen_stopped: dict[str, str] = {}  # 止まったと観たレーン → そのときの再開の印の `at`
        self.left_stopped: set[str] = set()  # 人が `park --clear` で止めたままにしたレーン
        self.superseded: set[str] = set()  # 答えた設計 PR の古い依頼の id。次の突き合わせで一覧から外す
        self.holds = 0  # 止めた回数

    def clear_revived(self, rows: dict) -> None:
        """起こしたレーンの最後に動いた時刻（`relay.py ls` の `at`）が、答えたときの観測の時刻より後へ進んだら `revive` から外す。
        答えを当ててすぐ止まるレーンは観測の間隔の中で stopped → running → stopped と動き、状態の変化として見えないためである。"""
        import clock

        for n in list(self.revive):
            at = (rows.get(n) or {}).get("at")
            t = clock.parse(at, naive="utc") if at else None
            if t is not None and t.timestamp() > self.revive_at.get(n, float("inf")):
                self.revive = [x for x in self.revive if x != n]
                self.revive_at.pop(n, None)

    def keep_stopped(self, lane: Optional[str]) -> None:
        """`park --clear` のメッセージを受ける。再起動の対象と起こした後の待ちから外す。"""
        if lane not in self.lanes:
            return
        self.left_stopped.add(lane)
        self.wake = [n for n in self.wake if n != lane]
        self.revive = [n for n in self.revive if n != lane]
        self.revive_at.pop(lane, None)

    def note_answered(self, q: dict) -> None:
        """答えた依頼の持ち主を止めたままの控えから外し、同じレーン・同じ設計 PR の古い依頼の id を控える。"""
        self.left_stopped -= _senders(q)
        if q.get("kind") != "gate1" or not q.get("pr"):
            return
        older = self.questions[: self.questions.index(q)]  # 依頼の `at` は秒までなので、新旧は届いた順で決める
        same = lambda o: o.get("kind") == "gate1" and (o.get("lane"), o.get("pr")) == (q.get("lane"), q.get("pr"))  # noqa: E731
        self.superseded |= {o["id"] for o in older if same(o)}

    def govern_hold(self, seen: dict) -> None:
        """観測の後の突き合わせ。閾値の載らない古い観測の記録の再生では何もしない（記録の順を変えないため）。"""
        if seen.get("limits") is None:
            return
        shown = [] if self.launching else self.legacy_wake_lanes()
        self._retire_superseded()
        self._update_hold(Limits(**seen["limits"]))
        if self.ls_ok:
            self._reconcile({n: m for n, m in (seen.get("marks") or {}).items() if n in self.lanes})
        if not self.launching and (now := self.legacy_wake_lanes()) and set(now) - set(shown):
            self.pause("notice", lanes=[], attach=[])  # 古い記録: 起こす 1 行（wake）が増えたらオーガナイザーへ知らせる

    def restore_hold(self, limits: Optional[dict], written: Optional[dict] = None) -> None:
        """作り直しの直後に、戻した問いと閾値から停止の範囲を戻す。閾値が無い（台帳を読まない途中の記録・閾値を載せない
        古い作り直しの記録）なら何もしない。`written` は作り直しで読んだ止める指示のファイル（`{"hold": 中身か None}`）。
        失われている間に答えて範囲が変わったら、ファイルの範囲から突き合わせて書き直す（レーンが解けた停止を読み続けない）。
        `written` の無い古い作り直しの記録では書かない（記録の順を変えないため）。"""
        if not limits:
            return
        if written is None:
            self.hold = decide_hold(self.questions, self.order, Limits(**limits))
            return
        self.hold = hold_of(written.get("hold"))
        self._update_hold(Limits(**limits))

    def _retire_superseded(self) -> None:
        gone, self.superseded = self.superseded, set()
        if any(q.get("id") in gone for q in self.questions):
            self.questions = [q for q in self.questions if q.get("id") not in gone]
            self.answered |= gone  # 再送で一覧へ戻さない。答えのイベントは立てない（持ち主は新しい依頼の答えで進む）
            self.publish_questions()

    def _update_hold(self, limits: Limits) -> None:
        now = decide_hold(self.questions, self.order, limits)
        if now.scope() == self.hold.scope():
            self.hold = now
            return
        changes = now.diff(self.hold, limits)
        got = self.write_hold(self.main, self.name, now.data(limits), changes, {**self.progress(), "hold": now.data(limits)})
        if not got.get("ok"):
            print(f"parallel: 答え待ちの停止を書けない。次の周で書き直す: {got.get('error')}", file=sys.stderr, flush=True)
            return
        self.hold = now
        self.holds += sum(c["change"] == "on" for c in changes)
        self.emit_hold({"seq": self.bump(), "action": CONDITION, "n": got["n"], "line": got["line"], "hold": now.data(limits)})

    def _reconcile(self, marks: dict[str, dict]) -> None:
        """再開の印を分ける。止まったという観測はそのとき観た印 1 つにだけ効かせ、計画全体の停止の間は再開も消去もしない。"""
        self.marks = marks
        stopped = self.stopped()
        self.left_stopped &= stopped
        self.seen_stopped = {n: at for n, at in self.seen_stopped.items() if (marks.get(n) or {}).get("at") == at}
        self.seen_stopped.update({n: marks[n].get("at") for n in marks if n in stopped})
        if self.hold.plan or not marks:
            return
        gave_up = {n for n in marks if self.lanes[n].get("given_up")}
        drop = sorted(gave_up | {n for n in marks if n not in stopped and n in self.seen_stopped})
        todo = [n for n in self.order if n in marks and n in stopped and n not in gave_up and not self.hold.covers(n)]
        if todo or drop:
            self._apply_resume(self.resume_lanes(self.main, self.name, todo, drop))

    def _apply_resume(self, got: dict) -> None:
        for n in [*got["restarted"], *got["gone"]]:
            self.marks.pop(n, None)
            self.seen_stopped.pop(n, None)
        for n in got["restarted"]:
            self.lanes[n]["state"] = None  # 次の観測で動いて見えたら待ちを外す。古い観測の stopped で起こす 1 行を出し直さない
            self.revive = sorted({*self.revive, n})
            self.revive_at[n] = self.seen_at
            self.wake = [x for x in self.wake if x != n]
        for n, why in got["refused"].items():
            self.count_refusal(n, why)
