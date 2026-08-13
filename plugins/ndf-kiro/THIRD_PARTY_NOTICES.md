# サードパーティー告知

NDF プラグインの Skill を設計する際に参照した外部リポジトリと、そのライセンスを記録する。

**このファイルが編集元である。** `plugins/ndf-claude/` / `plugins/ndf-codex/` /
`plugins/ndf-kiro/` にある同名ファイルは `scripts/build-runtime-plugins.sh` が同期する
生成物であり、直接編集しない。Kiro では配布物をそのまま読ませないため、
`plugins/ndf-kiro/install.sh` が導入先（`--scope workspace` なら `.kiro/`、`global` なら
`~/.kiro/`）へ配置する。

## 転用の状況

現時点で、**上流の文章・コード・表現を転用していない。** 参照したのは工程の分け方や
判断基準といった設計方針であり、NDF の Skill はすべて書き下ろしである。

この告知を転用が発生していない時点から用意しているのは、同期の経路を先に作っておくため
である。転用が生じてから経路を足すと、配布物への反映漏れに気づけない。

**転用が発生した場合に行うこと:**

1. 該当ライセンスの全文を「ライセンス全文」節へ追記する
2. 転用元のファイル・行範囲と改変内容を、下の表と
   `upstream-skills.lock.yaml` の `adaptation` に記録する
3. Apache-2.0 の場合は、上流の著作権表示・ライセンス・`NOTICE`（存在する場合）を保持し、
   変更した旨を明記する
4. `bash scripts/build-runtime-plugins.sh` を実行し、3 ランタイムの配布物へ同期する

## 参照した上流リポジトリ

固定コミットと参照したパスは `upstream-skills.lock.yaml` に記録する（2026-08-13 時点）。

| 参照元 | ライセンス | 参照した内容 | 対応する NDF Skill |
| --- | --- | --- | --- |
| [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | MIT | 仕様先行の進め方、リポジトリ固有のテストコマンドを事前調査する手順、振る舞いをテストする方針、テストダブルの優先順、ドキュメントと静的設定にテスト駆動を強制しない判断、バグ修正を再現テストから始める順序 | `requirements-design`、`tdd-cycle` |
| [obra/superpowers](https://github.com/obra/superpowers) | MIT | 失敗が期待した理由で起きたことを確認する規律、通す実装を最小限に保つ規律、整理中はテストを通ったまま保つ規律、実行していないテストを「通った」と報告しない規律、完了前の検証 | `tdd-cycle`、`quality-gates` |
| [modu-ai/moai-adk](https://github.com/modu-ai/moai-adk) | Apache-2.0 | テストが乏しい既存コードで現状固定テストを先行させる考え方、小さな安全な状態を積み重ねる進め方、完了前の品質ゲート | `safe-refactoring`、`quality-gates` |

採用しなかった方針も記録しておく。

| 参照元 | 採用しなかったもの | 理由 |
| --- | --- | --- |
| modu-ai/moai-adk | 固定のカバレッジ閾値（下限・最終値） | 閾値は対象プロジェクトのカバレッジツール設定を唯一の基準とし、Skill 側に既定値を持たせない |
| modu-ai/moai-adk | 同リポジトリが DDD と呼ぶ枠組み | 指すものが Domain-Driven **Development** であり、Evans の Domain-Driven **Design** とは別物。前者は `safe-refactoring` の既存コード向け手順として扱う |
| [ramziddin/solid-skills](https://github.com/ramziddin/solid-skills) | メソッドの行数上限・インスタンス変数の個数上限といった数値規則 | 凝集度・結合度・変更理由・認知負荷・テスト容易性をレビュー質問として使う方針を採る |

## ライセンス

### Apache-2.0（modu-ai/moai-adk）

Apache-2.0 は、頒布物の受領者へ著作権表示・ライセンス・変更の記載・`NOTICE` の内容が
届くことを求める。2026-08-13 時点で同リポジトリに `NOTICE` ファイルは存在しない。

ライセンス全文: https://www.apache.org/licenses/LICENSE-2.0

### MIT（addyosmani/agent-skills、obra/superpowers、ramziddin/solid-skills）

MIT は、実質的な部分を複製・頒布する場合に著作権表示とライセンス表示の保持を求める。

各リポジトリの `LICENSE` ファイルを参照する。

- https://github.com/addyosmani/agent-skills/blob/main/LICENSE
- https://github.com/obra/superpowers/blob/main/LICENSE
- https://github.com/ramziddin/solid-skills/blob/main/LICENSE

### ライセンス全文

転用が発生した時点で、該当ライセンスの全文をこの節へ追記する。現時点では転用がないため
空である。

## NDF 本体

NDF プラグイン本体は MIT ライセンスである（リポジトリ直下の `LICENSE`）。
