"""ラウンドの入口。`init` と `start-round` を持つ。

対象の Pull Request の文脈・参加者の決定・作業ツリーの用意・状態ファイルの
初期化と再開と、提案ラウンドの開始を扱う。
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from typing import Any, Iterable, Optional

import assignment
import auth
import models as models_lib
import statefile

from .. import ABORT, die, info
from ..gitfacts import run_with_timeout, safe_int
from ..paths import (
    default_worktree_base,
    load_state,
    repo_slug,
    sh,
    state_path,
    tmp_dir_for,
)
from ..plan import PLAN_COMMENT, PLAN_FILE, PLAN_NONE, normalize_plan_file
from ..rounds import (
    STRUCTURE,
    TEST,
    entry_kind,
    finish_outer_rounds,
    impl_for_seq,
    round_kind,
    rounds_of_kind,
)
from ..scope import require_scope_covers_tests, round_test_hint
from ..vocabulary import (
    DEFAULT_MAX_TEST_ROUNDS,
    DEFAULT_SEVERITY_THRESHOLD,
    DEFAULT_TEST_TIMEOUT,
    IMPL_STALL_MARGIN,
    REQUIRED_SKILLS,
    test_vocabulary,
    vocabulary,
)


# 再開で指定を外す予約語（#727 の決定 15）。足す者・外す者に渡すと一覧を空へ戻す。
NONE_WORD = "none"

# 新規の初期化で、未指定の引数を置き換える現行の既定。**引数の既定は `None` にする。**
# 既定値を引数に持たせると、再開で「渡さなかった」と「既定値を渡した」を区別できない
# （#727 の決定 13）。
NEW_RUN_DEFAULTS: dict[str, Any] = {
    "max_outer_rounds": 3,
    "max_test_rounds": DEFAULT_MAX_TEST_ROUNDS,
    "max_fix_rounds": 3,
    "max_items_per_round": 5,
    "test_timeout": DEFAULT_TEST_TIMEOUT,
    "severity_threshold": DEFAULT_SEVERITY_THRESHOLD,
    "workflow_step": False,
}

# 再開で渡した引数の反映の表（#727 の決定 13）。**状態ファイルに載る引数は、この 2 つの
# 表のどちらかに必ず載る。** `replace` は状態へ書いて記録へ積み、`notify` は状態と違う
# ときだけ「反映しない」と知らせる。
RESUME_REPLACE_FIELDS = tuple(
    statefile.ResumeField(key, key, "replace")
    for key in ("max_outer_rounds", "max_test_rounds", "max_fix_rounds",
                "max_items_per_round", "test_timeout")
)
RESUME_NOTIFY_FIELDS = (
    statefile.ResumeField("host", "host", "notify"),
    statefile.ResumeField("scope", "target_scope", "notify"),
    statefile.ResumeField("model", "models", "notify"),
    statefile.ResumeField("baseline_test", "baseline_test", "notify"),
    statefile.ResumeField("round_test", "round_test", "notify"),
    statefile.ResumeField("ci_check", "ci_check", "notify"),
    statefile.ResumeField("severity_threshold", "severity_threshold", "notify"),
    statefile.ResumeField("sync_command", "sync_command", "notify"),
    statefile.ResumeField("plan_file", "plan_file", "notify"),
    statefile.ResumeField("workflow_step", "workflow_step", "notify"),
    statefile.ResumeField("worktree_root", "worktree_root", "notify"),
)


def runtime_list(value: str) -> list[str]:
    """`--exclude` / `--include` の型。カンマ区切りの 4 つの名前、または `none`。"""
    names = [n.strip() for n in value.split(",") if n.strip()]
    if not names:
        raise argparse.ArgumentTypeError("名前を 1 つ以上指定してください")
    for name in names:
        if name != NONE_WORD and name not in assignment.ALL_RUNTIMES:
            raise argparse.ArgumentTypeError(
                f"{'/'.join(assignment.ALL_RUNTIMES)} か {NONE_WORD} を指定してください: {name}")
    return names


def _names_arg(args: argparse.Namespace, option: str) -> Optional[list[str]]:
    """`--include` / `--exclude` を平らな一覧へ直す。未指定は `None`、`none` は空。

    `action="append"` の入れ子を平らにし、`--exclude agy --exclude kiro` と
    `--exclude agy,kiro` を同じにする。`none` と名前の混在は中断する。
    """
    raw = getattr(args, option, None)
    if raw is None:
        return None
    names: list[str] = []
    for group in raw:
        names.extend(group if isinstance(group, list) else [group])
    if NONE_WORD in names:
        if len(names) > 1:
            die(f"--{option} に {NONE_WORD} と名前を同時に指定できません: {', '.join(names)}")
        return []
    return names


def resolve_participants(
    host: str, include: list[str], exclude: list[str], require_all: bool,
) -> dict[str, Any]:
    """参加者を決め、状態ファイルの `participants` を返す（#727 の決定 2〜5）。

    母集合の既定は `refactor_pool(host)`（codex / kiro とホスト）。確認は止めない確認
    （`auth.probe_auth`）で、通らない者は外して続ける。名前の矛盾・全員を要する指定で
    欠け・使える者が 0 者は、この工程の中断（終了コード 4）へ写す。母集合に無い者の
    除外は中断せず、`ℹ` の 1 行を出して続ける（#786 の決定 2）。状態ファイルは
    この関数の後に書かれるため、失敗したときは作られも書き換えられもしない。
    """
    try:
        pool = assignment.refactor_pool(host)
        resolved = assignment.resolve_participants(
            pool, host=host, include=include, exclude=exclude,
            probe=lambda names: auth.probe_auth(names, info=info),
            require_all=require_all,
        )
    except assignment.AssignmentError as e:
        die(str(e))
        raise
    info(f"ホスト: {host} / 母集合: {' / '.join(pool)}"
         f" / 使える者: {' / '.join(resolved.available) or 'なし'}")
    if resolved.ignored_exclude:
        info(f"ℹ --exclude {','.join(resolved.ignored_exclude)} は既定の母集合に無いため"
             f"無視しました（母集合: {', '.join(pool)}）")
    for name, reason in resolved.unavailable.items():
        info(f"⚠ {name} を担当から外しました（{reason}）")
    if not resolved.available:
        die(f"使える者がいません: 参加者の全員が確認を通りませんでした"
            f"（{' / '.join(f'{n}: {d}' for n, d in resolved.unavailable.items())}）")
    return resolved.to_state()


def _apply_post_event(state: dict[str, Any], is_own_pr: bool) -> None:
    """投稿の event に関する項目を状態へ入れる。

    初期化と再開の**両方**から呼ぶ。この指示が入る前の版で作った状態ファイルには
    項目そのものが無く、無いまま再開すると自分の Pull Request で `HTTP 422` を
    踏み続ける。値は GitHub 側の照合結果だけで決まるので、再開のたびに入れ直しても
    判定は変わらない。
    """
    state["is_own_pr"] = is_own_pr
    state["event_downgrade"] = is_own_pr


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
    m = _REPO_URL.search(sh(["git", "remote", "get-url", "origin"], check=False))
    return f"{m.group('owner')}/{m.group('name')}" if m else None


def _pr_payload(repo: str, pr: int) -> Optional[dict[str, Any]]:
    """`repos/{repo}/pulls/{pr}` の応答を返す。読めなければ `None`。"""
    out = sh(["gh", "api", f"repos/{repo}/pulls/{int(pr)}"], check=False)
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
        fallback = sh(
            ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
        body = _pr_payload(fallback, pr)
        if body is None:
            die(f"Pull Request #{pr} のメタデータを取得できません（リポジトリ名: {fallback}）")
            raise SystemExit(ABORT)
        resolved = fallback

    # **取得に失敗しても止めない。** bot トークン（Actions の `GITHUB_TOKEN` など）は
    # `/user` を読めず `HTTP 403` を返す。この値は自分の Pull Request かどうかの
    # 判定にしか使わないので、読めなければ他者の Pull Request として扱えばよい。
    viewer = sh(["gh", "api", "user", "--jq", ".login"], check=False)
    author = str((body.get("user") or {}).get("login") or "")
    is_own_pr = bool(viewer) and viewer == author
    head_branch = str((body.get("head") or {}).get("ref") or "")
    base_branch = str((body.get("base") or {}).get("ref") or "")
    return resolved, base_branch, head_branch, is_own_pr, author


def _plan_mode_of(plan_file: Optional[str]) -> str:
    """改修計画の置き場所を、`--plan-file` の与えられ方から決める。

    | 与え方 | 置き場所 |
    | --- | --- |
    | 指定しない（既定） | Pull Request のコメント 1 件 |
    | パスを指定 | そのファイル |
    | 空文字を指定 | 記録しない |
    """
    if plan_file is None:
        return PLAN_COMMENT
    return PLAN_FILE if str(plan_file).strip() else PLAN_NONE


@dataclass(frozen=True)
class InitialContext:
    repo: str
    base_branch: str
    head_branch: str
    root: pathlib.Path
    work: pathlib.Path
    tmp_dir: pathlib.Path
    host: str
    detection: str
    participants: dict[str, Any]
    model_spec: dict[str, Optional[str]]
    baseline: dict[str, Any]
    round_test: dict[str, Any]


def _build_initial_state(
    args: argparse.Namespace, ctx: InitialContext
) -> dict[str, Any]:
    """確定済みの材料から、初期の状態を組み立てて返す。

    **判断はここでは行わない。** ホストの検出・母集合の確定・認証・Pull Request の
    メタデータ・作業ディレクトリの用意・着手前のテストは、いずれも呼び出し側
    （`cmd_init`）が済ませたうえで値として渡す。この関数が持つのは、状態ファイルに
    何という鍵で何を残すかだけである。
    """
    runtimes = list(ctx.participants["available"])
    return {
        "id": args.pr,
        "started_at": statefile.now(),
        "repo": ctx.repo,
        "current_pr": args.pr,
        "base_branch": ctx.base_branch,
        "head_branch": ctx.head_branch,
        "worktree_root": str(ctx.root),
        "worktrees": {
            "work": str(ctx.work),
            **{r: str(ctx.root / r) for r in runtimes},
        },
        "tmp_dir": str(ctx.tmp_dir),
        "target_scope": list(args.scope),
        "host": ctx.host,
        "host_detection": ctx.detection,
        # **提案の対象と適用の輪番が同じ一覧を読む**（#727 の決定 5）。使える者と同じ値。
        "runtimes": runtimes,
        "participants": ctx.participants,
        "resume_changes": [],
        "models": ctx.model_spec,
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
        # 最終ゲートの分かれ道。**単独起動が既定である。**
        "workflow_step": bool(args.workflow_step),
        # **最初に開くのはテスト整備ラウンドである。** テストが乏しい箇所では、
        # 「テストが通ること」を検証に使えない（Step 5 の判定はテストで決まる）。
        "round_kind": TEST,
        "severity_threshold": args.severity_threshold,
        "baseline_test": ctx.baseline,
        # **群と修正コミットの検証が実行するテスト**（#880）。省けば全体テストと同じ。
        "round_test": ctx.round_test,
        # 生成物の同期は**進行側の責務**。push の直前に実行する。
        "sync_command": args.sync_command,
        # **改修計画の既定は Pull Request のコメント 1 件である**（#436 決定 6）。
        # `--plan-file` を明示したときだけファイルにし、空文字なら記録しない。
        "plan_mode": _plan_mode_of(args.plan_file),
        "plan_file": normalize_plan_file(args.plan_file),
        # 編集する先のコメント。**印で引き当て直せる**ので、失っても積み増さない。
        "plan_comment": None,
        "test_timeout": args.test_timeout,
        "outer_round": 0,
        "phase": "init",
        "rounds": [],
        "items": [],
        "deferred_items": [],
        "final": None,
    }


def cmd_init(args: argparse.Namespace) -> None:
    """Step 0 — ホストと参加者を確定し、作業ディレクトリ root と状態を用意する。

    **母集合は 1 つである**（#727 の決定 5）。提案と適用は同じ参加者で回す。参加者は
    codex / kiro とホストを既定とし、足す者・外す者で変える。確認を通らない者は外して
    続ける。前回の状態が残っていれば再開し、渡した引数を反映の表に従って扱う。
    """
    inputs = _resolve_init_inputs(args)
    if inputs is None:
        return
    prep = _prepare_init(args)
    if _resume_if_pending(args, inputs, prep):
        return

    for key, value in NEW_RUN_DEFAULTS.items():
        if getattr(args, key, None) is None:
            setattr(args, key, value)

    participants, baseline, round_record = _verify_init(args, inputs, prep)
    state = _save_initial_state(args, inputs, prep, participants, baseline, round_record)
    # **出力は入口から直接呼ぶ。** 手順書の変数の出所の検査
    # （`scripts/check-skill-shell-vars.py`）は `cmd_*` からヘルパーを 1 段だけたどる。
    _emit_init(state)


@dataclass
class _InitInputs:
    """`init` の引数から解決したホスト・モデル・足す者・外す者。"""

    host: str
    detection: str
    model_spec: dict[str, Optional[str]]
    include: Optional[list[str]]
    exclude: Optional[list[str]]


@dataclass
class _InitPreparation:
    """Pull Request の文脈と、用意した作業ディレクトリ。"""

    repo: str
    base_branch: str
    head_branch: str
    is_own_pr: bool
    root: pathlib.Path
    work: pathlib.Path
    tmp_dir: pathlib.Path
    state_file: pathlib.Path
    round_test: Optional[str]


def _resolve_init_inputs(args: argparse.Namespace) -> Optional[_InitInputs]:
    """ホスト・モデル・足す者・外す者を解決する。解決できなければ止めて `None` を返す。"""
    try:
        host, detection = assignment.detect_host(args.host)
    except assignment.AssignmentError as e:
        die(str(e))
        return None
    try:
        model_spec = models_lib.parse_model_args(args.model)
    except models_lib.ModelSpecError as e:
        die(str(e))
        return None
    return _InitInputs(
        host=host,
        detection=detection,
        model_spec=model_spec,
        include=_names_arg(args, "include"),
        exclude=_names_arg(args, "exclude"),
    )


def _prepare_init(args: argparse.Namespace) -> _InitPreparation:
    """Pull Request の文脈を取り、作業ディレクトリを用意して `--scope` の関門を通す。"""
    # リポジトリ名は git の設定から求め、Pull Request の応答で確かめる（#271）。
    repo, base_branch, head_branch, is_own_pr, author = _fetch_pr_context(args.pr)
    if is_own_pr:
        info(f"⚠ 自分の Pull Request です（作成者 {author}）— 投稿は COMMENT へ倒します")

    root = (
        pathlib.Path(args.worktree_root).resolve() if args.worktree_root
        else default_worktree_base() / repo_slug(repo) / f"rf{args.pr}"
    )
    work = root / "work"
    _ensure_work_worktree(work, head_branch)

    # **`--scope` の関門はここで通す**（#436 決定 5）。テストの置き場所が範囲に
    # 無い、または `--baseline-test` の実行集合に入らないまま進むと、テスト整備
    # ラウンドが足したテストが検証に効かない。案内だけでは同じ失敗を繰り返す
    # ため、**止める**。作業ディレクトリが要るのは、探索範囲の語がディレクトリか
    # どうかを実物で確かめるためである。
    # **足したテストが入るべき実行集合は `--round-test` である**（#880）。群の検証が
    # 走らせるのはこちらで、全体テストは着手前と最終ゲートにしか走らない。
    round_test = getattr(args, "round_test", None)
    if round_test:
        require_scope_covers_tests(args.scope, round_test, str(work), round_test=True)
    else:
        require_scope_covers_tests(args.scope, args.baseline_test, str(work))

    tmp_dir = tmp_dir_for(work)
    tmp_dir.mkdir(parents=True, exist_ok=True)
    return _InitPreparation(
        repo=repo,
        base_branch=base_branch,
        head_branch=head_branch,
        is_own_pr=is_own_pr,
        root=root,
        work=work,
        tmp_dir=tmp_dir,
        state_file=state_path(tmp_dir, args.pr),
        round_test=round_test,
    )


def _resume_if_pending(
    args: argparse.Namespace, inputs: _InitInputs, prep: _InitPreparation
) -> bool:
    """終わっていない前回の状態があれば再開し、`True` を返す。"""
    if not prep.state_file.exists():
        return False
    state = statefile.load(prep.state_file)
    if state.get("final") is not None:
        return False
    _resume(prep.state_file, state, args, inputs.model_spec,
            inputs.include, inputs.exclude, prep.is_own_pr)
    return True


def _verify_init(
    args: argparse.Namespace, inputs: _InitInputs, prep: _InitPreparation
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    """参加者を確定し、着手前のテストと範囲のテストを実行する。"""
    # **確認は着手前のテストより先に行う。** 使える者がいなければ、テストに時間を
    # 使わずに止める。
    participants = resolve_participants(
        inputs.host, inputs.include or [], inputs.exclude or [],
        bool(getattr(args, "require_all", None)))
    _warn_unmeasurable_models(inputs.model_spec, participants["available"])

    hint = round_test_hint(prep.round_test, args.baseline_test, args.scope, str(prep.work))
    if hint:
        info(hint)

    baseline = _run_baseline_test(args.baseline_test, prep.work, args.test_timeout)
    round_record = _run_round_test(prep.round_test, baseline, prep.work, args.test_timeout)
    return participants, baseline, round_record


def _save_initial_state(
    args: argparse.Namespace,
    inputs: _InitInputs,
    prep: _InitPreparation,
    participants: dict[str, Any],
    baseline: dict[str, Any],
    round_record: dict[str, Any],
) -> dict[str, Any]:
    """初期の状態を組み立てて保存し、保存した状態を返す。"""
    context = InitialContext(
        repo=prep.repo,
        base_branch=prep.base_branch,
        head_branch=prep.head_branch,
        root=prep.root,
        work=prep.work,
        tmp_dir=prep.tmp_dir,
        host=inputs.host,
        detection=inputs.detection,
        participants=participants,
        model_spec=inputs.model_spec,
        baseline=baseline,
        round_test=round_record,
    )
    state = _build_initial_state(args, context)
    # GitHub は自分の Pull Request への `APPROVE` と `REQUEST_CHANGES` を
    # `HTTP 422` で拒む。判定はそのまま結果ファイルへ残し、**投稿の event だけ**
    # を倒す。収束判定は結果ファイルの判定を見るので、倒しても進行は変わらない。
    _apply_post_event(state, prep.is_own_pr)
    statefile.save(prep.state_file, state)
    info(f"✅ 状態を初期化しました: {prep.state_file}")
    info(f"   ホスト: {inputs.host}（{inputs.detection}）")
    info(f"   参加者（提案と適用）: {' / '.join(state['runtimes'])}")
    return state


def _resume(
    state_file: pathlib.Path,
    state: dict[str, Any],
    args: argparse.Namespace,
    model_spec: dict[str, Optional[str]],
    include: Optional[list[str]],
    exclude: Optional[list[str]],
    is_own_pr: bool,
) -> None:
    """前回中断した状態から再開する（#727 / #648 の決定 13〜16）。

    上限は渡せば反映し、状態に載る他の引数は状態と違えば知らせる。足す者・外す者・
    全員を要する指定のどれかを渡したときだけ確かめ直し、**渡さなかった値は記録から
    補う**。作り直しは `resume_changes` に 1 件として積む。作り直しが失敗したときは
    書き込みの前に中断するため、状態ファイルは変わらない。
    """
    info(f"↻ 前回中断した状態から再開します（提案ラウンド {state.get('outer_round', 0)}）")
    for line in statefile.apply_resume_args(state, args, RESUME_REPLACE_FIELDS):
        info(line)
    view, given = _notify_view(state, args, model_spec)
    for line in statefile.apply_resume_args(view, given, RESUME_NOTIFY_FIELDS):
        info(line)

    require_all = getattr(args, "require_all", None)
    if include is not None or exclude is not None or require_all is not None:
        recorded = state.get("participants") or {}
        include_eff = include if include is not None else list(recorded.get("included") or [])
        # 渡さなかった除外は、共通の再開規則で記録から補う（#786 の AC4d）。
        exclude_eff = (exclude if exclude is not None
                       else assignment.recorded_exclusions(recorded, include_eff))
        participants = resolve_participants(
            str(state["host"]),
            include_eff,
            exclude_eff,
            bool(require_all) if require_all is not None else bool(recorded.get("require_all")),
        )
        state.setdefault("resume_changes", []).append({
            "at": statefile.now(), "field": "participants",
            "from": state.get("participants"), "to": participants,
        })
        state["participants"] = participants
        state["runtimes"] = list(participants["available"])
        worktrees = state.setdefault("worktrees", {})
        for runtime in state["runtimes"]:
            worktrees.setdefault(runtime, str(pathlib.Path(state["worktree_root"]) / runtime))

    _apply_post_event(state, is_own_pr)
    statefile.save(state_file, state)
    _emit_init(state)


def _notify_view(
    state: dict[str, Any], args: argparse.Namespace,
    model_spec: dict[str, Optional[str]],
) -> tuple[dict[str, Any], argparse.Namespace]:
    """「知らせる」の比較を、状態と引数の形を揃えて行うための写しを返す。

    状態は着手前のテストを `{command, status, checked_at}` で、モデルを全ランタイムの
    辞書で、作業ディレクトリ root を解決済みのパスで持つ。引数の形のまま比べると、
    同じ値でも「違う」と知らせてしまう。
    """
    view = dict(state)
    view["baseline_test"] = (state.get("baseline_test") or {}).get("command")
    view["round_test"] = (state.get("round_test") or {}).get("command")
    given = argparse.Namespace(**{f.arg: getattr(args, f.arg, None) for f in RESUME_NOTIFY_FIELDS})
    if given.model is not None:
        given.model = model_spec
    if given.worktree_root is not None:
        given.worktree_root = str(pathlib.Path(given.worktree_root).resolve())
    if given.plan_file is not None:
        given.plan_file = normalize_plan_file(given.plan_file)
    if given.scope is not None:
        given.scope = list(given.scope)
    return view, given


def _emit_init(state: dict[str, Any]) -> None:
    statefile.emit(
        ID=state["id"],
        REPO=state["repo"],
        HOST=state["host"],
        RUNTIMES=" ".join(state["runtimes"]),
        RUNTIMES_CSV=",".join(state["runtimes"]),
        WORKTREE_ROOT=state["worktree_root"],
        WORK=state["worktrees"]["work"],
        TMP_DIR=state["tmp_dir"],
        HEAD_BRANCH=state["head_branch"],
        BASE_BRANCH=state["base_branch"],
        SCOPE=" ".join(state["target_scope"]),
        # 適用・修正・最終ゲートの修正の担当はテストを 1 回実行し、その間は何も
        # 出力しない。テストの制限時間そのままでは、実行中に打ち切られる（#553）。
        IMPL_STALL_TIMEOUT=(
            safe_int(state.get("test_timeout"), DEFAULT_TEST_TIMEOUT)
            + IMPL_STALL_MARGIN
        ),
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
    sh(["git", "fetch", "origin", head_branch])
    # ローカルに head ブランチがあるかどうかで作り方が変わる。無い状態で
    # `worktree add <path> <branch>` を叩くと「そんなブランチは無い」で失敗する。
    exists = subprocess.run(
        ["git", "show-ref", "--verify", "--quiet", f"refs/heads/{head_branch}"],
        capture_output=True, text=True,
    ).returncode == 0
    if exists:
        sh(["git", "worktree", "add", str(work), head_branch])
    else:
        sh(["git", "worktree", "add", "-b", head_branch, str(work),
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
    out = sh(["git", "worktree", "list", "--porcelain"], check=False)
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
    code, timed_out = run_with_timeout(command, str(work), timeout)
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


def _run_round_test(
    command: Optional[str], baseline: dict[str, Any], work: pathlib.Path,
    timeout: int = DEFAULT_TEST_TIMEOUT,
) -> dict[str, Any]:
    """範囲のテストを着手前に 1 回実行して記録する（#880）。

    **省いたとき、または全体テストと同じ文字列のときは実行しない。** 同じコマンドを
    2 度走らせても判定は変わらず、時間だけが掛かる。全体テストの結果を写す。

    **失敗は全体テストと別に止める。** 全体テストが通っても範囲のテストが通らない
    （テストが 1 件も集まらない終了コード 5 を含む）なら、群の検証が初回から落ちる。
    """
    if not command or command == baseline["command"]:
        return {"command": baseline["command"], "status": baseline["status"],
                "checked_at": baseline["checked_at"]}
    code, timed_out = run_with_timeout(command, str(work), timeout)
    if timed_out:
        die(f"範囲のテストが {timeout} 秒で終わりませんでした（{command}）。打ち切りました")
        raise SystemExit(ABORT)
    if code != 0:
        die(
            f"範囲のテストが成功しません（{command} / 終了コード {code}）。"
            "--round-test が --scope のテストの置き場所を走らせるかを確かめてください"
        )
        raise SystemExit(ABORT)
    info(f"✅ 着手前の範囲のテスト成功: {command}")
    return {"command": command, "status": "green", "checked_at": statefile.now()}


def _new_round_entry(
    state: dict[str, Any], round_no: int, kind: str
) -> dict[str, Any]:
    """新しいラウンドの記録を組み立てる。"""
    impl, requested = impl_for_seq(state, round_no)
    return {
        "round": round_no,
        # **種類はラウンドごとに残す。** 上限を別々に数えるためと、提案の
        # 重複率を同じ種類どうしで測るためである。
        "kind": kind,
        "started_at": statefile.now(),
        "impl": impl,
        "impl_model": {"requested": requested, "observed": None},
        "proposed": {},
        "merged": 0, "adopted": 0, "deferred": 0,
        "items": [],
        "apply": {"applied": [], "failed": [], "base_sha": None, "head_sha": None},
        "fix_rounds": 0,
        "durations": {},
        "reviews": [],
    }


def _round_label_and_limit(
    state: dict[str, Any], kind: str
) -> tuple[str, Optional[int]]:
    """ラウンドの種類に対応する表示名と上限を返す。"""
    if kind == TEST:
        return "テスト整備ラウンド", state.get("max_test_rounds")
    return "提案ラウンド", state["max_outer_rounds"]


def cmd_start_round(args: argparse.Namespace) -> None:
    """Step 2 — ラウンドを開き、実装担当を返す。

    **レビュー担当は返さない**（#727 の決定 6）。レビュー工程は #436 で消え、Step 7 の
    cross-review が担う。

    終了コード: 0 = ラウンドを開いた / 1 = 繰り返しが終了済み。

    **開くのはテスト整備ラウンドか提案ラウンドのどちらかである。** どちらを開くかは
    状態の `round_kind` が持ち、切り替えるのは `advance` である（判定を 1 か所に
    まとめ、開く側は宣言に従うだけにする）。

    **再開しても担当は変わらない。** 同じラウンド番号を開き直したときは記録済みの
    割り当てをそのまま返す。
    """
    path, state = load_state(args.id)
    if state.get("final"):
        info(f"ラウンドの繰り返しは終了しています（{state['final']}）")
        sys.exit(1)

    rounds = state["rounds"]
    kind = round_kind(state)
    if kind == STRUCTURE and len(rounds_of_kind(state.get("rounds") or [], STRUCTURE)) >= state["max_outer_rounds"]:
        finish_outer_rounds(path, state, "max_outer_rounds")
        sys.exit(1)

    round_no = len(rounds) + 1
    existing = next((r for r in rounds if r["round"] == round_no), None)
    if existing is None:
        existing = _new_round_entry(state, round_no, kind)
        rounds.append(existing)
        state["outer_round"] = round_no
        state["phase"] = "propose"
        statefile.save(path, state)

    kind = entry_kind(existing)
    label, limit = _round_label_and_limit(state, kind)
    seq = len(rounds_of_kind(state.get("rounds") or [], kind))
    info(
        f"=== {label} {seq} / {limit} "
        f"（実装 {existing['impl']}）==="
    )
    statefile.emit(
        ROUND=round_no,
        ROUND_KIND=kind,
        # **母集合は繰り返しの中でも返す**（#518-1）。`init` だけが返す形では、
        # 状態ファイルから再開する経路と、骨組みを抜粋して写す経路の両方で
        # 未定義になる。出所は `init` と同じ状態ファイルの `runtimes` である。
        RUNTIMES=" ".join(state["runtimes"]),
        RUNTIMES_CSV=",".join(state["runtimes"]),
        # 提案に使う雛形の名前。**結果ファイルの名前は種類で変えない**
        # （ラウンド番号は通しなので衝突せず、監視の雛形をそのまま使える）。
        PROPOSE_PHASE="propose-tests" if kind == TEST else "propose",
        IMPL=existing["impl"],
        IMPL_MODEL=existing["impl_model"]["requested"],
        MAX_FIX_ROUNDS=state["max_fix_rounds"],
    )
