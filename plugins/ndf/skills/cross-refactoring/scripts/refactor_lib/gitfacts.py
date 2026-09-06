"""git の出力だけを情報源にして、コミットとテストの事実を取る。

取り消し・作業ツリーの掃除・生成物の同期・公開も、git を触る操作として同居する。
"""
from __future__ import annotations

import json
import os
import pathlib
import signal
import subprocess
import time
from typing import Any, Optional

import models as models_lib
import statefile

from . import die, info
from .paths import _sh, stem_for
from .plan import format_plan, normalize_plan_file, publish_plan_comment
from .vocabulary import (
    DEFAULT_TEST_TIMEOUT,
    PLAN_COMMIT_MESSAGE,
    SYNC_AND_PLAN_COMMIT_MESSAGE,
    SYNC_COMMIT_MESSAGE,
)


# 実装担当は自分の成果を報告する側なので、結果ファイルの値をそのまま検査に使うと
# 「JSON を書き換えるだけで通る」検査になる。ここは git だけを情報源にする。

# テストの置き場所。現状固定テストが先行しているかの判定に使う。
TEST_PATH_MARKERS = ("/test/", "/tests/", "/spec/", "/specs/", "__tests__/")
TEST_NAME_MARKERS = (".test.", ".spec.", "_test.", "_spec.", "test_", "spec_")


def _safe_int(value: Any, fallback: int = 0) -> int:
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


def _reported_shas(reported: Any) -> list[str]:
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


def _git_out(work: str, args: list[str], strip: bool = True) -> Optional[str]:
    """`git` を実行して標準出力を返す。失敗したら `None`。

    **固定幅で読む出力には `strip=False` を渡す。** `git status --porcelain` の
    状態コードは未 stage の変更で ` M` と先頭が空白になるため、`strip()` すると
    1 行目だけ 1 文字ずれ、切り出したパスの先頭が欠ける。欠けたパスは
    `git add` で `pathspec ... did not match any files` になり、同期が止まる。
    """
    r = subprocess.run(["git", *args], cwd=work, capture_output=True, text=True)
    if r.returncode != 0:
        return None
    return r.stdout.strip() if strip else r.stdout.rstrip("\n")


def commits_in_range(work: str, base: Optional[str], head: str) -> Optional[list[str]]:
    """`base..head` に含まれるコミットの完全な SHA を**新しい順**で返す。

    取得できなければ `None`。申告されたコミットが**実在し、このラウンドの範囲にある**
    ことを確かめるのと、範囲全体を取り消すときの順序に使う。

    **空リストと `None` を区別する。** 空リストは「1 件もコミットされていない」、
    `None` は「範囲を確定できなかった」である。混同すると、範囲を確定できないときに
    検査が素通りしてしまう（過去の任意のコミットが実在扱いになる）。
    """
    if not base:
        return None
    out = _git_out(work, ["rev-list", f"{base}..{head}"])
    return None if out is None else out.split()


def commit_trailers(work: str, sha: str) -> dict[str, str]:
    """コミットメッセージのトレーラーを git から読む。

    **結果ファイルの `trailers` は使わない。** JSON 上は仕様どおりでも、実際の
    `git commit` でトレーラーを書き忘れていれば集計に使えない。
    """
    out = _git_out(work, ["log", "-1", "--format=%(trailers:only,unfold)", sha])
    trailers: dict[str, str] = {}
    for line in (out or "").splitlines():
        key, sep, value = line.partition(":")
        if sep:
            trailers[key.strip()] = value.strip()
    return trailers


def commit_diff_lines(work: str, sha: str) -> int:
    """コミットの追加 + 削除行数を git から数える。"""
    out = _git_out(work, ["show", "--numstat", "--format=", sha])
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
    """コミットが触ったファイルのリポジトリ相対パス。範囲の検査に使う。"""
    out = _git_out(work, ["show", "--name-only", "--format=", sha])
    return [p.strip() for p in (out or "").splitlines() if p.strip()]


def commit_touches_tests(work: str, sha: str) -> bool:
    """コミットがテストの置き場所を触っているか。"""
    out = _git_out(work, ["show", "--name-only", "--format=", sha])
    for path in (out or "").splitlines():
        lowered = f"/{path.lower()}"
        name = lowered.rsplit("/", 1)[-1]
        if any(m in lowered for m in TEST_PATH_MARKERS):
            return True
        if any(m in name for m in TEST_NAME_MARKERS):
            return True
    return False


def _run_with_timeout(
    command: str, cwd: str, timeout: int, kill_grace: float = 5.0
) -> tuple[Optional[int], bool]:
    """テストコマンドを実行し `(終了コード, 打ち切ったか)` を返す。

    **新しいプロセスグループで起動し、打ち切るときはグループごと止める。**
    `shell=True` のまま `subprocess.run(timeout=...)` を使うと、終了するのは
    シェルだけで、pytest などの子プロセスは走り続ける。残ったプロセスは同じ
    作業ディレクトリを書き換え続けるため、直後の `git checkout` と競合する。
    """
    proc = subprocess.Popen(
        command, shell=True, cwd=cwd, start_new_session=True,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    try:
        proc.communicate(timeout=timeout)
        return proc.returncode, False
    except subprocess.TimeoutExpired:
        _kill_process_group(proc, kill_grace)
        # 出力はもう使わない。**パイプを閉じてから**待つ。開いたままだと、
        # パイプを継承した子が残っている限り EOF が来ず、ここで止まる。
        for pipe in (proc.stdout, proc.stderr):
            if pipe is not None:
                pipe.close()
        try:
            proc.wait(timeout=kill_grace)
        except subprocess.TimeoutExpired:
            proc.kill()
        return None, True


def _process_group_alive(pgid: int) -> bool:
    """プロセスグループに生きたプロセスが残っているか。"""
    try:
        os.killpg(pgid, 0)
        return True
    except ProcessLookupError:
        return False
    except (PermissionError, OSError):
        # 判断できないときは「残っている」側に倒す（SIGKILL まで進める）。
        return True


def _kill_process_group(
    proc: "subprocess.Popen[bytes]", grace: float = 5.0
) -> None:
    """プロセスグループごと止める。SIGTERM のあと、残っていれば SIGKILL。

    **親シェルの終了で打ち切らない。** 親が終わっても、SIGTERM を無視する子は
    グループに残って作業ディレクトリを書き換え続ける。判定は必ず
    **グループの存否**で行う。
    """
    try:
        pgid = os.getpgid(proc.pid)
    except (ProcessLookupError, PermissionError, OSError):
        proc.kill()
        return
    try:
        os.killpg(pgid, signal.SIGTERM)
    except ProcessLookupError:
        return
    except (PermissionError, OSError):
        proc.kill()
        return

    deadline = time.monotonic() + grace
    while time.monotonic() < deadline:
        if not _process_group_alive(pgid):
            return
        time.sleep(0.2)

    try:
        os.killpg(pgid, signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        pass


def run_test_at(
    work: str, sha: str, command: str, head_branch: str,
    timeout: int = DEFAULT_TEST_TIMEOUT, kill_grace: float = 5.0,
) -> str:
    """指定コミットを取り出してテストを実行し `pass` / `fail` を返す。

    **各コミットでテストが通ったかは、実際に走らせないと分からない。**
    結果ファイルの `test_status` は実装担当の申告にすぎず、検査の根拠にできない。
    実行後は必ず元のブランチへ戻す。

    上限時間を超えたら `fail` とする。生成されたコードやテストが無限ループに入ると、
    待ち続けて進行全体が止まるためで、通す側には倒さない。
    """
    if _git_out(work, ["checkout", "--detach", sha]) is None:
        return "missing"
    try:
        code, timed_out = _run_with_timeout(command, work, timeout, kill_grace)
        if timed_out:
            info(f"⚠ コミット {sha[:7]} のテストが {timeout} 秒で終わりませんでした")
            return "fail"
        return "pass" if code == 0 else "fail"
    finally:
        subprocess.run(
            ["git", "checkout", head_branch], cwd=work, capture_output=True, text=True
        )


def collect_commit_facts(
    work: str, shas: list[str], in_range: set[str], test_command: str,
    head_branch: str, test_timeout: int = DEFAULT_TEST_TIMEOUT,
) -> list[dict[str, Any]]:
    """申告されたコミットについて、git と実際のテスト実行から事実を集める。

    `in_range` は信頼できる起点から HEAD までのコミット集合。ここに無い SHA は
    `exists=False` として返す。実体が無いものにテストを走らせても意味がない。
    """
    facts: list[dict[str, Any]] = []
    for sha in shas:
        full = _git_out(work, ["rev-parse", "--verify", f"{sha}^{{commit}}"])
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
            # **テストコマンドが空なら走らせない。** 適用の検証は「適用そのものが
            # 通ったか」だけを見る。テストの合否は適用ラウンドの単位で
            # `verify-round` が 1 度だけ実行する（決定 3）。
            "test_status": run_test_at(
                work, full, test_command, head_branch, test_timeout
            ) if test_command else "skipped",
        })
    return facts


_REVIEW_THREADS_QUERY = """
query($owner: String!, $repo: String!, $pr: Int!, $cursor: String) {
  repository(owner: $owner, name: $repo) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $cursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved }
      }
    }
  }
}
"""


def resolved_threads_on_github(repo: str, pr: int) -> Optional[set[str]]:
    """GitHub 上で実際に解決済みのレビュースレッド ID を返す。

    取得できなければ `None` を返す。呼び出し側は**空集合と区別する**こと。
    「取得できなかった」を「解決済みが 0 件」と混同すると、通信が失敗しただけで
    全ての指摘を未解決扱いにするか、逆に自己申告を素通しすることになる。
    """
    owner, _, name = repo.partition("/")
    if not owner or not name:
        return None
    resolved: set[str] = set()
    cursor: Optional[str] = None
    while True:
        cmd = [
            "gh", "api", "graphql",
            "-f", f"query={_REVIEW_THREADS_QUERY}",
            "-F", f"owner={owner}", "-F", f"repo={name}", "-F", f"pr={pr}",
        ]
        if cursor:
            cmd += ["-F", f"cursor={cursor}"]
        r = subprocess.run(cmd, capture_output=True, text=True)
        if r.returncode != 0:
            info(f"⚠ レビュースレッドの取得に失敗しました: {r.stderr.strip()[:200]}")
            return None
        try:
            threads = (
                json.loads(r.stdout)["data"]["repository"]["pullRequest"]["reviewThreads"]
            )
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            info(f"⚠ レビュースレッドの応答を解釈できませんでした: {e}")
            return None
        resolved.update(
            n["id"] for n in threads.get("nodes", []) if n.get("isResolved")
        )
        page = threads.get("pageInfo") or {}
        if not page.get("hasNextPage"):
            return resolved
        cursor = page.get("endCursor")
        if not cursor:
            return resolved


# 継続的統合の照会は `commits/{sha}/check-runs` の 1 回だけにする。**併記された状態
# （`commits/{sha}/status`）は使わない。** GitHub Actions は検査ジョブを記録し commit の
# 状態を記録しないため、すべて成功した commit でも `pending` を返す。保留として読むと、
# 通っている検査で通過できなくなる。
CHECK_RUNS_PER_PAGE = 100


def check_run_result(repo: str, sha: str, name: str) -> Optional[str]:
    """名前が一致した検査ジョブの結果を 1 つの語で返す。

    - すべて完了して結論が `success` なら `"success"`
    - 未完了があれば `"pending"`
    - それ以外は最初に見つけた失敗の結論（`"failure"` など）
    - **照会できない・名前が一致する検査が 1 件も無いときは `None`**

    **「照会できなかった」と「成功した」を区別する。** 呼び出し側は `None` を
    通過させない（fail-closed）。名前で絞るのは、別の検査の成功で通さないためである。
    """
    if not repo or not sha or not name:
        return None
    out = _sh(
        ["gh", "api", f"repos/{repo}/commits/{sha}/check-runs"
                      f"?per_page={CHECK_RUNS_PER_PAGE}"],
        check=False,
    )
    if not out:
        return None
    try:
        body = json.loads(out)
    except json.JSONDecodeError:
        return None
    runs = body.get("check_runs") if isinstance(body, dict) else None
    if not isinstance(runs, list):
        return None
    matched = [
        r for r in runs
        if isinstance(r, dict) and str(r.get("name") or "") == name
    ]
    if not matched:
        return None
    if any(str(r.get("status") or "").lower() != "completed" for r in matched):
        return "pending"
    for run in matched:
        conclusion = str(run.get("conclusion") or "").lower()
        if conclusion != "success":
            return conclusion or "unknown"
    return "success"


def _revert_item_commits(
    state: dict[str, Any], item: dict[str, Any], dry_run: bool = False
) -> int:
    """改善項目のコミットを取り消し、取り消した件数を返す。

    **新しいコミットから順に戻す。** 逆順にすると後続の取り消しが競合する。
    取り消しに失敗したら中断する。半端な状態を Pull Request に残さない。

    適用の検証に失敗したときと、レビューが収束しなかったときの両方から呼ぶ。
    前者で呼ばないと、実装担当が既に push した差分が Pull Request に残り、
    以後のレビュー対象にも混入する。
    """
    # **取り消し済みなら何もしない。** push の失敗などで叩き直したときに、
    # 既に戻したコミットへもう一度 `git revert` を掛けると必ず失敗し、
    # そこから先へ進めなくなる。
    if item.get("reverted"):
        info(f"↩ {item['item_id']} は取り消し済みです")
        return 0

    work = state["worktrees"]["work"]
    shas = _order_newest_first(
        work, [s for s in (item.get("commits") or []) if isinstance(s, str) and s]
    )
    if dry_run:
        for sha in shas:
            info(f"（dry-run）git revert --no-edit {sha}")
        return len(shas)

    # 途中で失敗したら**着手前の HEAD まで戻す**。1 項目が複数のコミットを持つとき、
    # 先行して成功した取り消しだけが履歴に残ると、再実行で不整合になって進めなくなる。
    before = _git_out(work, ["rev-parse", "HEAD"])
    for sha in shas:
        r = subprocess.run(
            ["git", "revert", "--no-edit", sha],
            cwd=work, capture_output=True, text=True,
        )
        if r.returncode != 0:
            subprocess.run(["git", "revert", "--abort"], cwd=work,
                           capture_output=True, text=True)
            if before:
                subprocess.run(["git", "reset", "--hard", before], cwd=work,
                               capture_output=True, text=True)
            die(
                f"{item['item_id']} のコミット {sha} を取り消せませんでした: "
                f"{r.stderr.strip()[:400]}"
                f"（HEAD を {before} へ戻しました）"
            )
    item["reverted"] = True
    return len(shas)


def revert_unverified_range(
    path: pathlib.Path,
    state: dict[str, Any],
    entry: dict[str, Any],
    ordered_range: list[str],
    label: str,
) -> None:
    """検証を通らない範囲を取り消し、`entry` の起点を取り消し後の HEAD へ進める。

    `entry` は**修正の控えを持つ辞書**である。適用ラウンドの控え（`rounds[]` の
    要素）と最終ゲートの控え（`final_gate`）の両方が同じ 3 つの鍵
    （`pending_push` / `fix_base_sha`）を持つため、どちらからも呼べる。
    `label` は取り消しの単位を人が読むための名前で、git の操作には効かない。
    """
    work = state["worktrees"]["work"]
    # **状態へ記録する前に取り消す。** 先に記録すると、取り消し済みのコミットが
    # 状態ファイルに残り、後の見送り処理が同じコミットをもう一度取り消そうとする。
    info("検証を通らない変更を残さないため、この修正ラウンドの範囲を取り消します")
    # **取り消しへ着手する前に印を立てる。** 取り消しは済んだのに push できずに
    # 終わると、未検証の変更が Pull Request に残ったままになる。
    entry["pending_push"] = True
    statefile.save(path, state)
    _revert_item_commits(
        state,
        {"item_id": label, "commits": list(ordered_range)},
        dry_run=False,
    )
    # 取り消し後の状態を新しい起点にし、**その場で保存する**。ここで保存せずに
    # 落ちると、次の実行は古い起点から範囲を取り直して取り消しコミット自体を
    # 「未申告」と判定し、**取り消しを取り消して**しまう。
    entry["fix_base_sha"] = _git_out(work, ["rev-parse", "HEAD"])
    statefile.save(path, state)


def _reset_hard(work: str, sha: Optional[str]) -> None:
    """着手前の HEAD へ戻す。半端な履歴を Pull Request に残さないための後始末。"""
    if sha:
        subprocess.run(["git", "reset", "--hard", sha], cwd=work,
                       capture_output=True, text=True)


def _revert_range(work: str, ordered: list[str], before: Optional[str]) -> None:
    """範囲を**新しい順に**全て取り消す。失敗したら着手前へ戻して中断する。

    範囲全体を新しい順にたどる取り消しは、履歴をそのまま逆再生するだけなので
    **競合しない**。競合するのは「一部のコミットだけを飛ばして戻す」ときである。
    """
    for sha in ordered:
        r = subprocess.run(
            ["git", "revert", "--no-edit", sha],
            cwd=work, capture_output=True, text=True,
        )
        if r.returncode != 0:
            subprocess.run(["git", "revert", "--abort"], cwd=work,
                           capture_output=True, text=True)
            _reset_hard(work, before)
            die(
                f"コミット {sha} を取り消せませんでした: {r.stderr.strip()[:400]}"
                f"（HEAD を {before} へ戻しました）"
            )


def _replay_commits(work: str, shas: list[str]) -> Optional[dict[str, str]]:
    """残す項目のコミットを**古い順に**積み直し、`{元の SHA: 新しい SHA}` を返す。

    競合したら `None` を返す。**ここで中断しない。** どの項目を残せるか決められない
    だけなので、呼び出し側がラウンド全件の取り消しへ退避できる。
    """
    mapping: dict[str, str] = {}
    for sha in shas:
        r = subprocess.run(
            ["git", "cherry-pick", "--allow-empty", sha],
            cwd=work, capture_output=True, text=True,
        )
        if r.returncode != 0:
            subprocess.run(["git", "cherry-pick", "--abort"], cwd=work,
                           capture_output=True, text=True)
            info(f"⚠ {sha[:7]} を積み直せませんでした: {r.stderr.strip()[:200]}")
            return None
        mapping[sha] = _git_out(work, ["rev-parse", "HEAD"]) or sha
    return mapping


def scoped_item_ids(entry: dict[str, Any]) -> list[str]:
    """取り消しと積み直しの対象になる項目 ID。

    適用ラウンド（群）を持つ状態ファイルでは**進行中の群の項目だけ**を返す。
    1 件の失敗が群の外の項目を巻き込まないようにするためである（受け入れ条件 A4）。
    群を持たない状態ファイル（この版より前）は、ラウンド全体を 1 つの群として読む。
    """
    groups = entry.get("apply_rounds")
    if not groups:
        return list(entry.get("items") or [])
    current = entry.get("apply_round") or 1
    for group in groups:
        if group.get("apply_round") == current:
            return list(group.get("items") or [])
    return list(entry.get("items") or [])


def _commit_owner(
    work: str, state: dict[str, Any], entry: dict[str, Any]
) -> dict[str, str]:
    """このラウンドの `コミット → 改善項目 ID` の対応。完全な SHA へ正規化する。

    どの項目にも属さないコミット（過去の取り消しなど）はここに現れない。
    積み直しの対象から外すために、**属さないこと**を判定できる形にしておく。
    """
    owner: dict[str, str] = {}
    for item_id in scoped_item_ids(entry):
        item = _find_item(state, item_id, required=False)
        if item is None:
            continue
        for sha in item.get("commits") or []:
            if not isinstance(sha, str) or not sha.strip():
                continue
            full = _git_out(work, ["rev-parse", "--verify", f"{sha.strip()}^{{commit}}"])
            owner[full or sha.strip()] = item_id
    return owner


def _pending_drop_item_ids(state: dict[str, Any], drop_ids: list[str]) -> list[str]:
    """drop_ids から、まだ取り消されていない項目 ID だけを返す。"""
    return [
        i for i in drop_ids
        if not (_find_item(state, i, required=False) or {}).get("reverted")
    ]


def _drop_replay_plan(
    state: dict[str, Any], entry: dict[str, Any], pending: list[str],
    ordered: list[str],
) -> tuple[dict[str, str], list[str], list[str]]:
    """残す項目 (`keep_ids`) と積み直す SHA (`replay`) を求める。

    `ordered` は新しい順なので、積み直しは反転して古い順にする。
    **どの項目にも属さないコミット（過去の取り消しなど）は積み直さない。**
    """
    work = state["worktrees"]["work"]
    owner = _commit_owner(work, state, entry)
    drop = set(pending)
    keep_ids = [
        i for i in scoped_item_ids(entry)
        if i not in drop
        and not (_find_item(state, i, required=False) or {}).get("reverted")
    ]
    replay = [s for s in reversed(ordered) if owner.get(s) in keep_ids]
    return owner, keep_ids, replay


def _dry_run_drop_plan(
    pending: list[str], ordered: list[str], replay: list[str]
) -> dict[str, Any]:
    """dry-run 時の出力と戻り値を作る。実際の revert/cherry-pick は行わない。"""
    for sha in ordered:
        info(f"（dry-run）git revert --no-edit {sha}")
    for sha in replay:
        info(f"（dry-run）git cherry-pick {sha}")
    return {"mode": "item", "dropped": pending,
            "reverted": len(ordered), "replayed": len(replay)}


def _execute_drop_replay(
    work: str, ordered: list[str], head: Optional[str], replay: list[str],
) -> tuple[dict[str, str], str]:
    """範囲を取り消して残す項目を積み直す。積み直しに失敗したら round モードへ退避する。

    戻り値は `(mapping, mode)`。`mapping` は積み直し後の SHA 対応
    （`round` モードでは空）。
    """
    _revert_range(work, ordered, head)
    # 取り消しが済んだ地点。積み直しに失敗したらここへ戻せばよい。
    reverted_head = _git_out(work, ["rev-parse", "HEAD"])
    mapping = _replay_commits(work, replay)
    if mapping is None:
        info("⚠ 残す項目を積み直せませんでした。このラウンドは全件取り消します")
        # **着手前まで戻して取り消しをやり直さない。** 同じ範囲に対する取り消しが
        # 2 組できて履歴が無駄に汚れる。積み直す前の地点へ戻すだけでよい。
        _reset_hard(work, reverted_head)
        return {}, "round"
    return mapping, "item"


def _record_drop_result(
    state: dict[str, Any],
    entry: dict[str, Any],
    pending: list[str],
    keep_ids: list[str],
    ordered: list[str],
    replay: list[str],
    owner: dict[str, str],
    mapping: dict[str, str],
    mode: str,
) -> dict[str, Any]:
    """item の reverted/commits と entry.drops を更新し、結果を返す。"""
    scoped = scoped_item_ids(entry)
    dropped = list(scoped) if mode == "round" else pending
    for item_id in scoped:
        item = _find_item(state, item_id, required=False)
        if item is None:
            continue
        if mode == "round" or item_id not in keep_ids:
            item["reverted"] = True
            continue
        # **積み直しで SHA が変わる。** 記録を更新しないと、次の取り消しが
        # 履歴に無い SHA を指してしまう。
        item["commits"] = [mapping[s] for s in replay if owner.get(s) == item_id]

    entry.setdefault("drops", []).append({
        "at": statefile.now(), "mode": mode, "dropped": dropped,
        "reverted": len(ordered), "replayed": len(mapping),
    })
    info(
        f"↩ 取り消し {len(ordered)} コミット / 積み直し {len(mapping)} コミット"
        f"（{'ラウンド全件へ退避' if mode == 'round' else '項目単位'}）"
    )
    return {"mode": mode, "dropped": dropped,
            "reverted": len(ordered), "replayed": len(mapping)}


def _drop_items(
    state: dict[str, Any], entry: dict[str, Any], drop_ids: list[str],
    dry_run: bool = False,
) -> dict[str, Any]:
    """改善項目を取り消し、残す項目を積み直す。

    **範囲を新しい順に全て戻してから、残す項目を古い順に積み直す。** 項目のコミット
    だけを戻すと、取り消し対象より新しい**別項目**のコミットが同じ箇所を触っている
    ときに必ず競合する（実測では採用 5 件のうち 4 件が同一ファイルの隣接領域を
    変更しており、取り消しが競合して進行が止まった）。

    積み直しが競合したときは着手前 HEAD へ戻し、**ラウンド全件の取り消しへ退避する**。
    どの項目を残せるか決められない以上、半端な履歴を残すより全件捨てる方が安全である。

    戻り値の `mode` は次の 3 つ。

    | 値 | 意味 |
    | --- | --- |
    | `item` | 項目単位で取り消し、残す項目を積み直した |
    | `round` | 積み直せず、ラウンド全件を取り消した（退避） |
    | `skip` | 取り消すものが無かった（取り消し済み） |
    """
    work = state["worktrees"]["work"]
    pending = _pending_drop_item_ids(state, drop_ids)
    if not pending:
        info("↩ 取り消し対象は取り消し済みです")
        return {"mode": "skip", "dropped": [], "reverted": 0, "replayed": 0}

    head = _git_out(work, ["rev-parse", "HEAD"])
    ordered = commits_in_range(work, entry.get("apply_base_sha"), head or "HEAD")
    if ordered is None:
        # 起点を記録していない状態ファイル（旧版）では積み直せない。
        # 従来どおり項目のコミットだけを新しい順に戻す。
        info("⚠ 適用の範囲を確定できないため、項目のコミットだけを取り消します")
        reverted = 0
        for item_id in pending:
            reverted += _revert_item_commits(state, _find_item(state, item_id), dry_run)
        return {"mode": "item", "dropped": pending,
                "reverted": reverted, "replayed": 0}

    owner, keep_ids, replay = _drop_replay_plan(state, entry, pending, ordered)

    if dry_run:
        return _dry_run_drop_plan(pending, ordered, replay)

    mapping, mode = _execute_drop_replay(work, ordered, head, replay)

    return _record_drop_result(
        state, entry, pending, keep_ids, ordered, replay, owner, mapping, mode,
    )


def _order_newest_first(work: str, shas: list[str]) -> list[str]:
    """コミットを **git の履歴順（新しい順）** に並べ替える。

    申告された順序を信じない。古いコミットから取り消すと、後続の取り消しが
    競合して進めなくなる。履歴に無いものは順序を決められないので末尾へ置く。
    """
    if len(shas) < 2:
        return list(shas)
    history = _git_out(work, ["rev-list", "HEAD"])
    if history is None:
        return list(shas)
    rank = {sha: i for i, sha in enumerate(history.split())}   # 0 が最も新しい
    resolved = {
        s: (_git_out(work, ["rev-parse", "--verify", f"{s}^{{commit}}"]) or s)
        for s in shas
    }
    return sorted(shas, key=lambda s: rank.get(resolved[s], len(rank)))


def _worktree_changes(work: str) -> dict[str, str]:
    """作業ツリーの変更を `パス → 状態` で返す。同期の前後を比べるために使う。

    無視されているファイルは現れない（`--porcelain` の既定）。改名は移動先の
    パスだけを見る。
    """
    # `core.quotePath` の既定（true）では、非 ASCII を含むパスが `"` で囲まれ
    # `\343` の形へエスケープされる。そのまま `git add` へ渡すと見つからない。
    out = _git_out(
        work, ["-c", "core.quotePath=false", "status", "--porcelain", "-uall"],
        strip=False,
    )
    changes: dict[str, str] = {}
    for line in (out or "").splitlines():
        if len(line) < 4:
            continue
        path = line[3:]
        if " -> " in path:            # 改名。移動先だけを対象にする
            path = path.split(" -> ", 1)[1]
        changes[path.strip('"')] = line[:2]
    return changes


def _control_prefix(state: dict[str, Any], work: str) -> Optional[str]:
    """作業ディレクトリから見た制御用ディレクトリの相対パス。外にあれば `None`。

    状態ファイル・プロンプト・結果・ログの置き場所で、**同期コミットへ入れない**。
    `prepare-worktrees.sh` が無視の設定を置くが、置き場所を環境変数で移した場合や
    配置前に同期が走った場合に備えて、ここでも明示的に外す。
    """
    tmp_dir = str(state.get("tmp_dir") or "")
    if not tmp_dir:
        return None
    try:
        relative = pathlib.Path(tmp_dir).resolve().relative_to(
            pathlib.Path(work).resolve()
        )
    except ValueError:
        return None
    return f"{relative}/"


def _dirty_paths(state: dict[str, Any], work: str) -> list[str]:
    """作業ツリーの未コミット変更のパス。制御用ディレクトリは除く。"""
    control = _control_prefix(state, work)
    return sorted(
        path for path in _worktree_changes(work)
        if not (control and path.startswith(control))
    )


def _discard_worktree_changes(work: str) -> None:
    """作業ツリーと index の未コミット変更を捨てる。**着手前が綺麗なときだけ呼ぶ。**

    **index も戻す。** `git checkout -- .` は staged された差分を戻さないため、
    同期コマンドが `git add` してから失敗すると清浄性の検査が通らないままになり、
    `pending_push` の再試行が永久に進まない。

    無視されたファイル（制御用ディレクトリを含む）は消さない（`git clean` に
    `-x` を付けない）。
    """
    for args in (["reset", "--hard", "HEAD"], ["clean", "-fd"]):
        subprocess.run(["git", *args], cwd=work, capture_output=True, text=True)


def _discard_impl_leftovers(state: dict[str, Any], work: str) -> None:
    """実装担当が残した未コミットの変更を捨てる。取り込みの前に呼ぶ。

    **公開は進行側が検証を通してから行う**ので、コミットされなかった変更は
    どの検証も受けていない。Pull Request へ出す道が無い以上、残す意味がない。

    残したまま進むと、push の直前の清浄性の検査で中断する。実測では、修正
    フェーズでコミットを作れなかった実装担当が直しかけの差分を置いたまま終え、
    続く `merge-fix` が「修正 0 件」として先へ進むこともできなくなった。

    制御用ディレクトリ（状態・結果・ログ）は無視の設定で守られており、
    `git clean` に `-x` を付けないため消えない。
    """
    if not pathlib.Path(work).is_dir():
        return
    dirty = _dirty_paths(state, work)
    if not dirty:
        return
    shown = "、".join(dirty[:5])
    more = f" ほか {len(dirty) - 5} 件" if len(dirty) > 5 else ""
    _discard_worktree_changes(work)
    info(
        f"🧹 コミットされなかった変更を捨てました（{shown}{more}）。"
        "検証を受けていないため公開しません"
    )


def _require_clean_worktree(state: dict[str, Any], work: str) -> None:
    """同期の前に作業ツリーが綺麗であることを求める。汚れていたら中断する。

    汚れたまま同期すると、**同期が作った差分と元からあった差分を区別できない**。
    区別しようと状態コードを比べても足りず、次の 2 つを取りこぼす。

    - 元から ` M` のファイルを同期がさらに書き換えても、状態コードは ` M` のままで
      検知できない。その変更がコミットされず、**push がまた落ちる**
    - `git commit` は index の内容を全て含めるため、`git add` の対象を絞っても
      **先に staged だった変更が検証を受けないまま Pull Request へ入る**

    無視されたファイルはここに現れない。生成物やキャッシュを `.gitignore` へ
    入れてあれば止まらない。
    """
    dirty = _dirty_paths(state, work)
    if not dirty:
        return
    shown = ", ".join(dirty[:5])
    more = f" ほか {len(dirty) - 5} 件" if len(dirty) > 5 else ""
    die(
        f"生成物を同期する前に、作業ツリーへ未コミットの変更があります（{shown}{more}）。"
        "同期が作った差分と区別できず、検証を受けていない変更を公開しかねないため"
        "中断します。コミットするか `.gitignore` へ入れてから再実行してください"
    )


def _run_sync_command(state: dict[str, Any], work: str, command: str) -> None:
    """同期コマンドを実行する。失敗したら差分を捨てて中断する。

    **黙って push しない。** 同期できない状態を公開すると、利用者のリポジトリの
    検査を壊したまま進むことになる。
    """
    code, timed_out = _run_with_timeout(
        command, work, _safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT)
    )
    if not (timed_out or code != 0):
        return
    # **途中まで書き換えた差分を残さない。** 残すと次の実行は
    # `_require_clean_worktree` で必ず止まり、`pending_push` の再試行が
    # 永久に進まなくなる。着手前が綺麗だったことは確認済みなので、
    # ここにある変更は全て同期が作ったものだと分かる。
    _discard_worktree_changes(work)
    die(
        f"生成物の同期に失敗しました（{command}）: "
        + ("打ち切りました" if timed_out else f"終了コード {code}")
        + "。同期が作った差分は破棄したので、原因を直せばそのまま再開できます"
    )


def _write_plan_file(state: dict[str, Any], work: str, rel: str) -> None:
    """改修計画を作業ディレクトリの中へ書き出す。

    内容は状態から決まるので、**状態が動いていなければ差分は出ない**。
    書き出しを毎回行っても、余計なコミットは積まれない。
    """
    path = pathlib.Path(work) / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(format_plan(state), encoding="utf-8")


def _publish_commit_message(produced: list[str], plan_rel: str) -> str:
    """公開の直前に積むコミットのメッセージを、中身に合わせて選ぶ。"""
    has_plan = bool(plan_rel) and plan_rel in produced
    has_generated = any(p != plan_rel for p in produced)
    if has_plan and has_generated:
        return SYNC_AND_PLAN_COMMIT_MESSAGE
    if has_plan:
        return PLAN_COMMIT_MESSAGE
    return SYNC_COMMIT_MESSAGE


def _commit_sync_changes(
    work: str, command: str, produced: list[str], plan_rel: str = ""
) -> None:
    """同期が作った差分を進行側のコミットとして積む。差分が無ければ何もしない。

    このコミットはどの改善項目にも属さない。取り消しでは積み直されないが、
    次の push で作り直されるので失われても問題にならない。
    """
    if not produced:
        return
    # **後段で落ちたときも差分を残さない。** `git add` / `git commit` の失敗で
    # 作業ツリーを汚したまま中断すると、次の実行は `_require_clean_worktree` で
    # 必ず止まり、`pending_push` の再試行が永久に進まない。捨ててよい根拠は
    # 同期コマンド自身が失敗したときと同じで、着手前が綺麗だったことを
    # 確認済みだからである。
    try:
        _sh(["git", "add", "--", *produced], cwd=work)
        _sh(["git", "commit", "-m", _publish_commit_message(produced, plan_rel)],
            cwd=work)
    except SystemExit:
        _discard_worktree_changes(work)
        raise
    if command:
        info(f"🔧 生成物を同期しました（{command} / {len(produced)} ファイル）")
    else:
        info(f"📝 改修計画を記録しました（{len(produced)} ファイル）")


def _sync_generated(state: dict[str, Any]) -> None:
    """push の直前に生成物を同期し、差分があれば進行側のコミットとして積む。

    同期を**実装担当の責務にすると範囲外の変更が生まれ**、範囲の検査で全件失敗する
    （実測ではラウンドの採用 5 件が全て範囲外で落ちた）。かといって同期しないと、
    生成物の同期を検査する pre-push を持つリポジトリでは push そのものが通らず、
    取り消しを Pull Request へ反映できない。そこで**進行側が push の直前に同期する**。

    このコミットはどの改善項目にも属さない。取り消しでは積み直されないが、
    次の push で作り直されるので失われても問題にならない。

    同期に失敗したら中断する。**黙って push しない。** 同期できない状態を公開すると、
    利用者のリポジトリの検査を壊したまま進むことになる。
    """
    command = str(state.get("sync_command") or "").strip()
    # 状態ファイルの値も受け取った時点と同じ基準で通す。旧い状態ファイルや
    # 手で書き換えられた値でも、作業ディレクトリの外へは書き出さない。
    plan_rel = normalize_plan_file(state.get("plan_file"))
    if not command and not plan_rel:
        return
    work = state["worktrees"]["work"]
    # **同期の前に作業ツリーが綺麗であることを求める。** 汚れたまま同期すると、
    # 同期が作った差分と元からあった差分を区別できない。
    _require_clean_worktree(state, work)
    # 改修計画も生成物と同じ経路に乗せる。**別のコミットに分けない。**
    # 分けると、進行側のコミットが公開のたびに 2 つずつ積まれる。
    if plan_rel:
        _write_plan_file(state, work, plan_rel)
    if command:
        _run_sync_command(state, work, command)
    _commit_sync_changes(work, command, _dirty_paths(state, work), plan_rel)


def _push_head(state: dict[str, Any]) -> None:
    """head ブランチへ push する。**`--force` は使わない。**

    **公開するのは進行側だけである。** 実装担当に push させると、検証を通る前に
    変更が Pull Request へ現れ、取り消しの反映漏れがそのまま残る。
    """
    _sync_generated(state)
    _sh(
        ["git", "push", "origin", f"HEAD:{state['head_branch']}"],
        cwd=state["worktrees"]["work"],
    )
    # **改修計画のコメントは push の後で更新する**（#436 決定 6）。差分に混ざらない
    # ので push とは独立だが、公開した内容と食い違わないよう後ろへ置く。投稿に
    # 失敗しても進行は止めない（`publish_plan_comment` が出力へ残す）。
    publish_plan_comment(state)


def _push_with_retry_marker(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any]
) -> None:
    """保留の印を立ててから push し、成功したら印を消す。

    印を残さずに push すると、失敗したときに**取り消しがローカルだけに留まる**。
    処理済みガードで次回は素通りするため、Pull Request へ永久に反映されない。
    """
    entry["pending_push"] = True
    statefile.save(path, state)
    _push_head(state)
    entry["pending_push"] = False
    statefile.save(path, state)


def _flush_pending_push(
    path: pathlib.Path, state: dict[str, Any], entry: dict[str, Any]
) -> None:
    """前回やり残した push を、処理済みの判定より**先に**片づける。"""
    if not entry.get("pending_push"):
        return
    info("↻ 前回 push できなかった取り消しを反映します")
    _push_head(state)
    entry["pending_push"] = False
    statefile.save(path, state)


def _current_round(state: dict[str, Any]) -> dict[str, Any]:
    if not state["rounds"]:
        die("提案ラウンドが開かれていません。先に start-round を実行してください")
    return state["rounds"][-1]


def _round(state: dict[str, Any], round_no: int) -> dict[str, Any]:
    for entry in state["rounds"]:
        if entry["round"] == round_no:
            return entry
    die(f"ラウンド {round_no} がありません")
    raise SystemExit(1)


def _find_item(
    state: dict[str, Any], item_id: Optional[str], required: bool = True
) -> Any:
    for item in state["items"]:
        if item["item_id"] == item_id:
            return item
    if required:
        die(f"改善項目 {item_id} がありません")
    return None


def _read_result(path: pathlib.Path, runtime: str) -> dict[str, Any]:
    """結果ファイルを読む。**JSON オブジェクトでなければ失敗させる。**

    配列や数値が返ってきたまま呼び出し側へ渡すと、`payload.get(...)` で
    `AttributeError` になって進行が止まる。読み込みの時点で弾く。
    """
    if not path.exists():
        die(f"{runtime} の結果ファイルがありません: {path}", code=2)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as e:
        die(f"{runtime} の結果ファイルが JSON として読めません: {e}", code=2)
        raise SystemExit(2)
    if not isinstance(payload, dict):
        die(
            f"{runtime} の結果ファイルが JSON オブジェクトではありません"
            f"（{type(payload).__name__}）: {path}",
            code=2,
        )
    return payload


def _record_observed_model(
    entry: dict[str, Any], role: str, runtime: str,
    state: dict[str, Any], phase: str, round_no: Optional[int],
) -> None:
    """CLI の出力から実際に使われたモデル名を拾って記録する。

    取れるのは claude だけである。取れないランタイムは `None` のままにし、
    報告では既定モデルのラウンドとして集計から区別する。
    """
    stem = stem_for(runtime, phase, state["id"], round_no)
    stdout_log = pathlib.Path(state["tmp_dir"]) / f"{stem}-stdout.log"
    if not stdout_log.exists():
        return
    observed = models_lib.observed_model(
        runtime, stdout_log.read_text(encoding="utf-8", errors="replace")
    )
    if not observed:
        return
    if role == "impl":
        entry["impl_model"]["observed"] = observed
        requested = entry["impl_model"]["requested"]
    else:
        entry["reviewer_models"].setdefault(runtime, {"requested": None, "observed": None})
        entry["reviewer_models"][runtime]["observed"] = observed
        requested = entry["reviewer_models"][runtime]["requested"]
    warning = models_lib.mismatch_warning(runtime, requested, observed)
    if warning:
        info(warning)
