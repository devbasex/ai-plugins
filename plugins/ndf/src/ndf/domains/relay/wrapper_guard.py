"""ラッパーの見回りの替えと上限の後の替え先（#3604 #3864 の決定 8・11、I12・I13・I14。`wrapper.py` から行数の上限のため分けた）。

`Wrapper` の基底クラス `GuardSteps` が、見回りごとに次を判断の関数へ渡す値にする。

- 上限（`_limit_pick`）: 画面の上限の行か、今の窓の実測値が 100 以上で子が静かなときに、上限の観測を 1 回書いて
  `after_limit` を呼ぶ。使用率の API の上限の到達だけで検出したときは観測を書かず、API の解除に任せる。一度検出した上限は `Section.limit_at` で引き継ぎ、解けた後も子が静かな間は再開先を判断する
- 見回り（`_guard_pick`）: 今が使い切りの組か従量の接続のときに `guard` を呼ぶ
- 止めてよい時点（`_safe_point`）: 静かな時間・回答待ちの質問が無い・会話の記録の最後が終わった応答で、最後の入力より後

替えるときは、子を止める前に起動の要求（`Choice`）を確定して `Section.choice` に残す（`_confirm`）。子を止める経路
（`Wrapper._on_resume`）が止める直前に先頭へ `ready` を打ち直し（`_recheck_choice`）、外れれば子を止めずに判断をやり直す。
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
        spent: Optional[str] = None
        if line is None:
            # 上限の行が無い: 前に検出した上限（解けた後の再開先の判断）か、窓の実測値が 100 以上か API の上限の到達で、
            # 子が静かな間だけ上限として扱う
            quiet = self.clock() - sec.last_output >= self.s.quiet
            spent = self._spent(sec, now) if quiet and sec.limit_at is None else None
            if not quiet or (sec.limit_at is None and spent is None):
                sec.limit_at, sec.limit_lost = None, False  # 子がまた動き出した。次に上限を見たら観測を書き直す
                return None
            line = ""  # 窓が 100 以上で静かな子は上限の行を持たない（窓から until を決める）
        if sec.limit_at is None:
            sec.limit_at = now
            # API だけで検出した上限は観測を書かない。API が次の取得で解除を返すと今の行の上限が落ち、`after_limit` が再開する
            sec.limit_lost = spent != "api" and not self._see_limit(sec, line, now)

        def judge(inputs: launch_inputs.Inputs) -> Pick:
            rows = inputs.usage.rows if inputs.usage is not None else []
            if sec.limit_lost and sec.account is not None:  # 観測を書けなかったセグメントでは、今のアカウントを上限として扱う
                mine = account_decide.Usage(sec.account, None, None, None, None, True)
                rows = [r._replace(limited=True) if r.name == sec.account else r for r in rows]
                rows += [] if any(r.name == sec.account for r in rows) else [mine]
            # 観測を書いたのに今の行が無ければ、観測の until を過ぎて解けた（窓が分からないまま）。`after_limit` が同じアカウントで再開する。
            # 認証の失敗を確かめたセグメントでは、外した間を書けたかによらず今のアカウントを候補から除く（解けても同じアカウントで再開しない）
            candidates = [n for n in inputs.candidates() if sec.auth_at is None or n != sec.account]
            return account_decide.after_limit(sec.account, rows, candidates, [], self.s.guards)

        pick = self._confirm(sec, "limit", now, judge)
        # 認証に失敗したアカウントはリセットを待っても再開できない。替え先が無ければ認証の判断（stalled と直し方の 1 行）へ進める
        return None if sec.auth_at is not None and pick.kind != "switch" else pick

    def _spent(self, sec: Section, now: float) -> Optional[str]:
        """今の登録済みアカウントの過ぎていない窓の実測値が 100 以上なら `window`、使用率の API が上限の到達を返したなら
        `api`、どちらでもなければ None（上限の行の文言の変化と、上限の行を出さない子に備える）。

        別のレーンが書いた上限の観測（`Usage.limited`）では検出しない（静かなだけの子を止めない）。観測は替え先の除外に使う。
        API の上限の到達は、使用率のキャッシュが行と同じ取得の記録から取った `spent` を読む（見回りごとに記録を開かない）。
        """
        t = sec.target
        if t is None or t.lease is None or t.account is None:
            return None
        usage = self._inputs(now, False).usage
        if usage is None:
            return None
        mine = next((u for u in usage.rows if u.name == sec.account), None)
        if mine is None or not mine.limited:
            return None
        if mine.percent is not None and mine.percent >= accounts.FULL:
            return "window"
        return "api" if t.account in usage.spent and usage.generations.get(t.account) == t.generation else None

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

        入力のスタンプが変わらず `valid_until` の前なら、前の判断を返す。先頭への `ready` の打ち直しは子を止める経路
        （`_recheck_choice`）が止める直前に行う（止めてよい時点を待つ間の確かめでは、その後の認証や設定の変化を拾えない）。
        """
        inputs = self._inputs(now, True)
        # 認証の失敗を確かめる前の判断を、確かめた後に使い回さない（今のアカウントを替え先から除くかが変わる）
        key = (sec.launch, reason, sec.auth_at is not None, _stamp(inputs), launch_inputs.stamp(accounts.pin_path(self.s.state_dir)))
        if self.memo is not None and self.memo[0] == key:
            _, pick, sec.choice, sec.choice_reason = self.memo
            return pick
        pick = judge(inputs)
        if pick.kind != "switch":
            self.memo = (key, pick, None, reason)
            return pick
        names = self._names(sec, pick, reason, inputs, now)
        live = [n for n in names if not self._spins("account", n)]  # 再起動ループの検出で当たる名前を除く
        # 認証の失敗を確かめたセグメントとキーの子の停止（`revoked`）では、尽きたときに今のアカウントへ落とさない（既定のログインも起動しない）
        held = sec.auth_at is not None or reason == "revoked"
        choice = Choice(tuple(live or names), None if held else sec.account, last=held)
        pick = pick._replace(account=choice.names[0])
        self.memo = (key, pick, choice, reason)
        sec.choice, sec.choice_reason = choice, reason
        return pick

    def _recheck_choice(self, sec: Section) -> Optional[str]:
        """子を止める直前に、確定した起動順の先頭の登録済みアカウントへ `ready` を打ち直す。外れたら理由の符号、通れば None。

        外れたらキャッシュがその名前を候補から外し、前の判断を捨てる（次の見回りで子を止めずに選び直す）。
        """
        first = sec.choice.names[0] if sec.choice is not None and sec.choice.names else None
        if first is None or first not in self.reader.inputs.names:
            return None  # 従量の接続・既定のログインは `ready` を打たない
        why = self.reader.recheck(first)
        if why is not None:
            self.memo = None
        return why

    def _names(self, sec: Section, pick: Pick, reason: str, inputs: launch_inputs.Inputs, now: float) -> list[str]:
        """起動の理由ごとの起動順（上限の後は同じアカウントか P → order の残り、見回りは閾値の内側だけ）。"""
        if pick.account == sec.account:
            return [str(pick.account)]
        rows = inputs.usage.rows if inputs.usage is not None else []
        first = self._pinned_or(pick.account, reason, sec.account, now)
        if (sec.limit_lost or sec.auth_at is not None) and first == sec.account:
            first = pick.account  # 観測を書けず上限として扱う・認証に失敗した今のアカウントを、指定で先頭へ戻さない
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
