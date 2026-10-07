"""pr-review-steps.py（collect / finish / delegate）と、それが使う共通の部品の追加分（#860）。

GitHub と外部 CLI は呼ばない。`gh_parts.py` と `external-ai.py` は、モジュールの `GH_PARTS` /
`EXTERNAL_AI` を偽のスクリプトへ差し替える。偽物は受けた argv を `calls.jsonl` に積む。
上限越えのテストだけは本物の `external-ai.py` と応答しない偽の `codex` を使う（#345）。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
SKILL = HERE.parent
PLUGIN_ROOT = SKILL.parents[1]
SCRIPT = SKILL / "scripts" / "pr-review-steps.py"
sys.path.insert(0, str(PLUGIN_ROOT / "scripts" / "lib"))
import gh_graphql  # noqa: E402
import gh_pr_info  # noqa: E402
import gh_sections  # noqa: E402
import repo  # noqa: E402
from step_result import validate_result  # noqa: E402

PR = 1836


def _load():
    spec = importlib.util.spec_from_file_location("pr_review_steps", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


steps = _load()


# ---------------- git の一時リポジトリ ----------------


@pytest.fixture(autouse=True)
def _git_identity(monkeypatch):
    for k, v in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@e"}.items():
        monkeypatch.setenv(k, v)
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)


def _git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True, text=True).stdout.strip()


def _init(path: Path, branch: str = "main") -> Path:
    path.mkdir(parents=True)
    _git(path, "init", "-q", "-b", branch)
    (path / "README.md").write_text("x\n", encoding="utf-8")
    _git(path, "add", ".")
    _git(path, "commit", "-q", "-m", "init")
    return path


def _origin_and_clone(tmp_path: Path, branches=("develop",)) -> tuple[Path, Path]:
    """`main` と `branches` を持つ origin（bare）と、その clone。"""
    src = _init(tmp_path / "src")
    for b in branches:
        _git(src, "branch", b)
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(src), str(origin))
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    return origin, clone


def _declare(root: Path, base: str) -> None:
    (root / ".ndf").mkdir(exist_ok=True)
    (root / ".ndf" / "worktree.json").write_text(json.dumps({"version": 1, "base_branch": base}), encoding="utf-8")


# ---------------- existing_base_branch（I1・I2） ----------------


def test_declared_base_in_fetched_refs(tmp_path):
    _, clone = _origin_and_clone(tmp_path)
    _declare(clone, "develop")
    assert repo.existing_base_branch(clone) == ("develop", "宣言（取得済みの参照）")


def test_declared_base_found_only_by_asking_origin(tmp_path):
    origin, clone = _origin_and_clone(tmp_path, branches=())
    _git(origin, "branch", "develop", "main")  # clone の後に origin へ足す（取得していない）
    _declare(clone, "develop")
    assert repo.existing_base_branch(clone) == ("develop", "宣言（origin へ問い合わせ）")


def test_declared_base_missing_everywhere_does_not_fall_back(tmp_path):
    _, clone = _origin_and_clone(tmp_path, branches=())
    _declare(clone, "develop")
    name, why = repo.existing_base_branch(clone)
    assert name is None and "develop" in why


def test_ls_remote_suffix_match_is_not_the_branch(tmp_path):
    origin, clone = _origin_and_clone(tmp_path, branches=())
    _git(origin, "branch", "x/refs/heads/develop", "main")
    _declare(clone, "develop")
    assert repo.existing_base_branch(clone)[0] is None


def test_no_declaration_uses_origin_head(tmp_path):
    _, clone = _origin_and_clone(tmp_path)
    assert repo.existing_base_branch(clone) == ("main", "既定ブランチ")


@pytest.mark.parametrize("branch", ["main", "master"])
def test_no_declaration_no_origin_head_uses_local_main_or_master(tmp_path, branch):
    root = _init(tmp_path / "r", branch)
    assert repo.existing_base_branch(root) == (branch, "既定ブランチ")


def test_nothing_decides_the_base(tmp_path):
    root = _init(tmp_path / "r", "trunk")
    name, why = repo.existing_base_branch(root)
    assert name is None and why


# ---------------- 本来の判定（I4）と検査（I3） ----------------


def _c(severity, stage=None, **kw):
    return {"severity": severity, "body": "本文", **({"stage": stage} if stage else {}), **kw}


@pytest.mark.parametrize(
    "comments, intent",
    [
        ([_c("critical")], "REQUEST_CHANGES"),
        ([_c("major"), _c("nit")], "REQUEST_CHANGES"),
        ([_c("minor", "spec")], "REQUEST_CHANGES"),
        ([_c("minor"), _c("nit", "quality")], "COMMENT"),
        ([], "APPROVE"),
    ],
)
def test_decide_event(comments, intent):
    assert steps.decide_event(comments)["intent"] == intent


def test_decide_event_counts():
    v = steps.decide_event([_c("major"), _c("minor", "spec"), _c("minor")])
    assert v["by_severity"] == {"critical": 0, "major": 1, "minor": 2, "nit": 0} and v["spec_unmet"] == 1


@pytest.mark.parametrize("bad", [_c("high"), _c("major", "design"), {"severity": "major", "body": " "}, "x"])
def test_check_findings_rejects_unknown_values(bad):
    assert steps.check_findings({"comments": [bad]})


def test_check_findings_needs_a_comment_array():
    assert steps.check_findings({"summary": "s"}) and steps.check_findings([]) and not steps.check_findings({"comments": []})


# ---------------- 偽の子スクリプト ----------------

FAKE_GH_PARTS = r"""#!{py}
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
d = Path(os.environ["FAKE_DIR"])
with open(d / "calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(a, ensure_ascii=False) + "\n")
st = json.loads((d / "state.json").read_text(encoding="utf-8"))
if a[0] == "pr-info":
    out = Path(a[a.index("--out-dir") + 1]); out.mkdir(parents=True, exist_ok=True)
    (out / "pr.diff").write_text("diff --git a/a.py b/a.py\n+x\ndiff --git a/b.md b/b.md\n+y\n", encoding="utf-8")
    meta = {{"kind": "pr", "name": "#1", "result": "open", "repo": "o/r", "number": int(a[1]), "url": "https://x/pull/1",
             "title": st.get("title", "t"), "body": "受け入れ条件: 1", "head_sha": "abc", "base_branch": "develop"}}
    items = [meta, {{"kind": "diff", "name": "diff", "result": "saved", "path": str(out / "pr.diff")}}]
    items += [{{"kind": "thread", "name": "T1", "result": "unresolved", "thread_id": "T1", "path": "a.py", "line": 3,
               "body": "[major / logic] 空を弾く"}}]
    metrics = {{"unavailable": st["unavailable"]}} if st.get("unavailable") else {{}}
    print(json.dumps({{"tool": "pr-info", "status": "ok", "summary": "s", "items": items, "metrics": metrics}}))
    sys.exit(0)
if a[0] == "review-post":
    intent = json.loads(Path(a[a.index("--result") + 1]).read_text(encoding="utf-8"))["event"]
    posted_as = "COMMENT" if st.get("own") else intent
    item = {{"kind": "review", "name": a[a.index("--seat") + 1], "result": "posted", "intent": intent,
             "posted_as": posted_as, "review_url": "https://x/pull/1#pullrequestreview-9"}}
    code = st.get("post_code", 0)
    print(json.dumps({{"tool": "review-post", "status": "ok" if code == 0 else "stopped", "summary": "s",
                      "items": [item], "metrics": {{}}}}))
    sys.exit(code)
sys.exit(2)
"""

FAKE_EXTERNAL_AI = r"""#!{py}
import json, os, sys
from pathlib import Path
a = sys.argv[1:]
d = Path(os.environ["FAKE_DIR"])
with open(d / "calls.jsonl", "a", encoding="utf-8") as f:
    f.write(json.dumps(["external-ai", *a], ensure_ascii=False) + "\n")
Path(a[a.index("--output-file") + 1]).write_text((d / "findings.src").read_text(encoding="utf-8"), encoding="utf-8")
print(json.dumps({{"tool": "external-ai", "status": "ok", "summary": "s", "items": [], "metrics": {{"outcome": "ok"}}}}))
"""


class Fakes:
    def __init__(self, tmp_path: Path, monkeypatch):
        self.dir = tmp_path / "fake"
        self.dir.mkdir()
        self.state = {}
        self.write_state()
        monkeypatch.setenv("FAKE_DIR", str(self.dir))
        gp = self.dir / "gh_parts.py"
        gp.write_text(FAKE_GH_PARTS.format(py=sys.executable), encoding="utf-8")
        monkeypatch.setattr(steps, "GH_PARTS", gp)
        self.out = tmp_path / "out"

    def write_state(self, **kw):
        self.state.update(kw)
        (self.dir / "state.json").write_text(json.dumps(self.state), encoding="utf-8")

    def fake_external_ai(self, monkeypatch, findings: dict):
        ea = self.dir / "external-ai.py"
        ea.write_text(FAKE_EXTERNAL_AI.format(py=sys.executable), encoding="utf-8")
        (self.dir / "findings.src").write_text(json.dumps(findings, ensure_ascii=False), encoding="utf-8")
        monkeypatch.setattr(steps, "EXTERNAL_AI", ea)

    def calls(self) -> list[list[str]]:
        f = self.dir / "calls.jsonl"
        return [json.loads(x) for x in f.read_text(encoding="utf-8").splitlines()] if f.is_file() else []

    def posts(self) -> list[list[str]]:
        return [c for c in self.calls() if c[0] == "review-post"]


@pytest.fixture
def fakes(tmp_path, monkeypatch):
    root = _init(tmp_path / "work")
    monkeypatch.chdir(root)
    return Fakes(tmp_path, monkeypatch)


def _main(capsys, *argv) -> tuple[int, dict]:
    with pytest.raises(SystemExit) as e:
        steps.main([str(a) for a in argv])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert validate_result(out, e.value.code) == [], out
    return e.value.code, out


def _findings(tmp_path: Path, data) -> Path:
    f = tmp_path / "findings.json"
    f.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return f


# ---------------- collect ----------------


def test_collect_pr_passes_unresolved_threads_into_the_context(fakes, capsys):
    code, out = _main(capsys, "collect", PR, "--out-dir", fakes.out, "--repo", "o/r")
    assert code == 0, out
    m = out["metrics"]
    assert (m["mode"], m["pr"], m["unresolved_threads"], m["changed_files"]) == ("pr", PR, 1, 2)
    ctx = Path(m["context"]).read_text(encoding="utf-8")
    assert "`a.py:3` [major / logic] 空を弾く" in ctx and "受け入れ条件: 1" in ctx and m["findings"] in ctx
    assert fakes.calls()[0][:4] == ["pr-info", str(PR), "--with", "diff,threads"]


def test_collect_removes_the_previous_findings(fakes, capsys):
    fakes.out.mkdir()
    (fakes.out / "findings.json").write_text("{}", encoding="utf-8")
    _main(capsys, "collect", PR, "--out-dir", fakes.out)
    assert not (fakes.out / "findings.json").exists()


def test_collect_stops_without_threads(fakes, capsys):
    fakes.write_state(unavailable=["threads"])
    code, out = _main(capsys, "collect", PR, "--out-dir", fakes.out)
    assert code == 2 and "threads" in out["summary"]


def _stub_pr_info(monkeypatch, info, code=0):
    """`collect_pr` が起動する子スクリプトを、決まった結果を返す関数へ差し替える。呼んだ argv を返す。"""
    calls: list[list] = []

    def child(argv, cwd=None):
        calls.append([str(x) for x in argv])
        return info, code

    monkeypatch.setattr(steps, "_child", child)
    return calls


def test_collect_pr_metrics_and_sections_are_fixed(tmp_path, monkeypatch):
    """現状固定（I-007）: pr-info の結果から metrics と文脈の節を組む形。"""
    diff = tmp_path / "pr.diff"
    diff.write_text("diff --git a/a.py b/a.py\n+x\ndiff --git a/d/b.md b/d/b.md\n+y\n", encoding="utf-8")
    meta = {
        "kind": "pr",
        "repo": "o/r",
        "url": "https://x/pull/7",
        "title": "題",
        "head_sha": "abc",
        "base_branch": "develop",
        "body": "  本文  ",
    }
    threads = [
        {"kind": "thread", "path": "a.py", "line": 3, "body": " 指摘 "},
        {"kind": "thread", "path": None, "line": None, "body": None},
    ]
    info = {"status": "ok", "items": [meta, {"kind": "diff", "path": str(diff)}, *threads], "metrics": {}}
    calls = _stub_pr_info(monkeypatch, info)
    a = steps.argparse.Namespace(out_dir=str(tmp_path / "out"), repo="o/r")

    metrics, sections, d = steps.collect_pr(a, tmp_path, 7)

    assert d == (tmp_path / "out").resolve() and d.is_dir()
    assert calls == [[str(steps.GH_PARTS), "pr-info", "7", "--with", "diff,threads", "--out-dir", str(d), "--repo", "o/r"]]
    assert metrics == {"mode": "pr", "pr": 7, "head_sha": "abc", "base_branch": "develop", "changed_files": 2, "unresolved_threads": 2}
    assert len(sections) == 4
    assert sections[0] == "\n".join(
        [
            "## 対象\n",
            "- repo: o/r",
            "- PR: #7 https://x/pull/7",
            "- 題: 題",
            "- head の SHA: abc",
            "- ベースブランチ: develop",
            f"- 作業ディレクトリ: {tmp_path}",
        ]
    )
    assert sections[1].endswith("\n\n本文")
    assert sections[2] == f"## 差分\n\n- 差分のファイル: `{diff}`\n- 変更ファイル（2）:\n  - `a.py`\n  - `d/b.md`"
    assert sections[3].startswith("## 未解決のスレッド\n\n- `a.py:3` 指摘\n- `(位置なし):-` \n\n")


def test_collect_pr_without_diff_threads_or_body_is_fixed(tmp_path, monkeypatch):
    """現状固定（I-007）: 差分・スレッド・本文が無いとき、--repo を渡さないとき。"""
    calls = _stub_pr_info(monkeypatch, {"status": "ok", "items": [{"kind": "pr"}], "metrics": {"unavailable": []}})
    a = steps.argparse.Namespace(out_dir=str(tmp_path / "out"), repo=None)

    metrics, sections, _ = steps.collect_pr(a, tmp_path, 7)

    assert "--repo" not in calls[0]
    assert metrics == {"mode": "pr", "pr": 7, "head_sha": None, "base_branch": None, "changed_files": 0, "unresolved_threads": 0}
    assert sections[0].splitlines()[2:4] == ["- repo: ", "- PR: #7 "]
    assert sections[1].endswith("\n\n（本文なし）")
    assert sections[2] == "## 差分\n\n- 差分のファイル: `None`\n- 変更ファイル（0）:\n"
    assert sections[3].startswith("## 未解決のスレッド\n\nなし\n\n")


@pytest.mark.parametrize(
    "info, code, message",
    [
        ({"status": "stopped", "summary": "読めない"}, 2, "PR #7 を取得できない: 読めない"),
        ({"status": "ok", "summary": "s"}, 1, "PR #7 を取得できない: s"),
        ({"status": "gate"}, 0, "PR #7 を取得できない: None"),
        ({"status": "ok", "items": [], "metrics": {"unavailable": ["diff", "threads"]}}, 0, "PR #7 の diff, threads を取得できない"),
    ],
)
def test_collect_pr_stops_are_fixed(tmp_path, monkeypatch, info, code, message):
    """現状固定（I-007）: pr-info の失敗・取得できない部分は読めない（2）として止まる。"""
    _stub_pr_info(monkeypatch, info, code)
    a = steps.argparse.Namespace(out_dir=str(tmp_path / "out"), repo=None)

    with pytest.raises(steps.StepError) as e:
        steps.collect_pr(a, tmp_path, 7)

    assert (str(e.value), e.value.code) == (message, steps.EXIT_UNREADABLE)


def test_collect_branch_reports_base_files_stat_and_log(tmp_path, monkeypatch, capsys):
    _, clone = _origin_and_clone(tmp_path)
    _declare(clone, "develop")
    _git(clone, "switch", "-q", "-c", "feat/x", "origin/develop")
    (clone / "new.py").write_text("print(1)\n", encoding="utf-8")
    _git(clone, "add", "new.py")
    _git(clone, "commit", "-q", "-m", "足した")
    monkeypatch.chdir(clone)
    code, out = _main(capsys, "collect", "--branch", "--out-dir", tmp_path / "out")
    assert code == 0, out
    assert (out["metrics"]["base_branch"], out["metrics"]["changed_files"]) == ("develop", 1)
    ctx = Path(out["metrics"]["context"]).read_text(encoding="utf-8")
    assert "new.py" in ctx and "1 file changed" in ctx and "足した" in ctx and "origin/develop" in ctx


def test_collect_branch_stops_when_the_declared_base_is_missing(tmp_path, monkeypatch, capsys):
    _, clone = _origin_and_clone(tmp_path, branches=())
    _declare(clone, "develop")
    monkeypatch.chdir(clone)
    code, out = _main(capsys, "collect", "--branch", "--out-dir", tmp_path / "out")
    assert code == 3 and not (tmp_path / "out" / "context.md").exists()


def test_scripts_do_not_read_the_worktree_declaration_themselves():
    # 宣言を読むのは lib/repo.py だけ（写しを作らない）
    for f in (SCRIPT, SKILL / "SKILL.md"):
        assert "worktree.json" not in f.read_text(encoding="utf-8"), f


# ---------------- finish ----------------


def test_finish_posts_once_with_round_0_and_keeps_the_intent(fakes, tmp_path, capsys):
    fakes.write_state(own=True)
    data = {
        "summary": "総評",
        "comments": [
            {"path": "a.py", "line": 3, "severity": "minor", "category": "性能", "body": "内包表記にする"},
            {"severity": "major", "stage": "spec", "category": "受け入れ条件", "body": "条件 4 のテストが無い"},
        ],
    }
    f = _findings(tmp_path, data)
    code, out = _main(capsys, "finish", "--findings", f, "--pr", PR, "--out-dir", fakes.out)
    assert code == 0, out
    posts = fakes.posts()
    assert len(posts) == 1
    p = posts[0]
    assert p[p.index("--round") + 1] == "0" and p[p.index("--seat") + 1] == "pr-review-host"
    assert (out["metrics"]["intent"], out["metrics"]["posted_as"]) == ("REQUEST_CHANGES", "COMMENT")
    payload = json.loads(Path(p[p.index("--payload") + 1]).read_text(encoding="utf-8"))
    assert payload["summary"].startswith("### 仕様適合（満たさない）") and "条件 4" in payload["summary"]
    assert payload["comments"] == [{"severity": "minor", "body": "[minor / 性能] 内包表記にする", "path": "a.py", "line": 3}]
    assert json.loads(f.read_text(encoding="utf-8")) == data  # 指摘ファイルは書き換えない


@pytest.mark.parametrize("bad", [{"comments": [_c("high")]}, {"comments": [_c("major", "x")]}, "not json"])
def test_finish_does_not_post_unreadable_findings(fakes, tmp_path, capsys, bad):
    f = tmp_path / "findings.json"
    f.write_text(bad if isinstance(bad, str) else json.dumps(bad), encoding="utf-8")
    code, _ = _main(capsys, "finish", "--findings", f, "--pr", PR, "--out-dir", fakes.out)
    assert code == 2 and fakes.posts() == []


def test_finish_returns_the_post_failure(fakes, tmp_path, capsys):
    fakes.write_state(post_code=1)
    code, out = _main(capsys, "finish", "--findings", _findings(tmp_path, {"comments": []}), "--pr", PR, "--out-dir", fakes.out)
    assert code == 1 and out["status"] == "stopped" and Path(out["metrics"]["payload"]).is_file()


def test_finish_branch_reports_without_posting(fakes, tmp_path, capsys):
    f = _findings(tmp_path, {"comments": [_c("major", path="a.py", line=1)]})
    code, out = _main(capsys, "finish", "--findings", f, "--branch", "--out-dir", fakes.out)
    assert code == 0 and fakes.posts() == []
    assert "a.py:1" in Path(out["metrics"]["report"]).read_text(encoding="utf-8")


def test_payload_survives_quotes_and_command_substitution(fakes, tmp_path, capsys):
    body = 'PR の題 "x" と $(rm -rf /) と `y`'
    f = _findings(tmp_path, {"summary": body, "comments": []})
    _main(capsys, "finish", "--findings", f, "--pr", PR, "--out-dir", fakes.out)
    assert json.loads((fakes.out / "payload.json").read_text(encoding="utf-8"))["summary"] == body


# ---------------- delegate ----------------


def test_delegate_builds_the_prompt_from_the_skill_and_posts(fakes, monkeypatch, capsys):
    fakes.fake_external_ai(monkeypatch, {"comments": [_c("nit", path="a.py", line=3)]})
    code, out = _main(capsys, "delegate", "codex", PR, "--out-dir", fakes.out)
    assert code == 0, out
    run = next(c for c in fakes.calls() if c[0] == "external-ai")
    assert run[1:3] == ["run", "codex"] and run[run.index("--phase") + 1] == "review"
    assert run[run.index("--output-file") + 1] == str(fakes.out / "findings.json")
    prompt = Path(run[run.index("--prompt-file") + 1]).read_text(encoding="utf-8")
    section = gh_sections.get_section((SKILL / "SKILL.md").read_text(encoding="utf-8"), "## 観点")
    assert section.strip() in prompt and "`a.py:3` [major / logic] 空を弾く" in prompt
    post = fakes.posts()
    assert len(post) == 1 and post[0][post[0].index("--seat") + 1] == "pr-review-codex"
    assert out["metrics"]["intent"] == "COMMENT"


def test_delegate_does_not_post_unreadable_output(fakes, monkeypatch, capsys):
    fakes.fake_external_ai(monkeypatch, {"comments": [_c("blocker")]})
    code, _ = _main(capsys, "delegate", "agy", PR, "--out-dir", fakes.out)
    assert code == 2 and fakes.posts() == []


HANGING_CLI = """#!/bin/sh
case "$1 $2" in "login status"|"auth status") echo ok; exit 0;; esac
[ -t 0 ] || cat > /dev/null
sleep 30
"""


def test_delegate_times_out_without_posting(fakes, tmp_path, monkeypatch, capsys):
    """応答しない CLI と短い上限で、上限の後に 1 で終わり、投稿しない（#345）。"""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "codex").write_text(HANGING_CLI, encoding="utf-8")
    (bin_dir / "codex").chmod(0o755)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    monkeypatch.setenv("NDF_EXTERNAL_AI_TMP_DIR", str(tmp_path / "t"))
    monkeypatch.setenv("NDF_SKIP_AUTH_CHECK", "1")
    code, out = _main(capsys, "delegate", "codex", PR, "--out-dir", fakes.out, "--timeout", "2", "--poll", "1")
    assert code == 1, out
    assert out["items"][0]["result"] in ("timeout", "stalled") and fakes.posts() == []


# ---------------- 未解決のスレッドの本文（gh_graphql・gh_pr_info） ----------------


def test_thread_items_carry_the_body(monkeypatch):
    monkeypatch.setattr(
        gh_graphql, "unresolved_threads", lambda slug, pr: [{"thread_id": "T1", "path": "a.py", "line": "3", "body": "空を弾く"}]
    )
    items, count = gh_pr_info._thread_items("o/r", PR)
    assert count == 1
    assert items[0] == {
        "kind": "thread",
        "name": "T1",
        "result": "unresolved",
        "thread_id": "T1",
        "path": "a.py",
        "line": 3,
        "body": "空を弾く",
    }


@pytest.mark.skipif(shutil.which("jq") is None, reason="jq が無い")
def test_unresolved_threads_jq_folds_the_first_comment_body():
    assert "comments(first: 1) { nodes { body } }" in gh_graphql.UNRESOLVED_THREADS_QUERY
    long = "あ" * 300
    nodes = [
        {"id": "T1", "isResolved": False, "path": "a.py", "line": 3, "comments": {"nodes": [{"body": "一行目\n\t二行目\r\n三"}]}},
        {"id": "T2", "isResolved": True, "path": "b.py", "line": 1, "comments": {"nodes": []}},
        {"id": "T3", "isResolved": False, "path": None, "line": None, "comments": {"nodes": [{"body": long}]}},
    ]
    resp = json.dumps({"data": {"repository": {"pullRequest": {"reviewThreads": {"nodes": nodes}}}}})
    out = subprocess.run(["jq", "-r", gh_graphql.UNRESOLVED_THREADS_JQ], input=resp, capture_output=True, text=True, check=True).stdout
    threads = gh_graphql.unresolved_threads("o/r", PR, output=lambda argv: out)
    assert threads[0] == {"thread_id": "T1", "path": "a.py", "line": "3", "body": "一行目 二行目 三"}
    assert threads[1]["body"] == "あ" * 200 and len(threads) == 2
