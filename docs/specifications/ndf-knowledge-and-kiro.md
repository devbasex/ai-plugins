# NDF 知識構造・Kiro CLI 仕様

## 概要

NDF の知識配置（`AGENTS.md` と版と配布の正本、README の役割を含む）、Serena MCP 分離、Kiro CLI 対応に関する確定仕様。

Skill の挙動仕様は本ディレクトリでは管理しない。Skill の実体は `plugins/ndf/skills/*/SKILL.md` の 1 箇所で、Kiro CLI はその symlink を読む。

## 仕様化の扱い

本仕様は、完了済み issues / plans の内容を統合した現行仕様である。元の `issues/*` ファイルは完了後に削除されるため、マージ後の正は本ファイル、`AGENTS.md`、`KIRO.md`、`plugins/ndf/README.md`、`docs/ndf-plugin-reference.md` とする。過去の検討履歴が必要な場合は、この仕様を追加した commit の git 履歴を参照する。

## NDF 知識構造

AI エージェント向けの知識は以下の層で管理する。

| 層 | 役割 |
|---|---|
| `AGENTS.md` | リポジトリ共通のエントリポイント、ポリシー、ドキュメント案内 |
| `CLAUDE.md` / `KIRO.md` | 実行環境固有の補足設定 |
| `docs/` | リポジトリ知識、仕様、開発ガイド |
| `plugins/*/skills/` | 実行可能なワークフロー |

`CLAUDE.ndf.md` 注入と Serena memory 依存は廃止済みである。Serena は `mcp-serena` プラグインとして分離し、シンボル検索・参照検索・安全なリファクタリングなどのコード操作用途に限定する。知識は `docs/`、手順は `skills/` に置く。

`SessionStart` hook は `~/.claude/settings.json` の `cleanupPeriodDays` を 90 日以上に保ち、statusline 未設定時は NDF 標準 statusline を設定する。

## AGENTS.md と版と配布の正本

**`AGENTS.md` には判断の基準（何を選ぶか）だけを置き、手順と実測（どう動くか）は `docs/` へ置く。**
`AGENTS.md` の役割は「ナビゲーション + ポリシー（軽量）」であり、全セッション・全サブエージェントが
読む。版数の扱いの手順と実測が本文の 65% を占め、版を重ねるたびに増えていた。

**版数と配布の正本は `docs/versioning-and-distribution.md` である。** 章は読み手が問う順に並べる。

| 章 | 何を持つか |
| --- | --- |
| チャネルと ref | 正式版と開発版の対応、既定ブランチへ正式版を置く理由、clone の refspec |
| 版の付け方と開発版の配布 | semver の区分、接尾辞の規則、版の形の表（チェック J の位置決め） |
| ランタイムごとの取得と導入 | 4 ランタイムのコマンド、登録と導入が別の操作であること |
| 開発版を試す | ref を明示した登録、Kiro と agy の clone 経由の導入 |
| 正式版を出す | `develop` から `main` への Pull Request、タグ |
| 版数を持つ 15 箇所 | チェックが突き合わせる箇所 |
| チェックに載らず手で直す箇所 | 更新案内の本文、接尾辞の付け忘れ、変更履歴 |
| 取得元の登録を確かめる | 隔離した設定ディレクトリでの手順 |
| 利用者が過去の版へ戻る | タグへの固定、対象だけの固定 |

`AGENTS.md` の「版と配布の方針」に残すのは、チャネルを 2 つに分けること・正式版を既定ブランチへ
置くこと・接尾辞の方針・版を上げる時期・マイルストーンの名前・版を決める唯一の箇所・同名の取得元を
登録しないこと・タグを打つことである。

| 決定 | 理由 |
| --- | --- |
| 開発ガイドへ足さず、新しい文書にする | 開発ガイドは「プラグインを作る」手順で、版と配布は「作ったものを届ける」手順である。1 つにすると読みに来た主題と違う側を通過する |
| 開発ガイドの「バージョン管理」「利用者が過去の版へ戻る」を正本へ集め、跡はリンクにする | どちらが正かが書かれていない状態を残さない |
| `docs/ndf-version-decisions.md` へ足さない | あちらは出た版ごとの判断を残す履歴で、書き換えない。こちらは版を上げるたびに読み直す現行の手順である |
| `AGENTS.md` に版数の例を置かない（囲んだ版数は「主要プラグインです（v<版>）」の 1 箇所だけ） | チェックの突き合わせから外れた例は、版を上げても落ちず古いまま残る。置かなければ古くなる記載が無い |
| 残す節の見出しを「版と配布の方針」へ改める | 同じ見出しが 2 つの文書にあると、名前で指す参照がどちらを指すか決まらない。同じ見出しは 1 つの文書にだけ置く |
| 節ごとの分量の割合を機械で見る仕組みは作らない | 役割から離れたことは割合では判定できない。分量のチェック（501 行以上）は働いている |

チェック J の読む先の移し方は [説明文書のチェック](doc-consistency-checks.md) にある。

## README の役割

**`README.md` の名前を持つ文書は、規約 `plugins/ndf/skills/markdown-writing/03-readme-roles.md` の
役割（入口 / 索引 / 規約）へ当てはめる。** 規約の役割を持つ文書は `README.md` の名前を使わない。

**棚卸しの判定は、節の読み手とその文書を開く読み手が一致するかで行う。** 索引や試験の入口は、
開く人が規約と手順の読み手そのものである。`issues/README.md` の「ファイル名の付け方」のように、
置き場所の規則をそのディレクトリへ置く形は残す。役割は先頭の見出しと本文の過半で決める。

| 文書 | 役割 | 現行の姿 |
| --- | --- | --- |
| 根の `README.md` | 入口 | 概要・各ランタイムの最初の 1 手・利用可能なプラグイン・変更履歴・リファレンス・コントリビューション・サポート・ライセンス。開発版と過去の版は数行の案内と正本へのリンクだけを持つ |
| `plugins/ndf/README.md` | 入口 | NDF の導入の選択肢・更新案内・hook・通知。開発の手順は「変更するとき」から `CONTRIBUTING.md` と規約へ渡す |
| `plugins/ndf/skills/AUTHORING.md` | 規約 | Skill の frontmatter と命名の基準（Skill 執筆規約） |
| `docs/plugin-development-guide.md` | 正本 | プラグインを作る・変える・消す手順 |
| `docs/versioning-and-distribution.md` | 正本 | 開発版・過去の版・版数の扱い |
| `AGENTS.md` / `CONTRIBUTING.md` | 正本 | 構造・ベストプラクティス・検証 |

リンクは入口から正本へ張り、**正本から入口へは張らない。**

| 決定 | 理由 |
| --- | --- |
| Skill 執筆規約を同じディレクトリで `AUTHORING.md` にする | Skill を足す人は `plugins/ndf/skills/` を開いて規約を探す。`docs/` へ移すと、規約が律する Skill の実体から離れ、配布物から規約が消える |
| 根の README から外した節の正本は既存の文書にする | 外した節のほとんどは同じ主題が既存の文書にあり、根の側が古いものもあった。新しい文書を作ると正本が 3 か所目に増える |
| 「プラグインの削除」だけを開発ガイドへ移し、ブランチと Pull Request の手順へ書き換える | 削除の手順は他の文書に無い。作る・変える手順と同じ文書に置くとプラグインの一生が 1 か所で読める。運用の規則に反する直接 push の手順をそのまま移すと、正本が規則と食い違う |
| 導入の最初の 1 手は根と NDF の入口の両方に置き、選択肢と後続の手順は NDF の入口へ寄せる | 規約は入口に導入と最初の 1 手を書くと定める。Kiro の選択肢や agy の hook の差し込みは NDF に固有である |
| 更新案内（「vX へ更新するとき」）は入口に残す | 変更の一覧ではなく入れ替えに要る操作で、NDF の入口の見出しは版数を持つ 15 箇所の 1 つである |
| 「バージョン管理ルール」の例（`1.0.0 → 1.0.1` など）を正本へ持ち込まない | 区分の定義の言い換えで、囲めばチェック J が現行版より古い版数として落とす |
| 記録（`issues/`、`docs/development-history/`、`docs/presentations/`）の旧いパスと節名は書き換えない | 起きたことを残す文書で、書き換えると当時どのファイルを指していたかが読めなくなる |

## Kiro CLI 対応

Kiro CLI 用設定は `.kiro/agents/ndf.json` で管理する。agent 設定は `AGENTS.md` と `README.md` を `file://` resource として読み込む。`.kiro/skills/**/SKILL.md` の `skill://` 指定は組み込み agent の読み込み対象と重複するため持たない。常時適用したい指示は agent 選択に依存しない `.kiro/steering/ndf-policies.md` へ置く。

Kiro CLI では `plugins/ndf/dev.kiro/install.sh` が `plugins/ndf/skills/` から `.kiro/skills/` への symlink、`.kiro/steering/ndf-policies.md`、`.kiro/agents/ndf.json` を生成する。`ndf-policies` は steering の生成元としてのみ使い、`.kiro/skills/` へは symlink しない。Kiro は `.kiro/skills/*/SKILL.md` と `.kiro/steering/**/*.md` の両方を文脈へ読み込むため、両方に置くと同じ内容が 2 回注入されるからである。manifest（`plugins/ndf/manifests/kiro-skills.txt`）には steering の生成元として残す。`--scope global` を指定した場合の生成先は `~/.kiro/` 配下になる。生成した agent は既定にならないため、`--set-default` を指定したときだけ `kiro-cli agent set-default ndf` を実行する。`kiro-cli` は workspace agent を cwd 配下の `.kiro/agents/` からのみ検出するため、この呼び出しは導入先（`workspace` なら `--project` のパス、`global` なら `$HOME`）で行い、`agent list` で反映を検証する（`set-default` は agent 未検出でも終了コード 0 を返すため）。`agentSpawn` hook は初期化時の案内に使い、`--with-slack` 指定時のみ `stop` hook に `plugins/ndf/scripts/wait-notify.py --runtime kiro` を置き、応答が利用者の回答か承認を求めて終わったときだけ Slack へ知らせる。判定には stop hook payload の `assistant_response` を使う。

Codex 連携は MCP サーバではなく `/ndf:external-ai` skill と `corder` エージェント経由の Codex CLI 直接実行を標準とする。Kiro 用 `--with-codex` は Kiro セッションから Codex CLI を扱う場合の補助設定である。

### 導入先のチェック

`install.sh` は `--project` の値を、選択肢を解析する時点（`--scope` の解決より前）で確かめる。

| 入力 | 標準エラー | 終了コード |
|---|---|---|
| 存在しないパス | `ERROR: --project points at a path that does not exist: <パス>` と `HINT: mkdir -p <整形したパス>` | 2 |
| ディレクトリではないパス | `ERROR: --project points at a path that is not a directory: <パス>`（`HINT:` は出さない） | 2 |
| 存在するディレクトリ | 出さない（導入へ進む） | 変わらない |

`--scope global` と併用してもチェックが先に働く。存在するディレクトリとの併用だけが `WARN:` を出して
`--project` を無視する。

| 決定 | 理由 |
|---|---|
| 存在しない導入先を作らない | 綴りを誤ったパスへ導入すると、誤りに気づく機会が失われる。尋ねてから作る形は `--yes` の使い方で尋ねる相手がおらず、`--yes` のときだけ作る形は同意していない場所へ書き込む |
| 存在しないパスにだけ `mkdir -p` を案内する | ファイルに対する `mkdir -p` は失敗する。1 つの文言にまとめると、実行できない手順を案内する |
| 終了コードを 2 にする | このスクリプトは入力の誤りに 2、実行の前提の欠落に 1 を使う。導入先の指定は入力である |
| 案内の行の接頭辞を `HINT:` にする | `ERROR:` は止まった理由を述べる行で、作り方は次に打つ手である。接頭辞なしの行はシェルの裸の出力と見分けがつかない |
| `HINT:` のパスを `printf '%q'` で整形し、`ERROR:` のパスは整形しない | 空白を含むパスをそのまま埋めると、案内どおりに打っても 2 つのディレクトリができる。単引用符で囲む形は単引用符を含むパスで閉じる。`ERROR:` は利用者が打つ語ではない |
| 案内は英語で書く | `--project` に関わる既存の案内（`--project requires a path` など）と揃える |

整形の結果（制御文字では `$'...'`）は bash と zsh が読む書式である。スクリプト自身が bash で動き、
案内も `bash plugins/ndf/dev.kiro/install.sh ...` の形で載っているため、読む側も bash を前提にする。

## データ・設定

| 設定 | 用途 |
|---|---|
| `cleanupPeriodDays` | Claude Code transcript 保持期間。NDF hook が 90 日以上に保つ |
| `.kiro/agents/ndf.json` | Kiro CLI 用 agent 設定 |
| `.kiro/steering/ndf-policies.md` | agent 選択に依存しない常時指示 |
| `.kiro/skills/` | Kiro CLI 用 Skill symlink 配置 |

## 外部連携

| 連携 | 仕様 |
|---|---|
| Kiro CLI | `.kiro/agents/ndf.json`、`.kiro/steering/`、`.kiro/skills/` で Skill / hook / MCP 設定を提供 |
| Serena MCP | `mcp-serena` プラグインとして分離提供 |
| Codex CLI | `/ndf:external-ai` skill と `corder` エージェントから直接実行 |
| Slack | Claude Code / Kiro / Codex で、利用者の回答か承認を待つときの通知に使用 |

## テスト観点

| 領域 | 確認内容 |
|---|---|
| Kiro CLI | `.kiro/agents/ndf.json` が resources と hooks を持ち、`kiro-cli agent list` に `ndf` が現れること。`mcpServers` は `--with-codex` 指定時、または既存の利用者管理設定を引き継いだ場合にのみ現れる |
| Kiro installer の導入先 | 存在しないパス・ファイル・`--scope global` との併用で `ERROR:`（と `HINT:`）を出して終了コード 2、`HINT:` のパスを bash で読み戻すと 1 語で元のパスに一致、存在するディレクトリの `--dry-run` は 0（`scripts/tests/test_kiro_installer_project.py`） |
| ドキュメント | `AGENTS.md` / `CLAUDE.md` / `KIRO.md` / `docs/` の役割が重複しすぎていないこと |
| 版数の例 | `AGENTS.md` の囲んだ版数が「主要プラグインです（v<版>）」の 1 箇所だけであること（`python3 scripts/check-doc-staleness.py --root .`） |
| README の役割 | `git ls-files '*README.md'` に規約の役割を持つ文書が無いこと。根の `README.md` に「開発ガイドライン」「マーケットプレイス管理」の見出しが無いこと |

## 関連リンク

- [issue #415](https://github.com/devbasex/ai-plugins/issues/415) / [PR #502](https://github.com/devbasex/ai-plugins/pull/502)（設計） / [PR #586](https://github.com/devbasex/ai-plugins/pull/586)（実装） — Kiro installer の導入先のチェック
- [issue #499](https://github.com/devbasex/ai-plugins/issues/499) / [PR #505](https://github.com/devbasex/ai-plugins/pull/505)（設計） / [PR #594](https://github.com/devbasex/ai-plugins/pull/594)（実装） — `AGENTS.md` と版と配布の正本
- [issue #500](https://github.com/devbasex/ai-plugins/issues/500) / [PR #608](https://github.com/devbasex/ai-plugins/pull/608)（設計） / [PR #612](https://github.com/devbasex/ai-plugins/pull/612)（実装） — README の役割の適用
- [版と配布の正本](../versioning-and-distribution.md)
- [README の役割の規約](../../plugins/ndf/skills/markdown-writing/03-readme-roles.md)
- [説明文書のチェック](doc-consistency-checks.md)
- [mcp-serena README](../../plugins/mcp/mcp-serena/README.md)
- [NDF Plugin リファレンス](../ndf-plugin-reference.md)
- [NDF README](../../plugins/ndf/README.md)
