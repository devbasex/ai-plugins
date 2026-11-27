"""承認と判断の質問の前に、読み手役が確認した記録を調べる guard（#4285 の決定 10・11）。

見るのは質問の区分（`AskUserQuestion` の `metadata.source` の先頭）と、`reading-checks.jsonl` の行だけで、文の良し悪しは判定しない。
上から順に調べ、最初に当てはまったものに従う。

1. 区分が無い質問は通す
2. 人のいない実行（`NDF_FACTORY_RUN`）は通す
3. 記録を開けない・新しいバージョンの行がある・リポジトリの名前が決まらないときは通し、計測に `reading: unreadable` を残す
4. 同じ質問の記録が無い、または承認する PR の head が記録と違えば止める
5. ほかは通す。head を読めない PR があれば計測に `reading: no_head` を残す

PR の head は、手元のリモート追跡ブランチから git を 1 回だけ起動して読む。後の guard が質問を転送する時間を残すため、git に使うのは
hook の締め切りから余白を引いた残りまでとし、残りが無ければ起動しない（head を読めない PR として扱う）。
"""

from __future__ import annotations

import os
import subprocess
import time
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Optional

from ndf.adapters import git as git_mod
from ndf.domains.hooks import budget, tables
from ndf.domains.hooks.strict import _git_dir, _remote_slug, _repo_root
from ndf.domains.hooks.verdict import Verdict, deny, passing
from ndf.domains.records import reading_decide as rd

# 締め切りの時計（`svc.deadline` と同じ基準）。テストで差し替える
monotonic = time.monotonic

GATE = "ndf-gate:"
ASK = "ndf-ask:"
_KINDS = ((GATE, "承認を求める質問"), (ASK, "判断を求める質問"))
_REASONS = dict(
    no_record="この質問の文を読み手役が確認した記録が無い",
    head_moved="読み手役の確認の後に、承認する Pull Request のファイルが変わっている",
)
REASON = (
    "{kind}を止めた（{words}）。{why}。質問の文・その前に示す材料・読ませていない文書（直した部分）を読み手役に読ませ、"
    "`ndf records reading add --file <JSON>` で記録してから、同じ文の質問を出し直す。"
    "手順は Skill development-workflow の references/approval-request.md の「読み手役の確認」にある。"
)
# 承認ゲートの質問は、問う側が読み手役を起動しない。読み手役のステップを打ち直して質問を作り直す（#4446）
GATE_REASON = (
    "{kind}を止めた（{words}）。{why}。読み手役を自分で起動せず、`ndf records reading check --gate <種類> --pr <番号> "
    "--questions-out <ファイル>` で確かめ、合わなければ、工程の承認ゲートでは `ndf progress reread <ステートマシンの ID>`、"
    "会話のセッションのリリースでは `ndf document-rebuild gate --gate release --pr <番号> --material <ファイル>` を打ち直してから、"
    "記録した質問を 1 字も変えずに出す。手順は Skill development-workflow の references/approval-request.md にある。"
)


def check(ctx, svc) -> Verdict:
    kind = _kind(ctx.tool_input.get("metadata"))
    if kind is None or ctx.env.get("NDF_FACTORY_RUN"):
        return passing()
    root = _repo_root(Path(ctx.cwd))
    repo = _remote_slug(root) if root is not None else None
    rows = _recorded(_checks_path(ctx.env, svc.state_dir)) if repo is not None else None
    if root is None or repo is None or rows is None:
        return passing(reading="unreadable")
    key = rd.question_key(ctx.tool_input.get("questions"))
    row = rd.used_row(key, repo, rows)
    prs = _pulls_of(kind, row)
    heads = _tracked(root, prs, _git_seconds(ctx, svc)) if prs else {}
    words = rd.missing(key, repo, [row] if row is not None else [], heads)
    if words:
        return deny(denial(kind, words))
    return passing(reading="no_head") if len(heads) < len(prs) else passing()


def denial(kind: str, words: Sequence[str]) -> str:
    """止める理由。区分と語だけで決まる固定の文（同じ理由の拒否を数えるため、質問の文やハッシュ値を入れない）。"""
    template = GATE_REASON if kind == GATE else REASON
    return template.format(kind=dict(_KINDS)[kind], words="・".join(words), why="。".join(_REASONS[w] for w in words))


def _kind(metadata: Any) -> Optional[str]:
    """質問の区分（`ndf-gate:` か `ndf-ask:`）。区分の無い質問は None。"""
    source = metadata.get("source") if isinstance(metadata, dict) else None
    return next((k for k, _ in _KINDS if isinstance(source, str) and source.startswith(k)), None)


def _checks_path(env: Mapping[str, str], state_dir: Path) -> Path:
    """記録のファイル（`ndf.domains.records.reading.checks_path` と同じ保存先）。"""
    where = _fact("locations", "mvv-state")
    base = Path(env[where["env"]]) if env.get(where["env"]) else state_dir / where["path"]
    return base / _fact("ndf-files", "reading-checks")["path"]


def _fact(table: str, row_id: str) -> dict[str, Any]:
    return next(row for row in tables.load(f"domains/{table}.json")["rows"] if row["id"] == row_id)


def _recorded(path: Path) -> Optional[list[dict[str, Any]]]:
    """記録の行。ファイルが無ければ空。開けない・新しいバージョンの行があれば None。"""
    version = tables.load("schemas/internal/common.schema.json")["$defs"]["version"]["enum"][-1]
    try:
        return rd.light_rows(path.read_text(encoding="utf-8", errors="replace"), version)
    except FileNotFoundError:  # 記録がまだ無い
        return []
    except (OSError, rd.NewerRow):  # noqa: NDF401 ファイル: 記録を調べられなければ質問を止めない（決定 11）
        return None


def _pulls_of(kind: str, row: Optional[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """head を比べる PR（承認の質問で、使う記録に書かれたもの）。"""
    return list(row["prs"]) if kind == GATE and row is not None else []


def _git_seconds(ctx, svc) -> float:
    """git に使える秒: 締め切りから余白を引いた残りと、git を 1 回起動する上限の小さい方。予算が無ければ 0。"""
    found = budget.budget_for(ctx.runtime, ctx.event)
    if found is None:
        return 0.0
    return min(svc.deadline - found.margin - monotonic(), float(tables.policy_value("hook.git-rev-parse", "seconds")))


def _tracked(root: Path, prs: Sequence[Mapping[str, Any]], seconds: float) -> dict[int, str]:
    """PR の番号から、手元のリモート追跡ブランチの head。読めなかった PR は持たない（使える秒が無ければ空）。"""
    if seconds <= 0:
        return {}
    git_dir = _git_dir(root)
    try:
        remote = git_mod.remote_of(git_dir.parent if git_dir is not None else root)
        refs = {pr["number"]: f"refs/remotes/{remote}/{pr['branch']}" for pr in prs}
        found = _refs(root, sorted(set(refs.values())), seconds)
    except (OSError, subprocess.SubprocessError, git_mod.RemoteError):  # git の失敗では質問を止めない（決定 11）
        return {}
    return {number: found[ref] for number, ref in refs.items() if ref in found}


def _refs(root: Path, names: Sequence[str], seconds: float) -> dict[str, str]:
    """参照の名前から SHA。`for-each-ref` の指定は配下の参照にも当たるので、名前が同じものは呼び手が選ぶ。"""
    done = subprocess.run(
        ["git", "for-each-ref", "--format=%(objectname) %(refname)", *names],
        cwd=root, capture_output=True, text=True, check=True, timeout=seconds,
        env={**os.environ, **git_mod.NO_PROMPT},
    )  # fmt: skip
    pairs = [line.split(" ", 1) for line in done.stdout.splitlines()]
    return {ref: sha for sha, ref in pairs}
