# PLAN14: NDFプラグイン Kiro CLI対応 企画・設計書

## 1. 概要

NDFプラグイン（v3.1.0）のSkills・Hooks・MCPをKiro CLIでも利用可能にする。
Claude Code向けの既存構造はそのまま維持し、Kiro CLI用の設定ファイルを追加する。

## 2. 現状分析

### 2.1 NDFプラグイン構成（Claude Code向け）

| 要素 | 場所 | 数 |
|------|------|-----|
| Skills | `plugins/ndf/skills/*/SKILL.md` | 24個 |
| Agents | `plugins/ndf/agents/*.md` | 6個 |
| Hooks | `plugins/ndf/hooks/hooks.json` | 2個（SessionStart, Stop） |
| MCP | `plugins/ndf/.mcp.json` | 1個（Codex CLI） |
| Scripts | `plugins/ndf/scripts/slack-notify.js` | 1個 |

### 2.2 Kiro CLI既存設定

| ファイル | 内容 |
|---------|------|
| `KIRO.md` | 開発ガイドライン（コード探索方法等） |
| `.kiro/agents/default.json` | resources: `file://AGENTS.md`, `file://README.md` のみ |

### 2.3 Claude Code vs Kiro CLI 仕様比較

| 機能 | Claude Code | Kiro CLI |
|------|------------|----------|
| Skills定義場所 | `plugin.json` → `skills/*/SKILL.md` | agent JSON → `resources` に `skill://` パス |
| Skillフロントマター | `name`, `description`, `disable-model-invocation`, `user-invocable`, `allowed-tools` 等 | `name`, `description` のみ（他フィールドは無視される） |
| Skillロード | プラグインインストール時に自動 | `skill://` でメタデータ起動時ロード、本文オンデマンド |
| Hook定義場所 | `hooks/hooks.json` | agent JSON → `hooks` フィールド |
| Hookトリガー | `SessionStart`, `Stop` | `agentSpawn`, `userPromptSubmit`, `preToolUse`, `postToolUse`, `stop` |
| Hook入力 | トリガーにより異なる | JSON via STDIN（`hook_event_name`, `cwd`, `assistant_response`等） |
| MCP定義場所 | `.mcp.json` | agent JSON → `mcpServers` フィールド |
| エージェント | `agents/*.md`（plugin.jsonで登録） | `.kiro/agents/*.json`（ファイル名がエージェント名） |

## 3. 移植方針

### 3.1 Skills（24個）

SKILL.mdファイルはClaude CodeとKiro CLIで共通フォーマット（YAMLフロントマター `name` + `description`）のため、**ファイル自体の変更は不要**。

Kiro CLI側では `.kiro/agents/default.json` の `resources` に `skill://` パスを追加する。

**分類と対応方針：**

| 分類 | Skills | Kiro対応 |
|------|--------|---------|
| ワークフロー系 | pr, pr-tests, fix, review, merged, clean, cleanup, deepwiki-transfer, knowledge-reorg | ○ skill://で登録。`disable-model-invocation`はKiroでは無視されるが、SKILL.md本文の手順に従えば同等動作 |
| モデル起動型 | corder-code-templates, corder-test-generation, data-analyst-export, data-analyst-sql-optimization, docker-container-access, git-gh-operations, google-auth, markdown-writing, python-execution, qa-security-scan, researcher-report-templates, scanner-pdf-analysis, skill-development | ○ skill://で登録。descriptionのTriggersキーワードでオンデマンドロード |
| ポリシー注入 | ndf-policies | ○ `file://` で常時ロード（Kiroには`user-invocable: false`相当がないため） |

### 3.2 Hooks

| Claude Code Hook | Kiro CLI対応 | 方針 |
|-----------------|-------------|------|
| `SessionStart` → CLAUDE.ndf.md検出警告 | `agentSpawn` | ○ 同じシェルコマンドを`agentSpawn`フックに設定 |
| `Stop` → slack-notify.js | `stop` | △ 要改修。Kiro CLIのstopフックはSTDINに`assistant_response`を含むJSONを送る。Claude Codeの`transcript_path`ベースとは異なる |

**slack-notify.js改修方針：**

Kiro CLIのstopフックは `{"hook_event_name":"stop","cwd":"...","assistant_response":"..."}` をSTDINで送る。
現行のslack-notify.jsは `transcript_path` からファイルを読んで要約生成する方式。

対応案：
1. STDINのJSONを解析し、`assistant_response` があればそれを直接要約に使用
2. `transcript_path` があれば従来通りファイルから読む（Claude Code互換）
3. どちらもなければフォールバックメッセージ

これにより1つのスクリプトで両方のCLIに対応可能。

### 3.3 MCP（Codex CLI）

agent JSONの `mcpServers` フィールドに移植。構造はほぼ同一。

```json
"mcpServers": {
  "codex": {
    "command": "codex",
    "args": ["mcp-server"],
    "env": {}
  }
}
```

### 3.4 エージェント（6個）

Claude Codeのサブエージェント（`agents/*.md`）はKiro CLIでは直接対応する仕組みがない。
Kiro CLIのエージェントは `.kiro/agents/*.json` で定義するが、Claude Codeの「サブエージェント呼び出し」とは異なる概念。

対応案：
- 各エージェントのMDファイルを `skill://` としてロードし、知識として参照可能にする
- 必要に応じて専門エージェントを `.kiro/agents/` に個別JSON定義（将来課題）

## 4. 実装計画

### Phase 1: Skills + MCP + Hooks（コア機能）

#### 4.1 `.kiro/agents/default.json` の更新

```json
{
  "description": "NDF統合開発エージェント（Kiro CLI用）",
  "resources": [
    "file://AGENTS.md",
    "file://README.md",
    "file://plugins/ndf/skills/ndf-policies/SKILL.md",
    "skill://plugins/ndf/skills/pr/SKILL.md",
    "skill://plugins/ndf/skills/pr-tests/SKILL.md",
    "skill://plugins/ndf/skills/fix/SKILL.md",
    "skill://plugins/ndf/skills/review/SKILL.md",
    "skill://plugins/ndf/skills/merged/SKILL.md",
    "skill://plugins/ndf/skills/clean/SKILL.md",
    "skill://plugins/ndf/skills/cleanup/SKILL.md",
    "skill://plugins/ndf/skills/deepwiki-transfer/SKILL.md",
    "skill://plugins/ndf/skills/knowledge-reorg/SKILL.md",
    "skill://plugins/ndf/skills/corder-code-templates/SKILL.md",
    "skill://plugins/ndf/skills/corder-test-generation/SKILL.md",
    "skill://plugins/ndf/skills/data-analyst-export/SKILL.md",
    "skill://plugins/ndf/skills/data-analyst-sql-optimization/SKILL.md",
    "skill://plugins/ndf/skills/docker-container-access/SKILL.md",
    "skill://plugins/ndf/skills/git-gh-operations/SKILL.md",
    "skill://plugins/ndf/skills/google-auth/SKILL.md",
    "skill://plugins/ndf/skills/markdown-writing/SKILL.md",
    "skill://plugins/ndf/skills/python-execution/SKILL.md",
    "skill://plugins/ndf/skills/qa-security-scan/SKILL.md",
    "skill://plugins/ndf/skills/researcher-report-templates/SKILL.md",
    "skill://plugins/ndf/skills/scanner-pdf-analysis/SKILL.md",
    "skill://plugins/ndf/skills/skill-development/SKILL.md"
  ],
  "hooks": {
    "agentSpawn": [
      {
        "command": "if [ -f \"${PWD}/CLAUDE.ndf.md\" ] || [ -f \"$HOME/.claude/CLAUDE.ndf.md\" ]; then echo '[NDF] CLAUDE.ndf.md が検出されました。廃止済みです。cleanup を実行して削除してください。'; fi"
      }
    ],
    "stop": [
      {
        "command": "node plugins/ndf/scripts/slack-notify.js session_end",
        "timeout_ms": 70000
      }
    ]
  },
  "mcpServers": {
    "codex": {
      "command": "codex",
      "args": ["mcp-server"],
      "env": {}
    }
  }
}
```

#### 4.2 slack-notify.js の改修

`readHookInput()` 関数にKiro CLI対応を追加：

```javascript
// 既存: transcript_pathベース（Claude Code）
// 追加: assistant_responseベース（Kiro CLI）
async function readHookInput() {
  const input = await readStdin();
  const parsed = safeJsonParse(input);

  if (parsed?.hook_event_name === 'stop' && parsed?.assistant_response) {
    // Kiro CLI: assistant_responseを直接使用
    return {
      assistantResponse: parsed.assistant_response,
      transcriptPath: null,
      stopHookActive: true
    };
  }

  // Claude Code: 従来のtranscript_pathベース
  return {
    assistantResponse: null,
    transcriptPath: parsed?.transcript_path || null,
    stopHookActive: !!parsed?.stop_hook_active
  };
}
```

`generateSummary()` にも分岐を追加し、`assistantResponse` がある場合はファイル読み込みをスキップして直接要約プロンプトに渡す。

### Phase 2: インストーラースクリプト

#### 4.3 `scripts/install-kiro.sh`（実装済み）

`plugin.json` のskills一覧から `.kiro/agents/default.json` を動的に生成するインストーラー。

**使い方：**

```bash
# 基本（Skills + agentSpawnフックのみ）
bash scripts/install-kiro.sh

# Slack通知も有効化
bash scripts/install-kiro.sh --with-slack

# 全部入り（Slack + Codex MCP）
bash scripts/install-kiro.sh --with-slack --with-codex
```

**動作内容：**

1. `plugins/ndf/.claude-plugin/plugin.json` からskills一覧を読み取り
2. 各skillの `SKILL.md` 存在確認（なければスキップ）
3. `ndf-policies` → `file://`（常時ロード）、他 → `skill://`（オンデマンド）で登録
4. `agentSpawn` フック（CLAUDE.ndf.md廃止警告）を設定
5. `--with-slack` 指定時: `stop` フック（slack-notify.js）を追加
6. `--with-codex` 指定時: `mcpServers` にCodex CLIを追加
7. 既存の `default.json` は `.bak` にバックアップ
8. 生成後 python3 があればJSON整形

**設計判断：**

| 判断 | 理由 |
|------|------|
| plugin.jsonから動的生成 | skillの追加・削除時にインストーラー修正不要 |
| Slack/Codexはオプトイン | 環境依存のため。未設定で起動エラーを防ぐ |
| `name` フィールド必須 | Kiro CLIのagent configスキーマ要件（`kiro-cli agent validate` で検証済み） |

### Phase 3: インストールマニュアル

#### 4.4 Kiro CLI向けインストール手順（README.mdまたは別ドキュメントに追記）

```markdown
## Kiro CLIでの利用

### 前提条件
- Kiro CLI がインストール済み
- Node.js（slack-notify用、オプション）
- codex CLI（MCP用、オプション）

### セットアップ

1. リポジトリをクローン
2. インストーラーを実行：
   bash scripts/install-kiro.sh              # 基本
   bash scripts/install-kiro.sh --with-slack  # Slack通知あり
   bash scripts/install-kiro.sh --with-slack --with-codex  # 全部入り
3. Slack通知を使う場合は `.env` に以下を設定：
   - SLACK_CHANNEL_ID
   - SLACK_BOT_TOKEN
   - SLACK_USER_MENTION（オプション）
4. Kiro CLIを起動：kiro-cli chat

### 利用方法
- スキルはdescriptionのキーワードに基づいて自動的に参照されます
- ワークフロー系スキル（pr, fix, review等）は手動で指示してください
  例: 「prスキルの手順に従ってPRを作成して」

### 再インストール・更新
plugin.jsonにskillが追加された場合、再度インストーラーを実行してください。
既存設定は .bak にバックアップされます。
```

## 5. 制約事項・注意点

| 項目 | 詳細 |
|------|------|
| `disable-model-invocation` | Kiro CLIでは未サポート。ワークフロー系スキルもモデルが自動参照する可能性あり |
| `user-invocable: false` | Kiro CLIでは未サポート。ndf-policiesは`file://`で常時ロードして代替 |
| `allowed-tools` | Kiro CLIのskillフロントマターでは未サポート。agent JSONの`tools`/`allowedTools`で制御 |
| `context: fork` | Kiro CLIでは未サポート。サブエージェント実行は`/spawn`や`subagent`ツールで代替 |
| サブエージェント | Claude Codeの`agents/*.md`はKiro CLIのエージェントシステムとは異なる。将来課題 |
| `${CLAUDE_PLUGIN_ROOT}` | Kiro CLIでは使えない。スクリプトパスは相対パスまたは絶対パスで指定 |
| stopフック入力形式 | Claude Code: transcript_path / Kiro CLI: assistant_response。スクリプト側で両対応必要 |

## 6. ファイル変更一覧

| ファイル | 変更内容 |
|---------|---------|
| `scripts/install-kiro.sh` | 新規作成。plugin.jsonからdefault.jsonを動的生成するインストーラー |
| `.kiro/agents/default.json` | インストーラーにより生成。Skills/Hooks/MCP設定 |
| `plugins/ndf/scripts/slack-notify.js` | Kiro CLI stopフック入力形式に対応 |
| `KIRO.md` または `plugins/ndf/README.md` | Kiro CLIインストール手順を追記 |

## 7. 今後の拡張（将来課題）

- 専門エージェント（director, data-analyst等）のKiro CLI用JSON定義
- `skill://` のglob対応（`skill://plugins/ndf/skills/**/SKILL.md`）による設定簡素化
- Kiro CLI固有機能（`/plan`, `/spawn`）を活用した新ワークフロー
