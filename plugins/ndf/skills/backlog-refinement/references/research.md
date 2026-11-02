# ニーズとシーズの調査

**具体例**: 開発ツールのプラグインは、`materials.needs.usage` に Skill の利用統計を集計するコマンド（`aggregate_only: true`）を、
`seeds.libraries` に pytest（競合の候補 ward）を宣言する。調査は「`needs.usage:<名>`」「`seeds.library:pytest`」
「`seeds.alternatives:pytest`」の項目に分かれ、conductor がプランの完了を待つ間に 1 項目ずつ調べて
`.ndf/backlog/research/` に出典つきで書く。Web サービスなら同じ項目に、アクセス解析の集計のコマンドと帳票のライブラリが入る。

**フローは共通で、何を材料に調べるかはプロジェクトが宣言する。** 調査は記録を書くだけで、Issue の起票・クローズ・
ラベル・マイルストーン・順位を変えない。起票は見直しの手順 7 だけが行う（[review.md](review.md)）。

## 材料の宣言

`.ndf/project.json` の `materials`。形は `development-workflow/schemas/project.schema.json` が正本である。案は
`project-decl.py measure` が問い（`question`）で出し（README の冒頭・直接の依存・解析の SDK の依存を根拠にする）、
人が確かめて書く（`development-workflow` の `references/project-analysis.md`）。

| キー | 中身 | 調査の項目 |
| --- | --- | --- |
| `product` | プロダクトの種類（`kind`）と利用者（`users`） | すべての項目の前提として渡す |
| `needs.usage[]` | 利用の記録を読むコマンドと範囲（`name`・`command`・`scope`） | `needs.usage:<name>`・`method.unused:usage` |
| `needs.market[]` | 比べる相手の製品（`name`・`url`・`viewpoints`） | `needs.market:<name>` |
| `seeds.libraries[]` | 調べる依存（`name`・`ecosystem`・`alternatives`・`viewpoints`）。直接の依存のうち人が残したものだけ | `seeds.library:<name>`（変化）・`seeds.alternatives:<name>`（競合） |
| `seeds.environment[]` | 法令・業界標準・ブラウザや OS など見ておく変化（`name`・`url`） | `seeds.environment:<name>` |
| `deferred_milestones[]` | 後回しのマイルストーンの題名（見直しの対象から外す） | — |
| `cause_types[]` | 原因の型の一覧（`id`・`name`・`defect`）。無ければ用語集の「原因の型」A〜I | 大整理 |
| `code_measures[]` | 大整理の裏付けの計測のコマンド（`name`・`command`・`types`） | 大整理 |
| `failures[]` | 失敗の記録の読み先（開発ループの記録・振り返り・障害の記録のパス） | `method.failures:loop` |

`materials` があれば、ホットスポット（`method.hotspot:git`。git の履歴をスクリプトが数えて渡す）と失敗の集計
（`method.failures:loop`）も調べる。宣言の無い側面は `skipped_aspects` に「材料なし（未宣言）」で載り、推測で調べない。
`materials` が無いプロジェクトはすべての側面を飛ばす。

シーズの 1 つの依存は 2 つの項目になる。**変化**（新しい版の機能・非推奨・保守の停止・脆弱性・ライセンスの変更）と、
**競合**（置き換え先の候補と比べた観点: 保守の状況・性能・ライセンス・移行の費用。自前の実装を置き換えられるか）である。

## 利用の記録を読む範囲

本番の利用の記録には個人情報と秘密が含まれうる（共通原則 C1・C3）。

- `scope.aggregate_only` が `true` の行だけを読む。`false` の行と、`scope` の無い行は読まず、`skipped_aspects` に理由を書く
- 宣言が無ければ利用の記録を何も読まない
- コマンドはスクリプトが打ち（期間 `scope.days` は環境変数 `NDF_USAGE_DAYS` で渡す）、`scope.mask`（正規表現。当たった所を
  `***` にする）と秘密の伏せ字（`secret_redact`）を通した出力だけを調査へ渡す。調査の supervisor は読み先へ自分でつながず、
  手元を読む項目には Web の Tool を、Web を読む項目には手元を読む Tool を渡さない
- 読み先の認証は人が済ませる。NDF は認証の設定をしない

## 待ち時間の調査

開発ループの `loop sprint` は、`supervise.py wait` と並べて背景で打つ 1 行を `metrics.research` に返す。

```bash
# Bash の run_in_background: true で、wait と並べて起動する
python3 "$UPKEEP/upkeep.py" research run --until <done のパス> --queue <done>.plans.json --lap <周回> --loop <名>
```

| 決まり | 中身 |
| --- | --- |
| 持ち主 | 始めに `owner claim --what research` を取る。他の有効な持ち主がいれば何もせず終了コード 10 で終える |
| 空き | queue の一覧で走っているプランの本数 + 調査 1 本が `--max`（既定 6）以下のときだけ項目を始める |
| 止め方 | 完了のファイル（`--until`）が出たら新しい項目を始めない。項目の途中の結果は書かない |
| 1 回の量 | `--max-items`（既定 3）項目まで。1 項目の費用と秒は `depths.research.item_usd`（1 ドル）・`item_seconds`（900 秒）まで。超えた結果は捨てる |
| 記録 | 出典（`url` / `path` / `command`）と調べた日時のそろった結果だけを `<側面>--<対象>.json` に書く。費用と秒は周回の記録の `research` 行に入り、予算に数える |
| 外部 | Web は読み取りだけ（WebFetch・WebSearch）。外部へ書き込まない |

次の待ちは、記録の無い項目から続ける。見直しの中では `research run --run <持ち主の run> --since <前の見直しの at>` で、
足りない項目と古い項目だけを調べる。
