"""最新のバージョンの解決と失敗の課題の報告（`scripts/runtime-smoke-latest.py`、#1982 の受け入れ条件 7・8・9 と I4、#3687 の受け入れ条件 1）。

取得元の応答と `gh` は偽物（モジュールの `fetch_json` と `run_gh` の置き換え）にする。
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
SCRIPT = REPO / "scripts" / "runtime-smoke-latest.py"


@pytest.fixture
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location("runtime_smoke_latest_mod", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "runtime_smoke_latest_mod", m)
    spec.loader.exec_module(m)
    return m


def sources(claude="2.1.300", codex="0.163.0"):
    return {
        "https://registry.npmjs.org/-/package/@anthropic-ai/claude-code/dist-tags": {"latest": claude, "next": "9.9.9"},
        "https://registry.npmjs.org/-/package/@openai/codex/dist-tags": {"latest": codex, "alpha": "0.200.0-alpha.1"},
    }


def use_sources(mod, monkeypatch, table):
    def fake(url):
        if url not in table:
            raise mod.ResolveError(url, "unreachable")
        return table[url]

    monkeypatch.setattr(mod, "fetch_json", fake)


def run(mod, *argv):
    return mod.main(list(argv))


def test_resolve_all_emits_versions_list_shape(mod, monkeypatch, capsys):
    use_sources(mod, monkeypatch, sources())
    assert run(mod, "resolve", "--runtime", "all") == 0
    lines = capsys.readouterr().out.splitlines()
    pairs = dict(line.split("=", 1) for line in lines)
    assert pairs == {
        "CLAUDE_CODE_VERSION": "2.1.300",
        "CODEX_VERSION": "0.163.0",
    }
    # 一覧と同じキーの集合を出す（版を上げる手順で写せる）
    listed = REPO / "tests" / "runtime-smoke" / "cli-versions.env"
    keys = {ln.split("=", 1)[0] for ln in listed.read_text(encoding="utf-8").splitlines() if ln and not ln.startswith("#")}
    assert set(pairs) == keys


def test_resolve_one_runtime_emits_only_its_keys(mod, monkeypatch, capsys):
    use_sources(mod, monkeypatch, sources(codex="0.170.2"))
    assert run(mod, "resolve", "--runtime", "codex") == 0
    assert capsys.readouterr().out == "CODEX_VERSION=0.170.2\n"


@pytest.mark.parametrize(
    "runtime, table",
    [
        ("claude", sources(claude="latest")),
        ("codex", {k: v for k, v in sources().items() if "@openai/codex" not in k}),
    ],
    ids=["bad-version", "unreachable"],
)
def test_resolve_fails_with_source_url(mod, monkeypatch, capsys, runtime, table):
    use_sources(mod, monkeypatch, table)
    assert run(mod, "resolve", "--runtime", runtime) == 1
    out = capsys.readouterr()
    assert out.out == ""
    assert "https://" in out.err


class FakeGh:
    def __init__(self, issues, fail=False):
        self.issues = issues
        self.fail = fail
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, args, stdin=None):
        self.calls.append((args, stdin))
        if self.fail and args[:2] != ["issue", "list"]:
            raise subprocess.CalledProcessError(1, ["gh", *args], stderr="boom")
        if args[:2] == ["issue", "list"]:
            return json.dumps(self.issues)
        if args[:2] == ["issue", "create"]:
            return "https://github.com/o/r/issues/99\n"
        return ""

    def kinds(self):
        return [c[0][:2] for c in self.calls]


RUN_URL = "https://github.com/o/r/actions/runs/123"


SAMPLE = "tests/samples/github/rest/issue.json"


def outcome(runtime, status="failure", versions="", **extra):
    return {"runtime": runtime, "status": status, "versions": versions, **extra}


def mismatch(peer="github", sample=SAMPLE, detail="user/login: type", kind="mismatch"):
    return {"sample": sample, "peer": peer, "kind": kind, "detail": detail}


def run_report(mod, monkeypatch, tmp_path, outcomes=None, samples=None, issues=(), fail=False):
    """成果物を置いて `report` を打ち、終了コードと偽の gh を返す。`outcomes` はファイル名 → 中身（文字列はそのまま書く）。"""
    folder = tmp_path / "outcome"
    folder.mkdir()
    for name, data in (outcomes or {}).items():
        (folder / name).write_text(data if isinstance(data, str) else json.dumps(data), encoding="utf-8")
    result = tmp_path / "result.json"
    if samples is not None:
        result.write_text(samples if isinstance(samples, str) else json.dumps({"tool": "samples", "items": samples}), encoding="utf-8")
    gh = FakeGh(list(issues), fail=fail)
    monkeypatch.setattr(mod, "run_gh", gh)
    argv = ["--run-url", RUN_URL, "--outcomes", str(folder), "--samples", str(result), "--repo", "o/r"]
    return run(mod, "report", *argv), gh


def written(gh):
    """課題へ渡った（題・本文）。コメントの題は空。"""
    return [(a[a.index("--title") + 1] if "--title" in a else "", body) for a, body in gh.calls if a[:2] != ["issue", "list"]]


def test_version_keys_are_the_keys_the_resolvers_emit(mod, monkeypatch):
    use_sources(mod, monkeypatch, sources())
    assert set(mod.VERSION_KEYS) == set(mod.RUNTIMES)
    for runtime, keys in mod.VERSION_KEYS.items():
        assert tuple(key for key, _ in mod.resolve(runtime)) == keys


def test_report_creates_issue_with_runtime_versions_and_run_url(mod, monkeypatch, tmp_path):
    outcomes = {
        "claude.json": outcome("claude", versions="CLAUDE_CODE_VERSION=2.1.300\n"),
        "codex.json": outcome("codex", status="success", versions="CODEX_VERSION=0.163.0\n"),
    }
    issues = [{"number": 5, "title": "最新の版のスモークが落ちた: codex"}]
    code, gh = run_report(mod, monkeypatch, tmp_path, outcomes, issues=issues)
    assert code == 0
    assert gh.kinds() == [["issue", "list"], ["issue", "create"]]
    args, body = gh.calls[1]
    assert args[args.index("--title") + 1] == "最新の版のスモークが落ちた: claude"
    assert args[args.index("--repo") + 1] == "o/r"
    assert "claude" in body and "CLAUDE_CODE_VERSION=2.1.300" in body and RUN_URL in body
    assert not any(word in body for word in ("Closes", "Fixes", "Resolves"))


@pytest.mark.parametrize(
    ("given", "title"),
    [
        ({"outcomes": {"codex.json": outcome("codex")}}, "最新の版のスモークが落ちた: codex"),
        ({"samples": [mismatch()]}, "テストの見本が本物の記述と食い違った: github"),
    ],
    ids=["runtime", "samples"],
)
def test_report_comments_on_open_issue_instead_of_creating(mod, monkeypatch, tmp_path, given, title):
    issues = [{"number": 8, "title": f"{title} (old)"}, {"number": 9, "title": title}, {"number": 7, "title": title}]
    code, gh = run_report(mod, monkeypatch, tmp_path, **given, issues=issues)
    assert code == 0
    assert gh.kinds() == [["issue", "list"], ["issue", "comment"]]
    args, body = gh.calls[1]
    assert args[2] == "7"
    assert RUN_URL in body


def test_report_fails_when_gh_fails_after_trying_the_rest(mod, monkeypatch, tmp_path, capsys):
    outcomes = {f"{r}.json": outcome(r) for r in mod.RUNTIMES}
    code, gh = run_report(mod, monkeypatch, tmp_path, outcomes, [mismatch()], fail=True)
    assert code == 1
    assert gh.kinds().count(["issue", "create"]) == 3
    assert "gh" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("samples", "counts"),
    [
        (None, {}),
        ("", {}),
        ("not json", {}),
        ([], {}),
        ([mismatch(kind="unreachable", detail="HTTP 503")], {}),
        ([mismatch(), mismatch(detail="state: enum"), mismatch("other", kind="unreachable")], {"github": 2}),
        ([mismatch("b-peer"), mismatch("a-peer"), mismatch("a-peer", kind="unreachable")], {"a-peer": 1, "b-peer": 1}),
        ([mismatch(detail=f"k{n}: type") for n in range(60)], {"github": 60}),
    ],
    ids=["no-file", "empty-file", "broken-file", "no-items", "unreachable-only", "mismatch", "per-peer", "many"],
)
def test_report_creates_one_issue_per_mismatched_peer(mod, monkeypatch, tmp_path, samples, counts):
    code, gh = run_report(mod, monkeypatch, tmp_path, samples=samples)
    assert code == 0
    got = written(gh)
    assert [title.rsplit(": ", 1)[1] for title, _ in got] == list(counts)
    for (_, body), count in zip(got, counts.values(), strict=True):
        assert f"{count} 件" in body and SAMPLE in body and RUN_URL in body
        assert "HTTP 503" not in body
        assert body.count(SAMPLE) == min(count, mod.SHOWN_ITEMS)


@pytest.mark.parametrize(
    ("outcomes", "failed", "refused"),
    [
        ({}, [], 0),
        ({"claude.json": outcome("claude"), "codex.json": outcome("codex")}, ["claude", "codex"], 0),
        ({"claude.json": outcome("claude", status="success"), "codex.json": outcome("codex", status="cancelled")}, [], 0),
        ({"EVIL.json": outcome("EVIL"), "kiro.json": outcome("kiro")}, [], 0),
        ({"claude.json": outcome("EVIL"), "codex.json": outcome("claude")}, [], 2),
        ({"claude.json": outcome("claude", status="EVIL"), "codex.json": outcome("codex", status=["failure"])}, [], 2),
        ({"claude.json": "{EVIL", "codex.json": outcome("codex")}, ["codex"], 1),
        ({"claude.json": "[]", "codex.json": json.dumps("EVIL")}, [], 2),
    ],
    ids=["none", "both", "not-failure", "unknown-runtime", "name-differs", "bad-status", "broken-json", "not-object"],
)
def test_report_reads_only_failed_outcomes_of_known_runtimes(mod, monkeypatch, tmp_path, capsys, outcomes, failed, refused):
    code, gh = run_report(mod, monkeypatch, tmp_path, outcomes)
    assert code == 0
    assert [title for title, _ in written(gh)] == [f"最新の版のスモークが落ちた: {r}" for r in failed]
    out = capsys.readouterr()
    assert "EVIL" not in f"{out.out}{out.err}{gh.calls}"
    # 読めたのに報告しないファイルは標準エラーへ知らせる（知らない名前のファイルは読まない）
    assert len(out.err.splitlines()) == refused


@pytest.mark.parametrize(
    ("versions", "kept"),
    [
        ("EVIL=1\nCLAUDE_CODE_VERSION=2.1.300\nCODEX_VERSION=9.9.9", "CLAUDE_CODE_VERSION=2.1.300"),
        ("CLAUDE_CODE_VERSION=1.0`@EVIL", "解決できなかった"),
        ("CLAUDE_CODE_VERSION=2.1.300 EVIL\n CLAUDE_CODE_VERSION=2.1.300", "解決できなかった"),
        ("", "解決できなかった"),
        (["CLAUDE_CODE_VERSION=2.1.300"], "解決できなかった"),
        (None, "解決できなかった"),
    ],
    ids=["other-keys", "bad-version", "bad-line", "empty", "list", "null"],
)
def test_report_copies_only_allowed_version_lines(mod, monkeypatch, tmp_path, versions, kept):
    data = outcome("claude", versions=versions, title="EVIL", note="EVIL")
    code, gh = run_report(mod, monkeypatch, tmp_path, {"claude.json": data})
    assert code == 0
    [(_, body)] = written(gh)
    assert f"```text\n{kept}\n```" in body
    assert "EVIL" not in body and "9.9.9" not in body


BAD_ITEMS = {
    "peer-case": mismatch("EVIL"),
    "peer-markup": mismatch("a`@EVIL"),
    "peer-newline": mismatch("evil\n", detail="EVIL"),
    "peer-type": mismatch(["EVIL"]),
    "sample-root": mismatch(sample="EVIL.json"),
    "sample-parent": mismatch(sample="tests/samples/../EVIL.json"),
    "sample-newline": mismatch(sample=f"{SAMPLE}\n", detail="EVIL"),
    "detail-lines": mismatch(detail="a\n```\nEVIL"),
    "detail-long": mismatch(detail="EVIL" + "x" * 497),
    "detail-empty": mismatch(sample="tests/samples/evil/a.json", detail=""),
    "detail-surrogate": mismatch(detail="EVIL\ud800"),
    "kind": mismatch(kind="EVIL"),
    "not-object": "EVIL",
}


@pytest.mark.parametrize("bad", BAD_ITEMS.values(), ids=BAD_ITEMS)
def test_report_drops_sample_items_out_of_shape(mod, monkeypatch, tmp_path, capsys, bad):
    edge = mismatch(detail="x" * 500)
    code, gh = run_report(mod, monkeypatch, tmp_path, samples=[bad, edge])
    assert code == 0
    [(title, body)] = written(gh)
    assert title == "テストの見本が本物の記述と食い違った: github"
    assert "食い違い: 1 件" in body and "x" * 500 in body
    out = capsys.readouterr()
    assert "evil" not in f"{title}{body}{out.out}{out.err}{gh.calls}".lower()
    assert "1 件" in out.err
