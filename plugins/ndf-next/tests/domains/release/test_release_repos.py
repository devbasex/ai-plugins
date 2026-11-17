"""代表のリポジトリの設定で、`.claude-plugin` の無いリポジトリでも承認資料・リリースブランチを組む（本物の git と偽の GitHub。#3615）。

| 設定 | リリース方式 | 保存先 | 確かめる受け入れ条件 |
| --- | --- | --- | --- |
| Web サービス | `merge_base_branch` | `json`（`package.json`） | 3・4・8 |
| Flutter | `release_branch` | `pubspec` | 4・12（選んだスプリント） |
| nd-factory の設定のコピー | `release_branch`（`qa/dev`） | `tag` | 3・4・5 |
| 業務文書だけ | `trunk` の `production_pr` | `none` | 12（範囲が空にならない） |
| プラグインの trunk | `trunk` の `release_pr` | `plugin` | 12（単位ごとの前のタグ） |
"""

from __future__ import annotations

import json
from pathlib import Path

import git_sandbox
import pytest

from ndf.core.branch_strategy import Strategy
from ndf.domains.release import assemble
from ndf.domains.release import cli as release_cli

from ._fakes import FakeHub, configure, pull, section, write_json


def _seed(tmp_path: Path, files: dict[str, str]) -> Path:
    seed = tmp_path / "seed"
    seed.mkdir()
    git_sandbox.sh(seed, "init", "-q", "-b", "main")
    configure(seed)
    _commit(seed, files, "init")
    return seed


def _commit(repo: Path, files: dict[str, str], message: str) -> str:
    for rel, text in files.items():
        (repo / rel).parent.mkdir(parents=True, exist_ok=True)
        (repo / rel).write_text(text, encoding="utf-8")
    git_sandbox.sh(repo, "add", "-A")
    git_sandbox.sh(repo, "commit", "-q", "-m", message)
    return git_sandbox.sh(repo, "rev-parse", "HEAD").strip()


def _merge_pr(repo: Path, into: str, number: int, files: dict[str, str]) -> None:
    """`into` から切ったブランチに `files` を足し、PR #number のマージコミットで `into` へ入れる。"""
    git_sandbox.sh(repo, "switch", "-q", into)
    git_sandbox.sh(repo, "switch", "-q", "-c", f"feat/{number}")
    _commit(repo, files, f"change {number}")
    git_sandbox.sh(repo, "switch", "-q", into)
    git_sandbox.sh(repo, "merge", "-q", "--no-ff", f"feat/{number}", "-m", f"Merge pull request #{number} from o/feat/{number}")


def _publish(tmp_path: Path, seed: Path, checkout: str, remote: str = "origin") -> Path:
    origin = tmp_path / "origin.git"
    git_sandbox.sh(tmp_path, "clone", "-q", "--bare", str(seed), str(origin))
    work = tmp_path / "work"
    git_sandbox.sh(tmp_path, "clone", "-q", "-o", remote, "-b", checkout, str(origin), str(work))
    return configure(work)


def _hub(*numbers: int) -> FakeHub:
    hub = FakeHub(node_data={n: {"number": n, "title": f"PR {n}", "commits": {"nodes": []}} for n in numbers})
    hub.responses[("POST", "/repos/o/r/pulls")] = {"number": 90, "html_url": "https://example.invalid/pull/90"}
    hub.responses[("PATCH", "/repos/o/r/issues/90")] = {"html_url": "https://example.invalid/pull/90"}
    return hub


def _with_release(kind: str, **release: object) -> dict:
    got = section(kind)
    got["release"].update(release)
    return got


def _show(work: Path, ref: str, path: str) -> str:
    return git_sandbox.sh(work, "show", f"{ref}:{path}")


# Web サービス（develop → main、package.json、タグ v{version}）
WEB = _with_release("web-develop", tag="v{version}", github_release=True, cd={"workflow": "ndf-release.yml"})


def _web(tmp_path: Path, changed: dict[str, str], remote: str = "origin") -> Path:
    seed = _seed(tmp_path, {"package.json": json.dumps({"name": "app", "version": "1.2.2"}) + "\n", "src/a.ts": "a\n"})
    git_sandbox.sh(seed, "branch", "develop")
    _merge_pr(seed, "develop", 5, changed)
    return _publish(tmp_path, seed, "develop", remote)


def test_web_service_materials_and_assemble_use_package_json(tmp_path: Path) -> None:
    """受け入れ条件 3・4: `.claude-plugin` の無い Web サービスで、package.json の前と次を出し、リリースブランチで書き換える。"""
    work = _web(tmp_path, {"src/b.ts": "b\n"})
    target = assemble.Target("production", "patch", "develop", "main", "origin")
    facts = assemble.collect(work, _hub(5), target, Strategy(WEB))
    assert [p[:3] for p in facts.planned] == [("r", "1.2.2", "1.2.3")] and [p.number for p in facts.pulls] == [5]
    built = assemble.assemble(work, _hub(5), target, Strategy(WEB))
    assert built.branch == "release/v1.2.3"
    assert json.loads(_show(work, "origin/release/v1.2.3", "package.json"))["version"] == "1.2.3"


def test_web_service_tests_only_change_is_not_released(tmp_path: Path) -> None:
    """受け入れ条件 8: TypeScript・jest・PHP のテストだけの変化ではバージョンを上げない。"""
    changed = {"src/foo.test.ts": "t\n", "src/__tests__/bar.ts": "t\n", "tests/Unit/FooTest.php": "t\n"}
    work = _web(tmp_path, changed)
    with pytest.raises(assemble.NothingToRelease):
        assemble.collect(work, _hub(5), assemble.Target("production", "patch", "develop", "main", "origin"), Strategy(WEB))


def test_remote_name_comes_from_the_setting(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """受け入れ条件 11・I8: リモート名は `.ndf/worktree.json` の `remote` から求め、`origin` を書かない。"""
    work = _web(tmp_path, {"src/b.ts": "b\n"}, remote="upstream")
    write_json(work / ".ndf" / "project.json", {"version": 1, "branch_strategy": WEB})
    write_json(work / ".ndf" / "worktree.json", {"version": 1, "remote": "upstream"})
    monkeypatch.setattr(release_cli, "_hub", lambda args: _hub(5))
    out = tmp_path / "materials.md"
    args = ["materials", "--root", str(work), "--base", "develop", "--production", "main", "--channel", "production", "--out", str(out)]
    code = release_cli.main(args)
    got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert (code, got["status"]) == (0, "ok") and "- r: 1.2.2 → 1.2.3" in out.read_text(encoding="utf-8")


# Flutter（release_branch、pubspec.yaml）
FLUTTER = _with_release(
    "mobile", version_store={"kind": "pubspec", "path": "pubspec.yaml"}, tag="v{version}", github_release=False, cd={"workflow": "x.yml"}
)


def _units_origin(tmp_path: Path, files: dict[str, str], tag: str | None = None) -> Path:
    """本番（main）と、PR #6 をマージしたスプリントブランチ sprint/a を持つ origin の、main を取り出した clone。"""
    seed = _seed(tmp_path, files)
    if tag is not None:
        git_sandbox.sh(seed, "tag", tag)
    git_sandbox.sh(seed, "branch", "sprint/a")
    _merge_pr(seed, "sprint/a", 6, {"lib/b.dart": "b\n"})
    git_sandbox.sh(seed, "switch", "-q", "main")
    return _publish(tmp_path, seed, "main")


def test_flutter_collects_selected_sprints_and_writes_pubspec(tmp_path: Path) -> None:
    """受け入れ条件 4・12: 選んだスプリントを本番の HEAD へ集め、pubspec.yaml の前と次を出し、ビルド番号を上げて書く。"""
    work = _units_origin(tmp_path, {"pubspec.yaml": "name: app\nversion: 1.2.2+7\n", "lib/a.dart": "a\n"})
    target = assemble.Target("production", "patch", "develop", "main", "origin", units=("sprint/a",))
    facts = assemble.collect(work, _hub(6), target, Strategy(FLUTTER))
    assert [p[:3] for p in facts.planned] == [("r", "1.2.2", "1.2.3")] and [p.number for p in facts.pulls] == [6]
    assert len(git_sandbox.sh(work, "worktree", "list").splitlines()) == 1
    assert git_sandbox.sh(work, "branch", "--list").strip() == "* main"
    built = assemble.assemble(work, _hub(6), target, Strategy(FLUTTER))
    assert built.branch == "release/v1.2.3"
    assert _show(work, "origin/release/v1.2.3", "pubspec.yaml") == "name: app\nversion: 1.2.3+8\n"
    assert "Merge sprint/a into release/v1.2.3" in git_sandbox.sh(work, "log", "--format=%s", "origin/release/v1.2.3")


def test_release_branch_without_units_stops(tmp_path: Path) -> None:
    work = _units_origin(tmp_path, {"pubspec.yaml": "version: 1.0.0\n"})
    with pytest.raises(assemble.AssembleError, match="--units"):
        assemble.collect(work, _hub(), assemble.Target("production", "patch", "develop", "main", "origin"), Strategy(FLUTTER))


# nd-factory の設定のコピー（#3615 の決定 7: release_branch・qa/dev・タグだけ・CD あり）
ND_FACTORY = _with_release(
    "mobile", form="service", version_store={"kind": "tag"}, tag="v{version}", github_release=False, cd={"workflow": "ndf-release.yml"}
)
ND_FACTORY["branches"] = {"production": "main", "base": "qa/dev"}
ND_FACTORY["release"]["deliveries"] = [{"target": "本番", "kind": "manual", "trigger": "手で反映する", "channel": "production"}]


def test_nd_factory_copy_reads_the_previous_tag_and_builds_without_a_version_file(tmp_path: Path) -> None:
    """受け入れ条件 3・4・5: タグだけの保存先で、前のバージョンを v1.2.2 から読み、次のタグの名前は v1.2.3 になる。"""
    work = _units_origin(tmp_path, {"README.md": "x\n", "lib/a.dart": "a\n"}, tag="v1.2.2")
    target = assemble.Target("production", "patch", "qa/dev", "main", "origin", units=("sprint/a",))
    facts = assemble.collect(work, _hub(6), target, Strategy(ND_FACTORY))
    tagged = git_sandbox.sh(work, "rev-parse", "v1.2.2^{commit}").strip()
    assert list(facts.planned) == [("r", "1.2.2", "1.2.3", tagged)]
    built = assemble.assemble(work, _hub(6), target, Strategy(ND_FACTORY))
    assert built.branch == "release/v1.2.3" and built.version == "1.2.3"


# 業務文書だけ（trunk の production_pr、版数なし）
def test_docs_range_is_the_production_pull_and_has_no_version(tmp_path: Path) -> None:
    """受け入れ条件 12: 本番の PR を範囲にして空にならない。バージョンの行は出さず、組み立ては作らない。"""
    seed = _seed(tmp_path, {"docs/a.md": "a\n"})
    git_sandbox.sh(seed, "switch", "-q", "-c", "feat/9")
    head = _commit(seed, {"docs/b.md": "b\n"}, "書いた")
    git_sandbox.sh(seed, "switch", "-q", "main")
    work = _publish(tmp_path, seed, "main")
    git_sandbox.sh(work, "push", "-q", "origin", f"{head}:refs/pull/9/head")
    hub = _hub()
    hub.pulls[9] = pull(9, head=head, base="main", ref="feat/9")
    target = assemble.Target("production", "patch", "main", "main", "origin", pr=9)
    facts = assemble.collect(work, hub, target, Strategy(section("docs")))
    assert facts.planned == () and not facts.versioned and facts.head == head
    assert [c.distributed for c in facts.changes] == [("docs/b.md",)]
    with pytest.raises(assemble.NoReleasePr):
        assemble.assemble(work, hub, target, Strategy(section("docs")))
    with pytest.raises(assemble.AssembleError, match="--pr"):
        assemble.collect(work, hub, assemble.Target("production", "patch", "main", "main", "origin"), Strategy(section("docs")))


# プラグインの trunk（ai-plugins の形。base と production が同じ）
def test_trunk_collects_each_unit_from_its_previous_tag(tmp_path: Path) -> None:
    """受け入れ条件 12: 範囲は origin/main..origin/main の空でなく、単位ごとの前のタグから集める。"""
    manifest = {"plugins": [{"name": "a", "source": "./pa"}, {"name": "b", "source": "./pb"}]}
    seed = _seed(
        tmp_path,
        {
            ".claude-plugin/marketplace.json": json.dumps(manifest),
            "pa/.claude-plugin/plugin.json": json.dumps({"name": "a", "version": "1.0.0"}),
            "pb/.claude-plugin/plugin.json": json.dumps({"name": "b", "version": "2.0.0"}),
        },
    )
    git_sandbox.sh(seed, "tag", "a--v1.0.0")
    _commit(seed, {"pa/x.py": "x\n", "pb/y.py": "y\n"}, "両方を直した")
    git_sandbox.sh(seed, "tag", "b--v2.0.0")
    _commit(seed, {"pa/z.py": "z\n"}, "a を直した (#11)")
    work = _publish(tmp_path, seed, "main")
    target = assemble.Target("production", "patch", "main", "main", "origin")
    facts = assemble.collect(work, _hub(11), target, Strategy(section("plugin")))
    since_a = git_sandbox.sh(work, "rev-parse", "a--v1.0.0^{commit}").strip()
    assert list(facts.planned) == [("a", "1.0.0", "1.0.1", since_a)]
    assert [p.number for p in facts.pulls] == [11]
    built = assemble.assemble(work, _hub(11), target, Strategy(section("plugin")))
    assert built.branch == "release/v1.0.1"


def test_unsupported_form_stops_every_step(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    """受け入れ条件 13: 扱わない形では materials・assemble・publish が precondition で止まり、形の名前と表の在りかを出す。"""
    work = _web(tmp_path, {"src/b.ts": "b\n"})
    write_json(work / ".ndf" / "project.json", {"version": 1, "branch_strategy": _with_release("web-develop", form="desktop")})
    monkeypatch.setattr(release_cli, "_hub", lambda args: _hub())
    target = ["--base", "develop", "--production", "main", "--channel", "production"]
    for argv in (["materials", *target], ["assemble", *target], ["publish", "--revision", "HEAD"]):
        code = release_cli.main([argv[0], "--root", str(work), *argv[1:]])
        got = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
        assert code != 0 and got["cause"]["kind"] == "precondition", argv
        assert "desktop" in got["cause"]["detail"] and "release-forms.json" in got["cause"]["detail"]
