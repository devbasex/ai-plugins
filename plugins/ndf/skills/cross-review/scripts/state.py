#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""cross-review state.json 操作 CLI。

`<worktree>/.cross_review/cross-review-pr<PR>-state.json` の初期化 / 読み書きと、
ループ判定（round 開始 / 収束 / 振動 / PR ローテーション要否 / fix 結果マージ /
deferred nit レポート）を 1 つの CLI に集約する。

すべての出力は人間可読 + KEY=VALUE 形式（eval / read で取り回し可能）。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import os
import pathlib
import re
import shlex
import subprocess
import sys
import tempfile
import time
from typing import Any, NamedTuple

# 待ち行列は共通層に置く。指し方の契約は `plugins/ndf/scripts/lib/README.md` にある。
# **モジュール名を `queue` にしない。** 標準ライブラリに同じ名前があり、共通層を
# `sys.path` の先頭へ入れるとプロセス全体で標準ライブラリ側が隠れる。
sys.path.insert(
    0, str(pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"))
import post_queue  # noqa: E402


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


# ---------------- GitHub の呼び出し ----------------
#
# **尽きるのは GraphQL 側である**（#271）。`gh pr view` は項目を増やしても
# 1 リクエストのままだが、REST 側は上限 5,000 のうち大半が残ったまま進行が止まる。
# 項目をまとめる先を REST にして、GraphQL の消費を実行ごと・ラウンドごとに 0 点へ寄せる。


class RestResponse(NamedTuple):
    """`gh api -i` の 1 回の応答。ヘッダと本文を組で持つ。

    残量（`x-ratelimit-remaining`）は**通常の要求の応答ヘッダからしか読めない**。
    `gh api rate_limit` は同じ時刻でも消費を反映しない（実測）。読むためだけの
    呼び出しを置かず、毎回の応答から拾う。
    """

    headers: dict[str, str]
    body: Any
    rate_remaining: int | None
    rate_reset: str | None


def _parse_rest_headers(text: str) -> tuple[dict[str, str], str]:
    """`gh api -i` の出力を、ヘッダの辞書と本文へ分ける。

    状態行だけが `\r` を持たず、以降のヘッダは `\r\n` で終わる（実測）。行末の
    違いで分けられなくなるため、空行そのものを区切りとして読む。
    """
    headers: dict[str, str] = {}
    lines = text.splitlines(keepends=True)
    body_at = len(lines)
    for i, line in enumerate(lines):
        if not line.strip():
            body_at = i + 1
            break
        name, sep, value = line.partition(":")
        if sep:
            headers[name.strip().lower()] = value.strip()
    return headers, "".join(lines[body_at:])


def _gh_rest(path: str) -> RestResponse | None:
    """REST の 1 回の要求を投げ、ヘッダと本文を返す。失敗は `None`。

    **例外を投げず、進行を止めない側へ倒す**（#291 の待ち行列を挟む位置）。
    呼び出し側は `None` を「確かめられなかった」として扱う。積む・待つ・流すは
    ここではなく呼び出し側が持つ。
    """
    try:
        r = subprocess.run(["gh", "api", "-i", path], capture_output=True, text=True)
    except OSError as exc:
        info(f"⚠ gh の実行に失敗 ({path}): {exc}")
        return None
    if r.returncode != 0:
        info(f"⚠ REST が失敗 ({path}, exit={r.returncode}): {r.stderr.strip()[:200]}")
        return None
    headers, raw = _parse_rest_headers(r.stdout)
    try:
        body = json.loads(raw) if raw.strip() else None
    except json.JSONDecodeError as exc:
        info(f"⚠ REST の応答を読み取れない ({path}): {exc}")
        return None
    remaining = headers.get("x-ratelimit-remaining")
    try:
        rate_remaining = int(remaining) if remaining is not None else None
    except ValueError:
        rate_remaining = None
    return RestResponse(
        headers=headers,
        body=body,
        rate_remaining=rate_remaining,
        rate_reset=headers.get("x-ratelimit-reset"),
    )


_REPO_URL = re.compile(
    r"(?:github\.com[:/])(?P<owner>[^/]+)/(?P<name>[^/]+?)(?:\.git)?/?$"
)


def _git_remote_url() -> str:
    """`origin` の取得元を返す。読めなければ空文字。"""
    try:
        r = subprocess.run(
            ["git", "remote", "get-url", "origin"], capture_output=True, text=True,
        )
    except OSError:
        return ""
    return r.stdout.strip() if r.returncode == 0 else ""


def _repo_from_git() -> str | None:
    """git の設定から `owner/repo` を求める。求まらなければ `None`。

    **求めた名前はそのまま使わない。** `repos/{owner}/{repo}/pulls/{PR}` の応答が
    そのまま検証になるため、誤った名前は失敗として現れる（`_fetch_pr_metadata`）。
    """
    m = _REPO_URL.search(_git_remote_url())
    return f"{m.group('owner')}/{m.group('name')}" if m else None


class PrMetadata(NamedTuple):
    """REST の 1 回の応答から取れる、Pull Request のメタデータ。"""

    repo: str
    author: str
    head_branch: str
    head_sha: str
    base_branch: str
    is_fork: bool
    rate_remaining: int | None
    rate_reset: str | None


def _pr_metadata_of(repo: str, resp: RestResponse) -> PrMetadata | None:
    body = resp.body
    if not isinstance(body, dict) or not body.get("number"):
        return None
    head = body.get("head") or {}
    base = body.get("base") or {}
    head_repo = (head.get("repo") or {}).get("full_name") or ""
    return PrMetadata(
        repo=repo,
        author=str((body.get("user") or {}).get("login") or ""),
        head_branch=str(head.get("ref") or ""),
        head_sha=str(head.get("sha") or ""),
        base_branch=str(base.get("ref") or ""),
        is_fork=bool(head_repo) and head_repo != repo,
        rate_remaining=resp.rate_remaining,
        rate_reset=resp.rate_reset,
    )


def _fetch_pr_metadata(pr: int, repo: str | None = None) -> PrMetadata | None:
    """作成者・head・base・head の commit を REST の 1 回で取る。

    リポジトリ名は渡された値、無ければ git の設定から求める。**その名前が誤って
    いれば応答が失敗するため、そのときだけ `gh repo view` で解決し直す。**
    確かめる手段が同じ呼び出しに含まれるので、追加の消費なしで誤りを塞げる。
    """
    tried: list[str] = []
    for candidate in (repo, _repo_from_git()):
        if not candidate or candidate in tried:
            continue
        tried.append(candidate)
        resp = _gh_rest(f"repos/{candidate}/pulls/{int(pr)}")
        if resp is None:
            continue
        meta = _pr_metadata_of(candidate, resp)
        if meta is not None:
            return meta
    resolved = _sh(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"],
        check=False,
    )
    if not resolved or resolved in tried:
        return None
    resp = _gh_rest(f"repos/{resolved}/pulls/{int(pr)}")
    return _pr_metadata_of(resolved, resp) if resp is not None else None


# 継続的統合の照会は `commits/{sha}/check-runs` の 1 回だけにする。
# **併記された状態（`commits/{sha}/status`）は使わない。** GitHub Actions は検査ジョブを
# 記録し commit の状態を記録しないため、9 件すべてが成功した commit でも
# `state: "pending"` / `total_count: 0` を返す（実測）。保留として読むと、承認された
# ラウンドが収束しなくなる。

# 失敗した検査ジョブの名前の振り分け。**一覧に無い名前は code-related へ倒す。**
CI_CODE_PATTERNS = ("pint", "larastan", "phpstan", "test", "lint", "type",
                    "build", "ruff", "eslint", "tsc", "mypy")
CI_META_PATTERNS = ("check_pr_requirements", "assignees", "reviewers", "labels", "meta")
# メタ検査の名前は**語として**一致したときだけ meta-only にする。部分一致で拾うと
# `metabase tests` や `metadata lint` のようなコード検査まで meta-only になり、失敗した
# まま収束する。前後が英数字でないことを求めるため、区切り（空白・`_`・`-`・`/`）で
# 挟まれた語だけが一致する。**一覧に無い名前を code-related へ倒す既定は変わらない。**
_CI_META_RE = re.compile(
    "(?<![0-9a-z])(?:" + "|".join(re.escape(p) for p in CI_META_PATTERNS) + ")(?![0-9a-z])")
# 完了した検査ジョブのうち、失敗として数える結論。`cancelled` / `skipped` / `neutral`
# は失敗にしない。
CI_FAILED_CONCLUSIONS = ("failure", "timed_out", "action_required", "startup_failure")

# 検査ジョブの一覧は 1 ページ 100 件（REST の上限）で読む。**既定の 30 件のままにしない。**
# 31 件目以降に code-related の失敗があるリポジトリでは、失敗を見ないまま収束する。
CHECK_RUNS_PER_PAGE = 100
# 読むページ数の上限。100 件で収まるリポジトリは 1 回のままである（このリポジトリは 9 件）。
# 上限に達しても止めず、読めた範囲で判定する。**進行を止めない側へ倒す。**
CHECK_RUNS_MAX_PAGES = 10


class CiClassification(NamedTuple):
    """検査ジョブを、修正の要る失敗・コードと無関係な失敗・未完了へ分けた結果。"""

    code_failed: list[str]
    meta_failed: list[str]
    pending: list[str]


def _classify_ci(runs: list[dict[str, Any]]) -> CiClassification:
    """検査ジョブを振り分ける。`cmd_judge` と `cmd_merge_fix` が同じ実装を呼ぶ。

    **`status` が `completed` 以外の検査ジョブは失敗にしない。** 完了を待たずに
    未完了として別に返し、呼び出し側が「未完了のまま収束した」ことを残す。
    """
    code_failed: list[str] = []
    meta_failed: list[str] = []
    pending: list[str] = []
    for run in runs:
        name = str(run.get("name") or "")
        if str(run.get("status") or "completed").lower() != "completed":
            pending.append(name)
            continue
        if str(run.get("conclusion") or "").lower() not in CI_FAILED_CONCLUSIONS:
            continue
        if _CI_META_RE.search(name.lower()):
            meta_failed.append(name)
        else:
            # 一覧に無い名前も含めて code-related へ倒す（保守的）。
            code_failed.append(name)
    return CiClassification(code_failed, meta_failed, pending)


def _fetch_check_runs(repo: str, sha: str) -> list[dict[str, Any]] | None:
    """head の commit に対する検査ジョブの一覧を返す。照会できなければ `None`。

    **「照会できなかった」と「すべて成功」を区別する。** `HTTP 422`（GitHub 側に
    無い commit）も `total_count` が 0 のリポジトリも、失敗が無いことの根拠に
    ならない。どちらも `None` を返し、呼び出し側は収束を止めずに理由を残す。

    **`total_count` に届くまでページを読む。** 1 ページの上限は 100 件で、
    `total_count` はページの件数ではなく全体の件数を返す。読み切らないまま
    `_classify_ci` へ渡すと、後ろのページにある失敗が無いものとして扱われる。
    100 件で収まるリポジトリは 1 回で終わり、呼び出し回数は変わらない。
    """
    if not repo or not sha:
        return None
    base = f"repos/{repo}/commits/{sha}/check-runs?per_page={CHECK_RUNS_PER_PAGE}"
    runs: list[dict[str, Any]] = []
    total: int | None = None
    for page in range(1, CHECK_RUNS_MAX_PAGES + 1):
        resp = _gh_rest(f"{base}&page={page}")
        if resp is None or not isinstance(resp.body, dict):
            return None
        if total is None:
            try:
                total = int(resp.body.get("total_count") or 0)
            except (TypeError, ValueError):
                return None
            if total <= 0:
                return None
        chunk = resp.body.get("check_runs")
        if not isinstance(chunk, list) or not chunk:
            break
        runs.extend(r for r in chunk if isinstance(r, dict))
        if len(runs) >= total:
            break
    return runs or None


class HeadRef(NamedTuple):
    """レビュー対象の Pull Request の head。

    比較の基準は `oid`（`gh pr view --json headRefOid` が返すコミット）で、
    `origin/<branch>` ではない。フォークの Pull Request では head branch が base の
    リポジトリに無いため、`origin/<branch>` は解決できない。基準を ref で持つと、
    未 push のコミットの検出も一致判定も、フォークのときだけ行えなくなる。
    """

    branch: str
    oid: str
    is_fork: bool


def _resolve_head_ref(pr: int, code: int = 8, repo: str | None = None) -> HeadRef:
    """その時点の head を GitHub から取り直す。

    状態ファイルの `head_branch` は `init` が書いた後に更新されない。`squash` の
    巻き直しは `<branch>-r<HHMMSS>` という新しいブランチを作るため、そのまま使うと
    **巻き直しの後に巻き直し前のブランチへ戻すことになる**。取れないときは
    状態ファイルの古い値へ落とさずに止める。落ちた先が誤っていては意味が無い。

    照会は REST の 1 回で、ブランチ名・commit・フォークの別が同じ応答から取れる。
    """
    meta = _fetch_pr_metadata(pr, repo)
    if meta is None:
        die(f"PR #{pr} の head を取得できない", code=code)
        raise SystemExit(code)  # die は戻らないが、型のために置く
    if not meta.head_branch or not meta.head_sha:
        die(f"PR #{pr} の head が空である", code=code)
    return HeadRef(branch=meta.head_branch, oid=meta.head_sha, is_fork=meta.is_fork)


def _fetch_head(worktree: str, pr: int, head: HeadRef) -> bool:
    """基準のコミットを手元へ取り込み、手元にあるかを返す。

    取り込みの宛先だけが Pull Request の種別で変わる。フォークは base のリポジトリ側の
    `refs/pull/<番号>/head` から引く。取り込みの後に確かめるのは、`gh pr view` と
    `git fetch` の間に head が動いていると、取り込んだ内容に基準が含まれないためである。
    """
    target = f"refs/pull/{pr}/head" if head.is_fork else head.branch
    subprocess.run(
        ["git", "fetch", "origin", target],
        capture_output=True, text=True, cwd=worktree,
    )
    have = subprocess.run(
        ["git", "cat-file", "-e", f"{head.oid}^{{commit}}"],
        capture_output=True, text=True, cwd=worktree,
    )
    return have.returncode == 0


def _sync_exclusions(worktree: str) -> list[str]:
    """掃除（`git clean`）から外すパスを返す。

    状態ファイルと結果ファイルは tmp ディレクトリにある。`.cross_review/` が
    `.gitignore` に載っているのはこのリポジトリの都合で、レビュー対象のリポジトリで
    載っている保証は無い。載っていなければ、ラウンドごとの掃除がそれらを消す。
    消えると次の読み込みで止まり、振動検知は前のラウンドの payload を失う。
    """
    names = [".cross_review"]
    env = os.environ.get("CROSS_REVIEW_TMP_DIR")
    if env:
        try:
            rel = pathlib.Path(env).resolve().relative_to(pathlib.Path(worktree).resolve())
        except ValueError:
            rel = None
        if rel is not None and str(rel) not in ("", ".") and str(rel) not in names:
            names.append(str(rel))
    return names


def _worktree_changes(
    worktree: str,
    exclusions: list[str],
    code: int = 8,
) -> tuple[list[str], list[str]]:
    """作業ツリーの変更を、追跡対象と追跡対象外に分けて返す。

    `git status --porcelain` は先頭 2 文字が状態、3 文字目が空白、4 文字目からがパスである。
    **行全体を strip しない。** 先頭が空白の状態（` M path` など）でパスが 1 文字ずれる。
    """
    r = subprocess.run(
        ["git", "status", "--porcelain"],
        capture_output=True, text=True, cwd=worktree,
    )
    if r.returncode != 0:
        die(f"作業ツリーの状態を読み取れない: {r.stderr.strip()[:200]}", code=code)
    tracked: list[str] = []
    untracked: list[str] = []
    for line in r.stdout.splitlines():
        if len(line) < 4:
            continue
        status, path = line[:2], line[3:]
        if status == "??":
            if any(path == e or path.startswith(f"{e}/") for e in exclusions):
                continue
            untracked.append(path)
        else:
            tracked.append(path)
    return tracked, untracked


def _is_synced(
    worktree: str,
    pr: int,
    head: HeadRef,
    exclusions: list[str],
    code: int,
) -> bool:
    """基準と突き合わせ、書き換えが要らないかを返す。失われるものがあるときは止める。

    **`strict=True` の経路からだけ呼ぶ。** 見つかる変更は、同じループの修正の工程が
    今まさに残したものである。捨てると修正そのものが失われ、しかも失われたことが
    誰にも見えない。
    """
    tracked, untracked = _worktree_changes(worktree, exclusions, code)
    if tracked:
        die(
            "作業ツリーに未 push の変更が残っています: "
            f"{' '.join(tracked[:10])}。"
            " 修正を push してから次のラウンドを開始してください",
            code=code,
        )
    ahead = subprocess.run(
        ["git", "rev-list", "--count", f"{head.oid}..HEAD"],
        capture_output=True, text=True, cwd=worktree,
    )
    if ahead.returncode != 0:
        die(
            f"基準 {head.oid[:7]} からの差を数えられない: {ahead.stderr.strip()[:200]}",
            code=code,
        )
    try:
        extra = int(ahead.stdout.strip() or "0")
    except ValueError:
        # 数えられない値を 0 と読むと、未 push のコミットを見落として捨てることになる。
        die(f"基準 {head.oid[:7]} からの差を読み取れない: {ahead.stdout.strip()[:80]}", code=code)
    if extra > 0:
        die(
            f"作業ツリーに PR #{pr} の head へ含まれないコミットが {extra} 件あります。"
            " push してから次のラウンドを開始してください",
            code=code,
        )
    current = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        capture_output=True, text=True, cwd=worktree,
    )
    if current.returncode != 0 or current.stdout.strip() != head.oid or untracked:
        return False
    info(f"✔ worktree は PR #{pr} の head と同期済み: {head.oid[:7]}")
    return True


def _sync_worktree(
    worktree: str,
    pr: int,
    head: str | HeadRef,
    *,
    strict: bool = False,
) -> None:
    """既存 worktree を PR の head へ同期する。

    worktree は使い捨ての領域だが、実際には前回の実行のものがそのまま残る。
    同期せずに流用すると、**レビュー担当は古い差分を読む**。指摘は現在の PR に
    存在しない行に対して出るか、直したはずの箇所へ再び出る。どちらも投稿されて
    しまうため、読む側からは見分けが付かない。

    前回の実行が残した追跡対象外のファイルも消す。残したまま fix 担当が
    `git add -A` を使うと、レビューと無関係なファイルが Pull Request へ混ざる。
    tmp ディレクトリは `-e` で除外するため、state.json と result.json は残る。

    `strict` は、失われるものの扱いと止めるときの終了コードを切り替える。

    | 観点 | `strict=False`（init / 再開） | `strict=True`（ラウンドの開始） |
    | --- | --- | --- |
    | head と一致していて変更が無い | 巻き戻して掃除する | 何もしない |
    | 追跡対象の変更 / 基準に無いコミット | 捨てる | 止める |
    | 基準を手元に持てない | `gh pr checkout --detach` へ落とす | 止める |
    | 止めるときの終了コード | 1 | 8 |

    `init` の側を強くしないのは、そこで見つかる残骸が**前回の実行のもの**だからである。
    ラウンドの開始時に見つかる変更は、**同じループの修正の工程が今まさに残したもの**で
    あり、意味が違う。
    """
    code = 8 if strict else 1
    exclusions = _sync_exclusions(worktree)
    if isinstance(head, HeadRef):
        have_base = _fetch_head(worktree, pr, head)
        target = head.oid
        label = head.branch
    else:
        # 旧来の呼び出し（ブランチ名だけを渡す経路）。基準は `origin/<branch>` になる。
        fetch = subprocess.run(
            ["git", "fetch", "origin", head],
            capture_output=True, text=True,
        )
        have_base = fetch.returncode == 0
        target = f"origin/{head}"
        label = head

    if have_base:
        if strict and isinstance(head, HeadRef) and _is_synced(
                worktree, pr, head, exclusions, code):
            return
        reset = subprocess.run(
            ["git", "reset", "--hard", target],
            capture_output=True, text=True, cwd=worktree,
        )
        if reset.returncode != 0:
            die(f"worktree を {target} へ同期できない: {reset.stderr.strip()}", code=code)
    elif strict:
        # HEAD を動かす前に、何が失われるかを数える材料が無い（基準が手元に無いのだから、
        # 未 push のコミットを数えられない）。判定できない状態でフォールバックしない。
        die(
            f"PR #{pr} の基準のコミット {target[:7]} を取り込めない。"
            " ネットワークか権限を確認してください",
            code=code,
        )
    else:
        # フォーク PR は origin に head branch が無い。作成時と同じ経路で合わせる。
        info(f"⚠ git fetch origin {label} 失敗 (フォーク PR の可能性) — gh pr checkout でフォールバック")
        checkout = subprocess.run(
            ["gh", "pr", "checkout", str(pr), "--detach"],
            capture_output=True, text=True, cwd=worktree,
        )
        if checkout.returncode != 0:
            die(f"gh pr checkout --detach #{pr} 失敗: {checkout.stderr.strip()}", code=code)
    clean = subprocess.run(
        ["git", "clean", "-fd", *[a for e in exclusions for a in ("-e", e)]],
        capture_output=True, text=True, cwd=worktree,
    )
    if clean.returncode != 0:
        # 消せないまま進むと、残骸を抱えた作業ツリーで fix 担当が `git add -A` を
        # 使い、Pull Request へ混ざる。差分そのものは合っていても止める。
        die(f"追跡対象外のファイルを消せない: {clean.stderr.strip()}", code=code)
    rev = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True, text=True, cwd=worktree,
    )
    sha = rev.stdout.strip() if rev.returncode == 0 else "?"
    info(f"↻ 既存 worktree を PR #{pr} の head へ同期: {sha}")


def _tmp_dir(workspace: str | None = None) -> pathlib.Path:
    """cross-review 用 tmp ディレクトリを決定する。

    優先順位:
      1. 環境変数 `CROSS_REVIEW_TMP_DIR` (明示)
      2. `<workspace>/.cross_review/` (worktree 内。作業領域を 1 つに保つため)

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
        or name.startswith(".env")
        or name in {"dockerfile", "makefile", ".editorconfig"}
        or lower.startswith(".github/")
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
        or name in {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}
        or ext in INFRA_EXTENSIONS
        or lower.endswith(".tfvars.json")
    )


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

# ---------------- 待ち行列（#291） ----------------
#
# **GitHub が使えない間も収束ループを進める。** 上限に達したときだけ投稿する内容を
# ローカルへ積み、回復した後に順に流す。積む・流す・上限を見分けるところは共通層
# （`post_queue`）が持ち、ここが持つのは置き場所の解決と、流した直後の確認と、
# 収束の判定への結び付けだけである。
#
# **止めるのは `final = approved` を出すところだけである。** レビューも修正もローカルで
# 進み、判定も記録される。未反映のまま「両方が承認した」と記録しないことが、この課題で
# 守る一線である。


def _queue(pr: int) -> post_queue.Queue:
    """この Pull Request の待ち行列。状態ファイルと同じ親の下に置く。

    作業ツリーの中へ置くのは、巻き直しで作業ツリーを捨てるときに待ち行列も一緒に
    捨てられるようにするためである。**捨ててよいのは、巻き直しが close と create を
    積まないためである**（積む対象はその Pull Request 宛のコメントだけで、Pull Request
    ごと捨てるなら宛先も無くなる）。
    """
    return post_queue.Queue(_resolve_tmp_dir(pr) / post_queue.QUEUE_DIRNAME)


def _pending_posts(pr: int) -> int:
    """まだ届いていない投稿の件数。**照会は行わない**（ファイルを数えるだけ）。"""
    return _queue(pr).count()


def _confirm_flushed(pr: int, item: dict[str, Any]) -> None:
    """流した直後に、投稿が届いたことを 1 度だけ確かめる。

    #261 は「投稿が届いたことを確かめてから収束する」ことを求めている。待ち行列は
    確かめる時点を投稿の直後から**流した直後**へ移すだけで、決まりそのものは変えない。
    """
    if item.get("kind") != "review-post":
        return
    extra = item.get("extra") or {}
    agent, round_no = extra.get("agent"), extra.get("round")
    if not (agent and round_no):
        return
    resp = item.get("response") if isinstance(item.get("response"), dict) else {}
    url = str(resp.get("html_url") or "")
    if not url and resp.get("id"):
        url = f"#pullrequestreview-{resp['id']}"
    st = _load(pr)
    # **書き戻す先は、その項目が属するラウンドである。** 積んだラウンドと流した
    # ラウンドが同じとは限らないため、最後のラウンドへ書かない。
    target = next(
        (e for e in st.get("rounds", [])
         if e.get("round") == round_no and isinstance(e.get(agent), dict)),
        None,
    )
    if target is None:
        return
    exists = _review_exists(str(st.get("repo") or ""),
                            int(st.get("current_pr") or pr), url)
    if exists is False:
        # #261 の決まり。届いていない投稿は結果なしとして扱い、起動し直しの経路へ乗せる。
        target[agent] = {
            "intent": NO_RESULT,
            "no_result_reason": "not_posted",
            "posted_as": None,
            "comments": None,
            "review_url": None,
            "by_severity": {},
        }
        info(
            f"⚠ {agent}: 流した後も投稿を確認できません (review_url={url!r})。"
            " 結果なしとして記録します"
        )
    else:
        target[agent]["review_url"] = url
        target[agent]["queued"] = False
    _save(pr, st)


def _auto_flush(pr: int) -> None:
    """進行側の各コマンドの入口で待ち行列を流す。

    **自動だけにも明示だけにもしない。** 自動だけだと、回復を待つあいだ何もコマンドを
    実行していない場合に流れない。明示だけだと、進行側が忘れたときに待ち行列が残った
    まま収束の判定へ進む。流せなくても工程は止めない。
    """
    q = _queue(pr)
    if not q.count():
        return
    result = q.flush()
    for item in result.sent:
        _confirm_flushed(pr, item)
    if result.sent or result.skipped:
        info(
            f"↻ 待ち行列を流しました: 送った {len(result.sent)} 件 /"
            f" 既に届いていた {len(result.skipped)} 件"
        )
    if result.remaining:
        reason = (result.failed or {}).get("last_error", "")
        info(f"⏳ 待ち行列に {result.remaining} 件残っています: {reason}")


def cmd_flush(args: argparse.Namespace) -> None:
    """待ち行列に積んだ投稿を流す。**終了コードは常に 0**（工程を止めない）。"""
    pr = args.pr
    q = _queue(pr)
    before = q.count()
    result = q.flush()
    for item in result.sent:
        _confirm_flushed(pr, item)
    print(f"PENDING_BEFORE={before}")
    print(f"PENDING_SENT={len(result.sent)}")
    print(f"PENDING_SKIPPED={len(result.skipped)}")
    print(f"PENDING_REMAINING={result.remaining}")
    if result.remaining:
        info(
            f"⏳ 待ち行列に {result.remaining} 件残っています"
            f"{'（まだ上限です）' if result.rate_limited else ''}"
        )
    else:
        info(f"✅ 待ち行列は空です（送った {len(result.sent)} 件）")


def cmd_init(args: argparse.Namespace) -> None:
    """Step 0 — state 初期化 or 既存 state 引き継ぎ + プリチェック。"""
    pr = args.pr
    manual_extra_review = _extra_review_instructions(args)
    # worktree path を先に解決してから tmp_dir を決定する。
    # tmp_dir は <worktree>/.cross_review/ に配置し、作業領域を 1 つに保つ。
    # path には repo slug を含め、他リポジトリの同一 PR 番号と衝突しないようにする。
    # リポジトリ名は git の設定から求める。GraphQL を 1 点使わずに済み、誤りは
    # この後の REST の応答が検証する（`_fetch_pr_metadata`）。
    repo = _repo_from_git() or _sh(
        ["gh", "repo", "view", "--json", "nameWithOwner", "-q", ".nameWithOwner"])
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
            # 再開した時点で残っている未解決の指摘を引き継ぎとして記録する。
            if _record_carried_over(st, st.get("repo") or repo, st.get("current_pr") or pr):
                state_changed = True
            # 待ち行列は状態ファイルより先に読む。再開の入口で流しておくと、
            # 回復した後の 1 本目のコマンドで届く。
            _auto_flush(int(st.get("current_pr") or pr))
            if state_changed:
                resume_state_file.write_text(
                    json.dumps(st, indent=2, ensure_ascii=False),
                    encoding="utf-8",
                )
                info("↻ 追加レビュー観点を state に反映して再開")
            tmp_dir = _tmp_dir(worktree)
            wt = st.get("worktree_path") or ""
            # 再開でも同期する。中断から再開までの間に head が進んでいることがあり、
            # そのまま次のラウンドを回すと古い差分をレビューさせる。
            resume_head = str(st.get("head_branch") or "")
            if wt and resume_head and _is_registered_worktree(str(wt)):
                _sync_worktree(str(wt), int(st.get("current_pr") or pr), resume_head)
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
            print(f"CARRIED_OVER_THREADS={(st.get('carried_over') or {}).get('count', 0)}")
            print(f"RESUMED=1")
            return

    # 新規 init: プリチェック。
    # **作成者・head・base は REST の 1 回でまとめて取る。** 項目ごとに `gh pr view` を
    # 投げていた分（GraphQL 3 点）と、リポジトリ名の解決（同 1 点）が 0 点になる。
    meta = _fetch_pr_metadata(pr, repo)
    if meta is None:
        die(f"PR #{pr} のメタデータを取得できません（リポジトリ名: {repo}）")
        return
    if meta.repo != repo:
        repo = meta.repo
        if not args.worktree:
            worktree = str(_default_worktree_base() / _repo_slug(repo) / f"pr{pr}")
    if meta.rate_remaining is not None:
        info(f"ℹ GitHub REST の残量: {meta.rate_remaining}")

    me = _sh(["gh", "api", "user", "--jq", ".login"])
    author = meta.author
    is_own = (me == author)
    event_downgrade = is_own
    if is_own:
        info(f"⚠ 自分の PR (author={me}) — REQUEST_CHANGES → COMMENT 強制ダウングレード")

    # worktree 分離 — _tmp_dir() より先に worktree を作成/確認する
    head_branch = meta.head_branch
    base_branch = meta.base_branch
    changed_files = _fetch_changed_files(pr, repo)
    auto_review_categories = _classify_changed_files(changed_files)
    auto_review = _auto_review_instructions(auto_review_categories)
    review_instructions = _combined_review_instructions(auto_review, manual_extra_review)
    if not pathlib.Path(worktree).exists():
        _create_worktree(worktree, pr, head_branch)
    elif _is_registered_worktree(worktree):
        info(f"↻ 既存 worktree 流用: {worktree}")
        _sync_worktree(worktree, pr, head_branch)
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
        # 自分のログイン名は変わらない値である。一度取って持ち、以降は読まない。
        # 待ち行列の冪等の照合が「投稿者が自分か」を見るために使う。
        "viewer_login": me,
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
        # 引き継いだ指摘は再開の時点で決まる。新規の開始では空にする。
        "carried_over": None,
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
    print("CARRIED_OVER_THREADS=0")
    print("RESUMED=0")


AGENTS = ("codex", "agy")

NO_RESULT = "NO_RESULT"


def _is_pass(intent: str | None, severity: dict[str, int] | None) -> bool:
    """1 レビュアー分の判定が pass かどうか。

    pass は `APPROVE` と、重大な指摘が無い `COMMENT` だけである。指定によるスキップ
    （`--only`）は `_round_passes` が短絡して扱うため、ここへは届かない。結果を残さな
    かったレビュアー（`NO_RESULT`）は pass にしない。レビューが行われていないのに、
    行われて承認されたのと同じ出口へ進むためである。
    """
    if intent == "APPROVE":
        return True
    if intent == "COMMENT":
        sev = severity or {}
        return sev.get("critical", 0) == 0 and sev.get("major", 0) == 0
    return False


def _skipped_by_only(agent: str, only: str | None) -> bool:
    """`--only` の指定でそのレビュアーを起動しなかったかどうか。"""
    return bool(only) and only != agent


def _agent_intent(round_entry: dict[str, Any], agent: str, only: str | None) -> str:
    """そのラウンドに記録されたレビュアーの判定を読む。

    記録が無いときの既定値は、指定によるスキップなら `SKIP`、そうでなければ
    `NO_RESULT` になる。**この 2 つは別の事象である。**
    """
    entry = round_entry.get(agent) or {}
    default = "SKIP" if _skipped_by_only(agent, only) else NO_RESULT
    return entry.get("intent") or default


def _no_result_agents(round_entry: dict[str, Any], only: str | None) -> list[str]:
    """そのラウンドで、起動したのに使える結果が残らなかったレビュアーを返す。"""
    return [
        a
        for a in AGENTS
        if not _skipped_by_only(a, only) and _agent_intent(round_entry, a, only) == NO_RESULT
    ]


def _round_passes(round_entry: dict[str, Any], only: str | None) -> bool:
    """そのラウンドで新しく投稿された指摘だけを見た pass 判定。

    引き継いだ指摘はここでは見ない（`cmd_judge` が別に扱う）。
    """
    for agent in AGENTS:
        if _skipped_by_only(agent, only):
            continue
        entry = round_entry.get(agent) or {}
        if not _is_pass(_agent_intent(round_entry, agent, only), entry.get("by_severity")):
            return False
    return True


def _guard_previous_round(st: dict[str, Any], prev: dict[str, Any]) -> None:
    """前のラウンドの後始末が終わっているかを確かめる。

    進行側が手で修正して次のラウンドへ進めると、修正の工程（Step 5）が担う返信と
    Resolve が飛ばされる。飛ばされたまま進むと、未解決の指摘が残ったまま承認へ到達する。

    止めるのは次の 2 つ。

    1. 前のラウンドが修正必須の判定なのに、修正の記録が無い
    2. 前のラウンドで Resolve したと申告されたスレッドが、GitHub 側で未解決のまま

    未解決の指摘を取得できないときは検査を行わず、確認できなかったことを残して進む。
    取得の失敗で止めると、GitHub 側の一時的な不調でループが進まなくなる。

    スレッドの状態は、申告が行われた Pull Request（`prev["pr"]`）へ問い合わせる。
    ローテーションを挟んだラウンドでは Step 6 の `set-current-pr` が先に走るため、
    `current_pr` は既に新しい Pull Request を指している。そちらへ問い合わせると、
    旧 Pull Request のスレッドが未解決のままでも一覧に現れず検査が素通りする。
    """
    round_no = prev.get("round")
    verdict = prev.get("verdict")
    if verdict is None:
        # 判定の結果を持たない古い状態ファイルは、保存された重要度から判定し直す。
        # 項目が欠けたラウンドは結果なしであり、修正の記録を求める対象ではない。
        if _no_result_agents(prev, st.get("only")):
            verdict = "no_result"
        else:
            verdict = "approved" if _round_passes(prev, st.get("only")) else "changes_requested"
    fix = prev.get("fix")
    if verdict == "changes_requested" and not fix:
        die(
            f"round {round_no} は修正必須の判定でしたが、修正の記録がありません。"
            " 返信と Resolve が飛ばされている可能性があります。"
            " `/ndf:fix` を実行して戻り値ファイルを作り、`merge-fix` を通してから"
            " 次のラウンドを開始してください",
            code=5,
        )

    claimed = (fix or {}).get("resolved_thread_ids") or []
    if not claimed:
        return
    claimed_pr = int(prev.get("pr") or st.get("current_pr") or 0)
    threads = _fetch_unresolved_threads(str(st.get("repo") or ""), claimed_pr)
    if threads is None:
        info(
            f"⚠ round {round_no} で Resolve したと申告されたスレッドの状態を確認できません"
            " — 検査を飛ばして続行します"
        )
        return
    open_ids = {t["id"] for t in threads}
    still_open = [i for i in claimed if i in open_ids]
    if still_open:
        die(
            f"round {round_no} で Resolve したと申告されたスレッドが未解決のまま残っています: "
            f"{' '.join(still_open)}。返信と Resolve を済ませてから次のラウンドを開始してください",
            code=5,
        )


def _sync_before_round(st: dict[str, Any], pr: int) -> HeadRef | None:
    """ラウンドを開く前に、レビュー用の作業ツリーを Pull Request の head へ揃える。

    同期は作られるときと再開するときにしか行われていなかった。修正を作業ツリーの外で
    行って push すると、次のラウンドは 1 つ前の内容をレビューする。**どの経路で push
    されても同じ差分がレビューされる状態にする**（#217）。

    **ラウンドのエントリを開く前に行う。** 途中で止まったときにラウンドが半端に開かれず、
    原因を取り除いた後に同じラウンド番号から再開できる。

    「同期の対象が無い」と「同期できない」は分けて扱う。作業ツリーが失われている状態は
    この後の `launch-codex.sh` / `launch-agy.sh` が表に出すため、ここで止めても
    分かることは増えない。
    """
    wt = str(st.get("worktree_path") or "")
    if not wt:
        info("⚠ state に worktree_path が無い — 作業ツリーの同期を飛ばして続行します")
        return None
    if not _is_registered_worktree(wt):
        info(f"⚠ 登録済みの作業ツリーではない ({wt}) — 同期を飛ばして続行します")
        return None
    head = _resolve_head_ref(pr, repo=str(st.get("repo") or "") or None)
    _sync_worktree(wt, pr, head, strict=True)
    # 解決したブランチ名を書き戻す。巻き直しの後は state の値が古いため、再開の経路も
    # ここで書かれた値を読む。保存はこの後の round エントリの追加と同じ書き込みで済む。
    st["head_branch"] = head.branch
    return head


def cmd_start_round(args: argparse.Namespace) -> None:
    """Step 1 — round 開始判定。"""
    _auto_flush(args.pr)
    st = _load(args.pr)
    total = len(st["rounds"])
    max_r = st["max_rounds"]
    if total >= max_r:
        st["final"] = "max_rounds"
        st["ended_at"] = _now()
        _save(args.pr, st)
        die(f"max_rounds={max_r} 到達。中断。", code=1)
    # 上限に達していれば、そこでループが終わる。後始末の検査はその後で意味を持たない。
    if total > 0:
        _guard_previous_round(st, st["rounds"][-1])

    pr = st["current_pr"]
    head = _sync_before_round(st, pr)

    round_no = total + 1
    round_in_pr = sum(1 for r in st["rounds"] if r["pr"] == pr) + 1

    # round エントリを開く。head の commit を記録するのは、起動スクリプト 2 本と
    # 収束の判定が同じ値を読むためである。**2 本が同じ値を別々に取っていた分が 0 になる。**
    entry: dict[str, Any] = {
        "round": round_no,
        "pr": pr,
        "started_at": _now(),
    }
    if head is not None:
        entry["head_sha"] = head.oid
    st["rounds"].append(entry)
    _save(args.pr, st)

    info(f"=== Round {round_no} / {max_r} (PR #{pr}, round_in_pr={round_in_pr}) ===")
    print(f"ROUND={round_no}")
    print(f"ROUND_IN_PR={round_in_pr}")
    print(f"PR={pr}")
    print(f"MAX_ROUNDS={max_r}")
    print(f"ROTATE_AFTER={st['rotate_after']}")


def _as_count(value: object) -> int:
    """申告された件数を整数として読む。読めない値は 0 として扱う。

    相手は LLM なので、文字列や `null` が入ることがある。読めない申告を
    「件数あり」と見なすと、突き合わせる相手が決まらないまま中断してしまう。
    """
    try:
        return max(0, int(value))  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0


def _posted_comment_count(repo: str, pr: int, review_url: str | None) -> int | None:
    """レビューに実際にぶら下がっているインラインコメントの数。

    取得できなければ `None` を返す。**「取得できなかった」と「0 件」を区別する。**
    取得の失敗で中断すると、GitHub 側の一時的な不調でループが止まる。

    投稿は AI 自身が `gh api` で行うため、失敗しても結果ファイルの申告だけは残る。
    数え直す先は、申告された `review_url` の末尾にある識別子から決める。
    """
    if not repo or not review_url:
        return None
    m = re.search(r"pullrequestreview-(\d+)", str(review_url))
    if not m:
        return None
    try:
        out = _sh(
            ["gh", "api", f"repos/{repo}/pulls/{pr}/reviews/{m.group(1)}/comments",
             "--paginate", "--jq", "length"],
            check=False,
        )
    except Exception:
        return None
    counts = [int(line) for line in str(out).split() if line.strip().isdigit()]
    return sum(counts) if counts else None


# Pull Request 上の未解決の指摘（Resolve されていない review thread）を数えるための問い合わせ。
# `--paginate` に載せるため、カーソルと `pageInfo` を持たせる。
_UNRESOLVED_THREADS_QUERY = """
query($owner: String!, $name: String!, $pr: Int!, $endCursor: String) {
  repository(owner: $owner, name: $name) {
    pullRequest(number: $pr) {
      reviewThreads(first: 100, after: $endCursor) {
        pageInfo { hasNextPage endCursor }
        nodes { id isResolved path line }
      }
    }
  }
}
"""

_UNRESOLVED_THREADS_JQ = (
    ".data.repository.pullRequest.reviewThreads.nodes[]"
    " | select(.isResolved == false)"
    ' | [.id, (.path // ""), (.line // "" | tostring)] | @tsv'
)


def _review_exists(repo: str, pr: int, review_url: str | None) -> bool | None:
    """申告された `review_url` の指すレビューが GitHub 側にあるか。

    取得できなければ `None` を返す。**「取得できなかった」と「無い」を区別する。**
    取得の失敗で中断すると、GitHub 側の一時的な不調でループが止まる。

    投稿は AI 自身が `gh api` で行うため、失敗しても結果ファイルには判定が残る。
    判定だけを採ると、修正の担当が読むべき指摘が Pull Request に無いまま修正の工程が
    起動する（実測: `review_url` が空、重要度別の件数もすべて 0）。
    """
    if not repo or not review_url:
        return False
    m = re.search(r"pullrequestreview-(\d+)", str(review_url))
    if not m:
        return False
    try:
        out = _sh(
            ["gh", "api", f"repos/{repo}/pulls/{pr}/reviews/{m.group(1)}", "--jq", ".id"],
            check=False,
        )
    except Exception:
        return None
    text = str(out).strip()
    if not text:
        return None
    return text.split()[0] == m.group(1)


def _gh_output(cmd: list[str]) -> str | None:
    """`gh` を実行して標準出力を返す。実行に失敗したときは `None` を返す。

    **「取得できなかった」と「0 件」を区別する。** 失敗を空の出力として返すと、
    GitHub 側の一時的な不調が「未解決の指摘は無い」と読まれてしまう。
    """
    try:
        r = subprocess.run(cmd, capture_output=True, text=True)
    except OSError as exc:
        info(f"⚠ gh の実行に失敗: {exc}")
        return None
    if r.returncode != 0:
        info(f"⚠ gh が失敗 (exit={r.returncode}): {r.stderr.strip()[:200]}")
        return None
    return r.stdout


def _fetch_unresolved_threads(repo: str, pr: int) -> list[dict[str, Any]] | None:
    """Pull Request 上の未解決の指摘を GraphQL で数え、識別子つきで返す。

    **投稿数とは別のものを数えている。** 投稿数はそのラウンドで外部の AI が新しく
    投稿した件数で、ここで数えるのは前のラウンドの分も含む Pull Request 上の総数である。

    Returns:
      未解決の指摘の一覧（`{"id", "path", "line"}`）。0 件なら空の一覧。
      取得できなければ `None`。
    """
    owner, sep, name = str(repo or "").partition("/")
    if not (owner and sep and name):
        return None
    out = _gh_output([
        "gh", "api", "graphql", "--paginate",
        "-F", f"owner={owner}", "-F", f"name={name}", "-F", f"pr={int(pr)}",
        "-f", f"query={_UNRESOLVED_THREADS_QUERY}",
        "--jq", _UNRESOLVED_THREADS_JQ,
    ])
    if out is None:
        return None
    threads: list[dict[str, Any]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        cols = line.split("\t")
        threads.append({
            "id": cols[0],
            "path": cols[1] if len(cols) > 1 else "",
            "line": cols[2] if len(cols) > 2 else "",
        })
    return threads


def _thread_ids(value: Any) -> list[str]:
    """fix の戻り値から、Resolve したと申告されたスレッドの識別子を取り出す。

    正は dict の list だが、件数(int) や単一 dict で返ることがある。識別子を
    取り出せない形は空の一覧として扱い、後段の検査を行わない。
    """
    items = value if isinstance(value, list) else [value] if isinstance(value, dict) else []
    return [
        str(d["thread_id"]) for d in items
        if isinstance(d, dict) and d.get("thread_id")
    ]


def _record_carried_over(st: dict[str, Any], repo: str, pr: int) -> bool:
    """再開した時点で残っている未解決の指摘を「引き継いだ指摘」として記録する。

    記録があり、修正の工程を通したラウンドが未記録のあいだは収束させない
    （`cmd_judge`）。中断の前に受けた修正必須の指摘が、修正の工程を 1 度も
    通らないまま収束する経路を塞ぐ。

    取得できなかったときは記録を変更しない。0 件として扱うと、GitHub 側の
    一時的な不調で引き継ぎが消える。

    修正の工程を 1 度通した後も、deferred / rejected と最終スイープ待ちの指摘は
    Resolve されないまま残る。これを再開のたびに未処理として数え直すと、収束は
    再開のたびに 1 ラウンドずつ先送りされる。**通した後に新しい指摘が出ていない
    あいだは、通したラウンドの記録をそのまま残す**。新しい指摘が出たときだけ、
    それを含めて数え直し、もう 1 度修正の工程へ通す。

    Returns:
      記録を書き換えたかどうか。
    """
    before = st.get("carried_over")
    threads = _fetch_unresolved_threads(str(repo or ""), int(pr))
    if threads is None:
        info("⚠ 未解決の指摘を取得できませんでした — 引き継ぎの記録は変更しません")
        return False
    if not threads:
        st["carried_over"] = None
        return before is not None
    prev = before if isinstance(before, dict) else {}
    prev_fixed = prev.get("fixed_in_round")
    prev_ids = set(prev.get("thread_ids") or [])
    ids = [t["id"] for t in threads]
    new_ids = [i for i in ids if i not in prev_ids]
    if prev_fixed is not None and not new_ids:
        info(
            f"↻ 残っている {len(ids)} 件は round {prev_fixed} の修正の工程を通した後の"
            " 分です — 収束は抑止しません（最終スイープが受け持ちます）"
        )
        return False
    st["carried_over"] = {
        "detected_at": _now(),
        "count": len(ids),
        "thread_ids": ids,
        "fixed_in_round": None,
    }
    info(
        f"⚠ 引き継いだ指摘が {len(ids)} 件残っています"
        " — 修正の工程を 1 度通すまで収束させません"
    )
    return True


def _carried_over_pending(st: dict[str, Any]) -> dict[str, Any] | None:
    """修正の工程をまだ通していない引き継いだ指摘があれば、その記録を返す。"""
    carried = st.get("carried_over")
    if not isinstance(carried, dict):
        return None
    if not (carried.get("thread_ids") or carried.get("count")):
        return None
    if carried.get("fixed_in_round") is not None:
        return None
    return carried


def cmd_unresolved_threads(args: argparse.Namespace) -> None:
    """Pull Request 上の未解決の指摘を数えて出力する。

    Exit code: 0=数えられた（0 件を含む）, 1=取得できなかった
    """
    st = _load(args.pr)
    repo = str(st.get("repo") or "")
    pr = int(st.get("current_pr") or args.pr)
    threads = _fetch_unresolved_threads(repo, pr)
    if threads is None:
        die(
            f"未解決の指摘を取得できませんでした (repo={repo or '不明'}, PR #{pr})。"
            " 0 件として扱わず、取得し直してください"
        )
    print(f"UNRESOLVED_COUNT={len(threads)}")
    print(f"UNRESOLVED_THREAD_IDS={shlex.quote(' '.join(t['id'] for t in threads))}")
    for t in threads:
        info(f"- {t['id']} {t.get('path', '')}:{t.get('line', '')}")


def _record_no_result(pr: int, agent: str, reason: str) -> None:
    """使える結果が残らなかったことを、そのラウンドへ残す。

    判定（`cmd_judge`）はこの記録を読んで、起動し直しか中断かを決める。記録が無い
    ラウンドも結果なしとして読むため、ここで書けなかった場合も収束はしない。

    状態ファイルを読めないときとラウンドがまだ無いときは、何も書かずに戻る。呼び出し
    元はこの直後に die するため、ここで新たに止める理由が無い。
    """
    path = _state_path(pr)
    if not path.exists():
        return
    try:
        st = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(st, dict) or not st.get("rounds"):
        return
    st["rounds"][-1][agent] = {
        "intent": NO_RESULT,
        "no_result_reason": reason,
        "posted_as": None,
        "comments": None,
        "review_url": None,
        "by_severity": {},
    }
    _save(pr, st)


def _die_no_result(pr: int, agent: str, reason: str, msg: str, code: int = 1) -> None:
    """結果なしをラウンドへ残してから止める。終了コードは現行のまま変えない。"""
    _record_no_result(pr, agent, reason)
    die(msg, code=code)


def cmd_read_result(args: argparse.Namespace) -> None:
    """Step 2.5 — codex/agy の result.json を state にマージ。

    使える結果が残らなかったときは、`NO_RESULT` と理由をラウンドへ残してから止める。
    終了コードは現行のまま（無い・判定の値を持たないときは 1、JSON として読めない
    ときは 3）で、進む先を決めるのは次の判定である。
    """
    agent = args.agent
    pr = args.pr
    _auto_flush(pr)
    rfile = pathlib.Path(args.file or _resolve_tmp_dir(pr) / f"{agent}-review-pr{pr}-result.json")
    if not rfile.exists() or rfile.stat().st_size == 0:
        _die_no_result(pr, agent, "missing", f"{agent}: result 未生成 ({rfile})")

    try:
        r = json.loads(rfile.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        _die_no_result(
            pr,
            agent,
            "unparsable",
            f"{agent}: result.json の parse に失敗 ({rfile}): {exc}",
            code=3,
        )

    # gemini round 4 指摘: result.json は本来 dict だが、launcher の出力バグや
    # 別実行の残骸で list / str が入り込むと `r.get(...)` で AttributeError になる。
    # 不正な review result はバグなので即時 die(code=3) で停止させる。
    if not isinstance(r, dict):
        _die_no_result(
            pr,
            agent,
            "unparsable",
            f"{agent}: result.json が dict ではない "
            f"({rfile}, type={type(r).__name__})。review launcher の出力形式不正。",
            code=3,
        )

    # 別名フィールドへのフォールバック (`intent` / `comment_count` を使う変則 JSON を
    # 書き出す既知のケースに対応する。仕様としては `event` / `comments_count` が正)
    intent = r.get("event") or r.get("intent")
    posted_as = r.get("posted_as") or intent
    comments = r.get("comments_count")
    if comments is None:
        comments = r.get("comment_count")

    if intent is None:
        _die_no_result(
            pr,
            agent,
            "no_verdict",
            f"{agent}: result.json に event / intent フィールドが無い ({rfile})。"
            " launcher prompt のスキーマ違反の可能性。",
        )

    st = _load(pr)
    if not st.get("rounds"):
        die(f"{agent}: state.rounds が空。`state.py start-round` を先に呼んでください")

    repo = str(st.get("repo") or "")

    # **投稿が届いたかを先に確かめる。** 判定だけが残り、指摘の中身が Pull Request に
    # 無いまま修正の工程へ進む経路を塞ぐ（#261）。届いていないときは結果なしとして
    # 記録し、判定の側の「同じラウンドで 1 度だけ起動し直す」経路へ乗せる。修正の担当
    # から見ると、結果が残らなかった場合と、結果はあるが指摘が届いていない場合は同じ
    # 状態である（読むべき指摘が無い）。
    # **待ち行列へ積んだ投稿は、積んだ時点では届いていない。** ここで照会すると
    # 結果なしになり、起動し直しで同じ内容が二重に積まれる。届いたことは流した直後に
    # 1 度だけ確かめる（`_confirm_flushed`）。
    queued = bool(r.get("queued"))
    if queued:
        info(
            f"⚠ {agent}: 投稿を待ち行列へ積んでいます。"
            "届いたことの確認は流した直後に行います"
        )
    post_error = None if queued else r.get("post_error")
    if post_error:
        _die_no_result(
            pr,
            agent,
            "not_posted",
            f"{agent}: レビューの投稿に失敗しています (post_error={post_error})。"
            " 指摘が Pull Request に届いていないため、結果なしとして扱います",
        )
    exists = None if queued else _review_exists(repo, pr, r.get("review_url"))
    if exists is False:
        _die_no_result(
            pr,
            agent,
            "not_posted",
            f"{agent}: 投稿されたレビューを確認できません "
            f"(review_url={r.get('review_url')!r})。"
            " 指摘が Pull Request に届いていないため、結果なしとして扱います",
        )
    if exists is None and not queued:
        info(
            f"⚠ {agent}: レビューの投稿を確認できませんでした。"
            "申告をそのまま採用します"
        )

    # **申告を GitHub 側と突き合わせる。** 投稿は AI 自身が行うので、失敗しても
    # 結果ファイルには件数が残る。申告のまま進むと、修正担当が読むべき指摘が
    # GitHub 上に存在しないまま収束判定まで走る（実測: 申告 2 件に対しスレッド 0）。
    declared = _as_count(comments)
    if declared > 0 and not queued:
        actual = _posted_comment_count(repo, pr, r.get("review_url"))
        if actual is None:
            info(
                f"⚠ {agent}: 投稿されたコメント数を確認できませんでした。"
                f"申告（{declared} 件）をそのまま採用します"
            )
        elif actual < declared:
            die(
                f"{agent}: インラインコメントの申告 {declared} 件に対し、"
                f"GitHub 上には {actual} 件しかありません。投稿が届いていないため"
                "中断します。レビューを投稿し直してから再実行してください"
            )

    st["rounds"][-1][agent] = {
        "intent": intent,
        "posted_as": posted_as,
        "comments": comments,
        "review_url": r.get("review_url"),
        "by_severity": r.get("by_severity", {}),
        "queued": queued,
    }
    _save(pr, st)
    info(f"✅ {agent}: intent={intent} posted_as={posted_as} comments={comments}")


def _round_ci(st: dict[str, Any], last: dict[str, Any], pr: int) -> dict[str, Any]:
    """収束の直前に検査ジョブを 1 度だけ照会し、判定に使う記録を返す。

    head の commit は `rounds[-1].head_sha` から読む。承認したレビューが読んだ commit と
    同じ値であり、追加の呼び出しが要らない。値が無いときだけ REST を 1 回投げる。

    **照会できないことは、承認されたラウンドを差し戻す理由にならない。** `gh` の失敗・
    `HTTP 422`・検査ジョブ 0 件はいずれも `unverified` として収束させ、確かめられ
    なかったことを記録に残す。
    """
    repo = str(st.get("repo") or "")
    sha = str(last.get("head_sha") or "")
    if not sha:
        meta = _fetch_pr_metadata(pr, repo or None)
        if meta is not None:
            sha = meta.head_sha
            repo = repo or meta.repo
    if not repo or not sha:
        return {"verdict": "unverified", "reason": "head のコミットを特定できない"}
    runs = _fetch_check_runs(repo, sha)
    if runs is None:
        return {
            "verdict": "unverified",
            "reason": "検査ジョブを照会できない（未 push・権限・検査ジョブ 0 件のいずれか）",
            "sha": sha,
        }
    c = _classify_ci(runs)
    if c.code_failed:
        return {"verdict": "code_failure", "sha": sha, "failed": c.code_failed,
                "meta_failed": c.meta_failed, "pending": c.pending}
    if c.meta_failed:
        return {"verdict": "meta_only", "sha": sha, "meta_failed": c.meta_failed,
                "pending": c.pending,
                "note": f"メタチェックのみ失敗: {c.meta_failed} — コードと無関係のため収束"}
    if c.pending:
        return {"verdict": "pending", "sha": sha, "pending": c.pending}
    return {"verdict": "success", "sha": sha}


def cmd_judge(args: argparse.Namespace) -> None:
    """Step 3 — intent ベース pass 判定。

    出口は 5 つある。**結果を取り込めていないラウンドは、収束も修正も決められない。**
    そのため結果なしの検査を、通ったかどうかの判定より先に置く。

    収束の枝に入る直前で、継続的統合の検査ジョブを 1 度だけ照会する（#327）。
    code-related の失敗があれば**中断せず**終了コード 2 で修正のラウンドへ回す。
    収束の直前は修正の機会が残っている段であり、そこで中断すると直せる失敗まで
    人手へ戻すことになる。中断は上限のラウンド数・振動の検知・`merge-fix` が受け持つ。

    Exit code: 0=approved, 2=continue, 7=結果なしのため起動し直す,
               8=待ち行列に投稿が残っている, 1=error
    """
    pr = args.pr
    _auto_flush(pr)
    st = _load(pr)
    if not st.get("rounds"):
        die("state.rounds が空。`state.py start-round` を先に呼んでください")
    last = st["rounds"][-1]
    only = st.get("only")

    codex_intent = _agent_intent(last, "codex", only)
    agy_intent = _agent_intent(last, "agy", only)
    round_passes = _round_passes(last, only)

    carried = _carried_over_pending(st)
    carried_count = (st.get("carried_over") or {}).get("count", 0)

    print(f"CODEX_INTENT={codex_intent}")
    print(f"AGY_INTENT={agy_intent}")
    print(f"CARRIED_OVER_THREADS={carried_count}")
    pending_posts = _pending_posts(pr)
    print(f"PENDING_POSTS={pending_posts}")

    no_result = _no_result_agents(last, only)
    if no_result:
        last["verdict"] = "no_result"
        relaunched = last.get("relaunched") or []
        pending = [a for a in no_result if a not in relaunched]
        if not pending:
            # 2 度続けて結果が残らないのは、対象や負荷ではなく実行環境の側の事象である。
            st["final"] = "error"
            st["ended_at"] = _now()
            _save(pr, st)
            die(
                f"起動し直した後も結果が残りませんでした: {' '.join(no_result)}。"
                " 実行環境の側の問題として中断します。最終スイープを通してから"
                "完了報告へ進んでください",
                code=1,
            )
        last["relaunched"] = relaunched + pending
        _save(pr, st)
        print(f"RELAUNCH_AGENTS='{' '.join(pending)}'")
        print(f"RELAUNCH_TARGET={'both' if len(pending) == 2 else pending[0]}")
        info(
            f"→ 結果を残さなかったレビュアーがいる: {' '.join(pending)}。"
            "同じラウンドで 1 度だけ起動し直す。"
        )
        sys.exit(7)

    if round_passes and carried is None and pending_posts:
        # **届いていない投稿があるあいだは収束させない。** 修正するものは無いので
        # 修正の工程（2）へは回さず、流し直す先（8）へ分ける。
        last["verdict"] = "queued"
        _save(pr, st)
        info(
            f"→ 待ち行列に {pending_posts} 件残っている。"
            "流し切るまで収束させない（`state.py flush` で流す）。"
        )
        sys.exit(8)

    if round_passes and carried is None:
        ci = _round_ci(st, last, pr)
        last["ci"] = ci
        print(f"CI_VERDICT={ci['verdict']}")
        if ci["verdict"] == "code_failure":
            last["verdict"] = "changes_requested"
            _save(pr, st)
            info(
                f"→ 両方 APPROVE だが継続的統合が失敗している: {' '.join(ci['failed'])}。"
                "修正へ。"
            )
            sys.exit(2)
        last["verdict"] = "approved"
        st["final"] = "approved"
        st["ended_at"] = _now()
        _save(pr, st)
        if ci["verdict"] == "meta_only":
            info(f"⚠ {ci['note']}")
        elif ci["verdict"] == "pending":
            info(
                f"⚠ 未完了の検査ジョブが残ったまま収束する: {' '.join(ci['pending'])}。"
                "完了は待たない"
            )
        elif ci["verdict"] == "unverified":
            info(f"⚠ 継続的統合を確かめられないまま収束する: {ci['reason']}")
        info("✅ 両方 APPROVE。収束。")
        sys.exit(0)

    last["verdict"] = "changes_requested"
    _save(pr, st)
    if round_passes:
        info(
            f"→ 引き継いだ指摘が {carried_count} 件残っている。"
            "修正の工程を 1 度通すまで収束させない。"
        )
    else:
        info(f"→ codex={codex_intent} agy={agy_intent}。修正へ。")
    sys.exit(2)


# 指摘の位置がずれても同じ箇所として数える幅。修正で前後にずれる幅として、同じ処理の
# まとまりの中の移動を拾い、隣の指摘まで巻き込まない値を採る。**この値の根拠となる実測は
# まだ無い。** 出力へ内訳を出すのは、実測を集めるためである。
OSCILLATION_NEAR_LINES = 3
# 本文を比べる長さ。先頭だけを見るのは、末尾の言い回しの揺れで別物にならないようにするため。
OSCILLATION_BODY_CHARS = 80
# 正規化で落とすもの。**文字と数字は言語を問わず残す。** 指摘の本文は日本語で書かれるため、
# ASCII の英数字だけを残すと本文が空になり、別の指摘どうしが一致してしまう。
_OSCILLATION_DROP = re.compile(r"[^\w]", re.UNICODE)


def _normalized_body(body: object) -> str:
    """指摘の本文を、行番号・引用符・記号の違いで別物にならない形へ揃える。"""
    if not isinstance(body, str):
        return ""
    return _OSCILLATION_DROP.sub("", body.lower())[:OSCILLATION_BODY_CHARS]


def cmd_check_oscillation(args: argparse.Namespace) -> None:
    """Step 4 — 同じ箇所の指摘の重なりを計算。

    前ラウンドと現ラウンドで重なりが 50% 以上なら final=oscillation で中断。
    rotation 直後は round_in_pr<2 なのでスキップ。

    同じ箇所かどうかは 3 つの一致で測り、いずれか 1 つで結びつけば同じ箇所として数える。
    位置の完全一致だけで測ると、指摘の趣旨が同じでも行が 1 行ずれれば別の指摘として
    数える。レビューを行うのは codex / agy であり、同じ箇所を指すときに選ぶ行は毎回
    同じとは限らない。修正で行が前後にずれた場合も一致しない。

    | 一致 | 条件 |
    | --- | --- |
    | 位置の一致 | ファイルが同じで、行が同じ |
    | 近傍の一致 | ファイルが同じで、行の差が `OSCILLATION_NEAR_LINES` 以内 |
    | 本文の一致 | ファイルが同じで、正規化した本文が同じ |
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

    def collect_keys(round_no: int) -> list[tuple[str, int, str]]:
        """そのラウンドの指摘を (ファイル, 行, 正規化した本文) の並びで返す。"""
        keys: list[tuple[str, int, str]] = []
        for agent in ("codex", "agy"):
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
                    try:
                        keys.append((str(path), int(line), _normalized_body(c.get("body"))))
                    except (TypeError, ValueError):
                        continue
        return keys

    prev = collect_keys(prev_round_no)
    curr = collect_keys(curr_round_no)
    if not curr:
        info("⏭ 現ラウンドの payload なし: 振動検知スキップ")
        sys.exit(2)

    exact = near = same_body = 0
    for path, line, body in curr:
        same_file = [p for p in prev if p[0] == path]
        if any(line == p[1] for p in same_file):
            exact += 1
        elif any(abs(line - p[1]) <= OSCILLATION_NEAR_LINES for p in same_file):
            near += 1
        elif body and any(body == p[2] for p in same_file):
            same_body += 1
    overlap_count = exact + near + same_body
    ratio = overlap_count / len(curr)
    info(
        f"振動検知: overlap={overlap_count}/{len(curr)} ({ratio:.0%})"
        f" 位置={exact} 近傍={near} 本文={same_body}"
    )

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
        # 次のラウンドの開始時に、申告どおり Resolve されたかを突き合わせる。
        "resolved_thread_ids": _thread_ids(fix.get("resolved_threads")),
        "ci": fix.get("ci_status"),
        "ci_failed_checks": fix.get("ci_failed_checks", []) or [],
        "ci_note": fix.get("ci_note"),
        "by_severity": fix.get("by_severity", {}),
    }
    st["rounds"][-1]["ended_at"] = _now()
    # 引き継いだ指摘は、修正の工程を 1 度通した時点で収束の抑止から外す。
    # 残りは最終スイープ (Step 7.5) が受け持つ。
    carried = _carried_over_pending(st)
    if carried is not None:
        carried["fixed_in_round"] = round_no
        info(f"↻ 引き継いだ指摘を round {round_no} の修正の工程へ通しました")
    for d in _deferred_nits:
        st["deferred_nits"].append({**d, "pr": pr, "round": round_no})
    _save(pr, st)

    # CI 分類
    if (fix.get("ci_status") or "").upper() != "FAILURE":
        info(f"✅ fix マージ完了 (commit={fix_commit} fixed={fixed_count})")
        return

    # 振り分けは `_classify_ci` が 1 か所で持つ。ここが読むのは修正の担当が申告した
    # 失敗の名前で、進行側が照会し直す段ではない。申告は完了した失敗として渡す。
    failed = fix.get("ci_failed_checks") or []
    classified = _classify_ci(
        [{"name": str(n), "status": "completed", "conclusion": "failure"} for n in failed]
    )

    if classified.code_failed:
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


def _head_branch_of(pr: int) -> str | None:
    """Pull Request の head branch を取り直す。取れなければ `None` を返す。"""
    try:
        out = _sh(["gh", "pr", "view", str(pr), "--json", "headRefName", "-q", ".headRefName"],
                  check=False)
    except Exception:
        return None
    name = str(out).strip()
    return name or None


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

    # **巻き直しは新しい枝を作ることがある。** `squash` は `<枝名>-r<時刻>` を push する。
    # 状態ファイルの `head_branch` を更新しないと、巻き直しの直後に再開したときに
    # 巻き直し前の枝へ作業ツリーを合わせようとする（#244）。
    #
    # 枝名は引数で受け取る。巻き直しのスクリプトは light / squash のどちらでも
    # `NEW_BRANCH=` を出力する。渡されなかったときは新しい Pull Request から取り直し、
    # それも取れなければ既存の値を残す。**取り直せないことで進行を止めない。**
    # ラウンドの開始時の同期が毎回取り直すため、次のラウンドで書き戻される。
    head_branch = getattr(args, "head_branch", None)
    if not head_branch:
        head_branch = _head_branch_of(new_pr)
    if head_branch:
        st["head_branch"] = head_branch
    else:
        info(f"⚠ PR #{new_pr} の head branch を取得できませんでした。前の値を残します")

    _save(pr, st)
    info(f"✅ current_pr: {old_pr} → {new_pr} (head_branch={st.get('head_branch')})")


def _read_sweep_result(pr: int, file: str | None) -> dict[str, Any]:
    """最終スイープの結果ファイルを読む。読めない形はすべて中断する。

    結果が無いまま完了報告へ進むと、最終スイープを実行したかどうかが残らない。
    """
    path = pathlib.Path(file) if file else _resolve_tmp_dir(pr) / f"sweep-pr{pr}-result.json"
    if not (path.exists() and path.stat().st_size > 0):
        die(
            f"最終スイープの結果ファイルがありません ({path})。"
            " Step 7.5 を実行してから完了報告へ進んでください"
        )
    try:
        sweep = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        die(f"最終スイープの結果ファイルを読めません ({path}): {exc}")
    if not isinstance(sweep, dict):
        die(f"最終スイープの結果ファイルが dict ではありません ({path})")
    return sweep


def cmd_verify_sweep(args: argparse.Namespace) -> None:
    """Step 7.5 後段 — 最終スイープの後に未解決の指摘が残っていないかを確かめる。

    結果ファイルに書かれた残件数は申告であり、GitHub 側の実数とは別のものである。
    申告のまま完了報告へ進むと、未解決の指摘が残ったまま「0 件」と報告される。

    Exit code: 0=残っていない, 6=残っている（件数と理由を完了報告へ入れる）, 1=エラー
    """
    pr = args.pr
    st = _load(pr)
    sweep = _read_sweep_result(pr, args.file)

    declared = _as_count(sweep.get("remaining_open"))
    current_pr = int(st.get("current_pr") or pr)
    threads = _fetch_unresolved_threads(str(st.get("repo") or ""), current_pr)
    if threads is None:
        info(
            "⚠ 未解決の指摘を確認できません — 申告された残件数"
            f"（{declared} 件）をそのまま採用します"
        )
        remaining, verified = declared, False
    else:
        remaining, verified = len(threads), True

    reason = sweep.get("remaining_reason") or sweep.get("reason")
    if remaining > 0 and not reason:
        reason = "理由の記載なし"
    st["sweep"] = {
        "declared_remaining_open": declared,
        "remaining_open": remaining,
        "remaining_reason": reason if remaining > 0 else None,
        "verified": verified,
        "resolved": sweep.get("resolved"),
        "fixed_in_sweep": sweep.get("fixed_in_sweep"),
        "commit": sweep.get("commit"),
        "checked_at": _now(),
    }
    _save(pr, st)

    print(f"REMAINING_OPEN={remaining}")
    print(f"SWEEP_VERIFIED={'1' if verified else '0'}")
    if remaining == 0:
        info("✅ 未解決の指摘は残っていません")
        sys.exit(0)
    info(f"⚠ 未解決の指摘が {remaining} 件残っています（理由: {reason}）")
    sys.exit(6)


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
    print("| round | PR | codex | agy | fix | CI |")
    print("|---|---|---|---|---|---|")
    for r in st["rounds"]:
        codex = r.get("codex") or {}
        agy = r.get("agy") or {}
        fix = r.get("fix") or {}
        codex_s = f"{codex.get('intent', '-')} ({codex.get('comments', '-')})" if codex else "-"
        agy_s = f"{agy.get('intent', '-')} ({agy.get('comments', '-')})" if agy else "-"
        fix_s = "-"
        if fix:
            fix_s = f"{(fix.get('commit') or '')[:7]} ({fix.get('fixed', 0)} fixed, {fix.get('deferred', 0)} deferred)"
        ci_s = fix.get("ci") or "-"
        print(f"| {r['round']} | #{r['pr']} | {codex_s} | {agy_s} | {fix_s} | {ci_s} |")
    print()

    sweep = st.get("sweep")
    if isinstance(sweep, dict):
        source = "GitHub 側で確認済み" if sweep.get("verified") else "申告のまま（確認できず）"
        print("## 最終スイープ")
        print(f"- 未解決の指摘: {sweep.get('remaining_open', 0)} 件（{source}）")
        if sweep.get("remaining_open"):
            print(f"- 残った理由: {sweep.get('remaining_reason') or '理由の記載なし'}")
        print()
    else:
        print("## 最終スイープ: 未検証")
        print("`state.py verify-sweep <PR>` を実行してから完了報告へ進んでください。")
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
    # 副コマンドの説明はここ（`help`）だけが持つ。**モジュールの docstring へ写さない。**
    # 2 か所へ書くと片方だけが実装から離れる。振動の検知の基準は実装が 3 つの一致へ
    # 変わった後も、docstring 側が古い基準を出し続けていた（#329）。
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    sp = sub.add_parser("init", help="Step 0 — state 初期化 or 再開")
    sp.add_argument("pr", type=int)
    sp.add_argument("--max-rounds", type=int, default=12)
    sp.add_argument("--rotate-after", type=int, default=8)
    sp.add_argument("--only", choices=["codex", "agy"], default=None)
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

    sp = sub.add_parser(
        "start-round",
        help="Step 1 — round 開始判定 (1=上限到達/5=後始末の未了/8=同期できない)",
    )
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_start_round)

    sp = sub.add_parser("read-result", help="Step 2.5 — review result を state にマージ")
    sp.add_argument("pr", type=int)
    sp.add_argument("agent", choices=["codex", "agy"])
    sp.add_argument("--file", default=None)
    sp.set_defaults(func=cmd_read_result)

    sp = sub.add_parser(
        "unresolved-threads",
        help="PR 上の未解決の指摘を数える (0=数えられた/1=取得できなかった)",
    )
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_unresolved_threads)

    sp = sub.add_parser("flush", help="待ち行列に積んだ投稿を流す (常に 0)")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_flush)

    sp = sub.add_parser(
        "judge",
        help="Step 3 — intent ベース pass 判定 "
             "(0=approved/2=continue/7=起動し直し/8=待ち行列に残あり)",
    )
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_judge)

    sp = sub.add_parser(
        "check-oscillation",
        help="Step 4 — 同じ箇所を指す指摘の割合を計算 (2=続行/4=振動で中断)",
    )
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
    sp.add_argument(
        "--head-branch",
        default=None,
        help="巻き直しで作られた新しい枝名（rotate-pr.sh の NEW_BRANCH）",
    )
    sp.add_argument("pr", type=int, help="state file の元 PR")
    sp.add_argument("new_pr", type=int)
    sp.set_defaults(func=cmd_set_current_pr)

    sp = sub.add_parser(
        "verify-sweep",
        help="Step 7.5 後段 — 最終スイープ後の未解決の指摘を検証 (0=残なし/6=残あり)",
    )
    sp.add_argument("pr", type=int)
    sp.add_argument("--file", default=None)
    sp.set_defaults(func=cmd_verify_sweep)

    sp = sub.add_parser("report", help="Step 8 — deferred nit + サマリ表示")
    sp.add_argument("pr", type=int)
    sp.set_defaults(func=cmd_report)

    args = p.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
