"""規約の検査と生成（`ndf check` の規則 spec-docs・spec-decl・spec-io・spec-skill・spec-public。#3518）。

規約のモデル（`spec_model`）を読み、描画（`spec_render`）した中身とコミットされた生成物を比べ、コードと正本の表と規約の
食い違いを指摘にする。`--write-spec` のときは描画した中身を書いてから比べる（書くのは生成物の置き場だけ）。

| 規則 | 縛る不変条件 |
| --- | --- |
| spec-decl | 宣言の行がスキーマか節の表を持つ・スキーマのキーと置き場の表の `keys` が等しい・`main_keys` が `keys` に含まれる・キーが型と説明を持つ・`.ndf/` の実物がスキーマに通る |
| spec-io | 副命令が結果のスキーマを持つ・引数の出所が取り出しか手書きの表のどちらか一方・コードが名前で読む環境変数が環境変数の表にある・`bin/` のファイルが起動スクリプトの表にある |
| spec-skill | 実体のある配る Skill が Skill の入出力の表に行を持つ・Skill の本文とエージェント定義に書かれた `ndf` の呼び出しが規約の副命令と引数に当たる |
| spec-public | 公開する面の表の行が規約の項目に当たる・規約の置き場の行が実在のディレクトリを指す |
| spec-docs | コミットされた生成物が、生成し直した中身と 1 字も違わない |

契約の置き場（`contracts/`）を持たないプラグインには当てない（検査のテストが作る小さなプラグイン）。
"""

from __future__ import annotations

import ast
import json
import shlex
from collections.abc import Callable
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator

from ndf import identity
from ndf.checks import spec_model, spec_render
from ndf.checks.model import CheckError, Finding, Scope
from ndf.checks.spec_args import Arg, ArgTree
from ndf.checks.spec_model import CONTRACTS, DECL_SCHEMA_DIR, IO_SCHEMA_DIR, CommandSpec, DeclarationSpec, SpecModel, opt
from ndf.contracts import facts

DOCS, DECL, IO, SKILL, PUBLIC = "spec-docs", "spec-decl", "spec-io", "spec-skill", "spec-public"
# キーの型の語（どれか 1 つを持てば型がある）
TYPE_WORDS = ("type", "$ref", "anyOf", "oneOf", "const", "enum")
# 規約の置き場（公開する面の表の種類 spec の行が指す、プラグインの根からの相対パス）
SPEC_TARGETS = (spec_model.PAGES_DIR, f"{CONTRACTS}/{DECL_SCHEMA_DIR}", f"{CONTRACTS}/{IO_SCHEMA_DIR}")
# 書かれた呼び出しを探す置き場と、呼び出しの区切りの語
CALL_DIRS = ("skills/", "agents/")
CALL_STOPS = frozenset({"|", "||", "&&", ";", ">", ">>", "2>&1", "&", ")", "<"})
# 呼び出しの先頭の語（エントリポイントの短い名前か、末尾がそのファイルのパス）
ENTRY = identity.FIXED_WORDS["entrypoint"]
# プラグインの根からの相対パス → リポジトリの根からの相対パス（指摘の置き場）
Rel = Callable[[str], str]


def _table_rel(rel: Rel, name: str) -> str:
    """事実の表 `name` のリポジトリの根からの相対パス（置き場の facts/ か domains/ は `facts.place` が決める）。"""
    return rel(f"{CONTRACTS}/{facts.place(name)}")


def check(scope: Scope, rules: dict[str, Any], write_spec: bool = False, written: list[str] | None = None) -> list[Finding]:
    """規約の 5 つの規則の指摘を返す。`write_spec` なら生成物を書いてから比べ、書いたパスを `written` へ足す。"""
    if "spec" not in rules:
        raise CheckError("規則のデータに鍵 spec が無い")
    conf = rules["spec"]
    root = scope.plugin_root
    if not (root / CONTRACTS).is_dir():
        return []
    model = spec_model.load(root)
    rendered = spec_render.render(model)
    if write_spec:
        done = write(rendered, root, conf["generated"])
        if written is not None:
            written += done

    def rel(path: str) -> str:
        return scope.rel(root / path)

    found = decl_findings(model, scope.repo_root, rel) + io_findings(model, scope, rel) + skill_findings(model, scope, rel)
    found += public_findings(model, root, rel) + docs_findings(rendered, root, conf["generated"], rel)
    return found


# --- spec-decl ---


def _key_errors(decl: DeclarationSpec) -> list[str]:
    """キーごとの型と説明、スキーマの注釈の欠け。"""
    assert decl.schema is not None
    errs = []
    for key, prop in sorted(opt(decl.schema, "properties", {}).items()):
        if not isinstance(prop, dict) or "description" not in prop:
            errs.append(f"キー {key} に説明（description）が無い")
        elif not any(w in prop for w in TYPE_WORDS):
            errs.append(f"キー {key} に型が無い")
    places = sorted(spec_model.DECL_PLACES)
    if opt(decl.schema, "x-ndf-place", None) not in places:
        errs.append(f"注釈 x-ndf-place は {' か '.join(places)}")
    if not decl.read_by:
        errs.append("注釈 x-ndf-read-by（読む工程）が無い")
    if not opt(decl.schema, "examples"):
        errs.append("例（examples）が無い")
    return errs


def _keys_errors(decl: DeclarationSpec) -> list[str]:
    """置き場の表の keys とスキーマの最上位の非推奨でないキーの食い違い、keys に無い main_keys。"""
    errs = [f"main_keys の {k} が keys に無い" for k in decl.main_keys if k not in decl.keys]
    if decl.schema is None:
        return errs
    props = opt(decl.schema, "properties", {})
    # スキーマの最上位で置き場の表の keys と比べないキー（表の列 keys の説明のとおり）
    schema_only = {"version", "$schema"}
    live = {k for k, v in props.items() if k not in schema_only and not (isinstance(v, dict) and v.get("deprecated") is True)}
    errs += [f"キー {k} が置き場の表の keys にだけある（スキーマに無い）" for k in sorted(set(decl.keys) - live)]
    errs += [f"キー {k} がスキーマにだけある（置き場の表の keys に無い）" for k in sorted(live - set(decl.keys))]
    return errs


def _real_errors(decl: DeclarationSpec, path: Path) -> list[str]:
    """`.ndf/` の実物をスキーマで確かめる。無いファイルは確かめない。"""
    if decl.schema is None or not path.is_file():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"JSON として読めない: {exc}"]
    errs = Draft202012Validator(decl.schema).iter_errors(data)
    return sorted(f"/{'/'.join(str(p) for p in e.absolute_path)}: {e.message}" for e in errs)


def decl_findings(model: SpecModel, repo_root: Path, rel: Rel) -> list[Finding]:
    files_rel = _table_rel(rel, "ndf-files")
    decl_dir = repo_root / model.places[spec_model.decl_place(model.places)]["path"]
    found = []
    for decl in model.declarations:
        schema_rel = rel(f"{CONTRACTS}/{DECL_SCHEMA_DIR}/{spec_model.decl_schema_rel(decl.id, spec_model.SCHEMA_SUFFIX)}")
        if decl.schema is None and decl.sections is None:
            found.append(Finding(DECL, files_rel, 1, f"宣言 {decl.id} にスキーマか節の表が無い"))
        found += [Finding(DECL, files_rel, 1, f"宣言 {decl.id}: {e}") for e in _keys_errors(decl)]
        if decl.schema is not None:
            found += [Finding(DECL, schema_rel, 1, f"宣言 {decl.id}: {e}") for e in _key_errors(decl)]
            real = decl_dir / decl.path
            found += [Finding(DECL, real.relative_to(repo_root).as_posix(), 1, e) for e in _real_errors(decl, real)]
    return found


# --- spec-io ---


def _source_errors(command: CommandSpec) -> list[str]:
    if command.extracted and command.manual:
        return ["argparse から取り出せる。手書きの引数の表（manual-args）の行を消す"]
    if not command.extracted and not command.manual:
        return ["引数を取り出せず、手書きの引数の表（manual-args）にも行が無い"]
    return []


def _result_errors(command: CommandSpec) -> list[str]:
    if command.result_schema is None:
        return ["結果のスキーマが無い"]
    tool = opt(opt(command.result_schema, "properties", {}), "tool", {})
    return [] if opt(tool, "const", None) == command.name else [f"結果のスキーマの tool の const が {command.name} でない"]


def _is_environ(node: ast.expr) -> bool:
    return isinstance(node, ast.Attribute) and node.attr == "environ" and isinstance(node.value, ast.Name) and node.value.id == "os"


def _const(node: ast.expr | None) -> str | None:
    return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None


def _env_name(node: ast.AST) -> str | None:
    """名前の文字列で環境変数を読む式なら、その名前。"""
    if isinstance(node, ast.Subscript) and _is_environ(node.value):
        return _const(node.slice)
    if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.args:
        func = node.func
        reads = (func.attr == "get" and _is_environ(func.value)) or (
            func.attr == "getenv" and isinstance(func.value, ast.Name) and func.value.id == "os"
        )
        return _const(node.args[0]) if reads else None
    if isinstance(node, ast.Compare) and len(node.comparators) == 1 and isinstance(node.ops[0], (ast.In, ast.NotIn)):
        return _const(node.left) if _is_environ(node.comparators[0]) else None
    return None


def env_reads(tree: ast.Module) -> list[tuple[int, str]]:
    """構文木から、名前の文字列で読む環境変数を（行, 名前）の組で集める。名前が定数でない読み方は数えない。"""
    found = {(node.lineno, name) for node in ast.walk(tree) if (name := _env_name(node)) is not None and hasattr(node, "lineno")}
    return sorted(found)


def io_findings(model: SpecModel, scope: Scope, rel: Rel) -> list[Finding]:
    """副命令の引数の出所と結果のスキーマ、コードが読む環境変数、起動スクリプトの表と `bin/` の照合の指摘（この順）。"""
    return _command_findings(model, rel) + _env_findings(model, scope) + _launcher_findings(model, rel)


def _command_findings(model: SpecModel, rel: Rel) -> list[Finding]:
    io_rel = rel(f"{CONTRACTS}/{IO_SCHEMA_DIR}")
    found = []
    for command in model.commands:
        found += [Finding(IO, io_rel, 1, f"副命令 {command.name}: {e}") for e in _source_errors(command) + _result_errors(command)]
    return found


def _env_findings(model: SpecModel, scope: Scope) -> list[Finding]:
    known = model.env_ids
    found = []
    for src in scope.code():
        found += [
            Finding(IO, src.rel, line, f"環境変数 {name} が環境変数の表（env）に無い")
            for line, name in env_reads(src.tree)
            if name not in known
        ]
    return found


def _launcher_findings(model: SpecModel, rel: Rel) -> list[Finding]:
    rows = {r["id"] for r in model.launchers}
    launchers_rel = _table_rel(rel, "launchers")
    found = [Finding(IO, launchers_rel, 1, f"bin/{n} が起動スクリプトの表（launchers）に無い") for n in model.bin_files if n not in rows]
    found += [
        Finding(IO, launchers_rel, 1, f"起動スクリプトの表の行 {n} のファイルが bin/ に無い") for n in sorted(rows - set(model.bin_files))
    ]
    return found


# --- spec-skill ---


def _words(line: str) -> list[str]:
    plain = line.replace("`", " ")
    try:
        return shlex.split(plain)
    except ValueError:
        return plain.split()


def _call_at(words: list[str], i: int) -> list[str] | None:
    """`words[i]` が呼び出しの先頭なら、区切りの語までの後ろの語。副命令の形でなければ `None`。"""
    if words[i] != ENTRY and not words[i].endswith("/bin/" + ENTRY):
        return None
    rest: list[str] = []
    for w in words[i + 1 :]:
        if w in CALL_STOPS:
            break
        rest.append(w)
    head = rest[0] if rest else ""
    return rest if head.isascii() and head[:1].isalpha() and head.replace("-", "").isalnum() else None


def written_calls(text: str) -> list[tuple[int, list[str]]]:
    """本文の行から `ndf`（か末尾が `/bin/ndf` の語）で始まる呼び出しを探し、（行, 後ろの語の並び）を返す。"""
    out = []
    for number, line in enumerate(text.splitlines(), 1):
        words = _words(line)
        out += [(number, call) for i in range(len(words)) if (call := _call_at(words, i)) is not None]
    return out


def _options(tree: ArgTree) -> dict[str, Arg]:
    """オプションの名前（別名を含む）から引数への対応。"""
    return {n: a for a in tree.options if not a.positional for n in (a.name, *a.aliases)}


def _flags(tree: ArgTree) -> set[str]:
    """呼び出しに書いてよいオプションの名前（引数の名前・別名と、argparse が足す `-h` / `--help`）。"""
    return set(_options(tree)) | set(tree.help_flags)


def _is_placeholder(word: str) -> bool:
    return word.startswith(("<", "$", "{", "[")) or word in ("...", "…")


def call_errors(words: list[str], commands: dict[str, CommandSpec]) -> list[str]:
    """呼び出しの語を副命令・下位の命令・引数の名前で引数の木に当て、当たらない語を返す（値の型は見ない）。

    オプションの値の語は、そのオプションが取る数（`Arg.takes`）だけ読み飛ばしてから下位の命令を辿る。
    残りの語をすべて取る位置引数（`Arg.remainder`）に達するか、それを持つ命令で `--` に達したら、
    それより後ろの語は照合しない（argparse がその位置引数へ渡す語で、引数の名前ではない）。
    """
    name = words[0]
    if name not in commands:
        return [f"副命令 {name} が無い"]
    tree = commands[name].args
    if tree is None:
        return []
    allowed = _options(tree)
    flags = _flags(tree)
    errs = []
    rest = words[1:]
    seen = 0
    while rest:
        word, rest = rest[0], rest[1:]
        if _is_placeholder(word):
            continue
        if word == "--" and _remainder_at(tree, None):
            break
        if word.startswith("-"):
            errs += _flag_error(word, flags)
            rest = _skip_values(word, allowed, rest)
            continue
        if _remainder_at(tree, seen):
            break
        sub = _descend(tree, word)
        if sub is None:
            errs.append(f"下位の命令 {word} が無い")
            break
        seen = seen + 1 if sub is tree else 0
        tree = sub
        allowed |= _options(tree)
        flags |= _flags(tree)
    return errs


def _remainder_at(tree: ArgTree, seen: int | None) -> bool:
    """`seen` 個の位置の語を読んだ次の位置引数が残りの語をすべて取るか（`None` はどの位置でも）。下位の命令を持つ木は偽。"""
    if tree.subcommands:
        return False
    positionals = [a for a in tree.options if a.positional]
    return any(a.remainder for a in (positionals if seen is None else positionals[seen : seen + 1]))


def _flag_error(word: str, flags: set[str]) -> list[str]:
    """オプションの語（`--名前` か `--名前=値`）の名前が、ここまでの命令で書いてよい名前に無ければその指摘。"""
    flag = word.split("=", 1)[0]
    return [] if flag in flags else [f"引数 {flag} が無い"]


def _skip_values(word: str, allowed: dict[str, Arg], rest: list[str]) -> list[str]:
    """オプションの語の後ろから、そのオプションの値の語を除いた残り。`--名前=値` と知らない名前は値を取らない。"""
    if "=" in word or word not in allowed:
        return rest
    takes = allowed[word].takes
    if takes is None:
        takes = next((i for i, w in enumerate(rest) if w.startswith("-")), len(rest))
    return rest[takes:]


def _descend(tree: ArgTree, word: str) -> ArgTree | None:
    """位置の語 `word` で下位の命令を辿る。下位の命令が無い木はそのまま（語は位置引数）、下位の命令に無い語は `None`。"""
    if not tree.subcommands:
        return tree
    return tree.subcommands[word] if word in tree.subcommands else None


def _row_errors(model: SpecModel) -> list[str]:
    decls = model.file_ids
    env = model.env_ids
    names = model.skill_names
    errs = [f"Skill {s.name} が Skill の入出力の表（skill-io）に行を持たない" for s in model.skills if s.present and not s.row]
    errs += [f"公開する Skill {s.name} の行が args か env を持つ（正本はエントリポイントの表）" for s in model.skills if s.row_has_args_env]
    errs += [f"Skill の入出力の表の行 {i} の Skill に実体が無い" for i in model.skill_io_ids if i not in names]
    for s in model.skills:
        errs += [f"Skill {s.name} の読む宣言 {d} が置き場の表に無い" for d in s.declarations if d not in decls]
        errs += [f"Skill {s.name} の読む環境変数 {e} が環境変数の表に無い" for e in s.env if e not in env]
    return errs


def skill_findings(model: SpecModel, scope: Scope, rel: Rel) -> list[Finding]:
    table_rel = _table_rel(rel, "skill-io")
    found = [Finding(SKILL, table_rel, 1, e) for e in _row_errors(model)]
    commands = {c.name: c for c in model.commands}
    for src in scope.sources:
        if src.path.suffix != ".md" or not scope.plugin_rel(src).startswith(CALL_DIRS):
            continue
        for line, words in written_calls(src.text):
            found += [Finding(SKILL, src.rel, line, f"ndf {' '.join(words)}: {e}") for e in call_errors(words, commands)]
    return found


# --- spec-public ---


def _hits(model: SpecModel, row: dict[str, Any]) -> bool:
    """公開する面の行が規約の項目に当たるか。"""
    kind, target = row["kind"], row["target"]
    if kind == "file":
        return target in model.places and set(opt(row, "files")) <= model.file_ids
    face = spec_model.face_of(kind)
    # 面ごとの項目の名前（report は規約の項目を持たず、行があれば当たる）
    names = {
        "skill": model.skill_names,
        "command": {c.name for c in model.commands},
        "hook": {h.event for h in model.hooks},
    }
    if face in names:
        return target in names[face]
    return face == "report"


def public_findings(model: SpecModel, plugin_root: Path, rel: Rel) -> list[Finding]:
    ep_rel = _table_rel(rel, "entrypoints")
    found = []
    env = model.env_ids
    for row in model.entrypoints:
        found += [
            Finding(PUBLIC, ep_rel, 1, f"公開する面の行 {row['id']} の環境変数 {e} が環境変数の表に無い")
            for e in row["env"]
            if e not in env
        ]
        if spec_model.is_spec(row) or row["order"] == spec_model.CALLER_FIRST:
            continue
        if not _hits(model, row):
            found.append(Finding(PUBLIC, ep_rel, 1, f"公開する面の行 {row['id']} が規約のどの項目にも当たらない"))
    return found + _spec_place_findings(model, plugin_root, ep_rel)


def _spec_place_findings(model: SpecModel, plugin_root: Path, ep_rel: str) -> list[Finding]:
    """種類 spec の行が規約の置き場ごとにちょうど 1 行あり、どの行も実在の規約の置き場を指すか。"""
    found = []
    spec = [r for r in model.entrypoints if spec_model.is_spec(r)]
    for target in SPEC_TARGETS:
        n = sum(1 for r in spec if r["target"] == target)
        if n != 1:
            found.append(Finding(PUBLIC, ep_rel, 1, f"規約の置き場 {target} を指す種類 spec の行が {n} 行（1 行にする）"))
    for r in spec:
        if r["target"] not in SPEC_TARGETS or not (plugin_root / r["target"]).is_dir():
            found.append(
                Finding(PUBLIC, ep_rel, 1, f"種類 spec の行 {r['id']} の置き場 {r['target']} が規約の置き場でないか、ディレクトリが無い")
            )
    return found


# --- spec-docs ---


def _generated(plugin_root: Path, patterns: list[str]) -> set[str]:
    return {p.relative_to(plugin_root).as_posix() for pat in patterns for p in plugin_root.glob(pat) if p.is_file()}


def _stale(rendered: dict[str, str], plugin_root: Path, patterns: list[str]) -> list[str]:
    """描画に無いのに生成物の置き場に残るファイル（プラグインの根からの相対パスの昇順）。"""
    return sorted(_generated(plugin_root, patterns) - set(rendered))


def _first_diff(want: str, have: str) -> int:
    for i, (a, b) in enumerate(zip(want.splitlines(), have.splitlines()), 1):
        if a != b:
            return i
    return min(len(want.splitlines()), len(have.splitlines())) + 1


def docs_findings(rendered: dict[str, str], plugin_root: Path, patterns: list[str], rel: Rel) -> list[Finding]:
    """描画した中身とコミットされた生成物の違い（無い・違う・読めない）と、描画に無い生成物の残り。"""
    hint = "。ndf check --write-spec で生成し直す"
    found = []
    for path in sorted(rendered):
        target = plugin_root / path
        if not target.is_file():
            found.append(Finding(DOCS, rel(path), 1, "生成物が無い" + hint))
            continue
        try:
            have = target.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            found.append(Finding(DOCS, rel(path), 1, f"生成物を読めない: {exc}{hint}"))
            continue
        if have != rendered[path]:
            found.append(Finding(DOCS, rel(path), _first_diff(rendered[path], have), "生成し直した中身と違う" + hint))
    found += [
        Finding(DOCS, rel(p), 1, "生成されないファイルが生成物の置き場に残る" + hint) for p in _stale(rendered, plugin_root, patterns)
    ]
    return found


def write(rendered: dict[str, str], plugin_root: Path, patterns: list[str]) -> list[str]:
    """描画した中身を書き、描画に無い生成物を消す。中身の同じファイルは書かない。書いた・消したパスを返す。"""
    done = []
    try:
        for path in sorted(rendered):
            target = plugin_root / path
            if target.is_file() and target.read_text(encoding="utf-8") == rendered[path]:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(rendered[path], encoding="utf-8")
            done.append(path)
        for path in _stale(rendered, plugin_root, patterns):
            (plugin_root / path).unlink()
            done.append(path)
    except OSError as exc:
        raise CheckError(f"生成物を書けない: {exc}") from exc
    return done
