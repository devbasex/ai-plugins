"""文脈の量の guard（SF12・#2595・#2789）。

会話の記録の末尾の最後の応答の文脈の量（token）が運用上限（`context_thresholds.thresholds` の `limit`。上限の設定の
`context.limit` と `NDF_CONTEXT_LIMIT`。#3995）以上なら、次の 1 手だけを残して止める。
中継の下では応答の最後に次のセグメントのブロックを書いてセグメントを切り替え、外では引き継ぎの文書を書いて新しいセッションで
続ける。拒否は同じ理由で数えられ、続けば案内へ下がる（#3345 の決定 92）。

計測のマーカー `under_relay` は判定したときだけ書く（判定しなかった行と中継の外を見分ける。#2789）。

サブエージェント（入力の `agent_id`）と、メインディレクトリに `.ndf` の無い会話は判定せず、会話の記録も読まない（#3995 の 3995:15）。
メインディレクトリは `.git` と管理ディレクトリの `commondir` だけから求め、git のコマンドを起動しない。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from ndf import identity
from ndf.domains.hooks import tables
from ndf.domains.hooks.context import context_tokens
from ndf.domains.hooks.strict import _repo_root
from ndf.domains.relay.context_thresholds import thresholds
from ndf.domains.relay.signals import relay_dir
from ndf.domains.hooks.verdict import Verdict, deny, passing


def _gitdir(dot: Path) -> Optional[Path]:
    """`gitdir: <X>` のファイルが指す管理ディレクトリの実パス（`X` は `.git` を持つディレクトリから解く）。"""
    try:
        line = dot.read_text(encoding="utf-8").strip()
    except OSError:  # noqa: NDF401 ファイル: .git を読めなければメインディレクトリは求められない
        return None
    if not line.startswith("gitdir:"):
        return None
    return Path(os.path.realpath(dot.parent / line[len("gitdir:") :].strip()))


def _main_of(root: Path) -> Optional[Path]:
    """`.git` を持つ `root` のメインディレクトリ。共通の git のディレクトリの親はメインディレクトリとみなさない。"""
    dot = root / ".git"
    if dot.is_dir():
        return root
    gitdir = _gitdir(dot)
    if gitdir is None:
        return None
    commondir = gitdir / "commondir"
    if not commondir.exists():
        return root  # 管理ディレクトリを外に保存するメインディレクトリ（--separate-git-dir）
    try:
        common = Path(os.path.realpath(gitdir / commondir.read_text(encoding="utf-8").strip()))
    except OSError:  # noqa: NDF401 ファイル: commondir を読めなければメインディレクトリは求められない
        return None
    parent = common.parent
    candidate = parent / ".git"
    if candidate.is_dir():
        return parent if Path(os.path.realpath(candidate)) == common else None
    return parent if candidate.is_file() and _gitdir(candidate) == common else None


def _main(cwd: str) -> Optional[str]:
    """`cwd` を含むリポジトリのメインディレクトリの実パス。求められないか `.ndf` のディレクトリが無ければ None。"""
    root = _repo_root(Path(os.path.realpath(cwd)))
    main = _main_of(root) if root is not None else None
    return str(main) if main is not None and (main / identity.FIXED_WORDS["ndf_settings_dir"]).is_dir() else None


def _next_step(ctx, handoff_dir: str) -> bool:
    """引き継ぎの文書を書く Tool の呼び出しか。"""
    path = ctx.tool_input.get("file_path")
    return ctx.tool in ("Write", "Edit") and isinstance(path, str) and handoff_dir in path


def check(ctx, svc) -> Verdict | None:
    if "agent_id" in ctx.payload or _main(ctx.cwd) is None:
        return None
    tokens = context_tokens(ctx.transcript_tail)
    if tokens is None:
        return None
    limit = thresholds(ctx.env, tables.policy_value).limit
    if tokens < limit:
        return passing(context_tokens=tokens)
    handoff_dir = svc.params["handoff_dir"]
    if _next_step(ctx, handoff_dir):
        return passing(context_tokens=tokens)
    under = relay_dir(ctx.env) is not None
    if under:
        step = "Tool を呼ばずに、応答の最後に次のセグメントのブロックを書いて終える（中継が新しいセッションで続ける）"
    else:
        step = f"引き継ぎの文書を {handoff_dir} の下へ Write で書き、新しいセッションで続ける"
    reason = f"文脈の量が上限（{limit} token）を超えた。次の 1 手: {step}"
    return deny(reason, notes={"context_tokens": tokens, "under_relay": under})
