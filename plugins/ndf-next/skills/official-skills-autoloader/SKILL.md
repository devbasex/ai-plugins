---
name: official-skills-autoloader
description: "Install an official Anthropic Skill (docx / pptx / xlsx / pdf) on demand, showing the source first. Use when Office or PDF output is needed（Word作成・Excel出力・PDF作成）."
when_to_use: "Claude Code 専用。~/.claude/skills/ へ公式 Skill を取得して読み込む。インストールは同意を得てから実行する（.docx・.pptx・.xlsx・.pdf・MCPサーバーを作りたい・フロントエンド設計）."
allowed-tools:
  - Bash
  - Read
---

# 公式Skill自動ローダー

ユーザーの要求から必要なAnthropic公式Skillを特定し、未インストールなら**同意を得たうえで**インストール→読込して作業を進めます。利用者はインストール手順そのものを調べる必要はありませんが、**外部リポジトリの取得とホームディレクトリへの書き込みは同意なしに行いません**。

## 対応マッピング

| ユーザー要求の例 | 使用するSkill |
|---|---|
| Word / .docx / 文書 / レポート | `docx` |
| 既存の PowerPoint / .pptx を読む・直す | `pptx` |
| Excel / .xlsx / スプレッドシート / 表計算 | `xlsx` |
| PDF 生成 / フォーム / .pdf 作成 | `pdf` |
| フロントエンド設計 / UI設計 | `frontend-design` |
| Playwright / E2Eテスト / Webアプリテスト | `webapp-testing` |
| HTML/Reactアプリ生成 / Artifacts | `web-artifacts-builder` |
| 新規Skill作成 | `skill-creator` |
| Claude API / SDK開発 | `claude-api` |
| MCPサーバー作成 | `mcp-builder` |

**スライド（プレゼン・発表資料）を新しく作る依頼は、公式 Skill を使わない。** `release` の `references/form-slide.md`（生成スクリプトの経路）へ回す。公式の `pptx` は既存の .pptx を読む・直す依頼だけに使う。

## 対応ランタイム

**Claude Code 専用**。インストール先の `~/.claude/skills/` を読むのは Claude Code だけで、Codex は `.agents/skills/`、Kiro CLI は `.kiro/skills/` を読む。両ランタイムでは公式 Skill の自動読込は行われないため、デプロイするとしても Claude Code の manifest に限る。

導入先のランタイムは `plugins/ndf/manifests/claude-skills.txt` のみとする。Codex / Kiro の manifest には載せない。

## インストール前の同意取得（必須）

この Skill は自然文の依頼でも起動する。インストールは**外部のマーケットプレイスの登録**と
**Claude Code のプラグインの導入**を伴い、利用者が明示的に頼んでいない操作になりうる。
**ステップ3 を実行する前に、以下の 4 点を一覧で提示して利用者の同意を得る。同意が得られなければ
インストールを行わず、その Skill を使わない方法で作業を続けるか、作業を中断する。** 同意は `AskUserQuestion` で問い、
`metadata.source` に `ndf-ask:legal`（取得する Skill のライセンスへの同意）を付ける（区分の正本は
`development-workflow` の `references/asking.md`）。

公式 Skill は、Anthropic のマーケットプレイス `anthropic-agent-skills`（GitHub の `anthropics/skills`）が
プラグインとして配る。`docx`・`pptx`・`xlsx`・`pdf` はプラグイン `document-skills`、`claude-api` はプラグイン `claude-api`、ほかはプラグイン `example-skills` にある（取得元の `.claude-plugin/marketplace.json`、2026-11-15 に確認）。

| 提示する項目 | 値 |
|---|---|
| 対象 Skill 名 | ステップ1 で特定した名前（複数なら全件） |
| 取得元 | マーケットプレイス `anthropics/skills`（https://github.com/anthropics/skills ） |
| 入れるプラグイン | `document-skills@anthropic-agent-skills`・`claude-api@anthropic-agent-skills`・`example-skills@anthropic-agent-skills` のどれか |
| 導入の範囲 | `user`（既定。`~/.claude/` の下）。プロジェクトに限るなら `project` |

提示例:

```
公式 Skill `pptx` が未インストールです。インストールしてよろしいですか。
- 取得元: https://github.com/anthropics/skills （マーケットプレイス anthropic-agent-skills）
- 入れるプラグイン: document-skills@anthropic-agent-skills
- 導入の範囲: user
- 対象 Skill: pptx
```

規則:

- 「インストールしてよいですか」だけを尋ねるのは確認にならない。**上記 4 点を必ず示す**
- 利用者が `/ndf11:official-skills-autoloader pptx` のように対象を指定して明示起動した場合や、
  「公式 Skill を入れて」のように依頼自体がインストールを含む場合は、その依頼を同意とみなす。
  それでも取得元・入れるプラグイン・導入の範囲は提示する
- **暗黙起動（「スライドを作って」等）の場合は、提示のうえ明示的な同意を得てから実行する**
- すでにインストール済み（ステップ2 が `INSTALLED`）ならインストールは発生しないため、
  同意取得は不要。ステップ4 へ進む
- ライセンスがプロプライエタリな Skill（`docx` / `pptx` / `xlsx` / `pdf`）では、
  「注意事項 > ライセンス」の制約もあわせて提示する

## 動作手順

### ステップ1: 対象Skillを特定

ユーザーの発話から上記マッピングで対象Skill名を1つ決定し、入れるプラグイン（`document-skills`・`claude-api`・`example-skills` のどれか）を決める。
曖昧な場合は依頼に最も合う 1 つを AI が選び、選んだ理由を `ask.py decide --question` で記録する。

### ステップ2: インストール状態を確認

以下のBashコマンドで確認:

```bash
PLUGIN="<document-skills・claude-api・example-skills のどれか>@anthropic-agent-skills"
if claude plugin list 2>/dev/null | grep -Fq "$PLUGIN"; then
  echo "INSTALLED"
else
  echo "MISSING"
fi
```

### ステップ3: 未インストールならインストール

**「インストール前の同意取得（必須）」を先に実施し、同意を得てからこのコマンドを実行する。**
同意が得られていない状態でこのブロックを実行してはならない。

```bash
PLUGIN="<document-skills・claude-api・example-skills のどれか>@anthropic-agent-skills"
claude plugin marketplace add anthropics/skills
claude plugin install "$PLUGIN"   # プロジェクトに限るなら --scope project を足す
```

同意を得たうえで実行し、ユーザーには「公式Skill `<name>` を準備しています...」と一言伝える。

### ステップ4: SKILL.mdを読み込んで実行

導入したプラグインの Skill は、次のセッションから Claude Code が読み込む。今のセッションでは、導入したプラグインの置き場の
`skills/<対象名>/SKILL.md` を Read で読む。置き場は `claude plugin list` の出力か `~/.claude/plugins/installed_plugins.json` の
`installPath` にある。

読み込んだSKILL.mdの内容を**現在のコンテキストで実行**する。そのSkillが指定する `scripts/` ディレクトリや `reference/` ファイルも必要に応じて読込。

## 注意事項

### ライセンス

- Apache-2.0（mcp-builder, frontend-design, webapp-testing, claude-api 等）: 再頒布可
- プロプライエタリ（docx, pptx, xlsx, pdf）: **個人環境での利用のみ**。リポジトリに含めない、社内共有しない

このautoloaderが行うのは**利用者のローカル環境へのインストールのみ**で、再頒布には該当しません。

### 再読込

同一セッション内では Read したSKILL.mdの内容で作業を完結させます。次回セッション以降はClaude Codeが自動でそのSkillを認識するため、このautoloaderは介入しません。

### 手動管理したい場合

公式 Skill の一覧・更新・削除は Claude Code のプラグインの操作で行う。

- 一覧表示: `claude plugin list`
- 更新: `claude plugin marketplace update anthropic-agent-skills` の後に `claude plugin update <プラグイン>`
- 削除: `claude plugin uninstall <プラグイン>`

## エラーハンドリング

| 症状 | 対応 |
|---|---|
| マーケットプレイスの登録・導入の失敗 | ネットワーク・認証を確認。プロキシ環境では HTTP_PROXY 設定を確認 |
| 対象Skillが公式にない | 導入したプラグインの `skills/` の一覧で最新の公式一覧を確認し、マッピングを更新 |
| 権限エラー | `~/.claude/plugins/` の書込権限を確認 |

## 対象外

- 自作Skillの生成（これは `skill-creator` に委譲）
- プロプライエタリSkillのCIへの組込（ライセンス違反）
- NDFプラグイン自体のスキル管理
