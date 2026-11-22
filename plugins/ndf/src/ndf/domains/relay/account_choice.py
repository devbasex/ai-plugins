"""共有の替え先: 起動できる候補と、確定した替え先の子の環境と起動引数（#3604 #3864 の決定 3・8）。

ラッパーと `ndf plan` の `claude -p` は、どちらもこのモジュールの `target_env` だけで替え先の環境と起動引数を作り、
返った `env`・`argv` と同じ作業ディレクトリで起動する（確かめした組と起動する組を同じにする）。

- 登録済みアカウントは `account_dir.prepare` の後に `session_env.account_env` を当てる（`prepare` が認証の上書きを確かめる）
- 従量の接続（`metered`）は `build` で `session_env.metered_env` を当て、`verify` で実効値を 1 回だけ確かめる
- 返す理由は値を含まない符号だけである。トークン・キー・接続先の値を返りと理由に入れない（I5）

API キーの子（`key:<名前>`）と、API キーの設定の従量の接続の `--settings` の合成は後の単位で足す。
"""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, NamedTuple, Optional, Sequence, Union

from ndf.domains.relay import account_dir, accounts, session_env
from ndf.domains.relay import account_registry as reg

# 従量の接続の確かめで、提供元の選択（PROVIDER_ENV）の外にある変数（実効値が期待値と同じであること）
_CHECKED_VALUES = tuple(k for k in reg.METERED_CHECKED if k not in reg.PROVIDER_ENV)


class Built(NamedTuple):
    """確定した替え先の、子に渡す最終の環境・最終の起動引数・用意の結果（登録済みのとき）・その組の実効値。"""

    env: dict[str, str]
    argv: list[str]
    prepared: Optional[account_dir.Prepared]
    resolved: Optional[account_dir.Resolved]


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


def _metered_decl(store: Path, env: Mapping[str, str]) -> Optional[tuple[dict[str, str], bool]]:
    """従量の接続の設定（変数の組と、保存した設定か）。無い・空・保存した設定が壊れていれば None。"""
    if reg.declared_env(env) is None:
        decl = reg.load_metered(store)[0]
        return (dict(decl.env), True) if decl is not None else None
    extra = reg.fallback_env(store, env)
    return (extra, False) if extra else None


def key_child(env: Mapping[str, str], target: str, store: Path) -> bool:
    """キーの子か（環境変数の設定が API キーの設定の従量の接続。別冊の「キーの子」）。"""
    decl = _metered_decl(store, env) if target == reg.METERED else None
    return decl is not None and not decl[1] and reg.metered_kind(decl[0]) == reg.API_KEY_KIND


def build(
    env: Mapping[str, str], target: str, store: Path, state_dir: Path, cwd: Path, argv: list[str]
) -> Union[tuple[dict[str, str], list[str]], str]:
    """従量の接続の子の最終の環境と確かめ用の起動引数を作る（書かない）。作れなければ理由の符号 `metered_unavailable`。

    提供元を 1 つに分類できない従量の接続の設定（`metered_kind` が None。キーと別の資格情報やヘッダーの併用など）は、子の環境を
    組み立てる前に `metered_unclassified` で拒む（キーの子の判定をすり抜けて設定のキーのまま起動しない）。
    `env` は `child_env` の出力、`cwd` は子が実際に起動する作業ディレクトリ（`state_dir`・`cwd` は後の単位の合成が使う）。
    """
    decl = _metered_decl(store, env)
    if decl is None:
        return "metered_unavailable"
    extra, saved = decl
    if reg.metered_kind(extra) is None:
        return "metered_unclassified"
    return session_env.metered_env(env, extra, saved), list(argv)


def _provider(env: Mapping[str, str]) -> Optional[list[str]]:
    """`provider_on` が真の提供元の選択の変数。解けない値があれば None。"""
    on = [(k, reg.provider_on(env.get(k))) for k in reg.PROVIDER_ENV]
    return None if any(v is None for _, v in on) else [k for k, v in on if v]


def verify(
    env: Mapping[str, str], argv: list[str], cwd: Path, config_dir: Path, target: str, expected: Mapping[str, str]
) -> tuple[Optional[str], account_dir.Resolved]:
    """最終の環境・起動引数・作業ディレクトリで `effective_env` を 1 回だけ打ち、従量の実効値の確かめを当てる。

    返りは（理由の符号か None、打った `Resolved`）。理由は `metered_overridden` で、値を含まない。`target` は確かめの種類
    （後の単位で `key:<名前>` の確かめを足す）。
    """
    r = account_dir.effective_env(env, config_dir, cwd, argv)
    want = _provider(expected)
    same = all((r.env.get(k) or "") == (expected.get(k) or "") for k in _CHECKED_VALUES)  # noqa: NDF501 利用者の入力: 無いと空文字を同じに読む
    ok = want is not None and _provider(r.env) == want and same and r.helper is None and not r.unresolved
    return (None if ok else "metered_overridden"), r


def target_env(env: Mapping[str, str], target: str, store: Path, state_dir: Path, cwd: Path, argv: list[str]) -> Union[Built, str]:
    """確定した替え先の子の環境と最終の起動引数。使えなければ値を含まない理由の符号を返す。

    登録済みアカウントは `prepare` の後に `account_env`（`Built.resolved` は `Prepared.resolved`。理由は `prepare` の
    `Prepared.reason`）。`metered` は `build` の後に `verify` を当て、外れれば `metered_overridden` を返す。
    `env` は `child_env(…, account=target, decided=True)` の出力。
    """
    if target == reg.METERED:
        got = build(env, target, store, state_dir, cwd, argv)
        if isinstance(got, str):
            return got
        child, final = got
        reason, resolved = verify(child, final, cwd, reg.shared_config_dir(env), target, child)
        return reason if reason is not None else Built(child, final, None, resolved)
    acc = registered(store).get(target)
    if acc is None:
        return "not_registered"
    prepared = account_dir.prepare(store, target, env, launch_of(env, store, state_dir, acc, cwd, argv))
    if not prepared.ok:
        return str(prepared.reason)
    usage = accounts.usage_path(state_dir, target, prepared.generation)
    return Built(session_env.account_env(env, prepared.config_dir, usage), list(argv), prepared, prepared.resolved)
