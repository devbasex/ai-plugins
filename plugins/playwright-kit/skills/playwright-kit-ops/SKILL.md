---
name: playwright-kit-ops
description: "Run the playwright_kit scripts: init, page-role classification, a11y / CWV scans, Drive upload. Use when running one directly（playwright_kitのスクリプトを実行・a11yスキャン）."
allowed-tools:
  - Read
  - Bash(python *)
  - Bash(uv *)
  - Bash(pytest *)
  - Bash(playwright *)
  - Bash(./scripts/*)
  - Bash(bash *)
  - Bash(chmod *)
---

# playwright_kit 操作エージェント

playwright_kit のスクリプト群を実行してテスト環境のセットアップ・テスト実行・エビデンス管理を行う。

## スクリプト一覧

| スクリプト | 用途 | カテゴリ |
|---|---|---|
| `scripts/init_project.sh` | 利用者プロジェクトに scenario-test ランタイムを埋め込む | セットアップ |
| `scripts/init_project.bat` | 同 (Windows) | セットアップ |
| `scripts/classify_page_role.py` | URL の a11y tree + パターンから page role を自動推定 | テスト計画 |
| `scripts/record_scenario.py` | Playwright codegen で操作を記録しテストコード化 | テスト計画 |
| `scripts/run_a11y_scan.py` | axe-core による単発 accessibility スキャン | 品質 |
| `scripts/check_cwv.py` | Core Web Vitals (LCP/CLS/TTFB) 単発計測 | 品質 |
| `scripts/upload_evidence.py` | エビデンスファイルを Google Drive にアップロード | レポート |
| `scripts/gdrive_upload_dir.py` | ディレクトリごと Drive にバッチアップロード | レポート |
| `scripts/upload_md_as_gdoc.py` | Markdown を Google Doc に変換・アップロード | レポート |
| `scripts/build_gdoc_with_drive_links.py` | Google Doc にエビデンスの Drive リンクを埋め込み | レポート |

## 出力と終了コード

`scripts/*.py` の標準出力は 1 つの JSON である。経過や注意は標準エラーへ書く。
`--output` を取るスクリプトは、結果の本体をそのファイルへ書き、標準出力には書き出し先と件数の要約を返す。
`record_scenario.py` は `--output` を付けないとき codegen が出すコードを標準出力へ流す。
`init_project.sh` / `init_project.bat` は人が読む経過を出す。

| スクリプト | 0 | 1 | 2 |
|---|---|---|---|
| `init_project.sh` / `.bat` | 初期化した (dry-run を含む) | 引数の誤り・必要なコマンドが無い | — |
| `classify_page_role.py` | 分類した (開けなかった URL は要素の `error` に入る) | — | 引数の誤り |
| `record_scenario.py` | codegen の終了コードをそのまま返す | 同左 | playwright CLI が無い・起動できない |
| `run_a11y_scan.py` | 走査した | `--fail-on-violations` で違反が 1 件以上 | 引数の誤り |
| `check_cwv.py` | 計測した | `--fail-on-poor` で poor が 1 件以上 | 引数の誤り |
| `upload_evidence.py` | 上げた | 認証・Drive API の失敗 | 引数の誤り・ファイルが無い |
| `gdrive_upload_dir.py` / `upload_md_as_gdoc.py` | 上げた | 認証・Drive API の失敗 | 引数の誤り |
| `build_gdoc_with_drive_links.py` | Doc を作った | run-id のフォルダが無い・認証・Drive API の失敗 | 引数の誤り |

1 の「認証・Drive API の失敗」は例外で止まり、標準エラーに理由が出る (標準出力の JSON は出ない)。
計画と作成の工程のスクリプトは各 Skill に置く: `playwright-planning/scripts/plan_skeleton.py` (計画書の雛形)、
`playwright-authoring/scripts/lint_scenario.py` (構文木での検査) と `app_ready.sh` (起動の確認)。
終了コードは各 Skill の SKILL.md に書く。

## セットアップ

### プロジェクト初期化

```bash
# SKILL_DIR はこの skill のパス
./scripts/init_project.sh /path/to/your-app

# ディレクトリ名をカスタマイズ
./scripts/init_project.sh /path/to/your-app --runtime-dir e2e

# Windows
scripts\init_project.bat C:\path\to\your-app
```

→ `your-app/scenario-test/` に all-in-one ランタイムが作成され、Skill 非依存で動作する。

### テスト実行

```bash
cd /path/to/your-app
./scenario-test/run.sh                            # 全テスト
./scenario-test/run.sh -k test_admin              # フィルタ
./scenario-test/run.sh --pwk-overlay              # 字幕 + カーソル付き動画
./scenario-test/run.sh --pwk-drive-folder=<ID>    # Drive 自動アップロード
```

Drive 連携は optional dependency として扱う。`google-auth` / `google-drive` skill は
NDF の 4 つのランタイム (Claude Code / Codex / Kiro / agy) すべてへ配布されるが、
playwright-kit とは別のプラグインであるため、置かれる場所は導入したランタイムで変わる。
スクリプトは各ランタイムの標準の導入先・Claude Code のプラグインのキャッシュ
(`~/.claude/plugins/cache/<取得元>/ndf/<版>/` の最新の版)と、この Skill と並ぶ `google-auth/scripts` を探す
(`scripts/_drive_auth.py`)。候補で見つからないときは `GOOGLE_AUTH_SCRIPTS` を
`google-auth/scripts` へ設定する。

```bash
# 例: ai-plugins を clone した先の google-auth を使う
export GOOGLE_AUTH_SCRIPTS=<ai-plugins のパス>/plugins/ndf/skills/google-auth/scripts
cd scenario-test
uv sync --extra drive
```

## テスト計画ツール

```bash
# page role を自動推定
python scripts/classify_page_role.py --url https://example.com/products

# Playwright codegen で操作を記録
python scripts/record_scenario.py https://example.com/login
```

## 品質スキャンツール

```bash
# axe-core 単発スキャン
python scripts/run_a11y_scan.py --url https://example.com

# Core Web Vitals 単発計測
python scripts/check_cwv.py --url https://example.com
```

## エビデンスアップロードツール

```bash
# 単一ファイルを Drive にアップロード
python scripts/upload_evidence.py reports/run-001/test_login/trace.zip \
  --kind trace --parent-folder-id FOLDER_ID

# ディレクトリごとアップロード
python scripts/gdrive_upload_dir.py --local reports/run-001/ --parent FOLDER_ID

# Markdown を Google Doc へ変換
python scripts/upload_md_as_gdoc.py --md reports/run-001/report.md --parent FOLDER_ID

# エビデンスの Drive リンクを埋め込んだ Google Doc を作る
python scripts/build_gdoc_with_drive_links.py \
  --md reports/run-001/report.md --folder FOLDER_ID \
  --run-id run-001 --name "run-001 レポート"
```

`--parent` / `--parent-folder-id` / `--folder` に渡すのは Drive のフォルダ ID。
`upload_evidence.py` の `--parent-folder-id` だけは省略でき、その場合はマイドライブ
直下へ置く。

## パッケージ参照

playwright_kit Python パッケージ本体・templates・tests はこの skill ディレクトリ内に配置されている。

## 関連 Skill

- `/playwright-kit:playwright-planning` — テスト計画 (方法論 + チェックリスト + ワークフロー全体像)
- `/playwright-kit:playwright-authoring` — スクリプト作成と実行 (テストコード / エビデンス / ブラウザ接続)
- `/playwright-kit:playwright-evidence` — 証跡とレポート (report.md / Google Drive 保管)
