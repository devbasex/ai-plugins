"""実行の入口。`init` と `start-phase` を持つ。

対象の Pull Request の文脈・参加者と実装担当の決定・Jev を使うかの判定・作業ツリーの
用意・状態ファイル（版 2）の初期化と再開と、フェーズの開始の記録を扱う（#933）。
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
import jev
import models as models_lib
import statefile

from .. import ABORT, die, info
from ..gitfacts import run_with_timeout
from .. import timeline
from ..paths import (
    git_out,
    default_worktree_base,
    repo_slug,
    sh,
    state_path,
    tmp_dir_for,
)
from ..plan import PLAN_COMMENT, PLAN_FILE, PLAN_NONE, normalize_plan_file
from ..scope import require_scope_covers_tests, round_test_hint
from ..testcmd import is_known
from ..vocabulary import (
    DEFAULT_BUDGET_MINUTES,
    DEFAULT_SEVERITY_THRESHOLD,
    REQUIRED_SKILLS,
    vocabulary,
)

# 状態ファイルの版（#933）。無い状態ファイルは v10.17.x までのラウンド制の形である。
SCHEMA = 2

# 廃止した引数（決定 5・決定 24）。この変更を含む版では知らせて無視し、その次の版で外す。
# 修正の回数（`--max-fix-rounds`）とテスト 1 回の上限（`--test-timeout`）は、想定最大
# 時間から逆算する（決定 24）。
DEPRECATED_ARGS = ("max_test_rounds", "max_outer_rounds", "max_items_per_round",
                   "max_fix_rounds", "test_timeout")

# 開始を記録するフェーズ（CLI を起動するもの）。
# 計画のフェーズより前（予算と実装担当を当て直してよい間）のフェーズ。
BEFORE_PLAN = ("propose", "plan")


# 再開で指定を外す予約語（#727 の決定 15）。足す者・外す者に渡すと一覧を空へ戻す。
NONE_WORD = "none"

# 新規の初期化で、未指定の引数を置き換える現行の既定。**引数の既定は `None` にする。**
# 既定値を引数に持たせると、再開で「渡さなかった」と「既定値を渡した」を区別できない
# （#727 の決定 13）。
NEW_RUN_DEFAULTS: dict[str, Any] = {
    "budget_minutes": DEFAULT_BUDGET_MINUTES,
    "severity_threshold": DEFAULT_SEVERITY_THRESHOLD,
    "workflow_step": False,
}

# 再開で渡した引数の反映の表（#727 の決定 13）。**状態ファイルに載る引数は、この表の
# どれかに必ず載る。** `replace` は状態へ書いて記録へ積み、`notify` は状態と違う
# ときだけ「反映しない」と知らせる。
# 予算は計画のフェーズより前だけ置き換える（設計の「再開」）。採用の件数・締め切り・
# 控えは `merge-plan` の時点の予算で固定されるため、それ以降は知らせるだけにする。
RESUME_BUDGET_REPLACE = (statefile.ResumeField("budget_minutes", "budget_minutes", "replace"),)
RESUME_BUDGET_NOTIFY = (statefile.ResumeField("budget_minutes", "budget_minutes", "notify"),)
RESUME_NOTIFY_FIELDS = (
    statefile.ResumeField("implementer", "implementer_named", "notify"),
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
    implementer: str
    implementer_reason: str
    judge: dict[str, Any]


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
        "schema": SCHEMA,
        "id": args.pr,
        "started_at": statefile.now(),
        "budget_minutes": args.budget_minutes,
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
        # **提案は参加者の全員、計画以降は実装担当 1 者**（#933 の決定 1）。
        "runtimes": runtimes,
        "participants": ctx.participants,
        "implementer": ctx.implementer,
        "implementer_reason": ctx.implementer_reason,
        # 名指しの記録。再開で `--implementer` を比べる相手（置き換えない。知らせるだけ）。
        "implementer_named": getattr(args, "implementer", None),
        "implementer_model": {
            "requested": (ctx.model_spec or {}).get(ctx.implementer), "observed": None,
        },
        "judge": ctx.judge,
        "resume_changes": [],
        "models": ctx.model_spec,
        # 提案プロンプトへ許容値をそのまま列挙するために持たせる。
        # 定義は検証側（この CLI）にあり、状態ファイル経由で起動側へ渡す。
        "vocabulary": vocabulary(),
        "skills": {"required": list(REQUIRED_SKILLS)},
        # 最終ゲートで手元のテストの代わりに見る検査の名前。**排他である**
        # （指定があれば手元のテストを実行しない）。
        "ci_check": args.ci_check,
        # 最終ゲートの分かれ道。**単独起動が既定である。**
        "workflow_step": bool(args.workflow_step),
        "severity_threshold": args.severity_threshold,
        "baseline_test": ctx.baseline,
        # 項目の検証の元になるコマンド（#880 / #933 の AC10b）。省けば全体テストを元に組み立てる。
        "round_test": ctx.round_test,
        # 生成物の同期は**進行側の責務**。push の直前に実行する。
        "sync_command": args.sync_command,
        # **改修計画の既定は Pull Request のコメント 1 件である**（#436 決定 6）。
        # `--plan-file` を明示したときだけファイルにし、空文字なら記録しない。
        "plan_mode": _plan_mode_of(args.plan_file),
        "plan_file": normalize_plan_file(args.plan_file),
        # 編集する先のコメント。**印で引き当て直せる**ので、失っても積み増さない。
        "plan_comment": None,
        "phase": "propose",
        # フェーズの所要。**進行側の時計で測る**（決定 8）。
        "phases": {},
        "candidates": [],
        "plan": None,
        "items": [],
        "deferred_items": [],
        "whole_test": {"ran": False, "flags": [], "status": None, "seconds": None,
                       "head": None, "reverted": False},
        "verify_stats": {"items": 0, "seconds": 0.0},
        "fix_stats": {"launches": 0, "seconds": 0.0},
        "final_gate": {"fix_rounds": 0, "checks": []},
        "pending_push": False,
        "pending_drop": None,
        "history_written": False,
    }


def cmd_init(args: argparse.Namespace) -> None:
    """Step 0 — ホスト・参加者・実装担当を確定し、作業ディレクトリと状態を用意する。

    **提案は参加者の全員、計画以降は実装担当 1 者が通す**（#933 の決定 1）。前回の状態が
    残っていれば再開し、渡した引数を反映の表に従って扱う。旧い形（ラウンド制）で
    終わっていない状態ファイルは読み替えずに止める（決定 18）。
    """
    _normalize_args(args)
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


def _normalize_args(args: argparse.Namespace) -> None:
    """予算の検査と、廃止した引数の知らせ。**提案の前に止める**（AC1 AC2 AC3b）。"""
    raw = getattr(args, "budget_minutes", None)
    if raw is not None:
        try:
            value = int(str(raw).strip())
        except ValueError:
            value = 0
        if value < 1 or str(raw).strip() != str(value):
            die(f"--budget-minutes は 1 以上の整数で指定してください: {raw}")
        args.budget_minutes = value
    for name in DEPRECATED_ARGS:
        if getattr(args, name, None) is not None:
            option = "--" + name.replace("_", "-")
            print(f"⚠ {option} は廃止しました（#933）。--budget-minutes で所要を決めます",
                  file=sys.stderr, flush=True)
    # AC3b: 範囲のテストが無く、全体のテストから限ったテストを組み立てられないなら、
    # 提案と計画に時間を使った後で全項目が `no_target` になる。着手前に止める。
    if not getattr(args, "round_test", None) and not is_known(args.baseline_test):
        die(
            f"--baseline-test（{args.baseline_test}）からは項目ごとのテストを組み立てられません"
            "（既知の実行器: pytest / python -m pytest / jest / vitest）。"
            "--round-test で範囲のテストを渡してください"
        )


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
    """終わっていない前回の状態があれば再開し、`True` を返す。

    | 前回の状態 | 扱い |
    | --- | --- |
    | 無い | 新しく始める |
    | 版 2 で `phase` が `done` | 新しく始める（状態を作り直す） |
    | 版 2 で終わっていない | 再開する |
    | 旧い形（`schema` を持たず `rounds` を持つ）で `final` が空 | **止める**（決定 18） |
    | 旧い形で `final` が入っている | 新しく始める（版 2 の形で作り直す） |
    """
    if not prep.state_file.exists():
        return False
    state = statefile.load(prep.state_file)
    if state.get("schema") != SCHEMA:
        if "rounds" in state and state.get("final") is None:
            die(
                "旧い版（ラウンド制）の状態ファイルが途中のまま残っています。"
                f"旧い版（v10.17.5 以前）で終えるか、{prep.state_file} を消して始め直してください"
            )
        info(f"ℹ 旧い版の終わった状態ファイルを版 {SCHEMA} の形で作り直します")
        return False
    if state.get("phase") == "done":
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

    # 着手前のテストの上限は予算から導く（決定 24。全体のテストの実測はまだ無い）。
    timeout = timeline.init_test_timeout(args.budget_minutes)
    baseline = _run_baseline_test(args.baseline_test, prep.work, timeout)
    round_record = _run_round_test(prep.round_test, baseline, prep.work, timeout)
    return participants, baseline, round_record


def _choose_implementer(
    participants: dict[str, Any], host: str, named: Optional[str],
) -> tuple[str, str]:
    """実装担当を決める（決定 1）。名指しが参加者に無ければ中断する（AC21）。"""
    try:
        return assignment.choose_implementer(list(participants["available"]), host, named)
    except assignment.AssignmentError as e:
        die(str(e))
        raise


def _repo_is_public(repo: str) -> Optional[bool]:
    """対象のリポジトリが公開か。判定できなければ `None`（Jev を使わない側へ倒す）。"""
    out = sh(["gh", "repo", "view", repo, "--json", "visibility", "-q", ".visibility"],
             check=False)
    if not out:
        return None
    return out.strip().upper() == "PUBLIC"


def decide_judge(repo: str) -> dict[str, Any]:
    """この実行で Jev を使うかを 1 度だけ決める（決定 2・AC22 AC23）。"""
    judge = jev.decide(lambda: _repo_is_public(repo))
    if judge["kind"] == "jev":
        info("✅ 判断の一部（段・同じ変更か・D5）を Jev に問います")
    else:
        info(f"ℹ Jev は使いません（{judge['reason']}）。判断は実装担当が行います")
    return judge


def _save_initial_state(
    args: argparse.Namespace,
    inputs: _InitInputs,
    prep: _InitPreparation,
    participants: dict[str, Any],
    baseline: dict[str, Any],
    round_record: dict[str, Any],
) -> dict[str, Any]:
    """初期の状態を組み立てて保存し、保存した状態を返す。"""
    implementer, reason = _choose_implementer(
        participants, inputs.host, getattr(args, "implementer", None))
    context = InitialContext(
        implementer=implementer,
        implementer_reason=reason,
        judge=decide_judge(prep.repo),
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
    # **実行時の値を書き出す**（決定 24）。計画の後の値は `merge-plan` が足す。
    state["limits"] = timeline.of_state(state)
    info(f"   実装担当: {context.implementer}（{context.implementer_reason}）")
    # GitHub は自分の Pull Request への `APPROVE` と `REQUEST_CHANGES` を
    # `HTTP 422` で拒む。判定はそのまま結果ファイルへ残し、**投稿の event だけ**
    # を倒す。収束判定は結果ファイルの判定を見るので、倒しても進行は変わらない。
    _apply_post_event(state, prep.is_own_pr)
    statefile.save(prep.state_file, state)
    info(f"✅ 状態を初期化しました: {prep.state_file}")
    info(f"   ホスト: {inputs.host}（{inputs.detection}）")
    info(f"   参加者（提案）: {' / '.join(state['runtimes'])}"
         f" / 想定最大時間: {state['budget_minutes']} 分")
    return state


def _rebuild_participants(
    state: dict[str, Any],
    include: Optional[list[str]],
    exclude: Optional[list[str]],
    require_all: Optional[bool],
) -> None:
    """再開時の指定を補完し、参加者と作業ツリーの記録を作り直す。"""
    recorded = state.get("participants") or {}
    include_eff = include if include is not None else list(recorded.get("included") or [])
    # `--exclude` を渡さない再開では、外した者と無視した除外の両方を足し戻す（#786 の AC4d。
    # 規則は cross-review と共通の `assignment.recorded_exclusions`）
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


def _resume(
    state_file: pathlib.Path,
    state: dict[str, Any],
    args: argparse.Namespace,
    model_spec: dict[str, Optional[str]],
    include: Optional[list[str]],
    exclude: Optional[list[str]],
    is_own_pr: bool,
) -> None:
    """前回中断した状態から再開する（#727 / #648 の決定 13〜16、#933 の「再開」）。

    上限は渡せば反映し、状態に載る他の引数は状態と違えば知らせる。予算は計画の
    フェーズより前だけ置き換える。足す者・外す者・全員を要する指定のどれかを渡した
    ときだけ確かめ直し、**渡さなかった値は記録から補う**。作り直しが失敗したときは
    書き込みの前に中断するため、状態ファイルは変わらない。
    """
    info(f"↻ 前回中断した状態から再開します（フェーズ {state.get('phase')}）")
    budget_spec = RESUME_BUDGET_REPLACE if _before_plan(state) else RESUME_BUDGET_NOTIFY
    for line in statefile.apply_resume_args(state, args, budget_spec):
        info(line)
    view, given = _notify_view(state, args, model_spec)
    for line in statefile.apply_resume_args(view, given, RESUME_NOTIFY_FIELDS):
        info(line)

    require_all = getattr(args, "require_all", None)
    if include is not None or exclude is not None or require_all is not None:
        _rebuild_participants(state, include, exclude, require_all)
        _recheck_implementer(state)

    _apply_post_event(state, is_own_pr)
    # **予算を置き換えたら上限の表を組み直す**（計画の前だけ。計画の後は表を変えない）。
    if not state.get("plan"):
        state["limits"] = timeline.of_state(state)
    statefile.save(state_file, state)
    _emit_init(state)


def _before_plan(state: dict[str, Any]) -> bool:
    """計画を取り込む前か（予算と実装担当を当て直してよい間）。"""
    return state.get("phase") in BEFORE_PLAN and not state.get("plan")


def _recheck_implementer(state: dict[str, Any]) -> None:
    """参加者を作り直した結果、実装担当が外れていないかを確かめる（設計の「再開」）。

    計画の前なら決め方を当て直して記録に積む。計画の後なら止める。計画・テスト・実装を
    担った者が途中で替わると、見積りの前提と、項目とコミットの対応を読む者が食い違う。
    """
    current = state.get("implementer")
    if current in state["runtimes"]:
        return
    if not _before_plan(state):
        die(f"実装担当 {current} が参加者から外れました。計画の後は実装担当を替えられません")
    implementer, reason = _choose_implementer(
        state["participants"], str(state["host"]), state.get("implementer_named"))
    state.setdefault("resume_changes", []).append({
        "at": statefile.now(), "field": "implementer",
        "from": current, "to": f"{implementer}（{reason}）",
    })
    state["implementer"], state["implementer_reason"] = implementer, reason
    state["implementer_model"] = {
        "requested": (state.get("models") or {}).get(implementer), "observed": None,
    }
    info(f"↻ 実装担当を {implementer} へ替えました（{reason}）")


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
    # 範囲のテストを省いた（または全体のテストと同じ文字列だった）実行は `None` を持つ。
    # 同じ文字列を渡し直した再開を「違う」と知らせないため、全体のテストと同じなら同じと読む。
    recorded = (state.get("round_test") or {}).get("command")
    given_round = getattr(args, "round_test", None)
    if recorded is None and given_round == view["baseline_test"]:
        recorded = given_round
    view["round_test"] = recorded
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
        # 計画・テスト追加・実装・修正を通す 1 者（決定 1）。再開しても変わらない。
        IMPL=state["implementer"],
        IMPL_MODEL=(state.get("implementer_model") or {}).get("requested") or "",
        # 再開の地点。駆動は終わったフェーズを飛ばす（AC24）。
        PHASE=state.get("phase") or "propose",
        BUDGET_MINUTES=state["budget_minutes"],
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


def _run_baseline_test(command: str, work: pathlib.Path, timeout: int) -> dict[str, Any]:
    """着手前のテストを実行して記録する。

    失敗している状態で構造改善に入ると、**壊したのか元から壊れていたのか**
    区別できない。そもそも振る舞いが変わっていないことを示す手段が無い書き換えは
    構造改善ではないため、テストコマンドは必須にしている。
    """
    started = time.monotonic()
    code, timed_out = run_with_timeout(command, str(work), timeout)
    seconds = round(time.monotonic() - started, 1)
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
    info(f"✅ 着手前のテスト成功: {command}（{seconds} 秒）")
    # **所要を残す。** 危険の印と最終ゲートの全体のテストの控えを、この秒から見積もる。
    # **HEAD も残す。** 危険の印の全体のテストが落ちたとき、元からの失敗かをこの SHA で
    # 見分け（決定 22）、報告と改修計画に基準として出す。
    return {"command": command, "status": status, "checked_at": statefile.now(),
            "seconds": seconds, "head": git_out(str(work), ["rev-parse", "HEAD"])}


def _run_round_test(
    command: Optional[str], baseline: dict[str, Any], work: pathlib.Path, timeout: int,
) -> dict[str, Any]:
    """範囲のテストを着手前に 1 回実行して記録する（#880）。

    **省いたとき、または全体テストと同じ文字列のときは実行しない。** 同じコマンドを
    2 度走らせても判定は変わらず、時間だけが掛かる。全体テストの結果を写す。

    **失敗は全体テストと別に止める。** 全体テストが通っても範囲のテストが通らない
    （テストが 1 件も集まらない終了コード 5 を含む）なら、群の検証が初回から落ちる。
    """
    if not command or command == baseline["command"]:
        # **省いたことを残す。** 項目の検証は `--round-test` をそのまま使えず、
        # 全体のテストから組み立てた語の並びだけを使う（AC10b）。
        return {"command": None, "status": baseline["status"],
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
