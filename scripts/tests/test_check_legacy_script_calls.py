"""10 系のスクリプトの呼び出しの数え方と一覧との突き合わせ（#3799）。"""

from __future__ import annotations

import importlib.util
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
CHECK = ROOT / "scripts" / "check-legacy-script-calls.py"


def _load():
    spec = importlib.util.spec_from_file_location("check_legacy_script_calls", CHECK)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules["check_legacy_script_calls"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def checker():
    return _load()


def _write(root: pathlib.Path, rel: str, text: str) -> None:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def _allow(root: pathlib.Path, files: dict[str, int]) -> None:
    _write(root, "scripts/legacy-script-calls.json", json.dumps({"version": 1, "files": files}))


def test_the_real_tree_matches_the_list(checker) -> None:
    data = json.loads((ROOT / checker.ALLOW).read_text(encoding="utf-8"))
    examples = frozenset(e["name"] for e in data["examples"])
    assert checker.compare(checker.count_calls(ROOT, examples), data["files"]) == []


def test_counts_every_form_and_skips_guarded_lines_and_tests(checker, tmp_path) -> None:
    _write(
        tmp_path,
        "plugins/ndf/skills/example/SKILL.md",
        "\n".join(
            [
                'python3 "$SCRIPTS/pr-steps.py" plan',
                'python3 "${NDF_SCRIPTS}/parallel.py" start p1',
                "python3 plugins/ndf/scripts/sprint-state.py init",
                '[ -n "${SCRIPTS:-}" ] && python3 "$SCRIPTS/x.py"',
                "ndf relay install",
                "`parallel.py check` で検査する",
                "`tool.py` はプラグインにある・`inference.py` は例",
                "python3 skills/skill-stats/scripts/skill-stats.py",
                "`tests/test_auth.py` は利用者のリポジトリの例",
            ]
        ),
    )
    _write(tmp_path, "plugins/ndf/agents/example.md", 'bash "$SCRIPTS/projects-sync.sh" 1 stage x\n')
    _write(tmp_path, "plugins/ndf/skills/example/tests/test_x.py", 'X = "$SCRIPTS/pr-steps.py"\n')
    _write(tmp_path, "plugins/ndf/src/tool.py", "")
    got = checker.count_calls(tmp_path, frozenset({"inference.py"}))
    assert got == {"plugins/ndf/agents/example.md": 1, "plugins/ndf/skills/example/SKILL.md": 5}


@pytest.mark.parametrize(
    ("files", "code", "word"),
    [
        ({"plugins/ndf/skills/example/SKILL.md": 1}, 0, None),
        ({}, 1, "増えた"),
        ({"plugins/ndf/skills/example/SKILL.md": 2}, 1, "減った"),
        ({"plugins/ndf/skills/example/SKILL.md": 1, "plugins/ndf/skills/gone/SKILL.md": 3}, 1, "一覧から行を消す"),
    ],
    ids=["same", "increased", "decreased", "gone"],
)
def test_main_fails_on_any_difference_from_the_list(checker, tmp_path, capsys, files, code, word) -> None:
    _write(tmp_path, "plugins/ndf/skills/example/SKILL.md", 'python3 "$SCRIPTS/pr-steps.py" plan\n')
    _allow(tmp_path, files)
    assert checker.main(["--root", str(tmp_path)]) == code
    if word:
        assert word in capsys.readouterr().out


def test_main_reports_an_unreadable_list(checker, tmp_path) -> None:
    assert checker.main(["--root", str(tmp_path)]) == 2
