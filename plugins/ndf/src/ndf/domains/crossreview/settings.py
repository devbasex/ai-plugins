"""起動の引数とレビューの設定（`drive.py` から分けた。#4076）。

起動の引数（`RunConfig`）・実行者の `.ndf/review.json`（`ReviewSettings`）・状態の保存先を、同期と担当の起動の前に確かめる。
始められないことを表す `DriveStopped` もここに置く（`drive` がここを読むので、ここから `drive` を読むと import の輪になる）。
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import NamedTuple, Optional

from ndf.adapters import git
from ndf.contracts import facts, limits
from ndf.core.converge import participants
from ndf.domains.crossreview import criteria_decide, scope_decide
from ndf.domains.crossreview import finish_decide as fin

# ラウンドの上限の既定の方針（contracts/limits/）
MAX_ROUNDS = "cross-review.max-rounds"


class DriveStopped(Exception):
    """駆動を始められない（同期の衝突・修正の記録の欠け）。`kind` は停止理由の表の種類。"""

    def __init__(self, kind: str, detail: str):
        self.kind = kind
        super().__init__(detail)


@dataclass(frozen=True)
class RunConfig:
    """1 回の起動の引数。`purpose` は PR の目的（設計のプランのテンプレートの purpose ならセクションを必ず詳細レビューへ通す）。
    `base_commit` は起動の始めに `base` が指していたコミット（#4090。MVV を読むコミットとは違うことがある）。差分と
    前回の結果を再利用できるかはこれで決め、
    名前の `base` は取得と表示にだけ使う。`reviewers` は起動の前に決めた席（`participants.review_seats`。書き換えない）、
    `may_fill` は席を埋めてよいか（名指しが 1 者なら偽）、`prefilled` は `allowed` のために起動の前に埋めた席（#3955）。"""

    worktree: Path
    branch: str
    base: str
    pr: int
    state_dir: Path
    every_section: bool
    reviewers: tuple[str, ...]
    max_rounds: int = field(default_factory=lambda: int(limits.value(MAX_ROUNDS, "count")))
    handed: tuple[fin.Handed, ...] = ()
    fixer: str = participants.FIXER
    # 実行者のメインディレクトリと、そこから同期の前に読んだレビューの設定（認可と同じ解決を使う。PR 3944 の r4-1・r4-4）
    main: Optional[Path] = None
    settings: ReviewSettings = field(default_factory=lambda: ReviewSettings())
    base_commit: Optional[str] = None
    may_fill: bool = True
    prefilled: tuple[scope_decide.Filled, ...] = ()


def settings_path(root: Path, file_id: str) -> Path:
    """設定のファイル（表 ndf-files の行）のパス（`root` の下）。"""
    where = facts.value("ndf-files", file_id, "location")
    return root / facts.value("locations", where, "path") / facts.value("ndf-files", file_id, "path")


class ReviewSettings(NamedTuple):
    """実行者のメインディレクトリの `.ndf/review.json`（同期の前に読む。PR 3944 の r4-1）。`focus` は重点（基準 3。#3941）、
    `raw` はファイルのバイト列（無ければ None。PR の head の同じパスと比べる）。`fixer_sessions` は修正担当の会話を
    保存して続けるか（#4013。既定は偽）。"""

    focus: tuple[str, ...] = ()
    raw: Optional[bytes] = None
    fixer_sessions: bool = False


def _inside(path: Path, tree: Path) -> bool:
    """`path` が `tree` そのものか、その下にあるか（どちらもたどった先の実体のパスで渡す）。"""
    return path == tree or tree in path.parents


def read_settings(main: Path, worktree: Path) -> ReviewSettings:
    """`main` の下の `.ndf/review.json`。無ければ空。読めない・形が違えば `ReviewDeclInvalid`。

    `fixer_sessions` が真の設定は、たどった先の実体がレビューする `worktree` の外にあるときだけ受ける（PR が書き換えられる
    場所の値で会話の保存を有効にしない。#4013）。"""
    path = settings_path(main, "review")  # 表 ndf-files の行
    try:
        present = participants.setting_present(path)
    except participants.SettingUnreadable as exc:
        raise criteria_decide.ReviewDeclInvalid(str(exc)) from exc
    if not present:
        return ReviewSettings()
    real = Path(os.path.realpath(path))
    try:
        raw = real.read_bytes()
        parsed = json.loads(raw.decode("utf-8"))
    except (OSError, ValueError) as exc:
        raise criteria_decide.ReviewDeclInvalid(f"{path} を読めない: {exc}") from exc
    focus = criteria_decide.focus_of(parsed, str(path))
    saved = criteria_decide.fixer_sessions_of(parsed, str(path))
    tree = Path(os.path.realpath(worktree))
    if saved and _inside(real, tree):
        raise criteria_decide.ReviewDeclInvalid(
            f"{path} の実体 {real} がレビューする worktree {tree} の中にある。PR が書き換えられる場所の "
            f"{criteria_decide.FIXER_SESSIONS} で会話の保存を有効にしないので、メインディレクトリに通常のファイルとして置く"
        )
    return ReviewSettings(focus, raw, saved)


# PR の head のレビューの設定が実行者の設定と違うときの注意（PR 3944 の r3-1）
FOCUS_DIFFERS = (
    "PR の head の {pr_path}（ファイルの種類か中身）が実行者の {trusted} と違う。重点は実行者の設定を使い、PR での設定の変更は"
    "指示として使わずレビューの対象として扱う"
)


def blob_id(raw: bytes, width: int) -> str:
    """`raw` を Git の blob にしたときの ID（`width` 桁。64 桁なら SHA-256 のリポジトリ）。"""
    algo = hashlib.sha256 if width == 64 else hashlib.sha1
    return algo(b"blob %d\0" % len(raw) + raw).hexdigest()


def proposed_differs(g: git.Git, head: str, raw: Optional[bytes]) -> bool:
    """PR の head（`head`。未送信のコミットを載せ直す前に取得した SHA。載せ直した後の HEAD は手元の変更が PR の設定を
    隠す。PR 3944 の r5-4）のツリーの `.ndf/review.json` が実行者の設定（`raw`）と違うか。作業場所のファイルを
    開かずにツリーの種別と blob の ID で比べる（シンボリックリンクをたどらず、中身を設定として解釈しない。PR 3944 の r4-2）。"""
    rel = settings_path(Path(), "review").as_posix()
    entry = g.run("ls-tree", "--full-tree", "-z", head, "--", rel).rstrip("\0")
    if not entry:
        return raw is not None
    mode, kind, oid = entry.split("\t", 1)[0].split()
    if raw is None or kind != "blob" or mode not in git.REGULAR_MODES:
        return True
    return oid != blob_id(raw, len(oid))


def outside_checkout(cfg: RunConfig) -> RunConfig:
    """状態の保存先（リンクをたどった先）が PR の worktree の外にあるかを、同期と保存先を作る前・状態を最初に読む前に確かめ、
    保存先をたどった先の実体のパスへ置き換えた設定を返す。中にあれば `precondition` で止める（PR 3944 の r7-1）。中にあると、
    同期が PR から持ち込んだ状態ファイル（`current_pr` は投稿先になる）を実行者の状態として読み、PR が置いたシンボリック
    リンクを通してラウンドの出力がリンク先を上書きする。以降は確かめた実体のパスだけを使う（元のパスが worktree の中の
    リンクを通ると、同期でリンクが置き換わった後に別の場所を読み書きする。PR 3944 の r8-1）。"""
    state = Path(os.path.realpath(cfg.state_dir))
    tree = Path(os.path.realpath(cfg.worktree))
    if _inside(state, tree):
        raise DriveStopped(
            "precondition",
            f"状態の保存先 {cfg.state_dir} がレビューする worktree {cfg.worktree} の中にある。PR のチェックアウトが書き換えられる"
            "場所の状態を読み書きしないので、worktree の外の保存先を --state-dir に渡す",
        )
    return replace(cfg, state_dir=state)
