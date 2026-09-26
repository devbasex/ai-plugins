"""#1319: 指標の言語・ツール・宣言・出力の読み取り・時間の算術（純粋な処理）。

起動と書き出しは `test_code_metrics_measure.py` が見る。実物のツールの出力の形は、
2026-09-26（lizard）と 2026-09-27（Ruff・complexipy・symilar・jscpd）に保存した形をもとにする。
"""

from __future__ import annotations

import datetime as _dt
import json
import math
import sys

import pytest

ROOTS = ["/w/work"]


@pytest.fixture
def timeline(refactor):
    return sys.modules["refactor_lib.timeline"]


# ---------------- 既定と宣言 ----------------


def test_defaults_carry_no_repository_paths_or_settings(codemetrics):
    """AC8: 既定の表にパスとこのリポジトリ固有の設定が無い。"""
    blob = json.dumps([codemetrics.LANGUAGE_EXTENSIONS, codemetrics.DEFAULT_TOOLS, codemetrics.COMMANDS, codemetrics.TOOL_COMMANDS])
    assert "/" not in blob.replace("==", "")
    assert "plugins" not in blob and "ndf" not in blob


def test_no_declaration_uses_the_defaults(codemetrics):
    """AC5: 宣言が無ければ既定の対応。"""
    config = codemetrics.load_config(None)
    assert config["source"] == codemetrics.SOURCE_DEFAULT
    assert codemetrics.language_tool(config, "python") == (codemetrics.TOOL_RUFF_COMPLEXIPY, None)
    assert codemetrics.language_tool(config, "typescript") == (codemetrics.TOOL_LIZARD, None)
    assert codemetrics.language_tool(config, "go") == (codemetrics.TOOL_LIZARD, None)
    assert codemetrics.language_tool(config, "shell") == (None, codemetrics.UNSUPPORTED_LANGUAGE)


def test_declaration_replaces_only_the_declared_language(codemetrics):
    """AC6: 宣言した言語だけを置き換え、ほかは既定のまま。`null` はその言語を止める。"""
    config = codemetrics.load_config(json.dumps({"version": 1, "tools": {"python": "lizard", "go": None}}))
    assert config["source"] == codemetrics.SOURCE_DECLARED
    assert codemetrics.language_tool(config, "python") == (codemetrics.TOOL_LIZARD, None)
    assert codemetrics.language_tool(config, "typescript") == (codemetrics.TOOL_LIZARD, None)
    assert codemetrics.language_tool(config, "go") == (None, codemetrics.DISABLED)


@pytest.mark.parametrize(
    "text",
    [
        "{not json",
        json.dumps([1]),
        json.dumps({"version": 2, "tools": {}}),
        json.dumps({"version": True, "tools": {}}),
        json.dumps({"version": 1, "tools": {}, "extra": 1}),
        json.dumps({"version": 1, "tools": {"cobol": "lizard"}}),
        json.dumps({"version": 1, "tools": {"python": "radon"}}),
        json.dumps({"version": 1, "tools": {"typescript": "ruff-complexipy", "python": "lizard"}}),
    ],
)
def test_broken_declaration_falls_back_to_defaults_as_a_whole(codemetrics, text):
    """AC12: 壊れた宣言は全体を使わず既定で測り、理由を残す。一部の鍵だけを生かさない。"""
    config = codemetrics.load_config(text)
    assert config["source"] == codemetrics.SOURCE_INVALID and config["error"]
    assert config["tools"] == codemetrics.DEFAULT_TOOLS
    assert config["disabled"] == []


# ---------------- ファイルと重複検出の対象 ----------------


def test_classify_splits_by_extension_and_counts_the_rest(codemetrics):
    by_lang, ignored = codemetrics.split_by_language(["src/a.py", "src/b.ts", "src/c.TSX", "run.sh", "README.md", "Makefile"])
    assert by_lang == {"python": ["src/a.py"], "shell": ["run.sh"], "typescript": ["src/b.ts", "src/c.TSX"]}
    assert ignored == 2


def test_duplication_targets_split_python_from_the_rest(codemetrics):
    """重複検出の起動: symilar に `.py` だけ、jscpd にほかの言語（shell を含む）をまとめて。"""
    by_lang = {"python": ["a.py"], "shell": ["r.sh"], "typescript": ["b.ts"]}
    targets = codemetrics.duplication_targets(codemetrics.load_config(None), by_lang)
    assert targets == [("symilar", ["python"], ["a.py"]), ("jscpd", ["shell", "typescript"], ["b.ts", "r.sh"])]
    declared = codemetrics.load_config(json.dumps({"version": 1, "tools": {"python": "lizard", "shell": None}}))
    assert codemetrics.duplication_targets(declared, by_lang) == [("symilar", ["python"], ["a.py"]), ("jscpd", ["typescript"], ["b.ts"])]


def test_to_relative_drops_paths_outside_the_work_tree(codemetrics):
    """I3: 範囲外のパスは捨てる。"""
    assert codemetrics.to_relative("/w/work/src/a.py", ROOTS) == "src/a.py"
    assert codemetrics.to_relative("src/./a.py", ROOTS) == "src/a.py"
    assert codemetrics.to_relative("/elsewhere/a.py", ROOTS) is None
    assert codemetrics.to_relative("../a.py", ROOTS) is None


def test_count_lines_skips_blank_lines(codemetrics):
    assert codemetrics.count_lines("a\n\n   \n b\n") == 2


# ---------------- ランナーとコマンド ----------------


def test_runner_pins_the_version_and_falls_back_to_path_only_without_uvx(codemetrics):
    """AC22: uvx があれば版を固定して起動し、無いときだけ PATH のコマンドを使う。"""
    both = {"uvx", "ruff", "npx", "jscpd"}
    assert codemetrics.resolve_runner("ruff", lambda n: n in both or None) == ("uvx", ["uvx", "--from", "ruff==0.16.9", "ruff"])
    assert codemetrics.resolve_runner("complexipy", lambda n: n == "uvx" or None)[1][:3] == ["uvx", "--from", "complexipy==8.0.1"]
    assert codemetrics.resolve_runner("lizard", lambda n: n == "uvx" or None)[1][2] == "lizard==1.24.0"
    assert codemetrics.resolve_runner("symilar", lambda n: n == "uvx" or None)[1][2] == "pylint==4.0.9"
    assert codemetrics.resolve_runner("jscpd", lambda n: n in both or None) == ("npx", ["npx", "-y", "jscpd@4.3.0"])
    assert codemetrics.resolve_runner("ruff", lambda n: n == "ruff" or None) == ("path", ["ruff"])
    assert codemetrics.resolve_runner("ruff", lambda n: None) is None


def test_commands_keep_the_target_repository_unwritten(codemetrics):
    """I8: Ruff はキャッシュを書かず、complexipy のキャッシュは一時ディレクトリへ向ける。"""
    ruff = codemetrics.ruff_argv(["ruff"], ["a.py"])
    assert {"--isolated", "--no-cache", "--exit-zero"} <= set(ruff) and ruff[-1] == "a.py"
    cx = codemetrics.complexipy_argv(["complexipy"], ["a.py"], "/t/o.json", "/t/cache")
    assert cx[cx.index("--cache-dir") + 1] == "/t/cache"
    assert cx[cx.index("--max-complexity-allowed") + 1] == "1000000"
    jscpd = codemetrics.jscpd_argv(["jscpd"], ["/w/b.ts"], "/t/j")
    assert "--no-gitignore" in jscpd and jscpd[jscpd.index("--min-lines") + 1] == "8"


# ---------------- 出力の読み取り ----------------

RUFF_SAMPLE = json.dumps(
    [
        {
            "code": "C901",
            "filename": "/w/work/src/a.py",
            "location": {"row": 3, "column": 5},
            "message": "`check_new` is too complex (11 > 0)",
        },
        {"code": "PLR0912", "filename": "/w/work/src/a.py", "location": {"row": 3, "column": 5}, "message": "Too many branches (29 > 0)"},
        {
            "code": "PLR0913",
            "filename": "/w/work/src/a.py",
            "location": {"row": 3, "column": 5},
            "message": "Too many arguments in function definition (2 > 0)",
        },
        {
            "code": "C901",
            "filename": "/w/work/src/a.py",
            "location": {"row": 9, "column": 5},
            "message": "`Box.run` is too complex (1 > 0)",
        },
        {
            "code": "invalid-syntax",
            "filename": "/w/work/src/bad.py",
            "location": {"row": 1, "column": 1},
            "message": "Expected `)`, found newline",
        },
    ]
)

SOURCE = """\
import functools

@functools.cache
def check_new(a, b):
    return a


class Box:
    def run(self):
        def inner():
            return 1
        return inner()
"""


def test_ruff_values_bind_by_path_and_def_line(codemetrics, codemetrics_read):
    """Ruff の値の読み方: (パス・def の行) で結び付け、出ない PLR は 0、`invalid-syntax` は読めないファイル。"""
    values, invalid = codemetrics_read.parse_ruff(RUFF_SAMPLE.replace('"row": 3', '"row": 4'), ROOTS)
    assert invalid == {"src/bad.py"}
    listed = {"src/a.py": codemetrics_read.python_functions(SOURCE)}
    cognitive = {("src/a.py", "check_new"): 5, ("src/a.py", "Box.run"): 3}
    rows = {f["symbol"]: f for f in codemetrics_read.python_function_metrics(listed, values, cognitive, invalid)}
    assert rows["check_new"]["cc"] == 11 and rows["check_new"]["branches"] == 29
    assert rows["check_new"]["args"] == 2 and rows["check_new"]["returns"] == 0
    assert rows["check_new"]["cognitive"] == 5 and rows["check_new"]["lines"] == 2
    assert rows["Box.run"]["cc"] == 1 and rows["Box.run"]["lines"] == 4
    # complexipy は入れ子の関数を出さない
    assert rows["Box.run.inner"]["cognitive"] is None


def test_ruff_message_without_the_trailing_value_is_unreadable(codemetrics, codemetrics_read):
    bad = json.dumps([{"code": "C901", "filename": "/w/work/a.py", "location": {"row": 1}, "message": "`f` is too complex (11)"}])
    with pytest.raises(codemetrics_read.UnreadableOutput):
        codemetrics_read.parse_ruff(bad, ROOTS)
    with pytest.raises(codemetrics_read.UnreadableOutput):
        codemetrics_read.parse_ruff("not json", ROOTS)


def test_python_functions_reads_nothing_from_broken_syntax(codemetrics, codemetrics_read):
    assert codemetrics_read.python_functions("def f(:\n  pass\n") is None


def test_complexipy_names_use_dots(codemetrics, codemetrics_read):
    text = json.dumps(
        [{"complexity": 74, "file_name": "engine.py", "function_name": "Engine::run", "path": "src/engine.py", "refactor_plans": []}]
    )
    assert codemetrics_read.parse_complexipy(text, ROOTS) == {("src/engine.py", "Engine.run"): 74}
    with pytest.raises(codemetrics_read.UnreadableOutput):
        codemetrics_read.parse_complexipy(json.dumps([{"path": "a.py"}]), ROOTS)


def test_lizard_csv_needs_eleven_columns(codemetrics, codemetrics_read):
    row = '21,35,268,2,22,"check_new@144-165@src/n.py","src/n.py","check_new","check_new( a )",144,165'
    assert codemetrics_read.parse_lizard(row + "\n", ROOTS) == [
        {"path": "src/n.py", "symbol": "check_new", "cc": 35, "lines": 22, "role": "main"}
    ]
    with pytest.raises(codemetrics_read.UnreadableOutput):
        codemetrics_read.parse_lizard("1,2,3\n", ROOTS)


SYMILAR_SAMPLE = """
25 similar lines in 2 files
==src/ht_shparse.py:[219:248]
==src/write_target.py:[179:208]
       self.substs(c, st)
       return x

9 similar lines in 3 files
==tests/test_a.py:[0:9]
==tests/test_b.py:[10:19]
==tests/test_c.py:[20:29]
       assert x

8 similar lines in 2 files
==src/a.py:[4:12]
==tests/test_a.py:[40:48]
       y = 2
TOTAL lines=2872 duplicates=42 percent=1.46
"""


def test_symilar_ranges_become_one_based(codemetrics, codemetrics_read):
    """重複検出の読み取り: `[start:end]` を 1 始まりの `start + 1`〜`end` へ直す。本体とテストを分ける。"""
    parsed = codemetrics_read.parse_symilar(SYMILAR_SAMPLE, ROOTS)
    assert parsed["total_lines"] == 2872 and parsed["duplicated_lines"] == 42
    first, second, third = parsed["clones"]
    assert first["lines"] == 25
    assert first["locations"][0] == {"path": "src/ht_shparse.py", "start": 220, "end": 248}
    assert len(second["locations"]) == 3 and second["role"] == "test"
    # 1 か所でも本体があれば本体
    assert third["role"] == "main"


def test_symilar_without_total_is_unreadable(codemetrics, codemetrics_read):
    with pytest.raises(codemetrics_read.UnreadableOutput):
        codemetrics_read.parse_symilar(SYMILAR_SAMPLE.replace("TOTAL", "SUM"), ROOTS)
    with pytest.raises(codemetrics_read.UnreadableOutput):
        codemetrics_read.parse_symilar("3 similar lines in 2 files\n==a.py:[0:3]\n", ROOTS)


def test_jscpd_report_and_missing_report(codemetrics, codemetrics_read):
    """jscpd の報告が無い（調べるファイルが 0 本）なら 0 箇所。読めない報告は `unreadable_output`。"""
    report = json.dumps(
        {
            "statistics": {"total": {"lines": 4752, "sources": 27, "clones": 1, "duplicatedLines": 8}},
            "duplicates": [
                {
                    "format": "bash",
                    "lines": 9,
                    "firstFile": {"name": "/w/work/a.sh", "start": 30, "end": 38},
                    "secondFile": {"name": "/w/work/b.sh", "start": 30, "end": 39},
                }
            ],
        }
    )
    parsed = codemetrics_read.parse_jscpd(report, ROOTS)
    assert parsed["sources"] == 27 and parsed["duplicated_lines"] == 8
    assert parsed["clones"][0]["locations"] == [{"path": "a.sh", "start": 30, "end": 38}, {"path": "b.sh", "start": 30, "end": 39}]
    assert codemetrics_read.parse_jscpd(None, ROOTS)["clones"] == []
    with pytest.raises(codemetrics_read.UnreadableOutput):
        codemetrics_read.parse_jscpd("{", ROOTS)


def test_file_metrics_come_from_the_collected_list(codemetrics, codemetrics_read):
    """関数の無いファイルも載る（ツールの出力からファイルを数えない）。読めなかったファイルは載せない。"""
    functions = [{"path": "src/a.py", "lines": 12}, {"path": "src/a.py", "lines": 40}]
    rows = codemetrics_read.file_metrics(
        ["src/a.py", "src/__init__.py", "src/bad.py"], {"src/a.py": 90, "src/__init__.py": 1}, functions, {"src/bad.py"}
    )
    assert rows == [
        {"path": "src/__init__.py", "lines": 1, "functions": 0, "max_function_lines": 0, "role": "main"},
        {"path": "src/a.py", "lines": 90, "functions": 2, "max_function_lines": 40, "role": "main"},
    ]


# ---------------- 時間 ----------------


@pytest.mark.parametrize("budget", [30, 60, 7])
def test_measure_timeout_comes_from_the_budget(timeline, budget):
    """AC13: 上限は `ceil(0.05·B·60)` で、B に比例する。"""
    start = _dt.datetime(2026, 9, 26, 10, 0, tzinfo=_dt.timezone.utc)
    limits = timeline.compute(start, budget, None)
    assert limits["measure_timeout"] == math.ceil(budget * 60 * 0.05)


def test_measuring_does_not_move_any_phase_end(timeline):
    """AC15: 提案の枠の終わりは `開始 + 0.20·B` のまま。ほかの終わりも開始からの式のまま。"""
    start = _dt.datetime(2026, 9, 26, 10, 0, tzinfo=_dt.timezone.utc)
    limits = timeline.compute(start, 30, None)
    assert limits["propose_end_at"] == (start + _dt.timedelta(minutes=6)).isoformat(timespec="seconds")
    assert limits["plan_end_at"] == (start + _dt.timedelta(minutes=9)).isoformat(timespec="seconds")
    assert limits["final_end_at"] == (start + _dt.timedelta(minutes=30)).isoformat(timespec="seconds")


def test_measure_deadline_takes_at_most_half_of_what_is_left(timeline):
    """測定に使える時間 = min(上限, 0.5·max(0, 提案の枠の終わり − 今))。"""
    start = _dt.datetime(2026, 9, 26, 10, 0, tzinfo=_dt.timezone.utc)
    limits = timeline.compute(start, 30, None)
    assert timeline.measure_deadline(start, limits) == 90
    assert timeline.measure_deadline(start + _dt.timedelta(minutes=5), limits) == 30
    assert timeline.measure_deadline(start + _dt.timedelta(minutes=7), limits) == 0
