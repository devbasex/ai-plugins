"""PR 変更ファイルに応じた自動レビュー観点の分類テスト。"""

from __future__ import annotations
import review_lib.categories
import review_lib.github
import review_lib.review_focus


def test_parse_pr_files_payload_normalizes_gh_files_json(state_mod):
    entries = review_lib.github._parse_pr_files_payload(
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
    entries = review_lib.github._parse_pr_files_api_lines(
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
    entries = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"docs/plan.md","changeType":"MODIFIED"},{"path":"README.md","changeType":"ADDED"}]}'
    )

    categories = review_lib.categories._classify_changed_files(entries)

    assert categories == ["common", "docs_only"]
    instructions = review_lib.review_focus._auto_review_instructions(categories)
    assert "ドキュメントのみ PR" in instructions
    assert "コード、設定、コマンド" in instructions


def test_code_pr_detects_code_test_and_frontend(state_mod):
    entries = review_lib.github._parse_pr_files_payload(
        """
        {"files":[
          {"path":"src/components/UserCard.tsx","changeType":"MODIFIED"},
          {"path":"tests/UserCard.test.tsx","changeType":"ADDED"}
        ]}
        """
    )

    categories = review_lib.categories._classify_changed_files(entries)

    assert "code" in categories
    assert "test" in categories
    assert "frontend" in categories


def test_db_migration_detects_schema_concerns(state_mod):
    entries = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"database/migrations/20260101_create_users.sql","changeType":"ADDED"}]}'
    )

    categories = review_lib.categories._classify_changed_files(entries)

    assert "db_migration" in categories
    assert "DB migration / schema 変更" in review_lib.review_focus._auto_review_instructions(categories)


def test_dependency_ci_api_security_performance_i18n_infra_are_detected(state_mod):
    entries = review_lib.github._parse_pr_files_payload(
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

    categories = review_lib.categories._classify_changed_files(entries)

    assert "dependency" in categories
    assert "config_ci" in categories
    assert "api_contract" in categories
    assert "auth_security" in categories
    assert "performance" in categories
    assert "i18n" in categories
    assert "infra" in categories


def test_deletion_and_rename_are_detected(state_mod):
    entries = review_lib.github._parse_pr_files_payload(
        """
        {"files":[
          {"path":"src/old.py","changeType":"DELETED"},
          {"path":"src/b.py","previousPath":"src/a.py","changeType":"RENAMED"}
        ]}
        """
    )

    categories = review_lib.categories._classify_changed_files(entries)

    assert "deletion_rename" in categories


def test_index_entrypoint_does_not_trigger_performance(state_mod):
    entries = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"src/index.ts","changeType":"MODIFIED"}]}'
    )

    assert "performance" not in review_lib.categories._classify_changed_files(entries)


def test_i18n_extension_detection_does_not_match_po_substrings(state_mod):
    false_positive = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"src/import.polyfill.js","changeType":"MODIFIED"}]}'
    )
    real_po = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"locales/messages.po","changeType":"MODIFIED"}]}'
    )

    assert "i18n" not in review_lib.categories._classify_changed_files(false_positive)
    assert "i18n" in review_lib.categories._classify_changed_files(real_po)


def test_env_files_are_config_and_author_is_not_auth_security(state_mod):
    env_file = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":".env.example","changeType":"MODIFIED"}]}'
    )
    author_file = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"src/components/AuthorCard.tsx","changeType":"MODIFIED"}]}'
    )
    auth_file = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"app/auth/session.py","changeType":"MODIFIED"}]}'
    )
    authz_file = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"app/security/authz.py","changeType":"MODIFIED"}]}'
    )
    authentication_file = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"app/security/authentication.py","changeType":"MODIFIED"}]}'
    )
    authorizer_file = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"app/security/authorizer.ts","changeType":"MODIFIED"}]}'
    )

    assert "config_ci" in review_lib.categories._classify_changed_files(env_file)
    assert "auth_security" not in review_lib.categories._classify_changed_files(author_file)
    assert "auth_security" in review_lib.categories._classify_changed_files(auth_file)
    assert "auth_security" in review_lib.categories._classify_changed_files(authz_file)
    assert "auth_security" in review_lib.categories._classify_changed_files(authentication_file)
    assert "auth_security" in review_lib.categories._classify_changed_files(authorizer_file)


def test_tokenizer_async_jobcard_and_tflite_do_not_trigger_categories(state_mod):
    tokenizer_file = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"src/tokenizer.py","changeType":"MODIFIED"}]}'
    )
    token_file = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"app/auth/token.py","changeType":"MODIFIED"}]}'
    )
    async_helper = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"utils/async_helpers.ts","changeType":"MODIFIED"}]}'
    )
    job_card = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"components/JobCard.tsx","changeType":"MODIFIED"}]}'
    )
    worker_file = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"src/worker/queue.ts","changeType":"MODIFIED"}]}'
    )
    tflite_file = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"models/config.tflite","changeType":"MODIFIED"}]}'
    )
    terraform_file = review_lib.github._parse_pr_files_payload(
        '{"files":[{"path":"terraform/main.tf","changeType":"MODIFIED"}]}'
    )

    assert "auth_security" not in review_lib.categories._classify_changed_files(tokenizer_file)
    assert "auth_security" in review_lib.categories._classify_changed_files(token_file)
    assert "performance" not in review_lib.categories._classify_changed_files(async_helper)
    assert "performance" not in review_lib.categories._classify_changed_files(job_card)
    assert "performance" in review_lib.categories._classify_changed_files(worker_file)
    assert "infra" not in review_lib.categories._classify_changed_files(tflite_file)
    assert "infra" in review_lib.categories._classify_changed_files(terraform_file)


def test_combined_review_instructions_puts_auto_before_manual(state_mod):
    assert review_lib.review_focus._combined_review_instructions("auto", "manual") == "auto\n\nmanual"
    assert review_lib.review_focus._combined_review_instructions("auto", "") == "auto"
    assert review_lib.review_focus._combined_review_instructions("", "manual") == "manual"


def test_design_pr_adds_the_design_template(state_mod):
    """#542 の AC14: 設計の文書を含む PR は common / docs_only に design が加わる。"""
    for name in ("issue-1-design.md", "issue-1-requirements.md", "issue-1-design-decisions.md"):
        categories = review_lib.categories._classify_changed_files(
            [{"status": "M", "paths": [f"issues/{name}"]}])
        assert categories == ["common", "docs_only", "design"], name
    assert review_lib.review_focus.CATEGORY_TEMPLATES["design"] in review_lib.review_focus._auto_review_instructions(categories)


def test_other_docs_are_not_design(state_mod):
    """#542 の AC16: `issues/` の他の文書や `issues/` の外の設計文書は design にしない。"""
    for path in ("issues/notes.md", "docs/x-design.md", "issues/issue-1-implementation-plan.md"):
        categories = review_lib.categories._classify_changed_files([{"status": "M", "paths": [path]}])
        assert "design" not in categories, path
