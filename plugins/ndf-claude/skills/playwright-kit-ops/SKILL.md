---
name: playwright-kit-ops
description: "Run the playwright_kit scripts: project init, page-role classification, one-off a11y / CWV scans, and Drive upload helpers. Use when a playwright_kit script has to be run directly. Triggers: 'init_project.sh', 'classify_page_role.py', 'run_a11y_scan.py', 'upload_evidence.py'"
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

Drive 連携は optional dependency として扱う。`google-auth` skill はどのランタイムの
配布物にも同梱していないため、Drive 系コマンドや `--pwk-drive-folder` を使う場合は
リポジトリ [devbasex/ai-plugins](https://github.com/devbasex/ai-plugins) を clone し、
`GOOGLE_AUTH_SCRIPTS` をその clone 先の `google-auth/scripts` へ設定する。

```bash
# <ai-plugins を clone した先> を実パスに置き換える
export GOOGLE_AUTH_SCRIPTS=<ai-plugins のパス>/plugins/ndf-shared/skills/google-auth/scripts
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

- `/ndf:playwright-planning` — テスト計画 (方法論 + チェックリスト + ワークフロー全体像)
- `/ndf:playwright-authoring` — スクリプト作成と実行 (テストコード / エビデンス / ブラウザ接続)
- `/ndf:playwright-evidence` — 証跡とレポート (report.md / Google Drive 保管)
