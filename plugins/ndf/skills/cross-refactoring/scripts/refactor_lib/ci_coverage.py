"""継続的統合のジョブと宣言の突き合わせ（#464 E2）。

作業ディレクトリの `.github/workflows/` のジョブを、宣言の suite の `ci_jobs` と `test.ci_exempt` に照らし、
どれにも当たらないジョブ（宣言に無いジョブ）を挙げる。照合はジョブの識別子（`<パス>#<job id>` か、そのファイルの
ジョブすべてを指す `<パス>`）の文字列の一致だけで、glob も接頭辞も使わない（決定 3）。

結果を返すだけで、出力と状態への書き込みは呼ぶ側（`commands/setup.py`）が行う（決定 6）。起動を止めないため、
例外を外へ出さず「突き合わせられなかった」に倒す（I3）。
"""

from __future__ import annotations

import pathlib
from dataclasses import dataclass, field
from typing import Any

import ci_workflows
import test_strategy as ts

COMPARED = "compared"
SKIPPED = "skipped"
PROVIDER = "github-actions"
WORKFLOW_DIR = ".github/workflows"


@dataclass(frozen=True)
class CiCoverage:
    status: str
    reason: str = ""
    jobs: int = 0
    undeclared: list[str] = field(default_factory=list)

    def as_state(self) -> dict[str, Any]:
        return {"status": self.status, "reason": self.reason, "jobs": self.jobs, "undeclared": list(self.undeclared)}

    def lines(self) -> list[str]:
        """`init` が出す行。すべて覆うときは出さない（AC6）。"""
        if self.status == SKIPPED:
            return [f"ℹ 継続的統合のジョブと宣言を突き合わせられませんでした（{self.reason}）"]
        if not self.undeclared:
            return []
        return [
            f"🔎 継続的統合のジョブ {self.jobs} 件のうち、宣言に無いもの {len(self.undeclared)} 件（手元の検証では走りません）",
            *(f"   - {job}" for job in self.undeclared),
            "   直すときは、受け持つ suite の ci_jobs か test.ci_exempt に書きます（.ndf/project.json）",
        ]


def _declared_jobs(decl: dict[str, Any]) -> set[str]:
    """宣言が覆うジョブの識別子（suite の `ci_jobs` と `test.ci_exempt[].job`）。"""
    test = (decl or {}).get("test")
    if not isinstance(test, dict):
        return set()
    out: set[str] = set()
    for suite in test.get("suites") or []:
        if isinstance(suite, dict):
            out |= {j for j in suite.get("ci_jobs") or [] if isinstance(j, str)}
    for exempt in test.get("ci_exempt") or []:
        if isinstance(exempt, dict) and isinstance(exempt.get("job"), str):
            out.add(exempt["job"])
    return out


def _workflows(work: pathlib.Path) -> list[tuple[str, str]]:
    """作業ディレクトリのワークフロー（根からの相対パスと本文）を名前の順に。読めなければ `OSError` 系を投げる。"""
    base = work / WORKFLOW_DIR
    if not base.is_dir():
        return []
    files = sorted(p for p in base.iterdir() if p.suffix in (".yml", ".yaml") and p.is_file())
    out = []
    for p in files:
        rel = f"{WORKFLOW_DIR}/{p.name}"
        try:
            out.append((rel, p.read_text(encoding="utf-8")))
        except (OSError, UnicodeDecodeError) as e:
            raise _Unreadable(f"ワークフローを読めない（{rel}: {e}）") from None
    return out


class _Unreadable(Exception):
    pass


def compare_jobs(decl: dict[str, Any], work: pathlib.Path) -> CiCoverage:
    """宣言と作業ディレクトリのワークフローを突き合わせる。例外を外へ出さない（I3）。"""
    try:
        ci = ts.ci_of(decl)
        if ci is None:
            return CiCoverage(SKIPPED, "ci が無い")
        if ci.get("provider") != PROVIDER:
            return CiCoverage(SKIPPED, f"provider が {PROVIDER} でない（{ci.get('provider')}）")
        declared = _declared_jobs(decl)
        jobs = [(path, job) for path, text in _workflows(pathlib.Path(work)) for job in ci_workflows.job_ids(text)]
        undeclared = [f"{p}#{j}" for p, j in sorted(jobs) if p not in declared and f"{p}#{j}" not in declared]
        return CiCoverage(COMPARED, "", len(jobs), undeclared)
    except _Unreadable as e:
        return CiCoverage(SKIPPED, str(e))
    except Exception as e:  # noqa: BLE001 — 突き合わせは起動を止めない（I3）
        return CiCoverage(SKIPPED, f"突き合わせの途中で落ちた（{type(e).__name__}: {e}）")
