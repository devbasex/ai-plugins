"""`ndf relay account` のサブコマンド（子の claude を起動するアカウントの指定と、アカウント・従量の接続の登録。#3569）。

`relay/cli.py` から分けた。結果 JSON の tool の名前は `ndf relay` と同じ。
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
import shutil
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Callable, Iterator, Mapping, NamedTuple, Optional

from ndf import cli, identity
from ndf.contracts import result
from ndf.domains.relay import account_login, account_metered, account_registry, accounts, key_helper

# 結果 JSON の tool の名前はサブコマンドの名前（SSOT は `cli.COMMANDS`）
TOOL = cli.tool_of(f"{__package__}.cli")
ACCOUNT_COMMANDS = {
    "use": "子の claude を起動するアカウントを指定する",
    "show": "今選ばれるアカウント（環境変数 → 指定）を出す",
    "clear": "指定を消す",
    "api-key-helper": "API キーの子の apiKeyHelper（キーだけを標準出力へ出す。ラッパーが起動引数に載せる）",
    "config-gate": "API キーの子の ConfigChange の hook（確かめた設定から接続先などを変える保存を反映の前に拒む）",
}


def run(args: argparse.Namespace) -> int:
    """`ndf relay account` の振り分け（指定の use・show・clear と、登録のサブコマンド）。"""
    if args.account_action == "api-key-helper":
        return _acc_key_helper(args)
    if args.account_action == "config-gate":
        return _acc_config_gate(args)
    if args.account_action in ACCOUNT_REGISTRY_HANDLERS:
        return _account_registry(args)
    state_dir = identity.load().state_dir
    if args.account_action == "use":
        accounts.use(state_dir, args.name)
        # 利用者が直して指定したアカウントを、外した間の残りで飛ばさない（I8）。指定は済んだので、解けなくても成功で返して警告に分ける
        warnings = _cleanup(lambda: accounts.unhold(state_dir, args.name), "外した間を解けなかった（指定は保存した。打ち直してよい）")
        return result.emit(result.result(TOOL, result.OK, f"アカウント {args.name} を指定した", warnings=warnings))
    if args.account_action == "clear":
        done = accounts.clear(state_dir)
        return result.emit(result.result(TOOL, result.OK, "指定を消した" if done else "指定は無かった"))
    name = accounts.chosen(os.environ, state_dir)
    return result.emit(result.result(TOOL, result.OK, f"選ばれるアカウント: {name or '（claude の既定）'}", items=[{"account": name}]))


def _metered_key(environ: Mapping[str, str], fp: str) -> Optional[str]:
    """環境変数の設定が API キーの設定で、キーの SHA-256 が `fp` と同じならキーの値。無い・解けない・違えば None。"""
    raw = account_registry.declared_env(environ)
    if raw is None:
        return None
    try:  # ラッパーの `_metered` と同じ検証（詳細設計書の「従量の接続の設定の検証」）
        decl = account_registry.parse_declared(raw)
    except account_registry.DeclError:  # noqa: NDF401 利用者の入力: 使えない設定ではキーを出さない
        return None
    if account_registry.metered_kind(decl) != account_registry.API_KEY_KIND:
        return None
    key = decl["ANTHROPIC_API_KEY"]
    return key if key_helper.fingerprint(key) == fp else None


def _acc_key_helper(a: argparse.Namespace) -> int:
    """`api-key-helper metered <指紋>`。合うキーだけを標準出力へ出し、ほかは何も出さずに終了コード 1（別冊の「環境変数の設定の API キー」）。

    記録先と ID は起動引数の `--record`・`--id` だけから決め（両方か、どちらも無いか）、ある起動は記録の錠の中で払い出しを照らす。
    払い出しのたびに `--pin` の固定のファイルの設定と今の設定を比べ、違えば出さない（記録には `cause: pin` の行。別冊の
    「確かめた設定への固定」）。`--record` の無い起動（入れ子の `ndf plan`）は、出す前に同じ名前の `.pin.ok` を作る。
    標準エラーと記録にキーの値も指紋も出さない。
    """
    if (a.record is None) != (a.id is None):
        return 1
    key = _metered_key(os.environ, a.fingerprint) if a.target == account_registry.METERED else None

    def pinned() -> bool:
        return a.pin is not None and key_helper.pin_ok(Path(a.pin))

    if a.record is not None:  # 照合は記録の錠の中で打つ（錠を待つ間に変わった設定で出さない）
        if not key_helper.pay(Path(a.record), a.id, key, time.time, pinned=pinned):
            return 1
    elif key is None or not pinned() or not _mark_payout(Path(a.pin)):
        return 1
    if key is None:
        return 1
    sys.stdout.write(key)
    return 0


def _mark_payout(pin: Path) -> bool:
    """入れ子の `ndf plan` の払い出しのマーク（固定のファイルと同じ名前の `.pin.ok`。0600）を作る。作れなければ偽。"""
    mark = pin.with_name(pin.name.removesuffix(".json") + ".ok")
    try:
        os.close(os.open(mark, os.O_WRONLY | os.O_CREAT | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600))
    except OSError:  # noqa: NDF401 ファイル: マークを作れなければキーを出さない
        return False
    return True


def _acc_config_gate(a: argparse.Namespace) -> int:
    """`config-gate --pin <固定のファイル>`（`ConfigChange` の hook）。比べる範囲を変える保存を終了コード 2 で拒み、ほかは 0。

    標準入力の `source` と `file_path` で決める。`file_path` が無い・固定のファイルに無いパス・読めない・JSON の object でない・
    比べる範囲の SHA-256 が固定のファイルと違うとき、標準入力と固定のファイルを読めないときに拒む。拒んだら、記録先のある起動では
    記録へ `gate` の行を足す（別冊の「反映の前に拒む」）。拒めない管理の設定（`policy_settings`）は固定のファイルの全体を照らし、
    外れれば同じく `gate` の行を足し（ラッパーが `must_end`・`end_now` を立てて子を止める）、hook の timeout まで返らない（`key_helper.hold`）。
    """
    if (a.record is None) != (a.id is None):
        return 2
    try:
        event = json.loads(sys.stdin.read())
    except (OSError, ValueError):  # 利用者の入力: 読めない入力の変更は通さない
        event = None
    source = event.get("source") if isinstance(event, dict) else None
    if source == "skills":  # skills は比べる範囲の項目を持てない
        return 0
    if source == "policy_settings":
        # 管理の設定の変更は Claude Code が拒ませないので、固定のファイルの全体（ファイルと `managed-settings.d` の一覧）を照らし、
        # 外れれば記録へ `gate` の行を足してラッパーに止めさせる。終了コード 2 でも反映されるので、ラッパーが子を止め終えるまで
        # hook を返さない（返した後の反映で、変わった接続先へキャッシュのキーと会話を送らせない）
        if key_helper.pin_ok(Path(a.pin)):
            return 0
        if a.record is not None:
            key_helper.gate(key_helper.Record(Path(a.record), a.id, (0, 0)), str(source))
            key_helper.hold()
        return 2
    path = event.get("file_path") if isinstance(event, dict) else None
    want = key_helper.pin_has(Path(a.pin), path) if isinstance(path, str) and path else None
    if want is not None and key_helper.file_sha(Path(path)) == want:
        return 0
    if a.record is not None:
        key_helper.gate(key_helper.Record(Path(a.record), a.id, (0, 0)), str(source or "設定ファイル"))
    return 2


def _capacity_arg(v: str) -> Optional[float]:
    """スロットの引数（正の数か `-`）。`-` は None。"""
    if v == "-":
        return None
    x = account_registry.parse_number(v)
    if x is None or not 0 < x < float("inf"):
        raise account_registry.AccountError(f"スロットは正の数（USD）か - で渡す: {v}")
    return x


def _cleanup(run: Callable[[], None], what: str) -> list[str]:
    """確定した操作の後の記録の後始末を打つ。失敗は結果の警告の 1 行にし、確定した操作を失敗にしない（打ち直しで認証を繰り返させない）。"""
    try:
        run()
    except OSError as exc:  # 錠の待ちきれない（TimeoutError）・消せない
        return [f"{what}: {exc}"]
    return []


def _forget_after(state_dir: Path, store: Path, name: str, warnings: list[str]) -> Callable[[Optional[str]], None]:
    """登録簿の排他の中で、操作した登録の前の世代の記録を消す呼び出し（I8）。失敗は `warnings` に足す。"""

    def run(gone: Optional[str]) -> None:
        if gone is not None:
            what = "前の登録の使用率・外した間・上限の記録を消せなかった（登録の操作は済んだ。打ち直さない）"
            warnings.extend(_cleanup(lambda: accounts.forget(state_dir, store, name, gone), what))

    return run


@contextmanager
def _fetching(store: Path, name: str) -> Iterator[None]:
    """世代を変える操作の前に、同じ名前の取得の排他を待って取る（I16）。取れなければ `AccountLockTimeout`。

    形の違う名前は排他を取らずに通し、名前の検査は呼んだ操作に任せる。
    """
    if not account_registry.valid_name(name):
        yield
        return
    with accounts.fetch_lock(store, name, accounts.fetch_wait()) as got:
        if not got:
            raise account_registry.AccountLockTimeout("使用率の取得が終わらない。少し待ってから打ち直す")
        yield


def _acc_add(store: Path, a: argparse.Namespace) -> int:
    code = (sys.stdin.readline() if a.code == "-" else a.code) if a.code is not None else None
    warnings: list[str] = []
    forget = _forget_after(identity.load().state_dir, store, a.name, warnings)
    with _fetching(store, a.name):
        got = account_login.add(store, a.name, code, os.environ, settle=lambda old, _new: forget(old))
    if got.needs_code:
        detail = f"認可の URL: {got.url} をブラウザで開き、出た認可コードを {got.expires_at} までに渡す: printf '%s\\n' '<コード>' | ndf relay account add {a.name} --code -"
        return result.emit(
            result.stopped(
                TOOL, "input_missing", detail, items=[{"name": a.name, "kind": "oauth", "url": got.url, "expires_at": got.expires_at}]
            )
        )
    who = f"{got.email}・{got.org_name}" if got.org_name else got.email
    return result.emit(
        result.result(
            TOOL,
            result.OK,
            f"登録した: {a.name}（{who}）",
            items=[{"name": a.name, "kind": "oauth", "email": got.email, "org_name": got.org_name}],
            warnings=warnings,
        )
    )


def _acc_bedrock(store: Path, a: argparse.Namespace) -> int:
    asked = account_metered.Target(a.profile or "", a.region or "", a.model or "")  # noqa: NDF501 利用者の入力: 省いた欄は空で渡し、候補を添えて止める
    row = account_metered.add_bedrock(store, os.environ, asked, a.yes)
    return result.emit(
        result.result(TOOL, result.OK, f"登録した: 従量の接続（Bedrock・{'・'.join(row['details'].values())}）", items=[row])
    )


def _acc_api_key(store: Path, a: argparse.Namespace) -> int:
    usd = account_metered.amount(a.monthly)
    key = account_metered.read_key(a.key_file, "" if a.key_file else _stdin_key())
    row = account_metered.add_api_key(store, os.environ, account_metered.KeyRequest(a.name, a.account, key, usd))
    note = "組織に買ったクレジットか自動チャージがあると、月の API クレジットを使い切った後もそちらから払われる（Console の spend limit で抑える）"
    return result.emit(result.result(TOOL, result.OK, f"登録した: {a.name}（アカウント {a.account}）", items=[row], warnings=[note]))


def _stdin_key() -> str:
    """標準入力のキーの 1 行。端末からは旧版と同じく表示せずに読む（画面と端末の記録へ残さない）。"""
    if sys.stdin.isatty():
        return getpass.getpass("API キーの値（表示しない）: ", stream=sys.stderr)
    return sys.stdin.readline()


def _acc_monthly(store: Path, a: argparse.Namespace) -> int:
    row = account_metered.set_monthly(store, a.name, account_metered.amount(a.usd, allow_zero=True))
    usd = row["monthly_usd"]
    return result.emit(
        result.result(TOOL, result.OK, f"月の額を外した: {a.name}" if usd is None else f"月の額: {a.name} {usd:g} ドル", items=[row])
    )


def _acc_use(store: Path, a: argparse.Namespace) -> int:
    row = account_metered.set_use(store, a.mode, account_metered.weekly_at(a.weekly_at))
    return result.emit(result.result(TOOL, result.OK, f"API キーの使い方: {row['use']}（閾値 {row['weekly_at']:g}%）", items=[row]))


def _acc_list(store: Path, a: argparse.Namespace) -> int:
    state_dir = identity.load().state_dir
    now = time.time()
    rows, warnings = account_registry.list_rows(
        store, os.environ, accounts.pinned(state_dir), lambda generations: accounts.usages(state_dir, store, now, generations)
    )
    metrics = {"accounts": sum(r["kind"] == "oauth" for r in rows), "keys": sum(r["kind"] == "api_key" for r in rows)}
    summary = f"登録: アカウント {metrics['accounts']} 件・API キー {metrics['keys']} 件" if rows else "登録済みのアカウントは無い"
    return result.emit(result.result(TOOL, result.OK, summary, items=rows, metrics=metrics, warnings=warnings))


def _acc_capacity(store: Path, a: argparse.Namespace) -> int:
    row = account_registry.set_capacity(store, a.name, _capacity_arg(a.five), _capacity_arg(a.seven))
    return result.emit(result.result(TOOL, result.OK, f"スロットの大きさを定義した: {a.name}", items=[row]))


def _acc_remove(store: Path, a: argparse.Namespace) -> int:
    name = a.target
    valid = account_registry.valid_name(name)
    # 残りは 1 度だけ見て、経路の判定と remove_account の両方に使う。読み直すと、間にほかの端末が残りを片付けて登録し直したとき
    # 打ち直しの削除が新しい登録を外してしまう
    seen = valid and account_registry.removing(store, name)
    oauth = valid and (seen or name in account_registry.names(store))
    if valid and not oauth:
        # 残りを見てから一覧を読むまでに、ほかの端末の削除が登録を残りの場所へ移したかもしれない。登録と残りの両方が
        # 見えないときだけ、取得の排他と登録の排他の下で見直してから API キーの削除へ回す（最初に見た残りはそのまま使う）
        with _fetching(store, name), account_registry.locked(store, name):
            seen = account_registry.removing(store, name)
            appeared = not seen and name in account_registry.names(store)
        if appeared:
            # 待つ間にほかの端末の追加（account add の --code）が同じ名前を登録した。その資格情報は消さず、何も変えずに止まる
            detail = f"取得と登録の排他を待つ間に同じ名前の OAuth の登録ができた（何も外していない）。外すなら ndf relay account remove {name} をもう一度打つ"
            return result.emit(result.stopped(TOOL, "store_in_use", detail, items=[{"name": name, "kind": "oauth"}]))
        oauth = seen
    if not oauth:
        row = account_metered.remove(store, name)
        return result.emit(result.result(TOOL, result.OK, f"外した: {name}", items=[row]))

    def release() -> bool:
        # 登録の排他の中で呼ばれる。古い claude ではログアウトできないまま登録だけを外さない。登録も symlink も残して止まる（#3691）
        claude = shutil.which(account_login.claude_adapter.CLAUDE)
        if claude is not None:
            account_login.require_claude(claude, account_login.auth_env(os.environ, account_registry.account_dir(store, name)))
        account_registry.detach(store, name)  # logout が設定ディレクトリの中を消す前に、共有の項目への symlink を外す
        return account_login.logout(store, name, os.environ)

    warnings: list[str] = []
    # 残りは取得の排他を待つ前に見た（上の seen）。待つ間にほかの端末が残りを片付けても、この呼び出しは残りの後始末だけで返す
    with _fetching(store, name):
        forget = _forget_after(identity.load().state_dir, store, name, warnings)
        got = account_registry.remove_account(store, name, release, forget, seen=seen)
    item = {"name": name, "kind": "oauth"}
    if got.leftovers:
        # 前の削除が消し残した世代だけを消した。今の登録は外していない（外すのは残りが無くなった後の削除）
        if got.kept:
            warnings.append(
                f"同じ名前の今の登録は残した（前の削除の後に登録し直された）。外すなら ndf relay account remove {name} をもう一度打つ"
            )
        return result.emit(result.result(TOOL, result.OK, f"外した登録の残りを消した: {name}", items=[item], warnings=warnings))
    if not got.logged_out:
        warnings.insert(0, "claude auth logout が通らなかった（登録は外した）")
    return result.emit(result.result(TOOL, result.OK, f"外した: {name}", items=[item], warnings=warnings))


def _acc_check(store: Path, a: argparse.Namespace) -> int:
    row = account_metered.check(store, os.environ, a.target)
    return result.emit(result.result(TOOL, result.OK, f"呼べる: {a.target}", items=[row]))


class AccountSub(NamedTuple):
    """アカウントの登録のサブコマンドの 1 つ（名前・説明・ハンドラ）。"""

    name: str
    text: str
    handler: Callable[[Path, argparse.Namespace], int]


# アカウントと従量の接続の登録のサブコマンド（旧い保存先と同じ。#3569）
ACCOUNT_REGISTRY = (
    AccountSub("add", "OAuth のアカウントを登録する（--code なしで認可の URL を出し、--code で認可コードを渡す）", _acc_add),
    AccountSub("add-bedrock", "Bedrock の従量の接続を確かめて保存する", _acc_bedrock),
    AccountSub("add-api-key", "API キーを確かめて保存する（値は標準入力か --key-file）", _acc_api_key),
    AccountSub("api-key-monthly", "API キーの月の額を定義する（0 で外す）", _acc_monthly),
    AccountSub("api-key-use", "API キーへ切り替える使い方と閾値を読む・書く", _acc_use),
    AccountSub("list", "登録したアカウント・従量の接続・API キーの一覧", _acc_list),
    AccountSub("capacity", "アカウントの 5 時間と週のクォータの大きさを定義する（- で外す）", _acc_capacity),
    AccountSub("remove", "アカウント・従量の接続（metered）・API キーの登録を外す", _acc_remove),
    AccountSub("check", "従量の接続（metered）か API キーが呼べるかを確かめる（状態は記録しない）", _acc_check),
)
ACCOUNT_REGISTRY_HANDLERS = {sub.name: sub.handler for sub in ACCOUNT_REGISTRY}


def _account_registry(args: argparse.Namespace) -> int:
    """登録のサブコマンドを動かす。使い方の誤りは invalid、使用の届けのある名前の登録・削除は refused、待ちきれない排他は store_in_use、書けない保存先は unknown で止まる。"""
    try:
        return ACCOUNT_REGISTRY_HANDLERS[args.account_action](account_registry.store_dir(os.environ), args)
    except account_metered.MeteredError as exc:
        return result.emit(result.stopped(TOOL, exc.kind, str(exc), items=exc.items))
    except account_login.LoginError as exc:
        return result.emit(result.stopped(TOOL, exc.kind, str(exc)))
    except account_registry.NotRegistered as exc:
        return result.emit(result.stopped(TOOL, "refused", str(exc)))
    except account_registry.AccountInUse as exc:
        # その名前の設定ディレクトリを動いている子か `ndf plan` が使っている（I10）。何も変えずに止める
        return result.emit(result.stopped(TOOL, "refused", str(exc)))
    except account_registry.AccountLockTimeout as exc:
        return result.emit(result.stopped(TOOL, "store_in_use", str(exc)))  # 書かずに返す。打ち直さず人へ戻す
    except OSError as exc:
        return result.emit(result.stopped(TOOL, "unknown", f"アカウントの保存先へ書けない: {exc}"))


def parser(sub: Any, help_text: str) -> None:
    """`account` のサブコマンドの parser を `sub` へ足す（`help_text` は `ndf relay` の一覧の説明）。"""
    texts = {**ACCOUNT_COMMANDS, **{s.name: s.text for s in ACCOUNT_REGISTRY}}
    acc = sub.add_parser("account", help=help_text, description="サブコマンド: " + " / ".join(f"{k}（{v}）" for k, v in texts.items()))
    acc_sub = acc.add_subparsers(dest="account_action", required=True)
    p = {name: acc_sub.add_parser(name, help=text, description=text) for name, text in texts.items()}
    p["use"].add_argument("name")
    p["api-key-helper"].add_argument("target", help="metered")
    p["api-key-helper"].add_argument("fingerprint", help="キーの値の SHA-256 の 16 進")
    p["api-key-helper"].add_argument("--record", help="払い出しの記録（ラッパーが作る）")
    p["api-key-helper"].add_argument("--id", help="helper の ID")
    p["api-key-helper"].add_argument("--pin", help="確かめた設定への固定のファイル（ラッパーと ndf plan が作る）")
    p["config-gate"].add_argument("--pin", required=True, help="確かめた設定への固定のファイル")
    p["config-gate"].add_argument("--record", help="払い出しの記録（ラッパーが作る）")
    p["config-gate"].add_argument("--id", help="helper の ID")
    p["add"].add_argument("name", help="アカウントの名前（英小文字・数字・- と _ の 32 字まで）")
    p["add"].add_argument("--code", help="認可コード。- なら標準入力の 1 行（引数と記録に残さない）")
    for opt, text in (("--profile", "AWS のプロファイル"), ("--region", "地域（省けばプロファイルの region）"), ("--model", "モデルの ID")):
        p["add-bedrock"].add_argument(opt, help=text)
    p["add-bedrock"].add_argument("--yes", action="store_true", help="確かめが通れば保存する（前の設定を置き換える）")
    p["add-api-key"].add_argument("name", help="API キーの名前")
    p["add-api-key"].add_argument("--account", required=True, help="結び付ける登録済みアカウント")
    p["add-api-key"].add_argument("--key-file", help="キーの値を 1 行目に持つファイル（省けば標準入力の 1 行）")
    p["add-api-key"].add_argument("--monthly", help="月の額（ドル）")
    p["api-key-monthly"].add_argument("name", help="API キーの名前")
    p["api-key-monthly"].add_argument("usd", help="月の額（ドル。0 で外す）")
    p["api-key-use"].add_argument("mode", nargs="?", choices=account_registry.USES, help="使い方（省けば今の値を出す）")
    p["api-key-use"].add_argument("--weekly-at", help="閾値（百分率。1〜100）")
    p["capacity"].add_argument("name", help="アカウントの名前")
    p["capacity"].add_argument("five", help="5 時間のクォータ（USD か -）")
    p["capacity"].add_argument("seven", help="週のクォータ（USD か -）")
    p["remove"].add_argument("target", help="アカウントの名前・metered・API キーの名前")
    p["check"].add_argument("target", help="metered か API キーの名前")
