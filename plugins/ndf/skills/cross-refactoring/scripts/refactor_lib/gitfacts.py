"""git の出力だけを情報源にして、コミットとテストの事実を取る。

パスの判定・プロセス・GitHub・worktree・公開・結果ファイル・コミットのトレーラーは同じディレクトリの 7 本
（`pathkinds`・`process`・`github`・`worktree`・`publish`・`results`・`trailers`）が持つ。ここはそれらの名前を
再エクスポートし、`from .gitfacts import ...` で使う側の import を変えずに済ませる（#1142 の C4）。
"""

from __future__ import annotations

import pathlib
from typing import Any, Optional

from . import github, pathkinds, process, publish, results, trailers, worktree
from .paths import git_out, resolve_commit

# 分けた先の名前を再エクスポートする。`from .gitfacts import ...` で使う側の import を変えない。

check_run_result = github.check_run_result
resolved_threads_on_github = github.resolved_threads_on_github

CODE_EXTENSIONS = pathkinds.CODE_EXTENSIONS
TEST_NAME_MARKERS = pathkinds.TEST_NAME_MARKERS
TEST_PATH_MARKERS = pathkinds.TEST_PATH_MARKERS
is_test_path = pathkinds.is_test_path
is_test_path_in_versions = pathkinds.is_test_path_in_versions
lacks_python_tests = pathkinds.lacks_python_tests
python_text_lacks_tests = pathkinds.python_text_lacks_tests
production_code_changes = pathkinds.production_code_changes

run_test_at = process.run_test_at
run_with_timeout = process.run_with_timeout

_sync_generated = publish._sync_generated
_write_plan_file = publish._write_plan_file
credential_fallback_args = publish.credential_fallback_args
flush_pending_push = publish.flush_pending_push
gh_available = publish.gh_available
push_head = publish.push_head
push_with_retry_marker = publish.push_with_retry_marker

STOPPED_REASONS = results.STOPPED_REASONS
note_stopped = results.note_stopped
read_result = results.read_result
record_observed_model = results.record_observed_model

commit_trailers = trailers.commit_trailers

_dirty_paths = worktree._dirty_paths
_order_newest_first = worktree._order_newest_first
_worktree_changes = worktree._worktree_changes
discard_impl_leftovers = worktree.discard_impl_leftovers
replay_commits = worktree.replay_commits
reset_hard = worktree.reset_hard

# 実装担当は自分の成果を報告する側なので、結果ファイルの値をそのままチェックに使うと
# 「JSON を書き換えるだけで通る」チェックになる。ここは git だけを情報源にする。


def safe_int(value: Any, fallback: int = 0) -> int:
    """LLM が返した値を int にする。数値として読めなければ `fallback`。

    非数値の文字列・配列・辞書が返ってくることがあり、素の `int()` は
    `TypeError` / `ValueError` で落ちる。落とすと進行が止まるだけで、
    何の検証にもならない。
    """
    if isinstance(value, bool):
        return fallback
    if isinstance(value, (int, float)):
        return int(value)
    if isinstance(value, str):
        try:
            return int(value.strip())
        except ValueError:
            return fallback
    return fallback


def reported_shas(reported: Any) -> list[str]:
    """結果ファイルの `commits[]` から SHA を安全に取り出す。

    相手は LLM なので、`commits` が配列でない・要素が辞書でない・`sha` が
    文字列でないといった崩れ方をする。**壊れた形で落ちないことを型で保証しない。**
    ここで受け止めて、取り出せたものだけを返す。
    """
    if not isinstance(reported, dict):
        return []
    commits = reported.get("commits")
    if not isinstance(commits, list):
        return []
    shas: list[str] = []
    for c in commits:
        sha = c.get("sha") if isinstance(c, dict) else None
        if isinstance(sha, str) and sha.strip():
            shas.append(sha.strip())
    return shas


def commits_in_range(work: str, base: Optional[str], head: str) -> Optional[list[str]]:
    """`base..head` に含まれるコミットの完全な SHA を**新しい順**で返す。

    取得できなければ `None`。申告されたコミットが**実在し、このラウンドの範囲にある**
    ことを確かめるのと、範囲全体を取り消すときの順序に使う。

    **空リストと `None` を区別する。** 空リストは「1 件もコミットされていない」、
    `None` は「範囲を確定できなかった」である。混同すると、範囲を確定できないときに
    チェックが素通りしてしまう（過去の任意のコミットが実在扱いになる）。
    """
    if not base:
        return None
    out = git_out(work, ["rev-list", f"{base}..{head}"])
    return None if out is None else out.split()


def commit_diff_lines(work: str, sha: str) -> int:
    """コミットの追加 + 削除行数を git から数える。名前の変更は `-M` で 1 回に数える（記録だけ。#1814 決定 4）。"""
    out = git_out(work, ["show", "-M", "--numstat", "--format=", sha])
    total = 0
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        for n in parts[:2]:
            if n.isdigit():  # バイナリは `-` になるので数えない
                total += int(n)
    return total


def commit_files(work: str, sha: str) -> list[str]:
    """コミットが触ったファイルのリポジトリ相対パス。範囲のチェックに使う。"""
    out = git_out(work, ["show", "--name-only", "--format=", sha])
    return [p.strip() for p in (out or "").splitlines() if p.strip()]


def commit_file_status(work: str, sha: str) -> dict[str, str]:
    """コミットが触ったファイルごとの状態（`A` / `M` / `D` ほか）。名前の変更は元の `D` と先の `A` に分ける（`--no-renames`）。"""
    out = git_out(work, ["show", "--no-renames", "--name-status", "-z", "--format=", sha], strip=False)
    fields = [f for f in (out or "").strip("\n").split("\0") if f]
    return {path: status[:1] for status, path in zip(fields[0::2], fields[1::2])}


def commit_hunks(work: str, sha: str, path: str) -> list[tuple[list[str], list[str]]]:
    """コミットのファイル 1 つの `-U0` のハンクを `(削除の行, 追加の行)` の並びで返す（#1814 の呼び手の書き換えの判定）。"""
    out = git_out(work, ["show", "-U0", "--no-renames", "--format=", sha, "--", path], strip=False)
    hunks: list[tuple[list[str], list[str]]] = []
    for line in (out or "").splitlines():
        if line.startswith("@@"):
            hunks.append(([], []))
        elif hunks and line.startswith("-"):
            hunks[-1][0].append(line[1:])
        elif hunks and line.startswith("+"):
            hunks[-1][1].append(line[1:])
    return hunks


def path_in_tree(work: str, sha: str, path: str) -> bool:
    """コミット `sha` のツリーにパスがあるか。"""
    return git_out(work, ["cat-file", "-e", f"{sha}:{path}"]) is not None


def changed_files(work: str, shas: list[str]) -> list[str]:
    """コミットの並びが変えたファイルのうち、worktree に残るもの（静的解析の範囲テストの範囲。#1483 I10）。"""
    out: list[str] = []
    for sha in shas:
        for f in commit_files(work, sha):
            if f not in out and (pathlib.Path(work) / f).is_file():
                out.append(f)
    return out


def commit_test_changes(work: str, sha: str) -> dict[str, tuple[list[str], list[str]]]:
    """コミットが触ったテストの、変更前後の行をファイルごとに返す。

    **検証がテストの期待値を見るために要る**（#443）。差分ではなく前後の行を返すのは、
    判定が `assert` の行の集合を突き合わせる形だからである。テストかどうかは名前の形と、読んだ前後の版の
    中身で決める（`is_test_path_in_versions`）。作業ディレクトリの中身は読まない。テストの関数を消すコミットを
    取りこぼさないためである（#2516 決定 3）。版はファイルごとに 1 回ずつだけ読む。
    """
    out = git_out(work, ["show", "--name-only", "--format=", sha])
    changes: dict[str, tuple[list[str], list[str]]] = {}
    for path in (out or "").splitlines():
        if not path.strip() or not is_test_path(path):
            continue
        before = git_out(work, ["show", f"{sha}^:{path}"])
        after = git_out(work, ["show", f"{sha}:{path}"])
        if not is_test_path_in_versions(path, before, after):
            continue
        changes[path] = ((before or "").splitlines(keepends=True), (after or "").splitlines(keepends=True))
    return changes


def tracked_markdown(work: str) -> list[str]:
    """追跡している `.md` のリポジトリ相対パス（#723）。

    `-z` で読む。既定の出力は ASCII 以外を含むパスを引用符と 8 進数で書き換える。
    パターン `*.md` は `/` をまたいで一致し、下の階層の `.md` も拾う。
    """
    out = git_out(work, ["ls-files", "-z", "*.md"], strip=False)
    return [p for p in (out or "").split("\0") if p]


def commit_touches_tests(test_changes: dict[str, tuple[list[str], list[str]]]) -> bool:
    """コミットがテストを触っているか。`commit_test_changes` の結果から出し、git を起動しない（#2516 決定 3）。"""
    return bool(test_changes)


def commit_time(work: str, sha: str) -> Optional[str]:
    """コミットの時刻（コミッターの時刻、ISO 8601）。読めなければ `None`。

    **項目の所要と締め切りの判定はこの時刻で行う**（決定 8・決定 12）。担当の申告を
    使わない。作成者の時刻は `--date` や rebase で過去へ戻せるため、コミットを作った
    時点を表すコミッターの時刻を採る。
    """
    return git_out(work, ["log", "-1", "--format=%cI", sha])


def collect_commit_facts(
    work: str,
    shas: list[str],
    in_range: set[str],
    test_command: str,
    head_branch: str,
    test_timeout: int = 0,
) -> list[dict[str, Any]]:
    """申告されたコミットについて、git と実際のテスト実行から事実を集める。

    `test_timeout` はテストコマンドを渡したときだけ使う（今の呼び出し元はどれも渡さない）。

    `in_range` は信頼できる起点から HEAD までのコミット集合。ここに無い SHA は
    `exists=False` として返す。実体が無いものにテストを走らせても意味がない。
    """
    facts: list[dict[str, Any]] = []
    for sha in shas:
        full = resolve_commit(work, sha)
        if full is None or full not in in_range:
            facts.append({"sha": sha, "exists": False})
            continue
        test_changes = commit_test_changes(work, full)
        facts.append(
            {
                "sha": sha,
                "exists": True,
                "trailers": commit_trailers(work, full),
                "diff_lines": commit_diff_lines(work, full),
                "files": commit_files(work, full),
                "touches_tests": commit_touches_tests(test_changes),
                # **テストの期待値が変わっていないかを検証が見る**（#443）。
                "test_changes": test_changes,
                # **テストコマンドが空なら走らせない。** 適用の検証は「適用そのものが
                # 通ったか」だけを見る。テストの合否は適用ラウンドの単位で
                # `verify-round` が 1 度だけ実行する（決定 3）。
                "test_status": run_test_at(work, full, test_command, head_branch, test_timeout) if test_command else "skipped",
            }
        )
    return facts
