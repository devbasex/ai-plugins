"""このプラグインだけを読む claude の起動の引数（`ndf launch-args`。#3409・決定 74・S1-H4）。

外す設定と読む引数を、組み立てる関数の返りで確かめる。
"""

from __future__ import annotations

import json

from ndf import identity
from ndf.adapters import claude

ROOT = identity.ROOT

OLD = [{"id": f"{identity.FINAL_NAME}@some-market", "enabled": True}]


def test_launch_args_disable_every_old_plugin_but_never_this_one():
    # 入れ替え（S2）で名前が外す相手と同じになっても、自分を外さない
    argv = claude.plugin_args(ROOT, lambda: OLD).args
    disabled = json.loads(argv[argv.index("--settings") + 1])["enabledPlugins"] if "--settings" in argv else {}
    own = identity.load().name
    assert all(key.split("@", 1)[0] != own for key in disabled)
    assert set(disabled) == {e["id"] for e in OLD if e["id"].split("@", 1)[0] != own}
    assert argv[-2:] == ["--plugin-dir", str(ROOT)]


def test_launch_args_keep_a_plugin_whose_name_is_on_the_disabled_list(tmp_path):
    # 名前を読むだけで、そのプラグインの bin/ndf は起動しない（旧い状態の保存先に触れない）
    for entry in OLD:
        name = entry["id"].split("@", 1)[0]
        root = tmp_path / name
        (root / ".claude-plugin").mkdir(parents=True)
        (root / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": name}), encoding="utf-8")
        assert claude.plugin_args(root, lambda: OLD).args == ["--plugin-dir", str(root)]
    unnamed = tmp_path / "unnamed"
    expected = {e["id"]: False for e in OLD}
    assert claude.plugin_args(unnamed, lambda: OLD).args[:2] == [
        "--settings",
        json.dumps({"enabledPlugins": expected}, separators=(",", ":")),
    ]
