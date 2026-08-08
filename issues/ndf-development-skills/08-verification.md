# 影響範囲・リスク・テスト計画

用語は [01-overview.md](01-overview.md) を参照。

## 影響範囲

| 対象 | 影響 |
| --- | --- |
| Skill 総数 | [02-skill-inventory.md](02-skill-inventory.md)「Skill 総数の推移」のとおり、統合と削除で 29 まで減り、新設 9 個（開発方法論レイヤー 8 個 + `execute-goal` 1 個）を加えて最終 38 |
| コマンド名 | 起動実績のない `/ndf:clean` `/ndf:review-pr-comments` `/ndf:resolve-pr-comments` `/ndf:git-gh-operations` などが消える。起動上位 5 個（`fix` `cross-review` `merged` `pr` `issue-plan-strategy`、計 1,145 回）は改名しない。`/ndf:codex` `/ndf:gemini`(計 5 回) と `/ndf:branch-fix-strategy`(4 回) `/ndf:sync-main`(1 回) `/ndf:review-branch`(3 回) が変わる |
| 自動発動の挙動 | `merged` / `pr` / `pr-tests` が自然文で起動するようになる。起動しない前提の運用があれば変わる。`review` も同じ設定にしたが、Claude Code では組み込みの `code-review` が優先されるため自然文では起動しない（「自然文からの発動の実測」） |
| 常時注入されるコンテキスト | 棚卸で削減、新規追加で増加。合計サイズを継続的インテグレーションで監視 |
| 3 ランタイム | manifest 経由で配布。`build-runtime-plugins.sh --check` で生成物の差異を検出 |
| Kiro の導入方式 | エージェント名 `default` → `ndf`、Skill 読み込み指定の削除、steering 生成、スコープ選択。**既存の `.kiro/agents/default.json` を持つプロジェクトは再インストールが必要** |
| Codex の配布物 | 明示指示専用の Skill それぞれの直下に `agents/openai.yaml` が追加され、その Skill が暗黙起動しなくなる |
| `director` エージェント | Claude Code 版のみ改修。モード判定は持たず `development-workflow` へ委ねるため、モード追加時の変更は同 Skill に閉じる。Kiro は Skill 経由で追随、Codex はエージェント定義を持たない |
| ライセンス | 上流の文章を直接転用しない方針。転用が発生した場合は告知の記載に加え、3 ランタイム配布物と Kiro の導入先まで告知が届くことが Apache-2.0 の要件になる |
| 継続的インテグレーション | 既存 2 種が通ること。Release 0 で frontmatter 検査、Release 3 で挙動評価を追加 |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| Skill 名変更で既存利用者のコマンドが壊れる | メジャーバージョン 5.0.0 とし、旧名から新名への対応表を 1 リリース分残す。各 README の冒頭に移行案内 |
| 統合で内容が単純連結され、かえって肥大する | 各統合 PR で統合前後の行数を本文に記録。増えていたら再構成を差し戻す |
| 整理した Skill が実は使われていた | 判定は実測起動率を根拠とし、判定根拠を台帳に残す。迷うものは削除せず縮小 |
| 自動発動を許して誤発動が増える | 挙動評価シナリオ 11 で意図した発動を検証。破壊的操作は明示指示専用を維持 |
| 実測が 1 名 80 日分に偏り、チーム全体の傾向と異なる | 削除は [02-skill-inventory.md](02-skill-inventory.md) の判断基準表に従い、例外を適用したものは適用条件を台帳に残す。発動改善にとどめたものは次のリリースで再測定する |
| `merged` などの高頻度コマンドを改名して日常運用が止まる | 起動上位 5 個は改名しない。統合時は利用実績の多い側の名前を残す |
| トリガ語の重複 | 検査スクリプトで機械的に検出し、継続的インテグレーションで失敗させる |
| Codex と Kiro で Skill が発動しない | `description` に発動条件を含める規約を検査。挙動評価を 3 ランタイムで実行 |
| Codex の初期一覧予算を超え、`description` が短縮されるか Skill が一覧から落ちる | 合計を 8,000 文字以内に収める検査を継続的インテグレーションで実行し、`description` の先頭に主要な用途とトリガ語を置く（[02-skill-inventory.md](02-skill-inventory.md)「Codex の初期一覧予算」） |
| Kiro で拡張設定が無効のまま | エージェント名を `ndf` にして起動方法を案内し、`--set-default` を用意。動作確認テストで既定切り替えを検査 |
| エージェント名変更で既存の設定ファイルが孤児になる | 旧ファイルを検出したらバックアップして案内する。README に移行手順を記載 |
| `--set-default` が利用者の既存設定を奪う | オプトインとし、実行前に現在の既定を表示して確認する |
| Skill 読み込み指定の削除で Skill が発動しなくなる | 削除後に一覧表示と発動を再検証してからマージ |
| Kiro の挙動が将来のバージョンで変わる | 台帳に検証日と版数を残し、動作確認テストで継続検査する |
| Codex の設定ファイル形式の想定が誤っている | 実装前に実機で検証する。検証できなければ Codex 分を保留し、他を先に進める |
| 一気通貫実行が意図しないプルリクエストを作る | 完了条件に制約を書き込み、`--dry-run` を用意。リリース用プルリクエストのレビュー依頼化と本流へのマージは必ず利用者が行う |
| 一気通貫実行の長時間処理が中断される | 継続ループの状態はランタイムが持つ。Claude Code は `--resume`、Codex は `/goal resume` で復元する |
| 完了条件が会話に現れない事実を前提にして永久に充足しない | 評価器はツールを呼ばないため、条件は出力で証明できる形に限る。ターン上限を条件へ含める |
| 工程が重くなり日常作業が滞る | `light` モードを既定とし、`standard` 以上は明示的な条件でのみ発動 |
| 上流の文章転用によるライセンス違反 | 再執筆を原則とし、固定コミットと改変内容を記録 |
| 告知がリポジトリ内にとどまり、導入した利用者の手元へ届かない | 編集元 1 ファイルから `build-runtime-plugins.sh` が 3 配布物へ同期し、Kiro は `install.sh` が導入先へ配置する。同期漏れは `--check` で検出（[07-tasks.md](07-tasks.md) Task 1-5） |
| モード判定の基準が複数箇所に写され、モード追加時に食い違う | 判定は `development-workflow` の 1 箇所に限り、`director` は判定結果を受け取るだけにする。Release 2 のテスト項目で写しがないことを確認 |
| ランタイム間の機能差 | manifest 3 ファイルへの反映を PR チェックリスト項目にする |
| リンク切れ | 依存順に PR を並べ、`python3 scripts/check-markdown-links.py` を各 PR で実行 |

## テスト計画

### 各 PR 共通

- [ ] `bash scripts/build-runtime-plugins.sh` 実行後 `bash scripts/build-runtime-plugins.sh --check` が成功する
- [ ] `bash scripts/validate-runtime-plugins.sh` が成功する
- [ ] `claude plugin validate` が成功する
- [ ] `python3 scripts/check-markdown-links.py` でリンク切れがない
- [ ] `python3 scripts/check-skill-frontmatter.py` が成功する（Task 0-7 以降）
- [ ] `skills-ref validate` が全 Skill で成功する
- [ ] `SKILL.md` が 500 行を超えていない
- [ ] Skill の追加・統合・削除が 3 つの manifest すべてに反映されている
- [ ] 版数を上げた PR で `grep -rn '<旧版数>'` が残っていない（`plugin.json` 以外にも `marketplace.json` の説明文、`AGENTS.md`、`README.md`、プレゼン資料に記載がある）

### Release 0 固有

- [ ] 修正後の `skill-stats` が、[02-skill-inventory.md](02-skill-inventory.md)「利用実績の実測」と同じ期間で同じ「計 / 自動 / 明示」を出力する
- [ ] 修正後の `skill-stats` でトリガ抽出に失敗する Skill が 13 個だけになる（`when_to_use` を持たない 14 個のうち、`plan-to-spec` は `description` にトリガを持つ）
- [ ] 統合前後で機能が減っていない（統合元の手順が統合先に残っている）
- [ ] `grep -rn '<削除した Skill 名>'` がリポジトリ全体でヒットしない
- [x] `merged` / `pr` / `pr-tests` が自然文の依頼で起動する
- [ ] `review` が自然文の依頼で起動する — **満たせない**。実測を「自然文からの発動の実測」に記載
- [ ] `merged` `fix` `cross-review` `pr` `issue-plan-strategy` のコマンド名が変わっていない
- [ ] `deploy` と `cherry-pick-pr` 相当がエージェントから自動起動しない
- [ ] `install.sh` 実行後、`kiro-cli agent list` に `ndf` が現れる
- [ ] `kiro-cli chat --agent ndf` でエージェント起動時フックが動作する
- [ ] `install.sh --set-default` で既定が `ndf` に切り替わり、`kiro-cli chat` 単体で使われる
- [ ] Skill 読み込み指定の削除後も Skill が発動し、`/context show` の占有率が削除前より下がる
- [ ] `install.sh --scope global` で `~/.kiro/skills/` に配置される
- [ ] `.kiro/steering/ndf-policies.md` が生成され、エージェント選択に依存せず参照される
- [ ] `install.sh --scope global` で `~/.kiro/steering/ndf-policies.md` が生成され、プロジェクト外のディレクトリで起動しても常時指示として参照される
- [ ] 明示指示専用の Skill それぞれに `skills/<Skill 名>/agents/openai.yaml` が生成されている
- [ ] Codex で明示指示専用の Skill が暗黙起動しない
- [ ] Codex の初期一覧に載る合計が 8,000 文字以内に収まり、起動時に一覧の省略警告が出ない
- [ ] `when_to_use` を持つ Skill が `description` にない追加トリガを実際に持ち、`description` だけで足りる Skill には付いていない

### Release 1 固有

- [ ] `THIRD_PARTY_NOTICES.md` が `plugins/ndf-claude/` / `plugins/ndf-codex/` / `plugins/ndf-kiro/` の 3 ランタイム配布物すべてに含まれる
- [ ] 編集元の `THIRD_PARTY_NOTICES.md` を変更した後 `bash scripts/build-runtime-plugins.sh --check` が差異を検出する
- [ ] `install.sh` 実行後、告知が導入先（`--scope workspace` は `.kiro/`、`global` は `~/.kiro/`）に配置される

### Release 2 固有

- [ ] `director.md` にモード判定の基準と振り分け表が書かれておらず、`development-workflow` を呼ぶ手順だけになっている
- [ ] `development-workflow` のモード定義を 1 つ変更したとき、他のファイルを直さずに `director` の振る舞いが追随する

### Release 3 固有

- [ ] `--dry-run` が完了条件と実行計画のみを出力し、ブランチとプルリクエストを作らない
- [ ] 受け入れ条件のない計画ファイルで停止する
- [ ] リリース用プルリクエストを下書きのまま残して完了条件を満たす
- [ ] Claude Code と Codex の組み込み `/goal` に渡した条件でループが終了する
- [ ] 中断後の再実行で、既存のブランチとプルリクエストを重複作成せず途中から再開する
- [ ] `standard` と判定された計画のレビュー段階で `review` が呼ばれ、`cross-review` が起動しない（`execute-goal` は `review` を**明示的に**呼ぶこと。自然文に任せると Claude Code では組み込みの `code-review` が起動して手順が変わる）
- [ ] `architecture` と判定された計画、およびデータベース移行を含む計画のレビュー段階で `cross-review` が起動する
- [ ] Kiro では段階ごとに続行指示を求め、途中で破綻しない
- [ ] 挙動評価の 12 シナリオがすべて期待どおりになる

### リリース単位

- [ ] `bash scripts/runtime-smoke-test.sh` が 3 ランタイムで成功する
- [ ] Claude Code で新 Skill が起動できる
- [ ] Kiro で `install.sh` 実行後、`.kiro/skills/` に新 Skill が配置される
- [ ] Kiro のセッション開始直後の `/context show` で占有率が基準以内に収まる
- [ ] 同一の自然文依頼に対し、3 ランタイムで同じ Skill が発動する

### リグレッション

- [ ] 既存のプルリクエスト運用（作成 → レビュー → 修正 → 後処理）が統合後も通しで動作する
- [ ] `cross-review` が統合後の `external-ai` と `fix` を正しく呼び出す
- [ ] 文言修正のみの変更で仕様作成とテスト駆動が要求されない

## 自然文からの発動の実測

v5.0.0 を `main` へマージしたあと、Claude Code で `--output-format stream-json` を使い、
`Skill` ツール呼び出しを抽出して実測した（claude-haiku-4-5、一時 git リポジトリ）。

| 依頼文 | 起動された Skill |
| --- | --- |
| マージ済みのブランチを整理してください。 | `ndf:merged` |
| この変更で PR を作ってください。 | `ndf:pr` |
| PRテストを実行してください。 | `ndf:pr-tests` |
| このブランチの変更をレビューしてください。 | `code-review`（Claude Code 組み込み） |
| マージ前チェックをしてください。 | `code-review` |
| PR を作る前にセルフレビューしてください。 | `code-review` |
| codex にこのブランチをレビューさせてください。 | `ndf:external-ai` |

`disable-model-invocation` を外したこと自体は効いており、4 個のうち 3 個は狙いどおり
起動する。`review` だけが **Claude Code 組み込みの `code-review` に負ける**。

`description` を「PR へ APPROVE / REQUEST_CHANGES の判定を投稿する」「codex / gemini へ
委譲する」という差別化点へ寄せて測り直したが、結果は変わらなかった。組み込み側も
`--comment` でインラインコメントを投稿でき、用途の重なりが大きいためと考えられる。

### 判断

`review` の自然文発動は**この条件では達成できないものとして扱い、追わない**。

- `/ndf:review` の明示起動は動作する（`--branch` で統合後の報告形式が出ることを確認済み）
- `cross-review` は内部で `review` を直接呼ぶため、収束レビューの経路は影響を受けない
- 「codex にレビューさせる」意図が `external-ai` へ向かうのは責務どおりで、退行ではない

したがって機能は失われていない。名前や責務の見直しは破壊的変更になるため、
[#83](https://github.com/devbasex/ai-plugins/issues/83) で追跡する。

### 後続リリースへの申し送り

`execute-goal`（Release 3）のレビュー段階は、`review` を**明示的に**呼ぶ手順として書く。
自然文でレビューを依頼する形にすると、Claude Code では組み込みの `code-review` が起動して
判定の投稿経路が変わる。`cross-review` は既に内部で `review` を直接呼んでいるため影響しない。

### トリガ語の重複検査の限界

`scripts/check-skill-frontmatter.py` の重複検査は **NDF の Skill 同士**しか見ない。
ランタイム組み込みの Skill や他プラグインとの競合は、配布先の環境に依存するため
機械的に検査できない。`review` の件はこの限界が表面化した例である。

## 未確認事項

着手前に解消するか、解消しないまま進める場合はリスクとして扱う。

| 項目 | 状態 | 解消方法 |
| --- | --- | --- |
| Kiro の全体配置（`~/.kiro/skills/`）で `allowed-tools` が事前承認として機能するか | **未確認**。非対話モードではホーム配下の読み取り自体が承認対象になり、プロジェクト配置との比較ができなかった | 対話モードで両配置を比較する |
| Codex の `<Skill 名>/agents/openai.yaml` のスキーマ | **未確認**。配置が Skill ディレクトリ配下であることは公式ドキュメントで確認できたが、各項目の受理可否は実機で確認していない | Codex CLI 実機で最小構成を作って検証する（Task 0-8 の前提） |
| Kiro に継続ループの代替手段があるか | **未確認**。`/goal` が存在しないことは確認したが、フックなど他の手段は調べていない | Kiro のフック機構を調査する |
| 統合後の frontmatter 合計サイズの適正上限 | **未決定** | 棚卸完了時点の実測値を基準に設定する（Task 0-7） |
| Kiro 全文読み込みの再発有無 | 2.16.1 では未再現。将来のバージョンでの挙動は不明 | 動作確認テストで占有率を継続検査する |
