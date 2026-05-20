# CLAUDE.ndf.md廃止 - 3層構造移行計画

## ステータス
- 作成日: 2026-03-08
- 最終更新: 2026-03-08
- 現在のフェーズ: 実装完了（PR #39）

## 概要

CLAUDE.ndf.mdを廃止し、3層構造（エントリポイント/docs/skills）に移行する。
Serena memoryも廃止し、知識はdocs/、手順はskills/に配置する方針。

---

## 依存関係グラフ

```
タスク1 (mcp-serena新規作成)        ← 独立
タスク2 (ndf-policiesスキル追加)    ← 独立
タスク3 (/ndf:cleanupスキル追加)    ← 独立
タスク5 (MCPプラグインにdocs追加)   ← 独立
タスク4 (NDF既存資産の廃止・変更)   ← タスク1完了後（Serena MCPがNDFから分離済みであること）
タスク6 (ルートファイル変更)        ← タスク1完了後（marketplace.jsonにmcp-serena追加）
                                    ← タスク4完了後（ルートCLAUDE.ndf.md削除はタスク4の方針確定後）
```

---

## 実装順序

### フェーズA: 独立タスク（並列実行可能）

以下4タスクは相互依存なし。並列実行推奨。

#### タスク1: mcp-serenaプラグイン新規作成

**目的**: Serena MCPをNDFから分離し、独立プラグインとして作成

**ファイル操作**:
| 操作 | ファイル |
|------|---------|
| 作成 | `plugins/mcp-serena/.claude-plugin/plugin.json` |
| 作成 | `plugins/mcp-serena/.mcp.json` |
| 作成 | `plugins/mcp-serena/hooks/hooks.json` |
| 作成 | `plugins/mcp-serena/docs/serena-guide.md` |
| 作成 | `plugins/mcp-serena/README.md` |

**詳細**:
- `plugin.json`: name=mcp-serena, version=1.0.0。既存MCPプラグイン（mcp-bigqueryなど）の構造に準拠
- `.mcp.json`: NDF `.mcp.json`からserenaセクションのみ抽出。codexは含めない
- `hooks/hooks.json`: SessionStartフックで `activate_project` + `check_onboarding_performed` を実行
- `docs/serena-guide.md`: シンボル検索、リファクタリングコマンドのリファレンス。memory機能は記載しない
- スキルなし。memory機能は使用禁止。コード操作専用

**参考**: 既存MCPプラグインの構造パターン
```
plugins/mcp-bigquery/
├── .claude-plugin/plugin.json   # name, version, description, author, keywords のみ
├── .mcp.json                    # MCPサーバー定義
└── README.md
```

#### タスク2: ndf-policiesスキル追加

**目的**: CLAUDE.ndf.mdのポリシー部分をmodel-invokedスキルに移行

**ファイル操作**:
| 操作 | ファイル |
|------|---------|
| 作成 | `plugins/ndf/skills/ndf-policies/SKILL.md` |
| 編集 | `plugins/ndf/.claude-plugin/plugin.json` (skills配列に追加) |

**詳細**:
- `user-invocable: false`, `disable-model-invocation: false` で常時コンテキスト注入
- descriptionにポリシーサマリーを記載（Claudeが自動で読み込むトリガー）
- SKILL.md本文に以下のポリシーを記載:
  - 応答・ドキュメント・コミットメッセージは日本語
  - mainブランチへの直接push/merge禁止
  - commit/push/PR mergeはユーザー確認後
  - コンテキスト節約（ファイル全体を読む前にSerenaのシンボル概要確認）
  - 複雑タスクはndf:directorに委譲
  - 専門タスクは対応エージェントに直接委譲
  - 知識はdocs/に、手順はskills/に配置（AGENTS.mdを肥大化させない）

**注意**: plugin.jsonのバージョン更新はタスク4でまとめて行う

#### タスク3: /ndf:cleanupスキル追加

**目的**: CLAUDE.ndf.mdの後始末用スラッシュコマンド

**ファイル操作**:
| 操作 | ファイル |
|------|---------|
| 作成 | `plugins/ndf/skills/cleanup/SKILL.md` |
| 編集 | `plugins/ndf/.claude-plugin/plugin.json` (skills配列に追加) |

**詳細**:
- `user-invocable: true`, `disable-model-invocation: true`（手動呼出のみ）
- 処理内容:
  1. プロジェクトルートの`CLAUDE.ndf.md`検出 → 削除
  2. `CLAUDE.md`から`@CLAUDE.ndf.md`行を削除
  3. `AGENTS.md`から`@CLAUDE.ndf.md`行を削除（存在する場合）
  4. `~/.claude/CLAUDE.ndf.md`検出 → 削除
  5. `~/.claude/CLAUDE.md`から`@CLAUDE.ndf.md`行を削除
  6. 結果報告
- allowed-tools: Bash, Read, Edit, Write

**注意**: plugin.jsonのバージョン更新はタスク4でまとめて行う

#### タスク5: MCPプラグインにdocs/guide.md追加（5件）

**目的**: 各MCPプラグインに使い方ガイドを追加

**ファイル操作**:
| 操作 | ファイル |
|------|---------|
| 作成 | `plugins/mcp-bigquery/docs/bigquery-guide.md` |
| 作成 | `plugins/mcp-dbhub/docs/dbhub-guide.md` |
| 作成 | `plugins/mcp-markitdown/docs/markitdown-guide.md` |
| 作成 | `plugins/mcp-chrome-devtools/docs/chrome-devtools-guide.md` |
| 作成 | `plugins/mcp-aws-docs/docs/aws-docs-guide.md` |

**詳細**:
- スキルではなくドキュメントとして配置
- 各プラグインの.mcp.jsonとREADME.mdを参考に、具体的なツール名・使い方を記載
- 既存のdocs/ディレクトリは存在しないため新規作成が必要

---

### フェーズB: NDF既存資産の廃止・変更（フェーズA完了後）

#### タスク4: NDF既存資産の廃止・変更

**前提条件**: タスク1完了（Serena MCPが独立プラグインとして存在すること）

**目的**: memory系スキル削除、Serena MCP分離、inject仕組み廃止

**ファイル操作**:
| 操作 | ファイル |
|------|---------|
| 削除 | `plugins/ndf/skills/serena/SKILL.md` (ディレクトリごと) |
| 削除 | `plugins/ndf/skills/memory-handling/SKILL.md` (ディレクトリごと) |
| 削除 | `plugins/ndf/skills/serena-memory-strategy/SKILL.md` (ディレクトリごと) |
| 削除 | `plugins/ndf/skills/mem-capture/SKILL.md` (ディレクトリごと) |
| 削除 | `plugins/ndf/skills/mem-review/SKILL.md` (ディレクトリごと) |
| 削除 | `plugins/ndf/scripts/inject-plugin-guide.js` |
| 削除 | `plugins/ndf/CLAUDE.ndf.md` |
| 編集 | `plugins/ndf/.mcp.json` → serenaセクション削除（codexのみ残す） |
| 編集 | `plugins/ndf/hooks/hooks.json` → SessionStartのinjectフック削除、CLAUDE.ndf.md検出警告フックに変更 |
| 編集 | `plugins/ndf/.claude-plugin/plugin.json` → skills配列から5個削除、ndf-policies追加、cleanup追加、バージョン更新(3.0.0) |

**plugin.json skills配列の変更（差分）**:
```
削除:
  - "./skills/serena"
  - "./skills/mem-review"
  - "./skills/mem-capture"
  - "./skills/memory-handling"
  - "./skills/serena-memory-strategy"

追加（タスク2,3で作成済み）:
  + "./skills/ndf-policies"
  + "./skills/cleanup"
```

**hooks/hooks.json変更内容**:
- SessionStartの`inject-plugin-guide.js`呼出を削除
- 代わりにCLAUDE.ndf.md検出時の警告メッセージを出すフックに変更
  （例: 「CLAUDE.ndf.mdは廃止されました。/ndf:cleanupを実行してください」）
- Stopフック（slack-notify.js）はそのまま維持

**.mcp.json変更内容**:
```json
// 変更前: serena + codex の2サーバー
// 変更後: codex のみ
{
  "mcpServers": {
    "codex": { ... }  // 既存のまま
  }
}
```

**バージョン**: 2.9.0 → 3.0.0（破壊的変更: memory系スキル削除、Serena MCP分離）

---

### フェーズC: ルートファイル変更（フェーズA・B完了後）

#### タスク6: ルートファイル変更

**前提条件**: タスク1完了（mcp-serena存在）、タスク4完了（NDF側のCLAUDE.ndf.md削除済み）

**ファイル操作**:
| 操作 | ファイル |
|------|---------|
| 削除 | `CLAUDE.ndf.md` (ルート、544行) |
| 編集 | `.claude-plugin/marketplace.json` → pluginsにmcp-serenaエントリ追加 |
| 編集 | `CLAUDE.md` → `@CLAUDE.ndf.md`参照行を削除、Serena関連の記述をmcp-serenaプラグイン参照に変更 |

**marketplace.json追加エントリ**:
```json
{
  "name": "mcp-serena",
  "source": "./plugins/mcp-serena",
  "description": "Serena MCP server for semantic code intelligence (symbol search, refactoring)"
}
```

**CLAUDE.md変更箇所**:
- 155行目の`@CLAUDE.ndf.md`行を削除
- Serena MCPの使用説明をmcp-serenaプラグインへの参照に書き換え
- memory関連の記述を削除または更新

---

## 並列実行まとめ

```
時間軸 →

フェーズA（並列）:
  [タスク1: mcp-serena作成]
  [タスク2: ndf-policiesスキル]
  [タスク3: /ndf:cleanupスキル]
  [タスク5: MCPプラグインdocs追加]

フェーズB（タスク1完了後）:
  [タスク4: NDF既存資産の廃止・変更]

フェーズC（タスク4完了後）:
  [タスク6: ルートファイル変更]
```

**推奨エージェント**: すべて `ndf:corder`（ファイル作成・編集・削除のコーディングタスク）

---

## 注意点・リスク

### 高リスク

1. **plugin.jsonの競合**: タスク2,3,4がすべてplugin.jsonを編集する。タスク2,3はskills配列への追加のみだが、タスク4でskills配列の大幅変更+バージョン更新を行う。**対策**: タスク2,3ではplugin.jsonのバージョンは変更せず、タスク4でまとめてplugin.json全体を更新する。

2. **CLAUDE.ndf.md二重削除**: タスク4で`plugins/ndf/CLAUDE.ndf.md`を、タスク6で`CLAUDE.ndf.md`（ルート）を削除する。別ファイルなので問題ないが、混同に注意。

### 中リスク

3. **hooks.json変更の検証**: CLAUDE.ndf.md検出警告フックの実装方法は要検討。シェルコマンドで`[ -f CLAUDE.ndf.md ]`を検出し警告メッセージを出力する形が現実的。

4. **既存ユーザーへの影響**: mcp-serenaを別途インストールしないとSerena MCPが使えなくなる。/ndf:cleanupの実行案内が必要。

5. **CLAUDE.md（ルート）の編集範囲**: 155行目の`@CLAUDE.ndf.md`削除は単純だが、Serena関連の記述書き換え範囲はタスク実行時に確認が必要。

### 低リスク

6. **docs/ディレクトリ作成**: 5つのMCPプラグインにdocs/を新規作成する。既存ファイルとの衝突なし。

7. **scripts/inject-plugin-guide.js削除**: slack-notify.jsは残すため、scripts/ディレクトリ自体は削除しない。

---

## 復帰情報

各タスクは独立性が高いため、途中停止しても個別タスク単位で再開可能。
plugin.jsonの最終更新はタスク4に集約しているため、タスク4実行時にタスク2,3の完了状態を確認すること。
