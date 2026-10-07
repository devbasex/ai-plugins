"""範囲の判定（#1814 決定 1・2・11）。実装・修正・最終ゲート修正・テストの追加の取り込みが同じ `judge_unit` を呼ぶ。

判定の単位（1 回の取り込みで同じ規則に掛けるコミットの組）が触った各ファイルを、上から最初に当たった分類にする。

| 順 | 条件 | 分類 |
| --- | --- | --- |
| 1 | `--scope` の前方一致 | `in_scope` |
| 2 | 起点（`plan.base_sha`）のツリーに無い | `new`（置き場所によらず範囲の中） |
| 3 | 単位のどれかのコミットで消した（名前の変更の元を含む） | `outside` |
| 4 | 単位のコミットのハンクがすべて呼び手の書き換え | `rewrite`（最終ゲートのレビューへ引き継ぐ） |
| 5 | それ以外 | `outside` |

**判定の事実は git だけから取る。** 結果ファイルの申告（「新しく作った」「呼び手の書き換えだ」）は読まない（I1）。
"""

from __future__ import annotations

import json
import pathlib
import posixpath
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Iterable, Optional

from .gitfacts import CODE_EXTENSIONS, commit_file_status, commit_hunks, path_in_tree
from .paths import git_out, work_dir

IN_SCOPE = "in_scope"
NEW = "new"
REWRITE = "rewrite"
OUTSIDE = "outside"

# ありふれた語: 起点のツリーのコードのファイルの 20% 以上、かつ 3 ファイル以上に現れる識別子（決定 2 の初期値）
COMMON_RATIO = 0.2
COMMON_MIN_FILES = 3

_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass
class Unit:
    """判定の単位。`shas` は判定に掛けるコミット（古い順）、`context` は変えた名前だけを集める先のコミット。"""

    shas: list[str]
    scope: list[str]
    base_sha: Optional[str]
    context: list[str] = field(default_factory=list)


@dataclass
class FileVerdict:
    path: str
    kind: str
    reason: str = ""


@dataclass
class UnitVerdict:
    files: list[FileVerdict] = field(default_factory=list)
    rewrites: list[dict[str, str]] = field(default_factory=list)  # `review_scope_judgements` へ書く `{"path", "sha"}`
    problem: str = ""


def path_in_scope(path: str, scope: Iterable[str]) -> bool:
    """`path` が対象範囲の中にあるか。判定は**前方一致だけ**で行う。

    除外規則を足さない。規則を書けるようにすると、規則を 1 行足すだけで
    範囲のチェックを骨抜きにできてしまう。

    突き合わせる前に `./` を落とす。シェルの補完で `--scope ./src` の形になることが
    多い一方、git が出すのは `src/foo.py` なので、**そのまま比べると全てのコミットが
    範囲外**になり、適用が必ず失敗する。`.` と `./` はリポジトリ全体を指す。
    """
    for entry in scope:
        raw = str(entry).strip()
        if not raw:
            continue
        prefix = raw
        while prefix.startswith("./"):
            prefix = prefix[2:]
        prefix = prefix.rstrip("/")
        if prefix in {"", "."}:
            return True
        if path == prefix or path.startswith(prefix + "/"):
            return True
    return False


def identifiers(lines: Iterable[str]) -> set[str]:
    """行に現れる識別子。文字列とコメントの中も区別せずに拾う（言語を問わない）。"""
    return {m.group(0) for line in lines for m in _IDENT.finditer(line)}


# ---------- ありふれた語 ----------

_COMMON_CACHE: dict[tuple[str, str], frozenset[str]] = {}


def _code_pathspecs() -> list[str]:
    return [f"*{ext}" for ext in sorted(CODE_EXTENSIONS)]


def _count_common(work: str, base: str) -> frozenset[str]:
    """起点のツリーを 1 度だけ `git grep -o -w` で走査し、ありふれた語を返す。語ごとに起動しない。"""
    # `ls-tree` のパスの指定は glob を読まないため、拡張子はここで見る（`git grep` の指定は glob を読む）
    listed = git_out(work, ["ls-tree", "-r", "--name-only", base]) or ""
    total = len([p for p in listed.splitlines() if posixpath.splitext(p.strip())[1] in CODE_EXTENSIONS])
    out = git_out(work, ["grep", "-o", "-w", "-I", "-E", _IDENT.pattern, base, "--", *_code_pathspecs()], strip=False) or ""
    prefix = f"{base}:"
    pairs: set[tuple[str, str]] = set()
    for line in out.splitlines():
        if not line.startswith(prefix):
            continue
        path, sep, word = line[len(prefix) :].rpartition(":")
        if sep:
            pairs.add((path, word))
    counts = Counter(word for _, word in pairs)
    floor = max(COMMON_MIN_FILES, COMMON_RATIO * total)
    return frozenset(word for word, n in counts.items() if n >= floor)


def common_words(work: str, base: str, cache_dir: Optional[str] = None) -> frozenset[str]:
    """ありふれた語の表。1 実行に 1 度だけ作り、プロセスの中とファイル（`cache_dir`）で使い回す。"""
    key = (work, base)
    if key in _COMMON_CACHE:
        return _COMMON_CACHE[key]
    cache = pathlib.Path(cache_dir) / f"common-words-{base[:12]}.json" if cache_dir else None
    words: Optional[frozenset[str]] = None
    if cache is not None and cache.is_file():
        try:
            words = frozenset(json.loads(cache.read_text(encoding="utf-8")))
        except (OSError, ValueError):
            words = None
    if words is None:
        words = _count_common(work, base)
        if cache is not None:
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(json.dumps(sorted(words)), encoding="utf-8")
            except OSError:
                pass
    _COMMON_CACHE[key] = words
    return words


# ---------- 判定 ----------


def _path_names(path: str) -> set[str]:
    """パスの語幹とディレクトリ名（作った・消した・移したパスから変えた名前へ入れる）。"""
    stem = posixpath.splitext(posixpath.basename(path))[0]
    parts = [p for p in posixpath.dirname(path).split("/") if p]
    return identifiers([stem, *parts])


def changed_names(
    work: str,
    shas: Iterable[str],
    scope: list[str],
    base: str,
    common: frozenset[str],
    statuses: Optional[dict[str, dict[str, str]]] = None,
) -> set[str]:
    """変えた名前: 範囲の中と新しいファイルで変えた行の識別子と、作った・消した・移したパスの語幹とディレクトリ名（ありふれた語を除く）。"""
    names: set[str] = set()
    for sha in shas:
        status = (statuses or {}).get(sha) or commit_file_status(work, sha)
        for path, code in status.items():
            if code in {"A", "D"}:
                names |= _path_names(path)
            if path_in_scope(path, scope) or not path_in_tree(work, base, path):
                for removed, added in commit_hunks(work, sha, path):
                    names |= identifiers(removed) | identifiers(added)
    return names - common


def is_caller_rewrite(hunk: tuple[list[str], list[str]], names: set[str], common: frozenset[str]) -> bool:
    """ハンク 1 つが呼び手の書き換えか。

    H1: 変えた行に変えた名前が 1 つ以上ある。
    H2: 追加の行にだけ現れる識別子は、ありふれた語か、変えた名前か、同じハンクの削除の行にあるものに限る。
    """
    removed, added = identifiers(hunk[0]), identifiers(hunk[1])
    if not (removed | added) & names:
        return False
    return not (added - removed - common - names)


def _base_of(work: str, unit: Unit) -> Optional[str]:
    """起点。`plan.base_sha` が無い状態ファイルでは、単位の最初のコミットの親を使う。"""
    if unit.base_sha:
        return unit.base_sha
    return git_out(work, ["rev-parse", f"{unit.shas[0]}^"]) if unit.shas else None


def judge_unit(work: str, unit: Unit, cache_dir: Optional[str] = None) -> UnitVerdict:
    """判定の単位の各ファイルを分類し、範囲の外が 1 件でもあれば理由を返す。範囲が空ならチェックしない。"""
    verdict = UnitVerdict()
    if not unit.scope or not unit.shas:
        return verdict
    base = _base_of(work, unit)
    statuses = {sha: commit_file_status(work, sha) for sha in unit.shas}
    touched: dict[str, list[str]] = {}
    for sha in unit.shas:
        for path in statuses[sha]:
            touched.setdefault(path, []).append(sha)
    pending: dict[str, list[str]] = {}
    for path in sorted(touched):
        if path_in_scope(path, unit.scope):
            verdict.files.append(FileVerdict(path, IN_SCOPE))
        elif base is None or not path_in_tree(work, base, path):
            verdict.files.append(FileVerdict(path, NEW))
        elif any(statuses[sha].get(path) == "D" for sha in touched[path]):
            verdict.files.append(FileVerdict(path, OUTSIDE, "範囲の外の既存のファイルを消しています"))
        else:
            pending[path] = touched[path]
    if pending and base is not None:
        common = common_words(work, base, cache_dir)
        names = changed_names(work, [*unit.context, *unit.shas], unit.scope, base, common, statuses)
        for path, shas in pending.items():
            hunks = [(sha, h) for sha in shas for h in commit_hunks(work, sha, path)]
            if hunks and all(is_caller_rewrite(h, names, common) for _, h in hunks):
                verdict.files.append(FileVerdict(path, REWRITE))
                verdict.rewrites.extend({"path": path, "sha": sha} for sha in shas)
            else:
                verdict.files.append(FileVerdict(path, OUTSIDE, "変えた名前の読み込み・呼び出しの書き換え以外の変更があります"))
    verdict.files.sort(key=lambda f: f.path)
    outside = [f for f in verdict.files if f.kind == OUTSIDE]
    if outside:
        shown = "、".join(f"{f.path}（{f.reason}）" for f in outside[:5])
        more = f" ほか {len(outside) - 5} 件" if len(outside) > 5 else ""
        verdict.problem = (
            f"対象範囲の外の既存のファイルを変更しています: {shown}{more}。"
            "範囲の外の既存のファイルは、項目が変えた名前の読み込みと呼び出しの書き換えだけ変えられます。"
            "生成物の同期は進行側が公開の直前に行います"
        )
    return verdict


def judge_commits(state: dict, shas: list[str], context: Iterable[str] = ()) -> UnitVerdict:
    """状態ファイルの範囲（`target_scope`）と起点（`plan.base_sha`）で `judge_unit` を呼ぶ。取り込みの各経路の入口。"""
    scope = list(state.get("target_scope") or [])
    if not scope or not shas:
        return UnitVerdict()
    base = (state.get("plan") or {}).get("base_sha")
    return judge_unit(work_dir(state), Unit(list(shas), scope, base, list(context)), state.get("tmp_dir"))
