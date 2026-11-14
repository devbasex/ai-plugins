"""外の呼び手のエントリポイントの表（entrypoints）と見本（entrypoint-cases）を、呼び手の側から確かめる（D2+。#3390・#3341）。

`accepts` はこのテストの中の最小の写しで、エントリポイントを持つ子（表の owner_child）が同じ規則でエントリポイントを読む。
"""

from __future__ import annotations

import re

import pytest

from ndf.contracts import facts

EP = "entrypoints"
CASES = "entrypoint-cases"
ARG_KINDS = {"skill", "block", "report", "command"}


def _path_pattern(path: str) -> str:
    """ndf-files の path（`<名前>` は実行ごとに決まる 1 階層）を正規表現にする。"""
    return "".join("[^/]+" if part.startswith("<") else re.escape(part) for part in re.split(r"(<[^>]+>)", path))


def accepts(entrypoint: str, call: str, env: list[str]) -> bool:
    """エントリポイントが、呼び手の渡す値 `call` と環境変数 `env` を受けるか。"""
    ep = facts.row(EP, entrypoint)
    if not set(env) <= set(ep["env"]):
        return False
    if ep["kind"] in ARG_KINDS:
        return any(re.fullmatch(arg, call) for arg in ep["args"])
    if ep["kind"] == "hook":
        return call == ep["target"]
    paths = [facts.value("ndf-files", f, "path") for f in ep["files"]]
    return any(re.fullmatch(_path_pattern(p), call) for p in paths)


@pytest.mark.parametrize("case", facts.rows(CASES), ids=lambda r: r["id"])
def test_cases_are_judged_by_the_entrypoint(case):
    assert accepts(case["entrypoint"], case["call"], case["env"]) is case["valid"]


def test_every_entrypoint_has_an_accepted_case_and_new_loop_resume_are_shown():
    accepted = {r["entrypoint"] for r in facts.rows(CASES, valid=True)}
    hooks = set(facts.ids(EP, kind="hook"))
    assert set(facts.ids(EP)) - hooks <= accepted
    assert accepted & hooks
    assert {"new", "loop", "resume"} <= set(facts.ids(CASES, valid=True))


def test_entrypoints_refer_to_declared_env_files_and_places():
    env = set(facts.ids("env"))
    files = set(facts.ids("ndf-files"))
    for r in facts.rows(EP):
        assert set(r["env"]) <= env, r["id"]
        assert set(r.get("files", [])) <= files, r["id"]
        assert r["order"] in {"kept", "caller-first"}, r["id"]
        assert re.fullmatch(r"[A-Z][0-9]+[a-z]?\+?", r["owner_child"]), r["id"]
        assert ("args" in r) is (r["kind"] in ARG_KINDS), r["id"]
    for r in facts.rows(EP, kind="file"):
        assert {facts.value("ndf-files", f, "place") for f in r["files"]} == {r["target"]}, r["id"]


def test_files_the_service_reads_have_an_entrypoint():
    served = {f for r in facts.rows(EP, kind="file") for f in r["files"]}
    assert set(facts.ids("ndf-files", reader="service")) <= served


def test_env_the_service_sets_is_in_the_workflow_entrypoint():
    by_service = set(facts.ids("env", set_by="service"))
    assert by_service <= set(facts.value(EP, "skill-workflow", "env"))
    assert facts.value(EP, "skill-workflow", "env") == facts.value(EP, "block-next", "env")


def test_places_given_by_env_name_a_declared_variable():
    for r in facts.rows("places", root="env"):
        assert r["path"] == ".", r["id"]
