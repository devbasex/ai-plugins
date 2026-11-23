"""ラッパーの見回りの替えと上限の後の替え先（#3604 #3864 の決定 8・11、I12・I13・I14。`wrapper.py` から行数の上限のため分けた）。

`Wrapper` の基底クラス `GuardSteps` が、見回りごとに次を判断の関数へ渡す値にする。

- 上限（`_limit_pick`）: 画面の上限の行か、今の窓の実測値が 100 以上か API の上限の到達で子が静かなときに `after_limit` を
  呼ぶ。上限を初めて見たら今のアカウントをすぐ取り直し、その後の取得が成功するまでは上限として読む（`Reader.seen`。
  セグメントをまたいで持ち、ファイルには書かない。解けたかは取得の記録だけで決める）。一度検出した上限は `Section.limit_at` で引き継ぎ、解けた後も
  子が静かな間は再開先を判断する
- 見回り（`_guard_pick`）: 今が使い切りの組か従量の接続のときに `guard` を呼ぶ
- 止めてよい時点（`_safe_point`）: 静かな時間・回答待ちの質問が無い・会話の記録の最後が終わった応答で、最後の入力より後

替えるときは、子を止める前に起動の要求（`Choice`）を確定して `Section.choice` に残す（`_confirm`）。子を止める経路
（`Wrapper._on_resume`）が止める直前に先頭へ `ready` を打ち直し（`_recheck_choice`）、外れれば子を止めずに判断をやり直す。
"""

from __future__ import annotations

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
    fetcher: Any  # `wrapper_fetch.Fetcher`（取らないラッパーは None）
    _log: Callable[..., None]
    _inputs: Callable[[float, bool], launch_inputs.Inputs]
    _pinned_or: Callable[[Optional[str], str, Optional[str], float], Optional[str]]
    _spins: Callable[[Optional[str], Optional[str]], bool]

    def _limit_pick(self, sec: Section, screen: str) -> Optional[Pick]:
        """上限の後の判断。上限を初めて見た見回りで今のアカウントを取り直す（決定 8）。上限でなければ None。"""
        now = self.wall.now()
        if observe.limit_line(screen) is None:
            # 上限の行が無い: 前に検出した上限（解けた後の再開先の判断）か、窓の実測値が 100 以上か API の上限の到達で、
            # 子が静かな間だけ上限として扱う
            quiet = self.clock() - sec.last_output >= self.s.quiet
            if not quiet or (sec.limit_at is None and not self._spent(sec, now)):
                sec.limit_at = None  # 子がまた動き出した。次に上限を見たら取り直す
                return None
        if sec.limit_at is None:
            sec.limit_at = now
            t = sec.target
            if t is not None and t.account is not None and t.lease is not None:
                # 上限を見た後の取得が成功するまで上限として読み（`Reader.seen`。セグメントをまたぐ）、すぐ取り直す
                self.reader.seen[t.account] = (t.generation, now)
                if self.fetcher is not None:
                    self.fetcher.put(accounts.FetchTarget(self.store, t.account, t.generation), now, force=True)

        def judge(inputs: launch_inputs.Inputs) -> Pick:
            rows = inputs.usage.rows if inputs.usage is not None else []
            # 認証の失敗を確かめたセグメントでは、外した間を書けたかによらず今のアカウントを候補から除く（解けても同じアカウントで再開しない）
            candidates = [n for n in inputs.candidates() if sec.auth_at is None or n != sec.account]
            return account_decide.after_limit(sec.account, rows, candidates, inputs.fallbacks(), self.s.guards)

        pick = self._confirm(sec, "limit", now, judge)
        # 認証に失敗したアカウントはリセットを待っても再開できない。替え先が無ければ認証の判断（stalled と直し方の 1 行）へ進める
        return None if sec.auth_at is not None and pick.kind != "switch" else pick

    def _spent(self, sec: Section, now: float) -> bool:
        """今の登録済みアカウントが取得の記録で上限か（過ぎていない窓が 100 以上か、API が上限の到達を返した）。

        上限の行の文言の変化と、上限の行を出さない子に備える。世代が今の起動と違う記録では検出しない。上限を見た後の
        取得の待ち（`Reader.seen`）で上限と読む行では検出しない（替え先が無く同じアカウントで起動し直した子を止め続けない）。
        """
        t = sec.target
        usage = self._inputs(now, False).usage
        if t is None or t.lease is None or t.account is None or usage is None or usage.generations.get(t.account) != t.generation:
            return False
        mine = next((u for u in usage.rows if u.name == sec.account), None)
        return mine is not None and ((mine.percent is not None and mine.percent >= accounts.FULL) or t.account in usage.spent)

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
        """子を止める直前に、確定した起動順の先頭を確かめ直す。外れたら理由の符号、通れば None。

        登録済みアカウントは `ready` を打ち直し、従量の接続と API キー（`key:<名前>`）は替え先の判定のキャッシュを設定・登録簿の
        スタンプで確かめ直す（候補を読んだ後の設定の変更や `off` を、旧子を止める前に拾う）。外れたらキャッシュがその名前を
        候補から外し、前の判断を捨てる（次の見回りで子を止めずに選び直す）。
        """
        first = sec.choice.names[0] if sec.choice is not None and sec.choice.names else None
        if first is None:
            return None  # 既定のログインは確かめない
        if first in self.reader.inputs.names:
            why = self.reader.recheck(first)
        elif account_decide.metered(first):
            why = self.reader.recheck_fallback(first)
        else:
            return None
        if why is not None:
            self.memo = None
        return why

    def _names(self, sec: Section, pick: Pick, reason: str, inputs: launch_inputs.Inputs, now: float) -> list[str]:
        """起動の理由ごとの起動順（上限の後は同じアカウントか P → order の残り、見回りは閾値の内側だけ）。

        上限・認証の失敗・キーの子の停止（`limit`・`auth`・`revoked`）では、続けて今の接続先と `tried` を除いた従量の接続と
        API キーの替え先を並べる（先頭の用意が落ちても、残りの替え先を試す）。`revoked` で同じ名前を登録し直したときは今の名前も残す。
        """
        if pick.account == sec.account:
            return [str(pick.account)]
        rows = inputs.usage.rows if inputs.usage is not None else []
        first = self._pinned_or(pick.account, reason, sec.account, now)
        if (any(r.name == sec.account and r.limited for r in rows) or sec.auth_at is not None) and first == sec.account:
            first = pick.account  # 上限として読む・認証に失敗した今のアカウントを、指定で先頭へ戻さない
        rest = account_decide.order(sec.account, rows, inputs.candidates(), self.s.guards)
        if reason == "guard":
            by = {u.name: u for u in rows}
            rest = [n for n in rest if account_decide.group(by.get(n), self.s.guards) in INSIDE]
        # 同じ名前の登録し直しで止めたキーの子は、新しい世代のその名前を除かない（先頭の用意が落ちても新しい世代で続ける。別冊の「止める時点」）
        keep = reason == "revoked" and sec.rekeyed
        tail = [n for n in inputs.fallbacks() if keep or n != sec.account] if reason in ("limit", "auth", "revoked") else []
        return list(dict.fromkeys(n for n in (first, *rest, *tail) if n is not None))


def _stamp(inputs: launch_inputs.Inputs) -> tuple:
    """判断の入力のスタンプ（使用率のキャッシュのスタンプと `valid_until`・名前ごとの起動できるかのキャッシュ・従量の接続の判定・
    この続きで認証に失敗した接続先）。

    使用率のキャッシュはスタンプが変わるか `valid_until` を過ぎると読み直され、読み直すと `valid_until` も変わる。
    """
    usage = inputs.usage

    def cached(caches: dict[str, launch_inputs.LaunchCache]) -> tuple:
        return tuple((n, c.result, tuple(sorted(c.stamps.items()))) for n, c in sorted(caches.items()))

    head = (usage.stamp if usage else None, usage.valid_until if usage else None, inputs.names)
    return (*head, cached(inputs.launch), cached(inputs.fallback), tuple(sorted(inputs.tried)))
