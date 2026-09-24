"""言語の検出（AC5）と、対応表だけで言語を足せること（非機能の運用・保守性）。"""
import json

import pytest

from serena_lsp_testlib import files_of, make_repo, run_json
from serena_lsp import detect, table


def _choose(counts):
    return detect.choose(counts, {"min_files": 10, "min_share": 0.05})


def test_min_files_boundary_nine_is_skipped_ten_is_taken():
    detected, skipped = _choose({"python": 100, "bash": 9})
    assert [d["language"] for d in detected] == ["python"]
    assert skipped == [{"language": "bash", "files": 9, "share": pytest.approx(9 / 109, abs=1e-3),
                        "reason": "below_threshold"}]
    detected, _ = _choose({"python": 100, "bash": 10})
    assert [d["language"] for d in detected] == ["python", "bash"]


def test_share_boundary_four_point_nine_is_skipped_five_is_taken():
    # 49 / 1000 = 4.9%、50 / 1000 = 5.0%
    detected, skipped = _choose({"python": 951, "bash": 49})
    assert [s["language"] for s in skipped] == ["bash"]
    detected, skipped = _choose({"python": 950, "bash": 50})
    assert [d["language"] for d in detected] == ["python", "bash"]
    assert skipped == []


def test_detected_are_ordered_by_file_count():
    detected, _ = _choose({"bash": 20, "python": 30})
    assert [d["language"] for d in detected] == ["python", "bash"]


def test_count_uses_only_table_extensions_as_denominator():
    ext = table.extension_map(table.load())
    paths = [f"a{i}.py" for i in range(10)] + [f"d{i}.md" for i in range(1000)] + ["x.PY", "b.d.ts"]
    counts = detect.count(paths, ext)
    assert counts == {"python": 11, "typescript": 1}


def test_table_extensions_do_not_overlap_and_are_lowercase():
    data = table.load()
    seen = {}
    for lang in data["languages"]:
        assert lang["extensions"], lang["serena"]
        for e in lang["extensions"]:
            assert e == e.lower() and e.startswith(".")
            assert e not in seen, (e, seen.get(e), lang["serena"])
            seen[e] = lang["serena"]
    names = {lang["serena"] for lang in data["languages"]}
    assert {"python", "typescript", "php", "bash", "go", "ruby", "rust", "java", "kotlin",
            "csharp", "swift", "lua", "cpp"} <= names


def test_cli_detect_reports_detected_and_skipped(tmp_path):
    repo = make_repo(tmp_path / "r", files_of(py=30, sh=12, js=2, md=50))
    code, out, _ = run_json("detect", "--root", str(repo), "--json")
    assert code == 0
    assert [d["language"] for d in out["detected"]] == ["python", "bash"]
    assert out["detected"][0]["files"] == 30
    assert out["skipped"] == [{"language": "typescript", "files": 2, "share": 0.045,
                               "reason": "below_threshold"}]


def test_cli_detect_outside_git_exits_2(tmp_path):
    code, _, _ = run_json("detect", "--root", str(tmp_path), "--json")
    assert code == 2


def test_new_language_in_table_is_detected_without_code_change(tmp_path):
    data = table.load()
    data["languages"].append({"serena": "zig", "extensions": [".zig"], "claude_plugin": None,
                              "binaries": [], "extra_checks": ["shellcheck"]})
    custom = tmp_path / "languages.json"
    custom.write_text(json.dumps(data))
    repo = make_repo(tmp_path / "r", files_of(zig=12))
    code, out, _ = run_json("detect", "--root", str(repo), "--json",
                            env={"SERENA_LSP_TABLE": str(custom)})
    assert code == 0
    assert [d["language"] for d in out["detected"]] == ["zig"]
