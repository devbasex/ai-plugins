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
| [#159](https://github.com/devbasex/ai-plugins/issues/159) | 研究データの収集基盤と OSS 運用の整備 | [issue-159-telemetry-and-oss-governance.md](issue-159-telemetry-and-oss-governance.md) |
| [#181](https://github.com/devbasex/ai-plugins/issues/181) | playwright-evidence の手順が置換対象を狭く説明している | 未作成 |
| [#182](https://github.com/devbasex/ai-plugins/issues/182) | pytest のテストが CI で実行されていない | 未作成 |
| [#193](https://github.com/devbasex/ai-plugins/issues/193) | worktree の手順が参照する `$NDF_SCRIPTS` が定義されていない | 未作成 |
| [#196](https://github.com/devbasex/ai-plugins/issues/196) | cross-review: 結果を残さずタイムアウトしたとき収束と判定される | 未作成 |
| [#197](https://github.com/devbasex/ai-plugins/issues/197) | `wt_extract_write_target` がファイル記述子の番号を書き込み先に拾う | 未作成 |
| [#201](https://github.com/devbasex/ai-plugins/issues/201) | `wt_extract_write_target` が関数定義・case・前置リダイレクトで誤る | 未作成 |
| [#209](https://github.com/devbasex/ai-plugins/issues/209) | 版を上げても説明文書の本文の版数が古いまま残る | 未作成 |
| [#214](https://github.com/devbasex/ai-plugins/issues/214) | gemini CLI の呼び出しと記述を agy CLI へ置き換える | 未作成 |
| [#215](https://github.com/devbasex/ai-plugins/issues/215) | 対応ランタイムへ agy CLI を追加し、4 ランタイム構成にする | 未作成 |
| [#216](https://github.com/devbasex/ai-plugins/issues/216) | cross-refactoring: 参加する 4 CLI を同じ扱いにする | 未作成 |
| [#217](https://github.com/devbasex/ai-plugins/issues/217) | cross-review のレビュー用作業ツリーが PR の head へ同期されない | 未作成 |
| [#221](https://github.com/devbasex/ai-plugins/issues/221) | 工程表にある工程を飛ばしても気づく手立てが無い | 未作成 |
| [#224](https://github.com/devbasex/ai-plugins/issues/224) | 「書く前に実行して確かめる」の対象に外部コマンドが入っていない | 未作成 |

「未作成」の 16 件は、着手するときに `/ndf:implementation-plan` で
`issues/issue-<番号>-<内容>.md` を 1 ファイル作る。

## ファイル名の付け方

`issue-<issue 番号>-<内容を表す英小文字とハイフン>.md` とする。issue 番号を持たない調査資料は
番号を付けず、内容だけで名前を付ける。1 ファイルに収まらない規模のときだけ
`issue-<番号>-<内容>/` のディレクトリにして、`01-` から始まる連番のファイルへ分ける。
