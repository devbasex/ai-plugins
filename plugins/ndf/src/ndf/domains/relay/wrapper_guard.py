"""ラッパーの見回りの替えと上限の後の替え先（#3604 #3864 の決定 8・11、I12・I13・I14。`wrapper.py` から行数の上限のため分けた）。

`Wrapper` の基底クラス `GuardSteps` が、見回りごとに次を判断の関数へ渡す値にする。

- 上限（`_limit_pick`）: 登録済みアカウントは、使用率の API の結果（取得の記録）で上限のときだけ `choose` の `limit` を呼ぶ（#4404）。
  画面の上限の行は、今のアカウントの取り直しを急がせるきっかけにだけ使う。取り直した結果が上限でなければ、その行は前の起動の
  描き直しとして、このセグメントでは見ない。既定のログインと従量の接続と、使用率を取らないラッパー（`fetch` が None）は API の結果が無いので、画面の上限の行で決める。
  一度上限と判断したら `Section.limit_at` で引き継ぎ、解けた後も子が静かな間は再開先を判断する（解けたら同じアカウントで再開する）
- 見回り（`_guard_pick`）: 今が閾値の外側か従量の接続のときに `choose` の `running` を呼ぶ
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


class GuardSteps:
    """`Wrapper` の見回りの手順。属性と記録の手（`_log`・`_inputs`・`_explicit`・`_spins`）は `Wrapper` が持つ。"""

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
    _explicit: Callable[[], tuple[str, ...]]
    _spins: Callable[[Optional[str], Optional[str]], bool]

    def _limit_pick(self, sec: Section, screen: str) -> Optional[Pick]:
        """上限の後の判断（モジュールの説明の「上限」）。上限でなければ None。"""
        now = self.wall.now()
        quiet = self.clock() - sec.last_output >= self.s.quiet
        line = observe.limit_line(screen) is not None
        t = sec.target
        if self.fetcher is not None and t is not None and t.account is not None and t.lease is not None:
            if line and not sec.limit_refuted and sec.limit_asked is None and sec.limit_at is None:
                sec.limit_asked = now  # 画面の行は取り直しを急がせるだけ。判断は取り直した API の結果で行う
                self.fetcher.put(accounts.FetchTarget(self.store, t.account, t.generation), now, force=True)
            if self._spent(sec, now):
                if not (quiet or line):
                    return None
                sec.limit_asked = None
                sec.limit_at = sec.limit_at or now
            else:
                if sec.limit_asked is not None and (accounts.fetched_at(self.store, t.account, t.generation) or 0.0) >= sec.limit_asked:
                    sec.limit_asked, sec.limit_refuted = None, True
                    self._log({"kind": "limit_refuted", "account": t.account, "reason": "画面に上限の行があるが、API の結果で上限でない"})
                if sec.limit_at is None or not quiet:
                    if not quiet:
                        sec.limit_at = None  # 子がまた動き出した
                    return None
        else:
            if not line:
                if not quiet or sec.limit_at is None:
                    sec.limit_at = None
                    return None
            sec.limit_at = sec.limit_at or now

        def judge(inputs: launch_inputs.Inputs) -> Pick:
            rows = inputs.usage.rows if inputs.usage is not None else []
            # 認証の失敗を確かめたセグメントでは、今のアカウントを候補から除く（解けても同じアカウントで再開しない）
            candidates = [n for n in inputs.candidates() if sec.auth_at is None or n != sec.account]
            return account_decide.choose("limit", sec.account, rows, candidates, self.s.guards, inputs.fallbacks(), self._explicit())

        pick = self._confirm(sec, "limit", now, judge)
        # 認証に失敗したアカウントはリセットを待っても再開できない。替え先が無ければ認証の判断（stalled と直し方の 1 行）へ進める
        return None if sec.auth_at is not None and pick.kind != "switch" else pick

    def _spent(self, sec: Section, now: float) -> bool:
        """今の登録済みアカウントが使用率の API の結果で上限か（過ぎていない窓が 100 以上か、API が上限の到達を返した）。

        世代が今の起動と違う記録では検出しない。
        """
        t = sec.target
        usage = self._inputs(now, False).usage
        if t is None or t.lease is None or t.account is None or usage is None or usage.generations.get(t.account) != t.generation:
            return False
        mine = next((u for u in usage.rows if u.name == sec.account), None)
        return mine is not None and (mine.limited or t.account in usage.spent)

    def _guard_pick(self, sec: Section, now: float) -> Optional[Pick]:
        """見回りの判断。今が閾値の外側か従量の接続のときだけ候補を読み、`choose` の `running` を呼ぶ。"""
        usage = self._inputs(now, False).usage
        mine = next((u for u in (usage.rows if usage is not None else []) if u.name == sec.account), None)
        if sec.account is None or (not account_decide.metered(sec.account) and account_decide.inside(mine, self.s.guards)):
            return None

        def judge(inputs: launch_inputs.Inputs) -> Pick:
            rows = inputs.usage.rows if inputs.usage is not None else []
            return account_decide.choose("running", sec.account, rows, inputs.candidates(), self.s.guards, explicit=self._explicit())

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
        names = self._names(sec, pick, reason)
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

    def _names(self, sec: Section, pick: Pick, reason: str) -> list[str]:
        """起動の順（`choose` の `order`）。上限・認証の失敗・キーの子の停止で今の名前を入れるのは、判断がそれを選んだときだけ。"""
        names = list(pick.order) or [str(pick.account)]
        if reason == "revoked" and sec.rekeyed and sec.account is not None and sec.account not in names:
            names.append(sec.account)  # 同じ名前の登録し直しで止めたキーの子は、新しい世代のその名前を残す（別冊の「止める時点」）
        return names


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
