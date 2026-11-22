"""共有の替え先: 起動できる候補と、確定した替え先の子の環境と起動引数（#3604 #3864 の決定 3・8）。

ラッパーと `ndf plan` の `claude -p` は、どちらもこのモジュールの `target_env` だけで替え先の環境と起動引数を作り、
返った `env`・`argv` と同じ作業ディレクトリで起動する（確かめした組と起動する組を同じにする）。

- 登録済みアカウントは `account_dir.prepare` の後に `session_env.account_env` を当てる（`prepare` が認証の上書きを確かめる）
- 従量の接続（`metered`）は `build` で `session_env.metered_env` を当て、`verify` で実効値を 1 回だけ確かめる
- API キーの設定の従量の接続（キーの子）は、`build` が `--settings` に `api-key-helper metered <指紋>` の `apiKeyHelper` と
  `config-gate` の `ConfigChange` の hook を足し、`target_env` が確かめた設定への固定のファイル（`--pin`）を書く
  （別冊 issue-3604-3864-design-detail-keys.md の「環境変数の設定の API キー」「確かめた設定への固定」「反映の前に拒む」）
- 返す理由は値を含まない符号だけである。トークン・キー・接続先の値を返りと理由に入れない（I5）

登録簿の API キーの子（`key:<名前>`）は後の単位で足す。
"""

from __future__ import annotations

import json
import os
import secrets
import shlex
from pathlib import Path
from typing import Any, Mapping, NamedTuple, Optional, Sequence, Union

import semver

from ndf import identity
from ndf.adapters import cli_floor
from ndf.adapters.claude import CLAUDE
from ndf.contracts import limits
from ndf.domains.relay import account_dir, accounts, claude_args, key_helper, session_env
from ndf.domains.relay import account_registry as reg

# 従量の接続の確かめで、提供元の選択（PROVIDER_ENV）の外にある変数（実効値が期待値と同じであること）
_CHECKED_VALUES = tuple(k for k in reg.METERED_CHECKED if k not in reg.PROVIDER_ENV)
# helper と config-gate を起動する ndf の実行ファイルの絶対パス（このプラグインの根の `bin/ndf`）
NDF_EXE = str(identity.entrypoint(session_env.NDF_SRC.parent))
# `ConfigChange` の hook の `onFailure` が効く Claude Code のバージョンの下限（別冊の「起動できるバージョン」）
GATE_FLOOR = "2.1.295"
# 合成の一時ファイルと固定のファイルの名前の末尾（保存先は `settings_path`）
PIN_SUFFIX = key_helper.PIN_SUFFIX
PIN_OK_SUFFIX = ".pin.ok"
_STEM_RE = key_helper.STEM_RE  # 名前の頭の `<ラッパーの pid>-<開始時刻>`（config-gate も同じ形で起動したラッパーを読む）
# 関所の働かない起動引数と、実効の値で真なら関所の働かない変数
_GATE_OFF_FLAGS = ("--bare", "--safe-mode")
_GATE_OFF_ENV = ("CLAUDE_CODE_SIMPLE", "CLAUDE_CODE_SAFE_MODE")


class Built(NamedTuple):
    """確定した替え先の、子に渡す最終の環境・最終の起動引数・用意の結果（登録済みのとき）・その組の実効値。

    `settings_file` は `--settings <ファイル>` を合成して書いた一時ファイル、`pin_file` は確かめた設定への固定のファイル
    （キーの子のとき。どちらも子が終わるまで残し、起動した側が消す）。
    """

    env: dict[str, str]
    argv: list[str]
    prepared: Optional[account_dir.Prepared]
    resolved: Optional[account_dir.Resolved]
    settings_file: Optional[Path] = None
    pin_file: Optional[Path] = None


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
    """キーの子か（環境変数の設定が API キーの設定の従量の接続。別冊の「キーの子」）。"""
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


def _build(
    env: Mapping[str, str], decl: Decl, cwd: Path, argv: Sequence[str], pin: Optional[Path], record: Optional[tuple[Path, str]]
) -> Union[tuple[dict[str, str], list[str], Optional[str], bool], str]:
    """子の環境・起動引数・合成した helper の文字列（キーの子でなければ None）・元の `--settings` がファイルか。"""
    extra, saved = decl
    child = session_env.metered_env(env, extra, saved)
    if not _is_key(decl):
        return child, list(argv), None, False
    tail = _tail(pin, record)
    helper = shlex.join(
        [NDF_EXE, "relay", "account", "api-key-helper", reg.METERED, key_helper.fingerprint(extra["ANTHROPIC_API_KEY"]), *tail]
    )
    got = _compose(argv, cwd, helper, shlex.join([NDF_EXE, "relay", "account", "config-gate", *tail]))
    return got if isinstance(got, str) else (child, got[0], helper, got[1])


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
    decl = _decl_of(store, env)
    if isinstance(decl, str):
        return decl
    got = _build(env, decl, cwd, argv, None, None)
    return got if isinstance(got, str) else (got[0], got[1])


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
    も確かめる（登録簿のキーの確かめは後の単位で足す）。
    """
    r = account_dir.effective_env(env, config_dir, cwd, argv, pin=helper is not None)
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


def _settings_source(argv: Sequence[str], cwd: Path) -> Optional[Path]:
    """起動引数の `--settings` がファイルを指せばそのパス（相対パスは `cwd` から解く）。無い・JSON の文字列・2 つ以上なら None。"""
    found = [v for o, v in claude_args.options(argv[1:]) if o == "--settings"]
    if len(found) != 1 or not found[0] or found[0].lstrip().startswith("{"):
        return None
    return Path(found[0]) if os.path.isabs(found[0]) else cwd / found[0]


_UNREAD: Any = object()  # `fallbacks` の `metered_stamp` を渡さなかった印


def fallbacks(
    store: Path,
    environ: Mapping[str, str],
    metered: Optional[Decl],
    cwd: Path,
    argv: list[str],
    tried: frozenset[str],
    metered_stamp: Any = _UNREAD,
) -> list[account_dir.Probe]:
    """従量の接続の替え先の確かめ。`metered` があり `tried` に無ければ、確かめ用の起動引数で `build` と `verify` を当てる。

    返りは当てた名前の `Probe`（通れば `reason` が None）。`stamps` は `metered.json` のスタンプと `verify` の `Resolved.stamps`
    （`build` で落ちれば前者だけ）。ファイルを書かない（`--settings` のファイルの合成は確かめ用の起動引数に JSON の文字列で置く）。
    `metered_stamp` は呼び手が `metered` を読む前に取った `metered.json` のスタンプ（渡せばそれを使い、`stat` し直さない。
    読んだ後に更新されたスタンプで読む前のスタンプを上書きすると、古い設定の判定が残る）。
    """
    if metered is None or reg.METERED in tried:
        return []
    path = reg.metered_path(store)
    stamps = {str(path): account_dir.stat_stamp(path) if metered_stamp is _UNREAD else metered_stamp}
    source = _settings_source(argv, cwd)
    if source is not None:  # 合成で JSON の文字列に置き換わる元のファイルも、通ったか外れたかによらず依存先に入れる
        stamps[str(source)] = account_dir.stat_stamp(source)
    env = session_env.child_env(environ, Path(), reg.METERED, decided=True)  # 起動先を確定した子の環境（状態の保存先を読まない）
    got = _build(env, metered, cwd, argv, None, None)
    if isinstance(got, str):
        return [account_dir.Probe(reg.METERED, got, stamps)]
    child, final, helper, _ = got
    reason, r = verify(child, final, cwd, reg.shared_config_dir(environ), child, helper=helper)
    return [account_dir.Probe(reg.METERED, reason, {**stamps, **r.stamps})]


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


def key_target(
    env: Mapping[str, str], store: Path, state_dir: Path, cwd: Path, argv: list[str], record: Optional[tuple[Path, str]]
) -> Union[Built, str]:
    """キーの子（API キーの設定の従量の接続）の替え先。固定のファイルのパスを決めて (b) の形の起動引数で確かめ、通れば
    `Resolved.pins` を固定のファイルへ書く（別冊の「確かめた設定への固定」）。`record` はラッパーの払い出しの記録先と helper の ID
    （`ndf plan` は None）。キーの子でなければ `target_env` と同じ。
    """
    decl = _decl_of(store, env)
    if isinstance(decl, str) or not _is_key(decl):
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
    pin = d / f"{stem}{PIN_SUFFIX}"
    got = _build(env, decl, cwd, argv, pin, record)
    if isinstance(got, str):
        return got
    child, final, helper, from_file = got
    temp = d / f"{stem}.json" if from_file else None
    try:
        if temp is not None:  # 元のファイルの秘密を argv へ移さない（合成した JSON はファイルで渡す）
            _write_new(temp, final[2])
            final = [final[0], "--settings", str(temp), *final[3:]]
        reason, resolved = verify(child, final, cwd, reg.shared_config_dir(env), child, helper=helper)
        if reason is None:
            key_helper.write_pin(pin, resolved.pins, resolved.dirs)
            return Built(child, final, None, resolved, temp, pin)
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
    if target == reg.METERED:
        decl = _decl_of(store, env)
        if isinstance(decl, str):
            return decl
        if _is_key(decl):
            return key_target(env, store, state_dir, cwd, argv, None)
        got = _build(env, decl, cwd, argv, None, None)
        if isinstance(got, str):
            return got
        child, final = got[0], got[1]
        reason, resolved = verify(child, final, cwd, reg.shared_config_dir(env), child)
        return reason if reason is not None else Built(child, final, None, resolved)
    acc = registered(store).get(target)
    if acc is None:
        return "not_registered"
    prepared = account_dir.prepare(store, target, env, launch_of(env, store, state_dir, acc, cwd, argv))
    if not prepared.ok:
        return str(prepared.reason)
    usage = accounts.usage_path(state_dir, target, prepared.generation)
    return Built(session_env.account_env(env, prepared.config_dir, usage), list(argv), prepared, prepared.resolved)
