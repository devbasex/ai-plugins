"""規約のモデル: SSOT の表・スキーマ・引数の木・配る Skill の表・`bin/` を読み、面ごとの項目と公開か内部かを組む（#3518）。

ファイルを読むだけで書かない。読む SSOT は次のとおりで、値を写さない。

| 面 | SSOT |
| --- | --- |
| 設定 | 保存先の表（`ndf-files` の `kind` が `ndf_settings`・`location` が `ndf` の行）と設定のスキーマ（`schemas/ndf-settings/`） |
| サブコマンド | サブコマンドの表（`cli.COMMANDS`）・引数の定義（`spec_args`）・手書きの引数の表（`manual-args`）・結果のスキーマ（`schemas/io/`） |
| 環境変数・受け渡しのファイル・終了コード・停止理由 | `env`・`ndf-files`・`exit-codes`・`causes.json` |
| hook | hook の表（`hooks/runtimes.json`） |
| Skill・起動スクリプト | 配る Skill の表（`manifests/skills.json`）・Skill の入出力の表（`skill-io`）・起動スクリプトの表（`launchers`） |

公開か内部かは、公開する面の表（エントリポイントの表と、保存先の表の列 `reader` を持つ行。#3518 の決定 8）に当たるかで決める。
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError

from ndf import cli, contracts, identity
from ndf.checks.model import CONTRACTS, CheckError, read_json
from ndf.checks.spec_args import ArgTree, extract, from_manual
from ndf.contracts import exit_codes, facts

DECL_KIND = "ndf_settings"
# コントラクトの保存先からの相対パス
DECL_SCHEMA_DIR = f"{contracts.SCHEMAS}/ndf-settings"
IO_SCHEMA_DIR = f"{contracts.SCHEMAS}/io"
HOOKS_TABLE = "hooks/runtimes.json"
# プラグインの根からの相対パス
PAGES_DIR = "docs/contracts"
BIN_DIR = "bin"
SCHEMA_SUFFIX = ".schema.json"
SECTIONS_SUFFIX = ".sections.json"
# 結果のスキーマが結果のコントラクトを指す $ref（検証は結果のコントラクトと、この $ref を除いたサブコマンドの形の 2 つで行う）
RESULT_REF = "../result.schema.json"
# 公開か内部か
PUBLIC = "public"
INTERNAL = "internal"
# エントリポイントの表の順番（種類 spec は `is_spec`）
CALLER_FIRST = "caller-first"
# 設定のスキーマの注釈 x-ndf-location が取る値（設定を読む保存先）
DECL_LOCATIONS = ("main", "worktree")
# 公開する項目の集合（面・id の組）
PublicIds = frozenset[tuple[str, str]]
# SSOT が読めないときの指摘の前置き
UNREADABLE = "規約の SSOT を読めない"


def io_schema_rel(name: str, kind: str) -> str:
    """サブコマンド `name` の引数（`kind` が `args`）か結果（`result`）のスキーマの、`IO_SCHEMA_DIR` からの相対パス。"""
    return f"{name}.{kind}{SCHEMA_SUFFIX}"


def decl_schema_rel(ident: str, suffix: str) -> str:
    """設定 `ident` のスキーマ（`SCHEMA_SUFFIX`）かセクションの表（`SECTIONS_SUFFIX`）の、`DECL_SCHEMA_DIR` からの相対パス。"""
    return f"{ident}{suffix}"


def decl_location(locations: dict[str, dict[str, Any]]) -> str:
    """設定の保存先（リポジトリの根の `.ndf`）の、保存先の表の id。"""
    found = [i for i, r in locations.items() if r["root"] == "repo" and r["path"] == identity.FIXED_WORDS["ndf_settings_dir"]]
    if len(found) != 1:
        raise CheckError(f"保存先の表に設定の保存先がちょうど 1 行ない: {found}")
    return found[0]


def is_spec(row: dict[str, Any]) -> bool:
    """エントリポイントの表の行が種類 spec（規約の保存先を指す行）か。

    種類の語を最上位の定数に置くと、テストの保存先の語（`spec`）の集まりと const-fact に数えられる。
    """
    return row["kind"] == "spec"


def face_of(kind: str) -> str | None:
    """エントリポイントの表の種類 → 規約の面。種類 spec と file は面の項目を指さず `None`。

    対応表を最上位の定数に置くと、面の語が別の語彙（作業種別など）の定数と同じ文字列として const-fact に数えられる。
    """
    faces = {"skill": "skill", "block": "skill", "command": "command", "hook": "hook", "report": "report"}
    return faces[kind] if kind in faces else None


def opt(row: dict[str, Any], key: str, empty: Any = ()) -> Any:
    """表の任意の列（`optional`）の値。行が列を持たなければ `empty`（列の設定が欠けてよいとする値）。"""
    return row[key] if key in row else empty


@dataclass(frozen=True)
class NdfSettingsSpec:
    """設定のファイル 1 つ。`schema` は JSON の設定のスキーマ、`sections` は Markdown の設定のセクションの表の行。"""

    id: str
    path: str
    tracked: bool
    keys: tuple[str, ...]
    main_keys: tuple[str, ...]
    surface: str
    schema: dict[str, Any] | None = None
    sections: tuple[dict[str, Any], ...] | None = None
    read_by: tuple[dict[str, Any], ...] = ()


@dataclass(frozen=True)
class CommandSpec:
    """サブコマンド 1 つ。`args` は引数の木（取り出せず手書きの行も無ければ `None`）。"""

    name: str
    module: str
    args: ArgTree | None
    extracted: bool
    manual: bool
    result_schema: dict[str, Any] | None
    surface: str


@dataclass(frozen=True)
class SkillSpec:
    """Skill 1 つ。`present` は `skills/<名前>/SKILL.md` があるか、`row` は Skill の入出力の表に行があるか。"""

    name: str
    surface: str
    present: bool
    row: bool
    args: tuple[str, ...] = ()
    goal_end: str | None = None
    ndf_settings: tuple[str, ...] = ()
    env: tuple[str, ...] = ()
    result: str | None = None
    meaning: str = ""
    row_has_args_env: bool = False


@dataclass(frozen=True)
class HookSpec:
    """hook のランタイムと事象 1 つ。"""

    runtime: str
    event: str
    reads: tuple[str, ...]
    optional: tuple[str, ...]
    outputs: dict[str, Any]
    surface: str


@dataclass(frozen=True)
class SpecModel:
    """規約のモデル。並びはどれも id（名前）の昇順で、同じ入力から同じモデルになる。"""

    ndf_settings: tuple[NdfSettingsSpec, ...]
    commands: tuple[CommandSpec, ...]
    env: tuple[dict[str, Any], ...]
    files: tuple[dict[str, Any], ...]
    locations: dict[str, dict[str, Any]]
    hooks: tuple[HookSpec, ...]
    skills: tuple[SkillSpec, ...]
    launchers: tuple[dict[str, Any], ...]
    bin_files: tuple[str, ...]
    entrypoints: tuple[dict[str, Any], ...]
    exit_codes: tuple[dict[str, Any], ...]
    causes: tuple[dict[str, Any], ...]
    manual_ids: tuple[str, ...]
    skill_io_ids: tuple[str, ...]
    public: PublicIds = field(default_factory=frozenset)

    @property
    def orders(self) -> dict[str, str]:
        """エントリポイントの id → 列 `order`。"""
        return {r["id"]: r["order"] for r in self.entrypoints}

    @property
    def env_ids(self) -> frozenset[str]:
        """環境変数の表の id。"""
        return frozenset(r["id"] for r in self.env)

    @property
    def file_ids(self) -> frozenset[str]:
        """保存先の表の行の id。"""
        return frozenset(r["id"] for r in self.files)

    @property
    def skill_names(self) -> frozenset[str]:
        """規約に載る Skill の名前。"""
        return frozenset(s.name for s in self.skills)


def surface_of(public: PublicIds, face: str, ident: str) -> str:
    return PUBLIC if (face, ident) in public else INTERNAL


@contextmanager
def contracts_at(plugin_root: Path) -> Iterator[None]:
    """コントラクトの保存先を `plugin_root/contracts` へ向け、抜けるときに元へ戻す（読んだ表は捨てて読み直す）。"""
    before = contracts.contracts_dir()
    target = plugin_root / CONTRACTS
    if before.resolve() == target.resolve():
        yield
        return
    contracts.use_dir(target)
    try:
        yield
    finally:
        contracts.use_dir(before)


def load(plugin_root: Path) -> SpecModel:
    """規約のモデルを組む。表・スキーマが読めなければ `CheckError`（`ndf check` が config_broken にする）。"""
    with contracts_at(plugin_root):
        try:
            return _load(plugin_root)
        except contracts.ContractError as exc:
            raise CheckError(f"規約の SSOT を読めない: {exc}") from exc


def _load(plugin_root: Path) -> SpecModel:
    entrypoints = facts.rows("entrypoints")
    files = facts.rows("ndf-files")
    public = public_ids(list(entrypoints), list(files))
    launcher_rows, bin_files = launchers(plugin_root)
    return SpecModel(
        ndf_settings=tuple(ndf_settings(plugin_root, public)),
        commands=tuple(commands(public)),
        env=tuple(sorted(facts.rows("env"), key=lambda r: r["id"])),
        files=tuple(sorted(files, key=lambda r: r["id"])),
        locations={r["id"]: r for r in facts.rows("locations")},
        hooks=tuple(hooks(public)),
        skills=tuple(skills(plugin_root, public, list(entrypoints))),
        launchers=tuple(sorted(launcher_rows, key=lambda r: r["id"])),
        bin_files=tuple(bin_files),
        entrypoints=tuple(sorted(entrypoints, key=lambda r: r["id"])),
        exit_codes=tuple(sorted(facts.rows(exit_codes.TABLE), key=lambda r: r["code"])),
        causes=tuple(sorted(contracts.load_json("causes.json")["causes"], key=lambda r: r["kind"])),
        manual_ids=tuple(sorted(facts.ids("manual-args"))),
        skill_io_ids=tuple(sorted(facts.ids("skill-io"))),
        public=public,
    )


def public_ids(entrypoints: list[dict[str, Any]], files: list[dict[str, Any]]) -> PublicIds:
    """公開する面の表から、公開する項目の組（面・id）を集める。"""
    out: set[tuple[str, str]] = set()
    for row in entrypoints:
        kind = row["kind"]
        out |= {("env", name) for name in row["env"]}
        if is_spec(row):
            out |= {(DECL_KIND, f) for f in opt(row, "files")}
            continue
        out |= {("file", f) for f in opt(row, "files")}
        face = face_of(kind)
        if face is not None:
            out.add((face, row["target"]))
    out |= {("file", r["id"]) for r in files if "reader" in r}
    return frozenset(out)


def _checked_schema(path: Path) -> dict[str, Any]:
    schema = read_json(path, UNREADABLE)
    try:
        Draft202012Validator.check_schema(schema)
    except SchemaError as exc:
        raise CheckError(f"スキーマ {path} がスキーマとして誤り: {exc.message}") from exc
    return schema


def ndf_settings(plugin_root: Path, public: PublicIds) -> list[NdfSettingsSpec]:
    """設定の行ごとに、スキーマ（JSON の設定）かセクションの表（Markdown の設定）を読む。どちらも無ければ両方 `None`。"""
    base = plugin_root / CONTRACTS / DECL_SCHEMA_DIR
    out = []
    location = decl_location({r["id"]: r for r in facts.rows("locations")})
    for row in sorted(facts.rows("ndf-files", kind=DECL_KIND, location=location), key=lambda r: r["id"]):
        schema_path = base / f"{row['id']}{SCHEMA_SUFFIX}"
        sections_path = base / f"{row['id']}{SECTIONS_SUFFIX}"
        schema = _checked_schema(schema_path) if schema_path.is_file() else None
        sections, read_by = None, ()
        if sections_path.is_file():
            sections, read_by = _sections(sections_path)
        elif schema is not None:
            read_by = tuple(opt(schema, "x-ndf-read-by"))
        out.append(
            NdfSettingsSpec(
                id=row["id"],
                path=row["path"],
                tracked=row["tracked"],
                keys=tuple(opt(row, "keys")),
                main_keys=tuple(opt(row, "main_keys")),
                surface=surface_of(public, DECL_KIND, row["id"]),
                schema=schema,
                sections=sections,
                read_by=read_by,
            )
        )
    return out


def _sections(path: Path) -> tuple[tuple[dict[str, Any], ...], tuple[dict[str, Any], ...]]:
    """セクションの表（事実の表の形の行と、注釈 `x-ndf-read-by`）を読む。"""
    raw = read_json(path, UNREADABLE)
    missing = [k for k in ("summary", "owner", "columns", "rows") if k not in raw]
    if missing:
        raise CheckError(f"セクションの表 {path} にキーが無い: {', '.join(missing)}")
    validator = Draft202012Validator(facts.row_schema(raw["columns"]))
    for row in raw["rows"]:
        bad = sorted(e.message for e in validator.iter_errors(row))
        if bad:
            raise CheckError(f"セクションの表 {path} の行 {row.get('id')!r} の形が違う: {'; '.join(bad)}")
    rows = tuple(sorted(raw["rows"], key=lambda r: r["order"]))
    return rows, tuple(opt(raw, "x-ndf-read-by"))


def commands(public: PublicIds) -> list[CommandSpec]:
    """サブコマンドの表のサブコマンドごとに、引数の木と結果のスキーマを付ける。"""
    manual: dict[str, list[dict[str, Any]]] = {}
    for r in facts.rows("manual-args"):
        manual.setdefault(r["command"], []).append(r)
    io_dir = contracts.contracts_dir() / IO_SCHEMA_DIR
    out = []
    for name in sorted(cli.COMMANDS):
        module = cli.COMMANDS[name]
        tree = extract(module)
        args = tree if tree is not None else (from_manual(manual[name]) if name in manual else None)
        result_path = io_dir / io_schema_rel(name, "result")
        out.append(
            CommandSpec(
                name=name,
                module=module,
                args=args,
                extracted=tree is not None,
                manual=name in manual,
                result_schema=_checked_schema(result_path) if result_path.is_file() else None,
                surface=surface_of(public, "command", name),
            )
        )
    return out


def skills(plugin_root: Path, public: PublicIds, entrypoints: list[dict[str, Any]]) -> list[SkillSpec]:
    """実体のある配る Skill と、公開する面に載る Skill に、Skill の入出力の表の行を付ける。"""
    # 配る Skill の表と実体の保存先（オーナーは S1b の ndf.domains.manifests。検査はオーナーのパッケージを読まず、同じファイルを読む）
    listed = [s["name"] for s in read_json(plugin_root / "manifests" / "skills.json", UNREADABLE)["skills"]]
    present = {p.parent.name for p in (plugin_root / "skills").glob("*/SKILL.md")}
    rows = {r["id"]: r for r in facts.rows("skill-io")}
    public_eps = {r["target"]: r for r in entrypoints if r["kind"] == "skill"}
    names = sorted({n for n in listed if n in present} | set(public_eps))
    out = []
    for name in names:
        row = rows[name] if name in rows else {}
        ep = public_eps[name] if name in public_eps else None
        out.append(
            SkillSpec(
                name=name,
                surface=surface_of(public, "skill", name),
                present=name in present,
                row=name in rows,
                args=tuple(ep["args"] if ep is not None else opt(row, "args")),
                goal_end=opt(row, "goal_end", None),
                ndf_settings=tuple(opt(row, "ndf_settings")),
                env=tuple(ep["env"] if ep is not None else opt(row, "env")),
                result=opt(row, "result", None),
                meaning=opt(row, "meaning", ""),
                row_has_args_env=ep is not None and ("args" in row or "env" in row),
            )
        )
    return out


def hooks(public: PublicIds) -> list[HookSpec]:
    """hook の表のランタイムと事象ごとに、読む入力のキー・無くてよいキー・判定ごとの出力の形を並べる。"""
    table = contracts.load_json(HOOKS_TABLE)
    out = []
    for runtime in sorted(table["runtimes"]):
        events = table["runtimes"][runtime]["events"]
        for event in sorted(events):
            spec = events[event]
            out.append(
                HookSpec(
                    runtime=runtime,
                    event=event,
                    reads=tuple(opt(spec, "reads")),
                    optional=tuple(opt(spec, "optional")),
                    outputs=dict(opt(spec, "outputs", {})),
                    surface=surface_of(public, "hook", event),
                )
            )
    return out


def launchers(plugin_root: Path) -> tuple[list[dict[str, Any]], list[str]]:
    """起動スクリプトの表の行と、`bin/` の直下のファイルの名前（昇順。`__pycache__` を含めない）。"""
    base = plugin_root / BIN_DIR
    if not base.is_dir():
        raise CheckError(f"起動スクリプトの保存先が無い: {base}")
    names = sorted(p.name for p in base.iterdir() if p.is_file())
    return list(facts.rows("launchers")), names


def _without_result_ref(schema: dict[str, Any]) -> dict[str, Any]:
    """サブコマンドの結果のスキーマから結果のコントラクトへの `$ref` を外す（結果のコントラクトは `ndf.contracts.result.errors` が確かめる）。"""
    rest = [part for part in opt(schema, "allOf", []) if part != {"$ref": RESULT_REF}]
    out = {k: v for k, v in schema.items() if k != "allOf"}
    return out | ({"allOf": rest} if rest else {})


@contracts.cached_reader
def result_validator(name: str) -> Draft202012Validator | None:
    """サブコマンド `name` の結果のスキーマ（結果のコントラクトへの `$ref` を外したもの）の検証器。スキーマが無ければ `None`。"""
    path = contracts.contracts_dir() / IO_SCHEMA_DIR / io_schema_rel(name, "result")
    if not path.is_file():
        return None
    return Draft202012Validator(_without_result_ref(_checked_schema(path)))
