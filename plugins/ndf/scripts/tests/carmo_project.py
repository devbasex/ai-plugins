"""carmo の宣言（`.ndf/project.json` の形）。CI で全体テストを回す PHP の案件の見本。

test_lib_test_strategy・test_supervise・cross-refactoring の test_init が共有する。
呼ぶたびに新しい dict を返すので、受け取った側が書き換えても他のテストへ漏れない。
"""

from __future__ import annotations

from typing import Any


def carmo_project() -> dict[str, Any]:
    return {
        "version": 1,
        "test": {
            "strategy": "local-scoped-ci-whole",
            "ci": {"check": "test-results", "junit_artifacts": "junit-*"},
            "suites": [
                {
                    "name": "phpunit",
                    "runner": "phpunit",
                    "command": "docker compose exec -T app ./vendor/bin/phpunit --log-junit build/ndf/junit.xml",
                    "scope_command": "docker compose exec -T app ./vendor/bin/phpunit --log-junit build/ndf/junit.xml {paths}",
                    "junit": "build/ndf/junit.xml",
                    "container": {"service": "app"},
                    "paths": ["tests"],
                }
            ],
        },
        "test_duration": {"measured": [{"seconds": 3827.0, "source": "ci-junit", "detail": "run"}]},
        "ci": {
            "provider": "github-actions",
            "workflows": [{"path": ".github/workflows/test-results.yml", "jobs": 24, "wall_seconds": 360.0}],
            "required_checks": ["test-results", "codex-review"],
        },
    }
