#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""cross-review state.json 操作 CLI。

`<worktree>/.cross_review/cross-review-pr<PR>-state.json` の初期化 / 読み書きと、
ループ判定（round 開始 / 収束 / 振動 / PR ローテーション要否 / fix 結果マージ /
deferred nit レポート）を 1 つの CLI に集約する。

Subcommands:
  init           Step 0  state 初期化 or 再開（プリチェック込み）
  start-round    Step 1  round 開始判定 (ROUND/ROUND_IN_PR/PR を stdout に出す)
  read-result    Step 2.5 codex/gemini の result.json を state にマージ
  judge          Step 3  intent ベース pass 判定 (exit 0=approved, 2=continue)
  check-oscillation Step 4 path:line 重複率を計算
  merge-fix      Step 5 post  fix サブエージェント戻り値を state にマージ + CI 分類
  should-rotate  Step 6  rotate_after 到達判定 (exit 0=rotate, 2=keep)
  set-current-pr        PR ローテーション後の current_pr 更新
  report         Step 8  deferred nit + ラウンドサマリ表示

すべての出力は人間可読 + KEY=VALUE 形式（eval / read で取り回し可能）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any


# ---------------- helpers ----------------

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
CONFIG_EXTENSIONS = {
    ".json", ".toml", ".yaml", ".yml", ".ini", ".env", ".example",
}
API_CONTRACT_MARKERS = (
    "/api/", "/routes/", "/controllers/", "/openapi", "/swagger",
    "/proto/", "/graphql/", "/schemas/",
)
AUTH_SECURITY_MARKERS = (
    "auth", "permission", "policy", "role", "token", "secret", "password",
    "credential", "oauth", "jwt", "session", "csrf", "cors",
)
FRONTEND_EXTENSIONS = {
    ".css", ".scss", ".sass", ".less", ".html", ".jsx", ".tsx", ".vue", ".svelte",
}
PERFORMANCE_MARKERS = (
    "cache", "queue", "job", "worker", "async", "concurrent", "parallel",
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
    "dockerfile", "docker-compose", ".tf", ".tfvars",
)


COMMON_REVIEW_TEMPLATE = """## 自動追加レビュー観点: 共通
- PR の目的と変更範囲が一貫しているか。余分な変更、未説明の仕様変更、将来の保守を難しくする設計がないか。
- 変更が既存仕様・既存コメント・既存コードの前提と矛盾しないか。重複指摘は避け、根拠がある修正アクションだけを指摘する。
- テスト、検証手順、エラーハンドリング、ロールバック容易性が変更リスクに見合っているか。"""

DOCS_ONLY_REVIEW_TEMPLATE = """## 自動追加レビュー観点: ドキュメントのみ PR
- ドキュメントの主張・企画・手順が妥当で、読者の意思決定や作業を誤らせないか。
- コード、設定、コマンド、既存 README / docs / CHANGELOG との整合性が取れているか。古い名称、存在しないパス、実装と違う説明がないか。
- ドキュメント間で用語、前提、バージョン、責務分担が矛盾していないか。
- 追加・更新された説明が必要十分で、曖昧な表現や未検証の断定がないか。"""

CODE_REVIEW_TEMPLATE = """## 自動追加レビュー観点: コード変更 PR
- 設計、正確性、可読性、保守性、単純さを確認する。不要に複雑な分岐、責務の混在、過剰な抽象化がないか。
- 冗長・重複コード、既存ヘルパや標準 API で置き換えられる処理、言語・フレームワークらしくない実装がないか。
- 関数・クラス・ファイルのサイズと責務が適切か。長すぎる関数、肥大化したファイル、名前と実態がずれた単位がないか。
- セキュリティ観点として、入力検証、出力エンコード、認可、秘密情報、ログ、例外、外部コマンド、SQL/HTML/パス操作の扱いを確認する。
- テストの有無と質、境界値、失敗系、後方互換性、性能・並行性・リソース解放のリスクを確認する。"""

DB_MIGRATION_REVIEW_TEMPLATE = """## 自動追加レビュー観点: DB migration / schema 変更
- データ設計としてテーブル、カラム、型、NULL 可否、default、制約、外部キー、unique/index がドメイン要件に合っているか。
- 型の粒度が妥当か。文字列で持つべきでない値、過剰に広い型、精度不足、timezone、JSON の濫用がないか。
- 既存データへの影響、backfill、ロック時間、index 作成、ロールバック、アプリケーションの段階的デプロイ順序に問題がないか。
- migration とモデル、クエリ、ドキュメント、テストデータの整合性が取れているか。"""

TEST_REVIEW_TEMPLATE = """## 自動追加レビュー観点: テスト変更
- テストが実装詳細ではなくユーザー影響・仕様・境界値・失敗系を検証しているか。
- flaky になりやすい時間、乱数、順序、外部サービス、並行実行、共有状態への依存がないか。
- テスト名、fixture、期待値が読みやすく、失敗時に原因を特定しやすいか。"""

DEPENDENCY_REVIEW_TEMPLATE = """## 自動追加レビュー観点: 依存関係変更
- 追加・更新された依存の必要性、ライセンス、メンテナンス状況、既存依存との重複を確認する。
- lockfile と manifest の整合性、間接依存の大きな変化、ビルド/実行環境への影響を確認する。
- 依存更新がセキュリティ、互換性、バンドルサイズ、起動時間に与える影響を確認する。"""

CONFIG_CI_REVIEW_TEMPLATE = """## 自動追加レビュー観点: CI / 設定変更
- CI 条件、権限、secret 参照、cache key、artifact、並列実行、失敗時の検知性が妥当か。
- 設定変更がローカル・CI・本番で食い違わないか。環境変数の既定値と `.env.example` 相当の説明が揃っているか。
- 自動化が過剰な権限や予期せぬ副作用を持たないか。"""

API_CONTRACT_REVIEW_TEMPLATE = """## 自動追加レビュー観点: API / 契約変更
- 入出力スキーマ、HTTP status、エラー形式、互換性、バージョニング、既存クライアントへの影響を確認する。
- バリデーション、認可、ページング、冪等性、レート制限、監査ログが要件に合っているか。
- API ドキュメント、型定義、テスト、実装の整合性が取れているか。"""

AUTH_SECURITY_REVIEW_TEMPLATE = """## 自動追加レビュー観点: 認証・認可・機密情報
- 認証、認可、ロール、所有者チェック、テナント境界が欠落または過剰許可になっていないか。
- token、password、secret、PII がログ、例外、レスポンス、コミット差分に漏れていないか。
- CSRF/CORS/session/JWT/OAuth などの設定が安全で、失効・更新・リプレイ対策が妥当か。"""

FRONTEND_REVIEW_TEMPLATE = """## 自動追加レビュー観点: フロントエンド / UX
- UI 状態、loading/error/empty、キーボード操作、アクセシビリティ、レスポンシブ表示が破綻しないか。
- コンポーネント責務、重複 UI、状態管理、不要な再レンダリング、バンドルサイズへの影響を確認する。
- 表示文言、フォーム validation、ユーザー操作後のフィードバックが仕様と一致しているか。"""

PERFORMANCE_REVIEW_TEMPLATE = """## 自動追加レビュー観点: 性能・並行性
- N+1、不要な全件取得、過剰な同期 I/O、メモリ保持、ロック、競合、リトライ嵐がないか。
- cache、batch、pagination、stream、queue/worker の使い方が正しく、失敗時の再実行や重複実行に耐えるか。
- 計測・ログ・アラートが問題発生時の切り分けに足りるか。"""

DELETION_RENAME_REVIEW_TEMPLATE = """## 自動追加レビュー観点: 削除・リネーム
- 削除・リネーム対象への参照がコード、設定、CI、ドキュメント、テスト、外部連携に残っていないか。
- 後方互換性、migration、deprecation、利用者への移行手順が必要ないか。
- 同名別ファイルや大文字小文字差による環境依存の問題がないか。"""

GENERATED_REVIEW_TEMPLATE = """## 自動追加レビュー観点: 生成物・ロックファイル
- 生成物が本当にコミット対象か。生成元との差分、再生成手順、不要なノイズが混入していないか。
- lockfile の差分が意図した依存変更に対応しているか。手編集や不整合がないか。"""

I18N_REVIEW_TEMPLATE = """## 自動追加レビュー観点: i18n / 文言
- 翻訳キー、fallback、変数展開、複数形、日付・数値・通貨・タイムゾーン表記が妥当か。
- 原文と翻訳、UI 表示幅、アクセシビリティラベル、ドキュメント文言の整合性を確認する。"""

INFRA_REVIEW_TEMPLATE = """## 自動追加レビュー観点: インフラ / デプロイ
- 環境差分、権限、secret、ネットワーク公開範囲、永続化、バックアップ、スケール、ロールバック容易性を確認する。
- IaC / manifest / Dockerfile の設定が最小権限・再現可能・運用監視しやすい形になっているか。
- 既存環境への破壊的変更、手動作業、順序依存、ダウンタイムのリスクが説明されているか。"""

def _default_worktree_base() -> pathlib.Path:
    """worktree の親ディレクトリを解決する。

    優先順位:
      1. 環境変数 NDF_WORKTREE_BASE (明示オーバーライド)
      2. <システム tmpdir>/ndf-worktrees (非永続領域。コンテナ再作成で自動消滅)

    かつての /work/worktrees ($HOME/work/worktrees) は共有の永続 volume 上にあり、
    別リポジトリの pr<N> と衝突する・明示削除が必要・volume を消費する問題が
    あったため廃止した。
    """
    env = os.environ.get("NDF_WORKTREE_BASE")
    if env:
        # 相対パスのまま state.json に保存されると後続のパス比較が壊れるため、
        # 常に絶対パスへ解決して返す。
        return pathlib.Path(env).resolve()
    return pathlib.Path(tempfile.gettempdir()) / "ndf-worktrees"


def _repo_slug(repo: str) -> str:
    """`owner/name` を path-safe なディレクトリ名 `owner--name` に変換する。"""
    return repo.replace("/", "--")


def _is_registered_worktree(path: str) -> bool:
    """path が現リポジトリに登録済みの worktree かどうか。

    パスが存在しても別リポジトリの残骸や git 管理外ディレクトリの可能性があり、
    流用すると git 操作が壊れるため、流用前に必ずこれで検証する。
    """
    out = _sh(["git", "worktree", "list", "--porcelain"], check=False)
    target = str(pathlib.Path(path).resolve())
    return any(line == f"worktree {target}" for line in out.splitlines())


def _create_worktree(worktree: str, pr: int, head_branch: str) -> None:
    """origin/<head> から detached worktree を作成する (フォーク PR はフォールバック)。"""
    pathlib.Path(worktree).parent.mkdir(parents=True, exist_ok=True)
    # worktree を /tmp 等の非永続領域に置くと、実体だけ消えて親リポジトリの
    # 登録 (prunable) が残ることがある。その状態で `git worktree add` すると
    # 「パス登録済み」として失敗するため、追加前に prune で掃除しておく。
    subprocess.run(
        ["git", "worktree", "prune"],
        capture_output=True, text=True,
    )
    # フォーク PR の場合 origin に head_branch がないことがある。
    # fetch 失敗時は gh pr checkout --detach でフォールバックする。
    fetch_result = subprocess.run(
        ["git", "fetch", "origin", head_branch],
        capture_output=True, text=True,
    )
    if fetch_result.returncode == 0:
        # head branch が既に別の worktree で checkout されている場合を避けるため
        # detached で展開する。cross-review はファイル参照しかしないので問題ない。
        _sh(["git", "worktree", "add", "--detach", worktree, f"origin/{head_branch}"])
        info(f"✅ worktree 作成 (detached @ origin/{head_branch}): {worktree}")
    else:
        info(f"⚠ git fetch origin {head_branch} 失敗 (フォーク PR の可能性) — gh pr checkout でフォールバック")
        _sh(["git", "worktree", "add", "--detach", worktree, "HEAD"])
        # worktree 内で gh pr checkout を実行して正しいコミットに切り替え
        checkout_result = subprocess.run(
            ["gh", "pr", "checkout", str(pr), "--detach"],
            capture_output=True, text=True,
            cwd=worktree,
        )
        if checkout_result.returncode != 0:
            # HEAD (親コミット) 指向のまま残すと、次回実行時に
            # _is_registered_worktree() を通過して不正流用されるため、
            # die() の前に作成済み worktree をロールバックする。
            subprocess.run(
                ["git", "worktree", "remove", "--force", worktree],
                capture_output=True, text=True,
            )
            die(f"gh pr checkout --detach #{pr} 失敗: {checkout_result.stderr.strip()}")
        info(f"✅ worktree 作成 (gh pr checkout --detach #{pr}): {worktree}")


def _git_toplevel() -> str | None:
    """git worktree root を取得する。失敗時は None を返す。"""
    try:
        r = subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True, text=True,
        )
        if r.returncode == 0 and r.stdout.strip():
            return r.stdout.strip()
    except OSError:
        pass
    return None


def _tmp_dir(workspace: str | None = None) -> pathlib.Path:
    """cross-review 用 tmp ディレクトリを決定する。

    優先順位:
      1. 環境変数 `CROSS_REVIEW_TMP_DIR` (明示)
      2. `<workspace>/.cross_review/` (worktree 内。gemini の workspace 制約を根本回避)

    `workspace` 未指定なら `git rev-parse --show-toplevel` で worktree root を
    取得する。サブディレクトリから実行してもパス不一致が発生しない。
    git コマンドが失敗した場合のみ `os.getcwd()` にフォールバックする。
    """
    env = os.environ.get("CROSS_REVIEW_TMP_DIR")
    if env:
        d = pathlib.Path(env).resolve()
        d.mkdir(parents=True, exist_ok=True)
        return d
    ws = pathlib.Path(workspace or _git_toplevel() or os.getcwd()).resolve()
    d = ws / ".cross_review"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _resolve_tmp_dir(pr: int | None = None) -> pathlib.Path:
    """state.json に保存された tmp_dir を優先し、_tmp_dir() をフォールバックとする。

    init 時に確定した tmp_dir を再利用することで、CWD や環境変数の変化による
    パス不一致リスクを回避する。

    注意: この関数は成果物パスの解決にのみ使用する。state ファイル自体のパス解決
    には _tmp_dir() を直接使うこと（循環参照を防ぐため）。
    副作用: フォールバック時に _tmp_dir() を呼ぶため、ディレクトリ作成 (mkdir) が
    発生する可能性がある。
    """
    if pr is not None:
        # state.json から tmp_dir を読み出す (存在する場合)
        candidate = _tmp_dir() / f"cross-review-pr{pr}-state.json"
        if candidate.exists():
            try:
                st = json.loads(candidate.read_text(encoding="utf-8"))
                saved = st.get("tmp_dir")
                if saved:
                    p = pathlib.Path(saved)
                    if p.exists():
                        return p
            except (OSError, json.JSONDecodeError):
                pass
    return _tmp_dir()


def _state_path(pr: int) -> pathlib.Path:
    return _tmp_dir() / f"cross-review-pr{pr}-state.json"


def _payload_path(agent: str, pr: int, round_: int) -> pathlib.Path:
    return _resolve_tmp_dir(pr) / f"{agent}-review-pr{pr}-round{round_}-payload.json"


def _existing_comments_path(pr: int) -> pathlib.Path:
    return _resolve_tmp_dir(pr) / f"cross-review-pr{pr}-existing-comments.txt"


def _now() -> str:
    return _dt.datetime.now(_dt.timezone.utc).astimezone().isoformat(timespec="seconds")


def _parse_pr_files_payload(output: str) -> list[dict[str, Any]]:
    """`gh pr view --json files` の JSON を分類用の最小構造に正規化する。"""
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return []
    files = payload.get("files") if isinstance(payload, dict) else None
    if not isinstance(files, list):
        return []

    status_map = {
        "ADDED": "A",
        "MODIFIED": "M",
        "DELETED": "D",
        "RENAMED": "R",
        "COPIED": "C",
        "CHANGED": "M",
    }
    entries: list[dict[str, Any]] = []
    for f in files:
        if not isinstance(f, dict):
            continue
        path = f.get("path")
        if not isinstance(path, str) or not path:
            continue
        change_type = str(f.get("changeType") or "MODIFIED").upper()
        status = status_map.get(change_type, change_type[:1] or "M")
        paths = []
        previous = f.get("previousPath") or f.get("previous_filename")
        if isinstance(previous, str) and previous and previous != path:
            paths.append(previous)
        paths.append(path)
        entries.append({"status": status, "paths": paths})
    return entries


def _parse_pr_files_api_lines(output: str) -> list[dict[str, Any]]:
    """GitHub API の PR files を TSV(JSON jq) 出力から分類用構造に変換する。"""
    status_map = {
        "added": "A",
        "modified": "M",
        "removed": "D",
        "renamed": "R",
        "copied": "C",
        "changed": "M",
    }
    entries: list[dict[str, Any]] = []
    for raw in output.splitlines():
        if not raw.strip():
            continue
        cols = raw.split("\t")
        status_raw = cols[0].strip().lower() if cols else "modified"
        path = cols[1].strip() if len(cols) > 1 else ""
        previous = cols[2].strip() if len(cols) > 2 else ""
        if not path:
            continue
        paths = []
        if previous and previous != path:
            paths.append(previous)
        paths.append(path)
        entries.append({"status": status_map.get(status_raw, status_raw[:1].upper() or "M"), "paths": paths})
    return entries


def _fetch_changed_files(pr: int, repo: str) -> list[dict[str, Any]]:
    r = subprocess.run(
        [
            "gh", "api", f"repos/{repo}/pulls/{pr}/files",
            "--paginate",
            "--jq", '.[] | [.status, .filename, (.previous_filename // "")] | @tsv',
        ],
        capture_output=True, text=True,
    )
    if r.returncode == 0:
        entries = _parse_pr_files_api_lines(r.stdout)
        if entries:
            return entries
        info("⚠ PR files API の結果が空、または解析できません。gh pr view fallback を試行")

    else:
        info(f"⚠ PR files API 取得に失敗。gh pr view fallback を試行: {r.stderr.strip()[:200]}")

    fallback = subprocess.run(
        ["gh", "pr", "view", str(pr), "--json", "files"],
        capture_output=True, text=True,
    )
    if fallback.returncode != 0:
        info(f"⚠ PR 変更ファイル一覧の取得に失敗。自動レビュー観点は共通のみ: {fallback.stderr.strip()[:200]}")
        return []
    entries = _parse_pr_files_payload(fallback.stdout)
    if not entries:
        info("⚠ PR 変更ファイル一覧が空、または解析できません。自動レビュー観点は共通のみ")
    return entries


def _path_info(path: str) -> tuple[str, str, str, str]:
    p = pathlib.PurePosixPath(path.replace("\\", "/"))
    lower = str(p).lower()
    normalized = "/" + lower.lstrip("./")
    return lower, normalized, p.name.lower(), pathlib.PurePosixPath(lower).suffix


def _contains_any(text: str, needles: tuple[str, ...]) -> bool:
    return any(n in text for n in needles)


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
        or name in {"dockerfile", "makefile", ".editorconfig"}
        or lower.startswith(".github/")
        or (ext in CONFIG_EXTENSIONS and ("/config/" in normalized or "/configs/" in normalized))
    )


def _is_api_contract_path(path: str) -> bool:
    lower, normalized, _, _ = _path_info(path)
    return _contains_any(normalized, API_CONTRACT_MARKERS) or "openapi" in lower or "swagger" in lower


def _is_auth_security_path(path: str) -> bool:
    lower, _, _, _ = _path_info(path)
    return any(marker in lower for marker in AUTH_SECURITY_MARKERS)


def _is_frontend_path(path: str) -> bool:
    _, normalized, _, ext = _path_info(path)
    return ext in FRONTEND_EXTENSIONS or "/components/" in normalized or "/pages/" in normalized


def _is_performance_path(path: str) -> bool:
    lower, _, _, _ = _path_info(path)
    return any(marker in lower for marker in PERFORMANCE_MARKERS)


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
    lower, normalized, _, _ = _path_info(path)
    return _contains_any(normalized, INFRA_MARKERS) or any(marker in lower for marker in INFRA_MARKERS)


PATH_CATEGORY_RULES = (
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


def _auto_review_instructions(categories: list[str]) -> str:
    templates = {
        "common": COMMON_REVIEW_TEMPLATE,
        "docs_only": DOCS_ONLY_REVIEW_TEMPLATE,
        "code": CODE_REVIEW_TEMPLATE,
        "db_migration": DB_MIGRATION_REVIEW_TEMPLATE,
        "test": TEST_REVIEW_TEMPLATE,
        "dependency": DEPENDENCY_REVIEW_TEMPLATE,
        "config_ci": CONFIG_CI_REVIEW_TEMPLATE,
        "api_contract": API_CONTRACT_REVIEW_TEMPLATE,
        "auth_security": AUTH_SECURITY_REVIEW_TEMPLATE,
        "frontend": FRONTEND_REVIEW_TEMPLATE,
        "performance": PERFORMANCE_REVIEW_TEMPLATE,
        "deletion_rename": DELETION_RENAME_REVIEW_TEMPLATE,
        "generated": GENERATED_REVIEW_TEMPLATE,
        "i18n": I18N_REVIEW_TEMPLATE,
        "infra": INFRA_REVIEW_TEMPLATE,
    }
    return "\n\n".join(templates[c] for c in categories if c in templates)


def _combined_review_instructions(auto: str, manual: str) -> str:
    return "\n\n".join(part for part in (auto.strip(), manual.strip()) if part)


def _extra_review_instructions(args: argparse.Namespace) -> str:
    """cross-review launcher に渡す追加レビュー観点を組み立てる。

    `--focus` は短い観点を直接渡す用途、`--extra-instructions-file` は長めの
    チェックリストを渡す用途。両方指定された場合は順に連結する。
    """
    parts: list[str] = []
    focus = getattr(args, "focus", None)
    if focus and str(focus).strip():
        parts.append(str(focus).strip())

    extra_file = getattr(args, "extra_instructions_file", None)
    if extra_file:
        path = pathlib.Path(extra_file)
        try:
            text = path.read_text(encoding="utf-8").strip()
        except OSError as exc:
            die(f"追加レビュー観点ファイルを読めません: {path} ({exc})")
        if text:
            parts.append(text)

    return "\n\n".join(parts)


def _round_started_unixtime(round_entry: dict[str, Any]) -> float | None:
    """``round.started_at`` (ISO 8601) を UNIX time (秒) に変換する。

    fix 戻り値ファイルの mtime と比較して、round 開始前に書かれた古い
    ファイルを fallback から除外するために使う。
    パース失敗時は None を返し、呼び出し側で「検証スキップ」を選ばせる。
    """
    started = round_entry.get("started_at")
    if not started:
        return None
    try:
        return _dt.datetime.fromisoformat(started).timestamp()
    except (TypeError, ValueError):
        return None


def _is_fresh_fix_result(
    path: pathlib.Path,
    pr: int,
    round_started_ts: float | None,
    is_canonical: bool = False,
) -> tuple[bool, dict[str, Any] | None]:
    """fallback 候補の fix 戻り値ファイルを採用してよいか判定する。

    検証項目:
      1. ファイル mtime が `round_started_ts` 以降であること
         (round 開始前に作られた = 古い実行 / 別リポジトリの同番号 PR の残骸)
      2. JSON 内に `pr` フィールドがある場合は対象 PR と一致すること
         (`pr` フィールドが無い場合は 1 のみで判定)

    Returns:
      ``(is_fresh, parsed_payload)`` のタプル。``is_fresh=True`` の場合のみ
      ``parsed_payload`` (dict) が返る。呼び出し側はこれを使って再パースを省略できる。

    挙動:
      - 古い候補・`pr` 不一致は警告を stderr に出して ``(False, None)`` を返し、
        呼び出し側で次の候補へ進む。
      - `is_canonical=True` (= ``$TMP_DIR/fix-pr<PR>-result.json`` 正規パス) で
        **読み取り失敗 (OSError / JSONDecodeError)** が発生した場合のみ
        即時 ``die(code=3)`` する (codex round 2 指摘: 正規パスが壊れているのに
        後続候補へ流れて別 PR の戻り値を誤マージする事故を防ぐ)。
        正規パスでも `pr` 不一致 / stale mtime は fallback 継続対象とする。
    """
    # 1. mtime チェック
    if round_started_ts is not None:
        try:
            mtime = path.stat().st_mtime
        except OSError as exc:
            info(f"⚠ fallback 候補 stat 失敗 ({path}): {exc} — skip")
            return False, None
        if mtime < round_started_ts:
            info(
                f"⚠ fallback 候補が round 開始前の古いファイル ({path}, "
                f"mtime={_dt.datetime.fromtimestamp(mtime).isoformat(timespec='seconds')} "
                f"< round_started={_dt.datetime.fromtimestamp(round_started_ts).isoformat(timespec='seconds')}) "
                "— skip"
            )
            return False, None

    # 2. JSON 内 `pr` フィールドの一致 (任意)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if is_canonical:
            die(
                f"正規パスの fix 戻り値ファイルの読み取りに失敗 ({path}): {exc}。"
                " 後続 fallback への流れ込みを防ぐため即時中断。",
                code=3,
            )
        info(f"⚠ fallback 候補 JSON 解析失敗 ({path}): {exc} — skip")
        return False, None
    # gemini round 3 指摘: `json.loads` は dict 以外 (list 等) も返す。
    # 後続の `payload.get(...)` や cmd_merge_fix 側の `.get()` でクラッシュしないよう、
    # dict でない場合は warn を出して fallback 不採用 ((False, None)) として扱う。
    #
    # codex round 4 指摘: ただし `is_canonical=True` (= 正規パス) で非 dict が返った
    # 場合は parse 失敗と同じく即時 die(code=3) する。skip で後続 `/tmp/` fallback に
    # 流れると、壊れた正規出力を無視して別実行の戻り値を誤マージする経路が残るため。
    if not isinstance(payload, dict):
        if is_canonical:
            die(
                f"正規パスの fix 戻り値ファイルが dict ではない "
                f"({path}, type={type(payload).__name__})。"
                " 後続 fallback への流れ込みを防ぐため即時中断。",
                code=3,
            )
        info(
            f"⚠ fallback 候補 JSON が dict ではない ({path}, type={type(payload).__name__}) "
            "— skip"
        )
        return False, None
    file_pr = payload.get("pr")
    if file_pr is not None:
        try:
            file_pr_int = int(file_pr)
        except (TypeError, ValueError):
            info(
                f"⚠ fallback 候補の pr フィールドが数値として解釈できない "
                f"({path}, file_pr={file_pr!r}) — skip"
            )
            return False, None
        if file_pr_int != int(pr):
            info(
                f"⚠ fallback 候補の pr 不一致 ({path}, file_pr={file_pr} != pr={pr}) "
                "— 別 PR の戻り値の可能性。skip"
            )
            return False, None
    return True, payload


def _load(pr: int) -> dict[str, Any]:
    p = _state_path(pr)
    if not p.exists():
        die(f"state.json not found: {p}")
    return json.loads(p.read_text(encoding="utf-8"))


def _save(pr: int, state: dict[str, Any]) -> None:
    p = _state_path(pr)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def _sh(cmd: list[str], check: bool = True) -> str:
    r = subprocess.run(cmd, capture_output=True, text=True)
    if check and r.returncode != 0:
        die(f"command failed ({' '.join(cmd)}): {r.stderr.strip()}")
    return r.stdout.strip()


def die(msg: str, code: int = 1) -> None:
    print(f"❌ {msg}", file=sys.stderr)
    sys.exit(code)


def info(msg: str) -> None:
    print(msg, file=sys.stderr)


# ---------------- subcommands ----------------

def cmd_init(args: argparse.Namespace) -> None:
    """Step 0 — state 初期化 or 既存 state 引き継ぎ + プリチェック。"""
    pr = args.pr
    manual_extra_review = _extra_review_instructions(args)
    # worktree path を先に解決してから tmp_dir を決定する。
    # tmp_dir は <worktree>/.cross_review/ に配置し、gemini の workspace 制約を根本回避。
    # path には repo slug を含め、他リポジトリの同一 PR 番号と衝突しないようにする。
    repo = _sh(["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
    worktree = str(pathlib.Path(args.worktree).resolve()) if args.worktree else str(
        _default_worktree_base() / _repo_slug(repo) / f"pr{pr}")

    # worktree 存在チェック用: _tmp_dir() は mkdir するため、先に呼ぶと
    # worktree ディレクトリが副作用で作成され exists() が常に true になる。
    # そのため _tmp_dir() 呼び出しは worktree 作成/確認の後に行う。

    # 再開チェック: CROSS_REVIEW_TMP_DIR が設定されている場合はそちらを優先し、
    # 未設定なら <worktree>/.cross_review/ を直接パスとして組む。
    # _tmp_dir() は mkdir 副作用があるため使用せず、パス解決のみ行う。
    env_tmp = os.environ.get("CROSS_REVIEW_TMP_DIR")
    if env_tmp:
        resume_dir = pathlib.Path(env_tmp).resolve()
    else:
        resume_dir = pathlib.Path(worktree) / ".cross_review"
    resume_state_file = resume_dir / f"cross-review-pr{pr}-state.json"
    if resume_state_file.exists():
        st = json.loads(resume_state_file.read_text(encoding="utf-8"))
        if st.get("final") is None:
            state_changed = False
            if "auto_review_instructions" not in st:
                changed_files = _fetch_changed_files(pr, st.get("repo") or repo)
                categories = _classify_changed_files(changed_files)
                st["changed_files"] = changed_files
                st["auto_review_categories"] = categories
                st["auto_review_instructions"] = _auto_review_instructions(categories)
                state_changed = True
            if manual_extra_review:
                st["manual_extra_review_instructions"] = manual_extra_review
                # 後方互換: 旧 key も manual 指示として保持する。
                st["extra_review_instructions"] = manual_extra_review
                state_changed = True
            manual = st.get("manual_extra_review_instructions") or st.get("extra_review_instructions") or ""
            combined = _combined_review_instructions(
                st.get("auto_review_instructions") or "",
                manual,
            )
            if st.get("review_instructions") != combined:
                st["review_instructions"] = combined
                state_changed = True
            if state_changed:
                resume_state_file.write_text(
                    json.dumps(st, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                info("↻ 追加レビュー観点を state に反映して再開")
            tmp_dir = _tmp_dir(worktree)
            wt = st.get("worktree_path") or ""
            info(f"↻ 前回中断 state から再開（round={len(st.get('rounds', []))}）")
            print(f'PR={st["current_pr"]}')
            print(f'WORKTREE={shlex.quote(str(wt))}')
            print(f'TMP_DIR={shlex.quote(str(tmp_dir))}')
            print(f'REPO={shlex.quote(str(st.get("repo") or ""))}')
            print(f'HEAD_BRANCH={shlex.quote(str(st.get("head_branch") or ""))}')
            print(f'BASE_BRANCH={shlex.quote(str(st.get("base_branch") or ""))}')
            print(f"IS_OWN_PR={'1' if st.get('is_own_pr') else '0'}")
            print(f"EVENT_DOWNGRADE={'1' if st.get('event_downgrade') else '0'}")
            print(f"HAS_EXTRA_REVIEW_INSTRUCTIONS={'1' if st.get('review_instructions') else '0'}")
            print(f"RESUMED=1")
            return

    # 新規 init: プリチェック
    me = _sh(["gh", "api", "user", "--jq", ".login"])
    author = _sh(["gh", "pr", "view", str(pr), "--json", "author", "--jq", ".author.login"])
    is_own = (me == author)
    event_downgrade = is_own
    if is_own:
        info(f"⚠ 自分の PR (author={me}) — REQUEST_CHANGES → COMMENT 強制ダウングレード")

    # worktree 分離 — _tmp_dir() より先に worktree を作成/確認する
    head_branch = _sh(["gh", "pr", "view", str(pr), "--json", "headRefName", "--jq", ".headRefName"])
    base_branch = _sh(["gh", "pr", "view", str(pr), "--json", "baseRefName", "--jq", ".baseRefName"])
    changed_files = _fetch_changed_files(pr, repo)
    auto_review_categories = _classify_changed_files(changed_files)
    auto_review = _auto_review_instructions(auto_review_categories)
    review_instructions = _combined_review_instructions(auto_review, manual_extra_review)
    if not pathlib.Path(worktree).exists():
        _create_worktree(worktree, pr, head_branch)
    elif _is_registered_worktree(worktree):
        info(f"↻ 既存 worktree 流用: {worktree}")
    else:
        # パスは存在するが現リポジトリの worktree ではない (別リポジトリの残骸等)。
        # 流用すると git 操作が壊れるため退避して作り直す。
        stale = f"{worktree}.stale-{time.strftime('%Y%m%d%H%M%S')}"
        pathlib.Path(worktree).rename(stale)
        info(f"⚠ 現リポジトリの worktree でないため退避: {stale}")
        _create_worktree(worktree, pr, head_branch)

    # worktree 作成/確認後に _tmp_dir() を呼ぶ (ここで .cross_review/ が作られる)
    tmp_dir = _tmp_dir(worktree)
    state_file = tmp_dir / f"cross-review-pr{pr}-state.json"

    # 既存コメントスナップショット（重複指摘防止）。
    # 3 ソース (インラインコメント / レビュー body / PR レベルコメント) を
    # fix skill の共有スクリプトで一括取得する。
    fetch_script = pathlib.Path(__file__).resolve().parent.parent.parent / "fix" / "scripts" / "fetch-pr-comments.sh"
    r = subprocess.run(
        [str(fetch_script), repo, str(pr)],
        capture_output=True, text=True,
    )
    existing_path = tmp_dir / f"cross-review-pr{pr}-existing-comments.txt"
    if r.returncode == 0:
        existing_path.write_text(r.stdout, encoding="utf-8")
    else:
        die(f"既存コメント取得失敗 (重複検出無効のため中断): {r.stderr.strip()[:200]}")

    state = {
        "started_at": _now(),
        "max_rounds": args.max_rounds,
        "rotate_after": args.rotate_after,
        "only": args.only,
        "current_pr": pr,
        "worktree_path": worktree,
        "tmp_dir": str(tmp_dir),
        "repo": repo,
        "head_branch": head_branch,
        "base_branch": base_branch,
        "pr_author": author,
        "is_own_pr": is_own,
        "event_downgrade": event_downgrade,
        "changed_files": changed_files,
        "auto_review_categories": auto_review_categories,
        "auto_review_instructions": auto_review,
        "manual_extra_review_instructions": manual_extra_review,
        # 後方互換: 旧 key は manual 指示を保持する。
        "extra_review_instructions": manual_extra_review,
        "review_instructions": review_instructions,
        "pr_history": [{"pr": pr, "opened_at": _now(), "closed_at": None, "rounds": 0}],
        "rounds": [],
        "deferred_nits": [],
        "final": None,
    }
    state_file.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
    info(f"✅ state 初期化: {state_file}")
    print(f"PR={pr}")
    print(f'WORKTREE={shlex.quote(str(worktree))}')
    print(f'TMP_DIR={shlex.quote(str(tmp_dir))}')
    print(f'REPO={shlex.quote(str(repo))}')
    print(f'HEAD_BRANCH={shlex.quote(str(head_branch))}')
    print(f'BASE_BRANCH={shlex.quote(str(base_branch))}')
    print(f"IS_OWN_PR={'1' if is_own else '0'}")
    print(f"EVENT_DOWNGRADE={'1' if event_downgrade else '0'}")
    print(f"HAS_EXTRA_REVIEW_INSTRUCTIONS={'1' if review_instructions else '0'}")
    print("RESUMED=0")


def cmd_start_round(args: argparse.Namespace) -> None:
    """Step 1 — round 開始判定。"""
    st = _load(args.pr)
    total = len(st["rounds"])
    max_r = st["max_rounds"]
    if total >= max_r:
        st["final"] = "max_rounds"
        st["ended_at"] = _now()
        _save(args.pr, st)
        die(f"max_rounds={max_r} 到達。中断。", code=1)

    pr = st["current_pr"]
    round_no = total + 1
    round_in_pr = sum(1 for r in st["rounds"] if r["pr"] == pr) + 1

    # round エントリを開く
    st["rounds"].append({
        "round": round_no,
        "pr": pr,
        "started_at": _now(),
    })
    _save(args.pr, st)

    info(f"=== Round {round_no} / {max_r} (PR #{pr}, round_in_pr={round_in_pr}) ===")
    print(f"ROUND={round_no}")
    print(f"ROUND_IN_PR={round_in_pr}")
    print(f"PR={pr}")
    print(f"MAX_ROUNDS={max_r}")
    print(f"ROTATE_AFTER={st['rotate_after']}")


def cmd_read_result(args: argparse.Namespace) -> None:
    """Step 2.5 — codex/gemini の result.json を state にマージ。"""
    agent = args.agent
    pr = args.pr
    rfile = pathlib.Path(args.file or _resolve_tmp_dir(pr) / f"{agent}-review-pr{pr}-result.json")
    if not rfile.exists() or rfile.stat().st_size == 0:
        die(f"{agent}: result 未生成 ({rfile})")

    try:
        r = json.loads(rfile.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        die(f"{agent}: result.json の parse に失敗 ({rfile}): {exc}", code=3)

    # gemini round 4 指摘: result.json は本来 dict だが、launcher の出力バグや
    # 別実行の残骸で list / str が入り込むと `r.get(...)` で AttributeError になる。
    # 不正な review result はバグなので即時 die(code=3) で停止させる。
    if not isinstance(r, dict):
        die(
            f"{agent}: result.json が dict ではない "
            f"({rfile}, type={type(r).__name__})。review launcher の出力形式不正。",
            code=3,
        )

    # 別名フィールドへのフォールバック (gemini が `intent` / `comment_count` を使う変則 JSON を
    # 書き出す既知のケースに対応する。仕様としては `event` / `comments_count` が正)
    intent = r.get("event") or r.get("intent")
    posted_as = r.get("posted_as") or intent
    comments = r.get("comments_count")
    if comments is None:
        comments = r.get("comment_count")

    if intent is None:
        die(
            f"{agent}: result.json に event / intent フィールドが無い ({rfile})。"
            " launcher prompt のスキーマ違反の可能性。"
        )

    st = _load(pr)
    if not st.get("rounds"):
        die(f"{agent}: state.rounds が空。`state.py start-round` を先に呼んでください")
    st["rounds"][-1][agent] = {
        "intent": intent,
        "posted_as": posted_as,
        "comments": comments,
        "review_url": r.get("review_url"),
        "by_severity": r.get("by_severity", {}),
    }
    _save(pr, st)
    info(f"✅ {agent}: intent={intent} posted_as={posted_as} comments={comments}")


def cmd_judge(args: argparse.Namespace) -> None:
    """Step 3 — intent ベース pass 判定。

    Exit code: 0=approved, 2=continue, 1=error
    """
    pr = args.pr
    st = _load(pr)
    if not st.get("rounds"):
        die("state.rounds が空。`state.py start-round` を先に呼んでください")
    last = st["rounds"][-1]
    only = st.get("only")

    def is_pass(intent: str | None, severity: dict[str, int] | None) -> bool:
        if intent in ("APPROVE", "SKIP"):
            return True
        if intent == "COMMENT":
            sev = severity or {}
            return (sev.get("critical", 0) == 0 and sev.get("major", 0) == 0)
        return False

    codex_intent = (last.get("codex") or {}).get("intent", "SKIP")
    gemini_intent = (last.get("gemini") or {}).get("intent", "SKIP")
    codex_sev = (last.get("codex") or {}).get("by_severity")
    gemini_sev = (last.get("gemini") or {}).get("by_severity")

    codex_pass = (only == "gemini") or is_pass(codex_intent, codex_sev)
    gemini_pass = (only == "codex") or is_pass(gemini_intent, gemini_sev)

    print(f"CODEX_INTENT={codex_intent}")
    print(f"GEMINI_INTENT={gemini_intent}")

    if codex_pass and gemini_pass:
        st["final"] = "approved"
        st["ended_at"] = _now()
        _save(pr, st)
        info("✅ 両方 APPROVE。収束。")
        sys.exit(0)

    info(f"→ codex={codex_intent} gemini={gemini_intent}。修正へ。")
    sys.exit(2)


def cmd_check_oscillation(args: argparse.Namespace) -> None:
    """Step 4 — path:line 重複率を計算。

    前ラウンドと現ラウンドで重複が 50% 以上なら final=oscillation で中断。
    rotation 直後は round_in_pr<2 なのでスキップ。
    """
    pr = args.pr
    st = _load(pr)
    rounds = st["rounds"]
    current_pr = st["current_pr"]
    same_pr = [r for r in rounds if r["pr"] == current_pr]
    if len(same_pr) < 2:
        info("⏭ round_in_pr<2: 振動検知スキップ")
        sys.exit(2)  # continue

    prev_round_no = same_pr[-2]["round"]
    curr_round_no = same_pr[-1]["round"]

    def collect_keys(round_no: int) -> set[str]:
        keys: set[str] = set()
        for agent in ("codex", "gemini"):
            p = _payload_path(agent, pr, round_no)
            if not p.exists():
                continue
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
            except json.JSONDecodeError:
                continue
            # gemini round 4 指摘: payload は本来 dict (comments: [...]) だが、
            # launcher のバグで list / str が入り込むと `payload.get(...)` で
            # AttributeError になる。不正な review payload はバグなので
            # 即時 die(code=3) で停止させる。
            if not isinstance(payload, dict):
                die(
                    f"{agent}: payload.json が dict ではない "
                    f"({p}, type={type(payload).__name__})。"
                    " review launcher の出力形式不正。",
                    code=3,
                )
            for c in payload.get("comments", []):
                if not isinstance(c, dict):
                    # comments エントリが dict でない場合も同様に致命扱い
                    die(
                        f"{agent}: payload.comments のエントリが dict ではない "
                        f"({p}, type={type(c).__name__})。",
                        code=3,
                    )
                path = c.get("path")
                line = c.get("line") or c.get("start_line")
                if path and line is not None:
                    keys.add(f"{path}:{line}")
        return keys

    prev = collect_keys(prev_round_no)
    curr = collect_keys(curr_round_no)
    if not curr:
        info("⏭ 現ラウンドの payload なし: 振動検知スキップ")
        sys.exit(2)
    overlap = prev & curr
    ratio = len(overlap) / len(curr)
    info(f"振動検知: overlap={len(overlap)}/{len(curr)} ({ratio:.0%})")

    if ratio >= 0.5:
        st["final"] = "oscillation"
        st["ended_at"] = _now()
        _save(pr, st)
        die(f"振動検知 — 同一箇所が {ratio:.0%} 重複。中断。", code=4)
    sys.exit(2)


def _count(v: Any) -> int:
    """int(件数) でも list でも None でも件数(int)に正規化する。

    fix 結果スキーマ上 deferred/rejected/resolved_threads は list が正だが、
    fix サブエージェントが int(件数) を書いてしまうケースがあり、その場合に
    len() が `TypeError: object of type 'int' has no len()` で落ちるのを防ぐ。
    """
    if isinstance(v, bool):
        # bool は int のサブクラスだが件数として扱わない
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, dict):
        # LLM が単一要素を list ではなく dict 単体で返すケースを 1 件として扱う。
        return 1
    if isinstance(v, (list, tuple)):
        return len(v)
    if isinstance(v, str) and v.strip().isdigit():
        # LLM が件数を数値文字列 (例: "3" や " 3 ") で返すケースを許容する。
        # strip 後 isdigit() なので前後空白を許し、負号・小数点は引き続き弾く
        # (件数は非負整数なので十分)。
        return int(v.strip())
    return 0


def cmd_merge_fix(args: argparse.Namespace) -> None:
    """Step 5 後段 — fix サブエージェント戻り値を state にマージ + CI 分類。

    Exit code: 0=continue, 3=ci-code-fail (final=error)
    """
    pr = args.pr

    # state を先に読み、fallback 検証用の round 開始時刻を取得する
    # (round 開始前の古いファイルや、別リポジトリの同番号 PR の戻り値を
    # 誤マージするのを防ぐ)。
    st = _load(pr)
    if not st.get("rounds"):
        die("state.rounds が空。`state.py start-round` を先に呼んでください", code=3)
    round_started_ts = _round_started_unixtime(st["rounds"][-1])

    # 戻り値ファイルの探索順:
    #   1. --file 明示 (ユーザー指定なので mtime/pr 検証はスキップ)
    #   2. $TMP_DIR/fix-pr<PR>-result.json (正規; _tmp_dir() 解決先)
    #   3. /tmp/fix-pr<PR>-result.json (旧プロンプトで /tmp を指定したサブエージェント救済)
    # 2, 3 は PR 番号だけで命名されているため、別 round / 別リポジトリの
    # 古い結果を拾わないよう mtime と (あれば) JSON 内の `pr` で検証する。
    explicit = pathlib.Path(args.file) if args.file else None
    canonical_path = _resolve_tmp_dir(pr) / f"fix-pr{pr}-result.json"
    legacy_tmp_path = pathlib.Path(f"/tmp/fix-pr{pr}-result.json")
    # (path, is_canonical) のタプル: 正規パス (canonical) の parse 失敗は die(code=3) する
    fallback_candidates: list[tuple[pathlib.Path, bool]] = [
        (canonical_path, True),
        (legacy_tmp_path, False),
    ]

    ffile: pathlib.Path | None = None
    fix: dict[str, Any] | None = None
    if explicit is not None:
        # codex round 4 指摘: `--file` 明示時は fallback 探索に進まず即時失敗させる。
        # ユーザーが特定ファイルを指定しているのに、それが存在しない / 空 / JSON 不正
        # だった場合、無言で fallback に流れて別実行の戻り値を誤マージすると事故になる。
        if not explicit.exists():
            die(
                f"--file で指定されたパスが存在しません: {explicit}",
                code=3,
            )
        if explicit.stat().st_size == 0:
            die(
                f"--file で指定されたファイルが空です: {explicit}",
                code=3,
            )
        try:
            fix = json.loads(explicit.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            die(
                f"--file 指定の fix 戻り値ファイルの読み取り / parse に失敗 "
                f"({explicit}): {exc}",
                code=3,
            )
        # gemini round 3 指摘: `--file` で `list` 等の non-dict JSON が渡されると
        # 後続の `fix.get(...)` でクラッシュする。即時 die(code=3) で中断。
        if not isinstance(fix, dict):
            die(
                f"--file 指定の fix 戻り値ファイルが dict ではない "
                f"({explicit}, type={type(fix).__name__})。"
                " fix サブエージェント出力の形式不正。",
                code=3,
            )
        ffile = explicit
        # 明示指定は stale 検証スキップ
    else:
        for c, is_canonical in fallback_candidates:
            if not (c.exists() and c.stat().st_size > 0):
                continue
            is_fresh, parsed = _is_fresh_fix_result(c, pr, round_started_ts, is_canonical=is_canonical)
            if not is_fresh:
                continue
            ffile = c
            fix = parsed  # 既にパース済みのデータを再利用 (gemini round 2 指摘の性能改善)
            break

    if ffile is None or fix is None:
        checked = ([str(explicit)] if explicit else []) + [str(c) for c, _ in fallback_candidates]
        die(
            "fix サブエージェントが戻り値ファイルを生成しなかった "
            f"(checked: {checked})",
            code=3,
        )

    # key 名 fallback (サブエージェントが別名で書いた場合の救済)。
    # 正規は fix_commit / fixed_count、別名は commit_sha / fixed のみ受理する。
    fix_commit = fix.get("fix_commit") or fix.get("commit_sha")
    fixed_count = fix.get("fixed_count")
    if fixed_count is None:
        fixed_count = fix.get("fixed", 0)

    # `st` は冒頭の fallback 検証で既に load 済み。
    round_no = st["rounds"][-1]["round"]

    # deferred は list が正だが、LLM がスキーマを無視して文字列リスト
    # (例: ["nit: ..."]) や単一 dict、int(件数) を返すケースがある。後段の
    # deferred_nits 展開ループは dict 以外をスキップするため、まず dict 要素のみへ
    # 正規化する (単一 dict は 1 件として包む)。
    _deferred_raw = fix.get("deferred")
    if isinstance(_deferred_raw, list):
        _deferred_nits = [d for d in _deferred_raw if isinstance(d, dict)]
    elif isinstance(_deferred_raw, dict):  # 単一 dict フォールバック (gemini #3)
        _deferred_nits = [_deferred_raw]
    else:
        _deferred_nits = []

    # 保存件数の単一整合ルール:
    #   - 構造化データ (list / dict) は per-item を保持できるので、展開件数
    #     (len(_deferred_nits)) を保存し deferred_nits の件数と一致させる。
    #   - int / 数値文字列は per-item データを失った「劣化表現」なので、件数を
    #     失わないよう _count() の値を保存する (展開はできないので nits は空)。
    if isinstance(_deferred_raw, (list, dict)):
        _deferred_count = len(_deferred_nits)
    else:
        _deferred_count = _count(_deferred_raw)

    st["rounds"][-1]["fix"] = {
        "commit": fix_commit,
        "fixed": fixed_count,
        # deferred は上記の単一整合ルールで算出した件数を保存する。
        # resolved_threads / rejected は件数しか保存せず後段ループが無いため _count() で可。
        "deferred": _deferred_count,
        "rejected": _count(fix.get("rejected")),
        "resolved_threads": _count(fix.get("resolved_threads")),
        "ci": fix.get("ci_status"),
        "ci_failed_checks": fix.get("ci_failed_checks", []) or [],
        "ci_note": fix.get("ci_note"),
        "by_severity": fix.get("by_severity", {}),
    }
    st["rounds"][-1]["ended_at"] = _now()
    for d in _deferred_nits:
        st["deferred_nits"].append({**d, "pr": pr, "round": round_no})
    _save(pr, st)

    # CI 分類
    if (fix.get("ci_status") or "").upper() != "FAILURE":
        info(f"✅ fix マージ完了 (commit={fix_commit} fixed={fixed_count})")
        return

    code_patterns = ("pint", "larastan", "phpstan", "test", "lint", "type",
                     "build", "ruff", "eslint", "tsc", "mypy")
    meta_patterns = ("check_pr_requirements", "assignees", "reviewers", "labels", "meta")
    failed = fix.get("ci_failed_checks") or []
    code_fail = False
    meta_fail = False
    for name in failed:
        low = name.lower()
        if any(p in low for p in meta_patterns):
            meta_fail = True
        elif any(p in low for p in code_patterns):
            code_fail = True
        else:
            code_fail = True  # 不明は code-fail（保守的）

    if code_fail:
        st["final"] = "error"
        st["ended_at"] = _now()
        _save(pr, st)
        die(f"コード関連 CI 失敗。中断: {failed}", code=3)

    # meta only: 継続
    note = f"メタチェックのみ失敗: {failed} — コードと無関係のため継続"
    st["rounds"][-1]["fix"]["ci_note"] = note
    _save(pr, st)
    info(f"⚠ メタチェックのみ失敗 ({failed}) — 継続")


def cmd_should_rotate(args: argparse.Namespace) -> None:
    """Step 6 — PR ローテーション要否。Exit 0=rotate, 2=keep.

    判定は ``round_in_pr >= rotate_after && total < max_rounds`` のみで、
    rotate-pr.sh の ``--mode light|squash`` どちらでも同じ条件を使う。
    state.json の key は ``STATE_PR`` (最初に init した PR 番号) で固定なので、
    light モードで head_branch が変わらない場合でも整合する。
    """
    pr = args.pr
    st = _load(pr)
    current_pr = st["current_pr"]
    round_in_pr = sum(1 for r in st["rounds"] if r["pr"] == current_pr)
    total = len(st["rounds"])
    rotate_after = st["rotate_after"]
    max_r = st["max_rounds"]
    if round_in_pr >= rotate_after and total < max_r:
        info(f"🔄 PR #{current_pr} が {round_in_pr} round 経過 — ローテーション必要")
        print(f"CURRENT_PR={current_pr}")
        print(f"ROUND_IN_PR={round_in_pr}")
        sys.exit(0)
    sys.exit(2)


def cmd_set_current_pr(args: argparse.Namespace) -> None:
    """PR ローテーション完了後の state 更新。

    rotate-pr.sh の light / squash どちらでも、新 PR 番号を受け取って
    ``current_pr`` を切り替え、``pr_history`` に新 PR エントリを追加する。
    state.json のファイル名は ``STATE_PR`` (= ``args.pr``) ベースで不変なので、
    light モードで head_branch が変わらないケースでも問題なく追跡できる。
    """
    pr = args.pr  # 旧 PR (state file の key)
    new_pr = args.new_pr
    st = _load(pr)
    old_pr = st["current_pr"]
    now = _now()
    # 旧 PR の history を closed に
    for h in st["pr_history"]:
        if h["pr"] == old_pr and h["closed_at"] is None:
            h["closed_at"] = now
            h["rounds"] = sum(1 for r in st["rounds"] if r["pr"] == old_pr)
            break
    st["pr_history"].append({"pr": new_pr, "opened_at": now, "closed_at": None, "rounds": 0})
    st["current_pr"] = new_pr
    _save(pr, st)
    info(f"✅ current_pr: {old_pr} → {new_pr}")


def cmd_report(args: argparse.Namespace) -> None:
    """Step 8 — deferred nit + ラウンドサマリ表示。"""
    pr = args.pr
    st = _load(pr)
    final = st.get("final") or "in_progress"
    total = len(st["rounds"])
    prs = [h["pr"] for h in st["pr_history"]]
    rotated = max(0, len(prs) - 1)

    print(f"## 最終ステータス: {final}")
    print(f"## 総ラウンド数: {total} / PR数: {len(prs)} (rotated {rotated} 回)")
    print()
    print("## PR 履歴")
    for h in st["pr_history"]:
        state_str = "closed" if h.get("closed_at") else "open"
        print(f"- #{h['pr']} ({state_str}, {h.get('rounds', 0)} rounds)")
    print()
    print("## ラウンドサマリ")
    print("| round | PR | codex | gemini | fix | CI |")
    print("|---|---|---|---|---|---|")
    for r in st["rounds"]:
        codex = r.get("codex") or {}
        gemini = r.get("gemini") or {}
        fix = r.get("fix") or {}
        codex_s = f"{codex.get('intent', '-')} ({codex.get('comments', '-')})" if codex else "-"
        gemini_s = f"{gemini.get('intent', '-')} ({gemini.get('comments', '-')})" if gemini else "-"
        fix_s = "-"
        if fix:
            fix_s = f"{(fix.get('commit') or '')[:7]} ({fix.get('fixed', 0)} fixed, {fix.get('deferred', 0)} deferred)"
        ci_s = fix.get("ci") or "-"
        print(f"| {r['round']} | #{r['pr']} | {codex_s} | {gemini_s} | {fix_s} | {ci_s} |")
    print()

    nits = st.get("deferred_nits") or []
    if nits:
        print(f"## 残 deferred nit ({len(nits)} 件)")
        for n in nits:
            print(f"- [{n.get('severity')}] {n.get('path')}:{n.get('line')} — {n.get('summary')}")
        print()
        print("これらの nit を一括対応する場合は再度 `/ndf:fix <PR#>` を起動してください。")
    else:
        print("## 残 deferred nit: なし")


# ---------------- main ----------------

def main() -> None:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="Step 0 — state 初期化 or 再開")
    sp.add_argument("pr", type=int)
    sp.add_argument("--max-rounds", type=int, default=12)
    sp.add_argument("--rotate-after", type=int, default=8)
    sp.add_argument("--only", choices=["codex", "gemini"], default=None)
    sp.add_argument("--worktree", default=None)
    sp.add_argument(
        "--focus",
        default=None,
        help="追加レビュー観点。例: ドキュメントとコードの整合性を重点的に確認",
    )
    sp.add_argument(
        "--extra-instructions-file",
        default=None,
        help="追加レビュー観点を記載した UTF-8 テキストファイル",
    )
    sp.set_defaults(func=cmd_init)

    sp = sub.add_parser("start-round", help="Step 1 — round 開始判定")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_start_round)

    sp = sub.add_parser("read-result", help="Step 2.5 — review result を state にマージ")
    sp.add_argument("pr", type=int)
    sp.add_argument("agent", choices=["codex", "gemini"])
    sp.add_argument("--file", default=None)
    sp.set_defaults(func=cmd_read_result)

    sp = sub.add_parser("judge", help="Step 3 — intent ベース pass 判定 (0=approved/2=continue)")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_judge)

    sp = sub.add_parser("check-oscillation", help="Step 4 — path:line 重複率を計算")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_check_oscillation)

    sp = sub.add_parser("merge-fix", help="Step 5 post — fix 戻り値マージ + CI 分類")
    sp.add_argument("pr", type=int)
    sp.add_argument("--file", default=None)
    sp.set_defaults(func=cmd_merge_fix)

    sp = sub.add_parser("should-rotate", help="Step 6 — rotate 要否 (0=rotate/2=keep)")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_should_rotate)

    sp = sub.add_parser("set-current-pr", help="rotation 後の current_pr 更新")
    sp.add_argument("pr", type=int, help="state file の元 PR")
    sp.add_argument("new_pr", type=int)
    sp.set_defaults(func=cmd_set_current_pr)

    sp = sub.add_parser("report", help="Step 8 — deferred nit + サマリ表示")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_report)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
