"""ラッパーの認証の失敗の確かめと替え先（#3604 #3864 の決定 6、I8。`wrapper.py` から行数の上限のため分けた）。

`Wrapper` の基底クラス `AuthSteps` が、見回りごとに次を判断の関数へ渡す値にする。

- 画面の行（`observe.AUTH_FAILED`）は引き金だけ。今のセグメントの画面（利用者の入力を送るたびに空にする）に引き金があり、
  会話の記録の会話の行の最後が資格情報の失敗の API の失敗の記録で、セグメントの起動より後のときだけ確かめる
- 確かめた最初の見回りで、今の登録済みアカウントを `relay.auth-hold` 秒外す（`accounts.hold`。既定のログインは外さない）
- 替え先は `after_auth` で決め、子を止める前に起動の要求を確定する（`GuardSteps._confirm`。起動の理由は `auth`）
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

    def _auth_pick(self, sec: Section, screen: str) -> Optional[Pick]:
        """認証の失敗を確かめたときの替え先の判断。確かめられなければ None（画面の引き金だけでは何もしない）。"""
        if not observe.auth_failed(screen):
            return None
        transcript: Path = observe.transcript_path(self.s.environ, self.s.cwd, sec.session_id)
        if not observe.credential_failure(transcript, self.s.transcript_bytes, sec.wall_started):
            return None
        now = self.wall.now()
        t = sec.target
        if sec.auth_at is None:
            sec.auth_at = now
            if t is not None and t.account is not None and t.lease is not None:  # 登録済みアカウントだけ外す
                accounts.hold(self.s.state_dir, t.account, t.generation, now + limits.seconds("relay.auth-hold"), now)

        def judge(inputs: launch_inputs.Inputs) -> Pick:
            rows = inputs.usage.rows if inputs.usage is not None else []
            return account_decide.after_auth(sec.account, rows, inputs.candidates(), [], self.s.guards)

        pick = self._confirm(sec, "auth", now, judge)
        if pick.kind != "switch" and not sec.auth_told:
            sec.auth_told = True
            self._say(auth_fix(sec.account))
        return pick
