"""保存先・NDF が書くファイル・環境変数・PR 本文の部品・指示書の表（D2）を、読み手が頼る性質で確かめる。"""

from __future__ import annotations

import re

from ndf.contracts import facts

FILES = "ndf-files"


def test_untracked_locations_hold_only_untracked_files():
    tracked = facts.column("locations", "tracked")
    for r in facts.rows(FILES):
        if not tracked[r["location"]]:
            assert r["tracked"] is False, r["id"]


def test_measured_values_are_never_written_to_a_tracked_file():
    # 計測値は状態の保存先か、追跡されない設定の側（アカウントの登録簿の取得の記録。#3864 の決定 4）にだけ置く
    for r in facts.rows(FILES, kind="measured"):
        loc = facts.row("locations", r["location"])
        assert r["tracked"] is False and loc["root"] in {"state", "config"} and loc["tracked"] is False, r["id"]


def test_test_command_is_declared_in_one_file():
    holders = [fid for fid, keys in facts.column(FILES, "keys").items() if "test" in keys]
    assert holders == ["project"]
    # 計測値は設定と別のファイルが持ち、設定のキーと重ならない
    measured = {k for r in facts.rows(FILES, kind="measured") for k in r["keys"]}
    assert not measured & set(facts.value(FILES, "project", "keys"))


def test_relay_hub_exchange_files_are_declared():
    assert {r["path"] for r in facts.rows(FILES, location="relay-hub")} >= {"hub.json", "registry.json", "hub-log.jsonl"}


def test_env_names_and_secrets():
    names = facts.ids("env")
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]*", n) for n in names)
    assert {"NDF_RELAY_HUB_WAIT", "NDF_RELAY_STOP_WAIT", "NDF_RELAY_SPIN"} <= set(names)
    # 決定 13: 1 日の起動の上限は持たない
    assert "NDF_RELAY_MAX_STARTS" not in names
    assert set(facts.ids("env", secret=True)) == {
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "CLAUDE_CODE_OAUTH_TOKEN",
        "GH_TOKEN",
        "GOOGLE_WORKSPACE_CLI_TOKEN",
        "NDF_PROGRESS_DB",
        "NDF_SUPERVISE_CLAUDE_FALLBACK",
        "SLACK_BOT_TOKEN",
    }


def test_pr_body_orders_do_not_repeat_and_trailers_come_last():
    order = facts.column("pr-body", "order")
    assert len(set(order.values())) == len(order)
    trailers = [order[i] for i in facts.ids("pr-body", kind="trailer")]
    assert min(trailers) > max(v for k, v in order.items() if k not in facts.ids("pr-body", kind="trailer"))


def test_instruction_files_defaults():
    assert facts.ids("instruction-files", declared_default=True) == ("AGENTS.md", "CLAUDE.md", "KIRO.md")
    assert facts.ids("instruction-files", undeclared_check=True) == ("AGENTS.md", "CLAUDE.md")
    for fid in facts.ids("instruction-files", import_syntax=True):
        assert facts.value("instruction-files", fid, "runtime") == "claude"


def test_main_keys_are_declared_keys_and_guard_authorization():
    main = facts.column(FILES, "main_keys")
    for fid, keys in main.items():
        assert set(keys) <= set(facts.value(FILES, fid, "keys")), fid
    # 使ってよいランタイムは認可に関わり、PR の変更で広げられない
    assert set(main["runtime-policy"]) == set(facts.value(FILES, "runtime-policy", "keys"))


def test_each_ndf_settings_has_one_file():
    paths = [r["path"] for r in facts.rows(FILES, kind="ndf_settings")]
    assert len(paths) == len(set(paths))
    # リポジトリへ載る設定は `.ndf` に置く。手元だけの設定（中継のアカウントの指定など）は追跡しない保存先に置く
    tracked = facts.column("locations", "tracked")
    for r in facts.rows(FILES, kind="ndf_settings"):
        assert r["location"] == "ndf" or not tracked[r["location"]], r["id"]
