"""差分のファイルを、文書のファイルとコードのファイルに分ける（#2925 の決定 1）。

検査のプラン（リファクタリングの要否・範囲・全体テスト・振る舞いの確認）と ai-plugins の CI の判定が、
同じ分け方を読むための部品である。差分のファイルの一覧は呼び出し元が自分の手段（REST・git）で取って渡す。

宣言は `.ndf/project.json` の `docs`（省略可能。項目ごとに既定を置き換える）:

| 項目 | 形 | 省いたときの既定 |
| --- | --- | --- |
| `files` | 文書のファイルのパターン（`lib/pathmatch.py` の書き方） | `**/*.md` と、用語集の設定（`.ndf/glossary.json`）の `source`・`document` |
| `behavior` | 振る舞いを決める文書のパターン | `**/skills/**/*.md`・`**/agents/*.md` |
| `tested` | `{"paths": [パターン], "tests": [テストのパス]}` の並び | 空 |

決まり:
- 各ファイルは文書（`docs`）かコード（`code`）のどちらか 1 つにだけ入る（I1）
- 振る舞いを決める文書とテストが読む文書は、文書のファイルの部分集合である（I3）
- 走らせるテスト（`tests`）は、差分にあるテストが読む文書に対応するテストの和である（I4）
- 差分の一覧が無い・宣言が読めないときは、全部をコードとして扱い `unknown` に理由を書く（I2）

`load_docs_decl` は宣言を読み、`classify_files` はファイルも git も読まない純粋な関数である。差分の一覧は呼び出し元が
REST か `git_changed` で取る。使う側は `deps.require("pathmatch")` を先に呼ぶ。
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from pathmatch import path_matches

PROJECT_DECL = Path(".ndf") / "project.json"
GLOSSARY_DECL = Path(".ndf") / "glossary.json"
DEFAULT_FILES = ("**/*.md",)
DEFAULT_BEHAVIOR = ("**/skills/**/*.md", "**/agents/*.md")


class DeclError(ValueError):
    """宣言（`docs`）が読めない。文は 1 行の理由。"""


@dataclass(frozen=True)
class TestedDocs:
    paths: tuple[str, ...]
    tests: tuple[str, ...]


@dataclass(frozen=True)
class DocsDecl:
    files: tuple[str, ...]
    behavior: tuple[str, ...]
    tested: tuple[TestedDocs, ...] = ()


@dataclass
class DiffKinds:
    docs: list[str] = field(default_factory=list)
    behavior: list[str] = field(default_factory=list)
    tested: list[str] = field(default_factory=list)
    code: list[str] = field(default_factory=list)
    tests: list[str] = field(default_factory=list)
    unknown: str = ""

    def docs_only(self) -> bool:
        """分類でき、コードのファイルが無く、文書のファイルが 1 件以上あるときだけ真（I2）。"""
        return not self.unknown and not self.code and bool(self.docs)

    def as_json(self) -> str:
        return json.dumps(asdict(self), ensure_ascii=False)


def _patterns(value, where: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not value or not all(isinstance(p, str) and p.strip() for p in value):
        raise DeclError(f"{where} は空でない文字列の 1 件以上の並びにする")
    return tuple(value)


def _glossary_decl_files(root: Path) -> tuple[str, ...]:
    """用語集の設定の `source` と `document`。設定が無い・読めない・キーが無ければ空。"""
    try:
        data = json.loads((root / GLOSSARY_DECL).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ()
    if not isinstance(data, dict):
        return ()
    return tuple(v for v in (data.get("source"), data.get("document")) if isinstance(v, str) and v.strip())


def parse_decl(docs, root: Path) -> DocsDecl:
    """`docs` の値（無ければ `None`）を既定で補った宣言にする。形が壊れていれば `DeclError`。

    `{"unknown": ...}`（解析したが決まらなかった）は宣言が無いのと同じに扱う。
    """
    if docs is None or (isinstance(docs, dict) and set(docs) == {"unknown"}):
        docs = {}
    if not isinstance(docs, dict):
        raise DeclError("docs はオブジェクトにする")
    extra = set(docs) - {"files", "behavior", "tested"}
    if extra:
        raise DeclError(f"docs に知らない項目がある: {', '.join(sorted(extra))}")
    files = _patterns(docs["files"], "docs.files") if docs.get("files") is not None else DEFAULT_FILES + _glossary_decl_files(root)
    behavior = _patterns(docs["behavior"], "docs.behavior") if docs.get("behavior") is not None else DEFAULT_BEHAVIOR
    rows = docs.get("tested", [])
    if not isinstance(rows, list):
        raise DeclError("docs.tested は並びにする")
    tested = []
    for i, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != {"paths", "tests"}:
            raise DeclError(f"docs.tested[{i}] は paths と tests だけを持つ")
        tested.append(TestedDocs(_patterns(row["paths"], f"docs.tested[{i}].paths"), _patterns(row["tests"], f"docs.tested[{i}].tests")))
    return DocsDecl(files, behavior, tuple(tested))


def load_docs_decl(root: Path | str) -> DocsDecl:
    """`<root>/.ndf/project.json` の `docs` を読む。ファイルが無ければ既定。読めなければ `DeclError`。"""
    root = Path(root)
    f = root / PROJECT_DECL
    if not f.is_file():
        return parse_decl(None, root)
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError) as e:
        raise DeclError(f"{PROJECT_DECL} を読めない: {e}") from e
    if not isinstance(data, dict):
        raise DeclError(f"{PROJECT_DECL} がオブジェクトでない")
    return parse_decl(data.get("docs"), root)


def classify_files(files: Optional[list[str]], decl: Optional[DocsDecl], unknown: str = "") -> DiffKinds:
    """差分のファイルを分ける。`files` か `decl` が `None`、または `unknown` を渡したら全部をコードにする（I2）。"""
    if files is None or decl is None or unknown:
        reason = unknown or ("差分のファイルの一覧が無い" if files is None else "文書の分け方の宣言が無い")
        return DiffKinds(code=list(files or []), unknown=reason)
    out = DiffKinds()
    for f in dict.fromkeys(files):
        if not path_matches(f, decl.files):
            out.code.append(f)
            continue
        out.docs.append(f)
        if path_matches(f, decl.behavior):
            out.behavior.append(f)
        rows = [r for r in decl.tested if path_matches(f, r.paths)]
        if rows:
            out.tested.append(f)
        for r in rows:
            out.tests.extend(t for t in r.tests if t not in out.tests)
    return out


def classify_root(root: Path | str, files: Optional[list[str]]) -> DiffKinds:
    """`root` の宣言を読んで分ける。宣言が読めなければ理由つきで全部をコードにする。"""
    try:
        decl = load_docs_decl(root)
    except DeclError as e:
        return classify_files(files, None, str(e))
    return classify_files(files, decl)


def git_changed(root: Path | str, rev_range: str) -> Optional[list[str]]:
    """`git diff --name-only --no-renames <rev_range>` のファイル（消したファイルを含む）。取れなければ `None`。"""
    p = subprocess.run(["git", "-C", str(root), "diff", "--name-only", "-z", "--no-renames", rev_range], capture_output=True, check=False)
    return None if p.returncode != 0 else [f for f in p.stdout.decode("utf-8", "replace").split("\0") if f]
