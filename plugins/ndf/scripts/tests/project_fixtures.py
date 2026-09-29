"""テストで共有する `.ndf/project.json` の例。"""

# phpunit のコンテナ構成（CARMO）
CARMO = {
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
