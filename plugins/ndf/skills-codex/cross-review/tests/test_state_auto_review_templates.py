"""PR 変更ファイルに応じた自動レビュー観点の分類テスト。"""

from __future__ import annotations


def test_parse_name_status_handles_rename(state_mod):
    entries = state_mod._parse_name_status("R100\told.md\tdocs/new.md\nM\tREADME.md\n")

    assert entries == [
        {"status": "R100", "paths": ["old.md", "docs/new.md"]},
        {"status": "M", "paths": ["README.md"]},
    ]


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


def test_docs_only_pr_adds_docs_template(state_mod):
    entries = state_mod._parse_name_status("M\tdocs/plan.md\nA\tREADME.md\n")

    categories = state_mod._classify_changed_files(entries)

    assert categories == ["common", "docs_only"]
    instructions = state_mod._auto_review_instructions(categories)
    assert "ドキュメントのみ PR" in instructions
    assert "コード、設定、コマンド" in instructions


def test_code_pr_detects_code_test_and_frontend(state_mod):
    entries = state_mod._parse_name_status(
        "M\tsrc/components/UserCard.tsx\n"
        "A\ttests/UserCard.test.tsx\n"
    )

    categories = state_mod._classify_changed_files(entries)

    assert "code" in categories
    assert "test" in categories
    assert "frontend" in categories


def test_db_migration_detects_schema_concerns(state_mod):
    entries = state_mod._parse_name_status("A\tdatabase/migrations/20260101_create_users.sql\n")

    categories = state_mod._classify_changed_files(entries)

    assert "db_migration" in categories
    assert "DB migration / schema 変更" in state_mod._auto_review_instructions(categories)


def test_dependency_ci_api_security_performance_i18n_infra_are_detected(state_mod):
    entries = state_mod._parse_name_status(
        "M\tpackage-lock.json\n"
        "M\t.github/workflows/test.yml\n"
        "M\tapp/api/auth/token_controller.py\n"
        "M\tsrc/cache/worker.go\n"
        "M\tlocales/ja.json\n"
        "M\tterraform/main.tf\n"
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
    entries = state_mod._parse_name_status("D\tsrc/old.py\nR100\tsrc/a.py\tsrc/b.py\n")

    categories = state_mod._classify_changed_files(entries)

    assert "deletion_rename" in categories


def test_combined_review_instructions_puts_auto_before_manual(state_mod):
    assert state_mod._combined_review_instructions("auto", "manual") == "auto\n\nmanual"
    assert state_mod._combined_review_instructions("auto", "") == "auto"
    assert state_mod._combined_review_instructions("", "manual") == "manual"
