"""`ndf plan` の `claude -p` の起動の前の確かめと、上限での打ち直しの替え先（#3604 の 2・11、決定 1・8・9・12）。

`adapters/claude.call_claude` は、中継のマーカー（`NDF_RELAY_DIR`）のある環境の `NDF_CLAUDE_ACCOUNT`（中継の子から継いだ起動先）があるときだけ、`ndf` の
エントリポイントが入れたこのモジュールの `begin`・`retry` を呼ぶ（下の層の `adapters` は中継の部品を読めないため）。

- 登録済みアカウント: 認証の上書きを `account_dir.auth_overrides` で確かめ、`acquire` で worker 自身の使用の届けを取り、
  関所（`account_dir.gated`）で起動して `attach` し、終わった後に `release` で消す
- 従量の接続・登録簿の API キー: `account_choice.target_env` で替え先と同じ合成と確かめを当て、`Built.pin` が親の環境の
  `NDF_RELAY_PIN` と同じときだけ起動する。スロットを取った後の起動の直前に `account_choice.reverify` で確かめ直す（#4133）。
  キーの子は、起動の形がキーを渡せないとき（`keys` が偽）に `key_gate` で起動しない
- 上限: `account_choice.retry_env` の替え先を、登録済みアカウントなら `prepare` の届けのまま関所で起動し、終わったら `settle` する
返す理由に値（キー・トークン・接続先）を入れない。
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Callable, Mapping, Optional, Union

from ndf import identity
from ndf.adapters.claude import AccountRefused, AccountRun, Failure
from ndf.domains.relay import account_choice, account_dir, launch_target, session_env
from ndf.domains.relay import account_registry as reg
from ndf.domains.relay.account_decide import KEY_PREFIX

AUTH_LINE = "アカウント {name} の claude -p を起動しない: 認証を上書きする設定がある（{items}）。その設定から項目を外す"
LEASE_LINE = "アカウント {name} の claude -p を起動しない: 使用の届けを取れない（{why}）"
TARGET_LINE = "{name} の claude -p を起動しない（{code}: {why}）"
RETRY_LINE = "ndf plan: アカウント {name} が利用上限に当たった。{target} で打ち直す"
# 親の接続の識別が読み直した設定と食い違うときの理由（launch_target.SKIP_WHY に無い符号）
CHANGED_WHY = {
    "key_changed": "親が起動した後に、同じ名前の API キーが登録し直されたか消された",
    "metered_changed": "親が起動した後に、従量の接続の設定が替わった",
}


def _drop(*paths: Optional[Path]) -> None:
    for path in paths:
        if path is not None:
            path.unlink(missing_ok=True)


def _files(built: account_choice.Built) -> Callable[[bool], None]:
    """合成の一時ファイルと固定のファイル（とその払い出しのマーク）を消す後始末。"""
    pin = built.pin_file
    ok = pin.with_name(pin.name.removesuffix(account_choice.PIN_SUFFIX) + account_choice.PIN_OK_SUFFIX) if pin is not None else None
    return lambda _launched: _drop(built.settings_file, pin, ok)


def _refused(name: str, code: str) -> AccountRefused:
    why = CHANGED_WHY[code] if code in CHANGED_WHY else launch_target.why_of(code, name)
    return AccountRefused(Failure.REFUSED, TARGET_LINE.format(name=name, code=code, why=why))


def _connection(argv: list[str], env: Mapping[str, str], cwd: Path, keys: bool) -> Union[AccountRun, AccountRefused]:
    """従量の接続と API キーの親からの起動（詳細設計書の「`call_claude`（`key:` の親）・（`metered` の親）」）。"""
    name, store, state = env["NDF_CLAUDE_ACCOUNT"], reg.store_dir(env), identity.load().state_dir
    if not keys and account_choice.key_child(env, name, store):
        return _refused(name, "key_gate")
    child = session_env.child_env(env, state, name, decided=True)
    got = account_choice.target_env(child, name, store, state, account_choice.LaunchArgs(cwd, argv))
    if isinstance(got, str):
        return _refused(name, got)
    if got.pin is None or got.pin != env.get("NDF_RELAY_PIN"):
        _files(got)(False)
        return _refused(name, "key_changed" if name.startswith(KEY_PREFIX) else "metered_changed")
    return AccountRun(got.env, got.argv, end=_files(got), recheck=lambda: _reverified(name, got, cwd))


def _reverified(name: str, built: account_choice.Built, cwd: Path) -> Optional[str]:
    """スロットを待つ間に替わった接続先・認証を起動の直前に確かめ直す。外れれば起動しない 1 行（理由の符号だけ）。"""
    code = account_choice.reverify(built, cwd)
    return _refused(name, code).detail if code is not None else None


def _account(argv: list[str], env: Mapping[str, str], cwd: Path) -> Union[AccountRun, AccountRefused]:
    """登録済みアカウントの親からの起動（認証の上書きの確かめと、worker 自身の使用の届け）。"""
    name, store = env["NDF_CLAUDE_ACCOUNT"], reg.store_dir(env)
    config = env.get("CLAUDE_CONFIG_DIR")
    gen = account_choice.inherited_generation(env)
    if not config or gen is None:
        return AccountRefused(
            Failure.LAUNCH, LEASE_LINE.format(name=name, why="CLAUDE_CONFIG_DIR か NDF_ACCOUNT_GENERATION が無いか形が違う")
        )
    where = cwd / config  # 子は相対の設定ディレクトリを `Popen(cwd=…)` から解く（絶対パスならそのまま）
    refused = _overridden(name, env, where, cwd, argv)
    if refused is not None:
        return AccountRefused(Failure.REFUSED, refused)
    try:
        lease = account_dir.acquire(store, name, where, gen)
    except reg.AccountError as exc:
        return AccountRefused(Failure.LAUNCH, LEASE_LINE.format(name=name, why=exc))
    return AccountRun(
        dict(env),
        argv,
        gate=account_dir.gated,
        attach=lambda pid: account_dir.attach(store, name, lease, pid),
        end=lambda _launched: account_dir.release(store, name, lease),
        recheck=lambda: _overridden(name, env, where, cwd, argv),  # スロットを待つ間に足された上書きを起動の直前に確かめ直す
    )


def _overridden(name: str, env: Mapping[str, str], config: Path, cwd: Path, argv: list[str]) -> Optional[str]:
    """認証の上書きがあれば起動しない 1 行（項目名だけ）。無ければ None。"""
    items = account_dir.auth_overrides(env, config, cwd, argv)
    return AUTH_LINE.format(name=name, items="・".join(items)) if items else None


def _is_connection(name: str) -> bool:
    return name == reg.METERED or name.startswith(KEY_PREFIX)


def begin(argv: list[str], env: Mapping[str, str], cwd: Path, keys: bool) -> Union[AccountRun, AccountRefused]:
    """`env` の `NDF_CLAUDE_ACCOUNT`（空でない）の起動の前の確かめ。`keys` は起動の形がキーを渡せるか（`claude.KEY_FORMS`）。"""
    name = env["NDF_CLAUDE_ACCOUNT"]
    return _connection(argv, env, cwd, keys) if _is_connection(name) else _account(argv, env, cwd)


def retry(argv: list[str], env: Mapping[str, str], cwd: Path, keys: bool) -> Optional[AccountRun]:
    """上限に当たった登録済みアカウントの替え先で 1 回だけ打ち直す起動。替え先が無い・従量の接続と API キーの親なら None。"""
    name = env["NDF_CLAUDE_ACCOUNT"]
    if _is_connection(name):
        return None
    got = account_choice.retry_env(env, identity.load().state_dir, time.time(), cwd, argv, keys)
    if got is None:
        return None
    built, target = got
    print(RETRY_LINE.format(name=name, target=target), file=sys.stderr)
    p = built.prepared
    if p is None or p.lease is None:
        return AccountRun(built.env, built.argv, end=_files(built), recheck=lambda: _reverified(target, built, cwd))
    store, lease = reg.store_dir(env), p.lease

    def end(launched: bool) -> None:
        if launched:
            account_dir.settle(store, target, env, p.generation, lease)
        else:
            account_dir.release(store, target, lease)

    return AccountRun(
        built.env,
        built.argv,
        gate=account_dir.gated,
        attach=lambda pid: account_dir.attach(store, target, lease, pid),
        end=end,
        recheck=lambda: _overridden(target, built.env, p.config_dir, cwd, built.argv),
    )
