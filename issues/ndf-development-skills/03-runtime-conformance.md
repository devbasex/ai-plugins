# ランタイム規約への適合

用語は [01-overview.md](01-overview.md) を参照。

## 3 ランタイムの規約差分

Skill は Claude Code / Codex / Kiro に配布する。2026-08-07 時点の各仕様は次のとおり。

| 項目 | Claude Code | Codex | Kiro |
| --- | --- | --- | --- |
| 準拠 | Agent Skills 仕様 + 独自拡張（全 17 項目） | 仕様の `name` / `description` が必須 | Agent Skills 仕様に準拠と明記 |
| 文書化された項目 | 17 | 2 | 5（`name` / `description` / `license` / `compatibility` / `metadata`） |
| 配置 | `.claude/skills/`、プラグインの `skills/` | `.agents/skills/`（作業ディレクトリ → リポジトリルート → ホーム → システム） | `.kiro/skills/`（プロジェクト）/ `~/.kiro/skills/`（全体） |
| 発動制御 | `disable-model-invocation` / `user-invocable` | Skill ごとの `<Skill 名>/agents/openai.yaml` の `policy.allow_implicit_invocation` | 文書化された制御手段なし |
| 引数 | `argument-hint` / `arguments` | Skill ごとの `<Skill 名>/agents/openai.yaml` の `interface.default_prompt` | なし |
| `when_to_use` | 対応 | 文書なし | 文書なし |
| `allowed-tools` | 対応（ターン単位） | Skill ごとの `<Skill 名>/agents/openai.yaml` の `dependencies.tools` | プロジェクト配置では機能しない |
| 段階的読み込み | メタデータのみ起動時、本文は発動時 | 起動時に `name` + `description` + ファイルパスをシステムプロンプトへ読み込み | メタデータのみ起動時、本文はファイル読み取りで取得 |
| 初期一覧の総量予算 | 文書なし | コンテキストウィンドウの 2%、不明時は 8,000 文字。超過時は `description` を短縮し、なお超えると Skill を一覧から省略して警告（[02-skill-inventory.md](02-skill-inventory.md)） | 文書なし |

## `description` に発動条件を含める

Codex と Kiro は `when_to_use` を文書化していない。仕様は未知の項目を無視すると定めているため壊れはしないが、**両ランタイムでは `description` だけで発動判定される**。

現状は `description` を英語 1 行の「何をするか」だけにし、「いつ使うか」を Claude Code 独自項目へ置いている。Agent Skills 仕様は `description` について「what the skill does and when to use it」の両方を書くよう求めており、現状の構造では Codex と Kiro で発動精度が落ちる。

`description` に「何をするか + 主要トリガ」を入れ、`when_to_use` は Claude Code 向けの追加トリガに限定する。Codex は初期一覧が予算を超えると `description` を短縮するため、主要トリガは先頭へ置く。

## Kiro の実機検証結果

kiro-cli **2.16.1** で検証した（2026-08-07、認証済み環境）。検証用プロジェクトへ `install.sh` を実行し、`/context show` と検査用 Skill で確認した。

| 検証項目 | 結果 | 根拠 |
| --- | --- | --- |
| シンボリックリンク経由の Skill を認識するか | 認識する | 実体ディレクトリ 1 個とリンク 1 個を並べ、両方が一覧・読み取りとも成功 |
| 起動時に本文を読み込むか | 読み込まない | 「ファイルを読まずに本文中のマーカー文字列を出力せよ」に対し「本文なし」と応答。本文はファイル読み取りツールで都度取得していた |
| コンテキスト占有率 | 28 個で 0.1% | `/context show` の `Context files total` |
| `description` 一致で自動発動するか | 発動する | 検査用 Skill の説明に一致する依頼に対し、当該 Skill の本文を自ら読みに行った |
| プロジェクト配置で `allowed-tools` が事前承認になるか | **ならない** | 下記 |

`allowed-tools` の検証に使った Skill:

```yaml
---
name: gammatoolskill
description: "Run the gamma probe. Use when the user asks to run the gamma probe."
allowed-tools: execute_bash
---
本文でシェルコマンドの実行を指示
```

結果は `Command execute_bash is rejected because it matches one or more rules on the denied list` で、事前承認として機能していない。

公開されている不具合報告のうち、シンボリックリンク非追跡（[#6401](https://github.com/kirodotdev/Kiro/issues/6401)、報告版 0.10.78）、起動時の本文読み込み（[#6680](https://github.com/kirodotdev/Kiro/issues/6680)、1.28.1）、自動発動しない（[#5867](https://github.com/kirodotdev/Kiro/issues/5867)）は、いずれも 2.16.1 では確認できなかった。プロジェクト配置での `allowed-tools` 無効（[#6055](https://github.com/kirodotdev/Kiro/issues/6055)、1.26.2）のみ確認できた。

## Kiro 導入方式の不具合

### エージェント定義が有効になっていない

`install.sh` は `.kiro/agents/default.json` を生成するが、Kiro の既定エージェントは組み込みの `kiro_default` であり、生成した `default` は「プロジェクトに存在するが選択されていないエージェント」に過ぎない。

```text
$ kiro-cli agent list                              # install.sh 実行後
* kiro_default    (Built-in)    Default agent      ← * が現在の既定
  default         Workspace     NDF統合開発エージェント（Kiro CLI用）

$ kiro-cli chat --no-interactive "/context show"
Agent (kiro_default)                               ← 生成したエージェントは使われない
```

`install.sh` に `kiro-cli agent set-default` の呼び出しがない。結果として、`kiro-cli chat` を通常起動する限り以下がすべて無効である。

- エージェント起動時フック
- `--with-slack` で追加する終了通知フック
- `--with-codex` で追加する外部 AI 連携サーバ設定
- `resources` に指定した `AGENTS.md` / `README.md` / `ndf-policies` の明示読み込み

Skill だけは組み込みエージェントが `.kiro/skills/*/SKILL.md` を自前で読むため動作しており、問題が表面化していない。完了メッセージの「`kiro-cli chat` で動作確認してください」も実態と合っていない。

### Skill 読み込み指定が組み込み設定と重複している

組み込みエージェントの読み込み対象には既に `.kiro/skills/*/SKILL.md` と `~/.kiro/skills/*/SKILL.md` が含まれる。生成したエージェントはそこへ `skill://.kiro/skills/**/SKILL.md` を追加するため、同じファイルが 2 つの指定から二重に登録される。

```text
kiro_default            : Context files total: 0.1% of context window
生成したエージェント     : Context files total: 0.2% of context window
```

### Kiro にはプラグイン機構がない

Kiro CLI には Skill・フック・外部連携・常時指示をまとめて配布する仕組みがない（[#8578](https://github.com/kirodotdev/Kiro/issues/8578)）。Kiro IDE の Powers は CLI では使えない。`install.sh` による導入は当面継続する。

## Kiro 導入方式の変更

### エージェントを有効化する

生成するエージェントの名前を `ndf` にし、起動方法を `kiro-cli chat --agent ndf` として案内する。`--set-default` を指定した場合のみ `kiro-cli agent set-default ndf` を実行する。

既定エージェントの自動書き換えは利用者の既存設定を奪うため、オプトインとする。実行前に現在の既定を表示して確認を取る。

### Skill 読み込み指定を削除する

`resources` から `skill://.kiro/skills/**/SKILL.md` を削除する。組み込みエージェントの指定が同じファイルを拾うため、削除しても Skill は認識される。削除後に一覧表示と発動を再検証する。

### `allowed-tools` に依存しない

プロジェクト配置では事前承認として機能しない。

- Skill 本文に「無確認で実行される」前提の記述を持たせない
- Kiro 配布分について、権限確認が入ることを `plugins/ndf-kiro/README.md` に明記する
- `--scope workspace|global` は、この制限の回避策としてではなく利用形態の選択肢として追加する

### 常時指示を steering へ移す

`resources` の `file://.kiro/skills/ndf-policies/SKILL.md` はエージェントが選択されていなければ効かない。Kiro の steering はエージェント選択に依存しないため、`.kiro/steering/ndf-policies.md` を生成する。既定エージェントを書き換えない方針を採るため、この移行は必須になる。

配置先は導入スコープに従う。`--scope workspace` では `.kiro/steering/`、`--scope global` では `~/.kiro/steering/` へ生成し、後者はプロジェクト外のディレクトリで起動した場合も参照される。

## Codex 導入方式の変更

`disable-model-invocation: true` に相当する制御は `agents/openai.yaml` の `policy.allow_implicit_invocation: false` である。このファイルは Skill ディレクトリ配下の `<Skill 名>/agents/openai.yaml` として読まれるため、制御したい Skill ごとに置く必要がある（[Build skills](https://developers.openai.com/codex/skills)）。配布物の直下に単一ファイルを置いても、個別 Skill の暗黙起動には効かない。

```text
plugins/ndf-codex/skills/deploy/
├── SKILL.md
└── agents/
    └── openai.yaml
```

Codex 配布物にこのファイルがないため、`deploy` を含む全 Skill が暗黙起動できる状態にある。

`scripts/build-runtime-plugins.sh` で、`disable-model-invocation: true` を持つ Skill それぞれに `agents/openai.yaml` を生成する。既存の Codex 用マニフェスト生成処理と同じ形で実装できる。

## 配布先を広げる際の制約

Agent Skills 仕様が定めるのは `name` / `description` / `license` / `compatibility` / `metadata` / `allowed-tools` の 6 項目のみで、`when_to_use` / `argument-hint` / `arguments` / `disable-model-invocation` / `user-invocable` / `paths` は Claude Code 独自である。

仕様準拠のランタイムは未知の項目を無視するが、claude.ai へのアップロードや Skills API 経由では `Unexpected key(s) in SKILL.md frontmatter` のエラーになる。現在の配布先 3 種では問題にならないが、配布先を広げる際の制約として記録する。
