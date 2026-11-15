"""lib/design_repos.py と design-survey.py の代表のリポジトリの 2 規則（#3618）。

設計の検査が、代表のリポジトリの設定（`.ndf/design.json`）を置いたリポジトリで、振る舞いの表と
「このリポジトリだけの事情」の欄を求め（repo_scope_missing）、扱えなくなる種類を人へ残す判断へ結ぶ（repo_loss_unrouted）。
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parents[1]
SCRIPT = SCRIPTS / "design-survey.py"
SCHEMA = SCRIPTS.parent / "skills" / "design" / "schemas" / "design.schema.json"
sys.path.insert(0, str(SCRIPTS / "lib"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import deps  # noqa: E402
from survey_run import run_survey_check  # noqa: E402

deps.require("md", "schema", "pathmatch")
import design_repos as dr  # noqa: E402

SETTINGS = {
    "version": 1,
    "representative_repos": {
        "kinds": ["Web サービス", "モバイルアプリ", "業務文書だけ"],
        "paths": ["plugins/ndf/**"],
        "redline": "P2",
    },
}
S = dr.RepoSettings(("Web サービス", "モバイルアプリ", "業務文書だけ"), ("plugins/ndf/**",), "P2")

HEADER = "| 代表のリポジトリ | 設定の要点 | 直した後に起きること | この種類で扱えなくなること |\n| --- | --- | --- | --- |\n"
ROWS_OK = (
    "| Web サービス・設定なし（carmo） | Laravel | 今と同じ | 無し |\n"
    "| モバイルアプリ | Flutter | 今と同じ | `無し` |\n"
    "| 業務文書だけ | Markdown | 今と同じ | 無し（今と同じ） |\n"
)
OWN = "### このリポジトリだけの事情\n\n値は `.ndf/` に置く。\n"
RESULTS = "## 設計の結果\n\n| 課題 | 扱い | 取り込み先 | 触るファイル |\n| --- | --- | --- | --- |\n| #16 | 実装する | — | `{f}` |\n"
LEFT = "## 人へ残す判断\n\n| 判断 | 当たるレッドライン | なぜ当たるか |\n| --- | --- | --- |\n| {q} | {n} | 理由がある |\n"


def doc(
    rows: str = ROWS_OK, own: str = OWN, header: str = HEADER, behavior: bool = True, touched: str = "plugins/ndf/a.py", left: str = "C7"
) -> str:
    scope = "## 適用範囲\n\n- 働く範囲: すべて\n\n"
    if behavior:
        scope += "### 代表のリポジトリでの振る舞い\n\n" + header + rows + "\n"
    scope += own
    return "# 題\n\n## 目的\n\n目的。\n\n" + scope + "\n" + RESULTS.format(f=touched) + "\n" + LEFT.format(q="問い", n=left)


def rules(items: list[dict]) -> list[str]:
    return [it["rule"] for it in items]


# --- load_settings ---------------------------------------------------------


def _root(tmp_path: Path, data) -> Path:
    (tmp_path / ".ndf").mkdir(parents=True, exist_ok=True)
    text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    (tmp_path / ".ndf" / "design.json").write_text(text, encoding="utf-8")
    return tmp_path


def test_load_settings_reads_kinds_paths_and_redline(tmp_path):
    assert dr.load_settings(_root(tmp_path, SETTINGS)) == S


def test_load_settings_without_file_or_section_is_none(tmp_path):
    assert dr.load_settings(tmp_path) is None
    assert dr.load_settings(_root(tmp_path, {"version": 1})) is None


def test_load_settings_paths_may_be_omitted(tmp_path):
    data = {"version": 1, "representative_repos": {"kinds": ["Web サービス"], "redline": "P2"}}
    assert dr.load_settings(_root(tmp_path, data)).paths is None


@pytest.mark.parametrize(
    "repos",
    [
        {"kinds": [], "redline": "P2"},
        {"kinds": ["a"], "redline": "X1"},
        {"kinds": ["a"], "redline": "P0"},
        {"kinds": ["a"], "paths": [], "redline": "P2"},
        {"kinds": ["a"], "redline": "P2", "extra": 1},
        {"kinds": ["a", "a"], "redline": "P2"},
    ],
)
def test_load_settings_rejects_broken_shapes(tmp_path, repos):
    with pytest.raises(dr.RepoSettingsError):
        dr.load_settings(_root(tmp_path, {"version": 1, "representative_repos": repos}))


def test_load_settings_rejects_bad_json_and_version(tmp_path):
    with pytest.raises(dr.RepoSettingsError, match="JSON"):
        dr.load_settings(_root(tmp_path, "{"))
    with pytest.raises(dr.RepoSettingsError):
        dr.load_settings(_root(tmp_path, {"version": 2}))


# --- schema / dep_groups / applies -----------------------------------------


def test_published_schema_matches_the_model():
    first = dr.design_json_schema()
    assert first == dr.design_json_schema()
    assert SCHEMA.read_text(encoding="utf-8") == first


def test_dep_groups_follow_the_settings_file(tmp_path):
    assert dr.dep_groups(tmp_path, False) == ("md",)
    assert dr.dep_groups(tmp_path, True) == ("md", "schema")
    assert dr.dep_groups(None, False) == ("md",)
    root = _root(tmp_path, SETTINGS)
    assert dr.dep_groups(root, False) == ("md", "schema", "pathmatch")
    assert set(dr.dep_groups(root, True)) == {"md", "schema", "pathmatch"}


def test_applies_by_paths():
    assert dr.applies(S, {"plugins/ndf/scripts/a.py"})
    assert dr.applies(S._replace(paths=None), set())
    assert dr.applies(S, None)  # 触るファイルが分からない設計には当てる
    assert not dr.applies(S, set())
    assert not dr.applies(S, {"plugins/mcp/x.json"})


# --- check_repos -----------------------------------------------------------


def test_complete_scope_has_no_findings():
    assert dr.check_repos("d.md", doc(), S, {"C7"}) == []


@pytest.mark.parametrize(
    "text",
    [
        doc(behavior=False),
        doc(rows=ROWS_OK.splitlines(keepends=True)[0] + ROWS_OK.splitlines(keepends=True)[2]),
        doc(own=""),
        doc(own="### このリポジトリだけの事情\n\n"),
        doc(rows=ROWS_OK.replace("| 無し（今と同じ） |", "|  |")),
        doc(header=HEADER.replace("| 直した後に起きること ", "| 起きること ")),
        doc(rows=ROWS_OK.replace("| Flutter | 今と同じ |", "| Flutter |  |")),
        doc(header="| 種類 | 何が起きるか |\n| --- | --- |\n", rows="| Web サービス | 今と同じ |\n"),
    ],
    ids=["no-table", "missing-kind", "no-own", "empty-own", "empty-loss", "no-after-col", "empty-after", "wrong-columns"],
)
def test_scope_gaps_are_repo_scope_missing(text):
    got = dr.check_repos("d.md", text, S, {"C7"})
    assert got and set(rules(got)) == {"repo_scope_missing"}


def test_missing_scope_section_is_one_finding():
    text = doc().replace("## 適用範囲", "## 範囲")
    got = dr.check_repos("d.md", text, S, set())
    assert rules(got) == ["repo_scope_missing"] and got[0]["name"] == "d.md:0"


def test_same_subsection_outside_scope_is_not_counted():
    text = doc(behavior=False) + "\n## 付録\n\n### 代表のリポジトリでの振る舞い\n\n" + HEADER + ROWS_OK
    assert "repo_scope_missing" in rules(dr.check_repos("d.md", text, S, set()))


LOSS = ROWS_OK.replace("| Laravel | 今と同じ | 無し |", "| develop→main | 外す | develop を経て本番へ出す運用（代わりが無い） |")


@pytest.mark.parametrize("ids", [set(), {"P1"}, {"P20"}])
def test_loss_without_redline_row_is_unrouted(ids):
    got = dr.check_repos("d.md", doc(rows=LOSS), S, ids)
    assert rules(got) == ["repo_loss_unrouted"]


def test_loss_with_redline_row_is_routed():
    assert dr.check_repos("d.md", doc(rows=LOSS), S, {"P2", "C7"}) == []


def test_loss_rows_split_none_and_empty():
    import md

    text = doc(rows=LOSS.replace("| 無し（今と同じ） |", "|  |"))
    table = dr._behavior_table(text, md.section_named(text, "代表のリポジトリでの振る舞い"))
    lost, empty = dr._loss_rows(table)
    lines = text.splitlines()
    assert [lines[i].split("|")[1].strip() for i in lost] == ["Web サービス・設定なし（carmo）"]
    assert [lines[i].split("|")[1].strip() for i in empty] == ["業務文書だけ"]


# --- design-survey.py check ------------------------------------------------


def _repo(tmp_path: Path, text: str, settings=SETTINGS) -> Path:
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    if settings is not None:
        _root(tmp_path, settings)
    (tmp_path / "issues").mkdir()
    (tmp_path / "issues" / "issue-16-design.md").write_text(text, encoding="utf-8")
    (tmp_path / "issues" / "issue-16-design-decisions.md").write_text("# 決定\n\n## 目的\n\n無し\n", encoding="utf-8")
    return tmp_path


def test_check_flags_head_design_without_table(tmp_path):
    root = _repo(tmp_path, doc(behavior=False, own=""))
    code, out = run_survey_check(root, "--file", "issues/issue-16-design.md", "issues/issue-16-design-decisions.md")
    assert code == 1
    assert {it["rule"] for it in out["items"]} == {"repo_scope_missing"}
    assert all(it["name"].startswith("issues/issue-16-design.md:") for it in out["items"])
    assert out["metrics"]["repo_scope_missing"] == 2 and out["metrics"]["repo_loss_unrouted"] == 0


def test_check_passes_design_outside_paths(tmp_path):
    root = _repo(tmp_path, doc(behavior=False, own="", touched="plugins/mcp/x.json"))
    code, out = run_survey_check(root, "--file", "issues/issue-16-design.md")
    assert code == 0 and out["metrics"]["repo_scope_missing"] == 0


def test_check_routes_loss_through_left_table(tmp_path):
    root = _repo(tmp_path, doc(rows=LOSS))
    code, out = run_survey_check(root, "--file", "issues/issue-16-design.md")
    assert code == 1 and [it["rule"] for it in out["items"]] == ["repo_loss_unrouted"]
    root2 = _repo(tmp_path / "b", doc(rows=LOSS, left="`P2`"))
    code, out = run_survey_check(root2, "--file", "issues/issue-16-design.md")
    assert code == 0, out["items"]


def test_check_without_settings_is_unchanged(tmp_path):
    root = _repo(tmp_path, doc(behavior=False, own=""), settings=None)
    code, out = run_survey_check(root, "--file", "issues/issue-16-design.md")
    assert code == 0 and "repo_scope_missing" not in out["metrics"]


def test_check_stops_on_broken_settings(tmp_path):
    root = _repo(tmp_path, doc(), settings={"version": 1, "representative_repos": {"kinds": [], "redline": "P2"}})
    code, out = run_survey_check(root, "--file", "issues/issue-16-design.md")
    assert code == 2 and out["status"] == "stopped" and "design.json" in out["summary"]


def test_schema_subcommand_writes_out(tmp_path):
    out = tmp_path / "s.json"
    p = subprocess.run([sys.executable, str(SCRIPT), "schema", "--out", str(out)], capture_output=True, text=True)
    assert p.returncode == 0 and p.stdout.startswith(out.read_text(encoding="utf-8"))
    p = subprocess.run([sys.executable, str(SCRIPT), "schema", "--out", str(tmp_path / "no" / "s.json")], capture_output=True, text=True)
    assert p.returncode == 2


# --- 現状固定: check_repos の指摘の行・規則・理由と並び（I-001。_scope_problems の分割の前に置く） ---

HEAD_ROW = "| 代表のリポジトリ | 設定の要点 | 直した後に起きること | この種類で扱えなくなること |"
SCOPE_AT = "d.md:7"
OWN_GAP = "このリポジトリだけの事情の欄が無い: ## 適用範囲"
MISS = "repo_scope_missing"
UNROUTED = "扱えなくなることがあるのに、人へ残す判断に P2 の行が無い: "
_OK = ROWS_OK.splitlines(keepends=True)


@pytest.mark.parametrize(
    "text, expected",
    [
        (doc(), []),
        (doc().replace("## 適用範囲", "## 範囲"), [("d.md:0", MISS, "適用範囲の節が無い")]),
        (doc().replace("## 適用範囲", "### 適用範囲"), [("d.md:0", MISS, "適用範囲の節が無い")]),
        (doc(behavior=False), [(SCOPE_AT, MISS, "代表のリポジトリでの振る舞いの節が無い: ## 適用範囲")]),
        (
            doc(header="| 種類 | 何が起きるか |\n| --- | --- |\n", rows="| Web サービス | 今と同じ |\n"),
            [
                (
                    "d.md:11",
                    MISS,
                    "振る舞いの表に 代表のリポジトリ と この種類で扱えなくなること の列が無い: ### 代表のリポジトリでの振る舞い",
                )
            ],
        ),
        (doc(rows=_OK[0] + _OK[2]), [("d.md:13", MISS, f"種類 モバイルアプリ の行が無い: {HEAD_ROW}")]),
        (
            doc(rows=ROWS_OK.replace("| 無し（今と同じ） |", "|  |")),
            [("d.md:17", MISS, "扱えなくなることが空: | 業務文書だけ | Markdown | 今と同じ |  |")],
        ),
        (
            doc(header=HEADER.replace("| 直した後に起きること ", "| 起きること ")),
            [
                (
                    "d.md:13",
                    MISS,
                    "振る舞いの表に 直した後に起きること の列が無い: | 代表のリポジトリ | 設定の要点 | 起きること | この種類で扱えなくなること |",
                )
            ],
        ),
        (
            doc(rows=ROWS_OK.replace("| Flutter | 今と同じ |", "| Flutter |  |")),
            [("d.md:16", MISS, "直した後に起きることが空: | モバイルアプリ | Flutter |  | `無し` |")],
        ),
        (
            doc(rows="| Web サービス | x |\n" + _OK[1] + _OK[2]),
            [
                ("d.md:15", MISS, "扱えなくなることが空: | Web サービス | x |"),
                ("d.md:15", MISS, "直した後に起きることが空: | Web サービス | x |"),
            ],
        ),
        (doc(own=""), [(SCOPE_AT, MISS, OWN_GAP)]),
        (doc(own="### このリポジトリだけの事情\n\n"), [(SCOPE_AT, MISS, OWN_GAP)]),
        (doc(own="### このリポジトリだけの事情\n\n```\nx\n```\n"), [(SCOPE_AT, MISS, OWN_GAP)]),
        (
            doc(rows=LOSS),
            [
                (
                    "d.md:15",
                    "repo_loss_unrouted",
                    UNROUTED + "| Web サービス・設定なし（carmo） | develop→main | 外す | develop を経て本番へ出す運用（代わりが無い） |",
                )
            ],
        ),
        (
            doc(rows="| Web サービス | a |  |  |\n| 他 | b | 起きる | 失う |\n", own=""),
            [
                (SCOPE_AT, MISS, OWN_GAP),
                ("d.md:13", MISS, f"種類 モバイルアプリ の行が無い: {HEAD_ROW}"),
                ("d.md:13", MISS, f"種類 業務文書だけ の行が無い: {HEAD_ROW}"),
                ("d.md:15", MISS, "扱えなくなることが空: | Web サービス | a |  |  |"),
                ("d.md:15", MISS, "直した後に起きることが空: | Web サービス | a |  |  |"),
                ("d.md:16", "repo_loss_unrouted", UNROUTED + "| 他 | b | 起きる | 失う |"),
            ],
        ),
    ],
    ids=[
        "complete",
        "no-scope",
        "scope-not-h2",
        "no-behavior",
        "wrong-columns",
        "missing-kind",
        "empty-loss",
        "no-after-col",
        "empty-after",
        "short-row",
        "no-own",
        "empty-own",
        "fenced-own",
        "loss-unrouted",
        "every-gap-in-line-order",
    ],
)
def test_check_repos_items_are_fixed(text, expected):
    """現状固定: 指摘の name（行）・rule・text と、行の順（同じ行は見つけた順）。"""
    got = dr.check_repos("d.md", text, S, set())
    assert [(it["name"], it["rule"], it["text"]) for it in got] == expected


# --- 現状固定: design-survey.py の judge_docs の指摘の並びと metrics（I-014。judge_docs の分割の前に置く） ---


def _survey_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("design_survey_judge_docs", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


DECISION_SELF = "## 決定の記録\n\n### 決定 1: 何かのため、何かを決める\n\n理由。\n\n- 作り方: 自前で作る\n\n根拠: Value 7（MVV 版 5）\n"
BASE_METRICS = {
    "docs": 4,
    "decisions": 1,
    "with_method": 1,
    "self_built": 1,
    "skipped": 0,
    "unnamed_redline": 1,
    "detail_docs": 0,
    "detail_section": 0,
    "detail_failure": 0,
    "detail_reuse": 0,
    "detail_file": 0,
}
SURVEY_ITEMS = [("no_candidates", "issues/issue-16-design.md:38 決定 1"), ("unnamed_redline", "issues/issue-16-design.md:42")]
DETAIL_ITEMS = [("detail_missing", "#16"), ("detail_missing", "#17"), ("detail_missing", "#18")]
REPO_ITEMS = [
    ("repo_scope_missing", "issues/issue-16-design.md:7"),
    ("repo_loss_unrouted", "issues/issue-16-design.md:15"),
    ("repo_scope_missing", "issues/issue-18-design.md:7"),
]


def _judge_docs_inputs(mod):
    head16 = doc(rows=LOSS, own="") + "\n" + DECISION_SELF + "\n範囲は利用者が選ぶ。\n"
    return [
        mod.Doc("issues/issue-16-design.md", head16),
        mod.Doc("issues/issue-16-design-decisions.md", "# 決定\n\n## 目的\n\n無し\n"),
        mod.Doc("issues/issue-17-design.md", doc(behavior=False, own="", touched="plugins/mcp/x.json")),
        mod.Doc("issues/issue-18-design.md", doc(behavior=False)),
    ]


@pytest.mark.parametrize(
    "settings, require, expected_items, extra_metrics",
    [
        (
            S,
            True,
            SURVEY_ITEMS + DETAIL_ITEMS + REPO_ITEMS,
            {"detail_missing": 3, "repo_scope_missing": 2, "repo_loss_unrouted": 1},
        ),
        (S, False, SURVEY_ITEMS + REPO_ITEMS, {"detail_missing": 0, "repo_scope_missing": 2, "repo_loss_unrouted": 1}),
        (None, True, SURVEY_ITEMS + DETAIL_ITEMS, {"detail_missing": 3}),
    ],
    ids=["settings-require-detail", "settings-only", "no-settings"],
)
def test_judge_docs_items_and_metrics_are_fixed(settings, require, expected_items, extra_metrics):
    """現状固定: 調査 → レッドライン → 詳細設計 → 代表のリポジトリの順に指摘を並べ、metrics のキーの順も固定する。"""
    mod = _survey_module()
    items, metrics = mod.judge_docs(_judge_docs_inputs(mod), require, settings)
    assert [(it["rule"], it["name"]) for it in items] == expected_items
    want = {**BASE_METRICS, **extra_metrics}
    assert metrics == want and list(metrics) == list(want)


@pytest.mark.parametrize("settings", [S, None], ids=["settings", "no-settings"])
def test_judge_docs_without_documents_is_fixed(settings):
    """現状固定: 文書が 0 件でも metrics のキーはそろい、設定があるときだけ代表のリポジトリの 2 規則のキーが付く。"""
    mod = _survey_module()
    items, metrics = mod.judge_docs([], False, settings)
    want = {**BASE_METRICS, "docs": 0, "decisions": 0, "with_method": 0, "self_built": 0, "unnamed_redline": 0, "detail_missing": 0}
    if settings is not None:
        want.update({"repo_scope_missing": 0, "repo_loss_unrouted": 0})
    assert items == [] and metrics == want and list(metrics) == list(want)
