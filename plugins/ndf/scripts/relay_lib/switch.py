"""区間のアカウント（#1389）: 区間ごとのアカウントの選び方・上限シグナルファイルの判定・定期の確認。

`Relay`（`run.py`）が `AccountSwitch` を継いで使う。登録済みのアカウントが 2 つ以上あれば、区間ごとに最も上限から
遠いアカウントで起動し、子の応答が利用上限で終わった（`limit.json`）ら別のアカウントで次の区間を起動する。
`UsageWatch` が今のアカウントの使用率を定期的に読み（推論なし）、閾値を超えたら次のカットポイントで替える。
すべて上限なら宣言された従量の接続へ移り、上限の外れたアカウントへ次のカットポイントで戻す。
置き場と選び方は `lib/claude_accounts.py`、文言の読みは `lib/claude_usage.py` が持つ。
"""

from __future__ import annotations

import math
import os
import queue
import threading
import time

from . import claude as cl
from .common import LIMIT_FILE, QUESTION_FILE, load_json, parse_iso, remove, stamp
from .mark import asked_after

import claude_accounts as ca  # noqa: E402,I001  common が lib/ を sys.path に置く
import claude_usage as cu  # noqa: E402

# 上限シグナルファイルのうち、アカウントを替える引き金になる `error`（`authentication_failed` は別に扱う）
LIMIT_ERRORS = ("rate_limit", "billing_error")

# 切り替えの理由（`pick` が決め、画面の 1 行と記録の `reason` に出る）。上限の種類（`cu.KINDS`）も理由になる
REASON_THRESHOLD = "threshold"  # 使用率が閾値を超えた
REASON_START = "start"  # 既定のログインから最初のアカウントへ
REASON_UNUSABLE = "unusable"  # 今のアカウントが使えない
REASON_RECOVERED = "recovered"  # 従量の接続からアカウントへ戻す
REASON_LIMITED = "limited"  # すべて上限で従量の接続へ
REASON_AUTH = "auth"  # 認証が通らなかった


_RECOVERED, _LIMIT = object(), object()  # 理由の文字列と重ならない表の鍵


def _msg_recovered(sw, prev, to, reason) -> tuple[str, dict]:
    return f"{ca.account_label(to)}の上限が外れたため、従量の接続からアカウント {to} へ戻して続ける", {"reason": REASON_RECOVERED}


def _msg_threshold(sw, prev, to, reason) -> tuple[str, dict]:
    n = sw.watch.due if sw.watch is not None and sw.watch.due is not None else 0
    return f"{prev} の使用率が {n:.0f}% に達したため、アカウントを {ca.account_label(to)}へ替える", {"usage": round(n)}


def _msg_limit(sw, prev, to, reason) -> tuple[str, dict]:
    return f"利用上限（{reason}）に達したため、アカウントを {prev} から {ca.account_label(to)}へ替えて続ける", {}


def _msg_auth(sw, prev, to, reason) -> tuple[str, dict]:
    return f"認証が通らなかったため、アカウントを {prev} から {ca.account_label(to)}へ替えて続ける", {}


def _msg_default(sw, prev, to, reason) -> tuple[str, dict]:
    return f"アカウントを {prev or '既定のログイン'} から {ca.account_label(to)}へ替える", {}


# 切り替えの理由 → (画面の 1 行, 記録の行へ足す項目) を作る関数（`AccountSwitch._switch_message` が引く）
_SWITCH_MESSAGES = {
    _RECOVERED: _msg_recovered,
    REASON_THRESHOLD: _msg_threshold,
    _LIMIT: _msg_limit,
    REASON_AUTH: _msg_auth,
}


# 切り替えの理由 → 次のセッションの最初の入力（未達の `/goal` が無いとき。#1576 の決定 16）
_RESUME_TEXTS = {
    _LIMIT: "利用上限でアカウントを替えた。中断したところから続ける",
    REASON_AUTH: "認証が通らなかったためアカウントを替えた。中断したところから続ける",
    REASON_THRESHOLD: "使用率が切り替えの閾値を超えたためアカウントを替えた。中断したところから続ける",
    REASON_RECOVERED: "上限が外れたためアカウントへ戻した。中断したところから続ける",
}
_RESUME_DEFAULT = "アカウントを替えた。中断したところから続ける"


def resume_text(reason: str | None) -> str:
    """切り替えの理由に合う再開の文。上限の種類（`cu.KINDS`）はどれも同じ文にまとめる。"""
    return _RESUME_TEXTS.get(_LIMIT if reason in cu.KINDS else reason, _RESUME_DEFAULT)


class UsageWatch:
    """定期の確認（別スレッド）。今のアカウントの使用率を間隔ごとに読み、閾値を超えたら `due` を立てる。
    従量の接続で動く間は登録済みのアカウントすべてを読み、戻せるものがあれば `recover` を立てる。

    取得先は推論を呼ばず、間隔（`NDF_ACCOUNT_CHECK_INTERVAL`）の中なら保存した残量を使う。取得に失敗しても
    中継（主のスレッド）を止めない。画面の 1 行は `lines` に積み、主のスレッドが出す。"""

    def __init__(self, relay):
        self.relay = relay
        self.due: float | None = None  # 閾値を超えた今のアカウントの使用率
        self.recover: str | None = None  # 従量の接続から戻せるアカウント
        self.lines: queue.SimpleQueue[str] = queue.SimpleQueue()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self.run, name="ndf-usage-watch", daemon=True)

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.stopped.set()

    def reset(self) -> None:
        self.due = self.recover = None

    def run(self) -> None:
        while not self.stopped.wait(max(ca.check_interval(), 1.0)):
            try:
                self.check()
            except Exception:  # 確認の失敗で中継を止めない
                continue

    def check(self) -> None:
        cur, thr = self.relay.account, ca.switch_at()
        if cur == ca.METERED:
            c = ca.choose()  # 期限を過ぎたトークンだけを、更新の排他を取れたときに更新する
            if c.recoverable(thr):
                if self.recover != c.name:
                    self.lines.put(f"{c.name} の上限が外れた。次のカットポイントで従量の接続から戻す")
                self.recover = c.name
            else:
                self.recover = None
            return
        if not cur or thr >= 100 or self.due is not None:
            return
        u = ca.usage(cur, False)  # 動いているセッションのトークンは claude が更新する（#1576 の I8）
        score = u.score() if u else None
        if score is not None and score >= thr:
            self.due = score
            self.lines.put(f"{cur} の使用率が {score:.0f}% を超えた。次のカットポイントで替える")


class AccountSwitch:
    """`Relay` が継ぐ、区間のアカウントの扱い。`Relay` の `account`・`multi`・`watch`・`env`・`term`・`limit`・
    `section` などを使う。"""

    def pick(self, kind: str | None) -> tuple[str | None, str | None, ca.Choice | None]:
        """次の区間のアカウントを決める。(名前か `metered` か None, 理由, 選んだ結果)。

        `kind` は上限の種類（上限の後。None が返れば切り替えずに子を残す）か None（区間の起動。None は今と同じ環境）。
        区間の起動では、今のアカウントが使えて閾値未満なら替えない（使用率の小さな差で区間ごとに行き来しない）。"""
        cur, thr = self.account, ca.switch_at()
        declared = bool(ca.fallback_env(self.env))
        if kind is not None:
            return self._pick_after_limit(kind, cur, declared)
        if cur == ca.METERED:
            return self._pick_from_metered(thr)
        return self._pick_for_section(cur, thr, declared)

    def _pick_for_section(self, cur: str | None, thr: float, declared: bool) -> tuple[str | None, str | None, ca.Choice | None]:
        """区間の起動で、今のアカウント（か既定のログイン）から次を選ぶ。"""
        due = self.watch.due if self.watch is not None else None
        usable, score, left = False, None, None
        if cur is not None:
            usable, score, left = self._current_standing(cur)
            if usable and due is None and (score is None or score < thr):
                return cur, None, None
        c = ca.choose(exclude={cur} if cur else set(), keep=self.keep())
        if c.name:
            if usable and self.no_better(c, score, left):
                return cur, None, c
            return c.name, REASON_THRESHOLD if usable else (REASON_START if cur is None else REASON_UNUSABLE), c
        if usable:
            return cur, None, c
        if declared:
            return ca.METERED, REASON_LIMITED, c
        return cur, None, c

    def stop_accounts(self) -> None:
        """ラッパーの終了時: 定期の確認を止め、今のアカウントの .claude.json の共有する部分を書き戻す（E12）。"""
        if self.watch is not None:
            self.watch.stop()
        self.settle_account(self.account)

    def settle_account(self, name: str | None) -> None:
        """アカウント `name` のセッションの後の書き戻し（登録が 1 つ以下・従量の接続・既定のログインでは何もしない）。"""
        if self.multi and name and name != ca.METERED:
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

    def _pick_after_limit(self, kind: str, cur: str | None, declared: bool) -> tuple[str | None, str | None, ca.Choice]:
        """上限と認証の失敗の後の選び直し（今のアカウントを除く）。None が返れば切り替えずに子を残す。"""
        c = ca.choose(exclude=set() if cur in (None, ca.METERED) else {cur}, keep=self.keep())
        if c.name:
            return c.name, REASON_RECOVERED if cur == ca.METERED else kind, c
        if declared and cur != ca.METERED:
            return ca.METERED, kind, c
        return None, kind, c

    @staticmethod
    def _pick_from_metered(thr: float) -> tuple[str | None, str | None, ca.Choice]:
        """従量の接続で動く区間の起動。戻せるアカウントがあれば戻す。"""
        c = ca.choose()
        if c.recoverable(thr):
            return c.name, REASON_RECOVERED, c
        return ca.METERED, None, c

    @staticmethod
    def _current_standing(cur: str) -> tuple[bool, float | None, float | None]:
        """今のアカウントの (使えるか, 使用率, 残りの量)。トークンは更新しない（claude が更新する）。"""
        u = ca.usage(cur, False)
        acc = ca.load_account(cur)
        now = time.time()
        usable = acc is not None and not acc.needs_relogin and not acc.auth_held(now) and acc.limited_until(now) is None
        score = u.score() if u else None
        left = acc.remaining() if acc is not None else None
        return usable, score, left

    @staticmethod
    def no_better(c: ca.Choice, score: float | None, left: float | None) -> bool:
        """候補 `c` が今のアカウント（使用率 `score`・残りの量 `left`）より良くないか（#1453 の I9）。

        両方の残りの量が分かれば残りの量で、どちらかが不明なら使用率で比べる。"""
        if left is not None and c.remaining is not None:
            return c.remaining <= left
        return score is not None and c.score is not None and c.score >= score

    def tell_account(self, prev: str | None, to: str | None, reason: str | None, choice: ca.Choice | None) -> None:
        """区間を起動したアカウントを画面の 1 行と記録（`account` の行）に残す（I12）。トークンと宣言の値は書かない。"""
        if to == prev and self.section > 1:
            return
        if to is None:
            return
        if to == ca.METERED:
            keys = list(ca.fallback_env(self.env))
            self.term.screen("ndf-relay: " + self.all_limited(choice) + f"。従量の接続（{self.keys_text(keys)}）へ替えて続ける")
            self.log(
                event="account", section=self.section, reason=reason, **{"from": prev}, to=to, keys=keys, earliest=self.earliest(choice)
            )
            return
        if self.section == 1:
            self.term.screen(f"ndf-relay: アカウント {ca.account_label(to)}で起動する")
            return
        row = {"event": "account", "section": self.section, "reason": reason, "from": prev, "to": to}
        line, extra = self._switch_message(prev, to, reason)
        row.update(extra)
        self.term.screen("ndf-relay: " + line)
        self.log(**row)

    def _switch_message(self, prev: str | None, to: str, reason: str | None) -> tuple[str, dict]:
        """2 つ目以降の区間でアカウントを替えたときの (画面の 1 行, 記録の行へ足す項目)。理由ごとに決まる。"""
        # 従量の接続から戻すときは理由に依らず先に効く。上限の種類はどれも同じ文にまとめる
        key = _RECOVERED if prev == ca.METERED else _LIMIT if reason in cu.KINDS else reason
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

    @staticmethod
    def keys_text(keys: list[str]) -> str:
        return keys[0] + (f" ほか {len(keys) - 1} つ" if len(keys) > 1 else "") if keys else "宣言"

    def flush_lines(self) -> None:
        """定期の確認が積んだ 1 行を出す（画面へ書くのは主のスレッドだけ）。"""
        if self.watch is None:
            return
        while True:
            try:
                line = self.watch.lines.get_nowait()
            except queue.Empty:
                return
            self.term.screen("ndf-relay: " + line)

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
        auth = lim.get("error") == "authentication_failed" and self.account not in (None, ca.METERED) and self.auth_section != self.section
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
            self.term.screen("ndf-relay: " + self.all_limited(choice) + "。子はこのまま残す")
            self.drop_limit(lim)
            return None
        if not self._may_start(written):
            return None
        return {**lim, "_kind": "limit", "_snap": snap, "_plan": (to, reason, choice)}

    def _limit_relevant(self, lim, auth) -> bool:
        """替える対象の上限か。上限でない失敗（overloaded など）と、登録が 1 つ以下のときは読み捨てて偽を返す。"""
        if not self.multi or (lim.get("error") not in LIMIT_ERRORS and not auth):
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
                ca.note_auth_failed(self.account or "")
            else:
                ca.note_limit(self.account or "", kind, resets)

    def _wait_background(self, lim, kind, tp) -> bool:
        """背景の作業が残るなら 1 度だけ知らせて真を返す（終わるまで替えない）。"""
        if not cl.background_open(tp, time.time()):
            return False
        if self.told != lim["written_at"]:
            self.told = lim["written_at"]
            self.term.screen(f"ndf-relay: 上限（{kind}）に達したが、背景の作業が残っているため終わるまで替えない")
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
