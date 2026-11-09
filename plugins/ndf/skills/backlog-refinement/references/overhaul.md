# 大整理（`--depth overhaul`）

**具体例**: 2026-11-01、このリポジトリで未完了 194 件を原因の型で分けると、異常系・回復系（型 A〜F）が 103 件で、
新機能と文書を除いた不具合の 82% だった。コードを横断して数えると、共通部品はあるのに通らずに済んでいた（結果の共通の形を使う
入口は 37 本中 24 本・例外を捨てる箇所 49・上限の直書き 195）。10 月の起票 289 件に対して完了 271 件で、1 件ずつ直しても減らない。
大方針（https://github.com/devbasex/ai-plugins/issues/2335 ）を立て、柱 9 本の子へ 113 件を付け直し、マイルストーンを組み直した。

見直し（[review.md](review.md)）は課題を 1 件ずつ評価する。大整理は課題の集まりを見て、**多くの課題に共通する原因**を探す。
対象は後回しを含む未完了の課題すべてである。起動の条件と持ち主は [depths.md](depths.md) にある。

## 6 つの手順

| # | 手順 | 中身 | 道具 |
| --- | --- | --- | --- |
| 1 | 分ける | 未完了の課題すべてを固定し、原因の型（`materials.cause_types`。無ければ用語集の「原因の型」A〜I）で分ける。件数で割って並列の worker で分担してよい | `overhaul snapshot`・`causes record` |
| 2 | 数える | 型ごとの件数・課題の集まる部品・マイルストーンごとの割合・月ごとの起票と完了 | `overhaul count` |
| 3 | 裏付ける | 型に対応する観点（例外の扱い・ログ・入出力とデータの形・重複した実装・設定の置き場）をコードの横断で数える | `overhaul measure`（`materials.code_measures` のコマンド） |
| 4 | 立てる | Issue 単位では直らない根本原因があれば、大方針の Issue（柱と受け入れ条件つき）を起票し、MVV 判定にかける | `apply` の `create`（`kind: policy`）・`project-mvv.py vet --kind issue` |
| 5 | 付け直す | 大方針の MVV 判定が「従う」のときだけ、解消しうる課題を柱の子へ付け直し、マイルストーンを組み直す | `apply` の `reparent` |
| 6 | 止める | 大方針の設計が終わるまで、該当の型の新しい課題を個別に直さず子として起票する、と大方針の本文に書く | 大方針の本文 |

最初に `candidates --depth review --run <run>` で大整理の回を始める。`apply` は記録のリポジトリをこの回の候補の記録で確かめ、
報告の `open_before` もこの回から取る。順位を付け直すときは `rank` にも同じ `--run <run>` を渡す。

```bash
python3 "$UPKEEP/upkeep.py" candidates --since-ref <前のリリースのタグ> --depth review --run <run>
python3 "$UPKEEP/upkeep.py" overhaul snapshot --out <作業ディレクトリ>
python3 "$UPKEEP/upkeep.py" causes record --classes <[{number, type, sub}] の JSON> --depth overhaul
python3 "$UPKEEP/upkeep.py" overhaul count --snapshot <作業ディレクトリ>/snapshot.json --classes <同じ JSON>
python3 "$UPKEEP/upkeep.py" overhaul measure
```

分けられなかった課題は I（その他）に入れ、件数を報告に載せる。根本原因が見つからなければ起票せず、「見つからなかった」を報告する。

## 付け直しと戻し

大方針は先に起票し（1 回目の `apply`）、番号が付いてから付け直す（2 回目の `apply`）。2 回目の plan には
`"depth": "overhaul"`・`"owner": "<run>"`・`"policy_verdict": "follow"`（1 回目の MVV 判定）と `reparent` を書く。
判定が「従う」以外なら付け直しへ進まず（`not_reparented`）、大方針は `needs-decision` を付けて人の判断待ちに置く。
新しい承認ゲートは作らない。

`apply` は最初の書き込みの前に、対象の全件の親とマイルストーンを `.ndf/backlog/overhaul/<run>/before.json` に書く。
途中で失敗したら結果の `revert_command` に戻すコマンドが載る。報告にはいつも戻すコマンドを書く。

```bash
python3 "$UPKEEP/upkeep.py" revert --run <run>   # 戻せなかった課題は items に一覧で出る
```

## 後回しの再評価

大整理の中で、後回しのマイルストーン（`materials.deferred_milestones`）の課題を前倒し・やらない・据え置きに分ける。
前倒しは直近のマイルストーンへ移し、やらないは [no-work.md](no-work.md) の必要条件で閉じる。分けられなかった課題は据え置きにする。

## 報告

見直しと同じ形（[review.md](review.md) の「報告」）に、次の `metrics` を足して `depth record --depth overhaul` に渡す。

| キー | 中身 |
| --- | --- |
| `types` | 型ごとの件数と割合（`overhaul count` の値） |
| `measures` | 裏付けの計測の値 |
| `root_cause` | 根本原因の有無と、大方針の番号（無ければ `null`） |
| `reparented` | 付け直した課題の番号 |
| `revert_command` | 付け直しを戻すコマンド |
| `deferred` | 後回しの再評価の結果（前倒し・やらない・据え置きの番号） |
