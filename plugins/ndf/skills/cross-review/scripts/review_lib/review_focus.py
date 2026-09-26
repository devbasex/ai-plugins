"""レビュー観点のテンプレートと、ラウンドごとの観点の組み立て（#1142 の C2）。"""
from __future__ import annotations

import argparse
import pathlib
from typing import Any

import review_lib  # noqa: E402
from classifications import has_domain_model, review_stage  # noqa: E402


COMMON_REVIEW_TEMPLATE = """## 自動追加レビュー観点: 共通
- PR の目的と変更範囲が一貫しているか。余分な変更、未説明の仕様変更、将来の保守を難しくする設計がないか。
- 変更が既存仕様・既存コメント・既存コードの前提と矛盾しないか。重複指摘は避け、根拠がある修正アクションだけを指摘する。
- テスト、検証手順、エラーハンドリング、ロールバック容易性が変更リスクに見合っているか。"""

DOCS_ONLY_REVIEW_TEMPLATE = """## 自動追加レビュー観点: ドキュメントのみ PR
- ドキュメントの主張・企画・手順が妥当で、読者の意思決定や作業を誤らせないか。
- コード、設定、コマンド、既存 README / docs / CHANGELOG との整合性が取れているか。古い名称、存在しないパス、実装と違う説明がないか。
- ドキュメント間で用語、前提、バージョン、責務分担が矛盾していないか。
- 追加・更新された説明が必要十分で、曖昧な表現や未検証の断定がないか。"""

DESIGN_REVIEW_TEMPLATE = """## 自動追加レビュー観点: 設計 PR
- 要求・設計・決定の記録の 3 文書で、受け入れ条件ごとに設計の要素とテスト設計の行があるか。決定で退けた案が他の節に残っていないか。
- 状態（ファイル・環境変数・状態ファイルの項目・引数）ごとに、書き手と読み手を並べる。同じ状態を 2 つの経路が書く・読む側が書く側より先に動く・失敗した書き手の後に読む、の矛盾が無いか。
- 外部コマンド・外部ツールの挙動（優先順位・終了コード・一致の範囲）を断定する記述に、実測の根拠（コマンドと出力）があるか。
- 用語集どおりか。用語集に無い語・廃止した語で書いていないか。
- 不変条件を破っていないか。構成要素・処理の流れが、ドメインモデルの節の不変条件に反する手順を含まないか。
- コンテキストの境界を越えていないか。宣言した関係の外で、ほかのコンテキストの集約を書き換えていないか。
- ドメインモデルの節は確定したものとして扱う。変えるべきときは、指摘にそう書く。"""

# 設計 PR の 1 ラウンド目（モデルの段、#1111）で渡す観点。詳細の段は DESIGN_REVIEW_TEMPLATE を渡す
MODEL_REVIEW_TEMPLATE = """## 自動追加レビュー観点: 設計 PR（モデルの段）
このラウンドは、設計文書のドメインモデルの節と、用語集の差分だけを見る。**節の外への指摘はこのラウンドでは書かない。**
- 変更が 2 つ以上のコンテキストにまたがるのに、コンテキストマップの関係の宣言が無くないか。
- 集約ごとに、書き換えてよい持ち主が 1 つに決まっているか。
- 不変条件どうしが矛盾しないか。1 行が 1 つの条件か。
- ドメインイベントごとに受け手があるか。要求のドメインイベントの番号を引き継いでいるか。
- 用語の表が用語集と一致するか（足す語・意味を変える語・廃止する語が、同じ変更の用語集に反映されているか）。"""

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


# カテゴリ名 → レビュー観点テンプレートの対応。PATH_CATEGORY_RULES（カテゴリ名 →
# 判定述語）と対にして 1 か所に置く。カテゴリを増やすときは両方の表を同時に直す。
# special な common / docs_only / deletion_rename は判定述語を持たないためこの表にだけ載る。
CATEGORY_TEMPLATES = {
    "common": COMMON_REVIEW_TEMPLATE,
    "docs_only": DOCS_ONLY_REVIEW_TEMPLATE,
    "design": DESIGN_REVIEW_TEMPLATE,
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


def _design_stage_fields(kind: str, worktree: object, changed_files: list[dict[str, Any]],
                         review_instructions: str, manual: str) -> dict[str, Any]:
    """設計 PR の段（モデル / 詳細）の材料を返す（#1111）。設計 PR でなければ空。

    `design_has_model` は変更した設計文書のどれかに見出し `## ドメインモデル` があるか。
    段ごとの観点は `review_instructions_by_stage` に持ち、`launch-reviewer.sh` がラウンドの段で選ぶ。
    """
    if kind != "design":
        return {}
    paths = [p for entry in changed_files or [] if isinstance(entry, dict)
             for p in entry.get("paths", []) if isinstance(p, str)]
    paths += [entry for entry in changed_files or [] if isinstance(entry, str)]
    return {
        "design_has_model": has_domain_model(str(worktree) if worktree else None, paths),
        "review_instructions_by_stage": {
            "model": _combined_review_instructions(MODEL_REVIEW_TEMPLATE, manual),
            "detail": review_instructions,
        },
    }


def _round_stage(st: dict[str, Any], round_no: int) -> str | None:
    """ラウンドの段。設計 PR の 1 ラウンド目でドメインモデルの節があれば model、ほかの設計 PR は detail。"""
    return review_stage(st.get("review_kind") or "code", round_no, bool(st.get("design_has_model")))


def _auto_review_instructions(categories: list[str]) -> str:
    parts = (CATEGORY_TEMPLATES.get(c) for c in categories)
    return "\n\n".join(p for p in parts if p is not None)


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
            review_lib.die(f"追加レビュー観点ファイルを読めません: {path} ({exc})")
        if text:
            parts.append(text)

    return "\n\n".join(parts)
