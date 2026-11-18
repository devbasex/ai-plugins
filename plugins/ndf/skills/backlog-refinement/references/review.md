# 見直し（`--depth review`）

**具体例**: 業務システムの Web サービスで、3 スプリントぶりに見直しが起動した。枝刈りで重複 4 件と、利用率 0.3% の CSV 出力の
機能（廃止の Issue を 1 件起動する）を落とす。順位を付け直し、アクセス解析の集計から「月次の帳票の出力が 1 回 40 秒かかり離脱が多い」を
ニーズの Issue に、帳票のライブラリの競合の調査から「出力が速く保守の続いている置き換え先」をシーズの Issue にする。
起票は出典つきで上限（既定 5 件）まで、MVV 判定が「従う」のものだけを順位付けへ入れる。

対象は後回しのマイルストーン（`materials.deferred_milestones`）を除く open の全件である。後回しの課題の再評価は大整理で行う
（[overhaul.md](overhaul.md)）。起動の条件と、担うセッションの決め方は [depths.md](depths.md) にある。

## 7 つの手順

この順で通し、通した順を報告の `order` に残す。先に減らしてから足すのは、足した課題を古い課題と同じ基準で順位付けし、
起票の上限を守るためである。

| # | `order` の値 | 手順 | 使う道具 |
| --- | --- | --- | --- |
| 1 | `prune` | 枝刈り: 重複・やらない・ルートコーズへの統合。利用の記録の設定があれば、利用率の低い機能を廃止の候補にする（調査の項目 `method.unused`） | `ndf issues candidates --depth review` と SKILL.md の手順 2A / 2B |
| 2 | `rank` | 優先順位の再評価と順位付け | SKILL.md の手順 2C（`ndf issues rank`） |
| 3 | `milestones` | マイルストーンの再編 | [milestones.md](milestones.md) |
| 4 | `needs` | ニーズの調査 | [research.md](research.md) の材料をこの場で調べる |
| 5 | `seeds` | シーズの調査の結果を読む | 同上 |
| 6 | `methods` | ホットスポット分析と失敗の記録の集計 | 調査の項目 `method.hotspot`・`method.failures` |
| 7 | `create` | 起票と MVV 判定 | `ndf issues apply` の plan の `create`・MVV の本文と照らす判定 |

## 調査

調査のサブコマンドは無い。`.ndf/project.json` の `materials` から調査の項目を並べ、項目ごとにこの場で調べて、出典
（URL・記録のパス・実行したコマンド）と調べた日時を作業ディレクトリに書く。設定の無い側面は `skipped_aspects` に
「材料なし（未設定）」で残し、そこからは起票しない。項目の作り方と読む範囲は [research.md](research.md) にある。

## 起票

起票は `ndf issues apply` の plan の `create` に書く。キーと検査は [actions_decide.py](../../../src/ndf/domains/backlog/actions_decide.py) の冒頭にある。

| 決まり | 中身 |
| --- | --- |
| 本文 | 出典（`sources`: URL・記録のパス・実行したコマンド）と、そのプロジェクトの MVV のどこに当たるか（`mvv_basis`）を持つ。どちらかが無ければ `rejected_create` |
| ラベル | 区別のラベル（`depths.labels` の `needs` / `seeds`。大方針は `policy`）を `apply` が付ける |
| 上限 | 1 回の見直しで `depths.review.issue_limit`（既定 5）件まで。超えた分は `not_created` として報告に載る |
| MVV 判定 | 本文をプロジェクト MVV の本文（`.ndf/mvv.md`）と照らして判定し、結果を `mvv_verdict` に書く。`follow` 以外は `needs-decision` が付き、順位付けへ入れない（見積に `waiting_decision: true` を付ける） |

plan には `"depth": "review"` を書く。

## 報告

最後に結果 JSON（[結果のコントラクト](../../../contracts/schemas/result.schema.json) の形）の報告を、この回の `candidates`・`rank`・`apply` の
結果から組んで作業ディレクトリに書き、深さの記録を残す（取れない値は `null`）。

```bash
ndf issues depth-record --depth review --event ran   # 秒の上限で打ち切ったら --event timed_out --left <残りの番号>
```

| `metrics` のキー | 中身 |
| --- | --- |
| `trigger` | 起動した条件（`ndf issues due review` の値） |
| `order` | 通した手順（上の表の値の並び） |
| `closed` | 閉じた課題の番号 |
| `created` / `not_created` | 起票した課題の番号 / 上限で起票しなかった題名 |
| `open_before` / `open_after` | 見直しの前と後の open の件数 |
| `rank_changes` | 順位の変わった課題（番号・前・後） |
| `skipped_aspects` | 飛ばした側面と理由 |
| `research` | どの材料で調べたか。`items` は材料の設定から出た調査の項目、`researched` はこの実行が調べた項目と出典の数（`[{id, checked_at, sources}]`）、`sources` は出典の数の和 |
| `waiting` | `needs-decision` を付けた課題と、承認の要る反映 |
