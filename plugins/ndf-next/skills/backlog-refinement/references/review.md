# 見直し（`--depth review`）

**具体例**: 業務システムの Web サービスで、3 スプリントぶりに見直しが起動した。枝刈りで重複 4 件と、利用率 0.3% の CSV 出力の
機能（廃止の Issue を 1 件起こす）を落とす。順位を付け直し、アクセス解析の集計から「月次の帳票の出力が 1 回 40 秒かかり離脱が多い」を
ニーズの Issue に、帳票のライブラリの競合の調査から「出力が速く保守の続いている置き換え先」をシーズの Issue にする。
起票は出典つきで上限（既定 5 件）まで、MVV 判定が「従う」のものだけを順位付けへ入れる。

対象は後回しのマイルストーン（`materials.deferred_milestones`）を除く open の全件である。後回しの課題の再評価は大整理で行う
（[overhaul.md](overhaul.md)）。起動の条件と持ち主は [depths.md](depths.md) にある。

## 7 つの手順

この順で通し、通した順を報告の `order` に残す。先に減らしてから足すのは、足した課題を古い課題と同じ基準で順位付けし、
起票の上限を守るためである。

| # | `order` の値 | 手順 | 使う道具 |
| --- | --- | --- | --- |
| 1 | `prune` | 枝刈り: 重複・やらない・ルートコーズへの統合。利用の記録の宣言があれば、利用率の低い機能を廃止の候補にする（調査の項目 `method.unused`） | `candidates --depth review` と SKILL.md の手順 2A / 2B |
| 2 | `rank` | 優先順位の再評価と順位付け | SKILL.md の手順 2C（`rank`） |
| 3 | `milestones` | マイルストーンの再編 | [milestones.md](milestones.md) |
| 4 | `needs` | ニーズの調査の結果を読む | `research plan` と調査の記録 |
| 5 | `seeds` | シーズの調査の結果を読む | 同上 |
| 6 | `methods` | ホットスポット分析と失敗の記録の集計 | 調査の項目 `method.hotspot`・`method.failures` |
| 7 | `create` | 起票と MVV 判定 | `apply` の `create`・`project-mvv.py vet --kind issue` |

## 調査の結果の読み方

```bash
python3 "$UPKEEP/upkeep.py" research plan --since <前の見直しの at>
python3 "$UPKEEP/upkeep.py" research run --run <持ち主の run> --since <前の見直しの at> --max-items <todo の件数>
```

`todo` は記録の無い項目と前の見直しより前に調べた古い項目、`skipped_aspects` は宣言の無い側面（「材料なし（未宣言）」）である。
前の見直しの `at` は `depths.jsonl` の最後の `review` の `ran` の行にある（無ければ `--since` を省く）。待ち時間の調査で
溜まった項目は調べ直さず、**`todo` の項目だけを `research run --run` でこの場で調べ**（持ち主の宣言を取り直さず、解放もしない）、
溜まった記録（`.ndf/backlog/research/*.json`）の `findings` を読む。宣言の無い側面からは起票しない。手順は [research.md](research.md) にある。

## 起票

起票は `apply` の plan の `create` に書く。キーと検査は `scripts/upkeep_actions.py` の冒頭にある。

| 決まり | 中身 |
| --- | --- |
| 本文 | 出典（`sources`: URL・記録のパス・実行したコマンド）と、そのプロジェクトの MVV のどこに当たるか（`mvv_basis`）を持つ。どちらかが無ければ `rejected_create` |
| ラベル | 区別のラベル（`depths.labels` の `needs` / `seeds`。大方針は `policy`）を `apply` が付ける |
| 上限 | 1 回の見直しで `depths.review.issue_limit`（既定 5）件まで。超えた分は `not_created` として報告に載る |
| MVV 判定 | 本文を `project-mvv.py vet --body <本文> --kind issue` にかけ、結果を `mvv_verdict` に書く。`follow` 以外は `needs-decision` が付き、順位付けへ入れない（見積に `waiting_decision: true` を付ける） |

plan には `"depth": "review"` と、`owner claim` が返した `"owner": "<run>"` を書く。持ち主でない実行の `apply` は課題を書かない。

## 報告

最後に統一 JSON レポート（`scripts/lib/README.md` の「手順のスクリプトの結果」の形）を作業ディレクトリに書き、
`depth record` と `owner release` を打つ。報告の土台は `report --depth review` が記録から組む（記録から取れない値は `null`。
`open_after`・`rank_changes`・`waiting` と、`--order` を省いたときの `order` は手で埋める）。

```bash
python3 "$UPKEEP/upkeep.py" report --depth review --run <run> --order prune,rank,milestones,needs,seeds,methods,create > <報告の JSON>
python3 "$UPKEEP/upkeep.py" depth record --depth review --run <run> --report <報告の JSON> [--cost USD] [--seconds S]
python3 "$UPKEEP/upkeep.py" owner release --run <run>
```

| `metrics` のキー | 中身 |
| --- | --- |
| `trigger` | 起動した条件（`depth due` の値） |
| `order` | 通した手順（上の表の値の並び） |
| `closed` | 閉じた課題の番号 |
| `created` / `not_created` | 起票した課題の番号 / 上限で起票しなかった題名 |
| `open_before` / `open_after` | 見直しの前と後の open の件数 |
| `rank_changes` | 順位の変わった課題（番号・前・後） |
| `skipped_aspects` | 飛ばした側面と理由 |
| `research` | どの材料で調べたか。`items` は材料の宣言から出た調査の項目（`research plan` の `items`）、`researched` はこの実行が調べた項目と出典の数（`[{id, checked_at, sources}]`）、`sources` は出典の数の和 |
| `waiting` | `needs-decision` を付けた課題と、承認の要る反映 |
