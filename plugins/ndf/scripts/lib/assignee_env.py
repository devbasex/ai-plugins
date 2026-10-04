"""担当の起動の環境と、振り替え先のアカウントの選び方（収束ループ共通層、#919）。

cross-review と cross-refactoring の担当は CLI プロセスとして起動する。claude の担当にアカウント
（`claude_accounts` の登録済みアカウント）があれば、その環境（`CLAUDE_CONFIG_DIR` をアカウントの
設定ディレクトリへ向け、トークンとスコープの変数を外したもの）で起動する。それ以外は元の環境のまま。

**状態ファイルと耐久の記録へは環境ではなくアカウントの名前だけを書く。** 環境は起動のたびに
`seat_env` で名前から組み直す。認証の情報のファイルはここから開かない（開くのは `claude_accounts` の中だけ）。

`claude_accounts` は使うときだけ import する。読む側の多くはアカウントを使わずにこのモジュールを読む。
"""

from __future__ import annotations

import os
import sys
from typing import Callable, Mapping, Optional

_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

NAME_ENV = "NDF_CLAUDE_ACCOUNT"  # `claude_accounts.NAME_ENV` と同じ。起動したときのアカウント


def initial_account(environ: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """起動したときの claude のアカウントの名前（`NDF_CLAUDE_ACCOUNT`）。無ければ None（共有の設定）。"""
    environ = os.environ if environ is None else environ
    return environ.get(NAME_ENV) or None


def seat_env(seat: str, account: Optional[str], base: Optional[Mapping[str, str]] = None) -> dict[str, str]:
    """担当の起動の環境。claude の席にアカウントがあればその環境、無い・用意できなければ元の環境の写し。"""
    env = dict(os.environ if base is None else base)
    if not account or not seat.startswith("claude"):
        return env
    import claude_accounts as ca

    return ca.account_env(account, env) or env


def pick_account(tried: frozenset[str], base: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """試したアカウント（`tried`）の外から、環境を用意できるアカウントの名前を選ぶ。無ければ None。

    `assignment.after_no_result` の `pick_account` に渡す形。登録が無ければ使用量を取りに行かない。"""
    import claude_accounts as ca

    if not ca.registered():
        return None
    choice, env = ca.choose_env(exclude=set(tried), base=dict(os.environ if base is None else base))
    return choice.name if env is not None else None


def account_picker(base: Optional[Mapping[str, str]] = None) -> Callable[[frozenset[str]], Optional[str]]:
    """`pick_account` を `base` の環境に固定した関数を返す。"""
    return lambda tried: pick_account(tried, base)
