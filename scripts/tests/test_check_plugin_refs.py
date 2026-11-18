"""check-plugin-refs.py（#3341 の決定 1・R0）のテスト。一時のリポジトリで 2 つのモードと apply を確かめる。"""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
_SPEC = importlib.util.spec_from_file_location("check_plugin_refs", REPO_ROOT / "scripts/check-plugin-refs.py")
chk = importlib.util.module_from_spec(_SPEC)
# dataclass は定義の時にモジュールを sys.modules から引く
sys.modules[_SPEC.name] = chk
_SPEC.loader.exec_module(chk)

# プラグインの環境で確かめを打つ uv の代わり。語の並びの一覧（最後の引数）を受け、知っている副命令だけ使い方を返し、
# ほかは使い方の誤り（2）と答える。起動ごとに 1 行、打たれた語の並びを calls.log へ残す
FAKE_UV = """#!/usr/bin/env python3
import json, sys
from pathlib import Path
todo = json.loads(sys.argv[-1])
with Path(__file__).with_name("calls.log").open("a") as f:
    f.write(" | ".join(" ".join(w) for w in todo) + "\\n")
usage = {
    "release-steps publish": "usage: ndf release-steps publish [-h] [--root ROOT]",
    "check": "usage: check [-h] [--base BASE]",
    "check instructions": "usage: check [-h] [--base BASE]",
}
print(json.dumps([{"code": 0, "help": usage[" ".join(w)], "error": ""} if " ".join(w) in usage else {"code": 2, "help": "", "error": ""} for w in todo]))
"""

WORKFLOW = """jobs:
  publish:
    steps:
      - run: python3 plugins/ndf/scripts/release-steps.py publish --root .
      - run: python3 plugins/ndf/scripts/instructions-check.py --root .
      - run: uv run --project plugins/ndf/pyproject.toml pytest
"""

ROWS = [
    {
        "id": "R9",
        "file": ".github/workflows/ci.yml",
        "verdict": "redirect",
        "from": "python3 plugins/ndf/scripts/release-steps.py publish",
        "to": "plugins/ndf/bin/ndf release-steps publish",
        "target": {"command": ["release-steps", "publish"]},
    },
    {
        "id": "R10",
        "file": ".github/workflows/ci.yml",
        "verdict": "unbuilt",
        "from": "python3 plugins/ndf/scripts/instructions-check.py",
        "target": {"command": ["check", "instructions"]},
        "feature": "entrypoint:scripts/instructions-check.py",
    },
    {
        "id": "R30",
        "file": ".ndf/pace.json",
        "verdict": "redirect",
        "from": '"plugins/ndf/scripts/lib/**"',
        "to": '"plugins/ndf/src/ndf/**"',
        "target": {"path": "src/ndf/**"},
    },
    {"id": "R23", "file": ".ndf/glossary.json", "verdict": "drop", "from": "plugins/ndf/scripts/lib/guide.md"},
    {"id": "R2", "file": ".github/workflows/old.yml", "verdict": "delete_file", "from": "plugins/ndf/**"},
]


def row(row_id: str) -> dict:
    """`ROWS` の行を id で引く（並びを変えても、テストが名指す判定の行が替わらない）。"""
    return next(r for r in ROWS if r["id"] == row_id)


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path, monkeypatch) -> Path:
    """今の側（10 系）と新しい側（ndf-next）が表のとおりにそろい、コミット済みの一時のリポジトリ。PATH の先頭に偽の uv を置く。"""
    for rel in ("scripts/release-steps.py", "scripts/instructions-check.py", "scripts/lib/guide.md", "pyproject.toml"):
        write(tmp_path, f"plugins/ndf/{rel}", "")
    for rel in ("src/ndf/__init__.py", "pyproject.toml"):
        write(tmp_path, f"plugins/ndf-next/{rel}", "")
    write(tmp_path, "plugins/ndf-next/bin/ndf", "#!/bin/sh\n")
    (tmp_path / "plugins/ndf-next/bin/ndf").chmod(0o755)
    write(tmp_path, "fakebin/uv", FAKE_UV)
    (tmp_path / "fakebin/uv").chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path / 'fakebin'}:{os.environ['PATH']}")
    write(tmp_path, ".github/workflows/ci.yml", WORKFLOW)
    write(tmp_path, ".github/workflows/old.yml", 'paths: ["plugins/ndf/**"]\n')
    write(tmp_path, ".ndf/pace.json", '{"paths": ["plugins/ndf/scripts/lib/**"]}\n')
    write(tmp_path, ".ndf/glossary.json", '["plugins/ndf/scripts/lib/guide.md", "plugins/ndf/pyproject.toml"]\n')
    write(tmp_path, "scripts/plugin-refs-swap.json", json.dumps({"summary": "test", "rows": ROWS}))
    write(tmp_path, ".gitignore", "fakebin/\n")
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
    git(tmp_path, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-qm", "init")
    return tmp_path


def run(root: Path, *args: str, capsys) -> tuple[int, dict]:
    code = chk.main(["--root", str(root), *args])
    return code, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def set_rows(root: Path, rows: list[dict]) -> None:
    write(root, "scripts/plugin-refs-swap.json", json.dumps({"summary": "test", "rows": rows}))


def rules(out: dict) -> list[tuple[str, str | None]]:
    return [(i["rule"], i["row"]) for i in out["items"]]


def test_collect_refs_dedupes_and_skips_self_and_unreadable(repo):
    write(repo, "AGENTS.md", "`plugins/ndf/pyproject.toml`. and plugins/ndf/pyproject.toml, again\n")
    write(repo, "scripts/check-plugin-refs.py", 'X = "plugins/ndf/nowhere/"\n')
    write(repo, "tests/gone.sh", "plugins/ndf/x\n")
    git(repo, "add", "-A")
    (repo / "tests/gone.sh").unlink()
    refs, warnings = chk.collect_refs(repo)
    assert [r for r in refs if r.file == "AGENTS.md"] == [chk.Ref("AGENTS.md", "plugins/ndf/pyproject.toml")]
    assert not any(r.file == "scripts/check-plugin-refs.py" for r in refs)
    assert warnings == ["tests/gone.sh"]


def test_outside_git_stops(tmp_path, capsys):
    code, out = run(tmp_path, "check", "--tree", "current", capsys=capsys)
    assert code == 2 and out["status"] == "stopped"


@pytest.mark.parametrize(
    "row",
    [
        {"id": "X", "file": "a", "verdict": "move", "from": "plugins/ndf/a"},
        {"id": "X", "file": "a", "verdict": "redirect", "from": "plugins/ndf/a", "target": {"path": "a"}},
        {"id": "X", "file": "a", "verdict": "unbuilt", "from": "plugins/ndf/a", "target": {"path": "a"}},
        {"id": "X", "file": "a", "verdict": "drop", "from": "no path"},
    ],
)
def test_malformed_row_stops(repo, capsys, row):
    set_rows(repo, [row])
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 2 and out["status"] == "stopped"


def test_current_tree(repo, capsys):
    assert run(repo, "check", "--tree", "current", capsys=capsys)[0] == 0
    (repo / "plugins/ndf/scripts/instructions-check.py").unlink()
    code, out = run(repo, "check", "--tree", "current", capsys=capsys)
    assert code == 1 and rules(out) == [("missing", None)]


def test_swapped_counts_unbuilt_as_known_and_strict_fails(repo, capsys):
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    # 行き先の副命令（R9・R10）は 1 回の起動でまとめて確かめる
    assert code == 0 and out["metrics"]["unbuilt"] == 1 and out["metrics"]["probes"] == 1
    code, out = run(repo, "check", "--tree", "swapped", "--strict", capsys=capsys)
    assert code == 1 and rules(out) == [("unbuilt", "R10")]


def _deferred(rows: list[dict]) -> list[dict]:
    """未作成の行を「11.x へ送る」の行へ替える（#3304 の S2-0 の判断 (B)）。"""
    return [{**r, "verdict": "deferred", "issue": "#3791"} if r["verdict"] == "unbuilt" else r for r in rows]


def test_swapped_strict_counts_deferred_rows_without_failing(repo, capsys):
    set_rows(repo, _deferred(ROWS))
    code, out = run(repo, "check", "--tree", "swapped", "--strict", capsys=capsys)
    assert code == 0 and out["metrics"]["deferred"] == 1 and out["metrics"]["unbuilt"] == 0


def test_swapped_fails_when_deferred_target_exists(repo, capsys):
    # R9 の行き先（release-steps publish）は入れ替え先にある。送った行の行き先ができたら印を外させる
    rows = [{**r, "verdict": "deferred", "issue": "#3791", "feature": "f"} if r["id"] == "R9" else r for r in _deferred(ROWS)]
    set_rows(repo, rows)
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 1 and ("deferred_exists", "R9") in rules(out)


def test_apply_lists_deferred_rows_as_manual_with_issue(repo, capsys):
    set_rows(repo, _deferred(ROWS))
    code, out = run(repo, "apply", capsys=capsys)
    assert code == 0
    assert ("R10", "#3791") in [(i["row"], i.get("issue")) for i in out["items"] if i["rule"] == "manual"]
    assert "python3 plugins/ndf/scripts/instructions-check.py" in (repo / ".github/workflows/ci.yml").read_text()


def test_swapped_fails_on_unlisted_ref(repo, capsys):
    write(repo, ".github/workflows/ci.yml", WORKFLOW + "      - run: python3 plugins/ndf/scripts/issue-items.py\n")
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 1 and rules(out) == [("unlisted", None)]
    assert out["items"][0]["ref"] == "plugins/ndf/scripts/issue-items.py"


def test_swapped_fails_on_a_listed_path_called_with_other_words(repo, capsys):
    # R9 の from と同じ旧いパスを別の引数で呼ぶ行は、R9 の置き換えで直らないため表に無い参照として数える
    write(repo, ".github/workflows/ci.yml", WORKFLOW + "      - run: python3 plugins/ndf/scripts/release-steps.py approval-check\n")
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 1 and rules(out) == [("unlisted", None)]
    assert out["items"][0]["ref"] == "plugins/ndf/scripts/release-steps.py"


def test_swapped_fails_when_a_row_is_removed(repo, capsys):
    set_rows(repo, [r for r in ROWS if r["id"] != "R30"])
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 1 and rules(out) == [("unlisted", None)]


def test_swapped_rules_for_rows(repo, capsys):
    rows = [dict(r) for r in ROWS]
    rows[1]["target"] = {"path": "src/ndf/instructions.py"}
    rows[2]["to"] = '"plugins/ndf/src/nowhere/**"'
    rows[2]["target"] = {"path": "src/nowhere/**"}
    rows[3]["from"] = "plugins/ndf/scripts/lib/other.md"
    set_rows(repo, rows)
    write(repo, "plugins/ndf-next/src/ndf/instructions.py", "")
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 1
    # R23 の from を替えたため、用語集の guide.md への参照は表から外れて unlisted になる
    assert rules(out) == [("unlisted", None), ("unbuilt_exists", "R10"), ("target_missing", "R30"), ("stale_row", "R23")]
    assert out["items"][0]["ref"] == "plugins/ndf/scripts/lib/guide.md"


def test_placed_by_swap_is_counted_not_failed(repo, capsys):
    rows = [dict(r) for r in ROWS]
    rows[2]["to"] = '"plugins/ndf/swap-placed.md"'
    rows[2]["target"] = {"path": "swap-placed.md", "placed_by": "swap"}
    set_rows(repo, rows)
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 0 and out["metrics"]["placed_by_swap"] == 1


@pytest.mark.parametrize(
    ("index", "to"),
    [
        (0, "plugins/ndf/bin/ndf release-steps approval-check"),
        (0, "plugins/ndf/bin/ndf release-steps publishx"),
        (2, '"plugins/ndf/src/ndf/lib/**"'),
    ],
)
def test_swapped_and_apply_fail_when_to_does_not_name_the_target(repo, capsys, index, to):
    # target だけが正しく to を打ち間違えた行は、検査を通さず apply も書かない
    rows = [dict(r) for r in ROWS]
    rows[index]["to"] = to
    set_rows(repo, rows)
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 1 and rules(out) == [("to_mismatch", rows[index]["id"])]
    set_rows(repo, [r for r in rows if r["verdict"] != "unbuilt"])
    before = (repo / ".github/workflows/ci.yml").read_text()
    code, out = run(repo, "apply", capsys=capsys)
    assert code == 1 and rules(out) == [("to_mismatch", rows[index]["id"])]
    assert (repo / ".github/workflows/ci.yml").read_text() == before and (repo / ".github/workflows/old.yml").exists()


@pytest.mark.parametrize("broken", ["missing", "not_executable"])
def test_swapped_fails_on_a_command_without_the_entrypoint(repo, capsys, broken):
    entry = repo / "plugins/ndf-next/bin/ndf"
    if broken == "missing":
        entry.unlink()
    else:
        entry.chmod(0o644)
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 1 and rules(out) == [("target_missing", "R9")]


def test_target_exists_globs(tmp_path):
    (tmp_path / "skills/a").mkdir(parents=True)
    (tmp_path / "skills/a/x.md").write_text("")
    assert chk.target_exists(tmp_path, "skills/a/x.md")
    assert chk.target_exists(tmp_path, "**")
    assert chk.target_exists(tmp_path, "skills/*/scripts/drive.py")
    assert chk.target_exists(tmp_path, "skills/{a,b}/y")
    assert not chk.target_exists(tmp_path, "skills/b.md")
    assert not chk.target_exists(tmp_path, "manifests/{a,b}.txt")


def test_probe_runs_each_command_once_and_reads_usage(repo):
    probe = chk.CommandProbe(repo / "plugins/ndf-next")
    assert probe(("release-steps", "publish")) and probe(("release-steps", "publish"))
    # `check` は語を持たないため、`check instructions --help` も 0 で `check` の使い方を出す
    assert not probe(("check", "instructions"))
    assert not probe(("nosuch",))
    assert probe.calls == 3
    assert (repo / "fakebin/calls.log").read_text().count("release-steps publish") == 1
    probe.prefetch([("release-steps", "publish"), ("a",), ("b",)])
    assert probe.calls == 4 and (repo / "fakebin/calls.log").read_text().splitlines()[-1] == "a | b"


def test_probe_crash_stops(repo, capsys):
    write(repo, "fakebin/uv", "#!/usr/bin/env bash\necho deps_missing >&2\nexit 5\n")
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 2 and "deps_missing" in out["summary"]


def test_apply_refuses_while_unbuilt_rows_remain(repo, capsys):
    before = (repo / ".github/workflows/ci.yml").read_text()
    code, out = run(repo, "apply", capsys=capsys)
    assert code == 1 and rules(out) == [("unbuilt", "R10")]
    assert (repo / ".github/workflows/ci.yml").read_text() == before


def test_apply_refuses_stale_rows_without_writing(repo, capsys):
    rows = [r for r in ROWS if r["verdict"] != "unbuilt"]
    rows.append({**row("R23"), "id": "R99", "from": "plugins/ndf/gone.md"})
    set_rows(repo, rows)
    code, out = run(repo, "apply", capsys=capsys)
    assert code == 1 and rules(out) == [("stale_row", "R99")]
    assert (repo / ".github/workflows/old.yml").exists()


def test_apply_rewrites_deletes_and_lists_manual_rows(repo, capsys):
    set_rows(repo, [r for r in ROWS if r["verdict"] != "unbuilt"])
    code, out = run(repo, "apply", capsys=capsys)
    assert code == 0
    assert "plugins/ndf/bin/ndf release-steps publish --root ." in (repo / ".github/workflows/ci.yml").read_text()
    assert (repo / ".ndf/pace.json").read_text() == '{"paths": ["plugins/ndf/src/ndf/**"]}\n'
    assert not (repo / ".github/workflows/old.yml").exists()
    assert [i["row"] for i in out["items"] if i["rule"] == "manual"] == ["R23"]


def test_apply_keeps_the_file_mode_and_leaves_no_temporary_file(repo, capsys):
    set_rows(repo, [r for r in ROWS if r["verdict"] != "unbuilt"])
    (repo / ".ndf/pace.json").chmod(0o600)
    assert run(repo, "apply", capsys=capsys)[0] == 0
    assert (repo / ".ndf/pace.json").stat().st_mode & 0o777 == 0o600
    assert sorted(p.name for p in (repo / ".ndf").iterdir()) == ["glossary.json", "pace.json"]


def test_apply_refuses_to_delete_a_file_with_uncommitted_changes(repo, capsys):
    set_rows(repo, [r for r in ROWS if r["verdict"] != "unbuilt"])
    write(repo, ".github/workflows/old.yml", 'paths: ["plugins/ndf/**", "x"]\n')
    before = (repo / ".ndf/pace.json").read_text()
    code, out = run(repo, "apply", capsys=capsys)
    assert code == 1 and rules(out) == [("uncommitted", "R2")]
    assert (repo / ".github/workflows/old.yml").exists() and (repo / ".ndf/pace.json").read_text() == before


def test_apply_refuses_to_rewrite_a_file_with_uncommitted_changes(repo, capsys):
    # 書き換える先に利用者の変更があれば、途中で失敗して git checkout で戻すときに消えるため書き込む前に止まる
    set_rows(repo, [r for r in ROWS if r["verdict"] != "unbuilt"])
    write(repo, ".ndf/pace.json", '{"paths": ["plugins/ndf/scripts/lib/**"], "x": 1}\n')
    before = (repo / ".github/workflows/ci.yml").read_text()
    code, out = run(repo, "apply", capsys=capsys)
    assert code == 1 and rules(out) == [("uncommitted", "R30")]
    assert (repo / ".github/workflows/ci.yml").read_text() == before and (repo / ".github/workflows/old.yml").exists()


def test_apply_refuses_to_delete_a_file_whose_reference_is_gone(repo, capsys):
    # 表を作った時点の参照が消えたファイルは古い行として止め、変更が無く追跡済みでも消さない
    set_rows(repo, [r for r in ROWS if r["verdict"] != "unbuilt"])
    write(repo, ".github/workflows/old.yml", 'paths: ["other/**"]\n')
    git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-qam", "drop ref")
    code, out = run(repo, "apply", capsys=capsys)
    assert code == 1 and rules(out) == [("stale_row", "R2")]
    assert (repo / ".github/workflows/old.yml").exists()


@pytest.mark.parametrize("flag", ["--assume-unchanged", "--skip-worktree"])
def test_apply_refuses_a_file_whose_changes_the_index_hides(repo, capsys, flag):
    # index の印で git status が変更を報告しないファイルも、変更が無いと確かめられないため消さない
    set_rows(repo, [r for r in ROWS if r["verdict"] != "unbuilt"])
    git(repo, "update-index", flag, ".github/workflows/old.yml")
    write(repo, ".github/workflows/old.yml", 'paths: ["plugins/ndf/**", "x"]\n')
    code, out = run(repo, "apply", capsys=capsys)
    assert code == 1 and rules(out) == [("uncommitted", "R2")]
    assert (repo / ".github/workflows/old.yml").read_text() == 'paths: ["plugins/ndf/**", "x"]\n'


def test_root_and_table_are_accepted_after_the_subcommand(repo, capsys):
    # 使用例のとおり、副命令の後に --root と --table を書ける
    table = repo / "other-table.json"
    table.write_text(json.dumps({"summary": "test", "rows": [r for r in ROWS if r["verdict"] != "unbuilt"]}))
    code = chk.main(["apply", "--root", str(repo), "--table", str(table)])
    out = json.loads(capsys.readouterr().out.strip().splitlines()[-1])
    assert code == 0, out
    assert not (repo / ".github/workflows/old.yml").exists()


@pytest.mark.parametrize("file", ["../outside.yml", "link.yml", "./.github/workflows/old.yml", ".github//workflows/old.yml"])
def test_apply_stops_on_a_file_outside_the_worktree(repo, capsys, file):
    (repo / "link.yml").symlink_to(repo / ".github/workflows/old.yml")
    rows = [r for r in ROWS if r["verdict"] != "unbuilt"] + [{**row("R2"), "id": "R98", "file": file, "from": "plugins/ndf/**"}]
    set_rows(repo, rows)
    before = (repo / ".ndf/pace.json").read_text()
    code, out = run(repo, "apply", capsys=capsys)
    assert code == 2 and file in out["summary"]
    assert (repo / ".ndf/pace.json").read_text() == before and (repo / ".github/workflows/old.yml").exists()


@pytest.mark.parametrize("ignored", [False, True])
def test_apply_refuses_a_file_not_tracked_in_head(repo, capsys, ignored):
    # HEAD に無いファイル（無視するファイルは git status に出ない）は Git で戻せないため、書き換えも削除もしない
    write(repo, "local.yml", 'paths: ["plugins/ndf/**"]\n')
    if ignored:
        write(repo, ".gitignore", "fakebin/\nlocal.yml\n")
        git(repo, "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false", "commit", "-qam", "ignore")
    rows = [r for r in ROWS if r["verdict"] != "unbuilt"] + [{**row("R2"), "id": "R97", "file": "local.yml", "from": "plugins/ndf/**"}]
    set_rows(repo, rows)
    code, out = run(repo, "apply", capsys=capsys)
    assert code == 1 and rules(out) == [("untracked", "R97")]
    assert (repo / "local.yml").exists() and (repo / ".github/workflows/old.yml").exists()


@pytest.mark.parametrize(
    "extra",
    [
        {"verdict": "unbuilt", "target": {"path": "src/nowhere.py", "command": 1}, "feature": "f"},
        {"verdict": "delete_file", "target": 1},
    ],
)
def test_swapped_reads_only_the_checked_target(repo, capsys, extra):
    # 表が受け付けた行き先（path と配列でない command の併記・削除の行の数値）でも、結果の JSON を返す
    rows = [*ROWS, {"id": "R96", "file": ".github/workflows/old.yml", "from": "plugins/ndf/**", **extra}]
    set_rows(repo, rows)
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code in (0, 1) and out["tool"] == "check-plugin-refs"


def test_occurrences_do_not_match_inside_a_longer_path():
    text = "a plugins/ndf/scripts/ b plugins/ndf/scripts/lib/x c plugins/ndf/scripts."
    assert chk.occurrences(text, "plugins/ndf/scripts/") == [2]
    assert chk.occurrences(text, "plugins/ndf/scripts") == [53]


# 現状固定（I-004: _row_problem の入れ子の整理の前）。load_table が行の形の誤りごとに出す文を固定する。
_OK = {"id": "X", "file": "a", "from": "plugins/ndf/a"}


@pytest.mark.parametrize(
    ("row", "problem"),
    [
        ("not a dict", "行が object でない"),
        ({"file": "a", "verdict": "drop", "from": "plugins/ndf/a"}, "id が無い"),
        ({"id": "", "file": "a", "verdict": "drop", "from": "plugins/ndf/a"}, "id が無い"),
        ({"id": "X", "file": 1, "verdict": "drop", "from": "plugins/ndf/a"}, "file が無い"),
        ({"id": "X", "file": "a", "from": "plugins/ndf/a"}, "verdict が無い"),
        ({"id": "X", "file": "a", "verdict": "drop"}, "from が無い"),
        ({**_OK, "verdict": "move"}, "判定 'move' は redirect・unbuilt・deferred・drop・delete_file のどれでもない"),
        ({**_OK, "verdict": "drop", "from": "no path"}, "from が plugins/ndf/ のパスを含まない"),
        ({**_OK, "verdict": "redirect", "target": {"path": "a"}}, "redirect に to が無い"),
        ({**_OK, "verdict": "redirect", "to": "b"}, "redirect に target（path か command）が無い"),
        ({**_OK, "verdict": "redirect", "to": "b", "target": "a"}, "redirect に target（path か command）が無い"),
        ({**_OK, "verdict": "redirect", "to": "b", "target": {"path": 1}}, "redirect に target（path か command）が無い"),
        ({**_OK, "verdict": "redirect", "to": "b", "target": {"command": []}}, "redirect に target（path か command）が無い"),
        ({**_OK, "verdict": "redirect", "to": "b", "target": {"command": ["a", 1]}}, "redirect に target（path か command）が無い"),
        ({**_OK, "verdict": "redirect", "to": "b", "target": {"command": "a"}}, "redirect に target（path か command）が無い"),
        ({**_OK, "verdict": "unbuilt", "feature": "f"}, "unbuilt に target（path か command）が無い"),
        ({**_OK, "verdict": "unbuilt", "target": {"command": ["a"]}}, "unbuilt に feature が無い"),
        ({**_OK, "verdict": "deferred", "target": {"command": ["a"]}, "feature": "f"}, "deferred に移しの課題（issue: #<番号>）が無い"),
        (
            {**_OK, "verdict": "deferred", "target": {"command": ["a"]}, "feature": "f", "issue": "3791"},
            "deferred に移しの課題（issue: #<番号>）が無い",
        ),
    ],
)
def test_load_table_reports_row_problem(tmp_path, row, problem):
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"rows": [_OK | {"verdict": "drop"}, row]}), encoding="utf-8")
    with pytest.raises(chk.Stop) as e:
        chk.load_table(path)
    rid = row.get("id") if isinstance(row, dict) else "?"
    assert str(e.value) == f"{path}: rows[1]（{rid}）: {problem}"


def test_load_table_accepts_each_verdict(tmp_path):
    rows = [
        {**_OK, "verdict": "redirect", "to": "b", "target": {"path": "a"}},
        {**_OK, "verdict": "redirect", "to": "b", "target": {"command": ["a", "b"]}, "feature": "f"},
        {**_OK, "verdict": "unbuilt", "target": {"path": "a", "command": 1}, "feature": "f"},
        {**_OK, "verdict": "drop", "to": 1, "target": 1},
        {**_OK, "verdict": "delete_file"},
    ]
    path = tmp_path / "t.json"
    path.write_text(json.dumps({"rows": rows}), encoding="utf-8")
    assert chk.load_table(path) == [
        chk.Row("X", "a", "redirect", "plugins/ndf/a", "b", {"path": "a"}, None),
        chk.Row("X", "a", "redirect", "plugins/ndf/a", "b", {"command": ["a", "b"]}, "f"),
        chk.Row("X", "a", "unbuilt", "plugins/ndf/a", None, {"path": "a"}, "f"),
        chk.Row("X", "a", "drop", "plugins/ndf/a", 1, None, None),
        chk.Row("X", "a", "delete_file", "plugins/ndf/a", None, None, None),
    ]
