"""起動先の型と、登録済みアカウントの子を関所で起動する手順（#3604 #3864。`wrapper.py` から行数の上限のため分けた）。

関所（`account_dir.gated`）: 子が claude として設定ディレクトリを使い始める前に、その pid を使用の届けに確定する
（詳細設計の「起動の関所」の 1〜5）。届けに書けないか claude になれなければ、子を止めて届けを消す（同期は打たない）。
"""

from __future__ import annotations

import contextlib
import os
import select
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping, NamedTuple, Optional

from ndf.contracts import limits
from ndf.domains.relay import account_choice, account_dir, key_helper
from ndf.domains.relay import account_registry as reg

# 起動先を試す起動（#3604 #3864 の決定 3・7）。理由の文に値・メールアドレス・パスを入れない
SKIP_LINE = "ndf-relay: アカウント {name} で起動しない（{why}）。次の候補で起動する"
SKIP_WHY = {
    "limited": "上限に当たっている",
    "unusable": "再登録が要るか、外した間か、登録が無い",
    "metered_unavailable": "従量の接続の設定が無い",
    "metered_unclassified": "従量の接続の設定の提供元を 1 つに決められない（API キーと別の資格情報やヘッダーの併用など）",
    "key_record": "API キーの払い出しの記録か、確かめた設定への固定のファイルを作れない",
    "metered_overridden": "設定ファイルか環境が、従量の接続の設定と別の提供元・資格情報・接続先・apiKeyHelper を入れる",
    "key_overridden": "起動引数の --settings が別の apiKeyHelper を持つ",
    "key_unresolved": "起動引数の --settings が読めないか、2 つ以上あるか、hooks の形が違う",
    "key_gate": "設定の変更を反映の前に拒む hook が働かない（Claude Code のバージョン・--bare・--safe-mode・hook の無効化・簡易の起動）",
    "tried": "この続きで認証に失敗した",
}
RELOGIN_LINE = "ndf-relay: アカウント {name} は再登録が要る。ndf relay account add {name} で登録し直す"
METERED_BROKEN_LINE = "ndf-relay: 従量の接続の設定が読めないか壊れている。ndf relay account check metered で確かめて設定し直す"
METERED_EMPTY_LINE = (
    "ndf-relay: 従量の接続の環境変数（NDF_SUPERVISE_CLAUDE_FALLBACK）が空なので、従量の接続へ替えない。KEY=VALUE を書くか、環境変数を外す"
)
METERED_FIXED_LINE = (
    "ndf-relay: 従量の接続の環境変数（NDF_SUPERVISE_CLAUDE_FALLBACK）がラッパーの決める変数（{names}）を持つので、"
    "従量の接続へ替えない。その変数を外す"
)
# 環境変数の従量の接続の設定を起動の前に確かめた知らせ（別冊 issue-3604-3864-design-detail-keys.md の提供元の確定。変数の名前だけを出し、値は出さない）
METERED_UNCLASSIFIED_LINE = (
    "ndf-relay: 従量の接続の環境変数（NDF_SUPERVISE_CLAUDE_FALLBACK）が読めないか、提供元を 1 つに決められないので、従量の接続へ替えない。"
    "ndf relay account check metered で確かめて環境変数を直す"
)
METERED_HEADERS_LINE = (
    "ndf-relay: 従量の接続の環境変数（NDF_SUPERVISE_CLAUDE_FALLBACK）が ANTHROPIC_API_KEY と ANTHROPIC_CUSTOM_HEADERS を併せて持つので、"
    "従量の接続へ替えない。API キーの接続ではヘッダーを足せない。ヘッダーの要らない接続先にするか、環境変数から外す"
)
SETTLE_LINE = "ndf-relay: アカウント {name} の設定を共有側へ書き戻せない（{why}）。次の起動の前の同期でそろえる"
UNAVAILABLE = "替え先と今のアカウント {name} のどちらも用意できない（{why}）。会話を送らずに止めた。直してから{again}"
RUN_AGAIN = " ndf relay run で続ける"
GATE_FAILED = "関所を開けた子が claude にならない（{why}）"


class Choice(NamedTuple):
    """起動の要求。`names` は順に試す名前、`fallback` は尽きたときの起動先（None は既定のログイン）。

    「未選択」と「既定のログインに確定」は `names` が空で `fallback` が None の形で、登録済みの候補を列挙しない。
    `fixed` が真なら `names` の起動先に固定する（同じ会話を続ける起動し直し・引継ぎ）。除外を当てずに用意を試し、
    用意も起動もできなければ既定のログインへ落とさずに止める（別の認証で同じ会話を `--resume` しない）。
    `last` が真なら `names` が尽きたら `fallback` も既定のログインも起動せずに止める（認証に失敗した今のアカウントへ戻さない）。
    """

    names: tuple[str, ...]
    fallback: Optional[str] = None
    fixed: bool = False
    last: bool = False


@dataclass(frozen=True)
class Target:
    """確定した起動先（`account` が None なら既定のログイン）。`env`・`argv` は子に渡す最終の環境と起動引数。

    `config_dir`・`generation`・`lease` は登録済みアカウントの用意の済んだ設定ディレクトリ・登録の世代・使用の届けの ID、
    `reason` は起動の理由（`first`・`cut`・`limit`）、`skipped` は飛ばした名前、`resolved` はその組の実効値。
    `helper` はキーの子の払い出しの記録（キーの子でなければ None。#3864 の決定 12）。`settings_file`・`pin_file` は
    `--settings` の合成の一時ファイルと確かめた設定への固定のファイル（子の終わりに消す）、`held` は最初の払い出しを見届けてから
    子の端末へ送る最初のプロンプト（キーの子は起動引数に載せない。別冊の「最初の払い出しまで送らない」）。
    """

    account: Optional[str]
    config_dir: Optional[Path]
    generation: str
    reason: str
    skipped: tuple[str, ...]
    lease: Optional[str]
    env: dict[str, str]
    argv: list[str]
    resolved: Optional[account_dir.Resolved]
    helper: Optional[key_helper.Record] = None
    settings_file: Optional[Path] = None
    pin_file: Optional[Path] = None
    held: Optional[str] = None

    def drop_files(self) -> None:
        """`--settings` の合成の一時ファイルと固定のファイル（とその払い出しのマーク）を消す（子の終わり・起動の失敗）。"""
        for path in (self.settings_file, self.pin_file):
            if path is not None:
                path.unlink(missing_ok=True)
        if self.pin_file is not None:
            self.pin_file.with_name(self.pin_file.name.removesuffix(".json") + ".ok").unlink(missing_ok=True)


def _read_status(fd: int, seconds: float) -> Optional[bytes]:
    """関所の起動の知らせを EOF まで読む。何も読まずに EOF なら b""（claude になった）、待ち切れたら None。"""
    deadline, got = time.monotonic() + seconds, b""
    while True:
        left = deadline - time.monotonic()
        ready, _, _ = select.select([fd], [], [], max(left, 0.0))
        if not ready:
            return None
        data = os.read(fd, 64)
        if not data:
            return got
        got += data


class GateFailed(OSError):
    """関所の子が claude になれなかった（`attach` の失敗・`execvp` の失敗・待ち切れ・関所の口の入出力の失敗）。

    子の終わりを確かめ、届けを解除した後に上げる。ラッパーはこの起動先を飛ばして次の候補へ進む。`code` は理由の符号。
    """

    def __init__(self, code: str):
        super().__init__(GATE_FAILED.format(why=code))
        self.code = code


class ChildLeft(OSError):
    """関所の子を強制終了しても終わりを確かめられない。届けは解除せずに残す（子が設定ディレクトリを使い続けうる）。"""

    def __init__(self, code: str, pid: int):
        super().__init__(GATE_FAILED.format(why=code))
        self.code, self.pid = code, pid


def _reaped(child: Any) -> bool:
    """子を強制終了し、`relay.child-exit` の秒のうちに終わりを確かめたら真。"""
    if not child.isalive() or child.terminate(force=True):
        return True
    deadline = time.monotonic() + limits.seconds("relay.child-exit")
    while child.isalive():
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)
    return True


def spawn(spawn_fn: Callable[..., Any], target: Target, cwd: Path, store: Path) -> Any:
    """子を起動する。登録済みアカウントは関所で起動し、使用の届けに子の pid を書けてから開ける。

    子を起動できなければ `OSError`（届けは解除済み）。関所の子が claude になれなければ、子の終わりを確かめて届けを解除してから
    `GateFailed`、終わりを確かめられなければ届けを残して `ChildLeft`。
    """
    if target.lease is None or target.account is None:
        return spawn_fn(target.argv, target.env, cwd)
    r, w = os.pipe()
    sr, sw = os.pipe()
    child: Any = None
    try:
        try:
            child = spawn_fn(account_dir.gated(target.argv, r, sw), target.env, cwd, (r, sw))
        finally:
            os.close(r)
            os.close(sw)
        why = _open_gate(child, target, store, w, sr)
    except OSError as exc:
        if child is None:  # 子が無い: 届けを消して起動の失敗として上げる
            account_dir.release(store, target.account, target.lease)
            raise
        why = f"gate_io errno {exc.errno}"
    finally:
        for fd in (w, sr):
            with contextlib.suppress(OSError):  # noqa: NDF402 ファイル: 閉じ済みの関所の口は閉じ直さない
                os.close(fd)
    if why is None:
        return child
    if not _reaped(child):
        raise ChildLeft(why, child.pid)
    account_dir.release(store, target.account, target.lease)
    raise GateFailed(why)


def _open_gate(child: Any, target: Target, store: Path, w: int, sr: int) -> Optional[str]:
    """届けに子の pid を書き、関所を開けて起動の知らせを待つ。claude になれば None、なれなければ理由の符号。"""
    why = account_dir.attach(store, str(target.account), str(target.lease), child.pid)
    if why is not None:
        return why
    os.write(w, b"1")
    os.close(w)
    got = _read_status(sr, limits.seconds("relay.gate-exec"))
    if got is None:
        return "gate_timeout"
    return f"launch_failed errno {got.decode(errors='replace')}" if got else None


def why_of(code: str, name: str) -> str:
    """理由の符号の文（値を含まない）。文の無い符号はそのまま出す。"""
    text = account_dir.REASONS.get(code) or SKIP_WHY.get(code)
    return text.format(name=name) if text else code


def _declared_note(store: Path, environ: Mapping[str, str]) -> Optional[str]:
    """環境変数の従量の接続の設定が使えなければ、その 1 行（`account_choice.metered_decl` と同じ検証。変数の名前だけを出す）。

    環境変数が無ければ None（保存した設定の壊れは `METERED_BROKEN_LINE` が受け持つ）。
    """
    raw = reg.declared_env(environ)
    if raw is None:
        return None
    try:
        reg.parse_declared(raw)
    except reg.DeclError as exc:
        if exc.code == "empty":
            return METERED_EMPTY_LINE
        if exc.code == "fixed":
            return METERED_FIXED_LINE.format(names="・".join(exc.names))
        return METERED_HEADERS_LINE if exc.code == "headers" else METERED_UNCLASSIFIED_LINE
    return None


def start_notes(store: Path, environ: Mapping[str, str]) -> list[str]:
    """ラッパーの起動で出す知らせ。再登録が要るアカウントを 1 つにつき 1 行、従量の接続の設定が壊れていれば 1 行、
    環境変数の従量の接続の設定が読めないか提供元を 1 つに決められなければ 1 行（登録済みアカウントで起動しても、退避先が使えないことを先に伝える）。
    """
    lines = [RELOGIN_LINE.format(name=n) for n, a in account_choice.registered(store).items() if a.needs_relogin]
    lines += [METERED_BROKEN_LINE] if reg.load_metered(store)[1] is not None else []
    note = _declared_note(store, environ)
    return lines + ([note] if note is not None else [])


def named(account: Optional[str]) -> tuple[str, ...]:
    """起動先の名前の組（既定のログインは空）。"""
    return () if account is None else (account,)
