---
name: deepwiki-transfer
description: |
  DeepWiki（Devin MCP）のドキュメント内容を対象リポジトリのディレクトリにMarkdownファイルとして転載する。セクション構成を維持し、GFM準拠・日本語変換まで自動処理する。

  This skill provides DeepWiki content transfer capabilities:
  - DeepWikiコンテンツの一括取得（公開/プライベートリポジトリ対応）
  - セクション番号付きファイル分割
  - GFM準拠の自動修正
  - 日本語翻訳（オプション）

  Triggers: "deepwiki transfer", "deepwiki転載", "wiki転載", "リポジトリドキュメント取得", "DeepWikiからMarkdown", "transfer wiki contents"
disable-model-invocation: true
user-invocable: true
allowed-tools:
  - Bash
  - Read
  - Write
  - Edit
  - Task
---

# DeepWiki コンテンツ転載スキル

DeepWikiのドキュメントをリポジトリ内のディレクトリに1ページ1ファイルのMarkdownとして転載し、PRを作成する。

## 必要な入力

- **対象リポジトリ**: `owner/repo` 形式（例: `facebook/react`）
- **出力先ディレクトリ**: リポジトリ内のパス（例: `deepWiki/`）
- **出力言語**: ファイル名および内容の出力言語（デフォルト: 日本語）
- **ベースブランチ**: PRのマージ先（デフォルト: `main`）

## スクリプトのパス

本スキルのスクリプトは `$CLAUDE_PLUGIN_ROOT/skills/deepwiki-transfer/scripts/` に配置されている。

```bash
# スクリプトディレクトリの確認
SCRIPT_DIR="$CLAUDE_PLUGIN_ROOT/skills/deepwiki-transfer/scripts"
ls "$SCRIPT_DIR"
```

## 処理手順

### Phase A: DeepWikiコンテンツの取得とファイル化

`scripts/fetch_wiki.py` を使って MCP サーバーに直接HTTPリクエストを送信し、全コンテンツを一時ファイルに保存する。LLMコンテキストを経由しないため、130万文字超のレスポンスも完全に保存できる。

```bash
# 公開リポジトリ（認証不要）
python "$SCRIPT_DIR/fetch_wiki.py" \
  --repo owner/repo \
  --output /tmp/deepwiki_raw.md \
  --public

# プライベートリポジトリ（Devin APIキー必要）
python "$SCRIPT_DIR/fetch_wiki.py" \
  --repo owner/repo \
  --output /tmp/deepwiki_raw.md

# Wiki構造も取得（セクション順序の決定に使用）
python "$SCRIPT_DIR/fetch_wiki.py" \
  --repo owner/repo \
  --output /tmp/deepwiki_structure.md \
  --tool read_wiki_structure \
  --public
```

プライベートリポジトリの場合、環境変数 `DEVIN_API_KEY` にDevin APIキーを設定しておくこと。

### Phase B: 機械的ファイル分割＆リネーム

`scripts/split_pages.py` を使い、一時ファイルを `# Page:` マーカーで分割し、セクション番号prefix付きファイル名で出力する。

```bash
# まず dry-run で確認
python "$SCRIPT_DIR/split_pages.py" \
  /tmp/deepwiki_raw.md ./deepWiki/ \
  --structure /tmp/deepwiki_structure.md \
  --dry-run

# 問題なければ実行
python "$SCRIPT_DIR/split_pages.py" \
  /tmp/deepwiki_raw.md ./deepWiki/ \
  --structure /tmp/deepwiki_structure.md
```

ファイル名の命名規則:
- トップレベル: `XX_Title.md`（例: `01_Overview.md`）
- サブセクション: `XX_Y_Title.md`（例: `01_1_System_Architecture.md`）
- ソート順がセクション順と一致すること

**この時点では内容を一切変更しない。**

### Phase C: 1ページずつGFM形式変換

`scripts/validate_gfm.py` で自動修正した後、各ファイルを1ページずつ確認・修正する。

```bash
# 自動修正
python "$SCRIPT_DIR/validate_gfm.py" ./deepWiki/ --verbose

# 検証のみ
python "$SCRIPT_DIR/validate_gfm.py" ./deepWiki/ --check-only
```

自動修正の範囲:
- コードブロックの言語指定（``` → ```mermaid, ```php, ```sql 等）
- 見出し前後の空行挿入
- 末尾改行の統一

自動修正後、各ファイルを1ページずつ開いて以下を確認:
- コードブロックの言語指定が正しいか（自動推定が誤っている場合は手動修正）
- テーブルのパイプ区切りやアライメント行が正しいか
- リンクや画像参照の構文が正しいか
- 見出しの階層が適切か

**内容の意味を変えず、GFM上の記法修正のみ行う。言語変換はこのフェーズでは行わない。**

### Phase D: 1ページずつ言語変換（出力言語が原文と異なる場合のみ）

ユーザー指定の出力言語が原文の言語と異なる場合、各ファイルを1ページずつ処理する:

1. **ファイル名**: タイトル部分を指定言語に翻訳してリネーム（prefixの `XX_` / `XX_Y_` は変更しない）
2. **ファイル内容**: 本文を指定言語に翻訳する。コードブロック内のコード、コマンド、変数名等は翻訳しない

### Phase E: バリデーションとPR作成

1. 出力ディレクトリの `ls` でファイル一覧を表示し、ファイル数とセクション番号の整合性を確認
2. 数件をランダムに選び、DeepWiki原文と照合して内容が一致していることを確認
3. 変更をコミットしてプッシュ
4. PRを作成する。`.github/pull_request_template.md` がある場合はそのテンプレートに沿ってPR説明を作成

## 前提条件

- `requests` ライブラリ（`pip install requests`）
- 公開リポジトリ: 認証不要
- プライベートリポジトリ: 環境変数 `DEVIN_API_KEY` にDevin APIキーを設定

## 禁止事項

- **Phase B完了時点では原文を一字一句そのまま保持すること。要約・省略・改変してはならない**
- Phase B・Cの時点では翻訳してはならない。言語変換はPhase Dでのみ行う
- 原文の内容を「理解して書き直す」のではなく、スクリプトで機械的に処理すること
- 出力先ディレクトリ以外のファイルを変更してはならない

## 注意事項

- DeepWikiのコンテンツは通常**英語**で生成される。日本語出力が指定されている場合、Phase Dで翻訳する
- セクション番号が10以上でも0埋め2桁にすることでソート順を維持（`01_`, `02_`, ..., `10_`）
- `fetch_wiki.py` は MCP サーバーに直接HTTPリクエストを送信するため、`requests` ライブラリが必要
- 既存ファイルがある場合は内容を比較し、差分がある部分のみ更新する
