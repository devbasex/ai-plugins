"""ラウンドの入口。`init` と `start-round` を持つ。

対象の Pull Request の文脈・参加する CLI の認証・作業ツリーの用意・状態ファイルの
初期化と、提案ラウンドの開始を扱う。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
from typing import Any, Iterable, Optional

import assignment
import auth
import models as models_lib
import statefile

from .. import ABORT, die, info
from ..gitfacts import _run_with_timeout
from ..paths import (
    _default_worktree_base,
    _load,
    _repo_slug,
    _sh,
    _state_path,
    _tmp_dir_for,
)
from ..plan import default_plan_file, normalize_plan_file
from ..rounds import STRUCTURE, TEST, entry_kind, round_kind
from ..vocabulary import (
    DEFAULT_TEST_TIMEOUT,
    REQUIRED_SKILLS,
    test_vocabulary,
    vocabulary,
)
from .report import _finish


def check_auth(runtimes: Iterable[str]) -> dict[str, dict[str, Any]]:
    """参加する CLI の認証状態を確かめる。1 つでも欠けたら初期化を中断する。

    実装は共通層（`lib/auth.py`）にある。**この工程の中断は終了コード 4 である**ため、
    出力と中断の手段をここから渡す。
    """
    return auth.check_auth(runtimes, info=info, die=die)


def _review_post_note(is_own_pr: bool) -> str:
    """レビュープロンプトへ渡す投稿の event の指示を組み立てる。

    定義を検証側（この CLI）に置き、状態ファイル経由で起動側へ渡す。
    語彙の受け渡しと同じ形にして、文面の分岐が起動シェルへ散らないようにする。
    """
    if is_own_pr:
        return (
            "この Pull Request の作成者はあなたを動かしている利用者本人です。"
            "GitHub は自分の Pull Request への `APPROVE` と `REQUEST_CHANGES` を "
            "`HTTP 422` で拒むため、**投稿は必ず `-f event=COMMENT` で行ってください**。"
            "判定そのものは本文の先頭行と結果ファイルへ `APPROVE` / `REQUEST_CHANGES` "
            "のまま残します。収束判定は結果ファイルの判定を見るので、"
            "投稿を倒しても評価は変わりません。"
        )
    return (
        "投稿の `-f event=` には判定をそのまま渡してください"
        "（`APPROVE` または `REQUEST_CHANGES`）。"
    )


def _apply_post_event(state: dict[str, Any], is_own_pr: bool) -> None:
    """投稿の event に関する項目を状態へ入れる。

    初期化と再開の**両方**から呼ぶ。この指示が入る前の版で作った状態ファイルには
    項目そのものが無く、無いまま再開すると自分の Pull Request で `HTTP 422` を
    踏み続ける。値は GitHub 側の照合結果だけで決まるので、再開のたびに入れ直しても
    判定は変わらない。
    """
    state["is_own_pr"] = is_own_pr
    state["event_downgrade"] = is_own_pr
    state["review_post_note"] = _review_post_note(is_own_pr)


def _warn_unmeasurable_models(
    model_spec: dict[str, Optional[str]], participants: Iterable[str]
) -> None:
    """実際に動いたモデルを取得できない指定を、**着手前に**知らせる。

    分離の対象は 2 つある。kiro の既定 `auto` はラウンドごとに違うモデルが動きうる。
    実測モデル名を取れないランタイム（claude 以外）で `--model` を渡さないラウンドも、
    何が動いたかを後から確かめる手段が無い。報告まで分からないと、比較のために
    回した実行が丸ごと無駄になる。止めはしない（比較が目的でない実行もある）。
    """
    for runtime in sorted(participants):
        if models_lib.is_measurable(runtime, model_spec.get(runtime)):
            continue
        info(
            f"⚠ {runtime} のモデルが "
            f"{models_lib.label(model_spec.get(runtime))} です — "
            "実際に動いたモデルを取得できないため、そのラウンドは集計から分離されます。"
            f"比較するなら --model {runtime}=<モデル名> を指定してください"
        )


_REPO_URL = re.compile(
    r"(?:github\.com[:/])(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"
)


def _repo_from_git() -> Optional[str]:
    """git の設定から `owner/repo` を求める。求まらなければ `None`。

    **求めた名前はそのまま使わない。** `repos/{owner}/{repo}/pulls/{PR}` の応答が
    そのまま検証になるため、誤った名前は失敗として現れる（`_fetch_pr_context`）。
    """
    m = _REPO_URL.search(_sh(["git", "remote", "get-url", "origin"], check=False))
    return f"{m.group('owner')}/{m.group('name')}" if m else None


def _pr_payload(repo: str, pr: int) -> Optional[dict[str, Any]]:
    """`repos/{repo}/pulls/{pr}` の応答を返す。読めなければ `None`。"""
    out = _sh(["gh", "api", f"repos/{repo}/pulls/{int(pr)}"], check=False)
    if not out:
        return None
    try:
        body = json.loads(out)
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) and body.get("number") else None


def _fetch_pr_context(pr: int, repo: Optional[str] = None) -> tuple[str, str, str, bool, str]:
    """GitHub から Pull Request のメタデータを取り、自分の Pull Request かを判定する。

    返すのは `(repo, base_branch, head_branch, is_own_pr, author)`。

    **作成者・head・base は REST の 1 回でまとめて取る**（#271）。項目ごとに
    `gh pr view` を投げると、同じ Pull Request へ GraphQL を 3 点使う。尽きるのは
    GraphQL 側であり、REST 側は上限 5,000 のうち大半が残ったまま進行が止まる。
    """
    tried: list[str] = []
    body: Optional[dict[str, Any]] = None
    resolved = ""
    for candidate in (repo, _repo_from_git()):
        if not candidate or candidate in tried:
            continue
        tried.append(candidate)
        body = _pr_payload(candidate, pr)
        if body is not None:
            resolved = candidate
            break
    if body is None:
        # 求めた名前が誤っていたときだけ、GraphQL で解決し直す。
        fallback = _sh(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
        body = _pr_payload(fallback, pr)
        if body is None:
            die(f"Pull Request #{pr} のメタデータを取得できません（リポジトリ名: {fallback}）")
            raise SystemExit(ABORT)
        resolved = fallback

    # **取得に失敗しても止めない。** bot トークン（Actions の `GITHUB_TOKEN` など）は
    # `/user` を読めず `HTTP 403` を返す。この値は自分の Pull Request かどうかの
    # 判定にしか使わないので、読めなければ他者の Pull Request として扱えばよい。
    viewer = _sh(["gh", "api", "user", "--jq", ".login"], check=False)
    author = str((body.get("user") or {}).get("login") or "")
    is_own_pr = bool(viewer) and viewer == author
    head_branch = str((body.get("head") or {}).get("ref") or "")
    base_branch = str((body.get("base") or {}).get("ref") or "")
    return resolved, base_branch, head_branch, is_own_pr, author


def cmd_init(args: argparse.Namespace) -> None:
    """Step 0 — ホストと母集合を確定し、作業ディレクトリ root と状態を用意する。

    **提案・レビューの母集合（全 − ホスト）と適用の母集合（全 − agy）を
    別々に確定する。** 両者は重なるが一致しない。
    """
    try:
        host, detection = assignment.detect_host(args.host)
    except assignment.AssignmentError as e:
        die(str(e))
        return
    try:
        model_spec = models_lib.parse_model_args(args.model)
    except models_lib.ModelSpecError as e:
        die(str(e))
        return

    runtimes = assignment.review_pool(host)
    impl_capable = assignment.impl_pool()
    if host in runtimes:
        die(f"提案・レビューの母集合にホスト {host} が含まれています（判定の誤り）")
    _warn_unmeasurable_models(model_spec, set(runtimes) | set(impl_capable))

    # **認証は作業ディレクトリを作る前に確かめる。** 未認証のまま進むと、
    # 参加者が欠けた構成のまま最後まで走り切ってしまう。
    auth = check_auth(sorted(set(runtimes) | set(impl_capable)))

    # リポジトリ名は git の設定から求め、Pull Request の応答で確かめる（#271）。
    repo, base_branch, head_branch, is_own_pr, author = _fetch_pr_context(args.pr)
    if is_own_pr:
        info(f"⚠ 自分の Pull Request です（作成者 {author}）— 投稿は COMMENT へ倒します")

    root = (
        pathlib.Path(args.worktree_root).resolve() if args.worktree_root
        else _default_worktree_base() / _repo_slug(repo) / f"rf{args.pr}"
    )
    work = root / "work"
    _ensure_work_worktree(work, head_branch)

    tmp_dir = _tmp_dir_for(work)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    state_file = _state_path(tmp_dir, args.pr)

    if state_file.exists():
        state = statefile.load(state_file)
        if state.get("final") is None:
            info(f"↻ 前回中断した状態から再開します（提案ラウンド {state.get('outer_round', 0)}）")
            _apply_post_event(state, is_own_pr)
            statefile.save(state_file, state)
            _emit_init(state)
            return

    baseline = _run_baseline_test(args.baseline_test, work, args.test_timeout)

    state: dict[str, Any] = {
        "id": args.pr,
        "started_at": statefile.now(),
        "repo": repo,
        "current_pr": args.pr,
        "base_branch": base_branch,
        "head_branch": head_branch,
        "worktree_root": str(root),
        "worktrees": {"work": str(work), **{r: str(root / r) for r in runtimes}},
        "tmp_dir": str(tmp_dir),
        "target_scope": list(args.scope),
        "host": host,
        "host_detection": detection,
        "runtimes": runtimes,
        "impl_capable": impl_capable,
        "models": model_spec,
        "auth": auth,
        # 提案プロンプトへ許容値をそのまま列挙するために持たせる。
        # 定義は検証側（この CLI）にあり、状態ファイル経由で起動側へ渡す。
        "vocabulary": vocabulary(),
        # テスト整備ラウンドの語彙も同じ経路で渡す。**新しい語彙は作らず**、
        # 既存の 3 本の参照が持つ分類をそのまま列挙する（決定 9）。
        "test_vocabulary": test_vocabulary(),
        "skills": {"required": list(REQUIRED_SKILLS)},
        "max_outer_rounds": args.max_outer_rounds,
        "max_test_rounds": args.max_test_rounds,
        "max_fix_rounds": args.max_fix_rounds,
        "max_items_per_round": args.max_items_per_round,
        # 最終ゲートで手元のテストの代わりに見る検査の名前。**排他である**
        # （指定があれば手元のテストを実行しない）。
        "ci_check": args.ci_check,
        # **最初に開くのはテスト整備ラウンドである。** テストが乏しい箇所では、
        # 「テストが通ること」を検証に使えない（Step 5 の判定はテストで決まる）。
        "round_kind": TEST,
        "severity_threshold": args.severity_threshold,
        "baseline_test": baseline,
        # 生成物の同期は**進行側の責務**。push の直前に実行する。
        "sync_command": args.sync_command,
        # 改修計画の書き出し先も同じ経路に乗せる。指定が無ければ既定のパスを使い、
        # 空文字なら記録しない。
        "plan_file": normalize_plan_file(
            default_plan_file(args.pr) if args.plan_file is None else args.plan_file
        ),
        "test_timeout": args.test_timeout,
        "outer_round": 0,
        "phase": "init",
        "rounds": [],
        "items": [],
        "deferred_items": [],
        "final": None,
    }
    # GitHub は自分の Pull Request への `APPROVE` と `REQUEST_CHANGES` を
    # `HTTP 422` で拒む。判定はそのまま結果ファイルへ残し、**投稿の event だけ**
    # を倒す。収束判定は結果ファイルの判定を見るので、倒しても進行は変わらない。
    _apply_post_event(state, is_own_pr)
    statefile.save(state_file, state)
    info(f"✅ 状態を初期化しました: {state_file}")
    info(f"   ホスト: {host}（{detection}）")
    info(f"   提案・レビュー: {' / '.join(runtimes)}")
    info(f"   適用の母集合: {' / '.join(impl_capable)}")
    _emit_init(state)


def _emit_init(state: dict[str, Any]) -> None:
    statefile.emit(
        ID=state["id"],
        REPO=state["repo"],
        HOST=state["host"],
        RUNTIMES=" ".join(state["runtimes"]),
        RUNTIMES_CSV=",".join(state["runtimes"]),
        IMPL_POOL=" ".join(state["impl_capable"]),
        WORKTREE_ROOT=state["worktree_root"],
        WORK=state["worktrees"]["work"],
        TMP_DIR=state["tmp_dir"],
        HEAD_BRANCH=state["head_branch"],
        BASE_BRANCH=state["base_branch"],
        SCOPE=" ".join(state["target_scope"]),
    )


def _ensure_work_worktree(work: pathlib.Path, head_branch: str) -> None:
    """書き込み用の作業ディレクトリを冪等に用意する。

    ここだけが**唯一の非 detach**（Pull Request の head ブランチを checkout する）。
    読み取り用は `prepare-worktrees.sh` が `--detach` で作る。同一ブランチを
    2 つの作業ディレクトリへ checkout できないという git の制約があるためである。
    """
    if work.exists():
        if _is_registered_worktree(work):
            _sync_work_worktree(work, head_branch)
            return
        stale = work.with_name(f"work.stale-{time.strftime('%Y%m%d%H%M%S')}")
        work.rename(stale)
        info(f"⚠ 現リポジトリの作業ディレクトリではないため退避しました: {stale}")
    work.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "worktree", "prune"], capture_output=True, text=True)
    _sh(["git", "fetch", "origin", head_branch])
    # ローカルに head ブランチがあるかどうかで作り方が変わる。無い状態で
    # `worktree add <path> <branch>` を叩くと「そんなブランチは無い」で失敗する。
    exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{head_branch}"],
        capture_output=True, text=True,
    ).returncode == 0
    if exists:
        _sh(["git", "worktree", "add", str(work), head_branch])
    else:
        _sh(["git", "worktree", "add", "-b", head_branch, str(work),
             f"origin/{head_branch}"])
    info(f"✅ 書き込み用の作業ディレクトリを作成しました: {work}")


def _sync_work_worktree(work: pathlib.Path, head_branch: str) -> None:
    """既存の書き込み用作業ディレクトリを origin の head へ追いつかせる。

    再開までに Pull Request の head が進んでいることがある。同期せずに使うと、
    **古い HEAD に対して提案・適用**してしまう。早送りできない（履歴が分かれた）
    ときは、どちらが正しいかを機械が決められないので中断する。
    """
    fetched = subprocess.run(
        ["git", "fetch", "origin", head_branch],
        cwd=str(work), capture_output=True, text=True,
    )
    if fetched.returncode != 0:
        # 取得できないまま古い `origin/<head>` へ早送りすると、同期したつもりで
        # **古い HEAD のまま**進んでしまう。通信・認証の失敗はここで止める。
        die(
            f"origin/{head_branch} を取得できませんでした: "
            f"{fetched.stderr.strip()[:300]}。"
            "古い HEAD のまま進めないため中断します"
        )
    r = subprocess.run(
        ["git", "merge", "--ff-only", f"origin/{head_branch}"],
        cwd=str(work), capture_output=True, text=True,
    )
    if r.returncode != 0:
        die(
            f"作業ディレクトリを origin/{head_branch} へ早送りできませんでした: "
            f"{r.stderr.strip()[:300]}。"
            "履歴が分かれています。内容を確認してから再実行してください"
        )
    info(f"↻ 作業ディレクトリを origin/{head_branch} へ同期しました: {work}")


def _is_registered_worktree(path: pathlib.Path) -> bool:
    out = _sh(["git", "worktree", "list", "--porcelain"], check=False)
    target = str(path.resolve())
    return any(line == f"worktree {target}" for line in out.splitlines())


def _run_baseline_test(
    command: str, work: pathlib.Path, timeout: int = DEFAULT_TEST_TIMEOUT
) -> dict[str, Any]:
    """着手前のテストを実行して記録する。

    失敗している状態で構造改善に入ると、**壊したのか元から壊れていたのか**
    区別できない。そもそも振る舞いが変わっていないことを示す手段が無い書き換えは
    構造改善ではないため、テストコマンドは必須にしている。
    """
    code, timed_out = _run_with_timeout(command, str(work), timeout)
    if timed_out:
        die(
            f"着手前のテストが {timeout} 秒で終わりませんでした（{command}）。"
            "打ち切りました"
        )
        raise SystemExit(1)
    status = "green" if code == 0 else "red"
    if status == "red":
        die(
            f"着手前のテストが失敗しています（{command}）。"
            "先に直してから開始してください"
        )
    info(f"✅ 着手前のテスト成功: {command}")
    return {"command": command, "status": status, "checked_at": statefile.now()}


def rounds_of_kind(state: dict[str, Any], kind: str) -> list[dict[str, Any]]:
    """その種類のラウンドだけを取り出す。上限はそれぞれ別に数える。"""
    return [r for r in state.get("rounds") or [] if entry_kind(r) == kind]


def cmd_start_round(args: argparse.Namespace) -> None:
    """Step 2 — ラウンドを開き、実装担当とレビュー担当を返す。

    終了コード: 0 = ラウンドを開いた / 1 = 繰り返しが終了済み。

    **開くのはテスト整備ラウンドか提案ラウンドのどちらかである。** どちらを開くかは
    状態の `round_kind` が持ち、切り替えるのは `advance` である（判定を 1 か所に
    まとめ、開く側は宣言に従うだけにする）。

    **再開しても担当は変わらない。** 同じラウンド番号を開き直したときは記録済みの
    割り当てをそのまま返す。
    """
    path, state = _load(args.id)
    if state.get("final"):
        info(f"ラウンドの繰り返しは終了しています（{state['final']}）")
        sys.exit(1)

    rounds = state["rounds"]
    kind = round_kind(state)
    if kind == STRUCTURE and len(rounds_of_kind(state, STRUCTURE)) >= state["max_outer_rounds"]:
        _finish(path, state, "max_outer_rounds")
        sys.exit(1)

    round_no = len(rounds) + 1
    existing = next((r for r in rounds if r["round"] == round_no), None)
    if existing is None:
        impl, reviewers = assignment.assign(round_no, state["host"])
        models = state["models"]
        existing = {
            "round": round_no,
            # **種類はラウンドごとに残す。** 上限を別々に数えるためと、提案の
            # 重複率を同じ種類どうしで測るためである。
            "kind": kind,
            "started_at": statefile.now(),
            "impl": impl,
            "impl_model": {"requested": models.get(impl), "observed": None},
            "reviewers": reviewers,
            "reviewer_models": {
                r: {"requested": models.get(r), "observed": None} for r in reviewers
            },
            "proposed": {},
            "merged": 0, "adopted": 0, "deferred": 0,
            "items": [],
            "apply": {"applied": [], "failed": [], "base_sha": None, "head_sha": None},
            "fix_rounds": 0,
            "durations": {},
            "reviews": [],
        }
        rounds.append(existing)
        state["outer_round"] = round_no
        state["phase"] = "propose"
        statefile.save(path, state)

    kind = entry_kind(existing)
    if kind == TEST:
        label = "テスト整備ラウンド"
        limit = state.get("max_test_rounds")
    else:
        label = "提案ラウンド"
        limit = state["max_outer_rounds"]
    seq = len(rounds_of_kind(state, kind))
    info(
        f"=== {label} {seq} / {limit} "
        f"（実装 {existing['impl']} / レビュー {' + '.join(existing['reviewers'])}）==="
    )
    statefile.emit(
        ROUND=round_no,
        ROUND_KIND=kind,
        # 提案に使う雛形の名前。**結果ファイルの名前は種類で変えない**
        # （ラウンド番号は通しなので衝突せず、監視の雛形をそのまま使える）。
        PROPOSE_PHASE="propose-tests" if kind == TEST else "propose",
        IMPL=existing["impl"],
        IMPL_MODEL=existing["impl_model"]["requested"],
        REVIEWERS=" ".join(existing["reviewers"]),
        REVIEWERS_CSV=",".join(existing["reviewers"]),
        MAX_FIX_ROUNDS=state["max_fix_rounds"],
    )
