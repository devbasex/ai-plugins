"""PR 変更ファイルに応じた自動レビュー観点の分類テスト。"""

from __future__ import annotations


def test_parse_pr_files_payload_normalizes_gh_files_json(state_mod):
    entries = state_mod._parse_pr_files_payload(
        """
        {
          "files": [
            {"path": "src/app.py", "changeType": "MODIFIED"},
            {"path": "docs/new.md", "previousPath": "docs/old.md", "changeType": "RENAMED"},
            {"path": "src/old.py", "changeType": "DELETED"}
          ]
        }
        """
    )

    assert entries == [
        {"status": "M", "paths": ["src/app.py"]},
        {"status": "R", "paths": ["docs/old.md", "docs/new.md"]},
        {"status": "D", "paths": ["src/old.py"]},
    ]


def test_parse_pr_files_api_lines_normalizes_paginated_api_output(state_mod):
    entries = state_mod._parse_pr_files_api_lines(
        "modified\tsrc/app.py\t\n"
        "renamed\tdocs/new.md\tdocs/old.md\n"
        "removed\tsrc/old.py\t\n"
    )

    assert entries == [
        {"status": "M", "paths": ["src/app.py"]},
        {"status": "R", "paths": ["docs/old.md", "docs/new.md"]},
        {"status": "D", "paths": ["src/old.py"]},
    ]


def test_docs_only_pr_adds_docs_template(state_mod):
    entries = state_mod._parse_pr_files_payload(
        '{"files":[{"path":"docs/plan.md","changeType":"MODIFIED"},{"path":"README.md","changeType":"ADDED"}]}'
    )

    categories = state_mod._classify_changed_files(entries)

    assert categories == ["common", "docs_only"]
    instructions = state_mod._auto_review_instructions(categories)
    assert "ドキュメントのみ PR" in instructions
    assert "コード、設定、コマンド" in instructions


def test_code_pr_detects_code_test_and_frontend(state_mod):
    entries = state_mod._parse_pr_files_payload(
        """
        {"files":[
          {"path":"src/components/UserCard.tsx","changeType":"MODIFIED"},
          {"path":"tests/UserCard.test.tsx","changeType":"ADDED"}
        ]}
        """
    )

    categories = state_mod._classify_changed_files(entries)

    assert "code" in categories
    assert "test" in categories
    assert "frontend" in categories


def test_db_migration_detects_schema_concerns(state_mod):
    entries = state_mod._parse_pr_files_payload(
        '{"files":[{"path":"database/migrations/20260101_create_users.sql","changeType":"ADDED"}]}'
    )

    categories = state_mod._classify_changed_files(entries)

    assert "db_migration" in categories
    assert "DB migration / schema 変更" in state_mod._auto_review_instructions(categories)


def test_dependency_ci_api_security_performance_i18n_infra_are_detected(state_mod):
    entries = state_mod._parse_pr_files_payload(
        """
        {"files":[
          {"path":"package-lock.json","changeType":"MODIFIED"},
          {"path":".github/workflows/test.yml","changeType":"MODIFIED"},
          {"path":"app/api/auth/token_controller.py","changeType":"MODIFIED"},
          {"path":"src/cache/worker.go","changeType":"MODIFIED"},
          {"path":"locales/ja.json","changeType":"MODIFIED"},
          {"path":"terraform/main.tf","changeType":"MODIFIED"}
        ]}
        """
    )

    categories = state_mod._classify_changed_files(entries)

    assert "dependency" in categories
    assert "config_ci" in categories
    assert "api_contract" in categories
    assert "auth_security" in categories
    assert "performance" in categories
    assert "i18n" in categories
    assert "infra" in categories


def test_deletion_and_rename_are_detected(state_mod):
    entries = state_mod._parse_pr_files_payload(
        """
        {"files":[
          {"path":"src/old.py","changeType":"DELETED"},
          {"path":"src/b.py","previousPath":"src/a.py","changeType":"RENAMED"}
        ]}
        """
    )

    categories = state_mod._classify_changed_files(entries)

    assert "deletion_rename" in categories


def test_combined_review_instructions_puts_auto_before_manual(state_mod):
    assert state_mod._combined_review_instructions("auto", "manual") == "auto\n\nmanual"
    assert state_mod._combined_review_instructions("auto", "") == "auto"
    assert state_mod._combined_review_instructions("", "manual") == "manual"
