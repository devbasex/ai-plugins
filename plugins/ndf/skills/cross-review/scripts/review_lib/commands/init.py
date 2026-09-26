"""副命令 `init`（新規と再開）（#1142 の C2）。"""
from __future__ import annotations

import argparse
import pathlib
import shlex
import time
from typing import Any, NamedTuple

import review_lib  # noqa: E402
import assignment  # noqa: E402
from classifications import default_max_rounds, review_kind  # noqa: E402
from review_lib import (  # noqa: E402
    categories as categories_mod, findings as findings_mod, github, participants as participants_mod, posts,
    review_focus, store, workspace as workspace_mod)


class _InitResult(NamedTuple):
    """cmd_init が標準出力の機械可読ブロックへ書く初期化結果。

    再開経路と新規経路が同じ組を渡すため、位置引数の並びではなく名前付きの
    フィールドで受け渡す。
    """

    pr: object
    worktree: object
    tmp_dir: object
    repo: object
    head_branch: object
    base_branch: object
    is_own: bool
    event_downgrade: bool
    has_extra: bool
    carried_count: int
    resumed: bool


def _print_init_result(result: _InitResult) -> None:
    """cmd_init の 2 経路（再開・新規）が共有する末尾の出力ブロック。

    出力形式は再開側・新規側で同一のため 1 箇所へ寄せる。PR 番号だけは
    元の両分岐に合わせて quote しない（数値のため）。
    """
    print(f"PR={result.pr}")
    print(f'WORKTREE={shlex.quote(str(result.worktree))}')
    print(f'TMP_DIR={shlex.quote(str(result.tmp_dir))}')
    print(f'REPO={shlex.quote(str(result.repo))}')
    print(f'HEAD_BRANCH={shlex.quote(str(result.head_branch))}')
    print(f'BASE_BRANCH={shlex.quote(str(result.base_branch))}')
    print(f"IS_OWN_PR={'1' if result.is_own else '0'}")
    print(f"EVENT_DOWNGRADE={'1' if result.event_downgrade else '0'}")
    print(f"HAS_EXTRA_REVIEW_INSTRUCTIONS={'1' if result.has_extra else '0'}")
    print(f"CARRIED_OVER_THREADS={result.carried_count}")
    print(f"RESUMED={'1' if result.resumed else '0'}")


def _refresh_resume_state(
    st: dict[str, Any], pr: object, repo: str, manual_extra_review: str,
    args: argparse.Namespace,
) -> bool:
    """再開する state を最新化し、書き換えたかどうかを返す。

    旧形式の補完・manual 指示の反映・`review_instructions` の再計算・引継ぎの記録を
    行う。**保存はしない**（呼び出し側が変更有無を見て 1 度だけ書く）。
    """
    state_changed = False
    # 再開で渡した引数の反映（#727 / #648）。状態ファイルを読んだ直後に行い、
    # 作り直しの失敗はここで終了コードへ出る（以降の書き込みへ進まない）。
    if participants_mod._apply_resume_args_block(st, args):
        state_changed = True
    if "auto_review_instructions" not in st:
        changed_files = github._fetch_changed_files(pr, st.get("repo") or repo)
        categories = categories_mod._classify_changed_files(changed_files)
        st["changed_files"] = changed_files
        st["auto_review_categories"] = categories
        st["auto_review_instructions"] = review_focus._auto_review_instructions(categories)
        state_changed = True
    if manual_extra_review:
        st["manual_extra_review_instructions"] = manual_extra_review
        # 後方互換: 旧 key も manual 指示として保持する。
        st["extra_review_instructions"] = manual_extra_review
        state_changed = True
    manual = st.get("manual_extra_review_instructions") or st.get("extra_review_instructions") or ""
    combined = review_focus._combined_review_instructions(
        st.get("auto_review_instructions") or "",
        manual,
    )
    if st.get("review_instructions") != combined:
        st["review_instructions"] = combined
        state_changed = True
    by_stage = st.get("review_instructions_by_stage")
    if isinstance(by_stage, dict) and by_stage.get("detail") != combined:
        by_stage["detail"] = combined
        by_stage["model"] = review_focus._combined_review_instructions(review_focus.MODEL_REVIEW_TEMPLATE, manual)
        state_changed = True
    # 再開した時点で残っている未解決の指摘を引継ぎとして記録する。
    if findings_mod._record_carried_over(st, st.get("repo") or repo, st.get("current_pr") or pr):
        state_changed = True
    return state_changed


def _sync_resume_worktree(st: dict[str, Any], pr: object, worktree: str) -> pathlib.Path:
    """保存後の副作用（待ち行列の flush → tmp_dir 解決 → 作業ツリー同期）を順に行う。

    順序に意味がある:
    1. **待ち行列を流すのは、手元の `st` を書き戻した後である。** 流した結果
       （`queued` の解除と、届かなかった投稿の結果なし）は `_confirm_flushed` が
       状態ファイルへ直接書く。先に流すと、この後の書き戻しが古い `st` でそれらを
       消す。渡すのは状態ファイルの鍵（`args.pr`）で、`current_pr` ではない。
    2. `tmp_dir` を解決して返す（`_print_init_result` が使う）。
    3. **再開でも同期する。** 中断から再開までの間に head が進んでいることがあり、
       そのまま次のラウンドを回すと古い差分をレビューさせる。
    """
    posts._auto_flush(pr)
    tmp_dir = store._tmp_dir(worktree)
    wt = st.get("worktree_path") or ""
    resume_head = str(st.get("head_branch") or "")
    if wt and resume_head and workspace_mod._is_registered_worktree(str(wt)):
        workspace_mod._sync_worktree(str(wt), int(st.get("current_pr") or pr), resume_head)
    return tmp_dir


def _resume_from_state(
    pr: object,
    repo: str,
    worktree: str,
    manual_extra_review: str,
    args: argparse.Namespace,
) -> bool:
    """既存 state からの再開経路。

    再開に該当し出力まで済ませたら True、該当する state が無ければ False を返す。
    False のとき cmd_init は新規 init へ進む。
    """
    found = store._find_resumable_state(pr, worktree)
    if found is None:
        return False
    st, resume_state_file = found

    if _refresh_resume_state(st, pr, repo, manual_extra_review, args):
        store._write_state(resume_state_file, st)
        review_lib.info("↻ 追加レビュー観点を state に反映して再開")

    tmp_dir = _sync_resume_worktree(st, pr, worktree)
    review_lib.info(f"↻ 前回中断 state から再開（round={len(st.get('rounds', []))}）")
    _print_init_result(
        _InitResult(
            pr=st["current_pr"],
            worktree=st.get("worktree_path") or "",
            tmp_dir=tmp_dir,
            repo=st.get("repo") or "",
            head_branch=st.get("head_branch") or "",
            base_branch=st.get("base_branch") or "",
            is_own=bool(st.get("is_own_pr")),
            event_downgrade=bool(st.get("event_downgrade")),
            has_extra=bool(st.get("review_instructions")),
            carried_count=(st.get("carried_over") or {}).get("count", 0),
            resumed=True,
        )
    )
    return True


def cmd_init(args: argparse.Namespace) -> None:
    """Step 0 — state 初期化 or 既存 state 引継ぎ + プリチェック。"""
    pr = args.pr
    manual_extra_review = review_focus._extra_review_instructions(args)
    # worktree path を先に解決してから tmp_dir を決定する。
    # tmp_dir は <worktree>/.cross_review/ に配置し、作業領域を 1 つに保つ。
    # path には repo slug を含め、他リポジトリの同一 PR 番号と衝突しないようにする。
    # リポジトリ名は git の設定から求める。GraphQL を 1 点使わずに済み、誤りは
    # この後の REST の応答が検証する（`_fetch_pr_metadata`）。
    # **キャッシュを GitHub より先に読む。** git から求まらないときの落とし先が `gh` だけだと、
    # 上限に達している環境では再開の経路へ入る前に止まる（#291）。
    repo = github._repo_from_git() or github._repo_from_resume(pr, args.worktree) or review_lib._sh(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    worktree = str(pathlib.Path(args.worktree).resolve()) if args.worktree else str(
        workspace_mod._default_worktree_base() / github._repo_slug(repo) / f"pr{pr}")

    # worktree 存在チェック用: _tmp_dir() は mkdir するため、先に呼ぶと
    # worktree ディレクトリが副作用で作成され exists() が常に true になる。
    # そのため _tmp_dir() 呼び出しは worktree 作成/確認の後に行う。

    if _resume_from_state(pr, repo, worktree, manual_extra_review, args):
        return

    _init_new_state(args, pr, repo, worktree, manual_extra_review)


class _InitPRContext(NamedTuple):
    repo: str
    worktree: str
    meta: github.PrMetadata
    me: str
    author: str
    is_own: bool
    event_downgrade: bool


class _InitReviewContext(NamedTuple):
    changed_files: list[str]
    auto_review_categories: list[str]
    auto_review: str
    review_instructions: str


class _InitWorkspaceContext(NamedTuple):
    tmp_dir: pathlib.Path
    state_file: pathlib.Path


class _InitialAssignment(NamedTuple):
    host: str
    host_source: str
    participants: dict[str, Any]


class _InitialStateContext(NamedTuple):
    pr: object
    pr_ctx: _InitPRContext
    review_ctx: _InitReviewContext
    ws_ctx: _InitWorkspaceContext
    assignment: _InitialAssignment
    manual_extra_review: str


def _init_new_state(
    args: argparse.Namespace,
    pr: object,
    repo: str,
    worktree: str,
    manual_extra_review: str,
) -> None:
    """新規 init 経路: プリチェック → worktree 作成 → state 構築 → 出力。"""

    def _resolve_pr_and_ownership(
        pr: object, repo: str, worktree: str, args_worktree: str | None
    ) -> _InitPRContext | None:
        # 新規 init: プリチェック。
        # **作成者・head・base は REST の 1 回でまとめて取る。** 項目ごとに `gh pr view` を
        # 投げていた分（GraphQL 3 点）と、リポジトリ名の解決（同 1 点）が 0 点になる。
        meta = github._fetch_pr_metadata(pr, repo)
        if meta is None:
            review_lib.die(f"PR #{pr} のメタデータを取得できません（リポジトリ名: {repo}）")
            return None
        if meta.repo != repo:
            repo = meta.repo
            if not args_worktree:
                worktree = str(workspace_mod._default_worktree_base() / github._repo_slug(repo) / f"pr{pr}")
        if meta.rate_remaining is not None:
            review_lib.info(f"ℹ GitHub REST の残量: {meta.rate_remaining}")

        me = review_lib._sh(["gh", "api", "user", "--jq", ".login"])
        author = meta.author
        is_own = (me == author)
        event_downgrade = is_own
        if is_own:
            review_lib.info(f"⚠ 自分の PR (author={me}) — REQUEST_CHANGES → COMMENT 強制ダウングレード")

        return _InitPRContext(
            repo=repo,
            worktree=worktree,
            meta=meta,
            me=me,
            author=author,
            is_own=is_own,
            event_downgrade=event_downgrade,
        )

    def _prepare_review_instructions(
        pr: object, repo: str, manual_extra_review: str
    ) -> _InitReviewContext:
        changed_files = github._fetch_changed_files(pr, repo)
        auto_review_categories = categories_mod._classify_changed_files(changed_files)
        auto_review = review_focus._auto_review_instructions(auto_review_categories)
        review_instructions = review_focus._combined_review_instructions(auto_review, manual_extra_review)
        return _InitReviewContext(
            changed_files=changed_files,
            auto_review_categories=auto_review_categories,
            auto_review=auto_review,
            review_instructions=review_instructions,
        )

    def _prepare_worktree_and_comments(
        worktree: str, pr: object, head_branch: str, repo: str
    ) -> _InitWorkspaceContext:
        # worktree 分離 — _tmp_dir() より先に worktree を作成/確認する
        if not pathlib.Path(worktree).exists():
            workspace_mod._create_worktree(worktree, pr, head_branch)
        elif workspace_mod._is_registered_worktree(worktree):
            review_lib.info(f"↻ 既存 worktree 流用: {worktree}")
            workspace_mod._sync_worktree(worktree, pr, head_branch)
        else:
            # パスは存在するが現リポジトリの worktree ではない (別リポジトリの残骸等)。
            # 流用すると git 操作が壊れるため退避して作り直す。
            stale = f"{worktree}.stale-{time.strftime('%Y%m%d%H%M%S')}"
            pathlib.Path(worktree).rename(stale)
            review_lib.info(f"⚠ 現リポジトリの worktree でないため退避: {stale}")
            workspace_mod._create_worktree(worktree, pr, head_branch)

        # worktree 作成/確認後に _tmp_dir() を呼ぶ (ここで .cross_review/ が作られる)
        tmp_dir = store._tmp_dir(worktree)
        state_file = tmp_dir / f"cross-review-pr{pr}-state.json"

        # 既存コメントスナップショット（重複指摘防止）。
        # 3 ソース (インラインコメント / レビュー body / PR レベルコメント) を
        # fix skill の共有スクリプトで一括取得する。
        existing_path = tmp_dir / f"cross-review-pr{pr}-existing-comments.txt"
        error = github._fetch_existing_comments(repo, int(pr), existing_path, strict=False)
        if error is not None:
            review_lib.die(f"既存コメント取得失敗 (重複検出無効のため中断): {error}")

        return _InitWorkspaceContext(
            tmp_dir=tmp_dir,
            state_file=state_file,
        )

    def _prepare_initial_assignment(args: argparse.Namespace) -> _InitialAssignment:
        """担当ホストを確定し、起動対象の認証をチェックする。"""
        # **ホストを先に確定する。** 状態ファイルの `host` として残り、出力にも出る。
        # 推定できないときに既定を置かない（間違ったまま一周してしまう）。
        try:
            host, host_source = assignment.detect_host(getattr(args, "host", None))
        except assignment.AssignmentError as e:
            review_lib.die(str(e))
            raise
        review_lib.info(f"ホストの判定: {host}（{host_source}）")
        # 使える者の解決は共通層が持つ（#727）。通らない者は外して続け、使える者が
        # 1 者なら同じランタイムの 2 つ目で席を埋める。名前の矛盾と 0 者は終了コード 1。
        participants = participants_mod._resolve_reviewers(host, args)
        return _InitialAssignment(
            host=host, host_source=host_source, participants=participants)

    def _build_initial_review_state(
        args: argparse.Namespace,
        ctx: _InitialStateContext,
    ) -> dict[str, Any]:
        """確定済みの材料から、副作用なしに初期状態を組み立てる。"""
        host, host_source, participants = ctx.assignment
        only, _include, _exclude = participants_mod._normalize_participant_args(args)
        # 分類（design / code）ごとに上限の既定を変える（#1005）。設計は 3 ラウンドで関門 1 へ渡す
        kind = review_kind(ctx.pr_ctx.meta.head_branch, ctx.review_ctx.auto_review_categories)
        return {
            "started_at": review_lib._now(),
            "host": host,
            "host_source": host_source,
            # 引数の既定は未指定（`None`）で、新規の経路がここで定数を置く（決定 13）
            "review_kind": kind,
            "max_rounds": args.max_rounds if args.max_rounds is not None else default_max_rounds(kind),
            "rotate_after": args.rotate_after if args.rotate_after is not None else 8,
            "only": only,
            "participants": participants,
            "resume_changes": [],
            "current_pr": ctx.pr,
            "worktree_path": ctx.pr_ctx.worktree,
            "tmp_dir": str(ctx.ws_ctx.tmp_dir),
            "repo": ctx.pr_ctx.repo,
            "head_branch": ctx.pr_ctx.meta.head_branch,
            "base_branch": ctx.pr_ctx.meta.base_branch,
            "pr_author": ctx.pr_ctx.author,
            "viewer_login": ctx.pr_ctx.me,
            "is_own_pr": ctx.pr_ctx.is_own,
            "event_downgrade": ctx.pr_ctx.event_downgrade,
            "changed_files": ctx.review_ctx.changed_files,
            "auto_review_categories": ctx.review_ctx.auto_review_categories,
            "auto_review_instructions": ctx.review_ctx.auto_review,
            "manual_extra_review_instructions": ctx.manual_extra_review,
            "extra_review_instructions": ctx.manual_extra_review,
            "review_instructions": ctx.review_ctx.review_instructions,
            "pr_history": [{"pr": ctx.pr, "opened_at": review_lib._now(), "closed_at": None, "rounds": 0}],
            "rounds": [],
            "deferred_nits": [],
            "rejected_findings": [],
            "review_findings": [],
            "evidence_rounds": [],
            "verify_commands": list(getattr(args, "verify_command", None) or []),
            "verify_exit_codes": list(getattr(args, "verify_exit_code", None) or []),
            "carried_over": None,
            "final": None,
        }

    def _finalize_initial_state(
        args: argparse.Namespace,
        pr: object,
        pr_ctx: _InitPRContext,
        review_ctx: _InitReviewContext,
        ws_ctx: _InitWorkspaceContext,
        manual_extra_review: str,
    ) -> None:
        initial_assignment = _prepare_initial_assignment(args)
        context = _InitialStateContext(
            pr, pr_ctx, review_ctx, ws_ctx, initial_assignment, manual_extra_review
        )
        state = _build_initial_review_state(args, context)
        state["design_doc_oversize"] = categories_mod._warn_oversized_design_docs(
            pr_ctx.worktree, review_ctx.changed_files)
        state.update(review_focus._design_stage_fields(
            state["review_kind"], pr_ctx.worktree, review_ctx.changed_files,
            review_ctx.review_instructions, manual_extra_review))
        store._write_state(ws_ctx.state_file, state)
        review_lib.info(f"✅ state 初期化: {ws_ctx.state_file}")
        _print_init_result(
            _InitResult(
                pr=pr,
                worktree=pr_ctx.worktree,
                tmp_dir=ws_ctx.tmp_dir,
                repo=pr_ctx.repo,
                head_branch=pr_ctx.meta.head_branch,
                base_branch=pr_ctx.meta.base_branch,
                is_own=pr_ctx.is_own,
                event_downgrade=pr_ctx.event_downgrade,
                has_extra=bool(review_ctx.review_instructions),
                carried_count=0,
                resumed=False,
            )
        )

    pr_ctx = _resolve_pr_and_ownership(pr, repo, worktree, args.worktree)
    if pr_ctx is None:
        return

    review_ctx = _prepare_review_instructions(pr, pr_ctx.repo, manual_extra_review)
    ws_ctx = _prepare_worktree_and_comments(
        pr_ctx.worktree, pr, pr_ctx.meta.head_branch, pr_ctx.repo
    )
    _finalize_initial_state(
        args, pr, pr_ctx, review_ctx, ws_ctx, manual_extra_review
    )
