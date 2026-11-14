"""置き場・NDF が書くファイル・環境変数・PR 本文の部品・指示書の表（D2）を、読み手が頼る性質で確かめる。"""

from __future__ import annotations

import random
import re

from ndf.contracts import facts

FILES = "ndf-files"


def test_untracked_places_hold_only_untracked_files():
    tracked = facts.column("places", "tracked")
    for r in facts.rows(FILES):
        if not tracked[r["place"]]:
            assert r["tracked"] is False, r["id"]


def test_measured_values_are_never_written_to_a_tracked_file():
    for r in facts.rows(FILES, kind="measured"):
        assert r["tracked"] is False and facts.value("places", r["place"], "root") == "state", r["id"]


def test_test_command_is_declared_in_one_file():
    holders = [fid for fid, keys in facts.column(FILES, "keys").items() if "test" in keys]
    assert holders == ["project"]
    # 計測値は宣言と別のファイルが持ち、宣言のキーと重ならない
    measured = {k for r in facts.rows(FILES, kind="measured") for k in r["keys"]}
    assert not measured & set(facts.value(FILES, "project", "keys"))


def test_relay_hub_exchange_files_are_declared():
    assert {r["path"] for r in facts.rows(FILES, place="relay-hub")} >= {"hub.json", "registry.json", "hub-log.jsonl"}


def test_env_names_and_secrets():
    names = facts.ids("env")
    assert all(re.fullmatch(r"[A-Z][A-Z0-9_]*", n) for n in names)
    assert {"NDF_RELAY_HUB_WAIT", "NDF_RELAY_STOP_WAIT", "NDF_RELAY_MAX_STARTS"} <= set(names)
    assert set(facts.ids("env", secret=True)) == {"ANTHROPIC_API_KEY", "GH_TOKEN", "SLACK_BOT_TOKEN"}


def _assemble(parts: dict[str, str]) -> list[str]:
    """部品（id → 中身）を order の昇順に並べた本文の行。中身の無い節は見出しだけ。"""
    order = facts.column("pr-body", "order")
    lines: list[str] = []
    for pid in sorted(parts, key=order.__getitem__):
        r = facts.row("pr-body", pid)
        lines += [r["text"] + parts[pid]] if r["kind"] != "section" else [r["text"], parts[pid]]
    return lines


def _section(lines: list[str], heading: str) -> list[str]:
    """節の中身: 見出しの次から、次の部品（見出し・行の印・署名）の前まで。"""
    starts = tuple(r["text"] for r in facts.rows("pr-body"))
    i = lines.index(heading) + 1
    j = next((k for k in range(i, len(lines)) if lines[k].startswith(starts)), len(lines))
    return lines[i:j]


def test_mode_line_stays_outside_the_decisions_section():
    parts = {"decisions": "- 決定 1: 表を置く", "mode": "standard / 通した工程: 設計", "generated": " [Claude Code]", "changes": "無し"}
    keys = list(parts)
    random.Random(3093).shuffle(keys)
    body = _assemble({k: parts[k] for k in keys})
    assert body == _assemble(parts)
    decisions = facts.value("pr-body", "decisions", "text")
    assert _section(body, decisions) == ["- 決定 1: 表を置く"]
    assert body.index("モード: standard / 通した工程: 設計") < body.index(decisions)


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
    assert {"production_branch", "branch_flow"} <= set(main["worktree"])


def test_each_declaration_has_one_file():
    paths = [r["path"] for r in facts.rows(FILES, kind="declaration")]
    assert len(paths) == len(set(paths))
    assert all(r["place"] == "ndf" for r in facts.rows(FILES, kind="declaration"))
