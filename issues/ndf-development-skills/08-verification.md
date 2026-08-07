# 影響範囲・リスク・テスト計画

用語は [01-overview.md](01-overview.md) を参照。

## 影響範囲

| 対象 | 影響 |
| --- | --- |
| Skill 総数 | 49 → 37（統合）→ 28（削除）→ 37（新規 9 個追加後） |
| コマンド名 | 起動実績のない `/ndf:clean` `/ndf:review-pr-comments` `/ndf:resolve-pr-comments` `/ndf:git-gh-operations` などが消える。起動上位 5 個（`fix` `cross-review` `merged` `pr` `issue-plan-strategy`、計 1,145 回）は改名しない。`/ndf:codex` `/ndf:gemini`(計 5 回) と `/ndf:branch-fix-strategy`(4 回) `/ndf:sync-main`(1 回) `/ndf:review-branch`(3 回) が変わる |
| 自動発動の挙動 | `merged` / `review` / `pr` / `pr-tests` が自然文で起動するようになる。起動しない前提の運用があれば変わる |
| 常時注入されるコンテキスト | 棚卸で削減、新規追加で増加。合計サイズを継続的インテグレーションで監視 |
| 3 ランタイム | manifest 経由で配布。`build-runtime-plugins.sh --check` で生成物の差異を検出 |
| Kiro の導入方式 | エージェント名 `default` → `ndf`、Skill 読み込み指定の削除、steering 生成、スコープ選択。**既存の `.kiro/agents/default.json` を持つプロジェクトは再インストールが必要** |
| Codex の配布物 | 明示指示専用の Skill それぞれの直下に `agents/openai.yaml` が追加され、その Skill が暗黙起動しなくなる |
| `director` エージェント | Claude Code 版のみ改修。Kiro は Skill 経由で追随、Codex はエージェント定義を持たない |
| ライセンス | 上流の文章を直接転用しない方針。転用が発生した場合のみ告知の記載が必須になる |
| 継続的インテグレーション | 既存 2 種が通ること。Release 0 で frontmatter 検査、Release 3 で挙動評価を追加 |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| Skill 名変更で既存利用者のコマンドが壊れる | メジャーバージョン 5.0.0 とし、旧名から新名への対応表を 1 リリース分残す。各 README の冒頭に移行案内 |
| 統合で内容が単純連結され、かえって肥大する | 各統合 PR で統合前後の行数を本文に記録。増えていたら再構成を差し戻す |
| 整理した Skill が実は使われていた | 判定は実測起動率を根拠とし、判定根拠を台帳に残す。迷うものは削除せず縮小 |
| 自動発動を許して誤発動が増える | 挙動評価シナリオ 11 で意図した発動を検証。破壊的操作は明示指示専用を維持 |
| 実測が 1 名 80 日分に偏り、チーム全体の傾向と異なる | 削除は起動ゼロかつ機会ゼロに限る。機会があるものは発動改善にとどめ、次のリリースで再測定する |
| `merged` などの高頻度コマンドを改名して日常運用が止まる | 起動上位 5 個は改名しない。統合時は利用実績の多い側の名前を残す |
| トリガ語の重複 | 検査スクリプトで機械的に検出し、継続的インテグレーションで失敗させる |
| Codex と Kiro で Skill が発動しない | `description` に発動条件を含める規約を検査。挙動評価を 3 ランタイムで実行 |
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

### Release 0 固有

- [ ] 統合前後で機能が減っていない（統合元の手順が統合先に残っている）
- [ ] `grep -rn '<削除した Skill 名>'` がリポジトリ全体でヒットしない
- [ ] `merged` / `pr` / `review` / `pr-tests` が自然文の依頼で起動する
- [ ] `merged` `fix` `cross-review` `pr` `issue-plan-strategy` のコマンド名が変わっていない
- [ ] `deploy` と `cherry-pick-pr` 相当がエージェントから自動起動しない
- [ ] `install.sh` 実行後、`kiro-cli agent list` に `ndf` が現れる
- [ ] `kiro-cli chat --agent ndf` でエージェント起動時フックが動作する
- [ ] `install.sh --set-default` で既定が `ndf` に切り替わり、`kiro-cli chat` 単体で使われる
- [ ] Skill 読み込み指定の削除後も Skill が発動し、`/context show` の占有率が削除前より下がる
- [ ] `install.sh --scope global` で `~/.kiro/skills/` に配置される
- [ ] `.kiro/steering/ndf-policies.md` が生成され、エージェント選択に依存せず参照される
- [ ] 明示指示専用の Skill それぞれに `skills/<Skill 名>/agents/openai.yaml` が生成されている
- [ ] Codex で明示指示専用の Skill が暗黙起動しない

### Release 3 固有

- [ ] `--dry-run` が完了条件と実行計画のみを出力し、ブランチとプルリクエストを作らない
- [ ] 受け入れ条件のない計画ファイルで停止する
- [ ] リリース用プルリクエストを下書きのまま残して完了条件を満たす
- [ ] Claude Code と Codex の組み込み `/goal` に渡した条件でループが終了する
- [ ] 中断後の再実行で、既存のブランチとプルリクエストを重複作成せず途中から再開する
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

## 未確認事項

着手前に解消するか、解消しないまま進める場合はリスクとして扱う。

| 項目 | 状態 | 解消方法 |
| --- | --- | --- |
| Kiro の全体配置（`~/.kiro/skills/`）で `allowed-tools` が事前承認として機能するか | **未確認**。非対話モードではホーム配下の読み取り自体が承認対象になり、プロジェクト配置との比較ができなかった | 対話モードで両配置を比較する |
| Codex の `<Skill 名>/agents/openai.yaml` のスキーマ | **未確認**。配置が Skill ディレクトリ配下であることは公式ドキュメントで確認できたが、各項目の受理可否は実機で確認していない | Codex CLI 実機で最小構成を作って検証する（Task 0-8 の前提） |
| Kiro に継続ループの代替手段があるか | **未確認**。`/goal` が存在しないことは確認したが、フックなど他の手段は調べていない | Kiro のフック機構を調査する |
| 統合後の frontmatter 合計サイズの適正上限 | **未決定** | 棚卸完了時点の実測値を基準に設定する（Task 0-7） |
| Kiro 全文読み込みの再発有無 | 2.16.1 では未再現。将来のバージョンでの挙動は不明 | 動作確認テストで占有率を継続検査する |
