"""落ちたテストの組み立て（`ndf.adapters.ci.failed_tests_of`）のポートの偽の GitHub と、JUnit の成果物の作り方。

`tests/adapters/test_ci.py` と `tests/domains/crossrefactor/test_gate_and_probe.py` が使う。
"""

from __future__ import annotations

import io
import zipfile

from ndf.adapters.github import GitHubError


def zip_of(*xmls: bytes) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        for i, xml in enumerate(xmls):
            z.writestr(f"junit-{i}.xml", xml)
    return buf.getvalue()


def xml(*cases: tuple[str, str]) -> bytes:
    rows = "".join(f'<testcase file="{f}" name="{n}"><failure/></testcase>' for f, n in cases)
    return f"<testsuites><testsuite>{rows}</testsuite></testsuites>".encode()


class FakeHub:
    """ジョブ → 実行、実行 → ワークフローのファイル・成果物、成果物 → ZIP、ジョブ → ログを答える偽の GitHub。"""

    def __init__(
        self,
        jobs: dict[int, int],
        paths: dict[int, str],
        artifacts: dict[int, list[dict]] | None = None,
        zips: dict[int, bytes] | None = None,
        logs: dict[int, str] | None = None,
        broken_jobs: frozenset[int] = frozenset(),
    ) -> None:
        self.jobs, self.paths = jobs, paths
        self.artifacts = artifacts if artifacts is not None else {}
        self.zips = zips if zips is not None else {}
        self.logs = logs if logs is not None else {}
        self.broken_jobs = broken_jobs
        self.calls: list[tuple[str, int]] = []

    def job(self, job_id: int) -> dict:
        self.calls.append(("job", job_id))
        if job_id in self.broken_jobs:
            raise GitHubError(f"GitHub が断った（404）: GET jobs/{job_id}", 404)
        return {"id": job_id, "run_id": self.jobs[job_id]}

    def workflow_run(self, run_id: int) -> dict:
        self.calls.append(("workflow_run", run_id))
        return {"id": run_id, "path": f"{self.paths[run_id]}@refs/pull/7/merge"}

    def run_artifacts(self, run_id: int) -> list[dict]:
        self.calls.append(("run_artifacts", run_id))
        return self.artifacts.get(run_id, [])

    def artifact_zip(self, artifact_id: int) -> bytes:
        self.calls.append(("artifact_zip", artifact_id))
        if artifact_id not in self.zips:
            raise GitHubError("GitHub が断った（410）: GET zip", 410)
        return self.zips[artifact_id]

    def job_log(self, job_id: int) -> str:
        self.calls.append(("job_log", job_id))
        if job_id not in self.logs:
            raise GitHubError("GitHub が断った（403）: GET logs", 403)
        return self.logs[job_id]

    def count(self, kind: str) -> int:
        return sum(1 for k, _ in self.calls if k == kind)


def art(aid: int, name: str = "junit-0", **extra) -> dict:
    return {"id": aid, "name": name, "expired": False, "size_in_bytes": 100, **extra}
