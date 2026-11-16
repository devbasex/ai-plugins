"""起動の耐久ステップと起動の割り振り（#2936）: 一時レーンの起動と止まったレーンの再起動を耐久ワークフローが打つ。

- 耐久ステップ `parallel.launch`（`step_launch`）: 起動か再起動を 1 回打ち、立ち上がりを確かめる。立ち上がらなければ 1 回だけ
  再試行する。打つ前の一覧で動いていれば打たない。失敗は例外にせず結果（`up` / `failed`）で返す（設計の決定 2）
- `LaunchRouting`（`parallel_run._Run` が継ぐ）: 起動の目的ごとに耐久ステップを 1 回だけ呼び、結果を起動の記録（`launches`）へ残し、
  2 回とも立ち上がらなければ区分 `spawn` の問いを立てる。問いの答えの「起こし直す」は、答えた問いのキーを添えた新しい目的で流す（I2）
- 一時レーンの空き待ち（#3597 の決定 10）: 別の一時レーンが動いているための断り（断りの項目に `busy` がある）は 2 回目を打たず、
  起動の記録を `waiting` にする。ループの 1 回の観測ごとに読み直し、待つ相手が止まって観えたら打ち直し、上限を過ぎたら問う

耐久ワークフローの中の分岐は耐久ステップの結果だけで決める（`parallel_run.py` の決まり）。
"""

from __future__ import annotations

import os
import re
import time
from pathlib import Path
from typing import Optional

import durable
import limits
import parallel_release as pr
from parallel_files import RELAY
from parallel_helper import spawn_line
from parallel_repo import relay, relay_proc

UP = ("running", "awaiting_approval")
LAUNCH_SECONDS = float(limits.seconds("parallel-launch.up"))  # 立ち上がりを待つ上限の既定（設計の決定 3）
INTERVAL = float(limits.value("parallel-launch.up", "interval"))  # 一覧を読む間隔
HELPER_WAIT = float(limits.seconds("parallel-launch.helper-wait"))  # 一時レーンの空き待ちの上限の既定（#3597 の決定 10）
RESTART = ("起こし直す", "続きから起こし直す")
OUTCOME_KEYS = ("result", "attempts", "at", "reason")  # 起動の結果（`step_launch` の値）から起動の記録へ写す鍵


def _env_seconds(raw: Optional[str], default: float) -> float:
    """環境変数の値 `raw` を秒として読む。負は 0、空か読めなければ `default`。読む環境変数の名前は呼び手が字句で書く
    （契約のチェックが名前を拾えるようにする。`check-ndf-contracts.py`）。"""
    try:
        return max(0.0, float(raw or default))
    except ValueError:
        return default


def launch_seconds() -> float:
    """立ち上がりを待つ上限の秒（`NDF_PARALLEL_LAUNCH_SECONDS` で変えられる。既定 120 秒）。"""
    return _env_seconds(os.environ.get("NDF_PARALLEL_LAUNCH_SECONDS"), LAUNCH_SECONDS)


def helper_wait_seconds() -> float:
    """一時レーンの空き待ちの上限の秒（`NDF_PARALLEL_HELPER_WAIT_SECONDS` で変えられる。既定 3,600 秒）。"""
    return _env_seconds(os.environ.get("NDF_PARALLEL_HELPER_WAIT_SECONDS"), HELPER_WAIT)


def _launch_outcome(got: dict) -> dict:
    """起動の結果 `got` のうち、起動の記録へ写す値（`OUTCOME_KEYS`）。"""
    return {k: got.get(k) for k in OUTCOME_KEYS}


def _launch_stamp(t: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(t))


def _ls_row(main: Path, lane: str) -> Optional[dict]:
    """一覧のそのレーンの行（無ければ空の dict）。一覧が失敗したら None。"""
    got, res = relay_proc(main, "ls", "--json")
    if got.returncode != 0:
        return None
    return next((it for it in res.get("items") or [] if isinstance(it, dict) and it.get("name") == lane), {})


def _moved_since(row: dict, since: float) -> bool:
    """行の最後に動いた時刻（`at`。ミリ秒まで）が `since`（打った時刻）より後か。時計の端数の差に 0.5 秒の幅を置く。"""
    import clock

    at = clock.parse(row.get("at"), naive="utc") if row.get("at") else None
    return at is not None and at.timestamp() >= since - 0.5


def came_up(row: dict, since: float, fresh: bool) -> bool:
    """立ち上がったと読むか。`fresh`（動いて観えるまま止まったレーンの再起動）は `at` が打った後へ進んだときだけ。"""
    if fresh:
        return _moved_since(row, since)
    return row.get("state") in UP or (row.get("state") == "stopped" and _moved_since(row, since))


def _hit_once(main: Path, where: Path, argv: list[str], lane: str, fresh: bool) -> tuple[bool, float, str, str]:
    """1 回打って立ち上がりを確かめる。(立ち上がったか, 打った時刻, 立ち上がらなかった理由, 断りの元の一時レーンの名前)。
    断りの項目に `busy`（別の一時レーンが動いている）があれば、立ち上がりを待たずにその名前を返す（#3597 の決定 9）。"""
    import waits

    t0 = time.time()
    code, res = relay(where, *argv)
    item = (res.get("items") or [{}])[0] or {}
    why = item.get("result")
    if code != 0 and why != "name-taken":  # 名前の重なりは動いている同じ名前があるため、確かめに進む
        return False, t0, f"断られた（{why or res.get('summary') or f'exit {code}'}）", str(item.get("busy") or "")
    limit = launch_seconds()
    policy = limits.policy("parallel-launch.up")._replace(seconds=limit, interval=min(INTERVAL, max(limit / 10, 0.05)))
    got = waits.wait_until(lambda: _ls_row(main, lane), lambda row: row is not None and came_up(row, t0, fresh), policy=policy)
    return got.done, t0, "" if got.done else f"{limit:g} 秒のうちに一覧で動いて観えない", ""


@durable.step(name="parallel.launch")
def step_launch(main: str, argv: list[str], lane: str, cwd: Optional[str] = None, fresh: bool = False) -> dict:
    """`relay.py <argv>` を打って立ち上がりを確かめる。最大 2 回打つ。`cwd` は打つ場所（別のリポジトリの一時レーン）。
    返す値: `result`（up / failed / waiting）・`attempts`（打った回数。打つ前に動いていれば 0）・`at`（最後に打った時刻）・`reason`。
    別の一時レーンが動いているための断りは 2 回目を打たず、`waiting` に `busy`（動いている名前）と `limit`（空き待ちの上限の秒）を
    添えて返す（#3597 の決定 10）。上限は結果に記録し、打ち直しで環境変数を読み直さない。"""
    if not fresh and (row := _ls_row(Path(main), lane)) and row.get("state") in UP:
        return {"result": "up", "attempts": 0, "at": _launch_stamp(time.time()), "reason": "打つ前から動いていた"}
    t0, reason = time.time(), ""
    for attempt in (1, 2):
        up, t0, reason, busy = _hit_once(Path(main), Path(cwd) if cwd else Path(main), list(argv), lane, fresh)
        if up:
            return {"result": "up", "attempts": attempt, "at": _launch_stamp(t0), "reason": None}
        if busy:
            return {"result": "waiting", "attempts": attempt, "at": _launch_stamp(t0), "reason": reason, "busy": busy,
                    "limit": helper_wait_seconds()}  # fmt: skip
    return {"result": "failed", "attempts": 2, "at": _launch_stamp(t0), "reason": reason}


class LaunchRouting:
    """`_Run` が継ぐ。`_Run` は `main`・`name`・`launch`・`current`・`launches`・`helper_seen`・`launching`・`wake`・`woken`・`wake_tries`・`lanes`・`sprint`・
    `left_stopped`・`hold`・`marks`・`ask`・`stopped` を持つ。"""

    sprint_hold_wake = False  # 範囲の中の 1 スプリントのレーンを再開の印の有無で打つか。版の印の後の記録だけ True（#3000）

    def launch_once(self, purpose: str, lane: str, argv: list[str], cwd: Optional[str] = None, fresh: bool = False, **extra) -> dict:
        """起動の目的 `purpose` を 1 回だけ流す。記録のある目的は打ち直さない（`failed` でも。I3）。2 回とも立ち上がらなければ
        区分 `spawn` の問いを立てる。`extra` は問いへ添える値（リリースの連番 `n` など）。古い記録の打ち直しでは何もしない。
        別の一時レーンが動いているための断り（`waiting`）は問いにせず、起動の記録に残して `launch_waiting` が読み直す（#3597）。"""
        if not self.launching:
            return {}
        if purpose in self.launches:
            return self.launches[purpose]
        got = step_launch(self.main, list(argv), lane, cwd, fresh)
        rec = {"lane": lane, **_launch_outcome(got)}
        self.launches[purpose] = rec
        self.helper_seen.pop(lane, None)  # 止まりの観測は今の起動から数え直す（I4）
        spec = {"argv": list(argv), "cwd": cwd, "fresh": fresh}
        if rec["result"] == "waiting":
            rec.update(since=got.get("at"), busy=got.get("busy"), limit=got.get("limit"), launch=spec, extra=dict(extra))
        elif rec["result"] != "up":
            self._ask_not_up(purpose, lane, rec.get("reason"), spec, extra)
        return rec

    def _ask_not_up(self, purpose: str, lane: str, reason: Optional[str], spec: dict, extra: dict) -> None:
        """2 回起動しても立ち上がらないレーンの区分 `spawn` の問いを立てる（`reason` は立ち上がらなかった理由）。"""
        self._ask_launch(purpose, lane, f"レーン {lane} を 2 回起動しても立ち上がらない（{reason}）", spec, extra)

    def _ask_launch(self, purpose: str, lane: str, text: str, spec: dict, extra: dict) -> None:
        """起動できないレーンの区分 `spawn` の問い（選択肢「起こし直す」「止める」）を立てる。同じ id は 1 回だけ立つ。"""
        self.ask(
            "spawn", f"{self.name}-launch-{purpose}", lane, text, list(RESTART[:1]) + ["止める"], purpose=purpose, launch=spec, **extra
        )

    def launch_waiting(self, rows: dict) -> None:
        """空き待ち（`waiting`）の起動の記録を、ループの 1 回の観測（一覧の成功した行 `rows`）ごとに読み直す（#3597 の決定 10）。
        待つ相手が一覧で動いて観えなければ打ち直し、動いていて上限を過ぎていれば `failed` にして区分 `spawn` の問いを立てる。
        眠らない（I6）。古い記録の打ち直しでは何もしない。"""
        if not self.launching:
            return
        for purpose, rec in list(self.launches.items()):
            if rec.get("result") == "waiting":
                self._launch_waiting_one(purpose, rec, rows)

    def _launch_waiting_one(self, purpose: str, rec: dict, rows: dict) -> None:
        """空き待ちの起動の記録 `rec` を 1 件読み直す。待つ相手が動いて観えれば時間切れだけを扱い、観えなければ打ち直す。
        待ちの間に、記録が添えたリリース（`n`）か版の要るまとめ（`verify`）が締められていれば、打たずに `failed` にする（問わない）。"""
        import clock

        busy, spec, extra = str(rec.get("busy") or ""), rec.get("launch") or {}, dict(rec.get("extra") or {})
        if self._waiting_stale(extra):
            rec.update(result="failed", reason="待ちの間にリリースが締められた")
            return
        since = clock.parse(rec.get("since"), naive="utc") if rec.get("since") else None
        waited = max(0.0, self.seen_at - since.timestamp()) if since else 0.0
        if (rows.get(busy) or {}).get("state") in UP:
            if waited >= float(rec.get("limit") or 0):
                rec.update(result="failed", reason=f"一時レーンは同時に 1 本まで: {busy} が動いたまま {waited:g} 秒待った")
                text = f"レーン {rec['lane']} を起動できない（一時レーンは同時に 1 本まで。{busy} が {waited:g} 秒のあいだ動いている）"
                self._ask_launch(purpose, rec["lane"], text, spec, extra)
            return
        got = step_launch(self.main, list(spec.get("argv") or []), rec["lane"], spec.get("cwd"), bool(spec.get("fresh")))
        self.helper_seen.pop(rec["lane"], None)
        if got.get("result") == "waiting":
            rec["busy"] = got.get("busy")  # 別の一時レーンが先に立ち上がった: 最初に断られた時刻のまま待ちを続ける
        elif got.get("result") == "up":
            self.launches[purpose] = {"lane": rec["lane"], **_launch_outcome(got), "waited": {"busy": busy, "seconds": waited}}
        else:
            rec.update(**_launch_outcome(got))
            self._ask_not_up(purpose, rec["lane"], rec.get("reason"), spec, extra)

    def _waiting_stale(self, extra: dict) -> bool:
        """空き待ちの記録の添え（`extra`）が、もう流れていないリリース（`n` が今のリリースの連番と違う）か版の要るまとめ
        （`verify` が流している待ちの依頼と違う）を指すか。打ち直すと、締めた後のリリースの一時レーンが本番のプランを流しうる。"""
        if extra.get("n") is not None and (self.current is None or self.current.get("n") != extra["n"]):
            return True
        return extra.get("verify") is not None and getattr(self, "verifying", None) != extra["verify"]

    def launch_helper(self) -> None:
        """流れているリリースの最後の起動（`launch`）の一時レーンを起動する。立ち上がらなければ問いに `n` を添える（「止める」で締める）。"""
        got = self.launch or {}
        if got.get("purpose") and self.current is not None:
            self.launch_once(got["purpose"], pr.helper_name(self.name, got["n"]), got["argv"], got.get("cwd"), n=got["n"])

    def relaunch(self, q: dict, choice: list[str]) -> bool:
        """耐久ワークフローが立てた問いの「起こし直す」「続きから起こし直す」を、答えた問いのキーを添えた新しい目的で流す（I2）。
        区分 `stall` は動いて観えるまま止まったレーンが対象のため、一覧を見ずに再起動を打つ（I3）。流したら True。"""
        if not set(RESTART) & set(choice):
            return False
        kind, qid, lane = q.get("kind"), q.get("id"), q.get("lane")
        if kind == "stall" and lane:
            self.launch_once(f"ans-{lane}-{qid}", lane, ["restart", lane], fresh=True)
            return True
        spec = q.get("launch") or {}
        if kind in ("spawn", "helper") and q.get("purpose") and spec.get("argv"):
            extra = {k: q[k] for k in ("n", "verify") if q.get(k) is not None}  # 版の要るまとめの依頼も引き継ぐ（#3058）
            purpose = f"{q['purpose']}-a{qid}"
            self.wake_tries.pop(lane, None)  # 人が起こし直すと答えた: 再起動の回数を数え直す
            if self.launch and self.launch.get("purpose") == q["purpose"]:
                # 流れているリリースの一時レーンを起こし直した: 止まりの見張り（`_ask_helper`）を新しい起動へ向け、もう一度問えるようにする
                self.launch = {**self.launch, "purpose": purpose, "retry": int(self.launch.get("retry") or 0) + 1}
                self.helper_asked = None
            self.launch_once(purpose, lane, spec["argv"], spec.get("cwd"), bool(spec.get("fresh")), **extra)
            return True
        return False  # 起動の断り（区分 spawn の目的の無い問い）は、起動の条件がそろうたびに耐久ワークフローが打ち直す

    def launch_wakes(self) -> None:
        """再起動の対象（`wake`）のうち止まっているレーンを再起動する。止まっていないレーンは対象から外す。人が止めたままにした
        レーンと、再起動の失敗の問いへ「止める」と答えたレーン（`given_up`）と、答え待ちの停止の範囲にあり再開の印を持つ 1 スプリントの
        レーン（停止が解けたら印で起こす）は打たない。印を持たない範囲の中の 1 スプリントのレーンは、答えの受け付けが承認の要らない
        プランがあると決めたレーンで、打つ（#3000 の決定 4）。版の印 `parallel-sprint-hold-wake` の前の記録では、範囲の中の
        1 スプリントのレーンをすべて打たない。"""
        if not self.launching:
            return
        stopped = self.stopped()
        self.wake = [n for n in self.wake if n in stopped]
        for n in list(self.wake):
            if n in self.left_stopped or self.lanes[n].get("given_up") or self.sprint_held(n):
                continue
            k = 1 + sum(1 for p in self.launches if re.fullmatch(rf"wake-{re.escape(n)}-\d+", p))
            rec = self.launch_once(f"wake-{n}-{k}", n, ["restart", n])
            self.wake = [x for x in self.wake if x != n]
            if rec.get("result") == "up":
                self.lanes[n]["state"] = None  # 次の観測で読み直す。古い観測の stopped で打ち直さない
                self.woken.add(n)
                self.wake_tries[n] = self.wake_tries.get(n, 0) + 1

    def sprint_held(self, n: str) -> bool:
        """答え待ちの停止の範囲にあり、再起動を打たない 1 スプリントのレーンか。"""
        return self.hold.covers(n) and n in self.sprint and (not self.sprint_hold_wake or n in self.marks)

    def wake_or_ask(self, designs: set[str]) -> list[str]:
        """答えを当てていない持ち主（`designs`）のうち、再起動の対象へ戻すレーンを返す。答えを当てないまま 2 回再起動したレーンは
        戻さずに区分 `spawn` の問いを立てる（答えが来るまで再起動を打ち続けない。「止める」なら `given_up` で外れる）。"""
        again = []
        for n in sorted(designs):
            if self.wake_tries.get(n, 0) < 2:
                again.append(n)
                continue
            pattern = rf"wake-{re.escape(n)}-\d+"
            last = f"wake-{n}-{sum(1 for p in self.launches if re.fullmatch(pattern, p))}"
            text = f"レーン {n} を設計の答えを当てるために {self.wake_tries[n]} 回再起動したが、答えを当てずに止まった"
            spec = {"argv": ["restart", n], "cwd": None, "fresh": False}
            self.ask("spawn", f"{self.name}-launch-{last}", n, text, list(RESTART[:1]) + ["止める"], purpose=last, launch=spec)
        return again

    def release_woken(self, rows: dict) -> None:
        """設計の答えを当てるために再起動したレーン（`woken`）が、今の再起動より後に止まって観えたら `woken` から外す。
        動いて観える前に止まったレーンを、答えが未適用のまま待ち続けないためである。外したレーンは次の周回で、答えがまだ当たって
        いなければ `_finish_or_hold` が `wake_or_ask` で再起動の対象へ戻すか問う。再起動の回数は動いて観えても数え直さない
        （動いた後に答えを当てずに止まるレーンを打ち続けない）。数え直すのは答えが当たったとき（`_finish_or_hold`）である。"""
        import clock

        for n in sorted(self.woken):
            row = rows.get(n)
            if row is not None and row.get("state") != "stopped":
                self.woken.discard(n)  # 動いて観えたら設計の答えの再起動を解く
                continue
            done = [p for p in self.launches if re.fullmatch(rf"wake-{re.escape(n)}-\d+", p)]
            last = max(done, key=lambda p: int(p.rsplit("-", 1)[1]), default=None)
            since = clock.parse((self.launches.get(last or "") or {}).get("at"), naive="utc") if last else None
            if row is not None and (since is None or not _moved_since(row, since.timestamp())):
                continue  # 今の再起動より前の行: まだ動いて観えていない
            self.woken.discard(n)

    # -- 起動の印（`parallel-launch`）の無い古い記録の道: 起動と再起動はオーガナイザーが 1 行で打つ（#2936 の前の道） --

    def legacy_spawn(self, fields: dict) -> dict:
        """古い記録なら、知らせ `pause` の起動の入力へオーガナイザーが打つ 1 行（`spawn`）を添える。"""
        return {**fields, "spawn": spawn_line(fields)} if not self.launching and fields.get("argv") else fields

    def legacy_wake(self) -> dict:
        """古い記録なら、止まっているレーンを起こす 1 行（`wake`）を知らせへ添える。"""
        if self.launching:
            return {}
        stopped = self.stopped()
        self.wake = [n for n in self.wake if n in stopped]
        lanes = self.legacy_wake_lanes()
        return {"wake": [f"python3 {RELAY} restart {n}" for n in lanes]} if lanes else {}

    def legacy_wake_lanes(self) -> list[str]:
        return [n for n in self.wake if not self.hold.covers(n) and n not in self.left_stopped]

    def legacy_commands(self, commands: dict) -> dict:
        """古い記録なら、問いの答えごとにオーガナイザーが打つ 1 行（`commands`。answer が `metrics.next` に返す）を添える。"""
        return {} if self.launching else {"commands": commands}

    def legacy_mark(self) -> dict:
        """古い記録なら、問いの一覧の知らせへ印を添える。answer は `launching` で持ち主を起こす 1 行を返すかを、`sprint_hold_wake` で
        範囲の中の 1 スプリントのレーンへ再開の印を置かずに済ませてよいか（版の印 `parallel-sprint-hold-wake` の後の記録か。#3000）を読む。"""
        return {**({} if self.launching else {"launching": False}), **({} if self.sprint_hold_wake else {"sprint_hold_wake": False})}
