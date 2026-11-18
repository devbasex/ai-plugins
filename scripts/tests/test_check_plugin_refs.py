"""check-plugin-refs.py（#3341 の決定 1・R0）のテスト。一時のリポジトリで 2 つのモードと apply を確かめる。"""

from __future__ import annotations

import importlib.util
import json
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

# 知っている副命令だけ使い方を出し、ほかは使い方の誤り（2）で終わる。打たれた語を calls.log へ残す
FAKE_NDF = """#!/usr/bin/env bash
echo "$*" >> "$(dirname "$0")/calls.log"
case "$*" in
  "release-steps publish --help") echo "usage: ndf release-steps publish [-h] [--root ROOT]" ;;
  "check --help"|"check instructions --help") echo "usage: check [-h] [--base BASE]" ;;
  *) exit 2 ;;
esac
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
    {"id": "R23", "file": ".ndf/glossary.json", "verdict": "drop", "from": "plugins/ndf/scripts/lib/README.md"},
    {"id": "R2", "file": ".github/workflows/old.yml", "verdict": "delete_file", "from": "plugins/ndf/**"},
]


def git(root: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True)


def write(root: Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    """今の側（10 系）と新しい側（ndf-next）が表のとおりにそろった一時のリポジトリ。"""
    for rel in ("scripts/release-steps.py", "scripts/instructions-check.py", "scripts/lib/README.md", "pyproject.toml"):
        write(tmp_path, f"plugins/ndf/{rel}", "")
    for rel in ("src/ndf/__init__.py", "pyproject.toml"):
        write(tmp_path, f"plugins/ndf-next/{rel}", "")
    write(tmp_path, "plugins/ndf-next/bin/ndf", FAKE_NDF)
    (tmp_path / "plugins/ndf-next/bin/ndf").chmod(0o755)
    write(tmp_path, ".github/workflows/ci.yml", WORKFLOW)
    write(tmp_path, ".github/workflows/old.yml", 'paths: ["plugins/ndf/**"]\n')
    write(tmp_path, ".ndf/pace.json", '{"paths": ["plugins/ndf/scripts/lib/**"]}\n')
    write(tmp_path, ".ndf/glossary.json", '["plugins/ndf/scripts/lib/README.md", "plugins/ndf/pyproject.toml"]\n')
    write(tmp_path, "scripts/plugin-refs-swap.json", json.dumps({"summary": "test", "rows": ROWS}))
    git(tmp_path, "init", "-q")
    git(tmp_path, "add", "-A")
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
    assert code == 0 and out["metrics"]["unbuilt"] == 1
    code, out = run(repo, "check", "--tree", "swapped", "--strict", capsys=capsys)
    assert code == 1 and rules(out) == [("unbuilt", "R10")]


def test_swapped_fails_on_unlisted_ref(repo, capsys):
    write(repo, ".github/workflows/ci.yml", WORKFLOW + "      - run: python3 plugins/ndf/scripts/issue-items.py\n")
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 1 and rules(out) == [("unlisted", None)]
    assert out["items"][0]["ref"] == "plugins/ndf/scripts/issue-items.py"


def test_swapped_fails_when_a_row_is_removed(repo, capsys):
    set_rows(repo, [r for r in ROWS if r["id"] != "R30"])
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 1 and rules(out) == [("unlisted", None)]


def test_swapped_rules_for_rows(repo, capsys):
    rows = [dict(r) for r in ROWS]
    rows[1]["target"] = {"path": "src/ndf/instructions.py"}
    rows[2]["target"] = {"path": "src/nowhere/**"}
    rows[3]["from"] = "plugins/ndf/scripts/lib/other.md"
    set_rows(repo, rows)
    write(repo, "plugins/ndf-next/src/ndf/instructions.py", "")
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 1
    assert rules(out) == [("unbuilt_exists", "R10"), ("target_missing", "R30"), ("stale_row", "R23")]


def test_placed_by_swap_is_counted_not_failed(repo, capsys):
    rows = [dict(r) for r in ROWS]
    rows[2]["target"] = {"path": "README.md", "placed_by": "swap"}
    set_rows(repo, rows)
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 0 and out["metrics"]["placed_by_swap"] == 1


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
    assert (repo / "plugins/ndf-next/bin/calls.log").read_text().count("release-steps publish") == 1


def test_probe_crash_stops(repo, capsys):
    write(repo, "plugins/ndf-next/bin/ndf", "#!/usr/bin/env bash\necho deps_missing >&2\nexit 5\n")
    code, out = run(repo, "check", "--tree", "swapped", capsys=capsys)
    assert code == 2 and "deps_missing" in out["summary"]


def test_apply_refuses_while_unbuilt_rows_remain(repo, capsys):
    before = (repo / ".github/workflows/ci.yml").read_text()
    code, out = run(repo, "apply", capsys=capsys)
    assert code == 1 and rules(out) == [("unbuilt", "R10")]
    assert (repo / ".github/workflows/ci.yml").read_text() == before


def test_apply_refuses_stale_rows_without_writing(repo, capsys):
    rows = [r for r in ROWS if r["verdict"] != "unbuilt"]
    rows.append({**ROWS[3], "id": "R99", "from": "plugins/ndf/gone.md"})
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


def test_occurrences_do_not_match_inside_a_longer_path():
    text = "a plugins/ndf/scripts/ b plugins/ndf/scripts/lib/x c plugins/ndf/scripts."
    assert chk.occurrences(text, "plugins/ndf/scripts/") == [2]
    assert chk.occurrences(text, "plugins/ndf/scripts") == [53]
