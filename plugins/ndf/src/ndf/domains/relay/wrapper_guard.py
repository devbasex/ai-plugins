"""ラッパーの見回りの替えと上限の後の替え先（#3604 #3864 の決定 8・11、I12・I13・I14。`wrapper.py` から行数の上限のため分けた）。

`Wrapper` の基底クラス `GuardSteps` が、見回りごとに次を判断の関数へ渡す値にする。

- 上限（`_limit_pick`）: 画面の上限の行か、今の窓が 100 以上で子が静かなときに、上限の観測を 1 回書いて `after_limit` を呼ぶ
- 見回り（`_guard_pick`）: 今が使い切りの組か従量の接続のときに `guard` を呼ぶ
- 止めてよい時点（`_safe_point`）: 静かな時間・回答待ちの質問が無い・会話の記録の最後が終わった応答で、最後の入力より後

替えるときは、子を止める前に起動の要求（`Choice`）を確定して `Section.choice` に残す（`_confirm`）。先頭へ `ready` を
打ち直し、外れればその名前を除いたキャッシュで判断をやり直して子を止めない。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from ndf.domains.relay import account_decide, accounts, launch_inputs, observe, signals
from ndf.domains.relay.account_decide import Pick
from ndf.domains.relay.launch_target import Choice

Section = Any  # `wrapper.Section`（import の輪を作らないよう型の名前だけで受ける）
INSIDE = (0, 1, 2)  # 閾値の内側の組


class GuardSteps:
    """`Wrapper` の見回りの手順。属性と記録の手（`_log`・`_inputs`・`_pinned_or`・`_spins`）は `Wrapper` が持つ。"""

    s: Any
    dir: Path
    store: Path
    clock: Callable[[], float]
    wall: Any
    reader: launch_inputs.Reader
    memo: Optional[tuple[Any, Optional[Pick], Optional[Choice], str]]
    _log: Callable[..., None]
    _inputs: Callable[[float, bool], launch_inputs.Inputs]
    _pinned_or: Callable[[Optional[str], str, Optional[str], float], Optional[str]]
    _spins: Callable[[Optional[str], Optional[str]], bool]

    def _limit_pick(self, sec: Section, screen: str) -> Optional[Pick]:
        """上限の後の判断。上限を初めて見た見回りで上限の観測を書く（決定 8）。上限でなければ None。"""
        line = observe.limit_line(screen)
        now = self.wall.now()
        if line is None:
            if not self._spent_and_quiet(sec, now):
                return None
            line = ""  # 窓が 100 以上で静かな子は上限の行を持たない（窓から until を決める）
        if sec.limit_at is None:
            sec.limit_at = now
            sec.limit_lost = not self._see_limit(sec, line, now)

        def judge(inputs: launch_inputs.Inputs) -> Pick:
            rows = inputs.usage.rows if inputs.usage is not None else []
            if sec.limit_lost:  # 観測を書けなかったセグメントでは、今のアカウントを上限として扱う
                rows = [r._replace(limited=True) if r.name == sec.account else r for r in rows]
            return account_decide.after_limit(sec.account, rows, inputs.candidates(), [], self.s.guards)

        return self._confirm(sec, "limit", now, judge)

    def _spent_and_quiet(self, sec: Section, now: float) -> bool:
        """今の登録済みアカウントの窓が 100 以上（上限）で、子の出力が静かな時間止まっているか（上限の行の文言の変化に備える）。"""
        if sec.target is None or sec.target.lease is None or self.clock() - sec.last_output < self.s.quiet:
            return False
        usage = self._inputs(now, False).usage
        return any(u.name == sec.account and u.limited for u in (usage.rows if usage is not None else []))

    def _see_limit(self, sec: Section, line: str, now: float) -> bool:
        """今の登録済みアカウントの上限の観測を書く。書けた（か書かない起動先）なら真、錠を取れない・書けなければ偽。"""
        t = sec.target
        if t is None or t.account is None or t.lease is None:
            return True  # 従量の接続・既定のログインは書かない
        windows = accounts.read_windows(
            accounts.usage_path(self.s.state_dir, t.account, t.generation), accounts.fetch_path(self.store, t.account, t.generation)
        )
        tz = datetime.now().astimezone().tzinfo
        try:
            accounts.see_limit(self.s.state_dir, t.account, t.generation, now, accounts.limit_until(line, windows, now, tz))
        except OSError as exc:  # 書けなければ記録せず判断を続ける（`log.jsonl` に 1 行）
            self._log({"kind": "limit_seen", "account": t.account, "reason": f"上限の観測を書けない: {exc}"})
            return False
        return True

    def _guard_pick(self, sec: Section, now: float) -> Optional[Pick]:
        """見回りの判断。今が使い切りの組か従量の接続のときだけ候補を読み、`guard` を呼ぶ（決定 11・I12）。"""
        usage = self._inputs(now, False).usage
        mine = next((u for u in (usage.rows if usage is not None else []) if u.name == sec.account), None)
        if not account_decide.metered(sec.account) and (mine is None or account_decide.group(mine, self.s.guards) != 3):
            return None

        def judge(inputs: launch_inputs.Inputs) -> Pick:
            rows = inputs.usage.rows if inputs.usage is not None else []
            return account_decide.guard(sec.account, rows, inputs.candidates(), self.s.guards)

        return self._confirm(sec, "guard", now, judge)

    def _safe_point(self, sec: Section, idle: float) -> bool:
        """止めてよい時点か（I13）。静かな時間・回答待ちの質問が無い・最後の記録が終わった応答で、最後の入力より後。"""
        if idle < self.s.quiet or signals.question_present(self.dir):
            return False
        transcript = observe.transcript_path(self.s.environ, self.s.cwd, sec.session_id)
        got = observe.turn_done(transcript, self.s.transcript_bytes)
        if got is None or not got[0] or got[1] is None:
            return False
        return sec.input_at is None or got[1] > sec.input_at

    def _confirm(self, sec: Section, reason: str, now: float, judge: Callable[[launch_inputs.Inputs], Pick]) -> Pick:
        """替えるなら起動の要求を確定して `Section.choice` に残し、返す判断の替え先をその先頭にする。

        要求の先頭へ `ready` を打ち直し、外れればその名前を除いたキャッシュで判断をやり直す（子を止めない）。入力のスタンプが変わらず
        `valid_until` の前なら、前の判断を返す（見回りごとに `ready` を打たない）。
        """
        while True:
            inputs = self._inputs(now, True)
            key = (sec.launch, reason, _stamp(inputs), launch_inputs.stamp(accounts.pin_path(self.s.state_dir)))
            if self.memo is not None and self.memo[0] == key:
                _, pick, sec.choice, sec.choice_reason = self.memo
                return pick
            pick = judge(inputs)
            if pick.kind != "switch":
                self.memo = (key, pick, None, reason)
                return pick
            names = self._names(sec, pick, reason, inputs, now)
            live = [n for n in names if not self._spins("account", n)]  # 再起動ループの検出で当たる名前を除く
            if live and live[0] in inputs.names and self.reader.recheck(live[0]) is not None:
                continue  # キャッシュの後に使えなくなった。その名前を除いたキャッシュで判断し直す
            choice = Choice(tuple(live or names), sec.account)
            pick = pick._replace(account=choice.names[0])
            self.memo = (key, pick, choice, reason)  # 打ち直した `ready` のキャッシュは同じ値なので、次の見回りでも同じスタンプになる
            sec.choice, sec.choice_reason = choice, reason
            return pick

    def _names(self, sec: Section, pick: Pick, reason: str, inputs: launch_inputs.Inputs, now: float) -> list[str]:
        """起動の理由ごとの起動順（上限の後は同じアカウントか P → order の残り、見回りは閾値の内側だけ）。"""
        if pick.account == sec.account:
            return [str(pick.account)]
        rows = inputs.usage.rows if inputs.usage is not None else []
        first = self._pinned_or(pick.account, reason, sec.account, now)
        rest = account_decide.order(sec.account, rows, inputs.candidates(), self.s.guards)
        if reason == "guard":
            by = {u.name: u for u in rows}
            rest = [n for n in rest if account_decide.group(by.get(n), self.s.guards) in INSIDE]
        return list(dict.fromkeys(n for n in (first, *rest) if n is not None))


def _stamp(inputs: launch_inputs.Inputs) -> tuple:
    """判断の入力のスタンプ（使用率のキャッシュのスタンプと `valid_until`・名前ごとの起動できるかのキャッシュ）。

    使用率のキャッシュはスタンプが変わるか `valid_until` を過ぎると読み直され、読み直すと `valid_until` も変わる。
    """
    usage = inputs.usage
    probes = tuple((n, c.result, tuple(sorted(c.stamps.items()))) for n, c in sorted(inputs.launch.items()))
    return (usage.stamp if usage else None, usage.valid_until if usage else None, inputs.names, probes)
