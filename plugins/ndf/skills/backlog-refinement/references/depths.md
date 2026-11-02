# 棚卸しの 3 つの深さ

棚卸しは、遅れたときの害の出方で 3 つの深さに分けて回す。分け方はプロジェクトの性質によらない。
材料（何を調べるか）だけをプロジェクトが `.ndf/project.json` の `materials` で宣言する（[research.md](research.md)）。

| 深さ | `--depth` | 頻度（既定） | 対象 | 行うこと | 遅れると |
| --- | --- | --- | --- | --- | --- |
| 差分の棚卸し | `diff`（既定） | 毎スプリント | 差分から機械で決まる候補とそのスプリントで起票した課題（後回しの課題も含む） | 変更で食い違った本文の訂正・直ったのに開いたままの課題を閉じる・閉じた親の子の見直し・起票した課題への見積の段階の付与 | 直った課題や前提の違う課題を次のスプリントが選ぶ |
| 見直し | `review` | 3 回に 1 回 | 後回しのマイルストーンを除く open の全件 | 枝刈り・順位付け・マイルストーンの再編・ニーズとシーズの調査からの起票（[review.md](review.md)） | 件数が増え、方向を変える課題が入らない |
| 大整理 | `overhaul` | 12 回に 1 回 | 後回しを含む未完了の全件 | 原因の型で分け、Issue 単位で直らない根本原因を探して組み直す。後回しの再評価を含む（[overhaul.md](overhaul.md)） | 1 件ずつ直しても減らない |

差分の棚卸しは順位の表・前倒しと後ろ倒し・マイルストーンの設定・やらない・起票・付け直しを反映しない
（`upkeep.py apply` が `wrong_depth` で退ける）。回数は開発ループの周回で数え、`--loop` を使わないときはスプリントで数える。

## 起動の条件

まとめのプランは、差分の棚卸しの後に「大整理 → 見直し」の順で起動の判定を打つ。両方が起動する回は、組み直した後の
課題で順位を付けるため大整理を先にする。判定は `upkeep.py depth due review|overhaul` が行い、起動するとき 0、
しないとき 3 を返す。どちらの判定も `.ndf/backlog/depths.jsonl` に行を残す（起動しないときは `not_due` と判定の値）。

| 深さ | 条件（`trigger`） | 既定 |
| --- | --- | --- |
| 見直し | `count`: 前の見直しの後の差分の棚卸しが N 回 | `review.every` 3 |
| 見直し | `open_growth`: open が前の見直しの時点から増えた割合 | `review.open_growth` 0.3 |
| 見直し | `carry`: 持ち越しが候補の上限の何倍か | `review.carry_factor` 10 |
| 見直し | `no_issue`: 選べる課題が尽きた（`loop select` が `review_first` を返した） | — |
| 大整理 | `count`: 前の大整理の後の差分の棚卸しが M 回 | `overhaul.every` 12 |
| 大整理 | `created_over_closed` / `open_rising`: 前の大整理の後の見直しが続けて、起票が閉じた件数を上回った / open が増えた | `overhaul.streak` 3（回） |
| 大整理 | `cause_share`: 不具合の型の中で 1 つの型が占める割合 | `overhaul.cause_share` 0.3 |
| どちらも | `manual`: 人が呼んだ（`--manual`） | — |

値は `.ndf/backlog.json` の `depths` で上書きできる（`review`・`overhaul`・`research`・`owner`・`labels` のキーごとに重ねる）。
宣言が無いプロジェクトでも既定の回数で起動し、材料の無い側面を飛ばして止まらない。

選べる課題が尽きたとき、開発ループは止まる前に見直しを 1 回挟む（`review_first`）。見直しの後も 0 件なら `no_issue` で止まる。

## 見直しの持ち主

見直し・大整理・待ち時間の調査を行うのは 1 つのセッション（見直しの持ち主）だけである。並列のセッションが同じ調査に
費用を払い、同じ課題を 2 回起票し、互いの反映を上書きするのを防ぐ。

```bash
python3 "$UPKEEP/upkeep.py" owner claim --what review     # 0 = 持ち主になった / 10 = 他の持ち主がいる
python3 "$UPKEEP/upkeep.py" owner touch --run <run> [--item <調査の項目>]
python3 "$UPKEEP/upkeep.py" owner release --run <run>
python3 "$UPKEEP/upkeep.py" owner status
```

- 宣言はメインディレクトリの `.ndf/backlog/owner.json` に置き、錠（`owner.json.lock`）の中でだけ作る・書き換える
- 有効な宣言は、終わった時刻が無く、最後に動いた時刻から寿命（`owner.ttl` 900 秒）の内で、同じホストなら pid が生きているもの
- 持ち主は項目を 1 つ進めるたびと `owner.beat`（120 秒）ごとに `touch` する
- 無効な宣言は次の `claim` が引き取り、`taken_from` に前の持ち主を書く。引き取った側は調査の記録に残った項目の続きから進める
- 持ち主でないセッションの `apply --depth review|overhaul` は課題を書かない。`loop status` が持ち主を表示する

## 記録

| ファイル（`.ndf/backlog/`） | 中身 |
| --- | --- |
| `depths.jsonl` | 深さごとの実行・判定の行（`depth`・`event`（`ran` / `not_due` / `skipped`）・`trigger`・open の前後・閉じた件数・起票した件数・飛ばした側面・順・費用・秒） |
| `causes.jsonl` | 課題ごとの原因の型（見直しと大整理が `upkeep.py causes record` で足す） |
| `owner.json` | 見直しの持ち主の宣言 |
| `research/` | 調査の記録（項目ごとの 1 ファイル） |
| `overhaul/<run>/` | 大整理の固定の一覧・集計・付け直しの前の状態 |

行は足すだけで書き換えない。開発ループは周回を締めるとき、周回の間の `depths.jsonl` の行と、その周回で調べた調査の
費用と秒を周回の記録へ写し、調査の費用を予算に数える。
