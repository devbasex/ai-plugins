# NDFプラグイン - Sub-Agent Skills 詳細設計書

**作成日**: 2025-12-15
**担当**: director agent
**関連Issue**: i07.md

---

## 設計方針

### 基本原則
1. **焦点を絞る**: 1 Skill = 1機能
2. **明確な説明**: トリガー用語を含む具体的なdescription
3. **既存MCPとの重複回避**: MCPで実現できることはSkillsにしない
4. **作業効率最大化**: 繰り返しタスクの自動化、テンプレート化

### 優先度基準
- **高**: 頻繁に実行する定型作業、テンプレート化で大幅な時短
- **中**: 有用だが頻度は中程度、または実装コストが高い
- **低**: Nice-to-have、または既存ツールで十分対応可能

---

## 1. director agent 用 Skills

### Skill 1.1: Project Planning Templates
**name**: `director-project-planning`
**description**: Create structured project plans with task breakdown, timeline, resource allocation, and risk assessment. Use when starting new features, refactoring, or complex implementations. Triggers: "plan", "roadmap", "task breakdown", "project structure".

**提供機能**:
- プロジェクト計画書テンプレート生成
- タスク分解とマイルストーン設定
- リスク評価とリソース配分
- 並列実行可能性の自動判断

**ディレクトリ構造**:
```
skills/director-project-planning/
├── SKILL.md
├── templates/
│   ├── project-plan-template.md
│   ├── task-breakdown-template.md
│   └── risk-assessment-template.md
└── scripts/
    └── generate-plan.js
```

**allowed-tools**: Read, Write, Glob, Grep

**スクリプト概要** (`generate-plan.js`):
- ユーザー入力（プロジェクト概要、目標）を受け取る
- テンプレートを読み込み、動的に項目を埋める
- タスク分解を提案（実装→テスト→ドキュメント）
- issues/ディレクトリに自動保存

**テンプレート概要** (`project-plan-template.md`):
```markdown
# [プロジェクト名] 実装計画

## 概要
- 目的:
- スコープ:
- 期限:

## タスク分解
### フェーズ1: [名前]
- [ ] タスク1
- [ ] タスク2

## リソース配分
- 必要なサブエージェント:
- 並列実行可能タスク:

## リスク評価
- リスク1: [説明] - 対策:
```

**優先度**: 🔴 **高** - Directorの最も重要な機能

---

### Skill 1.2: GitHub Integration
**name**: `director-github-integration`
**description**: Create and manage GitHub issues, pull requests, and milestones from project plans. Use when converting plans to actionable GitHub items. Triggers: "create issue", "open PR", "github milestone", "track progress".

**提供機能**:
- 計画書からGitHub Issue自動生成
- Pull Request作成支援
- マイルストーン管理
- 進捗トラッキング

**ディレクトリ構造**:
```
skills/director-github-integration/
├── SKILL.md
├── templates/
│   ├── issue-template.md
│   └── pr-template.md
└── scripts/
    └── create-github-items.js
```

**allowed-tools**: Bash（git/gh コマンド）, Read, Write

**スクリプト概要** (`create-github-items.js`):
- 計画書を解析し、タスクごとにIssueを作成
- `gh issue create`コマンドを実行
- Issue番号を計画書に逆参照として追加
- ラベル、担当者、マイルストーンを自動設定

**テンプレート概要** (`issue-template.md`):
```markdown
## 概要
[タスクの説明]

## 受入基準
- [ ] 基準1
- [ ] 基準2

## 関連
- 計画書: [リンク]
- 親Issue: #XXX
```

**優先度**: 🟡 **中** - GitHub統合は便利だが、手動でも可能

---

### Skill 1.3: Progress Reporting
**name**: `director-progress-report`
**description**: Generate progress reports summarizing completed tasks, ongoing work, blockers, and next steps. Use when updating stakeholders or reviewing project status. Triggers: "progress report", "status update", "weekly report".

**提供機能**:
- 進捗レポート自動生成
- 完了タスク、進行中タスク、ブロッカーの整理
- 次のアクション提案
- グラフ・統計データ生成（オプション）

**ディレクトリ構造**:
```
skills/director-progress-report/
├── SKILL.md
├── templates/
│   └── progress-report-template.md
└── scripts/
    └── generate-report.js
```

**allowed-tools**: Read, Write, Bash（git log等）

**スクリプト概要** (`generate-report.js`):
- Git historyから最近のコミットを取得
- issues/ディレクトリの計画書を読み、進捗を抽出
- 完了率を計算
- レポートを生成してissues/配下に保存

**テンプレート概要** (`progress-report-template.md`):
```markdown
# 進捗レポート - [日付]

## サマリー
- 完了タスク: X個
- 進行中タスク: Y個
- ブロッカー: Z個

## 詳細
### 完了
- [タスク名] - [完了日]

### 進行中
- [タスク名] - [進捗率]

### ブロッカー
- [問題] - [対策]

## 次のステップ
1.
```

**優先度**: 🟢 **低** - Nice-to-have、手動でも容易

---

## 2. data-analyst agent 用 Skills

### Skill 2.1: SQL Optimization Patterns
**name**: `data-analyst-sql-optimization`
**description**: Apply SQL optimization patterns including index usage, query rewriting, JOIN optimization, and window functions. Use when improving query performance. Triggers: "optimize SQL", "slow query", "improve performance".

**提供機能**:
- SQLクエリ最適化パターンライブラリ
- パフォーマンス改善提案
- インデックス推奨
- クエリ実行計画の解析

**ディレクトリ構造**:
```
skills/data-analyst-sql-optimization/
├── SKILL.md
├── reference.md        # 最適化パターン詳細
└── examples.md         # Before/Afterサンプル
```

**allowed-tools**: なし（参照のみ）

**reference.md 概要**:
```markdown
## パターン1: N+1クエリ削減
**Before**: 複数回のSELECT
**After**: JOINまたはサブクエリ

## パターン2: WHERE句最適化
**Before**: 関数適用後のフィルタ
**After**: インデックス活用可能な形式

## パターン3: ウィンドウ関数活用
**Before**: サブクエリの入れ子
**After**: ROW_NUMBER(), RANK()
```

**優先度**: 🔴 **高** - データアナリストの頻繁なニーズ

---

### Skill 2.2: Data Visualization Scripts
**name**: `data-analyst-visualization`
**description**: Generate data visualizations (charts, graphs, tables) from query results using Python/matplotlib or JavaScript. Use when creating reports or dashboards. Triggers: "visualize data", "create chart", "plot graph".

**提供機能**:
- クエリ結果の可視化
- チャート生成（棒グラフ、折れ線グラフ、円グラフ）
- HTMLレポート生成
- 画像ファイル出力

**ディレクトリ構造**:
```
skills/data-analyst-visualization/
├── SKILL.md
├── scripts/
│   ├── visualize.py
│   └── generate-html-report.js
└── templates/
    └── report-template.html
```

**allowed-tools**: Bash（Pythonスクリプト実行）, Write

**スクリプト概要** (`visualize.py`):
```python
import pandas as pd
import matplotlib.pyplot as plt
import sys
import json

# JSON形式のクエリ結果を読み込み
data = json.loads(sys.stdin.read())
df = pd.DataFrame(data)

# チャート生成
df.plot(kind='bar', x='category', y='value')
plt.savefig('output.png')
```

**テンプレート概要** (`report-template.html`):
```html
<!DOCTYPE html>
<html>
<head><title>Data Analysis Report</title></head>
<body>
  <h1>{{title}}</h1>
  <img src="{{chart_path}}" />
  <table>{{data_table}}</table>
</body>
</html>
```

**優先度**: 🟡 **中** - 有用だがPython環境依存

---

### Skill 2.3: Data Export Templates
**name**: `data-analyst-export`
**description**: Export query results to various formats (CSV, JSON, Excel, Markdown tables) with proper formatting and headers. Use when saving analysis results. Triggers: "export data", "save results", "output CSV/JSON/Excel".

**提供機能**:
- CSV出力（カンマ区切り、ヘッダー付き）
- JSON出力（構造化、pretty-print）
- Excel出力（複数シート、書式設定）
- Markdownテーブル出力

**ディレクトリ構造**:
```
skills/data-analyst-export/
├── SKILL.md
└── scripts/
    ├── export-csv.js
    ├── export-json.js
    ├── export-excel.js
    └── export-markdown.js
```

**allowed-tools**: Write, Bash

**スクリプト概要** (`export-csv.js`):
```javascript
const fs = require('fs');

function exportToCSV(data, filename) {
  const headers = Object.keys(data[0]).join(',');
  const rows = data.map(row => Object.values(row).join(','));
  const csv = [headers, ...rows].join('\n');
  fs.writeFileSync(filename, csv);
}
```

**優先度**: 🔴 **高** - データアナリストの必須機能

---

## 3. corder agent 用 Skills

### Skill 3.1: Code Generation Templates
**name**: `corder-code-templates`
**description**: Generate code templates for common patterns: REST API endpoints, React components, database models, authentication, error handling. Use when implementing new features. Triggers: "create API", "new component", "implement auth", "add model".

**提供機能**:
- REST APIエンドポイントテンプレート
- Reactコンポーネントテンプレート
- データベースモデルテンプレート
- 認証ロジックテンプレート
- エラーハンドリングパターン

**ディレクトリ構造**:
```
skills/corder-code-templates/
├── SKILL.md
├── templates/
│   ├── rest-api-endpoint.js
│   ├── react-component.jsx
│   ├── database-model.js
│   ├── auth-middleware.js
│   └── error-handler.js
└── reference.md
```

**allowed-tools**: Read, Write, Bash

**テンプレート概要** (`rest-api-endpoint.js`):
```javascript
// [ROUTE_NAME] API Endpoint
const express = require('express');
const router = express.Router();

/**
 * @route   GET /api/[resource]
 * @desc    [Description]
 * @access  [Public/Private]
 */
router.get('/', async (req, res) => {
  try {
    // TODO: Implement logic
    res.json({ success: true, data: [] });
  } catch (error) {
    res.status(500).json({ success: false, error: error.message });
  }
});

module.exports = router;
```

**優先度**: 🔴 **高** - コーディング効率大幅向上

---

### Skill 3.2: Test Generation
**name**: `corder-test-generation`
**description**: Generate unit tests, integration tests, and test fixtures for code. Supports Jest, Mocha, pytest. Use when writing tests. Triggers: "generate tests", "create unit test", "add test coverage".

**提供機能**:
- ユニットテスト生成（Jest、Mocha、pytest）
- 統合テスト生成
- テストフィクスチャ生成
- モック/スパイ設定

**ディレクトリ構造**:
```
skills/corder-test-generation/
├── SKILL.md
├── templates/
│   ├── jest-unit-test.test.js
│   ├── mocha-test.test.js
│   ├── pytest-test.py
│   └── test-fixtures.json
└── scripts/
    └── generate-tests.js
```

**allowed-tools**: Read, Write, Bash

**テンプレート概要** (`jest-unit-test.test.js`):
```javascript
const { [functionName] } = require('../[modulePath]');

describe('[functionName]', () => {
  test('should [expected behavior]', () => {
    // Arrange
    const input = [testInput];
    const expected = [expectedOutput];

    // Act
    const result = [functionName](input);

    // Assert
    expect(result).toEqual(expected);
  });

  test('should handle edge cases', () => {
    // TODO: Add edge case tests
  });
});
```

**スクリプト概要** (`generate-tests.js`):
- ソースコードを解析（Serena MCP使用）
- 関数シグネチャを抽出
- テストケースのスケルトンを生成
- エッジケースの提案

**優先度**: 🔴 **高** - テスト作成は頻繁で時間がかかる

---

### Skill 3.3: Documentation Generator
**name**: `corder-doc-generation`
**description**: Generate API documentation, JSDoc comments, README sections, and inline code comments. Use when documenting code. Triggers: "generate docs", "add comments", "create API docs", "update README".

**提供機能**:
- JSDoc/PyDocコメント生成
- API仕様書生成
- README.mdテンプレート
- インラインコメント提案

**ディレクトリ構造**:
```
skills/corder-doc-generation/
├── SKILL.md
├── templates/
│   ├── jsdoc-template.js
│   ├── pydoc-template.py
│   ├── api-docs-template.md
│   └── readme-template.md
└── scripts/
    └── generate-docs.js
```

**allowed-tools**: Read, Write, Bash

**テンプレート概要** (`jsdoc-template.js`):
```javascript
/**
 * [Function description]
 *
 * @param {[type]} [paramName] - [parameter description]
 * @returns {[returnType]} [return value description]
 * @throws {[ErrorType]} [error condition]
 *
 * @example
 * const result = functionName(param);
 * // result: [expected output]
 */
function functionName(paramName) {
  // Implementation
}
```

**優先度**: 🟡 **中** - 便利だが頻度は中程度

---

## 4. researcher agent 用 Skills

### Skill 4.1: Research Report Templates
**name**: `researcher-report-templates`
**description**: Generate structured research reports with findings, comparisons, recommendations, and citations. Use when documenting investigation results. Triggers: "create report", "summarize findings", "compare technologies".

**提供機能**:
- 調査レポートテンプレート
- 技術比較テーブル生成
- ベストプラクティスまとめ
- 引用・参照リンク管理

**ディレクトリ構造**:
```
skills/researcher-report-templates/
├── SKILL.md
├── templates/
│   ├── research-report-template.md
│   ├── tech-comparison-template.md
│   └── best-practices-template.md
└── scripts/
    └── generate-report.js
```

**allowed-tools**: Read, Write

**テンプレート概要** (`research-report-template.md`):
```markdown
# [調査テーマ] 調査レポート

## 概要
- 調査目的:
- 調査期間:
- 情報源:

## 調査結果
### ポイント1
- 説明
- 詳細
- 参照: [リンク]

### ポイント2
...

## 技術比較
| 項目 | 技術A | 技術B | 技術C |
|------|------|------|------|
| 特徴 |      |      |      |
| 長所 |      |      |      |
| 短所 |      |      |      |

## 推奨事項
1.
2.

## 参考リンク
- [タイトル](URL)
```

**優先度**: 🔴 **高** - Researcherの主要な成果物

---

### Skill 4.2: API Specification Extractor
**name**: `researcher-api-extractor`
**description**: Extract and document API specifications from documentation sites including endpoints, parameters, responses, authentication. Use when integrating external APIs. Triggers: "extract API spec", "document API", "analyze endpoints".

**提供機能**:
- APIエンドポイント一覧抽出
- パラメータ仕様抽出
- レスポンス構造抽出
- 認証方式ドキュメント

**ディレクトリ構造**:
```
skills/researcher-api-extractor/
├── SKILL.md
├── templates/
│   └── api-spec-template.md
└── scripts/
    └── extract-api-spec.js
```

**allowed-tools**: Read, Bash（WebFetch間接利用）

**テンプレート概要** (`api-spec-template.md`):
```markdown
# [API Name] 仕様書

## ベースURL
`https://api.example.com/v1`

## 認証
- 方式: Bearer Token
- ヘッダー: `Authorization: Bearer {token}`

## エンドポイント

### GET /resource
**説明**: [説明]
**パラメータ**:
- `param1` (string, required): [説明]
- `param2` (number, optional): [説明]

**レスポンス**:
```json
{
  "success": true,
  "data": []
}
```

**スクリプト概要** (`extract-api-spec.js`):
- WebFetchでAPIドキュメントを取得
- Markdown/HTMLから構造化情報を抽出
- テンプレートに整形して出力

**優先度**: 🟡 **中** - API統合時に便利

---

## 5. scanner agent 用 Skills

### Skill 5.1: PDF Analysis
**name**: `scanner-pdf-analysis`
**description**: Analyze PDF documents with table extraction, section identification, and content summarization. Use when reading technical documents, reports, or papers. Triggers: "analyze PDF", "extract tables", "summarize document".

**提供機能**:
- PDF構造解析
- テーブル抽出とCSV変換
- セクション識別
- 重要ポイント要約

**ディレクトリ構造**:
```
skills/scanner-pdf-analysis/
├── SKILL.md
├── templates/
│   └── pdf-summary-template.md
└── scripts/
    └── analyze-pdf.py
```

**allowed-tools**: Bash（Pythonスクリプト実行）, Write

**スクリプト概要** (`analyze-pdf.py`):
```python
import PyPDF2
import tabula
import sys

def analyze_pdf(pdf_path):
    # PDFテキスト抽出
    with open(pdf_path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        text = ''.join([page.extract_text() for page in reader.pages])

    # テーブル抽出
    tables = tabula.read_pdf(pdf_path, pages='all')

    # 構造化出力
    return {
        'text': text,
        'tables': tables,
        'page_count': len(reader.pages)
    }
```

**テンプレート概要** (`pdf-summary-template.md`):
```markdown
# [ファイル名] 分析結果

## 概要
- ページ数: X
- テーブル数: Y

## 重要ポイント
1.
2.

## 抽出テーブル
### テーブル1
| 列1 | 列2 |
|-----|-----|
| ... | ... |
```

**優先度**: 🔴 **高** - PDFは頻繁に扱う

---

### Skill 5.2: Excel Data Extraction
**name**: `scanner-excel-extraction`
**description**: Extract, transform, and structure data from Excel files including multiple sheets, formulas, and formatting. Use when processing Excel data. Triggers: "extract Excel data", "read spreadsheet", "convert Excel to JSON/CSV".

**提供機能**:
- 複数シート読み込み
- データ構造化（JSON変換）
- CSV出力
- 数式評価

**ディレクトリ構造**:
```
skills/scanner-excel-extraction/
├── SKILL.md
└── scripts/
    ├── extract-excel.py
    └── convert-to-json.js
```

**allowed-tools**: Bash, Write

**スクリプト概要** (`extract-excel.py`):
```python
import pandas as pd
import sys
import json

def extract_excel(file_path):
    # 全シート読み込み
    excel_file = pd.ExcelFile(file_path)
    data = {}

    for sheet_name in excel_file.sheet_names:
        df = pd.read_excel(file_path, sheet_name=sheet_name)
        data[sheet_name] = df.to_dict(orient='records')

    # JSON出力
    print(json.dumps(data, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    extract_excel(sys.argv[1])
```

**優先度**: 🔴 **高** - Excelは頻繁に扱う

---

## 6. qa agent 用 Skills

### Skill 6.1: Code Review Checklist
**name**: `qa-code-review-checklist`
**description**: Comprehensive code review checklist covering readability, maintainability, performance, security, and best practices. Use when reviewing code. Triggers: "code review", "review checklist", "quality check".

**提供機能**:
- コードレビューチェックリスト
- 言語別ベストプラクティス
- セキュリティチェック項目
- パフォーマンスチェック項目

**ディレクトリ構造**:
```
skills/qa-code-review-checklist/
├── SKILL.md
├── checklists/
│   ├── general-checklist.md
│   ├── javascript-checklist.md
│   ├── python-checklist.md
│   └── security-checklist.md
└── templates/
    └── review-report-template.md
```

**allowed-tools**: なし（参照のみ）

**チェックリスト概要** (`general-checklist.md`):
```markdown
# コードレビューチェックリスト

## 可読性
- [ ] 変数名・関数名は明確か
- [ ] コメントは適切か
- [ ] ネストは深すぎないか

## 保守性
- [ ] DRY原則に従っているか
- [ ] 関数は単一責任か
- [ ] モジュール分割は適切か

## パフォーマンス
- [ ] 不要なループはないか
- [ ] データ構造は適切か
- [ ] キャッシュを活用しているか

## セキュリティ
- [ ] 入力値検証があるか
- [ ] SQLインジェクション対策があるか
- [ ] XSS対策があるか
```

**テンプレート概要** (`review-report-template.md`):
```markdown
# コードレビューレポート - [ファイル名]

## サマリー
- レビュー日:
- レビュアー:
- 評価: ⭐⭐⭐⭐☆

## 問題点
### 重大 🔴
- [問題] - [行番号] - [修正案]

### 警告 🟡
- [問題] - [行番号] - [修正案]

### 提案 🟢
- [改善案]

## 良い点
-

## 総評

```

**優先度**: 🔴 **高** - QAの主要機能

---

### Skill 6.2: Security Scan Templates
**name**: `qa-security-scan`
**description**: Security scanning templates and checklists for OWASP Top 10, authentication, authorization, data protection. Use when security testing. Triggers: "security scan", "vulnerability check", "OWASP".

**提供機能**:
- OWASP Top 10チェックリスト
- 認証・認可検証
- データ保護確認
- セキュリティレポート生成

**ディレクトリ構造**:
```
skills/qa-security-scan/
├── SKILL.md
├── checklists/
│   ├── owasp-top10-checklist.md
│   ├── auth-checklist.md
│   └── data-protection-checklist.md
└── templates/
    └── security-report-template.md
```

**allowed-tools**: なし（参照のみ）

**チェックリスト概要** (`owasp-top10-checklist.md`):
```markdown
# OWASP Top 10 チェックリスト

## 1. インジェクション
- [ ] SQLクエリはパラメータ化されているか
- [ ] コマンドインジェクション対策があるか
- [ ] LDAPインジェクション対策があるか

## 2. 認証の不備
- [ ] パスワードは安全にハッシュ化されているか
- [ ] セッション管理は適切か
- [ ] 多要素認証を実装しているか

## 3. 機密データの露出
- [ ] 通信は暗号化されているか（HTTPS）
- [ ] 機密データはログに出力されていないか
- [ ] APIキーは環境変数管理か
```

**優先度**: 🔴 **高** - セキュリティは最重要

---

### Skill 6.3: Performance Test Report
**name**: `qa-performance-test`
**description**: Generate performance test reports with Core Web Vitals, load times, bottleneck analysis, and optimization recommendations. Use when testing web applications. Triggers: "performance test", "load time", "Core Web Vitals".

**提供機能**:
- Core Web Vitals測定レポート
- ページロード時間分析
- ボトルネック特定
- 最適化提案

**ディレクトリ構造**:
```
skills/qa-performance-test/
├── SKILL.md
├── templates/
│   └── performance-report-template.md
└── scripts/
    └── analyze-performance.js
```

**allowed-tools**: Bash（Chrome DevTools MCP間接利用）, Write

**テンプレート概要** (`performance-report-template.md`):
```markdown
# パフォーマンステストレポート - [URL]

## Core Web Vitals
- **LCP** (Largest Contentful Paint): X.Xs
  - 評価: [Good/Needs Improvement/Poor]
- **FID** (First Input Delay): Xms
  - 評価: [Good/Needs Improvement/Poor]
- **CLS** (Cumulative Layout Shift): X.XX
  - 評価: [Good/Needs Improvement/Poor]

## ページロード時間
- First Contentful Paint: X.Xs
- Time to Interactive: X.Xs
- Total Blocking Time: Xms

## ボトルネック
1. [問題] - [影響度] - [改善案]
2.

## 推奨改善策
1.
2.
```

**スクリプト概要** (`analyze-performance.js`):
- Chrome DevTools MCPでパフォーマンス測定
- Core Web Vitalsを抽出
- ボトルネックを特定（Networkタイムライン解析）
- レポート生成

**優先度**: 🟡 **中** - Web開発時に有用

---

## 実装優先順位まとめ

### 🔴 優先度高（即座に実装）
1. **director-project-planning** - プロジェクト計画書生成
2. **data-analyst-sql-optimization** - SQL最適化パターン
3. **data-analyst-export** - データ出力テンプレート
4. **corder-code-templates** - コード生成テンプレート
5. **corder-test-generation** - テスト生成
6. **researcher-report-templates** - 調査レポートテンプレート
7. **scanner-pdf-analysis** - PDF分析
8. **scanner-excel-extraction** - Excel抽出
9. **qa-code-review-checklist** - コードレビューチェックリスト
10. **qa-security-scan** - セキュリティスキャン

### 🟡 優先度中（余裕があれば実装）
11. **director-github-integration** - GitHub統合
12. **data-analyst-visualization** - データ可視化
13. **corder-doc-generation** - ドキュメント生成
14. **researcher-api-extractor** - API仕様抽出
15. **qa-performance-test** - パフォーマンステスト

### 🟢 優先度低（将来的に検討）
16. **director-progress-report** - 進捗レポート

---

## 次のステップ

1. **外部公開Skills調査結果の統合** - researcherからの結果を待つ
2. **スクリプト・テンプレート詳細設計** - 優先度高のSkillsから着手
3. **実装** - corderエージェントに依頼
4. **テスト** - 各Skillsの動作確認
5. **ドキュメント更新** - plugin.json、README.md、CLAUDE.ndf.md

---

**作成者**: director agent
**次回更新**: 外部Skills調査完了後、またはスクリプト詳細設計完了後
