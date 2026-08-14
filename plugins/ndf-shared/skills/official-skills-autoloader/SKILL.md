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
| PowerPoint / .pptx / スライド / プレゼン | `pptx` |
| Excel / .xlsx / スプレッドシート / 表計算 | `xlsx` |
| PDF 生成 / フォーム / .pdf 作成 | `pdf` |
| フロントエンド設計 / UI設計 | `frontend-design` |
| Playwright / E2Eテスト / Webアプリテスト | `webapp-testing` |
| HTML/Reactアプリ生成 / Artifacts | `web-artifacts-builder` |
| 新規Skill作成 | `skill-creator` |
| Claude API / SDK開発 | `claude-api` |
| MCPサーバー作成 | `mcp-builder` |

## 対応ランタイム

**Claude Code 専用**。インストール先の `~/.claude/skills/` を読むのは Claude Code だけで、Codex は `.agents/skills/`、Kiro CLI は `.kiro/skills/` を読む。両ランタイムでは公式 Skill の自動読込は行われないため、配布するとしても Claude Code の manifest に限る。

配布先は `plugins/ndf-shared/manifests/claude-skills.txt` のみとする。Codex / Kiro の manifest には載せない。

## インストール前の同意取得（必須）

この Skill は自然文の依頼でも起動する。インストールは**外部リポジトリの取得**と
**ホームディレクトリ配下への書き込み**を伴い、利用者が明示的に頼んでいない操作になりうる。
**ステップ3 を実行する前に、以下の 4 点を一覧で提示して利用者の同意を得る。同意が得られなければ
インストールを行わず、その Skill を使わない方法で作業を続けるか、作業を中断する。**

| 提示する項目 | 値 |
|---|---|
| 対象 Skill 名 | ステップ1 で特定した名前（複数なら全件） |
| クローン元 URL | `https://github.com/anthropics/skills.git`（`--depth 1`） |
| クローン先 | `${XDG_CACHE_HOME:-$HOME/.cache}/anthropic-skills`（実際に展開したパスを表示する） |
| symlink を張る先 | `$HOME/.claude/skills/<対象 Skill 名>` |

提示例:

```
公式 Skill `pptx` が未インストールです。インストールしてよろしいですか。
- 取得元: https://github.com/anthropics/skills.git (--depth 1)
- 取得先: /home/user/.cache/anthropic-skills
- リンク作成先: /home/user/.claude/skills/pptx
- 対象 Skill: pptx
```

規則:

- 「インストールしてよいですか」だけを尋ねるのは確認にならない。**上記 4 点を必ず示す**
- 利用者が `/ndf:official-skills-autoloader pptx` のように対象を指定して明示起動した場合や、
  「公式 Skill を入れて」のように依頼自体がインストールを含む場合は、その依頼を同意とみなす。
  それでも取得元・取得先・リンク作成先は提示する
- **暗黙起動（「スライドを作って」等）の場合は、提示のうえ明示的な同意を得てから実行する**
- すでにインストール済み（ステップ2 が `INSTALLED`）ならインストールは発生しないため、
  同意取得は不要。ステップ4 へ進む
- ライセンスがプロプライエタリな Skill（`docx` / `pptx` / `xlsx` / `pdf`）では、
  「注意事項 > ライセンス」の制約もあわせて提示する

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

### ステップ3: 未インストールならインストール

**「インストール前の同意取得（必須）」を先に実施し、同意を得てからこのコマンドを実行する。**
同意が得られていない状態でこのブロックを実行してはならない。

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

同意を得たうえで実行し、ユーザーには「公式Skill `<name>` を準備しています...」と一言伝える。

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
- プロジェクト単位で配置したい場合は `bash ${CLAUDE_PLUGIN_ROOT}/scripts/install-official-skills.sh --scope project <name>` を直接実行

### 再読込

同一セッション内では Read したSKILL.mdの内容で作業を完結させます。次回セッション以降はClaude Codeが自動でそのSkillを認識するため、このautoloaderは介入しません。

### 手動管理したい場合

スクリプトはプラグインの配布物に含まれる。`${CLAUDE_PLUGIN_ROOT}` は Claude Code が
インストール済みプラグインのルートに展開する環境変数で、`scripts/` はその直下にある。

- 一覧表示: `bash ${CLAUDE_PLUGIN_ROOT}/scripts/install-official-skills.sh --list`
- 更新: `bash ${CLAUDE_PLUGIN_ROOT}/scripts/install-official-skills.sh --update`
- 明示的なインストール: `bash ${CLAUDE_PLUGIN_ROOT}/scripts/install-official-skills.sh <name...>`

リポジトリを直接 clone して作業している場合は `plugins/ndf-claude/scripts/install-official-skills.sh`
（編集元は `plugins/ndf-shared/scripts/install-official-skills.sh`）を使う。

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
