# playwright-kit Plugin

Playwright による E2E テストの計画・実装・証跡管理を提供するプラグインです。3 ランタイム分の
配布物を `plugins/playwright-kit/` の 1 ディレクトリにまとめており、Skill の実体は `skills/` の
1 箇所だけです。ランタイム固有のファイルは `.claude-plugin/`（Claude Code）と `dev.kiro/`
（Kiro CLI）に分かれ、Codex はルートの `plugin.json`（Agent Plugins 形式）を読みます。

NDF プラグイン v6.1.0 までは NDF に同梱していました。**Skill 名は変えていない**ため、
`/playwright-` まで打てば従来どおり 4 個が候補に出ます。

## Skill

| Skill | 役割 |
| --- | --- |
| `playwright-planning` | ページ役割を判定し、チェックリストとテスト技法を選ぶ |
| `playwright-authoring` | E2E スクリプトを書き、動画 / trace 付きで実行する |
| `playwright-evidence` | テスト報告書を生成し、証跡を Google Drive へ保管する |
| `playwright-kit-ops` | `playwright_kit` のスクリプト（init、ページ役割分類、a11y / CWV スキャン、Drive アップロード）を実行する |

`playwright-kit-ops` は Python パッケージ本体（`pyproject.toml` / `uv.lock` / `tests/`）を
同梱しており、この 4 個のうち最も大きい配布物です。

## インストール

### Claude Code

```bash
/plugin marketplace add devbasex/ai-plugins
/plugin install playwright-kit@ai-plugins
```

### Codex

```bash
codex plugin marketplace add devbasex/ai-plugins
codex plugin add playwright-kit@ai-plugins
```

### Kiro CLI

Kiro にはプラグイン機構がないため、installer が `.kiro/skills/` へ symlink を張ります。

```bash
# プロジェクトへ導入（既定）
bash plugins/playwright-kit/dev.kiro/install.sh

# ホーム配下へ導入
bash plugins/playwright-kit/dev.kiro/install.sh --scope global

# 書き込みを行わず内容だけ確認
bash plugins/playwright-kit/dev.kiro/install.sh --dry-run
```

NDF の installer と違い、エージェント定義・常時指示・プロンプト・フックは扱いません
（このプラグインは Skill だけを配ります）。

## v2.0.1 へ更新するとき

**証跡リンクの置換が働かない状態を直しました（#81）。** テストの報告書を共有ストレージ上の
文書へ変換するとき、報告書に書かれた証跡のファイル位置が URL へ書き換わらず、変換した文書から
証跡へたどり着けませんでした。

拾う対象を、行頭の `- trace: ` / `- HAR: ` に続くコード表記と、リンク記法のリンク先の 2 つに
しました。ケースのディレクトリ名が `TC-` で始まる必要はありません。共有ストレージ側の一覧に
載っている位置だけを書き換えるため、テストの失敗メッセージに現れるファイル位置や外部の URL は
そのまま残ります。

報告書を作る側の出力は変えていません。Skill の名前と数も変えていません（4 個）。

```bash
# Claude Code
/plugin marketplace update ai-plugins
/plugin install playwright-kit@ai-plugins

# Codex
codex plugin marketplace upgrade ai-plugins
codex plugin add playwright-kit@ai-plugins

# Kiro CLI
bash plugins/playwright-kit/dev.kiro/install.sh
```

## NDF との関係

| 用途 | プラグイン |
| --- | --- |
| E2E テストの計画・実装・証跡 | **playwright-kit**（このプラグイン） |
| PR 運用・レビュー・実装計画・開発方法論 | ndf |

NDF の `issue-plan-strategy` は release ブランチでの E2E 結合テストでこのプラグインの
`playwright-planning` を参照します。導入していない場合は、対象プロジェクトの既存手順に従って
ください。

## 分離した理由

Skill の `name` と `description` は起動時の一覧としてコンテキストへ常時注入され、予算は
プラグイン横断で共有されます。ブラウザ自動テストは使う場面が限られる一方で、MCP ツールを
多用するため frontmatter が大きく（4 個で約 2,400 文字、うち `playwright-authoring` の
`allowed-tools` が 1,916 文字）、常時注入する取り分に見合いませんでした。
必要な人だけが入れられるよう分離しています。
