"""範囲外の課題の起票の部品（`issue-file.py`、#851）。

`gh` は `gh_call.RUNNER` を見本の応答へ差し替えて答える（GitHub へは届かない）。起票先の解決の配置ごとの
振る舞いは `skills/out-of-scope/tests/test_issue_target.py` が見る。
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from gh_fake import fake, rest_out  # noqa: E402,F401
import step_result  # noqa: E402

SCRIPT = HERE.parent / "issue-file.py"
_spec = importlib.util.spec_from_file_location("ndf_issue_file", SCRIPT)
issue_file = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(issue_file)

REPO = "o/r"
BODY = """## 何を見つけたか

入力 A で B になる。

## どこで見つけたか

`a.py:3`

## なぜこの変更の範囲外なのか

受け入れ条件に無い。根拠: Value 1（MVV 版 2）

## 直さないと何が起きるか

利用者が止まる。

## 由来
"""


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("NDF_PRESENTATION_DIR", str(tmp_path / "present"))
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("NDF_SKILL_REPO", raising=False)
    monkeypatch.chdir(tmp_path)


def run(*argv: str) -> tuple[int, dict]:
    """部品を呼び、終了コードと結果 JSON を返す。結果は #846 の形を通ることを確かめる（AC1）。"""
    import io
    from contextlib import redirect_stdout

    out = io.StringIO()
    with redirect_stdout(out), pytest.raises(SystemExit) as done:
        issue_file.main(list(argv))
    code = done.value.code
    lines = out.getvalue().strip().splitlines()
    assert len(lines) == 1, out.getvalue()
    obj = json.loads(lines[0])
    assert step_result.validate_result(obj, code) == []
    assert obj["tool"] == "issue-file"
    return code, obj


def found(*rows):
    return json.dumps([{"number": n, "title": f"t{n}", "url": f"https://github.com/{REPO}/issues/{n}", "state": "OPEN"} for n in rows])


def target_is(fake, name: str | None):
    if name is None:
        fake.on("repo", "view", rc=1, err="no repo")
    else:
        fake.on("repo", "view", out=name + "\n")


def body_file(tmp_path: Path, text: str = BODY) -> str:
    p = tmp_path / "body.md"
    p.write_text(text, encoding="utf-8")
    return str(p)


def created(fake, number: int = 7):
    fake.on("api", "-X", "POST", out=rest_out({"number": number, "html_url": f"https://github.com/{REPO}/issues/{number}"}, "201 Created"))


def posted(fake) -> list[dict]:
    return [json.loads(stdin) for args, stdin in fake.calls if args[:3] == ["api", "-X", "POST"]]


# --- resolve-target（AC2） ------------------------------------------------------------


def test_resolve_target_takes_the_environment_variable(fake, monkeypatch):
    monkeypatch.setenv("NDF_SKILL_REPO", "up/stream")
    target_is(fake, "up/stream")
    code, obj = run("resolve-target")
    assert code == 0
    assert obj["metrics"] == {"upstream": "up/stream", "target": "up/stream", "same": True, "source": "env"}


def test_resolve_target_does_not_stop_when_the_target_is_unreadable(fake, monkeypatch):
    monkeypatch.setenv("NDF_SKILL_REPO", "up/stream")
    target_is(fake, None)
    code, obj = run("resolve-target")
    assert code == 0
    assert obj["metrics"]["target"] is None and obj["metrics"]["same"] is False


def test_resolve_target_rejects_a_malformed_environment_variable(fake, monkeypatch):
    monkeypatch.setenv("NDF_SKILL_REPO", "not a repo")
    code, obj = run("resolve-target")
    assert code == 2 and obj["status"] == "stopped"
    assert fake.calls == []


def test_resolve_target_pauses_when_nothing_is_found(fake):
    target_is(fake, "example/app")
    code, obj = run("resolve-target")
    assert code == step_result.EXIT_PAUSE and obj["status"] == "gate"
    assert obj["metrics"]["upstream"] is None and obj["items"] == []
    assert obj["metrics"]["target"] == "example/app"


# --- dup（AC4） ---------------------------------------------------------------------


def test_dup_searches_open_issues_once(fake):
    fake.on("issue", "list", out=found(3, 4))
    code, obj = run("dup", "--repo", REPO, "--query", "起票先 解決")
    assert code == 0
    assert [i["number"] for i in obj["items"]] == [3, 4]
    assert set(obj["items"][0]) == {"number", "title", "url"}
    assert obj["metrics"]["count"] == 2
    (args, _), = fake.calls
    assert args[args.index("--state") + 1] == "open"
    assert args[args.index("--search") + 1] == "起票先 解決"
    assert args[args.index("--repo") + 1] == REPO


def test_dup_returns_an_empty_list_for_no_hits(fake):
    fake.on("issue", "list", out="[]")
    code, obj = run("dup", "--repo", REPO, "--query", "x")
    assert code == 0 and obj["items"] == [] and obj["metrics"]["count"] == 0


def test_dup_failure_is_not_zero_hits(fake):
    fake.on("issue", "list", rc=1, err="HTTP 502")
    code, obj = run("dup", "--repo", REPO, "--query", "x")
    assert code == 2 and obj["status"] == "stopped" and obj["items"] == []


def test_dup_rejects_a_malformed_repo_without_calling_github(fake):
    code, _ = run("dup", "--repo", "bad", "--query", "x")
    assert code == 2 and fake.calls == []


# --- note（AC9） --------------------------------------------------------------------


def _commented(fake):
    fake.on("api", "-X", "POST", out=rest_out({"html_url": f"https://github.com/{REPO}/issues/5#issuecomment-1"}, "201 Created"))


def test_note_with_an_origin_writes_the_fixed_line(fake):
    _commented(fake)
    code, obj = run("note", "--repo", REPO, "--number", "5", "--origin", "PR #1900")
    assert code == 0
    assert posted(fake) == [{"body": "同じ事象を PR #1900 の作業中に確認した。"}]
    assert obj["items"] == [{"repo": REPO, "number": 5, "url": f"https://github.com/{REPO}/issues/5#issuecomment-1"}]
    assert "issues/5/comments" in " ".join(fake.calls[0][0])


def test_note_with_a_counterpart_writes_the_counterpart_line(fake):
    _commented(fake)
    code, _ = run("note", "--repo", REPO, "--number", "5", "--counterpart", "example/app#12")
    assert code == 0
    assert posted(fake) == [{"body": "開発対象の側は example/app#12 として残した。"}]


@pytest.mark.parametrize(
    "extra",
    [[], ["--origin", "PR #1", "--counterpart", "a/b#1"], ["--origin", "PR 1"], ["--counterpart", "a/b#x"]],
)
def test_note_needs_exactly_one_well_formed_line(fake, extra):
    code, _ = run("note", "--repo", REPO, "--number", "5", *extra)
    assert code == 2 and fake.calls == []


def test_note_write_failure_is_one(fake):
    fake.on("api", "-X", "POST", rc=1, err="gh: Not Found (HTTP 404)")
    code, obj = run("note", "--repo", REPO, "--number", "5", "--origin", "issue #3")
    assert code == 1 and obj["status"] == "stopped"


# --- create（AC5〜AC7・AC13） ---------------------------------------------------------


def test_create_without_consent_writes_the_approval_material_and_stops(fake, tmp_path):
    target_is(fake, "example/app")
    code, obj = run("create", "--repo", REPO, "--title", "題", "--body-file", body_file(tmp_path), "--origin", "PR #1900", "--label", "bug")
    assert code == step_result.EXIT_GATE and obj["status"] == "gate"
    assert len(obj["metrics"]["digest"]) == 64
    assert obj["metrics"]["other_repo"] is True
    material = Path(obj["presentation_path"]).read_text(encoding="utf-8")
    for shown in (REPO, "題", "bug", obj["metrics"]["body_path"]):
        assert shown in material
    assert "PR #1900" in Path(obj["metrics"]["body_path"]).read_text(encoding="utf-8")
    assert posted(fake) == []


def test_create_with_the_shown_digest_files_the_issue_with_the_origin(fake, tmp_path):
    target_is(fake, REPO)
    created(fake)
    args = ["create", "--repo", REPO, "--title", "題", "--body-file", body_file(tmp_path), "--origin", "PR #1900"]
    _, gate = run(*args)
    assert gate["metrics"]["other_repo"] is False
    code, obj = run(*args, "--approved", gate["metrics"]["digest"])
    assert code == 0
    assert obj["items"] == [{"repo": REPO, "number": 7, "url": f"https://github.com/{REPO}/issues/7"}]
    (payload,) = posted(fake)
    assert payload["title"] == "題"
    assert "labels" not in payload  # 渡さなければ足さない（AC13）
    origin = payload["body"].split("## 由来", 1)[1]
    assert origin.count("PR #1900") == 1
    assert payload["body"].split("## 由来")[0] == BODY.split("## 由来")[0]


def test_create_passes_only_the_labels_it_was_given(fake, tmp_path):
    target_is(fake, REPO)
    created(fake)
    args = ["create", "--repo", REPO, "--title", "題", "--body-file", body_file(tmp_path), "--origin", "issue #3", "--label", "b", "--label", "a"]
    _, gate = run(*args)
    run(*args, "--approved", gate["metrics"]["digest"])
    assert posted(fake)[0]["labels"] == ["b", "a"]


def test_create_keeps_an_origin_already_written(fake, tmp_path):
    target_is(fake, REPO)
    text = BODY + "PR #1900\n"
    _, gate = run("create", "--repo", REPO, "--title", "t", "--body-file", body_file(tmp_path, text), "--origin", "PR #1900")
    body = Path(gate["metrics"]["body_path"]).read_text(encoding="utf-8")
    assert body.count("PR #1900") == 1


def test_an_origin_with_a_longer_number_is_not_the_same_origin(fake, tmp_path):
    target_is(fake, REPO)
    text = BODY + "PR #19000 の続き\n"
    _, gate = run("create", "--repo", REPO, "--title", "t", "--body-file", body_file(tmp_path, text), "--origin", "PR #1900")
    section = Path(gate["metrics"]["body_path"]).read_text(encoding="utf-8").split("## 由来")[1]
    assert section.strip().splitlines()[0] == "PR #1900"


def test_create_writes_the_counterpart_and_returns_the_note_command(fake, tmp_path):
    target_is(fake, "example/app")
    created(fake, 12)
    args = ["create", "--repo", "example/app", "--title", "t", "--body-file", body_file(tmp_path), "--origin", "PR #1900"]
    args += ["--counterpart", "up/stream#5"]
    _, gate = run(*args)
    code, obj = run(*args, "--approved", gate["metrics"]["digest"])
    assert code == 0
    section = posted(fake)[0]["body"].split("## 由来")[1]
    assert section.index("PR #1900") < section.index("上流リポジトリの側: up/stream#5")
    assert "note --repo up/stream --number 5 --counterpart example/app#12" in obj["next"]


def test_a_stale_digest_does_not_file(fake, tmp_path):
    target_is(fake, REPO)
    path = body_file(tmp_path)
    args = ["create", "--repo", REPO, "--title", "t", "--body-file", path, "--origin", "PR #1"]
    _, gate = run(*args)
    Path(path).write_text(BODY.replace("入力 A", "入力 Z"), encoding="utf-8")
    code, obj = run(*args, "--approved", gate["metrics"]["digest"])
    assert code == 1 and obj["status"] == "stopped"
    assert posted(fake) == []


@pytest.mark.parametrize("heading", issue_file.SKELETON[:4])
def test_a_missing_heading_stops_before_github(fake, tmp_path, heading):
    text = BODY.replace(f"## {heading}\n", "")
    code, obj = run("create", "--repo", REPO, "--title", "t", "--body-file", body_file(tmp_path, text), "--origin", "PR #1")
    assert code == 1
    assert {"heading": heading, "result": "missing"} in obj["items"]
    assert fake.calls == []


def test_a_missing_origin_heading_is_not_added(fake, tmp_path):
    text = BODY.replace("## 由来\n", "")
    code, obj = run("create", "--repo", REPO, "--title", "t", "--body-file", body_file(tmp_path, text), "--origin", "PR #1")
    assert code == 1 and [g["heading"] for g in obj["items"]] == ["由来"]


def test_an_empty_section_stops(fake, tmp_path):
    text = BODY.replace("利用者が止まる。", "   ")
    code, obj = run("create", "--repo", REPO, "--title", "t", "--body-file", body_file(tmp_path, text), "--origin", "PR #1")
    assert code == 1
    assert obj["items"] == [{"heading": "直さないと何が起きるか", "result": "empty"}]


def test_a_heading_inside_a_fence_is_not_counted(fake, tmp_path):
    text = BODY.replace("## どこで見つけたか\n\n`a.py:3`\n", "```markdown\n## どこで見つけたか\n```\n")
    code, obj = run("create", "--repo", REPO, "--title", "t", "--body-file", body_file(tmp_path, text), "--origin", "PR #1")
    assert code == 1 and {"heading": "どこで見つけたか", "result": "missing"} in obj["items"]


@pytest.mark.parametrize("origin", ["PR 1900", "#1900", "pr #1900", "PR #0", "Issue #3"])
def test_a_malformed_origin_stops_before_github(fake, tmp_path, origin):
    code, _ = run("create", "--repo", REPO, "--title", "t", "--body-file", body_file(tmp_path), "--origin", origin)
    assert code == 2 and fake.calls == []


def test_an_unreadable_body_file_is_two(fake, tmp_path):
    code, _ = run("create", "--repo", REPO, "--title", "t", "--body-file", str(tmp_path / "none.md"), "--origin", "PR #1")
    assert code == 2 and fake.calls == []


def test_the_other_repo_flag_is_true_when_the_target_is_unreadable(fake, tmp_path):
    target_is(fake, None)
    _, gate = run("create", "--repo", REPO, "--title", "t", "--body-file", body_file(tmp_path), "--origin", "PR #1")
    assert gate["metrics"]["other_repo"] is True


def test_a_create_failure_is_one(fake, tmp_path):
    target_is(fake, REPO)
    fake.on("api", "-X", "POST", rc=1, err="gh: Validation Failed (HTTP 422)")
    args = ["create", "--repo", REPO, "--title", "t", "--body-file", body_file(tmp_path), "--origin", "PR #1"]
    _, gate = run(*args)
    code, obj = run(*args, "--approved", gate["metrics"]["digest"])
    assert code == 1 and obj["status"] == "stopped"


# --- by-origin（AC8） -----------------------------------------------------------------


def _by_query(answers: dict[tuple[str, str], str]):
    def answer(args, stdin):
        key = (args[args.index("--repo") + 1], args[args.index("--search") + 1])
        from gh_fake import gh_call

        return gh_call.GhResult(0, answers[key], "") if answers[key] != "FAIL" else gh_call.GhResult(1, "", "HTTP 502")

    return answer


def test_by_origin_searches_every_pair_and_merges_issues(fake):
    answers = {
        ("o/r", '"PR #9"'): found(1, 2),
        ("o/r", '"issue #8"'): found(2),
        ("up/s", '"PR #9"'): "[]",
        ("up/s", '"issue #8"'): found(2),
    }
    fake.on_fn("issue", "list", fn=_by_query(answers))
    code, obj = run("by-origin", "--origin", "PR #9", "--origin", "issue #8", "--repo", "o/r", "--repo", "up/s")
    assert code == 0
    assert obj["metrics"]["searches"] == 4
    keyed = {(i["repo"], i["number"]): i["origins"] for i in obj["items"]}
    assert keyed == {("o/r", 1): ["PR #9"], ("o/r", 2): ["PR #9", "issue #8"], ("up/s", 2): ["issue #8"]}
    for args, _ in fake.calls:
        assert args[args.index("--state") + 1] == "all"


def test_by_origin_fails_when_any_search_fails(fake):
    fake.on_fn("issue", "list", fn=_by_query({("o/r", '"PR #9"'): found(1), ("o/r", '"issue #8"'): "FAIL"}))
    code, obj = run("by-origin", "--origin", "PR #9", "--origin", "issue #8", "--repo", "o/r")
    assert code == 2 and obj["items"] == []


def test_by_origin_with_upstream_adds_the_upstream_once(fake, monkeypatch):
    monkeypatch.setenv("NDF_SKILL_REPO", "o/r")
    fake.on("issue", "list", out="[]")
    code, obj = run("by-origin", "--origin", "PR #9", "--origin", "issue #8", "--repo", "o/r", "--with-upstream")
    assert code == 0 and obj["metrics"]["searches"] == 2 and obj["metrics"]["upstream"] == "o/r"
    assert not any(a[:2] == ["repo", "view"] for a, _ in fake.calls)


def test_by_origin_continues_when_the_upstream_is_undecided(fake):
    fake.on("issue", "list", out=found(4))
    code, obj = run("by-origin", "--origin", "PR #9", "--repo", "o/r", "--with-upstream")
    assert code == 0 and obj["metrics"]["upstream"] is None and obj["metrics"]["searches"] == 1


def test_by_origin_needs_a_repository(fake):
    code, _ = run("by-origin", "--origin", "PR #9", "--with-upstream")
    assert code == 2 and fake.calls == []


def test_by_origin_rejects_a_malformed_origin(fake):
    code, _ = run("by-origin", "--origin", "PR9", "--repo", "o/r")
    assert code == 2 and fake.calls == []


# --- 引数の誤り・GitHub の呼び出し（AC1・I7） ------------------------------------------


def test_an_argument_error_is_still_a_result(fake):
    code, obj = run("create", "--repo", REPO)
    assert code == 2 and obj["status"] == "stopped"


def test_the_script_does_not_start_gh_directly():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "subprocess" not in text
