# Anthropic公式Skillsのインストール手順

Anthropicは `anthropics/skills` リポジトリで高品質なSkillを公開しています。本ドキュメントはNDFプラグイン利用者向けに、**自動インストーラ** と **手動インストール** の両方を案内します。

調査日: 2026-04-23
参照: https://github.com/anthropics/skills

## クイックスタート（推奨）

NDFプラグイン同梱のインストーラを使う方法:

```bash
# 利用可能Skill一覧を表示（ライセンス分類付き）
bash plugins/ndf/scripts/install-official-skills.sh --list

# 必要なSkillをユーザー領域（~/.claude/skills/）にインストール
bash plugins/ndf/scripts/install-official-skills.sh docx pptx xlsx

# プロジェクト .claude/skills/ に配置する場合
bash plugins/ndf/scripts/install-official-skills.sh --scope project pdf

# 全Skillをインストール
bash plugins/ndf/scripts/install-official-skills.sh --all

# 公式リポジトリを最新化
bash plugins/ndf/scripts/install-official-skills.sh --update
```

### インストーラの動作

1. 公式リポジトリを `~/.cache/anthropic-skills/` にshallow clone（初回のみ）
2. 指定Skillのディレクトリへ **シンボリックリンク** を作成
3. 重複時はユーザー確認後に上書き

リンク方式のため、`--update` で最新化すると全Skillが同時に追従します。

## ライセンス別の分類（2026-04-23時点）

**Skill単位でライセンスが異なる**ため、必ず各Skillの `LICENSE.txt` を確認してください。`--list` オプションでも表示されます。

### Apache License 2.0（再配布・派生物OK）

| Skill | 用途 |
|---|---|
| `mcp-builder` | MCPサーバー雛形生成（Python FastMCP / Node TypeScript SDK） |
| `skill-creator` | 新規Skill作成ガイド |
| `frontend-design` | フロントエンド設計 |
| `webapp-testing` | Playwrightでのテスト |
| `web-artifacts-builder` | HTML/Reactアプリ生成 |
| `claude-api` | Claude API / SDK開発 |
| `theme-factory` | テーマ生成 |
| `canvas-design` | キャンバスデザイン |
| `algorithmic-art` | アルゴリズム系アート生成 |
| `brand-guidelines` | ブランドガイド |
| `internal-comms` | 社内コミュニケーション |
| `slack-gif-creator` | Slack GIF作成 |

### プロプライエタリ（再配布・派生物禁止）

| Skill | 用途 |
|---|---|
| `docx` | Word文書の作成・編集 |
| `pptx` | PowerPoint作成・編集 |
| `xlsx` | Excel作成・編集 |
| `pdf` | PDF処理（読取・生成） |

> **ライセンス表記例**（docx/pptx/xlsx/pdfに含まれる `LICENSE.txt`）:
> 「© 2025 Anthropic, PBC. All rights reserved. これらの資料をAnthropicのサービスから抽出または外部に保持すること、複製、派生物作成を禁止する」

### ライセンス未指定

- `doc-coauthoring`: `LICENSE.txt` なし → 利用前にリポジトリ側で確認推奨

## 手動インストール

インストーラを使わずに個別管理したい場合:

### 方法1: ユーザー領域（全プロジェクトで有効）

```bash
# 公式リポジトリをclone
git clone https://github.com/anthropics/skills.git ~/work/anthropic-skills

# 必要なSkillをシンボリックリンク
mkdir -p ~/.claude/skills
ln -s ~/work/anthropic-skills/skills/docx ~/.claude/skills/docx
ln -s ~/work/anthropic-skills/skills/pptx ~/.claude/skills/pptx
ln -s ~/work/anthropic-skills/skills/xlsx ~/.claude/skills/xlsx
```

### 方法2: プロジェクト単位

```bash
cd /path/to/your/project
mkdir -p .claude/skills
cp -r ~/work/anthropic-skills/skills/docx .claude/skills/
```

## 規約遵守の注意点

- **プロプライエタリSkillは再配布不可**: リポジトリ（社内含む）に含めない、CIの自動セットアップで公式skillsをclone→コピーする構成も規約違反のおそれ
- **Claude Code / Claude.ai 上での利用に限定**: 他製品への取り込み、派生物作成、Anthropicサービス外での保持は禁止
- **各自の環境への一時的インストール**は規約上問題なし（`~/.claude/skills/` や `.claude/skills/` へのシンボリックリンク）
- ビルド成果物として配布する場合は必ずライセンスを再確認

## NDFプラグインとの関係

### NDFが同梱している公式Skill（Apache-2.0のみ）

- `mcp-builder` → `plugins/ndf/skills/mcp-builder/`（LICENSE.txt同梱）

### NDFが提供するインストーラ

- `plugins/ndf/scripts/install-official-skills.sh` → Apache-2.0 / プロプライエタリいずれも個人利用者環境にインストール可能

### NDFに独自実装を作らない方針

以下は公式Skillの利用を推奨し、NDFには独自版を作りません:
- `docx` / `pptx` / `xlsx` / `pdf`: インストーラで個別配置
- その他プロプライエタリSkill: 同上

## Claude Code側での認識確認

インストール後、以下で確認:

- Claude Codeを再起動 or `/plugin reload`
- `Skill.md` のYAMLフロントマターで自動検出される（スラッシュコマンド `/docx` 等）

## 参考

- 公式リポジトリ: https://github.com/anthropics/skills
- Skills公式ドキュメント: https://docs.claude.com/en/docs/claude-code/skills
- NDFインストーラ: `plugins/ndf/scripts/install-official-skills.sh`
- Skillフロントマター仕様: [docs/claude-code-skills-official-reference.md](./claude-code-skills-official-reference.md)
