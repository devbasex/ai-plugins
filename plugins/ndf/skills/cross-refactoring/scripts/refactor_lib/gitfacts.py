"""git の出力だけを情報源にして、コミットとテストの事実を取る。

パスの判定・プロセス・GitHub・worktree・公開・結果ファイルは同じディレクトリの 6 本
（`pathkinds`・`process`・`github`・`worktree`・`publish`・`results`）が持つ。ここはそれらの名前を
再エクスポートし、`from .gitfacts import ...` で使う側の import を変えずに済ませる（#1142 の C4）。
"""
from __future__ import annotations

import re
import subprocess
from typing import Any, Optional

from . import github, pathkinds, process, publish, results, worktree
from .paths import git_out

# 分けた先の名前を再エクスポートする。`from .gitfacts import ...` で使う側の import を変えない。

_REVIEW_THREADS_QUERY = github._REVIEW_THREADS_QUERY
_fetch_review_threads_page = github._fetch_review_threads_page
_gh_api_get = github._gh_api_get
check_run_result = github.check_run_result
resolved_threads_on_github = github.resolved_threads_on_github

CODE_EXTENSIONS = pathkinds.CODE_EXTENSIONS
TEST_NAME_MARKERS = pathkinds.TEST_NAME_MARKERS
TEST_PATH_MARKERS = pathkinds.TEST_PATH_MARKERS
_has_shebang = pathkinds._has_shebang
_is_code_path = pathkinds._is_code_path
is_test_path = pathkinds.is_test_path
production_code_changes = pathkinds.production_code_changes

_kill_process_group = process._kill_process_group
_process_group_alive = process._process_group_alive
run_test_at = process.run_test_at
run_with_timeout = process.run_with_timeout

_CREDENTIAL_LIB = publish._CREDENTIAL_LIB
_commit_sync_changes = publish._commit_sync_changes
_publish_commit_message = publish._publish_commit_message
_push_with_credential_fallback = publish._push_with_credential_fallback
_run_sync_command = publish._run_sync_command
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

_control_prefix = worktree._control_prefix
_dirty_paths = worktree._dirty_paths
_discard_worktree_changes = worktree._discard_worktree_changes
_order_newest_first = worktree._order_newest_first
_require_clean_worktree = worktree._require_clean_worktree
_worktree_changes = worktree._worktree_changes
discard_impl_leftovers = worktree.discard_impl_leftovers
replay_commits = worktree.replay_commits
reset_hard = worktree.reset_hard
revert_item_commits = worktree.revert_item_commits
revert_range = worktree.revert_range

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


def commit_trailers(work: str, sha: str) -> dict[str, str]:
    """コミットメッセージのトレーラーを git から読む。

    **結果ファイルの `trailers` は使わない。** JSON 上は仕様どおりでも、実際の
    `git commit` でトレーラーを書き忘れていれば集計に使えない。

    **末尾の段落から前へ 1 段落ずつ読む**（#553）。実行環境が帰属の段落を後ろへ
    足すと、git の標準の読み方は最後の段落しか見ないため必須の記名が読めなくなる。
    トレーラーの段落と判定しなかった段落で止めるので、散文の中にある記名の形の行は
    拾わない。同じ鍵が 2 つの段落にあれば、末尾に近い段落の値を採る。

    **1 段落目（題名）は掛けない。** 掛けると `Round: 本文の題名` の形の題名を
    トレーラーとして読む。
    """
    body = git_out(work, ["log", "-1", "--format=%B", sha], strip=False)
    paragraphs = re.split(r"\n[ \t]*\n", (body or "").strip("\n"))
    trailers: dict[str, str] = {}
    for paragraph in reversed(paragraphs[1:]):
        parsed = _parse_trailer_paragraph(paragraph)
        if not parsed:
            break
        for key, value in parsed.items():
            trailers.setdefault(key, value)
    return trailers


def _parse_trailer_paragraph(paragraph: str) -> dict[str, str]:
    """1 つの段落を git の判定に掛け、トレーラーの段落なら鍵と値を返す。

    **題名の行を補って渡す。** git はメッセージの 1 行目を題名として読むため、
    段落だけを渡すと何も返らない（git 2.53.0 で実測）。判定そのものは git に委ね、
    「何行以上なら記名の段落か」といった規則をこちら側に持たない。
    """
    if not paragraph.strip():
        return {}
    result = subprocess.run(
        ["git", "interpret-trailers", "--parse"],
        input=f"subject\n\n{paragraph}\n",
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        return {}
    parsed: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, sep, value = line.partition(":")
        if sep:
            parsed[key.strip()] = value.strip()
    return parsed


def commit_diff_lines(work: str, sha: str) -> int:
    """コミットの追加 + 削除行数を git から数える。"""
    out = git_out(work, ["show", "--numstat", "--format=", sha])
    total = 0
    for line in (out or "").splitlines():
        parts = line.split("\t")
        if len(parts) < 3:
            continue
        for n in parts[:2]:
            if n.isdigit():          # バイナリは `-` になるので数えない
                total += int(n)
    return total


def commit_files(work: str, sha: str) -> list[str]:
    """コミットが触ったファイルのリポジトリ相対パス。範囲のチェックに使う。"""
    out = git_out(work, ["show", "--name-only", "--format=", sha])
    return [p.strip() for p in (out or "").splitlines() if p.strip()]


def commit_test_changes(work: str, sha: str) -> dict[str, tuple[list[str], list[str]]]:
    """コミットが触ったテストの、変更前後の行をファイルごとに返す。

    **検証がテストの期待値を見るために要る**（#443）。差分ではなく前後の行を返すのは、
    判定が `assert` の行の集合を突き合わせる形だからである。
    """
    out = git_out(work, ["show", "--name-only", "--format=", sha])
    changes: dict[str, tuple[list[str], list[str]]] = {}
    for path in (out or "").splitlines():
        if not path.strip() or not is_test_path(path):
            continue
        before = git_out(work, ["show", f"{sha}^:{path}"]) or ""
        after = git_out(work, ["show", f"{sha}:{path}"]) or ""
        changes[path] = (before.splitlines(keepends=True),
                         after.splitlines(keepends=True))
    return changes


def tracked_markdown(work: str) -> list[str]:
    """追跡している `.md` のリポジトリ相対パス（#723）。

    `-z` で読む。既定の出力は ASCII 以外を含むパスを引用符と 8 進数で書き換える。
    パターン `*.md` は `/` をまたいで一致し、下の階層の `.md` も拾う。
    """
    out = git_out(work, ["ls-files", "-z", "*.md"], strip=False)
    return [p for p in (out or "").split("\0") if p]


def commit_touches_tests(work: str, sha: str) -> bool:
    """コミットがテストの置き場所を触っているか。"""
    return any(is_test_path(p) for p in commit_files(work, sha))


def commit_time(work: str, sha: str) -> Optional[str]:
    """コミットの時刻（コミッターの時刻、ISO 8601）。読めなければ `None`。

    **項目の所要と締め切りの判定はこの時刻で行う**（決定 8・決定 12）。担当の申告を
    使わない。作成者の時刻は `--date` や rebase で過去へ戻せるため、コミットを作った
    時点を表すコミッターの時刻を採る。
    """
    return git_out(work, ["log", "-1", "--format=%cI", sha])


def collect_commit_facts(
    work: str, shas: list[str], in_range: set[str], test_command: str,
    head_branch: str, test_timeout: int = 0,
) -> list[dict[str, Any]]:
    """申告されたコミットについて、git と実際のテスト実行から事実を集める。

    `test_timeout` はテストコマンドを渡したときだけ使う（今の呼び出し元はどれも渡さない）。

    `in_range` は信頼できる起点から HEAD までのコミット集合。ここに無い SHA は
    `exists=False` として返す。実体が無いものにテストを走らせても意味がない。
    """
    facts: list[dict[str, Any]] = []
    for sha in shas:
        full = git_out(work, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
        if full is None or full not in in_range:
            facts.append({"sha": sha, "exists": False})
            continue
        facts.append({
            "sha": sha,
            "exists": True,
            "trailers": commit_trailers(work, full),
            "diff_lines": commit_diff_lines(work, full),
            "files": commit_files(work, full),
            "touches_tests": commit_touches_tests(work, full),
            # **テストの期待値が変わっていないかを検証が見る**（#443）。
            "test_changes": commit_test_changes(work, full),
            # **テストコマンドが空なら走らせない。** 適用の検証は「適用そのものが
            # 通ったか」だけを見る。テストの合否は適用ラウンドの単位で
            # `verify-round` が 1 度だけ実行する（決定 3）。
            "test_status": run_test_at(
                work, full, test_command, head_branch, test_timeout
            ) if test_command else "skipped",
        })
    return facts
