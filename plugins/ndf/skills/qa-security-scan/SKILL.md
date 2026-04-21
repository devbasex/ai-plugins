---
name: qa-security-scan
description: |
  Security scanning templates and checklists for OWASP Top 10, authentication, authorization, data protection. Use when conducting security testing or vulnerability assessment.

  This skill provides comprehensive security testing:
  - OWASP Top 10 checklist with remediation
  - Authentication and authorization testing
  - Data protection verification
  - Security report generation

  Triggers: "security scan", "vulnerability check", "OWASP", "security test", "セキュリティスキャン", "脆弱性チェック", "セキュリティテスト"
---

# QA Security Scan Skill

## 概要

qaエージェントがセキュリティスキャンと脆弱性評価を実施する際に使用します。OWASP Top 10に基づいた包括的なチェックリストと、認証・認可・データ保護の検証手順を提供します。

## クイックリファレンス

### OWASP Top 10 概要

| # | 脆弱性 | 主な対策 |
|---|--------|----------|
| 1 | インジェクション | パラメータ化クエリ、ORM使用 |
| 2 | 認証の不備 | bcrypt/Argon2、MFA、レート制限 |
| 3 | 機密データ露出 | HTTPS、暗号化、環境変数管理 |
| 4 | XXE | DTD処理無効化、JSON使用 |
| 5 | アクセス制御不備 | RBAC、所有者チェック |
| 6 | 設定ミス | helmet.js、適切なCORS |
| 7 | XSS | エスケープ、CSP、DOMPurify |
| 8 | デシリアライゼーション | 署名検証、信頼できるデータのみ |
| 9 | 既知の脆弱性 | npm audit、Dependabot |
| 10 | ログ不足 | セキュリティイベント記録 |

### 基本的な使い方

1. 対象コードを特定
2. 該当するチェックリストを適用
3. 脆弱性を発見したらレポート作成
4. 修正方法を提案

## ベストプラクティス

| DO | DON'T |
|----|-------|
| 定期的なスキャン（月1回以上） | スキャンのみで満足 |
| CI/CDパイプラインに統合 | 警告を無視 |
| 重大度順に対応（高→低） | 本番環境で初スキャン |
| 修正後に再スキャン | 自動化ツールに全依存 |

## 詳細ガイド

| ファイル | 内容 |
|---------|------|
| `01-owasp-checklist.md` | OWASP Top 10 詳細チェックリストとコード例 |
| `02-auth-checklist.md` | 認証・認可テスト手順 |
| `03-report-template.md` | セキュリティレポートテンプレート |

## 関連Skill

- **corder-code-templates**: セキュアなコードテンプレート
