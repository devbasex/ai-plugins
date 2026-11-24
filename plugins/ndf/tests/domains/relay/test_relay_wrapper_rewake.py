"""ラッパーが起床の hook の登録を、確かめたバージョンの子にだけ渡す（#4068 の受け入れ条件 3・13）。

`test_relay_wrapper.py` が 1,000 行の規則に近いため、#4068 の分をこのファイルに置く（実装計画の「修正対象」の頭）。
"""

from __future__ import annotations

import json
import time

from ndf.domains.relay import accounts, claude_args, wrapper
from ndf.domains.relay.account_decide import Usage
from relay_children import FakeChild, put_usages, register, run_wrapper

USER_SETTINGS = json.dumps({"env": {"X": "1"}})
LIMITED = b"  \xe2\x8e\xbf  You've hit your individual spend limit \xc2\xb7 resets 10:40pm\r\n"


def _settings_of(argv: list) -> list:
    return [v for o, v in claude_args.options(argv[1:]) if o == "--settings"]


def _switching(tmp_path):
    def prepare(_d, state):
        store = register(tmp_path, "team", "main")
        put_usages(
            store,
            [Usage("team", 50.0, 50.0, None, None, False), Usage("main", 5.0, 5.0, None, None, False)],
            fetched_at=time.time() - 3600,
        )
        accounts.use(state, "team")

    return prepare


def test_user_settings_stay_once_on_the_first_launch_and_on_resume(tmp_path):
    # 4068:3（変えない側）: 利用者の --settings は、初回の起動と --resume の再開でそのまま 1 つだけ
    _, spawn, _ = run_wrapper(
        tmp_path, [FakeChild([LIMITED], None), FakeChild([], 0)], _switching(tmp_path), args=("--settings", USER_SETTINGS)
    )
    (first, env1), (second, env2) = spawn.calls
    assert second[1] == "--resume"
    assert _settings_of(first) == [USER_SETTINGS] and _settings_of(second) == [USER_SETTINGS]
    assert "NDF_RELAY_REWAKE" not in env1 and "NDF_RELAY_REWAKE" not in env2


def test_no_settings_are_added_while_no_version_is_listed(tmp_path):
    # 実装の PR ではバージョンの一覧が空なので、どの子にも起床の hook の登録を渡さない
    _, spawn, _ = run_wrapper(tmp_path, [FakeChild([LIMITED], None), FakeChild([], 0)], _switching(tmp_path))
    for argv, env in spawn.calls:
        assert _settings_of(argv) == [] and "NDF_RELAY_REWAKE" not in env
    assert wrapper.CLAUDE == spawn.calls[0][0][0]
