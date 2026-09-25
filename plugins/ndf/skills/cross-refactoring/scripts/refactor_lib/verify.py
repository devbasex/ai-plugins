"""適用結果の検証。

対象範囲の逸脱・コミットのトレーラー・差分予算・コミット粒度を判定する。判定に
使う事実は git から取った値で、結果ファイルの申告は使わない。
"""
from __future__ import annotations

import ast
import difflib
import posixpath
import re

from collections import Counter

from typing import Any, Iterable, Optional

from .paths import git_out
from .gitfacts import safe_int
from .vocabulary import (
    DIFF_BUDGET_FACTOR,
    EXTRACTION_DIFF_BUDGET_FACTOR,
    EXTRACTION_TECHNIQUES,
    FINAL_FIX_TRAILERS,
    REQUIRED_TRAILERS,
)


# **結果ファイルの申告は検証の材料にしない。** 実装担当は自分の成果を報告する側なので、
# トレーラーもテスト結果も差分行数も、JSON の値を書き換えるだけでチェックを通せてしまう。
# ここで使う事実（コミットの実在 / トレーラー / 差分行数 / テストの成否）は、すべて
# **git と実際のテスト実行**から取る。結果ファイルから使うのは「どのコミットが
# どの項目のものか」という対応付けの手がかりだけである。

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


def out_of_scope_files(commit: dict[str, Any], scope: Iterable[str]) -> list[str]:
    """コミットが触った**対象範囲の外**のファイル。範囲が空ならチェックしない。"""
    paths = list(scope)
    if not paths:
        return []
    return sorted(
        p for p in (commit.get("files") or []) if not path_in_scope(p, paths)
    )


def verify_scope(commit: dict[str, Any], scope: Iterable[str]) -> Optional[str]:
    """対象範囲の外を触っていれば理由を返す。

    範囲を必須にした目的は**提案の発散と変更の肥大を防ぐ**ことなので、指定を
    検証に反映しないと目的を果たせない。実測では、生成物を同期する規約に従った
    結果として範囲外が 3 系統変更され、差分が 4 倍に膨らんで差分予算を超えた。
    生成物の同期が要る構成では、**同期は進行側の責務**として分離する。
    """
    outside = out_of_scope_files(commit, scope)
    if not outside:
        return None
    shown = ", ".join(outside[:5])
    more = f" ほか {len(outside) - 5} 件" if len(outside) > 5 else ""
    return (
        f"コミット {commit.get('sha', '?')} が対象範囲の外を変更しています"
        f"（{shown}{more}）。生成物の同期は進行側が公開の直前に行います。"
        "現状固定テストの置き場所が範囲外なら、`--scope` に含めてから実行してください"
    )


def verify_commit_trailers(
    commit: dict[str, Any], required: Iterable[str] = REQUIRED_TRAILERS
) -> Optional[str]:
    """コミットのトレーラーが揃っているか。欠けていれば理由を返す。

    `commit` は **git から取った事実**（`collect_commit_facts()` の戻り値）である。
    結果ファイルの `trailers` を渡してはならない。

    求める顔ぶれを引数で受け取るのは、**最終ゲートの修正が改善項目にも提案ラウンド
    にも属さない**ためである（`FINAL_FIX_TRAILERS`）。既定は適用と修正の 4 つ。
    """
    trailers = commit.get("trailers") or {}
    missing = [k for k in required if not str(trailers.get(k) or "").strip()]
    if missing:
        return f"コミット {commit.get('sha', '?')} にトレーラーが欠けています: {', '.join(missing)}"
    return None


def verify_commit_basics(
    commit: dict[str, Any],
    scope: Optional[Iterable[str]],
    missing_reason: str,
    check_test: bool = True,
    required_trailers: Iterable[str] = REQUIRED_TRAILERS,
) -> Optional[str]:
    """コミット 1 件が手順を満たしているかを検証する。問題があれば理由を返す。

    実装・テストの追加（`commands/implement.py`）と修正（`commands/converge.py`）で
    **同じ基準**を使う。
    片方だけ直されると基準が食い違い、緩い側から手順を外れた変更が入る。

    実体が無いときの理由文だけは呼び出し側から渡す。

    **取り込みではテストの合否を見ない**（`check_test=False`）。手順を外れたことと、
    テストが落ちることは扱いが違う。前者はその項目を取り消し、後者は修正へ回す。
    テストは `verify` が項目の単位で走らせる（決定 14）。
    """
    if not commit.get("exists", True):
        return missing_reason
    problem = verify_commit_trailers(commit, required_trailers)
    if problem:
        return problem
    problem = verify_scope(commit, scope or [])
    if problem:
        return problem
    if check_test and commit.get("test_status") != "pass":
        return (
            f"コミット {commit.get('sha', '?')} でテストが成功していません "
            f"({commit.get('test_status')})"
        )
    return None


def verify_final_fix_commit(
    commit: dict[str, Any], scope: Optional[Iterable[str]] = None
) -> Optional[str]:
    """最終ゲート（Step 7）の修正コミットを検証する。問題があれば理由を返す。

    実装と修正の取り込みと**見る先が 2 つだけ違う**。

    - **テストの合否を見ない**（`check_test=False`）。`--ci-check` を指定した実行では
      手元のテストを 1 度も走らせないと決めてある（#436 決定 11 の排他）。ここで
      コミットごとに走らせると、その排他をこの経路だけが破ることになる。合否は
      直後の `final-gate` が採った側（手元のテスト / 継続的統合）で 1 度だけ見る。
    - **`Item-Id` を求めない**。最終ゲートの失敗は全体のテストのもので、
      どの改善項目にも紐づかない。

    **対象範囲は見る。** 最終ゲートでも `--scope` の外を触ってよい理由は無い。
    """
    return verify_commit_basics(
        commit,
        scope,
        f"コミット {commit.get('sha', '?')} が最終ゲートの修正の範囲に存在しません",
        check_test=False,
        required_trailers=FINAL_FIX_TRAILERS,
    )


def diff_budget_factor(technique: Optional[str]) -> int:
    """その手法に許す差分予算の倍率。

    抽出系だけ広げる。全体を広げると、範囲外を触った変更まで通ってしまう。
    """
    if technique in EXTRACTION_TECHNIQUES:
        return EXTRACTION_DIFF_BUDGET_FACTOR
    return DIFF_BUDGET_FACTOR


def verify_diff_budget(
    items: list[dict[str, Any]], facts: list[dict[str, Any]],
) -> Optional[str]:
    """実差分が、見積の行数から決まる差分予算に収まっているか。"""
    estimated = sum(safe_int(i.get("estimated_diff_lines")) for i in items)
    factor = max(
        (diff_budget_factor(i.get("technique")) for i in items),
        default=DIFF_BUDGET_FACTOR,
    )
    budget = estimated * factor
    actual = sum(int(c.get("diff_lines") or 0) for c in facts)
    if budget and actual > budget:
        return (
            f"実差分 {actual} 行が差分予算 {budget} 行"
            f"（見積 {estimated} 行 × {factor}）を超えました（範囲の逸脱）"
        )
    return None


def unassigned_fix_commits(
    work: str, reported_shas: list[str], ordered_range: list[str]
) -> list[str]:
    """範囲内のコミットのうち、どの申告にも含まれていないものを返す。

    適用と同じく、**範囲のコミットは全て申告されていること**を求める。
    申告から漏れた修正コミットは検証を受けないまま Pull Request に残る。
    """
    reported_full = {
        full for full in (
            git_out(work, ["rev-parse", "--verify", f"{s}^{{commit}}"])
            for s in reported_shas
        ) if full
    }
    return sorted(set(ordered_range) - reported_full)

# ---------- テストの変更の種類 ----------
#
# **「テストを足したか」だけでは、期待値の変更を止められない**（#443）。同じ入力に対する
# 期待出力が変わっていれば、それは振る舞いの変更である。
#
# ここが担うのは一次の判定（機械）で、決まらないものは最終ゲートのレビューへ引き継ぐ（決定 25）。
#
# **機械が「変わっていない」と言える範囲を最小にする。** 差分の意味を機械で読もうとすると
# 穴が開く。実測で 5 回続けて別の抜けが見つかった。
#
# | 抜けた形 | なぜ抜けたか |
# | --- | --- |
# | `Status.SUCCESS.value` → `Status.FAILURE.value` | 接頭辞を伏せると同じ行に見える |
# | `EXPECTED = 4` を足して既存を残す | 外側の行が減らない |
# | 値を定数へ抽出する | `assert` の行から値が消える |
#
# **決められないものを決めない。** 最終ゲートのレビューが読む。

# 値そのもの。数値・文字列・真偽・None を指す。
_LITERAL = re.compile(
    r"""(?:[rbuf]{0,2}"(?:\\.|[^"\\])*"|[rbuf]{0,2}'(?:\\.|[^'\\])*'"""
    r"""|\b\d+(?:\.\d+)?\b|\bTrue\b|\bFalse\b|\bNone\b)"""
)


def _values(lines: Iterable[str]) -> "Counter[str]":
    """差分の中の値を、件数ごと数える。**`assert` の行に限らない。**"""
    found: Counter[str] = Counter()
    for line in lines:
        for literal in _LITERAL.findall(line):
            found[literal] += 1
    return found


def assertion_change(before: Iterable[str], after: Iterable[str]) -> str:
    """テストの変更の種類を返す。

    | 戻り値 | 意味 | 判定 |
    | --- | --- | --- |
    | `unchanged` | 変わっていない | 前後が同一 |
    | `changed` | 期待出力が変わった | **値が失われた** |
    | `undecidable` | **機械では決まらない** | それ以外すべて |

    **`unchanged` は「同じ」のときだけ返す。** 経路だけの変更も、行の並べ替えも、
    テストの追加も、機械では期待出力への影響を否定できない。最終ゲートのレビューが読む。
    """
    rows_before, rows_after = list(before), list(after)
    if rows_before == rows_after:
        return "unchanged"
    if _values(rows_before) - _values(rows_after):
        return "changed"
    return "undecidable"


def undecidable_test_changes(
    changes: dict[str, tuple[list[str], list[str]]],
) -> list[str]:
    """機械では判定できないテストの差分を、ファイルの順で返す。

    **呼ぶ側はこれを最終ゲートのレビューへ引き継ぐ。** 空でないまま通さない。
    """
    return sorted(
        path for path, (before, after) in changes.items()
        if assertion_change(before, after) == "undecidable"
    )


def _changed_test_message(changed: list[str]) -> str:
    return (
        "テストの期待する振る舞いが変わっています"
        f"（{', '.join(changed)}）。"
        "構造改善では期待出力を変えません。振る舞いの変更は別の変更に分けてください"
    )


def collect_test_changes(
    facts: Iterable[dict[str, Any]],
) -> dict[str, tuple[list[str], list[str]]]:
    """コミット単位の `test_changes` を 1 つの辞書へまとめる。

    後のコミットの値が前のコミットの値を上書きする（同じファイルなら最後の状態を採る）。
    """
    changes: dict[str, tuple[list[str], list[str]]] = {}
    for commit in facts:
        changes.update(commit.get("test_changes") or {})
    return changes


def verify_test_changes(
    changes: dict[str, tuple[list[str], list[str]]],
) -> Optional[str]:
    """テストの差分に、期待値の変更が含まれていないかを見る。

    **判定できないものはここでは落とさない。** `undecidable_test_changes` が集め、
    呼ぶ側がレビューへ引き継ぐ。
    """
    changed = sorted(
        path for path, (before, after) in changes.items()
        if assertion_change(before, after) == "changed"
    )
    if not changed:
        return None
    return _changed_test_message(changed)


def pending_test_judgements(facts: Iterable[dict[str, Any]]) -> list[str]:
    """機械（一次の判定）で決まらないテストを、ファイルの順で返す。最終ゲートのレビューへ引き継ぐ（決定 25）。

    **機械で決まらなかったものだけが残る。** 空でないまま収束させない。
    """
    changes = collect_test_changes(facts)
    return undecidable_test_changes(changes)


# ---------- 文書の文言を固定するテスト（#723） ----------
#
# **判定は追跡している `.md` のパスとの一致で行う。** `.md` で終わる文字列をすべて弾くと、
# 一時ファイルの `.md` を入力に渡すチェックスクリプトのテストまで弾く。追跡している `.md` と
# 同じ名前の一時ファイル（`README.md` など）を使うテストは当たるが、そのときも群が
# 取り消されるだけで、Pull Request に文言固定テストが残る側には倒れない（決定 9）。
#
# **動的に組み立てたパス（`glob` の結果など）は追わない。** 提案の基準とレビューが見る。

_STRING = re.compile(r"""[rRbBuUfF]{0,2}(["'])((?:\\.|(?!\1)[^\\])*)\1""")


def _names_markdown(literal: str, tracked: Iterable[str]) -> bool:
    """文字列が追跡している `.md` のパスか、`/` の区切りで揃えたその末尾に一致するか。"""
    if not literal.endswith(".md"):
        return False
    return any(p == literal or p.endswith("/" + literal) for p in tracked)


def _markdown_literals(line: str, tracked: list[str]) -> list[str]:
    return [m.group(2) for m in _STRING.finditer(line)
            if _names_markdown(m.group(2), tracked)]


def _added_lines(before: list[str], after: list[str]) -> list[str]:
    """変更の後にだけある行。置き換えた行も追加として数える。"""
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    return [line for tag, _, _, j1, j2 in matcher.get_opcodes()
            if tag in {"insert", "replace"} for line in after[j1:j2]]


def _parse(source: str) -> Optional[ast.Module]:
    try:
        return ast.parse(source)
    except (SyntaxError, ValueError):
        return None


def _markdown_constants(tree: ast.Module, tracked: list[str]) -> dict[str, str]:
    """モジュールの直下の代入のうち、右辺が追跡している `.md` を指す名前と、その文字列。"""
    found: dict[str, str] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        literal = next((
            c.value for c in ast.walk(value)
            if isinstance(c, ast.Constant) and isinstance(c.value, str)
            and _names_markdown(c.value, tracked)
        ), None)
        if literal is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = literal
    return found


def _helper_source(
    helper: str, changes: dict[str, tuple[list[str], list[str]]],
    work: Optional[str], sha: Optional[str],
) -> Optional[str]:
    """補助モジュールの変更の後の内容。群で触っていなければ git から読む。"""
    if helper in changes:
        return "".join(changes[helper][1])
    if not work or not sha:
        return None
    return git_out(work, ["show", f"{sha}:{helper}"], strip=False)


def _imported_constants(
    tree: ast.Module, test_path: str, tracked: list[str],
    changes: dict[str, tuple[list[str], list[str]]],
    work: Optional[str], sha: Optional[str],
) -> dict[str, str]:
    """同じディレクトリの補助モジュールから import した、`.md` を指す定数。"""
    found: dict[str, str] = {}
    folder = posixpath.dirname(test_path)
    for node in tree.body:
        if (not isinstance(node, ast.ImportFrom) or not node.module
                or "." in node.module or node.level > 1):
            continue
        source = _helper_source(
            posixpath.join(folder, f"{node.module}.py"), changes, work, sha)
        helper_tree = _parse(source) if source else None
        if helper_tree is None:
            continue
        constants = _markdown_constants(helper_tree, tracked)
        for alias in node.names:
            if alias.name in constants:
                found[alias.asname or alias.name] = constants[alias.name]
    return found


def _last_sha(facts: Iterable[dict[str, Any]]) -> Optional[str]:
    shas = [c.get("sha") for c in facts if c.get("exists", True) and c.get("sha")]
    return shas[-1] if shas else None


def doc_wording_tests(
    facts: Iterable[dict[str, Any]], tracked_md: Iterable[str],
    work: Optional[str] = None,
) -> list[tuple[str, str]]:
    """追加したテストの行が、追跡している `.md` を指していれば `(ファイル, 文字列)` を返す。

    当たりは、追加行の文字列リテラルと、テストのファイルの直下の定数・同じディレクトリの
    補助モジュールから import した定数のうち `.md` を指すものを、追加行が識別子として
    使う場合である。補助モジュールを群で触っていなければ、`work` の git から読む。
    """
    facts = list(facts)
    tracked = list(tracked_md)
    if not tracked:
        return []
    changes = collect_test_changes(facts)
    sha = _last_sha(facts)
    hits: list[tuple[str, str]] = []
    for path, (before, after) in sorted(changes.items()):
        added = _added_lines(before, after)
        found = {lit for line in added for lit in _markdown_literals(line, tracked)}
        tree = _parse("".join(after)) if path.endswith(".py") else None
        if tree is not None:
            names = {
                **_markdown_constants(tree, tracked),
                **_imported_constants(tree, path, tracked, changes, work, sha),
            }
            found.update(
                literal for name, literal in names.items()
                if any(re.search(rf"\b{re.escape(name)}\b", line) for line in added)
            )
        hits.extend((path, literal) for literal in sorted(found))
    return hits
