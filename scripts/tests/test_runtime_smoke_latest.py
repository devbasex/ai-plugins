"""最新の版の解決と失敗の課題の報告（`scripts/runtime-smoke-latest.py`、#1982 の受け入れ条件 7・8・9 と I4）。

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
SHA256 = {"x86_64": "a" * 64, "aarch64": "b" * 64}
SHA512 = {"linux_amd64": "c" * 128, "linux_arm64": "d" * 128}


@pytest.fixture
def mod(monkeypatch):
    spec = importlib.util.spec_from_file_location("runtime_smoke_latest_mod", SCRIPT)
    m = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, "runtime_smoke_latest_mod", m)
    spec.loader.exec_module(m)
    return m


def sources(claude="2.1.300", codex="0.163.0", kiro="2.32.0", agy="1.3.5", agy_arm=None):
    agy_arm = agy_arm or agy

    def kiro_pkg(arch, ext="tar.gz", sha=None):
        return {"download": f"{kiro}/kirocli-{arch}-linux-musl.{ext}", "sha256": sha or SHA256[arch]}

    return {
        "https://registry.npmjs.org/-/package/@anthropic-ai/claude-code/dist-tags": {"latest": claude, "next": "9.9.9"},
        "https://registry.npmjs.org/-/package/@openai/codex/dist-tags": {"latest": codex, "alpha": "0.200.0-alpha.1"},
        "https://prod.download.cli.kiro.dev/stable/latest/manifest.json": {
            "version": kiro,
            "packages": [
                kiro_pkg("x86_64", "tar.xz", "e" * 64),
                kiro_pkg("x86_64"),
                kiro_pkg("aarch64"),
                {"download": f"{kiro}/kirocli-x86_64-linux.tar.gz", "sha256": "f" * 64},
            ],
        },
        "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_amd64.json": {
            "version": agy,
            "url": f"https://example.invalid/antigravity-cli/{agy}-1/linux-x64/cli_linux_x64.tar.gz",
            "sha512": SHA512["linux_amd64"],
        },
        "https://antigravity-cli-auto-updater-974169037036.us-central1.run.app/manifests/linux_arm64.json": {
            "version": agy_arm,
            "url": f"https://example.invalid/antigravity-cli/{agy_arm}-1/linux-arm/cli_linux_arm64.tar.gz",
            "sha512": SHA512["linux_arm64"],
        },
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
        "KIRO_CLI_VERSION": "2.32.0",
        "KIRO_CLI_SHA256_X86_64": SHA256["x86_64"],
        "KIRO_CLI_SHA256_AARCH64": SHA256["aarch64"],
        "AGY_VERSION": "1.3.5",
        "AGY_URL_X86_64": "https://example.invalid/antigravity-cli/1.3.5-1/linux-x64/cli_linux_x64.tar.gz",
        "AGY_URL_AARCH64": "https://example.invalid/antigravity-cli/1.3.5-1/linux-arm/cli_linux_arm64.tar.gz",
        "AGY_SHA512_X86_64": SHA512["linux_amd64"],
        "AGY_SHA512_AARCH64": SHA512["linux_arm64"],
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
        ("kiro", {**sources(), "https://prod.download.cli.kiro.dev/stable/latest/manifest.json": {"version": "2.32.0", "packages": []}}),
        ("agy", sources(agy_arm="1.3.6")),
        ("agy", {k: v for k, v in sources().items() if "linux_arm64" not in k}),
    ],
    ids=["bad-version", "kiro-no-musl", "agy-arch-mismatch", "unreachable"],
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


def test_report_creates_issue_with_runtime_versions_and_run_url(mod, monkeypatch, tmp_path):
    gh = FakeGh([{"number": 5, "title": "最新の版のスモークが落ちた: codex"}])
    monkeypatch.setattr(mod, "run_gh", gh)
    versions = tmp_path / "v.env"
    versions.write_text("AGY_VERSION=1.3.5\n", encoding="utf-8")
    assert run(mod, "report", "--runtime", "agy", "--run-url", RUN_URL, "--versions", str(versions), "--repo", "o/r") == 0
    assert gh.kinds() == [["issue", "list"], ["issue", "create"]]
    args, body = gh.calls[1]
    assert args[args.index("--title") + 1] == "最新の版のスモークが落ちた: agy"
    assert args[args.index("--repo") + 1] == "o/r"
    assert "agy" in body and "AGY_VERSION=1.3.5" in body and RUN_URL in body
    assert not any(word in body for word in ("Closes", "Fixes", "Resolves"))


def test_report_comments_on_open_issue_instead_of_creating(mod, monkeypatch, tmp_path):
    gh = FakeGh(
        [
            {"number": 7, "title": "最新の版のスモークが落ちた: agy"},
            {"number": 8, "title": "最新の版のスモークが落ちた: agy (old)"},
        ]
    )
    monkeypatch.setattr(mod, "run_gh", gh)
    assert run(mod, "report", "--runtime", "agy", "--run-url", RUN_URL) == 0
    assert gh.kinds() == [["issue", "list"], ["issue", "comment"]]
    args, body = gh.calls[1]
    assert args[2] == "7"
    assert "解決できなかった" in body and RUN_URL in body


def test_report_fails_when_gh_fails(mod, monkeypatch, capsys):
    monkeypatch.setattr(mod, "run_gh", FakeGh([], fail=True))
    assert run(mod, "report", "--runtime", "kiro", "--run-url", RUN_URL) == 1
    assert "gh" in capsys.readouterr().err
