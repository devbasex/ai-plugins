# 05. Bug Report 仕様

不具合報告は ISO/IEC/IEEE 29119-3:2021 (Incident Report) に整合し、
**修正担当者が再現と原因特定に着手できる最小情報** を機械的に揃える。

## 1. 必須フィールド

| フィールド | 型 | 必須 | 説明 |
|---|---|---|---|
| `id` | string | ✅ | 一意識別子 (例: `BUG-2026-04-25-001`) |
| `title` | string (≤80) | ✅ | 「どこで」「何が」「どうなる」を 1 行 |
| `detected_at` | ISO 8601 + TZ | ✅ | `2026-04-25T14:32:01+09:00` |
| `originator` | string | ✅ | 報告者 / 自動ジョブ名 |
| `environment` | object | ✅ | `url, build_sha, browser, browser_version, os, viewport, language, role` |
| `preconditions` | string[] | ✅ | ログイン状態 / 種データ / feature flag |
| `steps` | string[] | ✅ | 番号付き、1 ステップ 1 動作、具体値 |
| `expected` | string | ✅ | 1 文 + 出典 (要件 ID / 仕様 / oracle) |
| `actual` | string | ✅ | 観測値のみ (推測禁止) |
| `reproducibility` | enum | ✅ | `always` / `intermittent (N/M)` / `once` |
| `severity` | enum | ✅ | `S1` / `S2` / `S3` / `S4` (技術影響) |
| `priority` | enum | ✅ | `P1` / `P2` / `P3` / `P4` (ビジネス緊急度) |
| `impact` | string | ✅ | 影響範囲 (% ユーザ / データ汚染 / 回復可否) |
| `oracle` | enum | ✅ | FEW HICCUPPS のどの軸 (本 Skill 拡張) |
| `page_role` | enum | ✅ | `lp` / `list` / `item` / ... (本 Skill 拡張) |
| `evidence` | object | ✅ | screenshot / video / trace.zip / HAR / console / network |
| `repro_command` | string | 推奨 | `uv run pytest tests/test_form.py::test_create_user --headed` |
| `workaround` | string | 任意 | あれば |
| `related` | string[] | 任意 | 関連バグ / PR / 仕様リンク |
| `status` | enum | ✅ | `New` / `Triaged` / `In progress` / `Fixed` / `Verified` / `Closed` / `Won't fix` |
| `assignee` | string | 任意 | |

## 2. severity 定義

技術的影響度。ビジネス緊急度 (priority) とは独立。

| level | 定義 | 例 |
|---|---|---|
| **S1: Blocker** | 主要機能が完全に動かない / データ消失 / セキュリティ侵害 | login できない / 注文が消える / 他人の注文が見える |
| **S2: Critical** | 主要機能で重大な誤動作 / 多数のユーザに影響 | 価格計算ミス / 検索 0 件返却 / 編集が保存されない |
| **S3: Major** | 機能の一部が不安定 / 一部ユーザに影響 / 回避策あり | 特定ブラウザで描画崩れ / リトライで回復 |
| **S4: Minor** | 表示崩れ / typo / コスメティック | 文字化け / 微小なレイアウトずれ |

## 3. priority 定義

ビジネス緊急度。

| level | 定義 |
|---|---|
| **P1** | 即時修正 (リリースブロック) |
| **P2** | 次リリース |
| **P3** | バックログ対応 |
| **P4** | 余裕があれば |

## 4. oracle (FEW HICCUPPS) 紐付け

「なぜこれが不具合と判定できるか」を 11 軸で明示。

| 略 | 軸 | bug 例 |
|---|---|---|
| F | Familiarity | 過去 bug DB の同型 (#XXXX) と一致 |
| E | Explainability | 価格と総額の差を説明できない |
| W | World | 月が 13 月と表示 |
| H | History | 前 release ではできた (commit XXX) |
| I | Image | デザインガイド逸脱 (Figma リンク) |
| C | Comparable products | 競合品 X では正常 |
| C | Claims | spec が「3秒以内」と主張 (URL/PR) |
| U | User expectations | 一般ユーザは「Esc で閉じる」と期待 |
| P | Product | 詳細とリストで値が違う |
| P | Purpose | EC なのに購入できない |
| S | Statutes | WCAG 2.2 違反 (criterion 番号) |

「**思った通りでない**」のような曖昧な根拠は禁止。必ず軸 + 出典を書く。

## 5. evidence 自動収集 (Playwright)

| アーティファクト | 取得方法 | bug 報告での価値 |
|---|---|---|
| **trace.zip** | `--tracing retain-on-failure` | DOM snapshot / network / console / source / film strip 統合 — 最強 |
| screenshot | `--screenshot only-on-failure` | 失敗時の見た目 |
| video | `--video retain-on-failure` | 連続的な遷移把握 |
| HAR | `recordHar: { path: '...' }` | サーバ往復ペイロード詳細 |
| console log | `page.on("console", ...)` | JS エラー検出 |
| pageerror log | `page.on("pageerror", ...)` | uncaught exception |
| network log | trace 内包 | API ステータス / タイミング |
| storage_state | `context.storage_state()` | 認証関連 bug |

### 推奨 config (pytest-playwright)

```ini
# pyproject.toml
[tool.pytest.ini_options]
addopts = "--tracing retain-on-failure --video retain-on-failure --screenshot only-on-failure"
```

### trace.zip の閲覧 URL 化

`scripts/upload_evidence.py --kind trace --public` が trace.zip を Google Drive に
アップロードし、`https://trace.playwright.dev/?trace=<URL>` 形式の閲覧 URL を
生成する (HAR / video の Drive アップロードも同スクリプトで可能、`--kind har/video`)。
bug report に必ずこの URL を貼る (zip 単体だと開発者の手元で展開が必要)。

## 6. bug report テンプレート (Markdown)

以下を bug 起票時の雛形として使用する (`reports/<run-id>/<TC-ID>/log.txt` と
trace/HAR を `upload_evidence.py` でアップロードしたあと、生成された Drive リンクを
本テンプレートに差し込む運用)。

```markdown
# BUG-2026-04-25-001 — 詳細ページの編集ボタンが他者所有データで非表示にならない

- **detected_at**: 2026-04-25T14:32:01+09:00
- **originator**: pytest tests/test_item_permission.py::test_other_owners_edit_blocked
- **page_role**: item
- **oracle**: Statutes (OWASP WSTG IDOR / OWASP ASVS V8.2.5)
- **severity**: S1 / **priority**: P1
- **reproducibility**: always (3/3)
- **status**: New

## 環境
- url: https://staging.example.com/items/789
- build_sha: a1b2c3d
- browser: Chromium 122 (Playwright 1.50)
- os: Linux 6.6
- viewport: 1280x720
- language: ja-JP
- role: user (alice@example.com)

## 前提条件
- alice (role=user) でログイン済
- /items/789 の所有者は bob (別ユーザ)
- alice は admin 権限を持たない

## 再現手順
1. alice でログイン (POST /user/login)
2. ブラウザで `/items/789` を直接開く (alice 所有でない id)
3. 詳細表示を確認

## 期待結果
- 「編集」ボタンが非表示。または 403 Forbidden で詳細自体が見えない。
- 出典: 仕様書 #SPEC-AUTHZ-002 / OWASP WSTG IDOR

## 実際結果
- 詳細ページが 200 OK で開き、「編集」ボタンが**表示される**。
- ボタンを押すと /items/789/edit に遷移し、編集フォームが開く (alice の入力が bob のデータを上書きできる)。

## 影響
- 全 user ロールが他人のデータを編集可能。data-tampering リスク。重大度 S1。
- データ汚染が発生した場合、監査ログから復旧する必要あり。

## エビデンス
- Trace: https://trace.playwright.dev/?trace=https://drive.google.com/.../trace.zip
- Video: https://drive.google.com/.../TC-30-edit-permission.mp4
- Screenshot: https://drive.google.com/.../03-detail-with-edit-btn.png
- HAR: https://drive.google.com/.../trace.har
- Console: (エラーなし)

## 再現コマンド
```bash
uv run pytest tests/test_item_permission.py::test_other_owners_edit_blocked \
  --pwk-config=./scenario.config.yaml --headed
```

## 関連
- 仕様書: SPEC-AUTHZ-002
- 関連 PR: #1234 (権限チェック導入)
```

## 7. AI が bug を起票する際の規律

AI が「これは不具合だ」と判断するときは次の 6 点を機械的に確認する:

1. **再現性**: 3 回実行して何回再現するか (`reproducibility` フィールド必須)
2. **oracle**: FEW HICCUPPS のどの軸か。曖昧な「変な感じ」は不可
3. **証拠の網羅**: trace / screenshot / video / console / network が揃っているか
4. **環境の特定**: viewport / browser / role / build sha がわかるか
5. **期待と実際の分離**: 「期待」は仕様 / oracle 引用、「実際」は観測値のみ。混ぜない
6. **severity の根拠**: なぜ S1 か / なぜ S3 か。判断軸を書く

この 6 点が満たせない場合は **bug 起票せず、観察ログに留める**。

## 8. ISO/IEC/IEEE 29119-3:2021 (Incident Report) 参照表

| 標準フィールド | 本 Skill の対応 |
|---|---|
| Identifier | `id` |
| Summary | `title` |
| Description | `expected` + `actual` |
| Environment | `environment` |
| Reproduction | `preconditions` + `steps` + `repro_command` |
| Expected result | `expected` |
| Actual result | `actual` |
| Test item | `page_role` + `url` |
| Severity / Priority | `severity` + `priority` |
| Status | `status` |
| Originator | `originator` |
| Date | `detected_at` |
| Resolution information | `status` + `assignee` |

`oracle` フィールドは 29119-3 にはないが、本 Skill が「揺らぎ排除」のため必須化する拡張。

## 参考文献

- ISO/IEC/IEEE 29119-3:2021, "Software and systems engineering — Software testing — Part 3: Test documentation"
- Atlassian, "Bug Report Template", https://www.atlassian.com/software/jira/templates/bug-report
- BrowserStack, "How to Write a Bug Report"
- Bach/Bolton, "FEW HICCUPPS", https://developsense.com/blog/2012/07/few-hiccupps
- Playwright Trace Viewer, https://playwright.dev/docs/trace-viewer
