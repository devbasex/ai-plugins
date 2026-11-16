"""リポジトリの識別（#1142 の L0）: メインディレクトリ・`owner/repo`・slug・既定ブランチと、ブランチ戦略の宣言のブランチ名。

git だけで決める（`proc` の上に置く）。`gh repo view` まで使って決めるのは `gh_call.resolve_repo` で、
GitHub を呼ぶのは `gh_*` のモジュールだけにする。
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import branch_strategy
import decl
import limits
import proc
import strategy_rows
from closing import defer_closing

ORIGIN_URL = re.compile(r"[:/](?P<owner>[^/:]+)/(?P<name>[^/]+?)(?:\.git)?/?$")
WORKTREE_DECL = Path(".ndf") / "worktree.json"
# スプリントの目録（`supervise.py new sprint` が書く）と、目録を探すときに入らないプランの状態ディレクトリの末尾
SPRINT_MANIFEST = "sprint.json"
PLAN_STATE_SUFFIX = "-state"
# 確定仕様の置き場（リポジトリの根からの相対）。確定仕様化と設計との突き合わせが同じものを読む
SPEC_DIR = "docs/specifications"


# メインディレクトリの決め方の正本は decl.main_dir（サブモジュールは core.worktree。#2054）
main_dir = decl.main_dir


def owner_repo_from_url(url: str) -> str | None:
    """remote の URL（https・ssh・scp の形）から `owner/repo`。読めなければ `None`。"""
    m = ORIGIN_URL.search((url or "").strip())
    return f"{m.group('owner')}/{m.group('name')}" if m else None


def owner_repo(root, remote: str = "origin") -> str | None:
    """`root` の `remote` の URL から `owner/repo`。"""
    return owner_repo_from_url(proc.git_out(root, "remote", "get-url", remote) or "")


def slug(owner_repo_value: str | None) -> str | None:
    """`owner/repo` → `owner--repo`（ディレクトリ名に使う形）。"""
    return owner_repo_value.replace("/", "--") if owner_repo_value else None


def read_worktree_decl(root, fallback: bool = True) -> tuple[dict, str | None]:
    """`.ndf/worktree.json`（worktree の振る舞いの設定）の中身と、読めなかった理由。置き場は `decl.read_json_setting`
    （`root` → メインディレクトリ）。ブランチ名は持たない（ブランチ戦略の宣言 `strategy_of` が持つ。#3672）。

        `fallback=False` なら `root` の直下だけを読み、メインディレクトリへ探しに行かない。
        無ければ `({}, None)`。JSON として読めない・オブジェクトでなければ `({}, 理由)`（理由にファイルを書く）。
    """
    if not root:
        return {}, None
    if fallback:
        return decl.read_json_setting(root, WORKTREE_DECL.name)
    f = Path(root) / WORKTREE_DECL
    return decl.load_file(f, WORKTREE_DECL.name) if f.is_file() else ({}, None)


def strategy_of(root) -> branch_strategy.Strategy:
    """メインディレクトリのブランチ戦略の宣言（`.ndf/project.json` の `branch_strategy`）。

    メインディレクトリから読むのは、Pull Request の変更でブランチ名を動かせないようにするためである（#2363）。
    セクションが無い・壊れている・旧いキーが残るときは `branch_strategy.StrategyError`（既定値へ倒さない。I3・I4）。"""
    top = decl.main_dir(root) if root else None
    if top is None:
        raise branch_strategy.StrategyError("ブランチ戦略の宣言を探す git の木が無い")
    return branch_strategy.load_strategy(Path(top))


def default_branch(root) -> str | None:
    """既定ブランチ。origin の HEAD の指す先 → ローカルの候補（`strategy_rows.DEFAULT_BRANCH_CANDIDATES`）の順。どれも無ければ `None`。"""
    head = proc.git_out(root, "symbolic-ref", "--short", "refs/remotes/origin/HEAD")
    if head:
        return head.split("/", 1)[-1]
    for b in strategy_rows.DEFAULT_BRANCH_CANDIDATES:
        if proc.git_out(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{b}") is not None:
            return b
    return None


def merge_closes_issues(root, base) -> bool:
    """宛先 `base` の Pull Request をマージすると、GitHub が本文の closing keywords で課題を閉じるか。

    GitHub が閉じるのは既定ブランチへのマージだけである。既定ブランチを決められない・宛先が分からないときも真を返す
    （置き換えても閉じる予定の参照として NDF の手順で閉じられ、失うものが無い。#2228 の決定 9）。"""
    d = default_branch(root)
    return d is None or not base or str(base).removeprefix("origin/") == d


def defer_for_base(root, base, text):
    """宛先 base の Pull Request の本文を、マージで GitHub が課題を閉じるとき（merge_closes_issues）だけ
    closing.defer_closing に通す。(本文, 置き換えた指し先の字面の並び) を返す。`pr-steps.py` と `supervise_lib/pr.py` が使う。"""
    return defer_closing(text) if merge_closes_issues(root, base) else (text or "", [])


def base_branch(root) -> str:
    """ベースブランチ（宣言の `branches.base`）。宣言を読めなければ `branch_strategy.StrategyError`。"""
    return strategy_of(root).base


def _ref_exists(root, ref: str) -> bool:
    return proc.git_out(root, "show-ref", "--verify", "--quiet", ref) is not None


def _git_quiet(root, *args, timeout: float | None = None) -> subprocess.CompletedProcess | None:
    """`git -C <root> <args>` を対話のプロンプトを止めて起動する（GIT_TERMINAL_PROMPT=0）。起動できないか時間切れなら `None`。"""
    try:
        return proc.run(
            ["git", "-C", str(root), *args],
            check=False,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def _remote_has_branch(root, remote: str, name: str) -> bool:
    """`git ls-remote --heads` の行の参照名が `refs/heads/<name>` と完全に一致するか。

    `ls-remote` のパターンは参照名の末尾に一致するため、`refs/heads/x/refs/heads/<name>` のような
    別のブランチでも行が返る。問い合わせの成功ではなく、返った行の参照名そのものを照合する。
    """
    want = f"refs/heads/{name}"
    p = _git_quiet(root, "ls-remote", "--heads", remote, want)
    if p is None or p.returncode != 0:
        return False
    return any(line.split("\t", 1)[-1].strip() == want for line in p.stdout.splitlines() if "\t" in line)


def existing_base_branch(root, remote: str = "origin") -> tuple[str | None, str]:
    """差分の起点にするベースブランチと、その出所（または決まらない理由）。

    宣言の `branches.base` を、取得済みの参照 → `remote` への問い合わせの順に実在を確かめる。無ければ既定ブランチへ
    落とさずに `(None, 理由)` を返す。宣言を読めなければ `(None, 理由)`。
    """
    try:
        name = base_branch(root)
    except branch_strategy.StrategyError as e:
        return None, e.reason
    if _ref_exists(root, f"refs/remotes/{remote}/{name}") or _ref_exists(root, f"refs/heads/{name}"):
        return name, "宣言（取得済みの参照）"
    if _remote_has_branch(root, remote, name):
        return name, f"宣言（{remote} へ問い合わせ）"
    return None, f"{name} は {remote} にもローカルにも無い"


def production_branch(root) -> str:
    """本番ブランチ（宣言の `branches.production`）。宣言を読めなければ `branch_strategy.StrategyError`。"""
    return strategy_of(root).production


def strategy_or_none(root) -> branch_strategy.Strategy | None:
    """`strategy_of` の、読めなければ `None` を返す形。決まらないことを呼び手が扱える箇所（照会を飛ばす・判定しない）だけが使う。
    既定値へは倒さない（I3）。"""
    try:
        return strategy_of(root)
    except branch_strategy.StrategyError:
        return None


# 本番チャネルを取り込み直す上限（秒）。応答の無い origin でプランの組み立てを止め続けないため（#3308）
PRODUCTION_FETCH_TIMEOUT = limits.seconds("repo.production-fetch")


def _fetch_tracking(root, name: str) -> bool:
    """origin の `refs/heads/<name>` を `refs/remotes/origin/<name>` へ取り込み直す。成否を返す。

    `main` 宛の検査（`scripts/check-pr-base.sh`）と同じ refspec を使う。失敗しても手元の追跡ブランチは変わらない。"""
    p = _git_quiet(root, "fetch", "-q", "origin", f"refs/heads/{name}:refs/remotes/origin/{name}", timeout=PRODUCTION_FETCH_TIMEOUT)
    return p is not None and p.returncode == 0


def production_strategy(root, production: str | None) -> tuple[branch_strategy.Strategy | None, str]:
    """本番ブランチの先頭（`origin/<本番ブランチ>`）の宣言。#3308 の前提 1。

    読めたら `(宣言, 読んだ場所)`、読めなければ `(None, 読めない理由)`。取り込み直しに失敗しても手元の追跡ブランチを読む
    （前提 4）。読めないときの扱いは呼び手が決める（止まる。I3）。"""
    if not production:
        return None, "本番ブランチの名前が決まらない"
    if root is None:
        return None, "宣言を探す木に git の木が無い"
    _fetch_tracking(root, production)
    ref = f"origin/{production}"
    try:
        return branch_strategy.load_at_ref(Path(root), f"refs/remotes/{ref}"), f"{ref} の .ndf/project.json の {branch_strategy.SECTION}"
    except branch_strategy.StrategyError as e:
        return None, f"{ref}: {e.reason}"


def sprint_flow_record(root, branch: str | None) -> dict | None:
    """スプリントブランチ `branch` の開始時の記録（目録 `sprint.json` のスナップショット。#3310）。無ければ `None`。

    メインディレクトリの `.ndf/` の下の目録のうち、`ブランチ` が `branch` のもの（`ブランチ` を持たない目録は
    `sprint/<スプリント>`）を探し、更新の新しい目録の記録を返す。プランの状態ディレクトリ（`<プラン>-state`）の中は
    作業の写しを持つため探さない。目録が読めなければその目録を飛ばす。"""
    top = decl.main_dir(root) if root else None
    if not branch or top is None:
        return None
    hits = []
    for d, dirs, files in os.walk(Path(top) / ".ndf"):
        dirs[:] = [x for x in dirs if not x.endswith(PLAN_STATE_SUFFIX)]
        if SPRINT_MANIFEST not in files:
            continue
        hit = _sprint_record_candidate(Path(d) / SPRINT_MANIFEST, branch)
        if hit is not None:
            hits.append(hit)
    return max(hits, key=lambda h: (h[0], h[1]))[2] if hits else None


def _sprint_record_candidate(path: Path, branch: str) -> tuple[float, str, dict] | None:
    """目録 `path` が `branch` の開始時の記録を持てば (更新時刻, パス, 記録) を返す。読めないか対象外なら `None`。"""
    try:
        got = json.loads(path.read_text(encoding="utf-8"))
        mtime = path.stat().st_mtime
    except (OSError, ValueError):
        return None
    if not isinstance(got, dict):
        return None
    own = got.get("ブランチ") or f"{strategy_rows.SPRINT_PREFIX}{got.get('スプリント')}"
    snap = got.get(branch_strategy.SNAPSHOT_KEY)
    if own == branch and isinstance(snap, dict) and snap:
        return mtime, str(path), snap
    return None


def kept_sprint_branch(root, branch: str | None, record: dict | None = None) -> str | None:
    """後片付けでスプリントブランチを残す理由（#3121 の決定 7）。消してよければ None。

    選んで集める取り込み方（`release_branch`）では、スプリントブランチはリリースの単位として本番ブランチに含まれるまで要る。
    取り込み方と本番ブランチは、そのスプリントの開始時の記録（`record`）で決め、記録の無いスプリントだけ今の宣言で決める
    （#3308 の設計の決定 1・決定 6）。宣言か記録が読めなければ消さない側（残す）へ倒す。"""
    if not branch or not branch.startswith(strategy_rows.SPRINT_PREFIX):
        return None
    try:
        strategy = branch_strategy.from_snapshot(record) if record else strategy_of(root)
    except branch_strategy.StrategyError as e:
        return f"ブランチ戦略の{'開始時の記録' if record else '宣言'}を読めないため残す: {e.reason}"
    if proc.git_out(root, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}") is None:
        return None
    ref = f"origin/{strategy.production}"
    if strategy_rows.sprint_branch_kept(root, f"refs/heads/{branch}", ref, strategy.row):
        return f"取り込み方 {strategy.row.name} では {ref} に含まれるまでスプリントブランチを残す"
    return None


def restore_branch(root, branch: str, ref: str = "HEAD", run_git=None) -> bool:
    """Pull Request のマージで origin の `branch` が消えていれば（`delete_branch_on_merge`）、`ref` の先端を push し直す。

    在れば何もしない（強制 push はしない）。push し直したら真。`release-steps.py` の開発版のリリースブランチと、
    選んで集める取り込み方の検査の後のスプリントブランチ（#3121 の決定 7）が使う。`run_git` は呼び手の `git`
    （`proc.git` と同じ形。既定は `proc.git`）で、呼び手のモジュールの `git` を差し替える試験もこの処理を通す。"""
    g = run_git or proc.git
    if g(root, "ls-remote", "--exit-code", "--heads", "origin", f"refs/heads/{branch}", check=False).returncode == 0:
        return False
    g(root, "push", "-q", "origin", f"{ref}:refs/heads/{branch}")
    return True


HEAD_GONE = "Head ref must be a branch"  # gh pr create が、origin に head のブランチが無いときに返す語


def pr_create_restoring_head(root, gh, base: str, head: str, title: str, body: str, run_git=None):
    """`gh pr create` を `gh`（`gh_parts.gh` の形）で打ち、結果を返す。origin に head が無いと落ち、head が作業ツリーの
    ブランチなら、`restore_branch` で push し直して 1 回だけ打ち直す。開発版の PR のマージの後に `delete_branch_on_merge`
    が遅れて head を消し、`restore_branch` の確かめとすれ違った場合に効く（v10.27.63 の release）。"""
    g = run_git or proc.git
    args = ["pr", "create", "--base", base, "--head", head, "--title", title, "--body", body]
    p = gh(args, cwd=root)
    if p.returncode != 0 and HEAD_GONE in p.stderr and g(root, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == head:
        if restore_branch(root, head, run_git=g):
            p = gh(args, cwd=root)
    return p


def first_git_tree(roots) -> Path | None:
    """木の並びのうち、存在するディレクトリで、メインディレクトリ（`decl.main_dir`）を解ける最初の木。無ければ `None`。

    まだ作っていない worktree のパスを並びの先頭に持つ呼び手が、既定ブランチを存在する木から解くために使う（#2097）。
    存在しないパスには git を起動しない。
    """
    for r in roots:
        if r and Path(r).is_dir() and decl.main_dir(r) is not None:
            return Path(r)
    return None
