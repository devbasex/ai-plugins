"""共有の替え先: 起動できる候補と、確定した替え先の子の環境と起動引数（#3604 #3864 の決定 3・8）。

ラッパーと `ndf plan` の `claude -p` は、どちらもこのモジュールの `target_env` だけで替え先の環境と起動引数を作り、
返った `env`・`argv` と同じ作業ディレクトリで起動する（確かめした組と起動する組を同じにする）。

- 登録済みアカウントは `account_dir.prepare` の後に `session_env.account_env` を当てる（`prepare` が認証の上書きを確かめる）
- 従量の接続（`metered`）は `build` で `session_env.metered_env` を当て、`verify` で実効値を 1 回だけ確かめる
- API キーの設定の従量の接続（キーの子）は、`build` が `--settings` に `api-key-helper metered <指紋>` の `apiKeyHelper` と
  `config-gate` の `ConfigChange` の hook を足し、`target_env` が確かめた設定への固定のファイル（`--pin`）を書く
  （別冊 issue-3604-3864-design-detail-keys.md の「環境変数の設定の API キー」「確かめた設定への固定」「反映の前に拒む」）
- 返す理由は値を含まない符号だけである。トークン・キー・接続先の値を返りと理由に入れない（I5）

- 登録簿の API キーの子（`key:<名前>`）は、`key_env` の環境と `api-key-helper <名前> --generation <世代>` の helper で起動し、
  `verify` で既定の接続先だけを許す（詳細設計書の「起動の前の実効値の確かめ」）。`drift` は動いている間の設定の変更を確かめ直す
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import secrets
import shlex
import sys
from datetime import datetime
from pathlib import Path
from typing import Mapping, NamedTuple, Optional, Sequence, Union

import semver

from ndf import identity
from ndf.adapters import cli_floor
from ndf.adapters.claude import CLAUDE
from ndf.contracts import limits
from ndf.domains.relay import account_decide, account_dir, accounts, claude_args, key_helper, session_env
from ndf.domains.relay import account_registry as reg
from ndf.domains.relay.account_decide import KEY_PREFIX

# 従量の接続の確かめで、提供元の選択（PROVIDER_ENV）の外にある変数（実効値が期待値と同じであること）
_CHECKED_VALUES = tuple(k for k in reg.METERED_CHECKED if k not in reg.PROVIDER_ENV)
# helper と config-gate を起動する ndf の実行ファイルの絶対パス（このプラグインの根の `bin/ndf`）
NDF_EXE = str(identity.entrypoint(session_env.NDF_SRC.parent))
# `ConfigChange` の hook の `onFailure` が効く Claude Code のバージョンの下限（別冊の「起動できるバージョン」）
GATE_FLOOR = "2.1.295"
# 合成の一時ファイルと固定のファイルの名前の末尾（保存先は `settings_path`）
PIN_SUFFIX = ".pin.json"
PIN_OK_SUFFIX = ".pin.ok"
_STEM_RE = re.compile(r"(\d+)-(\d+\.\d{2})-[0-9a-f]{16}")
# 登録簿のキーの子で、提供元の選択・認証の値・接続先・ヘッダーのほかに、実効値が期待値と同じであるべき変数
_KEY_SAME = tuple(k for k in _CHECKED_VALUES if k not in (*reg.AUTH_ENV, "ANTHROPIC_BASE_URL", "ANTHROPIC_CUSTOM_HEADERS"))
# 動いているキーの子の設定の当たりのうち、止めてよい時点を待つもの（キーが変更前の接続先にしか送られない。別冊の「設定の変更でキーの子を止める範囲」）
WAIT_DRIFTS = ("key_removed", "key_rekeyed", "key_off")
# 関所の働かない起動引数と、実効の値で真なら関所の働かない変数
_GATE_OFF_FLAGS = ("--bare", "--safe-mode")
_GATE_OFF_ENV = ("CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_SAFE_MODE")


class Built(NamedTuple):
    """確定した替え先の、子に渡す最終の環境・最終の起動引数・用意の結果（登録済みのとき）・その組の実効値。

    `settings_file` は `--settings <ファイル>` を合成して書いた一時ファイル、`pin_file` は確かめた設定への固定のファイル
    （キーの子のとき。どちらも子が終わるまで残し、起動した側が消す）。`generation` は登録簿のキーの子の
    起動のときの世代（helper の `--generation`。ほかは空）。`pin` は親の接続の識別（`metered`・`key:<名前>` のとき。ほかは
    None）で、`env` の `NDF_RELAY_PIN` にも入れてある（詳細設計書の「親の接続の識別」）。
    """

    env: dict[str, str]
    argv: list[str]
    prepared: Optional[account_dir.Prepared]
    resolved: Optional[account_dir.Resolved]
    settings_file: Optional[Path] = None
    pin_file: Optional[Path] = None
    generation: str = ""
    pin: Optional[str] = None


def claude_version(env: Mapping[str, str], cwd: Path) -> Optional[str]:
    """起動する Claude Code のバージョン（起動の前に `cli_floor.require` が打った `--version` の記録）。読めなければ None。"""
    return cli_floor.seen(CLAUDE, env, cwd)


def _load(store: Path, name: str) -> Optional[reg.Account]:
    try:
        return reg.load(store, name)
    except reg.AccountError:  # noqa: NDF401 ファイル: 壊れた登録は候補にしない（`ready` の not_registered と同じ扱い）
        return None


def registered(store: Path) -> dict[str, reg.Account]:
    """読める登録済みアカウント（名前の順）。壊れた登録は登録が無いとして飛ばす（起動の前の確かめと同じ）。"""
    return {n: a for n in reg.names(store) if (a := _load(store, n)) is not None}


def usable(store: Path, state_dir: Path, now: float) -> dict[str, reg.Account]:
    """再登録が要らず、外した間でない登録済みアカウント（起動できるかを確かめる前の候補）。"""
    accs = registered(store)
    held = accounts.held(state_dir, now, {n: a.generation for n, a in accs.items()})
    return {n: a for n, a in accs.items() if not a.needs_relogin and n not in held}


def launch_of(
    env: Mapping[str, str], store: Path, state_dir: Path, account: reg.Account, cwd: Path, argv: Sequence[str]
) -> account_dir.Launch:
    """登録済みアカウントの子が受け取る環境・作業ディレクトリ・起動引数。`env` は `child_env` の出力。"""
    usage = accounts.usage_path(state_dir, account.name, account.generation)
    config = Path(reg.account_dir(store, account.name)).absolute()
    return account_dir.Launch(session_env.account_env(env, config, usage), cwd, list(argv))


def candidates(env: Mapping[str, str], store: Path, state_dir: Path, now: float, cwd: Path, argv: list[str]) -> list[str]:
    """登録済みアカウントのうち、再登録が要らず、外した間でなく、`account_dir.ready` に通るものの名前。

    `env` は親の環境（`child_env` の出力でもよい。`CLAUDE_CONFIG_DIR`・`NDF_SHARED_CONFIG_DIR` は同じ）。
    """
    child = session_env.child_env(env, state_dir, None, decided=True)
    return [
        n
        for n, a in usable(store, state_dir, now).items()
        if account_dir.ready(store, n, env, launch_of(child, store, state_dir, a, cwd, argv)).reason is None
    ]


Decl = tuple[dict[str, str], bool]  # 従量の接続の設定（変数の組、保存した設定か）


def metered_decl(store: Path, environ: Mapping[str, str]) -> Union[Decl, str, None]:
    """従量の接続の設定。無ければ None、使えなければ値を含まない理由の符号（詳細設計書の「従量の接続の設定の検証」）。

    環境変数の設定は `account_registry.parse_declared` の検証（符号は `DeclError.code`）、保存した設定は `load_metered` の検証で、
    壊れていれば `broken`。
    """
    raw = reg.declared_env(environ)
    if raw is None:
        decl, broken = reg.load_metered(store)
        if broken is not None:
            return "broken"
        return (dict(decl.env), True) if decl is not None else None
    try:
        return reg.parse_declared(raw), False
    except reg.DeclError as exc:
        return exc.code


def _is_key(decl: Decl) -> bool:
    return not decl[1] and reg.metered_kind(decl[0]) == reg.API_KEY_KIND


def key_child(env: Mapping[str, str], target: str, store: Path) -> bool:
    """キーの子か（登録簿の API キーの子と、環境変数の設定が API キーの設定の従量の接続。別冊の「キーの子」）。"""
    if target.startswith(KEY_PREFIX):
        return True
    decl = metered_decl(store, env) if target == reg.METERED else None
    return isinstance(decl, tuple) and _is_key(decl)


def _tail(pin: Optional[Path], record: Optional[tuple[Path, str]]) -> list[str]:
    """helper と config-gate の末尾の引数（別冊の「起動引数の 2 つの形」）。確かめ用は空、起動は `--pin` と記録先・ID。"""
    out = ["--pin", str(pin)] if pin is not None else []
    return out + (["--record", str(record[0]), "--id", record[1]] if record is not None else [])


def _read_settings(raw: Optional[str], cwd: Path) -> Union[tuple[dict, bool], str]:
    """`--settings` の値の object と、ファイルから読んだか。読めない・object でなければ `key_unresolved`。"""
    if not raw:
        return "key_unresolved"
    from_file = not raw.lstrip().startswith("{")
    try:
        text = Path(raw if os.path.isabs(raw) else cwd / raw).read_text(encoding="utf-8") if from_file else raw
        data = json.loads(text)
    except (OSError, ValueError):  # noqa: NDF401 利用者の入力: 読めない --settings ではキーの子を起動しない
        return "key_unresolved"
    return (data, from_file) if isinstance(data, dict) else "key_unresolved"


def _compose(argv: Sequence[str], cwd: Path, helper: str, gate: str) -> Union[tuple[list[str], bool], str]:
    """`--settings` の合成（詳細設計書の「`--settings` の合成と引用」）。返りは（合成した JSON を `argv[1:3]` に置いた起動引数、
    元がファイルか）。合成できなければ理由の符号。元の `--settings` は外し、1 つの `--settings` にまとめる。
    """
    found = [v for o, v in claude_args.options(argv[1:]) if o == "--settings"]
    if len(found) > 1:
        return "key_unresolved"
    data, from_file = {}, False
    if found:
        got = _read_settings(found[0], cwd)
        if isinstance(got, str):
            return got
        data, from_file = got
    if "apiKeyHelper" in data:
        return "key_overridden"
    hooks = data["hooks"] if "hooks" in data else {}
    groups = (hooks["ConfigChange"] if "ConfigChange" in hooks else []) if isinstance(hooks, dict) else None
    if not isinstance(groups, list):
        return "key_unresolved"
    hook = {"type": "command", "command": gate, "timeout": limits.value(key_helper.POLICY, "gate"), "onFailure": "block"}
    merged = {**data, "apiKeyHelper": helper, "hooks": {**hooks, "ConfigChange": [*groups, {"hooks": [hook]}]}}
    rest = claude_args.without(argv[1:], "--settings")
    return [*argv[:1], "--settings", json.dumps(merged), *rest], from_file


class Made(NamedTuple):
    """子の環境・起動引数・合成した helper の文字列（キーの子でなければ None）・元の `--settings` がファイルか・登録簿のキーの世代・
    親の接続の識別。"""

    child: dict[str, str]
    argv: list[str]
    helper: Optional[str]
    from_file: bool
    generation: str = ""
    pin: Optional[str] = None


def _pinned(child: dict[str, str], pin: str) -> dict[str, str]:
    """子の環境に親の接続の識別を入れる（入れ子の `ndf plan` が読み直した設定と比べる）。"""
    return {**child, "NDF_RELAY_PIN": pin}


def _metered_pin(child: Mapping[str, str], decl: Decl) -> str:
    """従量の接続の識別。提供元の種類・設定の出所・確かめる変数の期待値の SHA-256 で、秘密の値そのものは入れない。"""
    extra, saved = decl
    merged = {**child, **extra}
    body = {
        "kind": reg.metered_kind(extra),
        "source": "saved" if saved else "env",
        "env": {k: merged[k] for k in reg.METERED_CHECKED if k in merged},
    }
    return f"{reg.METERED}:{hashlib.sha256(json.dumps(body, sort_keys=True).encode()).hexdigest()}"


def _with_helper(
    child: dict[str, str], argv: Sequence[str], cwd: Path, words: list[str], tail: list[str], gen: str = ""
) -> Union[Made, str]:
    """`--settings` に helper（`api-key-helper <words> <tail>`）と `config-gate` の hook を合成する。"""
    helper = shlex.join([NDF_EXE, "relay", "account", "api-key-helper", *words, *tail])
    got = _compose(argv, cwd, helper, shlex.join([NDF_EXE, "relay", "account", "config-gate", *tail]))
    return got if isinstance(got, str) else Made(child, got[0], helper, got[1], gen, child.get("NDF_RELAY_PIN"))


def _build(
    env: Mapping[str, str], decl: Decl, cwd: Path, argv: Sequence[str], pin: Optional[Path], record: Optional[tuple[Path, str]]
) -> Union[Made, str]:
    """従量の接続の子の `Made`。API キーの設定では `api-key-helper metered <指紋>` の helper を合成する。"""
    extra, saved = decl
    child = session_env.metered_env(env, extra, saved)
    ident = _metered_pin(child, decl)
    child = _pinned(child, ident)
    if not _is_key(decl):
        return Made(child, list(argv), None, False, pin=ident)
    return _with_helper(child, argv, cwd, [reg.METERED, key_helper.fingerprint(extra["ANTHROPIC_API_KEY"])], _tail(pin, record))


def _key_build(env: Mapping[str, str], name: str, store: Path, cwd: Path, argv: Sequence[str], tail: list[str]) -> Union[Made, str]:
    """登録簿の API キーの子の `Made`（`key_env` と `api-key-helper <名前> --generation <世代>`）。

    登録簿に無い・世代が無い・壊れていれば `key_missing`、使い方が `off` なら `key_off`。
    """
    if name not in reg.recovery_keys(store):
        return "key_off" if name in reg.key_names(store) else "key_missing"
    try:
        gen = reg.key_generation(store, name)
    except reg.AccountError:  # noqa: NDF401 ファイル: 世代の無いキーは helper が出せないので起動しない
        return "key_missing"
    child = _pinned(session_env.key_env(env, name), f"{KEY_PREFIX}{name}:{gen}")
    return _with_helper(child, argv, cwd, [name, "--generation", gen], tail, gen)


def _decl_of(store: Path, env: Mapping[str, str]) -> Union[Decl, str]:
    decl = metered_decl(store, env)
    if decl is None:
        return "metered_unavailable"
    if isinstance(decl, str):
        return "metered_unclassified" if decl in ("unclassified", "headers") else "metered_unavailable"
    return decl


def build(
    env: Mapping[str, str], target: str, store: Path, state_dir: Path, cwd: Path, argv: list[str]
) -> Union[tuple[dict[str, str], list[str]], str]:
    """従量の接続の子の最終の環境と確かめ用の起動引数を作る（書かない）。作れなければ理由の符号。

    設定が無い・検証に外れれば `metered_unavailable`、提供元を 1 つに分類できなければ `metered_unclassified`。API キーの設定では
    `--settings` に helper と `config-gate` の hook を足す（合成できなければ `key_unresolved`・`key_overridden`）。末尾に
    `--pin`・`--record`・`--id` を付けない（起動の起動引数は `key_target` が付ける。別冊の「起動引数の 2 つの形」の (a)）。
    `env` は `child_env` の出力、`cwd` は子が実際に起動する作業ディレクトリ（`--settings` の相対パスはここから解く）。
    """
    if target.startswith(KEY_PREFIX):
        got = _key_build(env, target[len(KEY_PREFIX) :], store, cwd, argv, [])
    else:
        decl = _decl_of(store, env)
        if isinstance(decl, str):
            return decl
        got = _build(env, decl, cwd, argv, None, None)
    return got if isinstance(got, str) else (got.child, got.argv)


def _provider(env: Mapping[str, str]) -> Optional[list[str]]:
    """`provider_on` が真の提供元の選択の変数。解けない値があれば None。"""
    on = [(k, reg.provider_on(env.get(k))) for k in reg.PROVIDER_ENV]
    return None if any(v is None for _, v in on) else [k for k, v in on if v]


def _gate_off(r: account_dir.Resolved, env: Mapping[str, str], argv: Sequence[str], cwd: Path) -> bool:
    """キーの子の関所（`config-gate`）が働かないか（別冊の「起動できるバージョン」「起動の前の確かめ」）。"""
    found = claude_version(env, cwd)
    if found is None or not semver.Version.is_valid(found) or semver.Version.parse(found) < semver.Version.parse(GATE_FLOOR):
        return True
    if {o for o, _ in claude_args.options(argv[1:])} & set(_GATE_OFF_FLAGS) or r.hooks_off or r.managed_only:
        return True
    return any(reg.provider_on(r.env.get(k)) is not False for k in _GATE_OFF_ENV)


def verify(
    env: Mapping[str, str], argv: list[str], cwd: Path, config_dir: Path, expected: Mapping[str, str], helper: Optional[str] = None
) -> tuple[Optional[str], account_dir.Resolved]:
    """最終の環境・起動引数・作業ディレクトリで `effective_env` を 1 回だけ打ち、従量の実効値の確かめを当てる。

    返りは（理由の符号か None、打った `Resolved`）。理由は `metered_overridden`・`key_gate` で、値を含まない。`helper` はキーの子で
    合成した helper の文字列で、実効の helper がこの値で出所が `--settings` であり、helper が読む従量の接続の設定が期待値と同じこと
    も確かめる。登録簿のキーの子（`expected` の `NDF_CLAUDE_ACCOUNT` が `key:<名前>`）は `_key_ok` を当て、外れれば `key_overridden`。
    """
    r = account_dir.effective_env(env, config_dir, cwd, argv, pin=helper is not None)
    if (expected.get("NDF_CLAUDE_ACCOUNT") or "").startswith(KEY_PREFIX):  # noqa: NDF501 利用者の入力: 無い名前は登録簿のキーでない
        if not (helper is not None and r.helper == helper and r.helper_origin == "--settings" and _key_ok(r, expected)):
            return "key_overridden", r
        if None in r.pins.values():
            return "key_pin", r
        return ("key_gate" if _gate_off(r, env, argv, cwd) else None), r
    want = _provider(expected)
    same = all((r.env.get(k) or "") == (expected.get(k) or "") for k in _CHECKED_VALUES)  # noqa: NDF501 利用者の入力: 無いと空文字を同じに読む
    if helper is None:
        mine = r.helper is None
    else:
        mine = r.helper == helper and r.helper_origin == "--settings" and reg.declared_env(r.env) == reg.declared_env(expected)
    if not (want is not None and _provider(r.env) == want and same and mine and not r.unresolved):
        return "metered_overridden", r
    if helper is not None and None in r.pins.values():
        return "key_pin", r  # 照らせない設定ファイルでは最初の払い出しが拒まれる。旧子を止める前に候補から外す
    return ("key_gate" if helper is not None and _gate_off(r, env, argv, cwd) else None), r


def _key_ok(r: account_dir.Resolved, expected: Mapping[str, str]) -> bool:
    """登録簿のキーの子の実効値（詳細設計書の「起動の前の実効値の確かめ」）。提供元の選択がどれも偽、認証の値とヘッダーが空、
    接続先が既定（`DEFAULT_API_URL`）か空、ほかの確かめの変数が期待値と同じ、解けない源が無い。"""
    base = (r.env.get("ANTHROPIC_BASE_URL") or "").rstrip("/")  # noqa: NDF501 利用者の入力: 無い接続先は既定の接続先
    return (
        not r.unresolved
        and all(reg.provider_on(r.env.get(k)) is False for k in reg.PROVIDER_ENV)
        and not any(r.env.get(k) for k in (*reg.AUTH_ENV, "ANTHROPIC_CUSTOM_HEADERS"))
        and base in ("", account_dir.DEFAULT_API_URL)
        and all((r.env.get(k) or "") == (expected.get(k) or "") for k in _KEY_SAME)  # noqa: NDF501 利用者の入力: 無いと空文字を同じに読む
    )


def _settings_source(argv: Sequence[str], cwd: Path) -> Optional[Path]:
    """起動引数の `--settings` がファイルを指せばそのパス（相対パスは `cwd` から解く）。無い・JSON の文字列・2 つ以上なら None。"""
    found = [v for o, v in claude_args.options(argv[1:]) if o == "--settings"]
    if len(found) != 1 or not found[0] or found[0].lstrip().startswith("{"):
        return None
    return Path(found[0]) if os.path.isabs(found[0]) else cwd / found[0]


class Read(NamedTuple):
    """替え先の確かめの前に取ったスタンプ（パス → スタンプ）と、登録の保存先（登録簿の API キーを読む）。"""

    store: Path
    stamps: Mapping[str, Optional[tuple]]


def read_before(store: Path) -> Read:
    """`metered.json` と `api-keys.json` のスタンプを、設定と登録簿を読む前に取る。"""
    return Read(store, {str(p): account_dir.stat_stamp(p) for p in (reg.metered_path(store), reg.book_path(store))})


def fallbacks(
    read: Read, environ: Mapping[str, str], metered: Optional[Decl], cwd: Path, argv: list[str], tried: frozenset[str]
) -> list[account_dir.Probe]:
    """従量の接続と API キーの替え先の確かめ。`metered` があれば `metered`、続けて `recovery_keys` の `key:<名前>` を並べ、`tried` を
    除き、確かめ用の起動引数で `build` と `verify` を当てる。

    返りは当てた名前の `Probe`（通れば `reason` が None）。`stamps` は `read.stamps`（`metered` は全部、`key:<名前>` は
    `api-keys.json` の分）と、合成で置き換わる元の `--settings` のファイルのスタンプと、`verify` の `Resolved.stamps`（`build` で
    落ちれば前の 2 つだけ）。ファイルを書かない（`--settings` のファイルの合成は確かめ用の起動引数に JSON の文字列で置く）。
    `read` は呼び手が設定と登録簿を読む前に取ったスタンプ（`read_before`）。`stat` し直さない（読んだ後に更新されたスタンプで
    読む前のスタンプを上書きすると、古い設定の判定が残る）。
    """
    source = _settings_source(argv, cwd)
    extra = {str(source): account_dir.stat_stamp(source)} if source is not None else {}  # 通ったか外れたかによらず依存先に入れる
    book = str(reg.book_path(read.store))
    out: list[account_dir.Probe] = []
    if metered is not None and reg.METERED not in tried:
        env = session_env.child_env(environ, Path(), reg.METERED, decided=True)  # 起動先を確定した子の環境（状態の保存先を読まない）
        out.append(_probe(reg.METERED, _build(env, metered, cwd, argv, None, None), {**read.stamps, **extra}, cwd, environ))
    for name in reg.recovery_keys(read.store):
        target = f"{KEY_PREFIX}{name}"
        if target not in tried:
            env = session_env.child_env(environ, Path(), target, decided=True)
            got = _key_build(env, name, read.store, cwd, argv, [])
            out.append(_probe(target, got, {book: read.stamps.get(book), **extra}, cwd, environ))
    return out


def _probe(
    name: str, got: Union[Made, str], stamps: dict[str, Optional[tuple]], cwd: Path, environ: Mapping[str, str]
) -> account_dir.Probe:
    if isinstance(got, str):
        return account_dir.Probe(name, got, stamps)
    reason, r = verify(got.child, got.argv, cwd, reg.shared_config_dir(environ), got.child, helper=got.helper)
    return account_dir.Probe(name, reason, {**stamps, **r.stamps})


def drift(
    target: str, env: Mapping[str, str], argv: list[str], cwd: Path, store: Path, generation: str
) -> tuple[Optional[str], Optional[str], dict[str, Optional[tuple]]]:
    """動いているキーの子の設定を確かめ直す（詳細設計書の「起動の後の設定の変更」）。`env`・`argv` は起動した子の最終の値。

    返りは（止めてよい時点を待つ当たり `WAIT_DRIFTS`、待たない当たり（`verify` の符号）、次に比べるスタンプ）。
    登録簿のキーの子は、登録簿にキーが無い（`key_removed`）・世代が起動のときと違う（`key_rekeyed`）・使い方が `off`
    （`key_off`）を確かめ、どの子も同じ起動引数で `verify` を打ち直す。
    """
    stamps = {str(reg.book_path(store)): account_dir.stat_stamp(reg.book_path(store))}
    wait = None
    if target.startswith(KEY_PREFIX):
        name = target[len(KEY_PREFIX) :]
        try:
            gen: Optional[str] = reg.key_generation(store, name)
        except reg.AccountError:  # ファイル: 無いか読めない登録簿のキーは消されたとして扱う
            gen = None
        wait = "key_removed" if gen is None else "key_rekeyed" if gen != generation else None
        wait = wait or (None if name in reg.recovery_keys(store) else "key_off")
    found = [v for o, v in claude_args.options(argv[1:]) if o == "--settings"]
    got = _read_settings(found[0] if len(found) == 1 else None, cwd)
    helper = got[0].get("apiKeyHelper") if not isinstance(got, str) else None
    if not isinstance(helper, str):
        return wait, "key_unresolved", stamps
    reason, r = verify(env, argv, cwd, reg.shared_config_dir(env), env, helper=helper)
    return wait, reason, {**stamps, **r.stamps}


def _cmdlines() -> Optional[list[list[str]]]:
    """動いているプロセスの起動引数。`/proc` を読めなければ None（残骸を消さない）。"""
    out = []
    try:
        pids = [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:  # noqa: NDF401 ファイル: /proc を読めなければ残骸を消さない
        return None
    for pid in pids:
        raw = _cmdline(pid)
        if raw is None:
            return None
        out.append(raw.decode(errors="replace").split("\0"))
    return out


def _cmdline(pid: str) -> Optional[bytes]:
    """1 つのプロセスの起動引数。読む間に終わったプロセスは空、読めなければ None。"""
    try:
        return Path("/proc", pid, "cmdline").read_bytes()
    except (FileNotFoundError, ProcessLookupError):  # noqa: NDF401 ファイル: 読む間に終わったプロセスは何も持たない
        return b""
    except OSError:  # noqa: NDF401 ファイル: 読めないプロセスがあれば残骸を消さない
        return None


def _owner_ended(stem: str) -> bool:
    """名前の `owner` の pid と開始時刻のプロセスが生きていないと確かめられたら真（形の違う名前は消さない）。"""
    m = _STEM_RE.fullmatch(stem)
    return m is not None and reg.proc_start(int(m.group(1))) != float(m.group(2))


def _sweep(d: Path) -> None:
    """合成の一時ファイルと固定のファイルの残骸を消す（詳細設計書の「残骸」と別冊の「固定のファイルの残骸」）。一時ファイルが先。"""
    cmdlines = _cmdlines()
    try:
        names = os.listdir(d)
    except OSError:  # noqa: NDF401 ファイル: 列挙できない保存先の残骸は次の起動で消す
        return
    if cmdlines is None:
        return
    for n in names:
        path = d / n
        temp = n.endswith(".json") and not n.endswith(PIN_SUFFIX)
        if temp and _owner_ended(n[: -len(".json")]) and not any(str(path) in words for words in cmdlines):
            path.unlink(missing_ok=True)
    for n in names:
        stem = n[: -len(PIN_SUFFIX)] if n.endswith(PIN_SUFFIX) else n[: -len(PIN_OK_SUFFIX)] if n.endswith(PIN_OK_SUFFIX) else None
        pin = d / f"{stem}{PIN_SUFFIX}"
        if stem is None or not _owner_ended(stem) or (d / f"{stem}.json").exists():
            continue
        if not any(str(pin) in word for words in cmdlines for word in words):
            pin.unlink(missing_ok=True)
            (d / f"{stem}{PIN_OK_SUFFIX}").unlink(missing_ok=True)


def settings_path(state_dir: Path) -> Path:
    """合成の一時ファイルと固定のファイルの保存先（`<状態の保存先>/settings`）。"""
    return state_dir / "settings"


def _settings_dir(state_dir: Path) -> Path:
    d = settings_path(state_dir)
    d.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(d, 0o700)
    return d


def _write_new(path: Path, text: str) -> None:
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
    try:
        if os.write(fd, text.encode()) != len(text.encode()):
            raise OSError("一時ファイルの一部だけが書けた")
    finally:
        os.close(fd)


def _launch_made(
    env: Mapping[str, str], store: Path, cwd: Path, argv: list[str], pin: Path, record: Optional[tuple[Path, str]]
) -> Union[Made, str]:
    """起動するキーの子の `Made`（(b) の形の起動引数）。登録簿のキーか従量の接続かは `env` の `NDF_CLAUDE_ACCOUNT` で決める。"""
    target = env.get("NDF_CLAUDE_ACCOUNT") or ""  # noqa: NDF501 利用者の入力: 名前の無い環境は従量の接続として扱う
    if target.startswith(KEY_PREFIX):
        return _key_build(env, target[len(KEY_PREFIX) :], store, cwd, argv, _tail(pin, record))
    decl = _decl_of(store, env)
    return decl if isinstance(decl, str) else _build(env, decl, cwd, argv, pin, record)


def key_target(
    env: Mapping[str, str], store: Path, state_dir: Path, cwd: Path, argv: list[str], record: Optional[tuple[Path, str]]
) -> Union[Built, str]:
    """キーの子（API キーの設定の従量の接続）の替え先。固定のファイルのパスを決めて (b) の形の起動引数で確かめ、通れば
    `Resolved.pins` を固定のファイルへ書く（別冊の「確かめた設定への固定」）。`record` はラッパーの払い出しの記録先と helper の ID
    （`ndf plan` は None）。キーの子でなければ `target_env` と同じ。登録簿のキーの子かは `env` の `NDF_CLAUDE_ACCOUNT` で決める
    （引数の上限のため。`child_env(…, account=target, decided=True)` の出力が持つ）。
    """
    registry = (env.get("NDF_CLAUDE_ACCOUNT") or "").startswith(KEY_PREFIX)  # noqa: NDF501 利用者の入力: 名前の無い環境は従量の接続
    decl = _decl_of(store, env)
    if not registry and (isinstance(decl, str) or not _is_key(decl)):
        return target_env(env, reg.METERED, store, state_dir, cwd, argv)
    start = reg.proc_start(os.getpid())
    try:
        d = _settings_dir(state_dir)
    except OSError:  # noqa: NDF401 ファイル: 保存先を作れなければ固定できないので起動しない
        return "key_record"
    if start is None:
        return "key_record"
    _sweep(d)
    stem = f"{os.getpid()}-{start:.2f}-{secrets.token_hex(8)}"
    pin_file = d / f"{stem}{PIN_SUFFIX}"
    got = _launch_made(env, store, cwd, argv, pin_file, record)
    if isinstance(got, str):
        return got
    child, final, helper, from_file, gen, pin = got
    temp = d / f"{stem}.json" if from_file else None
    try:
        if temp is not None:  # 元のファイルの秘密を argv へ移さない（合成した JSON はファイルで渡す）
            _write_new(temp, final[2])
            final = [final[0], "--settings", str(temp), *final[3:]]
        reason, resolved = verify(child, final, cwd, reg.shared_config_dir(env), child, helper=helper)
        if reason is None:
            key_helper.write_pin(pin_file, resolved.pins, resolved.dirs)
            return Built(child, final, None, resolved, temp, pin_file, gen, pin)
    except OSError:  # ファイル: 一時ファイルか固定のファイルを書けなければ起動しない
        reason = "key_record"
    if temp is not None:
        temp.unlink(missing_ok=True)
    return reason


def target_env(env: Mapping[str, str], target: str, store: Path, state_dir: Path, cwd: Path, argv: list[str]) -> Union[Built, str]:
    """確定した替え先の子の環境と最終の起動引数。使えなければ値を含まない理由の符号を返す。

    登録済みアカウントは `prepare` の後に `account_env`（`Built.resolved` は `Prepared.resolved`。理由は `prepare` の
    `Prepared.reason`）。`metered` は `build` の後に `verify` を当て、外れれば `metered_overridden`・`key_gate` を返す。キーの子は
    確かめた設定への固定のファイル（`Built.pin_file`）と、元の `--settings` がファイルなら合成の一時ファイル（`Built.settings_file`）を
    書く（払い出しの記録先を helper へ渡す起動は `key_target`）。
    `env` は `child_env(…, account=target, decided=True)` の出力。
    """
    if target.startswith(KEY_PREFIX):
        return key_target({**env, "NDF_CLAUDE_ACCOUNT": target}, store, state_dir, cwd, argv, None)
    if target == reg.METERED:
        decl = _decl_of(store, env)
        if isinstance(decl, str):
            return decl
        if _is_key(decl):
            return key_target(env, store, state_dir, cwd, argv, None)
        got = _build(env, decl, cwd, argv, None, None)
        if isinstance(got, str):
            return got
        child, final = got.child, got.argv
        reason, resolved = verify(child, final, cwd, reg.shared_config_dir(env), child)
        return reason if reason is not None else Built(child, final, None, resolved, pin=got.pin)
    acc = registered(store).get(target)
    if acc is None:
        return "not_registered"
    prepared = account_dir.prepare(store, target, env, launch_of(env, store, state_dir, acc, cwd, argv))
    if not prepared.ok:
        return str(prepared.reason)
    usage = accounts.usage_path(state_dir, target, prepared.generation)
    return Built(session_env.account_env(env, prepared.config_dir, usage), list(argv), prepared, prepared.resolved)


def guards() -> account_decide.Guards:
    """閾値 A・B・C（`relay.five-hour-guard`・`relay.weekly-prefer`・`relay.weekly-guard`）。"""
    return account_decide.Guards(
        limits.value("relay.five-hour-guard", "count"),
        limits.value("relay.weekly-prefer", "count"),
        limits.value("relay.weekly-guard", "count"),
    )


def _limited_rows(env: Mapping[str, str], state_dir: Path, now: float, account: reg.Account) -> list[account_decide.Usage]:
    """上限の観測を書き（`until` は窓から決める。書けなければ 1 行を出して続ける）、今のアカウントを上限とした使用率の行を返す。"""
    store, name, gen = reg.store_dir(env), account.name, account.generation
    windows = accounts.read_windows(accounts.usage_path(state_dir, name, gen), accounts.fetch_path(store, name, gen))
    tz = datetime.now().astimezone().tzinfo
    try:
        accounts.see_limit(state_dir, name, gen, now, accounts.limit_until("", windows, now, tz))
    except OSError as exc:  # ファイル: 書けなくても、今のアカウントを上限として替え先を選ぶ
        print(f"ndf plan: アカウント {name} の上限の観測を書けない: {type(exc).__name__}", file=sys.stderr)
    rows, _ = accounts.usage_rows(state_dir, store, now, {n: a.generation for n, a in registered(store).items()})
    mine = account_decide.Usage(name, None, None, None, None, True)
    return [r._replace(limited=True) if r.name == name else r for r in rows] + ([] if any(r.name == name for r in rows) else [mine])


def retry_env(
    env: Mapping[str, str], state_dir: Path, now: float, cwd: Path, argv: list[str], keys: bool = False
) -> Optional[tuple[Built, str]]:
    """`ndf plan` の上限での打ち直しの替え先（決定 9）。返りは（`target_env` の `Built`、替え先の名前）か None。

    `env` の `NDF_CLAUDE_ACCOUNT` の上限の観測を `see_limit` で書き、`after_limit` が `switch` なら `target_env` を打つ。`keys` が偽なら
    キーの子（API キーの設定の従量の接続と登録簿の API キー）を替え先にしない（`call_claude` の起動の形がキーを渡せないとき）。
    登録済みアカウントの名前でない・`wait_reset`・`target_env` が理由の符号を返したら None（符号は標準エラーの 1 行）。
    """
    store = reg.store_dir(env)
    account = registered(store).get(env.get("NDF_CLAUDE_ACCOUNT") or "")  # noqa: NDF501 利用者の入力: 名前の無い環境は打ち直さない
    if account is None:
        return None
    rows = _limited_rows(env, state_dir, now, account)
    read = read_before(store)
    decl = metered_decl(store, env)
    probes = fallbacks(read, env, decl if isinstance(decl, tuple) else None, cwd, argv, frozenset())
    usable = [p.name for p in probes if p.reason is None and (keys or not key_child(env, p.name, store))]
    pick = account_decide.after_limit(account.name, rows, candidates(env, store, state_dir, now, cwd, argv), usable, guards())
    if pick.kind != "switch" or pick.account is None:
        return None
    child = session_env.child_env(env, state_dir, pick.account, decided=True)
    got = target_env(child, pick.account, store, state_dir, cwd, argv)
    if isinstance(got, str):
        print(f"ndf plan: 替え先 {pick.account} を用意できない（{got}）。打ち直さない", file=sys.stderr)
        return None
    return got, pick.account
