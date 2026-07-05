---
name: official-skills-autoloader
description: "Install and use official document Skills on demand."
when_to_use: "Use when user requests Word/Excel/PowerPoint/PDF creation/editing, frontend design, webapp testing, or other tasks handled by Anthropic's official skills collection. Triggers: 'Word作成', 'Excel出力', 'スライド生成', 'PDF作成', '.docx', '.pptx', '.xlsx', '.pdf', 'create docx', 'generate excel', 'make slides', 'create pdf'."
allowed-tools:
  - Bash
  - Read
---

# 公式Skill自動ローダー

ユーザーの要求から必要なAnthropic公式Skillを特定し、未インストールなら自動でインストール→読込して作業を進めます。利用者は**インストール作業を意識する必要がありません**。

## 対応マッピング

| ユーザー要求の例 | 使用するSkill |
|---|---|
| Word / .docx / 文書 / レポート | `docx` |
| PowerPoint / .pptx / スライド / プレゼン | `pptx` |
| Excel / .xlsx / スプレッドシート / 表計算 | `xlsx` |
| PDF 生成 / フォーム / .pdf 作成 | `pdf` |
| フロントエンド設計 / UI設計 | `frontend-design` |
| Playwright / E2Eテスト / Webアプリテスト | `webapp-testing` |
| HTML/Reactアプリ生成 / Artifacts | `web-artifacts-builder` |
| 新規Skill作成 | `skill-creator` |
| Claude API / SDK開発 | `claude-api` |

## 動作手順

### ステップ1: 対象Skillを特定

ユーザーの発話から上記マッピングで対象Skill名を1つ決定。曖昧な場合はユーザーに確認。

### ステップ2: インストール状態を確認

以下のBashコマンドで確認:

```bash
SKILL_NAME="<対象名>"
if [ -d "$HOME/.claude/skills/$SKILL_NAME" ] || [ -L "$HOME/.claude/skills/$SKILL_NAME" ]; then
  echo "INSTALLED"
else
  echo "MISSING"
fi
```

### ステップ3: 未インストールなら自動インストール

```bash
SKILL_NAME="<対象名>"
CACHE_DIR="${XDG_CACHE_HOME:-$HOME/.cache}/anthropic-skills"
USER_SKILLS="$HOME/.claude/skills"

# 初回のみ公式リポジトリをclone
if [ ! -d "$CACHE_DIR/.git" ]; then
  echo "公式Skillリポジトリを取得中..."
  mkdir -p "$(dirname "$CACHE_DIR")"
  git clone --depth 1 https://github.com/anthropics/skills.git "$CACHE_DIR"
fi

# 対象Skillの存在確認
if [ ! -d "$CACHE_DIR/skills/$SKILL_NAME" ]; then
  echo "ERROR: $SKILL_NAME は公式リポジトリに存在しません"
  exit 1
fi

# シンボリックリンク作成
mkdir -p "$USER_SKILLS"
ln -sfn "$CACHE_DIR/skills/$SKILL_NAME" "$USER_SKILLS/$SKILL_NAME"
echo "Installed: $USER_SKILLS/$SKILL_NAME"
```

ユーザーには「公式Skill `<name>` を準備しています...」と一言伝える。

### ステップ4: SKILL.mdを読み込んで実行

```
Read(file_path="$HOME/.claude/skills/<SKILL_NAME>/SKILL.md")
```

読み込んだSKILL.mdの内容を**現在のコンテキストで実行**する。そのSkillが指定する `scripts/` ディレクトリや `reference/` ファイルも必要に応じて読込。

## 注意事項

### ライセンス

- Apache-2.0（mcp-builder, frontend-design, webapp-testing, claude-api 等）: 再配布可
- プロプライエタリ（docx, pptx, xlsx, pdf）: **個人環境での利用のみ**。リポジトリに含めない、社内共有しない

このautoloaderが行うのは**利用者のローカル環境へのインストールのみ**で、再配布には該当しません。

### パス規約

- cache: `~/.cache/anthropic-skills/` （XDG準拠）
- リンク先: `~/.claude/skills/<name>/` （ユーザー領域）
- プロジェクト単位で配置したい場合は `plugins/ndf/scripts/install-official-skills.sh --scope project <name>` を直接実行

### 再読込

同一セッション内では Read したSKILL.mdの内容で作業を完結させます。次回セッション以降はClaude Codeが自動でそのSkillを認識するため、このautoloaderは介入しません。

### 手動管理したい場合

- 一覧表示: `bash plugins/ndf/scripts/install-official-skills.sh --list`
- 更新: `bash plugins/ndf/scripts/install-official-skills.sh --update`
- 明示的なインストール: `bash plugins/ndf/scripts/install-official-skills.sh <name...>`

## エラーハンドリング

| 症状 | 対応 |
|---|---|
| git clone失敗 | ネットワーク・認証を確認。プロキシ環境では HTTP_PROXY 設定を確認 |
| 対象Skillが公式にない | --list で最新の公式一覧を確認、マッピングを更新 |
| 権限エラー | `~/.claude/skills/` の書込権限を確認 |
| 既に別物がある | ユーザーに確認してから上書き |

## 対象外

- 自作Skillの生成（これは `skill-creator` に委譲）
- プロプライエタリSkillのCIへの組込（ライセンス違反）
- NDFプラグイン自体のスキル管理
