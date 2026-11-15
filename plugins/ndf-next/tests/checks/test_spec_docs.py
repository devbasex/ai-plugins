"""規約の検査と生成（`ndf.checks.spec_docs`）: コードと正本の表と規約が食い違えば、名前を出して落とす。

プラグインの契約・生成物・`bin/`・Skill・エージェントを一時の置き場へ写し、そこを壊して確かめる（実物を書き換えない）。
"""

from __future__ import annotations

import ast
import dataclasses
import json
import shutil
from pathlib import Path
from urllib.parse import urljoin, urlparse
from urllib.request import url2pathname

import pytest

from ndf.checks import run, spec_docs, spec_model, spec_render
from ndf.checks.model import CheckError, Scope, Source, load_rules

PLUGIN_ROOT = Path(__file__).resolve().parents[2]
COPIED = ("contracts", "docs", "bin", "skills", "agents", "manifests")


class Copy:
    def __init__(self, tmp: Path) -> None:
        self.repo = tmp / "repo"
        self.root = self.repo / "plugins" / "p"
        for name in COPIED:
            shutil.copytree(PLUGIN_ROOT / name, self.root / name, ignore=shutil.ignore_patterns("__pycache__"))

    def path(self, rel: str) -> Path:
        return self.root / rel

    def edit_json(self, rel: str, change) -> None:
        path = self.path(rel)
        data = json.loads(path.read_text(encoding="utf-8"))
        change(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    def write(self, rel: str, text: str) -> None:
        path = self.path(rel)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def findings(self, write_spec: bool = False):
        scope = Scope(self.repo, self.root, "p")
        scope.sources = run.collect(self.root, self.repo)
        return spec_docs.check(scope, load_rules(), write_spec)


@pytest.fixture
def copy(tmp_path: Path) -> Copy:
    return Copy(tmp_path)


def _rows(found, rule):
    return [f.detail for f in found if f.rule == rule]


def _row(data: dict, ident: str) -> dict:
    return next(r for r in data["rows"] if r["id"] == ident)


def test_the_plugin_has_no_findings_and_render_is_stable(copy):
    assert copy.findings() == []
    model = spec_model.load(copy.root)
    assert spec_render.render(model) == spec_render.render(spec_model.load(copy.root))


def test_hand_edit_of_a_page_is_caught_and_write_spec_restores_it(copy):
    page = copy.path("docs/contracts/env.md")
    page.write_text(page.read_text(encoding="utf-8") + "手で足した行\n", encoding="utf-8")
    copy.write("docs/contracts/stale.md", "残り\n")
    found = copy.findings()
    assert {f.path.rsplit("/", 1)[-1] for f in found if f.rule == spec_docs.DOCS} == {"env.md", "stale.md"}
    assert copy.findings(write_spec=True) == []
    assert not copy.path("docs/contracts/stale.md").exists()


def test_a_key_only_in_the_files_table_names_the_declaration_and_key(copy):
    copy.edit_json("contracts/domains/ndf-files.json", lambda d: _row(d, "review")["keys"].append("zzz"))
    assert any("review" in d and "zzz" in d for d in _rows(copy.findings(), spec_docs.DECL))


def test_a_key_without_description_or_main_key_outside_keys_is_caught(copy):
    rel = "contracts/schemas/declarations/review.schema.json"
    copy.edit_json(rel, lambda d: d["properties"]["focus"].pop("description"))
    copy.edit_json("contracts/domains/ndf-files.json", lambda d: _row(d, "review").update(main_keys=["nope"]))
    details = _rows(copy.findings(), spec_docs.DECL)
    assert any("focus" in d and "説明" in d for d in details)
    assert any("main_keys の nope" in d for d in details)


def test_a_declaration_without_schema_is_caught(copy):
    copy.path("contracts/schemas/declarations/review.schema.json").unlink()
    assert any("宣言 review にスキーマか節の表が無い" in d for d in _rows(copy.findings(), spec_docs.DECL))


def test_the_real_declaration_is_checked_with_a_json_pointer(copy):
    (copy.repo / ".ndf").mkdir()
    (copy.repo / ".ndf" / "review.json").write_text('{"version": 1, "focus": "一つ"}', encoding="utf-8")
    found = [f for f in copy.findings() if f.rule == spec_docs.DECL]
    assert [f.path for f in found] == [".ndf/review.json"]
    assert found[0].detail.startswith("/focus:")


def test_result_schemas_reach_the_result_contract_from_their_own_file():
    """結果のスキーマの `$id` はファイルの置き場から解いて自分を指し、結果の契約への `$ref` は実在のファイルへ解ける。"""
    paths = sorted((PLUGIN_ROOT / "contracts" / spec_model.IO_SCHEMA_DIR).glob(f"*.result{spec_model.SCHEMA_SUFFIX}"))
    assert paths
    for path in paths:
        base = urljoin(path.as_uri(), json.loads(path.read_text(encoding="utf-8"))["$id"])
        assert base == path.as_uri(), path.name
        assert Path(url2pathname(urlparse(urljoin(base, spec_model.RESULT_REF)).path)).is_file(), path.name


def test_io_findings_for_bin_result_schema_and_manual_rows(copy):
    copy.write("bin/extra", "#!/bin/sh\n")
    copy.path("contracts/schemas/io/check.result.schema.json").unlink()
    copy.edit_json("contracts/domains/manual-args.json", lambda d: d["rows"].append({"id": "check", "command": "check", "meaning": "m"}))
    details = _rows(copy.findings(), spec_docs.IO)
    assert any("bin/extra" in d for d in details)
    assert any("check: 結果のスキーマが無い" in d for d in details)
    assert any("check: argparse から取り出せる" in d for d in details)


def test_env_reads_finds_the_four_forms_and_skips_names_in_variables():
    tree = ast.parse(
        "import os\n"
        "a = os.environ['A']\n"
        "b = os.environ.get('B')\n"
        "c = os.getenv('C')\n"
        "d = 'D' in os.environ\n"
        "name = 'E'\n"
        "e = os.environ.get(name)\n"
    )
    assert spec_docs.env_reads(tree) == [(2, "A"), (3, "B"), (4, "C"), (5, "D")]


def test_written_calls_and_call_errors(copy):
    text = "打つ: `ndf check --base x | tail`\n`ndf:design` は Skill\nplugins/p/bin/ndf contracts check --nope\nndf の副命令\n"
    calls = spec_docs.written_calls(text)
    assert calls == [(1, ["check", "--base", "x"]), (3, ["contracts", "check", "--nope"])]
    commands = {c.name: c for c in spec_model.load(copy.root).commands}
    assert spec_docs.call_errors(["check", "--base=x", "<起点>"], commands) == []
    assert spec_docs.call_errors(["contracts", "check", "--nope"], commands) == ["引数 --nope が無い"]
    assert spec_docs.call_errors(["contracts", "nosuch"], commands) == ["下位の命令 nosuch が無い"]
    assert spec_docs.call_errors(["nosuch"], commands) == ["副命令 nosuch が無い"]
    assert spec_docs.call_errors(["worktree", "--cwd", "/tmp/example", "list"], commands) == []
    # 残りの語をすべて取る位置引数（REMAINDER）の後ろの語は、引数の名前として照合しない
    assert spec_docs.call_errors(["test-slot", "run", "--", "uv", "run", "pytest", "-q"], commands) == []
    assert spec_docs.call_errors(["relay", "run", "--name", "a", "claude", "-p", "x"], commands) == []
    assert spec_docs.call_errors(["test-slot", "run", "--nope", "--", "x"], commands) == ["引数 --nope が無い"]
    # argparse が足す -h / --help は引数として取り出さないが、呼び出しには書いてよい
    assert spec_docs.call_errors(["worktree", "--help"], commands) == []
    assert spec_docs.call_errors(["check", "-h"], commands) == []
    assert spec_docs.call_errors(["contracts", "check", "--help"], commands) == []


def test_a_written_call_with_an_unknown_argument_names_the_file_and_line(copy):
    copy.write("skills/chrome-cdp/references/calls.md", "# 例\n\n`ndf check --nope`\n")
    found = [f for f in copy.findings() if f.rule == spec_docs.SKILL]
    assert [(f.path.rsplit("/", 1)[-1], f.line) for f in found] == [("calls.md", 3)]
    assert "ndf check --nope" in found[0].detail


def test_a_moved_skill_without_an_io_row_is_caught(copy):
    copy.write("skills/new-skill/SKILL.md", "---\nname: new-skill\ndescription: d\n---\n")
    copy.edit_json("manifests/skills.json", lambda d: d["skills"].append({"name": "new-skill", "runtimes": ["claude", "codex"]}))
    assert any("new-skill" in d for d in _rows(copy.findings(), spec_docs.SKILL))


def test_public_rows_must_hit_an_item_and_spec_places_must_exist(copy):
    row = {
        "id": "cmd-x",
        "kind": "command",
        "target": "nosuch",
        "args": ["x"],
        "env": [],
        "owner_child": "S2",
        "order": "kept",
        "source": "s",
    }
    copy.edit_json("contracts/domains/entrypoints.json", lambda d: d["rows"].append(row))
    copy.edit_json("contracts/domains/entrypoints.json", lambda d: d["rows"].remove(_row(d, "spec-io")))
    details = _rows(copy.findings(), spec_docs.PUBLIC)
    assert any("cmd-x" in d for d in details)
    assert any("contracts/schemas/io" in d for d in details)


def test_env_page_has_no_example_column_for_secrets(copy):
    model = spec_model.load(copy.root)
    page = spec_render.page_env(model)
    secret = [r["id"] for r in model.env if r["secret"]]
    lines = [line for line in page.splitlines() if any(line.startswith(f"| `{name}` ") for name in secret)]
    assert len(lines) == len(secret)
    assert all(line.count(" | ") == 5 for line in lines)


def test_broken_table_is_check_error(copy):
    copy.path("contracts/domains/skill-io.json").write_text("{", encoding="utf-8")
    with pytest.raises(CheckError):
        copy.findings()


def test_plugin_without_contracts_is_not_checked(tmp_path):
    root = tmp_path / "repo" / "plugins" / "p"
    root.mkdir(parents=True)
    scope = Scope(tmp_path / "repo", root, "p")
    assert spec_docs.check(scope, load_rules()) == []


# --- 現状固定: spec-decl のキーと注釈の指摘（_key_errors を decl_findings 経由で通す） ---


def _decl_only(copy, decl):
    """実物のモデルの宣言を 1 つに差し替え、spec-decl の指摘を (rule, path, line, detail) で返す。"""
    model = dataclasses.replace(spec_model.load(copy.root), declarations=(decl,))
    found = spec_docs.decl_findings(model, copy.repo, lambda p: f"plugins/p/{p}")
    return [(f.rule, f.path, f.line, f.detail) for f in found]


def _decl(schema, read_by=({"skill": "s"},)):
    return spec_model.DeclarationSpec(
        id="zz",
        path="zz.json",
        tracked=True,
        keys=tuple(k for k in sorted(schema.get("properties", {})) if k != "version"),
        main_keys=(),
        surface="public",
        schema=schema,
        read_by=read_by,
    )


def test_key_errors_pins_every_message_and_its_order(copy):
    schema = {
        "properties": {
            "b_no_type": {"description": "d"},
            "a_no_desc": {"type": "string"},
            "c_not_dict": "x",
            "d_ok_ref": {"description": "d", "$ref": "#/x"},
        },
        "x-ndf-place": "elsewhere",
    }
    path = "plugins/p/contracts/schemas/declarations/zz.schema.json"
    assert _decl_only(copy, _decl(schema, read_by=())) == [
        (spec_docs.DECL, path, 1, "宣言 zz: キー a_no_desc に説明（description）が無い"),
        (spec_docs.DECL, path, 1, "宣言 zz: キー b_no_type に型が無い"),
        (spec_docs.DECL, path, 1, "宣言 zz: キー c_not_dict に説明（description）が無い"),
        (spec_docs.DECL, path, 1, "宣言 zz: 注釈 x-ndf-place は main か worktree"),
        (spec_docs.DECL, path, 1, "宣言 zz: 注釈 x-ndf-read-by（読む工程）が無い"),
        (spec_docs.DECL, path, 1, "宣言 zz: 例（examples）が無い"),
    ]


@pytest.mark.parametrize("word", spec_docs.TYPE_WORDS)
def test_key_errors_accepts_each_type_word(copy, word):
    schema = {"properties": {"k": {"description": "d", word: "x"}}, "x-ndf-place": "worktree", "examples": [{}]}
    assert _decl_only(copy, _decl(schema)) == []


@pytest.mark.parametrize("place", ["main", "worktree"])
def test_key_errors_without_properties_and_with_a_valid_place_has_no_findings(copy, place):
    assert _decl_only(copy, _decl({"x-ndf-place": place, "examples": [{}]})) == []


def test_key_errors_missing_place_and_empty_examples_are_findings(copy):
    found = _decl_only(copy, _decl({"examples": []}))
    assert [d for *_, d in found] == ["宣言 zz: 注釈 x-ndf-place は main か worktree", "宣言 zz: 例（examples）が無い"]


def test_key_errors_are_not_run_for_a_declaration_without_schema(copy):
    decl = dataclasses.replace(_decl({}), schema=None, sections=({"id": "s"},))
    assert _decl_only(copy, decl) == []


# --- 現状固定: spec-io の指摘（io_findings の副命令・環境変数・起動スクリプトの 3 つの部分と並び） ---


def _command(name, extracted, manual, result_schema):
    return spec_model.CommandSpec(
        name=name, module=f"ndf.{name}", args=None, extracted=extracted, manual=manual, result_schema=result_schema, surface="public"
    )


def _io_only(copy, sources=(), **fields):
    """実物のモデルの列を差し替え、spec-io の指摘を (rule, path, line, detail) で返す。"""
    model = dataclasses.replace(spec_model.load(copy.root), **fields)
    scope = Scope(copy.repo, copy.root, "p")
    for rel, text in sources:
        copy.write(rel, text)
        scope.sources.append(Source(copy.path(rel), f"plugins/p/{rel}"))
    found = spec_docs.io_findings(model, scope, lambda p: f"plugins/p/{p}")
    return [(f.rule, f.path, f.line, f.detail) for f in found]


def test_io_findings_pins_command_messages_and_their_order(copy):
    ok = {"properties": {"tool": {"const": "c-ok"}}}
    commands = (
        _command("c-both", True, True, {"properties": {"tool": {"const": "other"}}}),
        _command("c-none", False, False, None),
        _command("c-ok", True, False, ok),
        _command("c-manual", False, True, {"properties": {}}),
    )
    io = "plugins/p/contracts/schemas/io"
    assert _io_only(copy, commands=commands, env=(), launchers=(), bin_files=()) == [
        (spec_docs.IO, io, 1, "副命令 c-both: argparse から取り出せる。手書きの引数の表（manual-args）の行を消す"),
        (spec_docs.IO, io, 1, "副命令 c-both: 結果のスキーマの tool の const が c-both でない"),
        (spec_docs.IO, io, 1, "副命令 c-none: 引数を取り出せず、手書きの引数の表（manual-args）にも行が無い"),
        (spec_docs.IO, io, 1, "副命令 c-none: 結果のスキーマが無い"),
        (spec_docs.IO, io, 1, "副命令 c-manual: 結果のスキーマの tool の const が c-manual でない"),
    ]


def test_io_findings_pins_env_messages_for_code_only(copy):
    code = "import os\nos.environ['KNOWN']\nos.getenv('UNKNOWN_B')\nos.environ.get('UNKNOWN_A')\n"
    sources = [("src/ndf/zz_env.py", code), ("tests/zz_env.py", code)]
    found = _io_only(copy, sources, commands=(), env=({"id": "KNOWN"},), launchers=(), bin_files=())
    assert found == [
        (spec_docs.IO, "plugins/p/src/ndf/zz_env.py", 3, "環境変数 UNKNOWN_B が環境変数の表（env）に無い"),
        (spec_docs.IO, "plugins/p/src/ndf/zz_env.py", 4, "環境変数 UNKNOWN_A が環境変数の表（env）に無い"),
    ]


def test_io_findings_pins_launcher_messages_and_their_order(copy):
    found = _io_only(
        copy, commands=(), env=(), launchers=({"id": "z-row"}, {"id": "a-row"}, {"id": "both"}), bin_files=("y-bin", "both", "b-bin")
    )
    launchers = "plugins/p/contracts/domains/launchers.json"
    assert found == [
        (spec_docs.IO, launchers, 1, "bin/y-bin が起動スクリプトの表（launchers）に無い"),
        (spec_docs.IO, launchers, 1, "bin/b-bin が起動スクリプトの表（launchers）に無い"),
        (spec_docs.IO, launchers, 1, "起動スクリプトの表の行 a-row のファイルが bin/ に無い"),
        (spec_docs.IO, launchers, 1, "起動スクリプトの表の行 z-row のファイルが bin/ に無い"),
    ]


def test_io_findings_orders_commands_then_env_then_launchers(copy):
    found = _io_only(
        copy,
        [("src/ndf/zz_env.py", "import os\nos.getenv('NOPE')\n")],
        commands=(_command("c", True, False, None),),
        env=(),
        launchers=({"id": "row"},),
        bin_files=("file",),
    )
    assert [d for *_, d in found] == [
        "副命令 c: 結果のスキーマが無い",
        "環境変数 NOPE が環境変数の表（env）に無い",
        "bin/file が起動スクリプトの表（launchers）に無い",
        "起動スクリプトの表の行 row のファイルが bin/ に無い",
    ]


def test_io_findings_of_the_real_model_is_empty(copy):
    scope = Scope(copy.repo, copy.root, "p")
    scope.sources = run.collect(copy.root, copy.repo)
    assert spec_docs.io_findings(spec_model.load(copy.root), scope, lambda p: f"plugins/p/{p}") == []


# --- 現状固定: Skill のページの行（page_skills の Skill・報告・起動スクリプトの行） ---


def test_page_skills_pins_the_rows_of_skills_reports_and_launchers(copy):
    skill = spec_model.SkillSpec
    skills = (
        skill(name="a-moved", surface=spec_model.INTERNAL, present=False, row=False),
        skill(
            name="b-full",
            surface=spec_model.PUBLIC,
            present=True,
            row=True,
            args=("", "run .+"),
            goal_end="done",
            declarations=("d1", "d2"),
            env=("E1",),
            result="R",
            meaning="m|x",
        ),
    )
    entrypoints = (
        {"id": "r", "kind": "report", "target": "tgt", "args": ["x", "y"], "env": ["E2"], "order": "kept"},
        {"id": "r2", "kind": "report", "target": "t2", "env": [], "order": "kept"},
        {"id": "s", "kind": "skill", "target": "b-full", "args": [], "env": [], "order": "kept"},
    )
    launchers = (
        {"id": "l1", "kind": "launcher", "passes_to": "p", "meaning": "mm"},
        {"id": "l2", "kind": "other", "passes_to": "q", "meaning": "nn"},
    )
    model = dataclasses.replace(spec_model.load(copy.root), skills=skills, entrypoints=entrypoints, launchers=launchers)
    page = spec_render.page_skills(model)
    assert [line for line in page.splitlines() if line.startswith("| `")] == [
        "| `a-moved`（skills/ へ移す前） | 内部（予告なく変えてよい） | 引数を受けない | — | — | — | — | — |",
        "| `b-full` | 公開 | （引数なし）<br>`run .+` | done | `d1`・`d2` | `E1` | R | m\\|x |",
        "| `tgt` | `x`・`y` | `E2` |",
        "| `t2` | — | — |",
        "| `l1` | p | mm |",
    ]
