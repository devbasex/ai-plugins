"""ラッパーの認証の失敗の確かめと替え先（#3604 #3864 の決定 6、I8。`wrapper.py` から行数の上限のため分けた）。

`Wrapper` の基底クラス `AuthSteps` が、見回りごとに次を判断の関数へ渡す値にする。

- 画面の行（`observe.AUTH_FAILED`）は引き金だけ。今のセグメントの画面（利用者の入力を送るたびに空にする）に引き金があり、
  会話の記録の会話の行の最後が資格情報の失敗の API の失敗の記録で、セグメントの起動より後のときだけ確かめる
- 確かめと外す記録（`_auth_seen`）は上限の手を選ぶ見回りでも打つ。確かめた最初の見回りで、今の登録済みアカウントを
  `relay.auth-hold` 秒外す（`accounts.hold`。既定のログインは外さない）。錠を取れない・書けなければ `log.jsonl` に 1 行書き、
  今のアカウントを除いた替え先の判断を続ける
- 会話の記録はスタンプ（`stat`）が変わったときだけ読み直す（替え先が無く待つ間に、見回りごとに末尾を読まない）。
  開けない・読めなかった見回りの結果は残さず、次の見回りで読み直す
- 替え先は `account_decide.choose` の `auth` で決め、子を止める前に起動の要求を確定する（`GuardSteps._confirm`。起動の理由は `auth`）
- 替え先が無ければ、ログインの形ごとの直し方の 1 行を同じセグメントで 1 回出す（`Section.auth_told`）
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable, Optional

from ndf.contracts import limits
from ndf.domains.relay import account_decide, accounts, launch_inputs, observe
from ndf.domains.relay.account_decide import Pick
from ndf.domains.relay.account_registry import METERED

Section = Any  # `wrapper.Section`（import の輪を作らないよう型の名前だけで受ける）
# 替え先が無いときの直し方の 1 行（#3864 の受け入れ条件 7。ログインの形ごとに文が違う）
AUTH_FIX_LINE = "ndf-relay: {who}の認証に失敗し、替える先が無い。{fix}"
AUTH_FIX_DEFAULT = "claude の中で /login を打つ"
AUTH_FIX_ACCOUNT = "この子を /exit で終えてから ndf relay account add {name} で登録し直す（子が動いている間は登録のコマンドが止まる）"
AUTH_FIX_METERED = "ndf relay account check metered で設定を確かめる"


def auth_fix(account: Optional[str]) -> str:
    """替え先が無いときの直し方の 1 行。既定のログイン（None）・従量の接続・登録済みアカウントで文が違う。"""
    if account is None:
        return AUTH_FIX_LINE.format(who="既定のログイン", fix=AUTH_FIX_DEFAULT)
    if account == METERED:
        return AUTH_FIX_LINE.format(who="従量の接続", fix=AUTH_FIX_METERED)
    return AUTH_FIX_LINE.format(who=f"アカウント {account} ", fix=AUTH_FIX_ACCOUNT.format(name=account))


class AuthSteps:
    """`Wrapper` の認証の失敗の手順。属性と手（`_say`・`_confirm`）は `Wrapper` と `GuardSteps` が持つ。"""

    s: Any
    wall: Any
    _say: Callable[[str], None]
    _confirm: Callable[[Section, str, float, Callable[[launch_inputs.Inputs], Pick]], Pick]
    _log: Callable[..., None]
    tried: set[str]
    auth_memo: Optional[tuple[tuple[str, Optional[tuple]], bool]] = None  # ((起動の ID, 会話の記録のスタンプ), 確かめた結果)

    def _transcript(self, sec: Section) -> Path:
        return observe.transcript_path(self.s.environ, self.s.cwd, sec.session_id)

    def _auth_seen(self, sec: Section, screen: str) -> bool:
        """画面の引き金を会話の記録で確かめ、確かめた最初の見回りで今の登録済みアカウントを外す。確かめられなければ偽。"""
        if not observe.auth_failed(screen):
            return False
        transcript = self._transcript(sec)
        key = (sec.launch, launch_inputs.stamp(transcript))
        if self.auth_memo is None or self.auth_memo[0] != key:
            got = observe.credential_failure(transcript, self.s.transcript_bytes, sec.wall_started)
            if got is None:  # 読めなかった結果は残さない（権限を直してもスタンプは変わらない。次の見回りで読み直す）
                self.auth_memo = None
                return False
            self.auth_memo = (key, got)
        if not self.auth_memo[1]:
            return False
        if sec.auth_at is None:
            sec.auth_at = self.wall.now()
            self._auth_hold(sec, sec.auth_at)
        return True

    def _auth_hold(self, sec: Section, now: float) -> None:
        """今の登録済みアカウントを外す。錠を取れない（`TimeoutError`）・書けなければ記録せず、判断を続ける（`log.jsonl` に 1 行）。

        同じ障害で `log.jsonl` にも書けなければ、その行も諦めて判断を続ける（`registry.append_log` が例外を出さない）。
        """
        t = sec.target
        if t is None or t.account is None or t.lease is None:  # 登録済みアカウントだけ外す
            return
        try:
            accounts.hold(self.s.state_dir, t.account, t.generation, now + limits.seconds("relay.auth-hold"), now)
        except OSError as exc:
            self._log({"kind": "auth_hold", "account": t.account, "reason": f"外した間を書けない: {exc}"})

    def _auth_pick(self, sec: Section) -> Pick:
        """認証の失敗を確かめた（`_auth_seen` が真の）見回りの替え先の判断。替え先は今のアカウントを除いて選ぶ。"""
        now = self.wall.now()

        def judge(inputs: launch_inputs.Inputs) -> Pick:
            rows = inputs.usage.rows if inputs.usage is not None else []
            return account_decide.choose(
                "auth",
                sec.account,
                rows,
                account_decide.Pool(inputs.candidates(), inputs.fallbacks(), explicit=self._explicit()),
                self.s.guards,
            )

        if account_decide.metered(sec.account):  # 従量の接続は外した間の代わりに、この続きで試した接続先として除く
            self.tried.add(str(sec.account))
        pick = self._confirm(sec, "auth", now, judge)
        if pick.kind != "switch" and not sec.auth_told:
            sec.auth_told = True
            self._say(auth_fix(sec.account))
        return pick
