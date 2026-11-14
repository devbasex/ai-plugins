"""殻: 項目の検証で、宣言の検査とテストをファイルごとに当てて落ちた鍵を集める（#3339・#3049）。

- 宣言（`.ndf/project.json` の `test.suites[]`）のうち、`scope_command` に `{paths}` を持つ suite を使う。検査の suite
  （`kind` が `lint`）は項目が変えたファイルのうち suite の `paths` の中のものへ、テストの suite は項目が触るモジュールに
  対応するテストのファイル（`tests_decide.tests_for_change`）へ当てる
- テストを CI に寄せる宣言（`test.ci.only` が真）のリポジトリでは、手元ではテストの suite を打たない
- suite ごとにまとめて 1 回打ち、落ちたときだけファイルごとに打ち直して落ちたファイルを決める
- コマンドはテストの枠の下で、自分のプロセスの組で起こす（`slots.run_command`。上限で子孫まで止める）
- 項目の起点の鍵は、落ちた鍵だけを起点のコミットの別の作業場所で打ち直して決める（`verify_decide.attribute`）
"""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ndf.adapters import git
from ndf.contracts import limits
from ndf.core.converge import worktree
from ndf.domains.crossrefactor import scope, tests_decide, verify_decide
from ndf.domains.crossrefactor.diff_decide import in_scope
from ndf.testing import limits as test_limits
from ndf.testing import slots, weight

RUN_POLICY = "test-run.test"
SCOPE_KEY = "scope_command"


@dataclass(frozen=True)
class Suite:
    name: str
    template: str
    paths: tuple[str, ...]
    test: bool


def _read_declaration(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):  # noqa: NDF401 ファイル: 読めない宣言は suite が無いとして扱う
        return None


def _declaration(work: Path) -> Mapping[str, Any]:
    path = test_limits.find_setting(work, test_limits.PROJECT_FILE)
    data = _read_declaration(path) if path is not None else None
    test = data.get("test") if isinstance(data, dict) else None
    return test if isinstance(test, dict) else {}


def _suite(raw: Mapping[str, Any]) -> Suite | None:
    template = raw.get(SCOPE_KEY)
    if not isinstance(template, str) or tests_decide.PATHS_SLOT not in template:
        return None
    paths = raw["paths"] if isinstance(raw.get("paths"), list) else ["."]
    return Suite(str(raw["name"]), template, tuple(str(p) for p in paths), weight.is_test_suite(raw))


def suites(work: Path) -> list[Suite]:
    """検証に使う suite。テストを CI に寄せる宣言があれば検査の suite だけ。"""
    decl = _declaration(work)
    ci = decl.get("ci")
    ci_only = isinstance(ci, dict) and ci.get("only") is True
    raws = decl.get("suites")
    found = [s for s in (_suite(r) for r in (raws if isinstance(raws, list) else []) if isinstance(r, dict)) if s is not None]
    return [s for s in found if not (s.test and ci_only)]


def targets(work: Path, found: Sequence[Suite], changed: Sequence[str]) -> dict[str, list[str]]:
    """suite → 当てるファイル。検査は変えたファイル、テストは対応するテストのファイル。"""
    tests = list(scope.tests_for_change(work, changed).files) if any(s.test for s in found) else []
    out: dict[str, list[str]] = {}
    for s in found:
        files = [f for f in (tests if s.test else changed) if in_scope(f, s.paths) and (work / f).is_file()]
        if files:
            out[s.name] = files
    return out


def _passes(work: Path, command: str, timeout: float) -> bool:
    """`command` を枠の下で打って通ったか（`slots.run_command`。上限を超えたら子孫まで止めて落ちたとして数える）。"""
    try:
        done = slots.run_command(command, work, timeout)
    except subprocess.TimeoutExpired:  # noqa: NDF401 外部コマンド: 上限を超えた検査は落ちたとして数える
        return False
    return done.returncode == 0


def failing(work: Path, found: Sequence[Suite], picked: Mapping[str, Sequence[str]]) -> frozenset[str]:
    """`picked`（suite → ファイル）を作業場所 `work` で打ち、落ちた鍵（`<suite>:<ファイル>`）を返す。"""
    timeout = limits.seconds(RUN_POLICY)
    by_name = {s.name: s for s in found}
    keys: set[str] = set()
    for name, files in picked.items():
        template = by_name[name].template
        if _passes(work, tests_decide.fill_paths(template, files), timeout):
            continue
        keys.update(verify_decide.failure_key(name, f) for f in files if not _passes(work, tests_decide.fill_paths(template, [f]), timeout))
    return frozenset(keys)


def tests_failing(work: Path, found: Sequence[Suite], tests: Sequence[str]) -> frozenset[str]:
    """足したテストのファイル `tests` をテストの suite で打ち、落ちた鍵を返す（どの suite にも当たらないファイルは打たない）。"""
    picked = {s.name: [t for t in tests if in_scope(t, s.paths) and (work / t).is_file()] for s in found if s.test}
    return failing(work, found, {n: fs for n, fs in picked.items() if fs})


def regroup(keys: Iterable[str]) -> dict[str, list[str]]:
    """鍵の集まりを suite → ファイルへ戻す。"""
    out: dict[str, list[str]] = {}
    for key in sorted(keys):
        name, _, path = key.partition(":")
        out.setdefault(name, []).append(path)
    return out


def at_commit(probe: Path, commit: str, found: Sequence[Suite], keys: Iterable[str]) -> frozenset[str]:
    """別の作業場所 `probe` を `commit` へ合わせ、`keys` のうちそこで落ちる鍵を返す（項目の起点の失敗）。"""
    wanted = regroup(keys)
    if not wanted:
        return frozenset()
    worktree.detach_clean(git.Git(probe), commit)
    present = {n: [f for f in fs if (probe / f).is_file()] for n, fs in wanted.items()}
    return failing(probe, found, {n: fs for n, fs in present.items() if fs})
