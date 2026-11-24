"""検査が共有する型: 指摘・見る範囲・1 回だけ解析するファイル・規則のデータ。

構文木は `Source.tree` が 1 ファイルにつき 1 回だけ作り、すべての規則が同じ木を読む（RF3）。規則は木を書き換えない。
コントラクトの値（`Source.contract`）と検証済みの上限の設定（`Scope.limit_settings`）も同じく 1 回だけ作り、規則は書き換えない。
"""

from __future__ import annotations

import ast
import json
import os
import re
import sys
import warnings
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path, PurePosixPath
from typing import Any, Optional

from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap
from ruamel.yaml.error import YAMLError

from ndf import contracts, identity
from ndf.checks import imports
from ndf.contracts import limits

RULES_PATH = Path(__file__).with_name("rules.json")
# スクリプトを置くディレクトリ（プラグインの根からの相対パスの前置き）
SCRIPT_PREFIXES = ("bin/", "hooks/")
# 製品のコードを置くディレクトリ（プラグインの根からの相対パス）
SRC_DIR = "src"
# プラグインの根からのコントラクトの保存先（`ndf.contracts` の既定の保存先の名前）
CONTRACTS = contracts.DEFAULT_DIR.name
# 見るファイルを集めるときに降りないディレクトリ（依存の環境・キャッシュ）
SKIP_DIRS = frozenset({"__pycache__", ".venv", ".pytest_cache", ".git", ".ruff_cache"})
# `ReusedAnchorWarning` の文の、2 つ目の定義の位置（行の抜粋は字下げされ、行頭の語に当たらない）
REUSED_ANCHOR_AT = re.compile(r'^second occurrence\s+in "[^"\n]*", line (\d+), column (\d+)', re.MULTILINE)


class CheckError(Exception):
    """規則のデータ・用語集・git が読めず、検査を始められない。"""


@dataclass(frozen=True)
class Finding:
    """1 つの規則が 1 つの箇所で見つけた違反。`path` はリポジトリの根からの相対のパス。"""

    rule: str
    path: str
    line: int
    detail: str

    def as_item(self) -> dict[str, Any]:
        return {"rule": self.rule, "path": self.path, "line": self.line, "detail": self.detail}


@dataclass
class Source:
    """見るファイル 1 本。本文と構文木は最初に読んだときに 1 回だけ作る。"""

    path: Path
    rel: str

    @cached_property
    def text(self) -> str:
        return self.path.read_text(encoding="utf-8")

    @cached_property
    def tree(self) -> ast.Module:
        """構文の誤りは `SyntaxError` のまま送出する（検査のエントリポイントが `syntax` の違反にする）。"""
        return ast.parse(self.text, filename=self.rel)

    @cached_property
    def _contract(self) -> Any:
        return contracts.parse_file(self.path, self.text)

    def contract(self, unreadable: str) -> Any:
        """コントラクトのファイルの値。実行時の読み手と同じく同じキーが 2 つあれば断る。断るときは `CheckError`。"""
        try:
            return self._contract
        except (OSError, contracts.ContractError, UnicodeDecodeError) as exc:
            raise CheckError(f"{unreadable}: {exc}") from exc

    @property
    def is_python(self) -> bool:
        return self.path.suffix == ".py"


@dataclass(frozen=True)
class Limits:
    """プラグインの上限の設定の、方針の名前 → ファイルと、スキーマで確かめた方針。"""

    sources: Mapping[str, Source]
    policies: Mapping[str, limits.Policy]


@dataclass(frozen=True)
class Workflow:
    """1 回だけ解析したワークフロー 1 本。`jobs` はジョブの名前 → 定義（`jobs` が無ければ空）。"""

    path: Path
    lines: list[str]
    jobs: dict[str, Any]

    @property
    def steps(self) -> list[CommentedMap]:
        """すべてのジョブのステップ。"""
        return [
            step
            for job in self.jobs.values()
            if isinstance(job, dict) and isinstance(job.get("steps"), list)
            for step in job["steps"]
            if isinstance(step, CommentedMap)
        ]


@dataclass
class Scope:
    """見る範囲。`sources` は `plugin_root` の下だけを持つ。"""

    repo_root: Path
    plugin_root: Path
    plugin_name: str
    sources: list[Source] = field(default_factory=list)

    @cached_property
    def settings(self) -> dict[str, Any]:
        """リポジトリの設定（`.ndf/project.json`）の値。設定を使う規則が最初に読んだときに 1 回だけ読む。

        設定を使わない規則だけを当てる検査は、設定のファイルが壊れていても止まらない。
        """
        return load_settings(self.repo_root)

    @cached_property
    def workflows(self) -> list[Workflow]:
        """設定の `toolchain.workflows` に当たるワークフロー（キーが無ければ空）。規則の数によらず、読みと解析は 1 ファイル 1 回だけ。"""
        toolchain = self.settings.get("toolchain")
        if not isinstance(toolchain, dict) or "workflows" not in toolchain:
            return []
        return [read_workflow(p) for p in self.repo_files(toolchain["workflows"])]

    def rel(self, path: Path) -> str:
        return path.relative_to(self.repo_root).as_posix()

    def repo_files(self, glob: str) -> list[Path]:
        """`repo_root` からの相対のグロブに当たるファイル（プラグインの外も引ける）。並びは昇順。

        グロブが絶対パス・`..` を含む・空なら `CheckError`。解決した先がリポジトリの外のもの（外を指すシンボリックリンク）は返さない。
        """
        pure = PurePosixPath(glob)
        if not glob or pure.is_absolute() or Path(glob).is_absolute() or ".." in pure.parts:
            raise CheckError(f"リポジトリの根からの相対のグロブでない: {glob!r}")
        root = self.repo_root.resolve()
        try:
            matched = list(self.repo_root.glob(glob))
        except (ValueError, NotImplementedError) as exc:
            raise CheckError(f"グロブを解けない: {glob!r}: {exc}") from exc
        return sorted(p for p in matched if p.is_file() and _resolves_under(p, root))

    def repo_file(self, rel: str, label: str) -> Path:
        """`rel` に当たるただ 1 つのファイル。`repo_files` と同じ確かめをし、1 つに決まらなければ読む前に `CheckError`。"""
        paths = self.repo_files(rel)
        if len(paths) != 1:
            raise CheckError(f"{label}が 1 つに決まらない（リポジトリの中のファイルだけを見る）: {rel}: {len(paths)} 件")
        return paths[0]

    def python_under(self, top: Path) -> list[Source]:
        """`top` の下の `.py`。プラグインの中は集めた `sources` を使い、外のファイルだけを読む。解決した先がリポジトリの外のものは除く。

        プラグインの外の `.py` が読めなければ `CheckError`（プラグインの中の構文の誤りは検査が違反にして見る範囲から外す）。
        """
        if top.is_relative_to(self.plugin_root):
            return [s for s in self.python() if s.path.is_relative_to(top)]
        root = self.repo_root.resolve()
        if not _resolves_under(top, root):
            return []
        found = [s for s in self.python() if s.path.is_relative_to(top)]
        for path in walk(top):
            if path.suffix != ".py" or path.is_relative_to(self.plugin_root):
                continue
            if _resolves_under(path, root) and path.is_file():
                found.append(_parsed(Source(path, self.rel(path))))
        return found

    def plugin_rel(self, src: Source) -> str:
        """プラグインの根からの相対のパス（`src/ndf/...`・`tests/...`）。"""
        return src.path.relative_to(self.plugin_root).as_posix()

    def source(self, path: Path) -> Source:
        """`path` のファイル。見る範囲にあれば、すべての規則が共有する `Source` を返す。"""
        return next((s for s in self.sources if s.path == path), None) or Source(path, self.rel(path))

    def split_table(self, rel: str) -> Optional[dict[str, Source]]:
        """コントラクトの分けた表 `rel` の、項目のキー → ファイル。ディレクトリが無ければ `None`、キーの形が違えば `CheckError`。"""
        root = self.plugin_root / CONTRACTS / rel
        if not root.is_dir():
            return None
        try:
            keys = contracts.item_keys(p.name for p in root.iterdir())
        except contracts.ContractError as exc:
            raise CheckError(f"分けた表 {root} に{exc}") from exc
        held = {s.path: s for s in self.sources}
        paths = {key: root / f"{key}.json" for key in keys}
        return {key: held[path] if path in held else Source(path, self.rel(path)) for key, path in paths.items()}

    def contract(self, rel: str, unreadable: str) -> Any:
        """コントラクトの値。`rel` がファイル（`.json`）ならその値、分けた表なら項目のキー → 値。読めなければ `CheckError`。"""
        if rel.endswith(".json"):
            return self.source(self.plugin_root / CONTRACTS / rel).contract(unreadable)
        table = self.split_table(rel)
        if table is None:
            raise CheckError(f"{unreadable}: 分けた表のディレクトリが無い: {rel}")
        return {key: source.contract(unreadable) for key, source in table.items()}

    @cached_property
    def limit_settings(self) -> Optional[Limits]:
        """上限の設定。持たないプラグインは `None`、読めない・形が違えば `CheckError`。"""
        sources = self.split_table(limits.NDF_SETTINGS)
        if sources is None:
            return None
        raw = {name: source.contract("上限の設定を読めない") for name, source in sources.items()}
        try:
            return Limits(sources, limits.validate(raw))
        except limits.LimitsError as exc:
            raise CheckError(str(exc)) from exc

    def code(self) -> list[Source]:
        """製品のコード（`src/` の下の `.py`）。兆候・構造・輪・重複の規則が見る。"""
        return [s for s in self.sources if s.is_python and self.plugin_rel(s).startswith(SRC_DIR + "/")]

    def code_files(self) -> list[Source]:
        """コードのファイル（テストを含む `.py` と `bin/`・`hooks/` のスクリプト）。行数の上限が見る。"""
        return [s for s in self.sources if s.is_python or self.plugin_rel(s).startswith(SCRIPT_PREFIXES)]

    def modules(self) -> dict[str, Source]:
        """製品のコード（`code()`）をモジュール名 → ファイルの表にする。"""
        return {module_name(self, s): s for s in self.code()}

    def python(self) -> list[Source]:
        """テストを含む `.py` のすべて。I3 が見る。"""
        return [s for s in self.sources if s.is_python]


def _load_yaml(path: Path, text: str) -> Any:
    """YAML の値。読み手の警告（同じ名前のアンカーなど）の文には入力の行の抜粋が載るので、標準エラーへはパスと位置だけを出す。"""
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        doc = YAML(typ="rt").load(text)
    for w in caught:
        at = REUSED_ANCHOR_AT.search(str(w.message))
        where = f":{at[1]}:{at[2]}" if at else ""
        print(f"ワークフローの YAML の警告 {w.category.__name__}: {path}{where}", file=sys.stderr)
    return doc


def read_workflow(path: Path) -> Workflow:
    """ワークフローを読む。YAML として読めなければ `CheckError`。"""
    try:
        text = path.read_text(encoding="utf-8")
        doc = _load_yaml(path, text)
    except YAMLError as exc:
        # 例外の文には入力の行の抜粋が載る（Git の外の設定ファイルも読みうる）。パスと位置だけを出す
        mark = getattr(exc, "problem_mark", None) or getattr(exc, "context_mark", None)
        where = f":{mark.line + 1}:{mark.column + 1}" if mark is not None else ""
        raise CheckError(f"ワークフローを YAML として読めない: {path}{where}") from None
    except UnicodeDecodeError as exc:
        raise CheckError(f"ワークフローを UTF-8 として読めない: {path}: 位置 {exc.start}") from None
    except (ValueError, LookupError, AttributeError):
        # 型のタグ（`!!int` など）や日付の値を変換できないときの例外の文には、値がそのまま載る
        raise CheckError(f"ワークフローの値を型のとおりに読めない: {path}") from None
    except OSError as exc:
        raise CheckError(f"ワークフローを読めない: {path}: {exc.strerror}") from None
    jobs = doc.get("jobs") if isinstance(doc, dict) else None
    return Workflow(path, text.splitlines(), jobs if isinstance(jobs, dict) else {})


def walk(top: Path) -> list[Path]:
    """`top` の下のファイル。`SKIP_DIRS` のディレクトリへは降りない（依存の環境などを列挙しない）。"""
    found = []
    for parent, dirs, files in os.walk(top):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
        found += [Path(parent, name) for name in files]
    return found


def _resolves_under(path: Path, root: Path) -> bool:
    """シンボリックリンクを解いた先が `root` の中か。`root` は解決済みで渡す（ファイルごとに解決し直さない）。"""
    return path.resolve().is_relative_to(root)


def _parsed(src: Source) -> Source:
    """構文木を作ったファイル。読めなければ `CheckError`。"""
    try:
        _ = src.tree
    except (OSError, SyntaxError, ValueError) as exc:
        raise CheckError(f"Python のファイルを読めない: {src.rel}: {exc}") from exc
    return src


def module_name(scope: Scope, src: Source) -> str:
    """`src/ndf/core/loop.py` → `ndf.core.loop`。`__init__.py` はパッケージの名前。"""
    parts = list(src.path.relative_to(scope.plugin_root / "src").with_suffix("").parts)
    if parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def module_rel(module: str, suffix: str) -> str:
    """`ndf.identity` と `.py` → `src/ndf/identity.py`（プラグインの根からの相対パス。`module_name` の逆）。"""
    return SRC_DIR + "/" + module.replace(".", "/") + suffix


def source_imports(src: Source, module: str) -> list[imports.Import]:
    """ファイルの import（`__init__.py` はパッケージとして相対の import を解く）。"""
    return imports.collect(src.tree, module, src.path.name == "__init__.py")


def _docstrings(tree: ast.Module) -> set[int]:
    owners = [tree, *(n for n in ast.walk(tree) if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)))]
    return {id(o.body[0].value) for o in owners if o.body and isinstance(o.body[0], ast.Expr) and isinstance(o.body[0].value, ast.Constant)}


def string_constants(tree: ast.Module) -> list[tuple[int, str]]:
    """文字列の定数（docstring を除き、f 文字列の文字の部分を含む）を行とともに返す。"""
    docs = _docstrings(tree)
    return [(n.lineno, n.value) for n in ast.walk(tree) if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs]


def contained(root: Path, rel: str | Path) -> Path:
    """`root` の下の `rel` の解決後のパス。`root` の外を指すか、シンボリックリンクを通れば `CheckError`（たどって読み書きしない）。"""
    base = root.resolve()
    named = Path(os.path.normpath(base / rel))
    try:
        resolved = named.resolve()
    except (OSError, RuntimeError) as exc:
        raise CheckError(f"パスを解決できない: {rel}: {exc}") from exc
    if resolved != named or not resolved.is_relative_to(base):
        raise CheckError(f"{base} の外を指すか、シンボリックリンクを通るパスは読み書きしない: {rel}")
    return resolved


def read_json_text(path: Path | str, unreadable: str) -> tuple[str, Any]:
    """JSON のファイルの本文と値。読めない・JSON でなければ `CheckError`（`<unreadable>: <渡したパス>: <例外>`）。"""
    try:
        text = Path(path).read_text(encoding="utf-8")
        return text, json.loads(text)
    except (OSError, ValueError) as exc:
        raise CheckError(f"{unreadable}: {path}: {exc}") from exc


def read_json(path: Path | str, unreadable: str) -> Any:
    """JSON のファイルの値。読めないときは `read_json_text` と同じ `CheckError`。"""
    return read_json_text(path, unreadable)[1]


def key_line(lines: Sequence[str], name: str) -> int:
    """JSON の本文の行の並び `lines` で、キー `name` が最初に現れる行の番号。無ければ 1。"""
    return next((n for n, line in enumerate(lines, 1) if f'"{name}":' in line), 1)


def settings_path(repo_root: Path) -> Path:
    """リポジトリの設定（`.ndf/project.json`）のパス。"""
    return repo_root / identity.FIXED_WORDS["ndf_settings_dir"] / "project.json"


def load_settings(repo_root: Path) -> dict[str, Any]:
    """リポジトリの設定（`.ndf/project.json`）の値。無ければ空の辞書、読めない・オブジェクトでなければ `CheckError`。

    取得した他人のリポジトリを検査する経路があるため、シンボリックリンクを解いた先がリポジトリの外なら、ファイルの有無を
    見る前に `CheckError` にする（外のファイルを読まない）。確かめと読みの間の差し替えまでは縛らない（検査の最中に
    リポジトリへ書ける人は、検査の結果そのものを変えられる）。
    """
    path = settings_path(repo_root)
    if not path.resolve().is_relative_to(repo_root.resolve()):
        raise CheckError(f"リポジトリの設定がリポジトリの外を指す: {path}")
    if not path.is_file():
        return {}
    data = read_json(path, "リポジトリの設定を読めない")
    if not isinstance(data, dict):
        raise CheckError(f"リポジトリの設定がオブジェクトでない: {path}")
    return data


def load_rules(path: Path = RULES_PATH) -> dict[str, Any]:
    """規則のデータを読む。形の確かめは使う側のキーの参照（欠ければ `KeyError`）に任せず、ここでキーの有無を見る。"""
    data = read_json(path, "規則のデータを読めない")
    missing = [
        k
        for k in (
            "smells",
            "ruff",
            "boundaries",
            "exception_types",
            "structure",
            "facts",
            "limits",
            "internal_names",
            "words",
            "copy_dup",
            "child_deps",
            "contracts_split",
            "experimental",
            "toolchain",
            "budget",
        )
        if k not in data
    ]
    if missing:
        raise CheckError(f"規則のデータにキーが無い: {path}: {missing}")
    bad = [code for code, rule in data["smells"].items() if rule["suppress"] not in ("none", "boundary", "boundary-or-limit")]
    if bad:
        raise CheckError(f"規則のデータの mark が知らない値: {bad}")
    return data
