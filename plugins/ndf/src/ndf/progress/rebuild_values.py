"""工程「設計」「ドキュメント再構成」の差し込み `rebuild` の既定（#4337 の決定 1・3・7）。

設定（`workflow.launch.rebuild`）が無ければ、`ndf document-rebuild run` の語の並びを組む。対象の文書はステップを打つ時に
サブコマンドが選ぶので、ここは差分を読まない。ベースブランチとリモートの名前は 1 回だけ決め、テンプレートが使う
`base`・`remote` にも同じ値を入れる（`route.derived_locally` が求め直さない）。決められなければ理由を返す。
工程とモードから選ぶ文書の種類を決める対応はここが持つ（`ndf.domains.docrebuild` は `ndf.progress` を読まない）。
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

from ndf import identity
from ndf.adapters.git import Git, GitError
from ndf.core import branch_strategy, route

SETTING = "workflow.launch.rebuild"
# 設定のキーの道（`invoke.PROJECT_KEYS["rebuild"]` もここから読む）
SETTING_PATH = tuple(SETTING.split("."))
SHAPE = "コマンドの語の並び（空でない文字列を 1 つ以上持つ配列）"
# テンプレートの名前 → モード → `--kind` の並び（None のキーがほかのモード。設計書の「既定のコマンド」の表）
KINDS: dict[str, dict[Optional[str], tuple[str, ...]]] = {
    "design": {None: ("design",)},
    "document-rebuild": {"documentation": ("design", "plan", "manuscript"), None: ("design", "plan")},
}
NO_ISSUES = "工程の入力に課題が無い"


@dataclass(frozen=True)
class Undecided:
    """既定を決められなかった理由。"""

    reason: str

    def fields(self) -> dict[str, str]:
        """欠けの行へ足す 3 つのキー（理由・設定のキー・値の形）。"""
        return {"reason": self.reason, "setting": SETTING, "shape": SHAPE}


@dataclass(frozen=True)
class Filled:
    """足す差し込みの値か、決められなかった理由のどちらか。"""

    values: dict[str, route.Value] = field(default_factory=dict)
    undecided: Optional[Undecided] = None


def kinds_for(template_name: str, mode: Optional[str]) -> tuple[str, ...]:
    """テンプレートの名前とモードから `--kind` の並び。表に無い名前は空。"""
    if template_name not in KINDS:
        return ()
    table = KINDS[template_name]
    return table[mode] if mode in table else table[None]


def launch(base: str, remote: str, issues: Sequence[str], kinds: Sequence[str]) -> list[str]:
    """既定のコマンドの語の並び。`{state_dir}` はプランの実行が絶対パスへ置き換え、`--out` はテンプレートが足す。"""
    argv = [identity.FIXED_WORDS["entrypoint"], "document-rebuild", "run", "--base", base, "--remote", remote]
    for n in issues:
        argv += ["--issue", str(n)]
    for k in kinds:
        argv += ["--kind", k]
    return [*argv, "--state-dir", "{state_dir}"]


def _still_open(t: route.Template, name: str, values: Mapping[str, route.Value]) -> bool:
    return name in t.params and name not in values


def fill(
    t: route.Template,
    values: Mapping[str, route.Value],
    issues: Sequence[str],
    mode: Optional[str],
    cwd: Path,
    *,
    project: Mapping[str, Any],
) -> Filled:
    """設定のコマンドが無いときだけ、`rebuild` と、テンプレートが使いまだ値の無い `base`・`remote` を決める。

    `base` を先に決め、決められなければ `remote` を求めずに返す（子プロセスを打たずに止まる）。
    """
    kinds = kinds_for(t.name, mode)
    if "rebuild" not in t.params or "rebuild" in values or not kinds:
        return Filled()
    if not issues:
        return Filled(undecided=Undecided(NO_ISSUES))
    added: dict[str, route.Value] = {}
    base = values.get("base")
    if not isinstance(base, str):
        try:
            base = branch_strategy.of_project(project).pr_target("sprint")
        except branch_strategy.StrategyError as exc:
            return Filled(undecided=Undecided(exc.reason))
        if _still_open(t, "base", values):
            added["base"] = base
    remote = values.get("remote")
    if not isinstance(remote, str):
        try:
            remote = Git(cwd).remote()
        except GitError as exc:
            return Filled(undecided=Undecided(str(exc)))
        if _still_open(t, "remote", values):
            added["remote"] = remote
    return Filled({"rebuild": launch(base, remote, issues, kinds), **added})
