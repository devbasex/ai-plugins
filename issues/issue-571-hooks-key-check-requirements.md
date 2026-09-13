# #571: hooks 定義の未知キーを継続的統合で落とす — 要求と受け入れ条件

**hooks 定義をランタイムが警告付きで読む状態を、Pull Request の `runtime-smoke` で落とす。**
継続的統合に検査を足し、Pull Request の合否が変わる。そのためモードは `standard` である。
設計は [issue-571-hooks-key-check-design.md](issue-571-hooks-key-check-design.md) にある。

## 受け入れ条件

### Claude Code

- [ ] `plugins/ndf/hooks/claude.json` の `hooks.PreToolUse[0]` へ `"description"` を戻した差分で、
      `runtime-smoke (claude)` が失敗する
- [ ] `plugins/mcp/mcp-serena/hooks/hooks.json` の `hooks.SessionStart[0]` へ `"description"` を
      戻した差分で、`runtime-smoke (claude)` が失敗する
- [ ] 失敗したとき、`smoke.log` に Claude Code の警告の行（プラグイン名と階層を含む）が出る
- [ ] Claude Code が hooks 定義を持つプラグインのどれかを読まなかったとき（ログの形が変わった
      場合を含む）、`runtime-smoke (claude)` が失敗する
- [ ] 陽性対照を読ませて警告が出ないとき、`runtime-smoke (claude)` が失敗する

### Codex

- [ ] `plugins/ndf/hooks/codex.json` の `type` を読めない値（例: `cmd`）にした差分で、
      `runtime-smoke (codex)` が失敗する
- [ ] 失敗したとき、`smoke.log` に Codex の警告の文（`failed to parse plugin hooks config`）が出る
- [ ] Codex の `hooks/list` に、hooks 定義を持つプラグインのどれかが現れないとき、
      `runtime-smoke (codex)` が失敗する
- [ ] 陽性対照を読ませて警告が出ないとき、`runtime-smoke (codex)` が失敗する

### 共通

- [ ] この変更を載せた `develop` で `runtime-smoke (claude)` と `runtime-smoke (codex)` が通る
- [ ] 検査が認証情報を使わない（`--with-secrets=off` のまま通る）
- [ ] 検査が利用者の設定ディレクトリと、同じ実行の他の検査が使う設定ディレクトリに書き込まない
- [ ] 検査の対象のプラグインをファイルに列挙していない（hooks 定義を持つプラグインを実行時に
      見つける）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 検査が `runtime-smoke` の 1 ランタイムあたりの所要時間を 30 秒より多く延ばさない |
| 運用・保守性 | 失敗の出力だけで、どのプラグインのどの定義が原因かを特定できる |
| システム環境 | ランタイムの版を固定しない。新しい版が報告を変えたときは、陽性対照が失敗として知らせる |

## 対象範囲

含む:

| ランタイム | 対象の hooks 定義 |
| --- | --- |
| Claude Code | `plugins/ndf/hooks/claude.json`、`plugins/mcp/mcp-serena/hooks/hooks.json`、`plugins/mcp/mcp-playwright/hooks/hooks.json` |
| Codex | `plugins/ndf/hooks/codex.json`、上の 2 つの `hooks/hooks.json` |

いずれも 2026-09-13 の `develop` で `git ls-files` により実在を確かめた。

- 今後 hooks 定義を持つプラグインが増えたときに、検査の対象へ自動で入ること
- 検査がランタイムの報告を受け取れなかったときに、通過ではなく失敗にすること

含まない:

- `plugins/ndf/dev.agy/hooks.json`。agy はプラグイン配下の `hooks.json` を読まず、
  `install-hooks.sh` が利用者の設定へ差し込む（`AGENTS.md`）。起動時に警告を出す経路が
  この変更の対象と異なる
- Kiro CLI。hooks 定義は `build-runtime-plugins.sh` がエージェント定義へ変換してから読ませる
  ため、元の定義のキーを Kiro は見ない
- hooks 定義以外の設定（`.mcp.json`・`plugin.json`）の未知キー
- ランタイムが報告しない未知キー（Claude Code はコマンド階層の未知キーを報告しない。
  codex-cli 0.153.4 / 0.154.0 はどの階層の未知キーも報告しない）
- `runtime-smoke` の失敗理由が継続的統合の画面に出ず、成果物の `smoke.log` にしか残らない
  こと（検査を足しても変わらない既存の性質。#577 として起票した）
- 型・クラスの構造。変更はシェルスクリプトと継続的統合の手順だけで、型を持たないため
  設計文書にクラス図を置かない

## 前提

- 前提 1: 継続的統合の `runtime-smoke` は、実行のたびにランタイムの最新版を導入する。
  `Containerfile.claude` は `npm install -g @anthropic-ai/claude-code`、`Containerfile.codex` は
  公式の導入スクリプトで、どちらも版を固定しない。2026-09-13 の実行では Claude Code 2.1.270 /
  codex-cli 0.154.0 だった
- 前提 2: 検査は認証情報を持たない実行（`--with-secrets=off`）で成り立たせる。Pull Request の
  必須の検査はこの条件で走る
- 前提 3: 「受け取らないキー」の判定は、ランタイム自身の報告に従う。報告の無いキーは
  受け取るものとして扱う

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない。配布物に手を入れない |
| データ | なし |
| 既存の振る舞い | `runtime-smoke (claude)` / `(codex)` の合否。今の `develop` の定義は警告を出さないため、今は通る |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 手元での実行 | `bash scripts/runtime-smoke-test.sh --runtime claude` と `--runtime codex` |
| 失敗の再現 | 受け入れ条件の差分を当てた作業ツリーで同じコマンドを実行し、終了コードと `tmp/runtime-smoke/<runtime>/smoke.log` を見る |
| 継続的統合 | Pull Request の `runtime-smoke (claude)` / `runtime-smoke (codex)` の結果 |
| 既存の検査 | `bash scripts/validate-runtime-plugins.sh`、`claude plugin validate .` |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 検査は `tests/runtime-smoke/assertions/` に置き、各ランタイムのアダプタ（`tests/runtime-smoke/adapters/`）から呼ぶ |
| コーディング規約 | 既存のアサーションに合わせ `set -euo pipefail`。`find ... \| grep -q` を使わない（`assert-plugin-files.sh` の注記） |
| テスト戦略 | 陽性対照を検査の中に持ち、検査自身が報告を受け取れることを毎回確かめる。受け入れ条件の差分による失敗は、実装の Pull Request で手元の実行により 1 度確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 手元で `runtime-smoke-test.sh` を実行して結果を確かめる |
| 確認してから行う | 必須の検査の名前を変えること、ワークフローの起動条件を変えること |
| 行わない | hooks 定義そのものの書き換え、ランタイムの版の固定 |

## 依頼（原文）

> hooks 定義（`plugins/ndf/hooks/claude.json` / `hooks/codex.json` / `plugins/mcp/*/hooks/hooks.json`）が、各ランタイムの受け取るキーだけで書かれていることを、継続的統合で落とせるようにする。
>
> 候補は 2 つある。どちらを採るかは着手時に決める。
>
> 1. **実物に読ませる**: `runtime-smoke (claude)` で `--debug-file` を付けて起動し、`unknown key` の行があれば落とす。Claude Code が受け取るキーの一覧を持たずに済むが、認証の無い CI で起動まで届くかを先に確かめる
> 2. **一覧と突き合わせる**: マッチャーグループ（`hooks.<イベント>[n]`）に置けるキーを `matcher` / `hooks` に限る検査を `validate-runtime-plugins.sh` へ足す。一覧を手で保つ必要があり、ランタイムの変更に遅れる
>
> ## 受け入れ条件
>
> - マッチャーグループへ `description` を戻した差分で、継続的統合が落ちる
> - 今の `develop` では通る

背景として、同じ種類の不具合が 2 回、利用者の起動時に見つかった（#36 は Codex、PR #568 は
Claude Code）。PR #568 の時点で `claude plugin validate`・`validate-runtime-plugins.sh`・
継続的統合の 13 件はすべて通っていた。

## 用語

| 用語 | この文書での意味 |
| --- | --- |
| hooks 定義 | プラグインが持つ、フックのイベントとコマンドを並べた JSON ファイル |
| マッチャーグループ | `hooks.<イベント>[n]` の階層。`matcher` と `hooks` を持つ |
| 読み込みの報告 | ランタイムが hooks 定義を読んだときに出す警告と誤り。Claude Code はデバッグログ、Codex は `hooks/list` の応答に出す |
| 陽性対照 | 必ず報告が出るはずの壊れた定義。これを読ませて報告が出ることを確かめてから、本物の定義を判定する |

