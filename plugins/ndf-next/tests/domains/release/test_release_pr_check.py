"""main 宛ての PR の検査（#3572 の I7・I9）と、取り込み方の宣言の読み（#3119 の I1・I2）。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from jsonschema import Draft202012Validator

from ndf import identity
from ndf.domains.release import cli as release_cli
from ndf.domains.release import declaration as decl_mod
from ndf.domains.release import pr_check

from ._fakes import FakeHub, write_json
from ._fakes import pull as fake_pull

MANIFEST = "tools/kit/.claude-plugin/plugin.json"
ALLOWED = frozenset({MANIFEST, "CHANGELOG.md", ".claude-plugin/marketplace.json"})


def _facts(
    head_ref: str = "sprint/x", after: str = "1.2.3", changed: tuple[str, ...] = ("tools/kit/a.py",), labels: tuple[str, ...] = ()
) -> pr_check.PrFacts:
    return pr_check.PrFacts(head_ref, {MANIFEST: "1.2.3"}, {MANIFEST: after}, changed, labels, "verify/next", ALLOWED)


def test_a_pr_that_keeps_the_version_has_no_problem() -> None:
    assert pr_check.problems(_facts()) == []


def test_a_pr_that_changes_the_version_is_refused_with_the_reason() -> None:
    """受け入れ条件 11・I7: 版数を変えた main 宛ての PR は落ち、理由に「版数は CD がマージの時点に上げる」が出る。"""
    (got,) = pr_check.problems(_facts(after="1.3.0", changed=("tools/kit/a.py", MANIFEST)))
    assert got["kind"] == "version" and got["name"] == MANIFEST and "版数は CD がマージの時点に上げる" in got["detail"]


def test_the_bump_pr_may_change_the_version() -> None:
    assert pr_check.problems(_facts(head_ref="release/v1.3.0", after="1.3.0", changed=(MANIFEST, "CHANGELOG.md"))) == []
    # 版上げの名前でも、版のファイルの外を変えれば版数の破れになる
    assert [p["kind"] for p in pr_check.problems(_facts(head_ref="release/v1.3.0", after="1.3.0", changed=(MANIFEST, "src/x.py")))] == [
        "version"
    ]


def test_two_level_labels_and_the_verify_ref_are_refused() -> None:
    """I9: head が検証の ref ならその破れだけを返す。上げ幅のラベルが 2 つ以上なら level。"""
    (level,) = pr_check.problems(_facts(labels=("release:minor", "release:patch")))
    assert level["kind"] == "level" and level["name"] == "release:minor, release:patch"
    (verify,) = pr_check.problems(_facts(head_ref="verify/next", after="9.9.9"))
    assert verify["kind"] == "verify-ref" and "検証の ref は main へ戻さない" in verify["detail"]


def _decl(root: Path, flow: dict[str, Any] | None) -> Path:
    write_json(
        root / ".ndf" / "worktree.json",
        {"base_branch": "main", "production_branch": "main", **({"branch_flow": flow} if flow is not None else {})},
    )
    return root


def test_check_does_nothing_outside_the_github_flow(tmp_path: Path) -> None:
    """決定 7: 取り込み方の行が github-flow でなければ何も見ない（入れ替えまでは今の NDF の PR が版を上げる）。"""
    assert pr_check.check(_decl(tmp_path, {"promotion": "whole"}), FakeHub(), None, 7) is None  # type: ignore[arg-type]
    hub = FakeHub(pulls={7: fake_pull(number=7, base="feature/x")})
    assert pr_check.check(_decl(tmp_path, {"promotion": "direct"}), hub, None, 7) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("flow", "row"),
    [(None, "github-flow"), ({"promotion": "direct"}, "github-flow"), ({"promotion": "whole"}, "promote-whole")],
)
def test_flow_of_reads_the_row(tmp_path: Path, flow: dict | None, row: str) -> None:
    assert decl_mod.flow_of(_decl(tmp_path, flow)) == row


@pytest.mark.parametrize("flow", [{"promotion": "merge"}, {"promotion": "direct", "start": "base"}])
def test_flow_of_refuses_values_outside_the_row(tmp_path: Path, flow: dict) -> None:
    with pytest.raises(decl_mod.DeclarationError):
        decl_mod.flow_of(_decl(tmp_path, flow))


def test_require_github_flow_names_the_other_row(tmp_path: Path) -> None:
    decl_mod.require_github_flow(_decl(tmp_path, {"promotion": "direct"}))
    with pytest.raises(decl_mod.DeclarationError, match="取り込み方 promote-whole は ndf-next のリリースで扱わない"):
        decl_mod.require_github_flow(_decl(tmp_path, {"promotion": "whole"}))


CROSS = {"checks": ["cross-review"], "diff": "pr"}
NONE = {"checks": [], "diff": "none"}


@pytest.mark.parametrize(
    "reviews",
    [None, {"impl": NONE}, {"impl": NONE, "sprint": CROSS, "design": CROSS, "spec": NONE}],
)
def test_review_problems_accepts_what_the_row_allows(tmp_path: Path, reviews: dict | None) -> None:
    flow = {"promotion": "direct", **({"reviews": reviews} if reviews is not None else {})}
    assert decl_mod.review_problems(_decl(tmp_path, flow)) == []


def test_review_problems_names_the_release_row_and_the_missing_cross_review(tmp_path: Path) -> None:
    """#3119 の I2: 行 github-flow では release の行を書けず、impl・sprint のどちらも cross-review しなければ破れ。"""
    flow = {"promotion": "direct", "reviews": {"impl": NONE, "sprint": NONE, "release": CROSS}}
    got = decl_mod.review_problems(_decl(tmp_path, flow))
    assert [(p["kind"], p["name"]) for p in got] == [("reviews", "release"), ("reviews", "cross-review")]
    assert decl_mod.review_problems(_decl(tmp_path, {"promotion": "whole", "reviews": {"release": CROSS}})) == []


def test_verify_of_reads_one_verify_row(tmp_path: Path) -> None:
    row = {
        "target": "検証版",
        "kind": "auto",
        "trigger": "wf",
        "channel": "verify",
        "versioned": False,
        "verify": {"ref": "verify/next", "marketplace": "x-next"},
    }
    write_json(tmp_path / ".ndf" / "project.json", {"delivery": [row]})
    assert decl_mod.verify_of(tmp_path) == decl_mod.VerifyDecl("verify/next", "x-next")
    for rows in ([], [row, row], [{**row, "verify": {"ref": "", "marketplace": "x"}}]):
        write_json(tmp_path / ".ndf" / "project.json", {"delivery": rows})
        with pytest.raises(decl_mod.DeclarationError):
            decl_mod.verify_of(tmp_path)


@pytest.mark.parametrize(
    "schema", ["contracts/schemas/declarations/project.schema.json", "skills/development-workflow/schemas/project.schema.json"]
)
def test_the_project_schema_accepts_the_verify_row_and_rejects_unknown_keys(schema: str) -> None:
    """決定 9: 宣言のスキーマが verify_of の読む行（channel verify・鍵 verify）を通し、verify の知らない鍵を断る。"""
    doc = json.loads((identity.ROOT / schema).read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(doc)
    v = Draft202012Validator(doc)
    row = {"target": "検証版", "kind": "auto", "trigger": "wf", "channel": "verify", "versioned": False}
    ok = {**row, "verify": {"ref": "verify/next", "marketplace": "x-next"}}
    assert not list(v.iter_errors({"version": 1, "delivery": [ok, {**row, "channel": "production"}]}))
    for bad in (
        {**row, "verify": {"ref": "verify/next", "marketplace": "x-next", "branch": "x"}},
        {**row, "verify": {"ref": "verify/next"}},
        {**ok, "unknown": 1},
    ):
        assert list(v.iter_errors({"version": 1, "delivery": [bad]}))


def test_cli_pr_check_exits_1_with_the_items_and_0_outside_the_flow(monkeypatch, capsys, tmp_path: Path) -> None:
    monkeypatch.setattr(release_cli, "_hub", lambda args: FakeHub())
    problem = {"kind": "version", "name": MANIFEST, "detail": pr_check.VERSION_DETAIL}
    monkeypatch.setattr(pr_check, "check", lambda *a: [problem])
    assert release_cli.main(["pr-check", "--root", str(tmp_path), "--pr", "7"]) == 1
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert out["cause"]["kind"] == "check_failed" and out["items"] == [problem]
    monkeypatch.setattr(pr_check, "check", lambda *a: None)
    assert release_cli.main(["pr-check", "--root", str(tmp_path), "--pr", "7"]) == 0
