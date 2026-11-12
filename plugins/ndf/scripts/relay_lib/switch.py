"""区間のアカウント（#1389）: 区間ごとのアカウントの選び方・上限シグナルファイルの判定。

`Relay`（`run.py`）が `AccountSwitch` を継いで使う。登録済みのアカウントが 2 つ以上あれば、区間の起動（区切り）の
たびに起動先の決定（`launch_rules`。API キーは使わない）で選び直し、今のアカウントが最良の候補と同じ選ぶ順の組に
なければ替える（#2930 の決定 6）。子の応答が利用上限で終わった（`limit.json`）ら別のアカウントで次の区間を起動する。
すべて上限なら宣言された従量の接続へ移り、候補のアカウントが出たら次の区切りで戻す。区切りの間に使用率を取りに
行かない（#2930 の決定 7）。
置き場と選び方は `lib/claude_accounts.py`、文言の読みは `lib/claude_usage.py` が持つ。
"""

from __future__ import annotations

import math
import os
import time

from . import claude as cl
from . import pin
from .common import LIMIT_FILE, QUESTION_FILE, load_json, parse_iso, remove, stamp
from .mark import asked_after

import claude_accounts as ca  # noqa: E402,I001  common が lib/ を sys.path に置く
import claude_usage as cu  # noqa: E402

# 上限シグナルファイルのうち、アカウントを替える引き金になる `error`（`authentication_failed` は別に扱う）
LIMIT_ERRORS = ("rate_limit", "billing_error")

# 切り替えの理由（`pick` が決め、画面の 1 行と記録の `reason` に出る）。上限の種類（`cu.KINDS`）も理由になる
REASON_RULE = "rule"  # 区切りの選び直しで、今のアカウントが最良の候補と同じ組にない
REASON_START = "start"  # 既定のログインから最初のアカウントへ
REASON_UNUSABLE = "unusable"  # 今のアカウントが使えない
REASON_RECOVERED = "recovered"  # 従量の接続からアカウントへ戻す
REASON_LIMITED = "limited"  # すべて上限で従量の接続へ
REASON_AUTH = "auth"  # 認証が通らなかった
REASON_PINNED = "pinned"  # `account use` で指定したアカウントへ（#2460）


_RECOVERED, _LIMIT = object(), object()  # 理由の文字列と重ならない表の鍵


def _msg_recovered(sw, prev, to, reason) -> tuple[str, dict]:
    return f"{ca.account_label(to)}の上限が外れたため、従量の接続からアカウント {to} へ戻して続ける", {"reason": REASON_RECOVERED}


def _msg_rule(sw, prev, to, reason) -> tuple[str, dict]:
    return f"選ぶ順の組が変わったため、アカウントを {prev} から {ca.account_label(to)}へ替える", {}


def _msg_limit(sw, prev, to, reason) -> tuple[str, dict]:
    return f"利用上限（{reason}）に達したため、アカウントを {prev} から {ca.account_label(to)}へ替えて続ける", {}


def _msg_auth(sw, prev, to, reason) -> tuple[str, dict]:
    return f"認証が通らなかったため、アカウントを {prev or '既定のログイン'} から {ca.account_label(to)}へ替えて続ける", {}


def _msg_pinned(sw, prev, to, reason) -> tuple[str, dict]:
    return f"指定に従い、アカウントを {prev or '既定のログイン'} から {ca.account_label(to)}へ替える", {}


def _msg_default(sw, prev, to, reason) -> tuple[str, dict]:
    return f"アカウントを {prev or '既定のログイン'} から {ca.account_label(to)}へ替える", {}


# 切り替えの理由 → (画面の 1 行, 記録の行へ足す項目) を作る関数（`AccountSwitch._switch_message` が引く）
_SWITCH_MESSAGES = {
    _RECOVERED: _msg_recovered,
    REASON_RULE: _msg_rule,
    _LIMIT: _msg_limit,
    REASON_AUTH: _msg_auth,
    REASON_PINNED: _msg_pinned,
}


# 切り替えの理由 → 次のセッションの最初の入力（未達の `/goal` が無いとき。#1576 の決定 16）
_RESUME_TEXTS = {
    _LIMIT: "利用上限でアカウントを替えた。中断したところから続ける",
    REASON_AUTH: "認証が通らなかったためアカウントを替えた。中断したところから続ける",
    REASON_RULE: "選ぶ順の組が変わったためアカウントを替えた。中断したところから続ける",
    REASON_RECOVERED: "上限が外れたためアカウントへ戻した。中断したところから続ける",
}
_RESUME_DEFAULT = "アカウントを替えた。中断したところから続ける"

# 再登録・宣言の確かめのコマンド（relay.md が利用者に示している形）
_RELAY_CMD = "python3 ~/.claude/ndf/relay.py"


def login_on(name: str | None) -> str:
    """区間のログインの形と助詞（`既定のログインで`・`従量の接続で`・`アカウント <名前> で`）。"""
    if name is None:
        return "既定のログインで"
    return "従量の接続で" if name == ca.METERED else f"アカウント {name} で"


def auth_guide(name: str | None) -> str:
    """401 で替え先が無いときの 1 行（ログインの形ごとの直し方。#1727 の決定 4）。名前と変数の名前だけを書く（I5）。"""
    if name is None:
        fix = "claude の中で /login を打ってログインし直す"
    elif name == ca.METERED:
        fix = f"{_RELAY_CMD} account check metered で宣言（環境変数 {ca.FALLBACK_ENV} か保存した宣言）を確かめる"
    else:
        fix = f"{_RELAY_CMD} account add {name} で登録し直す"
    return f"{login_on(name)}認証に失敗した（401）。{fix}。子はこのまま残す"


def resume_text(reason: str | None) -> str:
    """切り替えの理由に合う再開の文。上限の種類（`cu.KINDS`）はどれも同じ文にまとめる。"""
    return _RESUME_TEXTS.get(_LIMIT if reason in cu.KINDS else reason, _RESUME_DEFAULT)


class AccountSwitch:
    """`Relay` が継ぐ、区間のアカウントの扱い。`Relay` の `account`・`multi`・`env`・`term`・`limit`・
    `section` などを使う。"""

    def pick(self, kind: str | None) -> tuple[str | None, str | None, ca.Choice | None]:
        """次の区間のアカウントを決める。(名前か `metered` か None, 理由, 選んだ結果)。

        `kind` は上限の種類（上限の後。None が返れば切り替えずに子を残す）か None（区間の起動。None は今と同じ環境）。
        区間の起動では、`account use` の指定が使えればそれを、無ければ起動先の決定で選び直す（今のアカウントが最良の
        候補と同じ組にあれば替えない。切り替えの余裕）。"""
        cur = self.account
        declared = bool(ca.fallback_env(self.env))
        if kind is not None:
            return self._pick_after_limit(kind, cur, declared)
        pinned = self._pick_pinned(cur)
        if pinned is not None:
            return pinned
        if cur == ca.METERED:
            return self._pick_from_metered()
        return self._pick_for_section(cur, declared)

    def _pick_pinned(self, cur: str | None) -> tuple[str | None, str | None, ca.Choice | None] | None:
        """区間の起動で、`account use` の指定が使えればそれを選ぶ（閾値と残りの量に依らない）。使えなければ None
        （今の選び方へ。指定は外さない。#2460）。指定は区間の起動のたびにファイルから読む。"""
        p = pin.pinned()
        if p is None:
            return None
        if p == cur:
            return (cur, None, None) if self._current_usable(cur) else None
        ca.usage(p, p not in self.keep())
        acc, now = ca.load_account(p), time.time()
        if acc is None or acc.needs_relogin or acc.auth_held(now) or acc.limited_until(now) is not None or not ca.usable(p, now):
            return None
        return p, REASON_PINNED, ca.Choice(p, acc.score(), None, acc.remaining())

    def _pick_for_section(self, cur: str | None, declared: bool) -> tuple[str | None, str | None, ca.Choice | None]:
        """区間の起動で、起動先の決定（API キーを使わない）で選び直す。候補が無く、今のアカウントも使えなければ従量の接続へ。"""
        c = ca.choose(keep=self.keep(), current=cur)
        if c.name == cur and cur is not None:
            return cur, None, c
        if c.name:
            usable = cur is not None and self._current_usable(cur)
            return c.name, REASON_RULE if usable else (REASON_START if cur is None else REASON_UNUSABLE), c
        if declared and not (cur is not None and self._current_usable(cur)):
            return ca.METERED, REASON_LIMITED, c
        return cur, None, c

    def section_plan(self, plan, prev: str | None) -> tuple[str | None, str | None, ca.Choice | None]:
        """区間の起動で使う (アカウント, 理由, 選んだ結果)。予定（上限・認証の失敗の後）は登録の数に依らず使い、予定が無く
        登録が 1 つ以下なら今のアカウントを引き継ぐ（#1727 の I7）。"""
        if plan:
            return plan
        if self.multi:
            return self.pick(None)
        return self._pick_pinned(prev) or (prev, None, None)

    def stop_accounts(self) -> None:
        """ラッパーの終了時: 今のアカウントの .claude.json の共有する部分を書き戻す（E12）。"""
        self.settle_account(self.account)

    def settle_account(self, name: str | None) -> None:
        """アカウント `name` のセッションの後の書き戻し（従量の接続・既定のログインでは何もしない）。"""
        if name and name != ca.METERED:
            ca.settle(name, self.env, lambda row: self.note_account_dir(row, self.section))

    def note_account_dir(self, row: dict, section: int | None = None) -> None:
        """アカウントの設定ディレクトリの用意・書き戻しの結果を `log.jsonl` の `account_dir` の行と、使わないときは画面の
        1 行に残す。行に載せるのは項目の名前と理由の語だけである（I14）。"""
        self.log(event="account_dir", section=self.section + 1 if section is None else section, **row)
        if not row.get("ok"):
            why = ca.prepare_reason_text(row["account"], row.get("reason"))
            self.term.screen(f"ndf-relay: アカウント {row['account']} を使わない（{why}）")

    def keep(self) -> set[str]:
        """トークンを更新しないアカウント（今のセッションのアカウント。claude が更新する。#1576 の I8）。"""
        return {self.account} if self.account and self.account != ca.METERED else set()

    def _pick_after_limit(self, kind: str, cur: str | None, declared: bool) -> tuple[str | None, str | None, ca.Choice | None]:
        """上限と認証の失敗の後の選び直し（今のアカウントを除く）。None が返れば切り替えずに子を残す。
        従量の接続の認証の失敗では替えない（宣言を直さないまま行き来しない。#1727 の I2）。"""
        if kind == REASON_AUTH and cur == ca.METERED:
            return None, kind, None
        c = ca.choose(exclude=set() if cur in (None, ca.METERED) else {cur}, keep=self.keep())
        if c.name:
            return c.name, REASON_RECOVERED if cur == ca.METERED else kind, c
        if declared and cur != ca.METERED:
            return ca.METERED, kind, c
        return None, kind, c

    def _pick_from_metered(self) -> tuple[str | None, str | None, ca.Choice]:
        """従量の接続で動く区間の起動。起動先の決定が候補のアカウントを返せば戻す（組を問わない。#2930 の決定 7）。"""
        c = ca.choose(keep=self.keep())
        if c.recoverable():
            return c.name, REASON_RECOVERED, c
        return ca.METERED, None, c

    @staticmethod
    def _current_usable(cur: str) -> bool:
        """今のアカウントが使えるか（再登録が要らず、認証の失敗の観測が無く、上限にない）。トークンは更新しない。"""
        ca.usage(cur, False)
        acc = ca.load_account(cur)
        now = time.time()
        return acc is not None and not acc.needs_relogin and not acc.auth_held(now) and acc.limited_until(now) is None

    def tell_account(self, prev: str | None, to: str | None, reason: str | None, choice: ca.Choice | None) -> None:
        """区間を起動したアカウントを画面の 1 行と記録（`account` の行）に残す（I12）。トークンと宣言の値は書かない。"""
        if to == prev and self.section > 1:
            return
        if to is None:
            return
        if to == ca.METERED:
            keys = list(ca.fallback_env(self.env))
            if reason == REASON_AUTH:
                why = f"{login_on(prev)}認証に失敗し、替えられる登録済みのアカウントが無い"
            else:
                why = self.all_limited(choice)
            self.term.screen("ndf-relay: " + why + f"。従量の接続（{self.keys_text(keys)}）へ替えて続ける")
            self.log(
                event="account", section=self.section, reason=reason, **{"from": prev}, to=to, keys=keys, earliest=self.earliest(choice)
            )
            return
        if self.section == 1:
            self.term.screen(f"ndf-relay: アカウント {ca.account_label(to)}で起動する")
            return
        row = {"event": "account", "section": self.section, "reason": reason, "from": prev, "to": to}
        decision = getattr(choice, "decision", None)
        if decision is not None:  # 起動先の決定の規則の名前と根拠の値（#2930 の受け入れ条件 23）
            row.update(rule=decision.rule, account_rule=decision.account_rule, evidence=decision.evidence)
        line, extra = self._switch_message(prev, to, reason)
        row.update(extra)
        self.term.screen("ndf-relay: " + line)
        self.log(**row)

    def _switch_message(self, prev: str | None, to: str, reason: str | None) -> tuple[str, dict]:
        """2 つ目以降の区間でアカウントを替えたときの (画面の 1 行, 記録の行へ足す項目)。理由ごとに決まる。"""
        # 従量の接続から戻すときは理由に依らず先に効く。上限の種類はどれも同じ文にまとめる
        key = REASON_PINNED if reason == REASON_PINNED else _RECOVERED if prev == ca.METERED else _LIMIT if reason in cu.KINDS else reason
        return _SWITCH_MESSAGES.get(key, _msg_default)(self, prev, to, reason)

    @staticmethod
    def earliest(choice: ca.Choice | None) -> dict | None:
        if choice is None or choice.earliest is None:
            return None
        name, t = choice.earliest
        return {"name": name, "at": None if t == math.inf else stamp(t)}

    @staticmethod
    def all_limited(choice: ca.Choice | None) -> str:
        if choice is None or choice.earliest is None:
            return "登録済みのアカウントはすべて上限にある"
        name, t = choice.earliest
        return f"登録済みのアカウントはすべて上限にある（最も早く戻るのは {name}、{ca.local_time(t, '%H:%M')}）"

    def tell_relogin(self) -> None:
        """起動時に、再登録が要るアカウントを 1 つにつき 1 行出す（E1）。判定に失敗しても起動を止めない。"""
        try:  # `usable` で判定してから印を読む（候補の判定と食い違わない。#1727 の決定 5）
            found = [n for n in ca.names() if not ca.usable(n) and (a := ca.load_account(n)) is not None and a.needs_relogin]
        except Exception:
            return
        for n in found:
            self.term.screen(f"ndf-relay: アカウント {n} は再登録が要る。{_RELAY_CMD} account add {n} で登録し直す")

    @staticmethod
    def keys_text(keys: list[str]) -> str:
        return keys[0] + (f" ほか {len(keys) - 1} つ" if len(keys) > 1 else "") if keys else "宣言"

    def read_limit(self) -> dict | None:
        m = load_json(self.path(LIMIT_FILE))
        return m if isinstance(m, dict) and isinstance(m.get("written_at"), str) else None

    def drop_limit(self, lim: dict) -> None:
        """上限シグナルファイルを、読んだ内容と同じときだけ消す（I15）。"""
        now = load_json(self.path(LIMIT_FILE))
        if isinstance(now, dict) and now == {k: v for k, v in lim.items() if not k.startswith("_")}:
            remove(self.path(LIMIT_FILE))

    def _tick_limit(self):
        """上限シグナルファイルの判定（F5）。替えるなら合図と同じ形の辞書（`_kind` が `limit`）を返す。"""
        lim = self.read_limit()
        if lim is None:
            return None
        auth = lim.get("error") == "authentication_failed" and self.auth_section != self.section
        if not self._limit_relevant(lim, auth):
            return None
        written = parse_iso(lim.get("written_at")) or time.time()
        tp = lim.get("transcript_path") or ""
        snap = cl.file_snap(tp)
        if self._limit_on_hold(lim, written, tp, snap):
            return None
        kind, resets = (REASON_AUTH, None) if auth else cl.limit_of(tp)
        self._note_observed(lim, kind, resets)
        if self._wait_background(lim, kind, tp):
            return None
        if auth:
            self.auth_section = self.section  # NDF は動いている claude のトークンを更新しない（#1576 の I8）
        to, reason, choice = self.pick(kind)
        if to is None:
            self.log(event="account", section=self.section, reason=kind, **{"from": self.account}, to=None, earliest=self.earliest(choice))
            line = auth_guide(self.account) if auth else self.all_limited(choice) + "。子はこのまま残す"
            self.term.screen("ndf-relay: " + line)
            self.drop_limit(lim)
            return None
        if not self._may_start(written):
            return None
        return {**lim, "_kind": "limit", "_snap": snap, "_plan": (to, reason, choice)}

    def _limit_relevant(self, lim, auth) -> bool:
        """替える対象の上限か。認証の失敗は登録の数に依らず扱う。上限でない失敗（overloaded など）と、登録が 1 つ以下の
        ときの上限は読み捨てて偽を返す。"""
        if not auth and not (self.multi and lim.get("error") in LIMIT_ERRORS):
            self.drop_limit(lim)
            return False
        return True

    def _limit_on_hold(self, lim, written, tp, snap) -> bool:
        """静けさ・質問・利用者の入力・停止の合図のどれかで、今は替えないなら真。"""
        latest = max(written, self.term.last_input, snap[1] / 1e9 if snap else 0)
        if time.time() - latest < self.quiet:
            return True
        if self._limit_asked():
            return True
        if cl.after_mark(tp, written)[1]:
            self.drop_limit(lim)  # シグナルファイルの後に利用者が入力した
            return True
        return self._stop_requested()

    def _limit_asked(self) -> bool:
        """上限のシグナルファイルの後に質問が出ているか（質問ファイルがあるか、シグナルファイルより後に尋ねた）。"""
        return os.path.exists(self.path(QUESTION_FILE)) or asked_after(self.dir, self.path(LIMIT_FILE))

    def _limit_interrupted(self, lim, written: float) -> bool:
        """上限のシグナルファイルの後に利用者が割り込んだか（質問が出た、または利用者が入力した）。"""
        return self._limit_asked() or cl.after_mark(lim.get("transcript_path") or "", written)[1]

    def _note_observed(self, lim, kind, resets):
        """上限の観測を 1 つのシグナルファイルにつき 1 回だけ記録する。"""
        if self.noted != lim["written_at"]:
            self.noted = lim["written_at"]
            if kind == REASON_AUTH:
                if self.account not in (None, ca.METERED):  # 登録済みのアカウントにだけ残す（#1727 の I6）
                    ca.note_auth_failed(self.account)
            else:
                ca.note_limit(self.account or "", kind, resets)

    def _wait_background(self, lim, kind, tp) -> bool:
        """背景の作業が残るなら 1 度だけ知らせて真を返す（終わるまで替えない）。"""
        if not cl.background_open(tp, time.time()):
            return False
        if self.told != lim["written_at"]:
            self.told = lim["written_at"]
            why = "認証に失敗した" if kind == REASON_AUTH else f"上限（{kind}）に達した"
            self.term.screen(f"ndf-relay: {why}が、背景の作業が残っているため終わるまで替えない")
        return True

    def limit_start(self, m) -> tuple[list[str], str, str, dict]:
        """上限で替えた次の区間の (引数, 記録のコマンド, 元の会話, cwd の元)。`ndf-next` があればそれを入力にする。

        無ければ、未達の `/goal` か、切り替えの理由に合う再開の文を入力にする。"""
        nxt = self.read_mark()
        if nxt is not None:
            return [nxt["command"]], nxt["command"], nxt.get("session_id") or "", nxt
        plan = m.get("_plan")
        reason = plan[1] if isinstance(plan, tuple) and len(plan) > 1 else None
        first = cl.resume_input(m.get("transcript_path") or "", resume_text(reason))
        sid = m.get("session_id") or ""
        return (["--resume", sid, first] if sid else [first]), first, sid, m
