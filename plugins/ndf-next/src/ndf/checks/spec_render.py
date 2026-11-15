"""規約の描画: 規約のモデルからコントラクトのページ（`docs/contracts/` の 8 ページ）と引数のスキーマの中身を作る（#3518）。

純粋な処理で、ファイルを触らない。同じモデルから 1 字も違わない文字列を返す。行の並びは id の昇順で、日時・ホスト名・
プラグインの名前を入れない（入れ替えの後も同じ中身になる）。
"""

from __future__ import annotations

import json
from typing import Any

from ndf import identity
from ndf.checks.spec_args import Arg, ArgTree
from ndf.checks.spec_model import (
    CALLER_FIRST,
    CONTRACTS,
    DECL_LOCATIONS,
    DECL_SCHEMA_DIR,
    INTERNAL,
    IO_SCHEMA_DIR,
    PAGES_DIR,
    PUBLIC,
    SCHEMA_SUFFIX,
    SECTIONS_SUFFIX,
    CommandSpec,
    NdfSettingsSpec,
    SkillSpec,
    SpecModel,
    decl_schema_rel,
    io_schema_rel,
    is_spec,
    opt,
    surface_of,
)

# エントリポイントの短い名前と設定の保存先（`identity` の固定の語）
ENTRY = identity.FIXED_WORDS["entrypoint"]
DECL_DIR = identity.FIXED_WORDS["ndf_settings_dir"]
JSON_SCHEMA = "https://json-schema.org/draft/2020-12/schema"
# 範囲の語（保存先の表の列 tracked から導く）
SCOPE_WORDS = {True: "チーム", False: "個人"}
# 公開か内部かの語
SURFACE_WORDS = {PUBLIC: "公開", INTERNAL: "内部（予告なく変えてよい）"}
GENERATED = "<!-- ndf check --write-spec が生成する。手で直さない（SSOT は各節の出典） -->\n"
NONE = "—"
MAIN_MARK = "（メインディレクトリから読む）"
# 設定のスキーマの注釈 x-ndf-location の値（spec_model.DECL_LOCATIONS の並び）→ 保存先の語
LOCATION_LABELS = dict(zip(DECL_LOCATIONS, ("メインディレクトリ（既定ブランチの worktree）", "worktree"), strict=True))
DEPRECATED_MARK = "（非推奨。読み手は読まない）"
# ページの名前 → 見出し
PAGES = {
    "index.md": "設定と入出力の規約",
    "ndf-settings.md": f"設定（{DECL_DIR}/）",
    "commands.md": "サブコマンドの引数と結果",
    "exit-codes.md": "終了コードと停止理由",
    "env.md": "環境変数",
    "files.md": "受け渡しのファイル",
    "hooks.md": "hook の入出力",
    "skills.md": "Skill と起動スクリプト",
}


def md_table(header: list[str], rows: list[list[str]]) -> str:
    """Markdown の表。セルの `|` と改行を逃がす。"""

    def cell(text: str) -> str:
        return text.replace("|", "\\|").replace("\n", " ") if text else NONE

    lines = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    lines += ["| " + " | ".join(cell(c) for c in row) + " |" for row in rows]
    return "\n".join(lines) + "\n"


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2) + "\n"


def code(text: str) -> str:
    return f"`{text}`" if text else NONE


def _page(name: str, intro: str, body: list[str]) -> str:
    return f"# {PAGES[name]}\n\n{GENERATED}\n{intro}\n\n" + "\n".join(body)


def render(model: SpecModel) -> dict[str, str]:
    """コントラクトのページ 8 つとサブコマンドごとの引数のスキーマを、プラグインの根からの相対パス → 中身で返す。"""
    pages = {
        "index.md": page_index(model),
        "ndf-settings.md": page_ndf_settings(model),
        "commands.md": page_commands(model),
        "exit-codes.md": page_exit_codes(model),
        "env.md": page_env(model),
        "files.md": page_files(model),
        "hooks.md": page_hooks(model),
        "skills.md": page_skills(model),
    }
    out = {f"{PAGES_DIR}/{name}": text for name, text in pages.items()}
    for command in model.commands:
        if command.args is not None:
            out[args_rel(command.name)] = dumps(args_schema(command))
    return out


def args_rel(name: str) -> str:
    return f"{CONTRACTS}/{IO_SCHEMA_DIR}/{io_schema_rel(name, 'args')}"


def result_rel(name: str) -> str:
    return f"{CONTRACTS}/{IO_SCHEMA_DIR}/{io_schema_rel(name, 'result')}"


# --- 引数のスキーマ ---


def _arg_schema(arg: Arg) -> dict[str, Any]:
    out: dict[str, Any] = {"type": arg.type}
    if arg.items is not None:
        values = {"type": arg.items} | ({"enum": list(arg.choices)} if arg.choices else {})
        out["items"] = {"type": "array", "items": values} if arg.repeated else values
    elif arg.choices:
        out["enum"] = list(arg.choices)
    if arg.default is not None:
        out["default"] = arg.default
    if arg.help:
        out["description"] = arg.help
    if arg.aliases:
        out["x-ndf-aliases"] = list(arg.aliases)
    if arg.positional:
        out["x-ndf-positional"] = True
    return out


def leaves(tree: ArgTree, path: tuple[str, ...] = (), inherited: tuple[Arg, ...] = ()) -> list[tuple[tuple[str, ...], tuple[Arg, ...]]]:
    """下位の命令の道ごとに、その道で使える引数（上の命令の引数を含む）を並べる。下位の命令の無い木は道 1 つ。"""
    here = inherited + tree.options
    if not tree.subcommands:
        return [(path, here)]
    out: list[tuple[tuple[str, ...], tuple[Arg, ...]]] = []
    for name, sub in tree.subcommands.items():
        out += leaves(sub, (*path, name), here)
    return out


def args_schema(command: CommandSpec) -> dict[str, Any]:
    """引数の木を、下位の命令の道ごとの `oneOf` の枝を持つ JSON Schema にする。"""
    assert command.args is not None
    branches = []
    for path, args in leaves(command.args):
        props: dict[str, Any] = {}
        if path:
            props["subcommand"] = {"const": " ".join(path), "description": "下位の命令（入れ子は空白でつなぐ）"}
        props |= {a.name: _arg_schema(a) for a in args}
        required = (["subcommand"] if path else []) + [a.name for a in args if a.required]
        branch = {"type": "object", "additionalProperties": False, "properties": props}
        if required:
            branch["required"] = required
        branches.append(branch)
    return {
        "$schema": JSON_SCHEMA,
        "$id": f"{IO_SCHEMA_DIR}/{io_schema_rel(command.name, 'args')}",
        "title": f"ndf {command.name} の引数",
        "description": f"ndf {command.name} が受ける引数。キーは引数の名前（オプションは -- の名前、位置引数は名前）。規約の生成が作り、手で直さない",
        "oneOf": branches,
    }


# --- 型の語 ---


def type_word(schema: Any) -> str:
    """スキーマの型を短い語にする（`string`・`array of string`・`Suite` など）。"""
    if not isinstance(schema, dict):
        return NONE
    if "$ref" in schema:
        return schema["$ref"].rsplit("/", 1)[-1]
    for key in ("anyOf", "oneOf"):
        if key in schema:
            return " か ".join(dict.fromkeys(type_word(s) for s in schema[key]))
    if "const" in schema:
        return f"const {json.dumps(schema['const'], ensure_ascii=False)}"
    kind = schema.get("type")
    if isinstance(kind, list):
        return " か ".join(kind)
    if kind == "array" and "items" in schema:
        return f"array of {type_word(schema['items'])}"
    return kind if isinstance(kind, str) else NONE


def _value(value: Any) -> str:
    return code(json.dumps(value, ensure_ascii=False)) if value is not None else NONE


def _enum(schema: dict[str, Any]) -> str:
    values = schema.get("enum")
    if values is None and isinstance(schema.get("items"), dict):
        values = schema["items"].get("enum")
    return "・".join(code(json.dumps(v, ensure_ascii=False)) for v in values) if values else NONE


# --- ページ ---


def _count(items: list[str]) -> str:
    public = sum(1 for s in items if s == PUBLIC)
    return f"公開 {public}・内部 {len(items) - public}"


def page_index(model: SpecModel) -> str:
    """面の一覧と、公開と内部の約束。"""
    counts = {
        "ndf-settings.md": _count([d.surface for d in model.ndf_settings]),
        "commands.md": _count([c.surface for c in model.commands]),
        "exit-codes.md": f"終了コード {len(model.exit_codes)}・停止理由 {len(model.causes)}",
        "env.md": _count([surface_of(model.public, "env", r["id"]) for r in model.env]),
        "files.md": _count([surface_of(model.public, "file", r["id"]) for r in model.files]),
        "hooks.md": _count([h.surface for h in model.hooks]),
        "skills.md": _count([s.surface for s in model.skills]),
    }
    rows = [[f"[{PAGES[name]}]({name})", text] for name, text in counts.items()]
    spec = [[code(r["id"]), code(r["target"]), "・".join(code(f) for f in opt(r, "files"))] for r in model.entrypoints if is_spec(r)]
    caller = [[code(r["id"]), r["kind"], code(r["target"]), r["source"]] for r in model.entrypoints if r["order"] == CALLER_FIRST]
    intro = (
        "NDF を外から呼ぶ面（設定・サブコマンド・環境変数・受け渡しのファイル・hook・Skill・起動スクリプト）の規約である。"
        "機械が読む形は JSON Schema（`contracts/schemas/ndf-settings/`・`contracts/schemas/io/`）で、このページの表はそれと SSOT の表から生成する。\n\n"
        "**公開**の項目は外の呼び手（連携システム）が頼ってよく、名前と意味をバージョンで約束する。"
        "**内部**の項目は NDF の中の Skill・エージェント・AI が使い、予告なく変えてよい。"
        "公開か内部かは、公開する面の表（`contracts/domains/entrypoints.json` と、`contracts/domains/ndf-files.json` の列 `reader`）に当たるかで決まる"
    )
    body = [
        "## ページ\n",
        md_table(["ページ", "件数"], rows),
        "## 規約の保存先（公開する面の表の種類 spec）\n",
        md_table(["行", "保存先", "公開する設定"], spec),
        "## 呼び手と合わせる前の行\n",
        "公開する面の表で順番が `caller-first` の行は、入れ替えの前に呼び手の側を新しいエントリポイントへ直す行で、規約の項目にまだ当たらなくてよい。\n",
        md_table(["行", "種類", "名前", "出所"], caller),
    ]
    return _page("index.md", intro, body)


def _location_word(decl: NdfSettingsSpec) -> str:
    """保存先（スキーマの注釈 `x-ndf-location`）。`main_keys` のキーは worktree の設定でもメインディレクトリから読む。"""
    location = opt(decl.schema, "x-ndf-location", None) if decl.schema is not None else None
    label = LOCATION_LABELS[location] if location in LOCATION_LABELS else NONE
    return f"{label}（抑制コメントのキーはメインディレクトリ）" if decl.main_keys else label


def _read_by(rows: tuple[dict[str, Any], ...]) -> str:
    return "・".join(f"{code(r['by'])}（{r['when']}）" for r in rows) if rows else NONE


def _key_rows(decl: NdfSettingsSpec) -> list[list[str]]:
    assert decl.schema is not None
    props = opt(decl.schema, "properties", {})
    required = set(opt(decl.schema, "required"))
    rows = []
    for key in sorted(props):
        p = props[key]
        mark = (MAIN_MARK if key in decl.main_keys else "") + (DEPRECATED_MARK if p.get("deprecated") else "")
        rows.append(
            [
                code(key) + mark,
                type_word(p),
                _value(p.get("default")),
                _enum(p),
                "必須" if key in required else "任意",
                opt(p, "description", ""),
            ]
        )
    return rows


def _ndf_settings(decl: NdfSettingsSpec) -> list[str]:
    meta = f"{_location_word(decl)}・{SCOPE_WORDS[decl.tracked]}・{SURFACE_WORDS[decl.surface]}・{_schema_link(decl)}。読む工程: {_read_by(decl.read_by)}\n"
    head = [f"## `{DECL_DIR}/{decl.path}`（{decl.id}）\n", meta]
    if decl.sections is not None:
        rows = [
            [str(r["order"]), "#" * r["level"] + " " + r["heading"], "必須" if r["required"] else "任意", r["meaning"]]
            for r in decl.sections
        ]
        return [*head, "Markdown の設定。節の並び:\n", md_table(["順", "見出し", "必須か", "持つもの"], rows)]
    if decl.schema is None:
        return [*head, "スキーマが無い（`ndf check` の spec-decl が落とす）。\n"]
    examples = "".join(f"例: `{json.dumps(ex, ensure_ascii=False)}`\n" for ex in opt(decl.schema, "examples"))
    keys = md_table(["キー", "型", "既定値", "取りうる値", "必須か", "説明"], _key_rows(decl))
    return [*head, opt(decl.schema, "description", "") + "\n", keys, examples]


def _schema_link(decl: NdfSettingsSpec) -> str:
    name = decl_schema_rel(decl.id, SECTIONS_SUFFIX if decl.sections is not None else SCHEMA_SUFFIX)
    return f"[{name}](../../{CONTRACTS}/{DECL_SCHEMA_DIR}/{name})"


def page_ndf_settings(model: SpecModel) -> str:
    """設定のファイルごとの保存先・範囲・読む工程と、キーごとの型・既定値・取りうる値・必須か・説明・例。"""
    intro = (
        f"リポジトリの `{DECL_DIR}/` に置く設定（人が決める設定のファイル）である。SSOT は保存先の表（`contracts/domains/ndf-files.json` の種類 "
        "`ndf_settings` の行）と設定のスキーマで、読み手は表の `keys` に無いキーを読まない。範囲のチームは git が追跡するファイル、個人は追跡しないファイルである"
    )
    body: list[str] = []
    for decl in model.ndf_settings:
        body += _ndf_settings(decl)
    return _page("ndf-settings.md", intro, body)


def _arg_text(arg: Arg) -> str:
    """引数 1 つを 1 行の文にする（名前・型・必須か・既定値・取りうる値・説明）。細部は引数のスキーマが持つ。"""
    name = arg.name if not arg.aliases else f"{arg.name}（{'・'.join(arg.aliases)}）"
    kind = arg.type if arg.items is None else f"array of {'array of ' if arg.repeated else ''}{arg.items}"
    parts = [kind, "必須" if arg.required else "任意"] + (["位置"] if arg.positional else [])
    if arg.default is not None:
        parts.append(f"既定 {_value(arg.default)}")
    if arg.choices:
        parts.append("値 " + "・".join(code(c) for c in arg.choices))
    return f"{code(name)}（{'・'.join(parts)}）" + (f": {arg.help}" if arg.help else "")


def _props_text(schema: Any) -> str:
    if not isinstance(schema, dict) or not opt(schema, "properties", {}):
        return "決まった項目を持たない"
    return "・".join(f"{code(k)}（{type_word(v)}）" for k, v in sorted(opt(schema, "properties", {}).items()))


def _command(command: CommandSpec) -> list[str]:
    links = f"[引数のスキーマ](../../{args_rel(command.name)})・[結果のスキーマ](../../{result_rel(command.name)})"
    out = [f"## `{ENTRY} {command.name}`\n", f"{SURFACE_WORDS[command.surface]}。{links}\n"]
    if command.args is None:
        return [*out, "引数を取り出せない（`ndf check` の spec-io が落とす）。\n"]
    rows = [
        [code(" ".join((ENTRY, command.name, *path))), "<br>".join(_arg_text(a) for a in args) if args else "引数を受けない"]
        for path, args in leaves(command.args)
    ]
    props = opt(command.result_schema, "properties", {}) if command.result_schema is not None else {}
    result = f"結果の `metrics`: {_props_text(opt(props, 'metrics', None))}。`items` の要素: {_props_text(opt(opt(props, 'items', {}), 'items', None))}\n"
    return [*out, md_table(["呼び方", "引数"], rows), result]


def page_commands(model: SpecModel) -> str:
    """サブコマンドと下位の命令ごとの引数の表と、結果の `metrics`・`items` の形。"""
    intro = (
        "`ndf <サブコマンド>` の引数と結果である。どのサブコマンドも結果 JSON（`contracts/schemas/result.schema.json`）を標準出力の最後の 1 行に出す。"
        "SSOT はサブコマンドの表（`src/ndf/cli.py` の `COMMANDS`）・各サブコマンドの引数の定義・手書きの引数の表（`contracts/domains/manual-args.json`）・結果のスキーマ。"
        "終了コードと停止理由は [終了コードと停止理由](exit-codes.md) にある"
    )
    body: list[str] = []
    for command in model.commands:
        body += _command(command)
    return _page("commands.md", intro, body)


def page_exit_codes(model: SpecModel) -> str:
    """終了コードの表と停止理由の表の全行。"""
    codes = {r["id"]: r for r in model.exit_codes}
    ranges = [
        [code(r["id"]), str(r["code"]) + (f"〜{r['last']}" if "last" in r else ""), r["status"], r["label"]] for r in model.exit_codes
    ]
    causes = [
        [
            code(c["kind"]),
            c["meaning"],
            c["std_type"],
            f"{codes[c['exit']]['code']}（{c['exit']}）" if c["exit"] in codes else c["exit"],
            c["route"],
            c["retry"] if c.get("retry") else NONE,
            c["exhausted"] if c.get("exhausted") else NONE,
        ]
        for c in model.causes
    ]
    intro = (
        "結果 JSON の `status` と終了コードの区分、`stopped` の `cause.kind`（停止理由）である。SSOT は `contracts/facts/exit-codes.json` と `contracts/causes.json`。"
        "ルートは `cause.kind` だけで選び、終了コードは `status` の区分を表すだけである"
    )
    body = [
        "## 終了コード\n",
        md_table(["区分", "終了コード", "status", "見出し"], ranges),
        "## 停止理由\n",
        md_table(["種類", "意味", "標準の型", "終了コード", "ルート", "再試行の方針", "尽きたとき"], causes),
    ]
    return _page("exit-codes.md", intro, body)


def page_env(model: SpecModel) -> str:
    """環境変数の表の全行。値が秘密の行も名前と意味だけで、値の例の列を持たない。"""
    rows = [
        [
            code(r["id"]),
            r["set_by"],
            r["type"],
            "秘密" if r["secret"] else NONE,
            r["meaning"],
            SURFACE_WORDS[surface_of(model.public, "env", r["id"])],
        ]
        for r in model.env
    ]
    intro = (
        "NDF が読む環境変数と、NDF が子のプロセスへ渡す環境変数である。SSOT は `contracts/domains/env.json`。"
        "公開は、公開する面の表のエントリポイントの `env` に載る変数である。値が秘密の変数は値をログ・結果・記録へ出さない"
    )
    return _page("env.md", intro, [md_table(["名前", "設定する者", "型", "秘密か", "意味", "公開か"], rows)])


def _location(model: SpecModel, location: str) -> str:
    row = model.locations.get(location)
    if row is None:
        return code(location)
    where = f"{row['root']}:{row['path']}" + (f"（{row['env']} で差し替え）" if "env" in row else "")
    return f"{code(location)} {where}"


def page_files(model: SpecModel) -> str:
    """保存先の表の全行と、公開する行の項目（列 `fields`）。"""
    rows = [
        [
            code(r["id"]),
            _location(model, r["location"]),
            code(r["path"]),
            r["kind"],
            "追跡" if r["tracked"] else NONE,
            r["writer"],
            r["ends"],
            SURFACE_WORDS[surface_of(model.public, "file", r["id"])],
        ]
        for r in model.files
    ]
    public = [
        [code(r["id"]), opt(r, "reader", NONE), "・".join(code(f) for f in opt(r, "fields"))]
        for r in model.files
        if surface_of(model.public, "file", r["id"]) == PUBLIC
    ]
    intro = (
        "NDF の工程が書くファイル（設定・計測値・実行の記録・文書・受け渡し）である。SSOT は `contracts/domains/ndf-files.json` と保存先の表 `contracts/domains/locations.json`。"
        "形（項目）を約束するのは公開する行だけで、内部の行は保存先と種類だけを載せる"
    )
    body = [
        "## 一覧\n",
        md_table(["行", "保存先", "パス", "種類", "追跡", "書き手", "消す時点", "公開か"], rows),
        "## 公開する行の項目\n",
        "書き手は項目の名前と意味を変えない。ほかの項目は足してよく、読み手は知らない項目を受け流す。\n",
        md_table(["行", "読む外の呼び手", "項目"], public),
    ]
    return _page("files.md", intro, body)


def page_hooks(model: SpecModel) -> str:
    """ランタイムと事象ごとの、読む入力のキーと判定ごとの出力の形。"""
    intro = (
        "Claude Code・Codex から呼ばれる hook の入出力である。SSOT は `contracts/hooks/runtimes.json`。"
        "出力の形の `{reason}` と `{context}` は判定の文に置き換わり、形の無い判定は何も出さない"
    )
    body: list[str] = []
    for hook in model.hooks:
        body.append(f"## {hook.runtime}: `{hook.event}`\n")
        body.append(
            md_table(
                ["読む入力のキー", "無くてよいキー", "公開か"],
                [["・".join(code(k) for k in hook.reads), "・".join(code(k) for k in hook.optional), SURFACE_WORDS[hook.surface]]],
            )
        )
        for verdict in sorted(hook.outputs):
            body.append(f"判定 `{verdict}` の出力:\n\n```json\n{dumps(hook.outputs[verdict])}```\n")
        if not hook.outputs:
            body.append("出力を持たない。\n")
    return _page("hooks.md", intro, body)


def _skill_args_text(skill: SkillSpec) -> str:
    """引数の形の並び。空の並びは引数を受けない、空文字の形は引数なしで呼べる。"""
    if not skill.args:
        return "引数を受けない"
    return "<br>".join(code(a) if a else "（引数なし）" for a in skill.args)


def _skill_row(skill: SkillSpec) -> list[str]:
    return [
        code(skill.name) + ("" if skill.present else "（skills/ へ移す前）"),
        SURFACE_WORDS[skill.surface],
        _skill_args_text(skill),
        skill.goal_end if skill.goal_end else NONE,
        "・".join(code(d) for d in skill.ndf_settings),
        "・".join(code(e) for e in skill.env),
        skill.result if skill.result else NONE,
        skill.meaning,
    ]


def page_skills(model: SpecModel) -> str:
    """Skill ごとの入出力と、起動スクリプトの表の launcher の行。"""
    rows = [_skill_row(s) for s in model.skills]
    reports = [
        [code(r["target"]), "・".join(code(a) for a in opt(r, "args")), "・".join(code(e) for e in r["env"])]
        for r in model.entrypoints
        if r["kind"] == "report"
    ]
    launchers = [[code(r["id"]), r["passes_to"], r["meaning"]] for r in model.launchers if r["kind"] == "launcher"]
    intro = (
        "配る Skill（`manifests/skills.json` の行で `skills/<名前>/SKILL.md` があるもの）と、公開する面に載る Skill の入出力である。"
        "SSOT は公開する Skill の引数と環境変数がエントリポイントの表、ほかは `contracts/domains/skill-io.json`。引数の形は正規表現で、どれか 1 つに全体が一致する"
    )
    body = [
        "## Skill\n",
        md_table(["Skill", "公開か", "引数の形", "/goal の終わりの条件", "読む設定", "読む環境変数", "結果", "意味"], rows),
        "## 応答の末尾の報告\n",
        md_table(["結果の行の項目", "値", "環境変数"], reports),
        "## 起動スクリプト\n",
        "利用者と AI が打つ `bin/` のスクリプトである（SSOT は `contracts/domains/launchers.json`）。`ndf` のサブコマンドは [サブコマンドの引数と結果](commands.md) にある。\n",
        md_table(["名前", "渡し先", "意味"], launchers),
    ]
    return _page("skills.md", intro, body)
