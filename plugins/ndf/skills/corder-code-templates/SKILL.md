---
name: corder-code-templates
description: |
  Generate code templates for common patterns: REST API endpoints, React components, database models, authentication, error handling. Use when implementing new features or creating boilerplate code.

  This skill provides production-ready code templates:
  - REST API endpoints (Express, FastAPI)
  - React/Vue components with best practices
  - Database models (Sequelize, TypeORM, Mongoose)
  - Authentication middleware (JWT, OAuth)

  Triggers: "create API", "new component", "implement auth", "add model", "generate code", "コードテンプレート", "API作成", "コンポーネント作成"
allowed-tools:
  - Read
  - Write
  - Bash
---

# Corder Code Templates Skill

## 概要

corderエージェントが新機能を実装する際に使用するコードテンプレート集です。REST APIエンドポイント、Reactコンポーネント、データベースモデル、認証ロジックなど、頻出パターンのテンプレートを提供します。

## クイックリファレンス

### テンプレート一覧

| テンプレート | ファイル | 用途 |
|-------------|---------|------|
| REST API | `rest-api-endpoint.js` | Express CRUD操作 |
| FastAPI | `rest-api-endpoint.py` | Python APIエンドポイント |
| React Component | `react-component.jsx` | 関数コンポーネント + Hooks |
| Database Model | `database-model.js` | Sequelizeモデル |
| Auth Middleware | `auth-middleware.js` | JWT認証 |
| Error Handler | `error-handler.js` | グローバルエラーハンドラー |

### カスタマイズ手順

1. テンプレートをコピー
2. プレースホルダーを置換（`[RESOURCE]`, `[MODEL_NAME]`等）
3. ビジネスロジックを追加
4. テスト作成（corder-test-generation使用）

## ベストプラクティス

| DO | DON'T |
|----|-------|
| テンプレートをそのままコピー | 安全機能を削除 |
| プロジェクト規約に合わせる | 古いパターンを使用 |
| セキュリティを最優先 | セキュリティを軽視 |
| テストを作成 | async/awaitを避ける |

## 詳細ガイド

| ファイル | 内容 |
|---------|------|
| `01-api-templates.md` | REST API、認証ミドルウェア、エラーハンドラーのテンプレート |
| `02-component-templates.md` | React/Vueコンポーネント、データベースモデルのテンプレート |

## 関連Skill

- **corder-test-generation**: テンプレートから生成したコードのテスト作成
