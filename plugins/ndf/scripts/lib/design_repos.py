"""design_repos.py: 代表のリポジトリの設定（`.ndf/design.json`）を読み、頭の設計文書に 2 規則を当てる（#3618）。

- `repo_scope_missing`: 「適用範囲」に、代表のリポジトリでの振る舞いの表（設定の種類ごとの行・「直した後に起きること」と
  「この種類で扱えなくなること」が空でない）と「このリポジトリだけの事情」の欄が無い
- `repo_loss_unrouted`: 振る舞いの表に「この種類で扱えなくなること」が `無し` で始まらない行があるのに、
  「人へ残す判断」の表に設定が名指すレッドラインの番号の行が無い

設定が無いか `representative_repos` が無ければ規則は働かない。設定の `paths` を置けば、頭の設計文書の
「設計の結果」の触るファイルのどれかが当たる設計（触るファイルを読めない設計を含む）だけに当てる。
ファイルを書かない。外部パッケージ（`md`・`schema`・`pathmatch`）は使う関数の中で読み、依存は呼ぶ側が
`dep_groups` の並びで先に求めておく。
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import NamedTuple

SETTINGS_PATH = ".ndf/design.json"
SCOPE_MISSING = "repo_scope_missing"
LOSS_UNROUTED = "repo_loss_unrouted"
RULES = (SCOPE_MISSING, LOSS_UNROUTED)
SCOPE_TITLE = "適用範囲"
BEHAVIOR_TITLE = "代表のリポジトリでの振る舞い"
OWN_SUFFIX = "だけの事情"
REPO_COL = "代表のリポジトリ"
AFTER_COL = "直した後に起きること"
LOSS_COL = "この種類で扱えなくなること"
NONE_VALUE = "無し"
REDLINE_PATTERN = r"^[CPR][1-9][0-9]*$"


class RepoSettingsError(ValueError):
    """代表のリポジトリの設定を読めない・形が合わない。"""


class RepoSettings(NamedTuple):
    kinds: tuple[str, ...]
    paths: tuple[str, ...] | None
    redline: str


def dep_groups(root: Path | None, schema: bool) -> tuple[str, ...]:
    """design-survey.py の 1 回の起動で要る依存のグループ。標準ライブラリだけで動く。"""
    groups = ["md"]
    if root is not None and (root / SETTINGS_PATH).is_file():
        groups += ["schema", "pathmatch"]
    if schema:
        groups.append("schema")
    return tuple(groups)


def _settings_model():
    """設定の型（`lib/schema.py` の `Shape`）。pydantic は呼ぶ側が依存を求めた後に読む。"""
    from typing import Annotated, Literal, Optional

    import schema
    from pydantic import Field, StringConstraints

    NonEmptyStr = Annotated[str, StringConstraints(min_length=1)]

    class RepresentativeRepos(schema.Shape):
        kinds: list[NonEmptyStr] = Field(min_length=1)
        paths: Optional[list[NonEmptyStr]] = Field(default=None, min_length=1)
        redline: Annotated[str, StringConstraints(pattern=REDLINE_PATTERN)]

    class DesignSettings(schema.Shape):
        version: Literal[1]
        representative_repos: Optional[RepresentativeRepos] = None

    return DesignSettings


def load_settings(root: Path) -> RepoSettings | None:
    """根の `.ndf/design.json` を読んで形を確かめる。無いか `representative_repos` が無ければ None。"""
    path = root / SETTINGS_PATH
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise RepoSettingsError(f"{SETTINGS_PATH} を読めない: {exc}") from None
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise RepoSettingsError(f"{SETTINGS_PATH} を JSON として読めない: {exc.lineno}:{exc.colno}") from None
    import schema

    model = _settings_model()
    try:
        shape = schema.load_shape(model, data)
    except schema.ShapeError as exc:
        raise RepoSettingsError(f"{SETTINGS_PATH} の形が合わない: {exc}") from None
    repos = shape.representative_repos
    if repos is None:
        return None
    seen: set[str] = set()
    for k in repos.kinds:
        if k in seen:
            raise RepoSettingsError(f"{SETTINGS_PATH} の種類が重なる: {k}")
        seen.add(k)
    return RepoSettings(tuple(repos.kinds), tuple(repos.paths) if repos.paths else None, repos.redline)


def design_json_schema() -> str:
    """設定の JSON Schema の文字列（末尾の改行つき）。"""
    model = _settings_model()
    s = model.model_json_schema()
    s["$schema"] = "https://json-schema.org/draft/2020-12/schema"
    s["title"] = "NDF の代表のリポジトリの設定（.ndf/design.json）"
    return json.dumps(s, ensure_ascii=False, indent=2) + "\n"


def applies(settings: RepoSettings, touched: set[str] | None) -> bool:
    """頭の設計文書に 2 規則を当てるか。触るファイルが分からない設計は安全側に倒して当てる。"""
    if settings.paths is None or touched is None:
        return True
    import pathmatch

    return any(pathmatch.path_matches(f, settings.paths) for f in touched)


def strip_marks(cell: str) -> str:
    return cell.strip().strip("`*_ ").strip()


def _col(header: list[str], name: str) -> int | None:
    return next((k for k, h in enumerate(header) if strip_marks(h) == name), None)


def _behavior_table(text: str, section):
    """節の中で、代表のリポジトリと扱えなくなることの列を持つ最初の表。"""
    import md

    return next(
        (t for t in md.tables_in(text, [section]) if _col(t.header, REPO_COL) is not None and _col(t.header, LOSS_COL) is not None),
        None,
    )


def _row_cell(row: list[str], col: int) -> str:
    return strip_marks(row[col]) if col < len(row) else ""


def _loss_rows(table) -> tuple[list[int], list[int]]:
    """（扱えなくなることがある行, 扱えなくなることが空の行）を文書の中の行番号（0 始まり）で返す。"""
    col = _col(table.header, LOSS_COL)
    lost: list[int] = []
    empty: list[int] = []
    for k, row in enumerate(table.rows):
        value = _row_cell(row, col)
        if not value:
            empty.append(table.body_line(k))
        elif not value.startswith(NONE_VALUE):
            lost.append(table.body_line(k))
    return lost, empty


def _table_problems(table, kinds: tuple[str, ...]) -> tuple[list[tuple[int, str]], list[int]]:
    """振る舞いの表の欠け（種類の行・扱えなくなることの空・直した後に起きることの列と空）を（行, 理由）で並べ、扱えなくなることがある行と返す。"""
    problems: list[tuple[int, str]] = []
    repo_col = _col(table.header, REPO_COL)
    names = [_row_cell(r, repo_col) for r in table.rows]
    for kind in kinds:
        if not any(n.startswith(kind) for n in names):
            problems.append((table.start, f"種類 {kind} の行が無い"))
    lost, empty = _loss_rows(table)
    problems += [(i, "扱えなくなることが空") for i in empty]
    after_col = _col(table.header, AFTER_COL)
    if after_col is None:
        problems.append((table.start, f"振る舞いの表に {AFTER_COL} の列が無い"))
    else:
        problems += [(table.body_line(k), f"{AFTER_COL}が空") for k, r in enumerate(table.rows) if not _row_cell(r, after_col)]
    return problems, lost


def _has_own_note(text: str, inner: list) -> bool:
    """「このリポジトリだけの事情」の節のどれかに、コードブロックの外の空でない行があるか。"""
    import md

    lines = text.splitlines()
    fenced = md.fenced_lines(text)
    own = [s for s in inner if s.heading.title.endswith(OWN_SUFFIX)]
    return any(any(not fenced[i] and lines[i].strip() for i in range(s.start, min(s.end, len(lines)))) for s in own)


def _scope_problems(text: str, settings: RepoSettings):
    """「適用範囲」の欠けを（行, 理由）で並べ、振る舞いの表の扱えなくなることがある行（表が無ければ空）と返す。"""
    import md

    sections = md.md_sections(text)
    scope = next((s for s in sections if s.heading.level == 2 and s.heading.title == SCOPE_TITLE), None)
    if scope is None:
        return [(-1, f"{SCOPE_TITLE}の節が無い")], []
    inner = [s for s in sections if scope.start <= s.heading.line < scope.end and s.heading.level == 3]
    head_line = scope.heading.line
    problems: list[tuple[int, str]] = []
    table = None
    lost: list[int] = []
    behavior = next((s for s in inner if s.heading.title == BEHAVIOR_TITLE), None)
    if behavior is None:
        problems.append((head_line, f"{BEHAVIOR_TITLE}の節が無い"))
    else:
        table = _behavior_table(text, behavior)
        if table is None:
            problems.append((behavior.heading.line, f"振る舞いの表に {REPO_COL} と {LOSS_COL} の列が無い"))
    if table is not None:
        found, lost = _table_problems(table, settings.kinds)
        problems += found
    if not _has_own_note(text, inner):
        problems.append((head_line, f"このリポジトリ{OWN_SUFFIX}の欄が無い"))
    return problems, lost


def check_repos(path: str, text: str, settings: RepoSettings, left_ids: set[str]) -> list[dict]:
    """頭の設計文書 1 本に 2 規則を当て、指摘を文書の行の順に返す。`left_ids` は人へ残す判断の表の番号の集合。"""
    lines = text.splitlines()

    def item(i: int, rule: str, reason: str) -> dict:
        shown = lines[i].strip() if 0 <= i < len(lines) else ""
        return {"name": f"{path}:{i + 1}", "rule": rule, "text": f"{reason}: {shown}" if shown else reason}

    problems, lost = _scope_problems(text, settings)
    found = [(i, item(i, SCOPE_MISSING, why)) for i, why in problems]
    if lost and settings.redline not in left_ids:
        why = f"扱えなくなることがあるのに、人へ残す判断に {settings.redline} の行が無い"
        found += [(i, item(i, LOSS_UNROUTED, why)) for i in lost]
    return [it for _, it in sorted(found, key=lambda x: x[0])]
