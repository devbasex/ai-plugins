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
| [#158](https://github.com/devbasex/ai-plugins/issues/158) | モデルのアンサンブル利用の優位性を示す研究 | [issue-158-llm-ensemble-for-agentic-development.md](issue-158-llm-ensemble-for-agentic-development.md) |
| [#159](https://github.com/devbasex/ai-plugins/issues/159) | 研究データの収集基盤と OSS 運用の整備 | [issue-159-telemetry-and-oss-governance.md](issue-159-telemetry-and-oss-governance.md) |
| [#175](https://github.com/devbasex/ai-plugins/issues/175) | リリース後テストと振り返りの工程を追加する | [issue-175-release-verification-retrospective.md](issue-175-release-verification-retrospective.md) |
| [#116](https://github.com/devbasex/ai-plugins/issues/116) | どこにも配布されていない Skill 4 個を配布する | 未作成 |
| [#144](https://github.com/devbasex/ai-plugins/issues/144) | notion-writing: Notion ページ作成の落とし穴を Skill にする | 未作成 |
| [#156](https://github.com/devbasex/ai-plugins/issues/156) | cross-review に証拠ベースの adversarial review と評価基盤を入れる | 未作成 |
| [#161](https://github.com/devbasex/ai-plugins/issues/161) | 設計フェーズの成果物を規約化する Skill を作る | 未作成 |
| [#176](https://github.com/devbasex/ai-plugins/issues/176) | development-workflow の進行管理に GitHub Projects を使う | 未作成 |
| [#181](https://github.com/devbasex/ai-plugins/issues/181) | playwright-evidence の手順が置換対象を狭く説明している | 未作成 |
| [#182](https://github.com/devbasex/ai-plugins/issues/182) | pytest のテストが CI で実行されていない | 未作成 |
| [#186](https://github.com/devbasex/ai-plugins/issues/186) | 作業ツリーで相対パス編集すると主ディレクトリ向けの案内が出る | 未作成 |
| [#188](https://github.com/devbasex/ai-plugins/issues/188) | 複数の Pull Request をまとめてマージした後の版上げの担い手と時期 | 未作成 |

#175 の実装は v9.3.0 で公開済みだが、完了の定義に残るリリース後テストと振り返りが未了のため
issue は開いたままである。

「未作成」の 8 件は、着手するときに `/ndf:implementation-plan` で
`issues/issue-<番号>-<内容>.md` を 1 ファイル作る。

## ファイル名の付け方

`issue-<issue 番号>-<内容を表す英小文字とハイフン>.md` とする。issue 番号を持たない調査資料は
番号を付けず、内容だけで名前を付ける。1 ファイルに収まらない規模のときだけ
`issue-<番号>-<内容>/` のディレクトリにして、`01-` から始まる連番のファイルへ分ける。
