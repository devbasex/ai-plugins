"""適用結果の検証。

コミットのトレーラーとテストの期待値を判定する。範囲の判定は `scope_check.judge_unit` が持つ（#1814 決定 11）。
判定に使う事実は git から取った値で、結果ファイルの申告は使わない。
"""

from __future__ import annotations

import ast
import difflib
import posixpath
import re

from typing import Any, Iterable, Optional

from .paths import git_out, resolve_commit
from .vocabulary import FINAL_FIX_TRAILERS, REQUIRED_TRAILERS


# **結果ファイルの申告は検証の材料にしない。** 実装担当は自分の成果を報告する側なので、
# トレーラーもテスト結果も差分行数も、JSON の値を書き換えるだけでチェックを通せてしまう。
# ここで使う事実（コミットの実在 / トレーラー / 差分行数 / テストの成否）は、すべて
# **git と実際のテスト実行**から取る。結果ファイルから使うのは「どのコミットが
# どの項目のものか」という対応付けの手がかりだけである。


def verify_commit_trailers(commit: dict[str, Any], required: Iterable[str] = REQUIRED_TRAILERS) -> Optional[str]:
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
    missing_reason: str,
    check_test: bool = True,
    required_trailers: Iterable[str] = REQUIRED_TRAILERS,
) -> Optional[str]:
    """コミット 1 件が手順を満たしているかを検証する。問題があれば理由を返す。

    実装・テストの追加（`commands/implement.py`）と修正（`commands/converge.py`）で
    **同じ基準**を使う。
    片方だけ直されると基準が食い違い、緩い側から手順を外れた変更が入る。範囲は単位（コミットの組）で
    `scope_check.judge_unit` が見る（#1814 決定 11）。

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
    if check_test and commit.get("test_status") != "pass":
        return f"コミット {commit.get('sha', '?')} でテストが成功していません ({commit.get('test_status')})"
    return None


def verify_final_fix_commit(commit: dict[str, Any]) -> Optional[str]:
    """最終ゲート（Step 7）の修正コミットを検証する。問題があれば理由を返す。

    実装と修正の取り込みと**見る先が 2 つだけ違う**。

    - **テストの合否を見ない**（`check_test=False`）。`--ci-check` を指定した実行では
      手元のテストを 1 度も走らせないと決めてある（#436 決定 11 の排他）。ここで
      コミットごとに走らせると、その排他をこの経路だけが破ることになる。合否は
      直後の `final-gate` が採った側（手元のテスト / 継続的統合）で 1 度だけ見る。
    - **`Item-Id` を求めない**。最終ゲートの失敗は全体のテストのもので、
      どの改善項目にも紐づかない。

    **対象範囲は見る。** 最終ゲートでも `--scope` の外を触ってよい理由は無い。範囲は修正の全コミットを
    1 つの単位として `scope_check.judge_unit` が見る（#1814 決定 11）。
    """
    return verify_commit_basics(
        commit,
        f"コミット {commit.get('sha', '?')} が最終ゲートの修正の範囲に存在しません",
        check_test=False,
        required_trailers=FINAL_FIX_TRAILERS,
    )


def unassigned_fix_commits(work: str, reported_shas: list[str], ordered_range: list[str]) -> list[str]:
    """範囲内のコミットのうち、どの申告にも含まれていないものを返す。

    適用と同じく、**範囲のコミットは全て申告されていること**を求める。
    申告から漏れた修正コミットは検証を受けないまま Pull Request に残る。
    """
    reported_full = {full for full in (resolve_commit(work, s) for s in reported_shas) if full}
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

# 行末までのコメントの始まり。行頭か空白の後の `#` だけを指す（`this.#x` などは含めない）。
_COMMENT = r"(?:^|(?<=\s))#"

# 値かコメントの始まりを、行の左から順に拾う。**文字列の中の `#` は文字列の側が先に取る。**
_TOKEN = re.compile(rf"(?P<value>{_LITERAL.pattern})|(?P<comment>{_COMMENT})")


def _values(lines: Iterable[str]) -> set[str]:
    """差分の中に現れる値の集まりを返す。**`assert` の行に限らない。**

    - **コメントの中は数えない**（#641）。項目 ID などを書き換えただけで値が失われたことにしない
    - **件数は数えない**（#705）。同じ値を定数へ寄せて出現が減っても、値は失われていない
    """
    found: set[str] = set()
    for line in lines:
        for match in _TOKEN.finditer(line):
            if match.group("comment") is not None:
                break
            found.add(match.group("value"))
    return found


def assertion_change(before: Iterable[str], after: Iterable[str]) -> str:
    """テストの変更の種類を返す。

    | 戻り値 | 意味 | 判定 |
    | --- | --- | --- |
    | `unchanged` | 変わっていない | 前後が同一 |
    | `changed` | 期待出力が変わった | **前にあった値が、後のファイルのどこにも無い**（コメントを除く） |
    | `undecidable` | **機械では決まらない** | それ以外すべて |

    **`unchanged` は「同じ」のときだけ返す。** 経路だけの変更も、行の並べ替えも、
    テストの追加も、機械では期待出力への影響を否定できない。最終ゲートのレビューが読む。

    **値の出現が減っただけでは `changed` にしない。** 重複を定数へ寄せた変更（#705）も、
    内部の参照を確かめる `assert` を消した変更（#641）も、値が残っていれば最終ゲートのレビューが読む。
    """
    rows_before, rows_after = list(before), list(after)
    if rows_before == rows_after:
        return "unchanged"
    if _values(rows_before) - _values(rows_after):
        return "changed"
    return "undecidable"


def _lost_values(changes: dict[str, tuple[list[str], list[str]]]) -> set[str]:
    """単位が変えたテストのファイルの和集合で、前にあった値のうち後のどこにも無いもの（#1814 決定 5）。"""
    before = set().union(*(_values(b) for b, _ in changes.values())) if changes else set()
    after = set().union(*(_values(a) for _, a in changes.values())) if changes else set()
    return before - after


def _changed_paths(changes: dict[str, tuple[list[str], list[str]]]) -> list[str]:
    """和集合で消えた値を前に持っていたファイル。**値を別のテストのファイルへ移しただけなら空。**"""
    lost = _lost_values(changes)
    if not lost:
        return []
    return sorted(path for path, (before, _) in changes.items() if _values(before) & lost)


def undecidable_test_changes(
    changes: dict[str, tuple[list[str], list[str]]],
) -> list[str]:
    """機械では判定できないテストの差分を、ファイルの順で返す。

    ファイルごとには値が消えていても、単位の和集合で値が残っているもの（値を補助のファイルへ移したなど）も含める。
    **呼ぶ側はこれを最終ゲートのレビューへ引き継ぐ。** 空でないまま通さない。
    """
    changed = set(_changed_paths(changes))
    return sorted(
        path for path, (before, after) in changes.items() if path not in changed and assertion_change(before, after) != "unchanged"
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
    """コミット単位の `test_changes` を 1 つの辞書へまとめる（`facts` は古い順）。

    前はそのファイルを最初に触ったコミットの前、後は最後に触ったコミットの後を採る（#1814 決定 5）。
    """
    changes: dict[str, tuple[list[str], list[str]]] = {}
    for commit in facts:
        for path, (before, after) in (commit.get("test_changes") or {}).items():
            first = changes.get(path, (before, after))[0]
            changes[path] = (first, after)
    return changes


def verify_test_changes(
    changes: dict[str, tuple[list[str], list[str]]],
) -> Optional[str]:
    """テストの差分に、期待値の変更が含まれていないかを見る。

    値の消失は単位（項目の全コミット）が変えたテストのファイルの**和集合**で見る（#1814 決定 5）。
    テストの値を同じ単位で変えた・作った別のテストのファイルへ移しただけなら取り消さない。
    **判定できないものはここでは落とさない。** `undecidable_test_changes` が集め、
    呼ぶ側がレビューへ引き継ぐ。
    """
    changed = _changed_paths(changes)
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
    return [m.group(2) for m in _STRING.finditer(line) if _names_markdown(m.group(2), tracked)]


def _added_lines(before: list[str], after: list[str]) -> list[str]:
    """変更の後にだけある行。置き換えた行も追加として数える。"""
    matcher = difflib.SequenceMatcher(None, before, after, autojunk=False)
    return [line for tag, _, _, j1, j2 in matcher.get_opcodes() if tag in {"insert", "replace"} for line in after[j1:j2]]


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
        literal = next(
            (
                c.value
                for c in ast.walk(value)
                if isinstance(c, ast.Constant) and isinstance(c.value, str) and _names_markdown(c.value, tracked)
            ),
            None,
        )
        if literal is None:
            continue
        for target in targets:
            if isinstance(target, ast.Name):
                found[target.id] = literal
    return found


def _helper_source(
    helper: str,
    changes: dict[str, tuple[list[str], list[str]]],
    work: Optional[str],
    sha: Optional[str],
) -> Optional[str]:
    """補助モジュールの変更の後の内容。群で触っていなければ git から読む。"""
    if helper in changes:
        return "".join(changes[helper][1])
    if not work or not sha:
        return None
    return git_out(work, ["show", f"{sha}:{helper}"], strip=False)


def _imported_constants(
    tree: ast.Module,
    test_path: str,
    tracked: list[str],
    changes: dict[str, tuple[list[str], list[str]]],
    work: Optional[str],
    sha: Optional[str],
) -> dict[str, str]:
    """同じディレクトリの補助モジュールから import した、`.md` を指す定数。"""
    found: dict[str, str] = {}
    folder = posixpath.dirname(test_path)
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom) or not node.module or "." in node.module or node.level > 1:
            continue
        source = _helper_source(posixpath.join(folder, f"{node.module}.py"), changes, work, sha)
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
    facts: Iterable[dict[str, Any]],
    tracked_md: Iterable[str],
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
        found = _direct_references(added, tracked)
        found.update(_constant_references(path, after, added, tracked, changes, work, sha))
        hits.extend((path, literal) for literal in sorted(found))
    return hits


def _direct_references(added: list[str], tracked: list[str]) -> set[str]:
    """追加行の文字列リテラルのうち、追跡している `.md` を指すもの。"""
    return {lit for line in added for lit in _markdown_literals(line, tracked)}


def _constant_references(
    path: str,
    after: list[str],
    added: list[str],
    tracked: list[str],
    changes: dict[str, tuple[list[str], list[str]]],
    work: Optional[str],
    sha: Optional[str],
) -> set[str]:
    """Python のテストで、`.md` を指す定数（直下の定数と補助モジュールの import）を追加行が識別子として使うもの。"""
    tree = _parse("".join(after)) if path.endswith(".py") else None
    if tree is None:
        return set()
    names = {
        **_markdown_constants(tree, tracked),
        **_imported_constants(tree, path, tracked, changes, work, sha),
    }
    return {literal for name, literal in names.items() if any(re.search(rf"\b{re.escape(name)}\b", line) for line in added)}
