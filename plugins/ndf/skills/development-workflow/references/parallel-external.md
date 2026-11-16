# 外のオーガナイザーのレーン（`NDF_ORGANIZER_DIR`）

並列開発のオーガナイザーの手順の正本は [parallel.md](parallel.md) にある。この文書は、そのうち外のオーガナイザーのレーンの決まりを持つ。

## 決まり

環境変数 `NDF_ORGANIZER_DIR` があるセッションは、外のオーガナイザー（サービスが実行ジョブの間で動かすまとめ役など）のレーンである。
名前は `NDF_ORGANIZER_LANE` で、依頼と答えは置き場のファイルで受け渡す（形は `contracts/forms/parallel-external.json`）。
並列の計画（`.ndf/parallel/`）は使わず、計画名の代わりに `--external` を付ける。

| 点 | 決まり |
| --- | --- |
| 本番のステージ | [parallel.md](parallel.md) の「レーンの側」の表のとおり経路で決まる。テンプレートで出すリポジトリではリリースの依頼のプラン（`parallel.py request --external`）になり、依頼を `requests/<id>.json` へ書いて 10 で終わる。自分の Pull Request がすでに本番チャネルにあれば書かずに 0 で終わる |
| 答え | `parallel.py await --external --from <プランの状態ディレクトリ>/work/request.txt` が `answers/<id>.json` を読む。ループのレーンはループの 1 回の境で読む（[parallel.md](parallel.md) の「レーンの側」と同じ） |
| 問い | 設計の承認、リリースの Pull Request で届く経路のリリース承認ゲート、承認ゲートの外の問いは依頼にしない。今どおり止まって人へ戻す |
| 1 台の中のオーガナイザー | 起動しない。`NDF_FACTORY_RUN` がある実行ジョブでは `parallel.py init`・`start`・`run` が 1 で断る |

## リリースの実行（`--parallel-helper --external`）

外のオーガナイザーがまとめたリリース 1 つのために起こすセッションは、次の 1 行で始める。`batch.json` の手（`step`）ごとに 1 手を打って終える。

```text
/goal /ndf:development-workflow --parallel-helper --external -- 終わり: 作業の報告を出したとき
```

1. `python3 "$NDF_SCRIPTS/parallel.py" compose --external` を打つ。`metrics.pipeline` が流すものを決める
2. `metrics.pipeline` を流す（待ちは背景で打ち、完了の通知を待つ）

| `step` | 流すもの | 終わり方 |
| --- | --- | --- |
| `present` / `represent` | 提示 1（`represent` は提示 2）の提示のプラン → 本番のプラン | 提示のプランがリリース承認ゲートで止まったら `metrics.record` の 1 行で提示を書き返し、「判断が要る」で終える。流れ切れば手順 3 |
| `confirm:<k>` | 答えの記録 → 提示 k のプランを `approve` から → 提示 k の本番のプラン | 手順 3。確定しない提示の本番のプランは流さない |
| `continue` | 止まった本番のプランを `--resume` で続ける | 手順 3 か、直せない失敗を問いにして止まる |

3. `metrics.settle` の 1 行（`parallel.py settle --external <連番> --done <D>`）を打つ。届いたステージと結果を置き場へ書き、作業の報告で終える

CI の失敗・衝突・版数の衝突は一時レーン（[parallel.md](parallel.md) の「一時レーン（`--parallel-helper`）」）と同じくリリースプランの直しの手順で直す。直せない失敗は「判断が要る」で止まる。
