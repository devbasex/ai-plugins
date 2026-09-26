"""変更ファイルのパスの分類（#1142 の C2）。

分類ごとの観点の文章は `review_focus` が持つ。
"""
from __future__ import annotations

import pathlib
from typing import Any

import review_lib  # noqa: E402
from classifications import oversized_design_docs  # noqa: E402


DOC_EXTENSIONS = {
    ".md", ".mdx", ".rst", ".txt", ".adoc", ".asciidoc",
}
DOC_FILENAMES = {
    "readme", "license", "changelog", "contributing", "codeowners",
}
CODE_EXTENSIONS = {
    ".c", ".cc", ".cpp", ".cs", ".css", ".dart", ".ex", ".exs", ".go", ".h",
    ".hpp", ".html", ".java", ".js", ".jsx", ".kt", ".kts", ".php", ".py",
    ".rb", ".rs", ".scala", ".scss", ".sh", ".sql", ".swift", ".ts", ".tsx",
    ".vue", ".yaml", ".yml",
}
MIGRATION_PATH_MARKERS = (
    "/migrations/", "/migration/", "/db/migrate/", "/database/migrations/",
    "/alembic/versions/", "/prisma/migrations/",
)
MIGRATION_NAME_MARKERS = (
    "migration", "migrate", "schema.sql", "schema.prisma",
)
TEST_PATH_MARKERS = (
    "/test/", "/tests/", "/spec/", "/specs/", "__tests__/",
)
TEST_NAME_MARKERS = (
    ".test.", ".spec.", "_test.", "_spec.", "test_", "spec_",
)
DEPENDENCY_FILENAMES = {
    "package.json", "package-lock.json", "pnpm-lock.yaml", "yarn.lock",
    "composer.json", "composer.lock", "gemfile", "gemfile.lock",
    "go.mod", "go.sum", "requirements.txt", "requirements-dev.txt",
    "pyproject.toml", "poetry.lock", "uv.lock", "cargo.toml", "cargo.lock",
    "pom.xml", "build.gradle", "build.gradle.kts",
}
CI_CONFIG_MARKERS = (
    "/.github/workflows/", "/.gitlab-ci", "/.circleci/", "/.buildkite/",
    "/.kiro/", "/.claude/", "/.codex/",
)
CONFIG_CI_FILENAMES = {"dockerfile", "makefile", ".editorconfig"}
# 環境別の接尾辞を持つファイルも、名前の先頭で判定する。
ENV_FILENAME_PREFIX = ".env"
# ルート直下の GitHub 設定を対象にするため、部分一致のマーカーとは分ける。
GITHUB_CONFIG_PATH_PREFIX = ".github/"
CONFIG_EXTENSIONS = {
    ".json", ".toml", ".yaml", ".yml", ".ini", ".env", ".example",
}
API_CONTRACT_MARKERS = (
    "/api/", "/routes/", "/controllers/", "/openapi", "/swagger",
    "/proto/", "/graphql/", "/schemas/",
)
AUTH_SECURITY_TOKEN_MARKERS = (
    "auth", "authn", "authz", "permission", "policy", "role", "oauth", "jwt",
    "session", "csrf", "cors", "token",
)
AUTH_SECURITY_SUBSTRING_MARKERS = (
    "authentication", "authorization", "authenticat", "authoriz",
    "secret", "password", "credential",
)
FRONTEND_EXTENSIONS = {
    ".css", ".scss", ".sass", ".less", ".html", ".jsx", ".tsx", ".vue", ".svelte",
}
PERFORMANCE_MARKERS = (
    "cache", "queue", "job", "worker", "concurrent", "parallel",
    "batch", "stream", "pagination", "performance",
)
GENERATED_MARKERS = (
    "/dist/", "/build/", "/generated/", "/vendor/", "/node_modules/",
)
I18N_MARKERS = (
    "/locales/", "/locale/", "/i18n/", "/translations/",
)
I18N_EXTENSIONS = {".po", ".pot"}
INFRA_MARKERS = (
    "/terraform/", "/helm/", "/k8s/", "/kubernetes/", "/docker/",
    "dockerfile", "docker-compose",
)
INFRA_EXTENSIONS = {".tf", ".tfvars"}
INFRA_FILENAMES = {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}


def _path_info(path: str) -> tuple[str, str, str, str]:
    p = pathlib.PurePosixPath(path.replace("\\", "/"))
    lower = str(p).lower()
    normalized = "/" + lower.lstrip("./")
    return lower, normalized, p.name.lower(), pathlib.PurePosixPath(lower).suffix


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


def _path_tokens(path: str) -> set[str]:
    _, normalized, _, _ = _path_info(path)
    return {
        token
        for token in normalized.replace(".", "/").replace("-", "/").replace("_", "/").split("/")
        if token
    }


def _is_doc_path(path: str) -> bool:
    lower, normalized, name, ext = _path_info(path)
    stem = pathlib.PurePosixPath(lower).stem
    return (
        ext in DOC_EXTENSIONS
        or stem in DOC_FILENAMES
        or normalized.startswith("/docs/")
        or "/docs/" in normalized
        or normalized.startswith("/documentation/")
        or "/documentation/" in normalized
    )


def _is_code_path(path: str) -> bool:
    _, _, _, ext = _path_info(path)
    return ext in CODE_EXTENSIONS


def _is_migration_path(path: str) -> bool:
    lower, normalized, name, ext = _path_info(path)
    return (
        ext == ".sql"
        or _contains_any(normalized, MIGRATION_PATH_MARKERS)
        or any(marker in name or marker in lower for marker in MIGRATION_NAME_MARKERS)
    )


def _is_test_path(path: str) -> bool:
    lower, normalized, name, _ = _path_info(path)
    return _contains_any(normalized, TEST_PATH_MARKERS) or any(m in name or m in lower for m in TEST_NAME_MARKERS)


def _is_dependency_path(path: str) -> bool:
    _, _, name, _ = _path_info(path)
    return name in DEPENDENCY_FILENAMES


def _is_config_ci_path(path: str) -> bool:
    lower, normalized, name, ext = _path_info(path)
    return (
        _contains_any(normalized, CI_CONFIG_MARKERS)
        or name.startswith(ENV_FILENAME_PREFIX)
        or name in CONFIG_CI_FILENAMES
        or lower.startswith(GITHUB_CONFIG_PATH_PREFIX)
        or (ext in CONFIG_EXTENSIONS and ("/config/" in normalized or "/configs/" in normalized))
    )


def _is_api_contract_path(path: str) -> bool:
    lower, normalized, _, _ = _path_info(path)
    return _contains_any(normalized, API_CONTRACT_MARKERS) or "openapi" in lower or "swagger" in lower


def _is_auth_security_path(path: str) -> bool:
    lower, _, _, _ = _path_info(path)
    tokens = _path_tokens(path)
    return (
        bool(tokens.intersection(AUTH_SECURITY_TOKEN_MARKERS))
        or any(marker in lower for marker in AUTH_SECURITY_SUBSTRING_MARKERS)
    )


def _is_frontend_path(path: str) -> bool:
    _, normalized, _, ext = _path_info(path)
    return ext in FRONTEND_EXTENSIONS or "/components/" in normalized or "/pages/" in normalized


def _is_performance_path(path: str) -> bool:
    return bool(_path_tokens(path).intersection(PERFORMANCE_MARKERS))


def _is_generated_path(path: str) -> bool:
    _, normalized, name, _ = _path_info(path)
    return _contains_any(normalized, GENERATED_MARKERS) or name in {
        "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "composer.lock",
        "gemfile.lock", "go.sum", "poetry.lock", "uv.lock", "cargo.lock",
    }


def _is_i18n_path(path: str) -> bool:
    _, normalized, _, ext = _path_info(path)
    return _contains_any(normalized, I18N_MARKERS) or ext in I18N_EXTENSIONS


def _is_infra_path(path: str) -> bool:
    lower, normalized, name, ext = _path_info(path)
    return (
        _contains_any(normalized, INFRA_MARKERS)
        or name in INFRA_FILENAMES
        or ext in INFRA_EXTENSIONS
        or lower.endswith(".tfvars.json")
    )


# 設計 PR の文書の名前（#542 の決定 5）。`design` の成果物が `issues/` に置く 3 文書。
DESIGN_DOC_SUFFIXES = ("-requirements.md", "-design.md", "-design-decisions.md")


def _is_design_doc_path(path: str) -> bool:
    return path.startswith("issues/") and path.endswith(DESIGN_DOC_SUFFIXES)


PATH_CATEGORY_RULES = (
    ("design", _is_design_doc_path),
    ("code", _is_code_path),
    ("db_migration", _is_migration_path),
    ("test", _is_test_path),
    ("dependency", _is_dependency_path),
    ("config_ci", _is_config_ci_path),
    ("api_contract", _is_api_contract_path),
    ("auth_security", _is_auth_security_path),
    ("frontend", _is_frontend_path),
    ("performance", _is_performance_path),
    ("generated", _is_generated_path),
    ("i18n", _is_i18n_path),
    ("infra", _is_infra_path),
)


def _classify_changed_files(entries: list[dict[str, Any]]) -> list[str]:
    paths = [p for entry in entries for p in entry.get("paths", []) if isinstance(p, str)]
    categories: list[str] = ["common"]
    if not paths:
        return categories

    if paths and all(_is_doc_path(p) for p in paths):
        categories.append("docs_only")

    categories.extend(
        category
        for category, predicate in PATH_CATEGORY_RULES
        if any(predicate(path) for path in paths)
    )
    if any(str(entry.get("status", "")).startswith(("D", "R")) for entry in entries):
        categories.append("deletion_rename")

    # rename の旧パスだけで検知したカテゴリが混ざるのは有用だが、docs_only は
    # 旧パス/新パス両方が docs であるときだけ採用するため上で all(paths) にしている。
    return list(dict.fromkeys(categories))


def _warn_oversized_design_docs(worktree: object, changed_files: list[dict[str, Any]]) -> list[dict]:
    """行数の上限を超える設計文書を知らせる（#1005）。**止めない。** 超えた文書の一覧を返す。"""
    paths = [entry for entry in changed_files or [] if isinstance(entry, str)]
    paths += [p for entry in changed_files or [] if isinstance(entry, dict)
              for p in entry.get("paths", []) if isinstance(p, str)]
    over = oversized_design_docs(str(worktree) if worktree else None, paths)
    for doc in over:
        review_lib.info(f"⚠️ 設計文書が {doc['lines']} 行ある（上限 1,000 行）: {doc['path']}。主題を分けて設計を 2 本にする")
    return over
