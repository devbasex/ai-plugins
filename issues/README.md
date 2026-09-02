# issues/ の構成

作業中の調査・実装計画・依頼文を置く。**1 つの issue につき 1 ファイル**とし、
完了したものは [old/](old/) へ移す。

| 場所 | 中身 |
| --- | --- |
| `issues/*.md` | 未完了の issue の実装計画・研究メモ。1 issue = 1 ファイル |
| `issues/old/` | 完了した issue の計画・実機試行の記録・報告書・調査資料 |

`old/` の中身は履歴として読むためのもので、運用判断の根拠には使わない。確定した仕様は
[docs/specifications/](../docs/specifications/) を、リリース済みの変更点は `CLAUDE.md` を参照する。

## 未完了の issue

| issue | 内容 | ファイル |
| --- | --- | --- |
| [#113](https://github.com/devbasex/ai-plugins/issues/113) | cross-refactoring の進行の設計見直しと不具合 3 件の修正 | [issue-113-cross-refactoring.md](issue-113-cross-refactoring.md) |
| [#116](https://github.com/devbasex/ai-plugins/issues/116) | どこにも配布されていない Skill 4 個を配布する | 未作成 |
| [#144](https://github.com/devbasex/ai-plugins/issues/144) | notion-writing: Notion ページ作成の落とし穴を Skill にする | 未作成 |
| [#156](https://github.com/devbasex/ai-plugins/issues/156) | cross-review に証拠ベースの adversarial review と評価基盤を入れる | 未作成 |
| [#158](https://github.com/devbasex/ai-plugins/issues/158) | モデルのアンサンブル利用の優位性を示す研究 | [issue-158-llm-ensemble-for-agentic-development.md](issue-158-llm-ensemble-for-agentic-development.md) |
| [#159](https://github.com/devbasex/ai-plugins/issues/159) | 研究データの収集基盤を整える | [issue-159-telemetry.md](issue-159-telemetry.md) |
| [#181](https://github.com/devbasex/ai-plugins/issues/181) | playwright-evidence の手順が置換対象を狭く説明している | 未作成 |
| [#182](https://github.com/devbasex/ai-plugins/issues/182) | pytest のテストが CI で実行されていない | [parallel-batch-03/01-issue-182.md](parallel-batch-03/01-issue-182.md) |
| [#193](https://github.com/devbasex/ai-plugins/issues/193) | worktree の手順が参照する `$NDF_SCRIPTS` が定義されていない | 未作成 |
| [#196](https://github.com/devbasex/ai-plugins/issues/196) | cross-review: 結果を残さずタイムアウトしたとき収束と判定される | [parallel-batch-03/02-issue-196.md](parallel-batch-03/02-issue-196.md) |
| [#197](https://github.com/devbasex/ai-plugins/issues/197) | `wt_extract_write_target` がファイル記述子の番号を書き込み先に拾う | 未作成 |
| [#201](https://github.com/devbasex/ai-plugins/issues/201) | `wt_extract_write_target` が関数定義・case・前置リダイレクトで誤る | 未作成 |
| [#209](https://github.com/devbasex/ai-plugins/issues/209) | 版を上げても説明文書の本文の版数が古いまま残る | [parallel-batch-03/04-issue-209.md](parallel-batch-03/04-issue-209.md) |
| [#214](https://github.com/devbasex/ai-plugins/issues/214) | gemini CLI の呼び出しと記述を agy CLI へ置き換える | 未作成 |
| [#215](https://github.com/devbasex/ai-plugins/issues/215) | 対応ランタイムへ agy CLI を追加し、4 ランタイム構成にする | 未作成 |
| [#216](https://github.com/devbasex/ai-plugins/issues/216) | cross-refactoring: 参加する 4 CLI を同じ扱いにする | 未作成 |
| [#217](https://github.com/devbasex/ai-plugins/issues/217) | cross-review のレビュー用作業ツリーが PR の head へ同期されない | [parallel-batch-03/03-issue-217.md](parallel-batch-03/03-issue-217.md) |
| [#221](https://github.com/devbasex/ai-plugins/issues/221) | 工程表にある工程を飛ばしても気づく手立てが無い | 未作成 |
| [#224](https://github.com/devbasex/ai-plugins/issues/224) | 「書く前に実行して確かめる」の対象に外部コマンドが入っていない | 未作成 |
| [#231](https://github.com/devbasex/ai-plugins/issues/231) | projects-sync が工程「設計レビュー」を受け付けない | 未作成 |
| [#232](https://github.com/devbasex/ai-plugins/issues/232) | 起点をリポジトリの根へ置くと playwright-kit-ops のテストが収集されない | 未作成 |
| [#233](https://github.com/devbasex/ai-plugins/issues/233) | bash / jq / git が無い環境でテストが実行されないまま終了コード 0 になる | 未作成 |
| [#235](https://github.com/devbasex/ai-plugins/issues/235) | cross-refactoring のテストが実行環境の git の身元へ依存している | 未作成 |
| [#236](https://github.com/devbasex/ai-plugins/issues/236) | OSS としてメンテナーを募れる状態にリポジトリを整える（親） | 未作成 |
| [#237](https://github.com/devbasex/ai-plugins/issues/237) | `main` / `develop` を ruleset で保護し、マージの設定とラベルを揃える | 未作成 |
| [#238](https://github.com/devbasex/ai-plugins/issues/238) | `CONTRIBUTING.md` などのコミュニティ健全性ファイルを置く | 未作成 |
| [#239](https://github.com/devbasex/ai-plugins/issues/239) | issue / Pull Request のテンプレートと `CODEOWNERS`・`dependabot.yml` を置く | 未作成 |
| [#240](https://github.com/devbasex/ai-plugins/issues/240) | `README.md` から `CHANGELOG.md` を分離する | 未作成 |
| [#241](https://github.com/devbasex/ai-plugins/issues/241) | `GOVERNANCE.md` を置き、メンテナーを募る導線を作る | 未作成 |
| [#242](https://github.com/devbasex/ai-plugins/issues/242) | 振り返りの記録先を `docs/development-history/` から issue へ移す | 未作成 |
| [#243](https://github.com/devbasex/ai-plugins/issues/243) | 工程の進行記録を横断的な Skill へ集約し、記録の中身を決める | 未作成 |
| [#244](https://github.com/devbasex/ai-plugins/issues/244) | 巻き直しの後に state.json の head_branch が古いままになる | 未作成 |
| [#245](https://github.com/devbasex/ai-plugins/issues/245) | cross-review の SKILL.md が 500 行の上限に張り付いている | 未作成 |
| [#246](https://github.com/devbasex/ai-plugins/issues/246) | 振動検知が path:line の一致だけで測るため同趣旨の指摘を拾えない | 未作成 |
| [#248](https://github.com/devbasex/ai-plugins/issues/248) | 更新案内の見出しの検査が接尾辞付きの版数を読めない | 未作成 |
| [#252](https://github.com/devbasex/ai-plugins/issues/252) | 主ディレクトリの未コミット変更を develop へ移し、作業ツリーの起点を正す | 未作成 |

「未作成」の 29 件は、着手するときに `/ndf:implementation-plan` で
`issues/issue-<番号>-<内容>.md` を 1 ファイル作る。

並行して進める複数の課題は `parallel-batch-<連番>/` にまとめる。全体指示書（`00-overview.md`）が
担当どうしの境界とマージの順序を定め、担当ごとの指示書が受け入れ条件と設計を持つ。

## ファイル名の付け方

`issue-<issue 番号>-<内容を表す英小文字とハイフン>.md` とする。issue 番号を持たない調査資料は
番号を付けず、内容だけで名前を付ける。1 ファイルに収まらない規模のときだけ
`issue-<番号>-<内容>/` のディレクトリにして、`01-` から始まる連番のファイルへ分ける。
