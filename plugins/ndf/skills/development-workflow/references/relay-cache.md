# 待つ子のキャッシュの起床（Claude Code だけ）

中継のラッパー（[relay.md](relay.md)）が起動した claude（子）のうち、裏の作業を待つ子のキャッシュの扱いを書く。

**裏の作業（supervisor など）を待って応答を終えた子を、プロンプトのキャッシュの寿命が切れる手前で短く応答させ、待ちを終えたときの書き直しを避ける。**
例: conductor が supervisor を起動して 70 分待つとき、1 時間の寿命の 600 秒前（50 分の時点）に起床の文を 1 回受け、「待機を続ける」の 1 行で応答を終える。
supervisor の知らせが届いた応答は、文脈を読み出しの値段で読む。

| 語 | 意味 |
| --- | --- |
| 起床の hook | 中継の子だけが持つ Stop の hook（予算は `hook.budget.stop-rewake`）。Stop ごとにバックグラウンドで起動し（`asyncRewake`）、起床の時点まで眠って確かめ直し、起床させるなら終了コード 2 で終わる |
| 起床の間隔 | 最後の応答の時刻から起床までの秒。寿命から余白（`cache.wake` の `seconds`）を引いた値 |
| 起床の回数の上限 | 起床の費用の合計が書き直し 1 回の追加の費用 `(w − r) × S` を上回らない最大の回数（`S` は文脈の token） |
| 書き直し | キャッシュが切れた後の応答で、文脈の全体をキャッシュへ書き込むこと |

- **既定では働かない。** ラッパーは、上限の設定の `cache.rewake` の `versions` に載った Claude Code のバージョンの子にだけ、起床の hook の登録（`--settings`）を渡す。一覧は実測を通ったバージョンを載せる Pull Request で足す
- **起床させるのは作業を待つ子だけで、監視（`ndf lanes wait` など）だけを待つ子は起床させない。** 寿命が 5 分の応答・回答待ちの質問がある間・起床の応答でキャッシュが当たらなかった会話でも起床させない
- 値（余白・倍率・起床の費用 `d`・当たりの境・バージョンの一覧）はすべて上限の設定の `cache.*` にある。直すのは方針のファイルで、コードは変えない

**起床の記録。** 起床の hook は、ラッパーの保存先の `cache-log.jsonl`（`log.jsonl` と同じディレクトリ）へ 1 行ずつ書く。どの行も
`at`・`kind`・`launch`・`session_id`・`claude_version`・`wait_id`（待ちの始まりの応答の時刻）を持つ。

| `kind` | いつ |
| --- | --- |
| `wait_start`・`wait_end` | 待ちが始まった・待ちを終える入力（利用者の入力か裏の作業の知らせ）を確かめた |
| `wake`・`wake_result` | 起床させた・起床の応答を突き合わせた（`outcome` は `woke`・`preempted`・`truncated`・`unknown`） |
| `stop_waking` | 起床をやめた（`scope` は `session`・`wait`・`once`、`reason` は `miss`・`limit` など） |
| `wait_void`・`contest`・`arrival`・`work_stop` | 待ちを取り消した・2 つの hook が同じ応答を取り合った・後の応答の到着で予約を失効させた・作業を待つ Stop で世代を取った |

**見直しの手順（`wake_result` が 20 件たまるごと）。** `ndf relay cache report [--since <日時>]` で全ラッパーの記録を集計する。
費用は各行に書いた倍率で出すので、方針を直した後も過去の行の費用は変わらない。

1. `metrics.hit_rate_by_age` で、当たりの率が 95% を下回る `age` の幅が起床の時点（既定 50 分）を含めば、`cache.wake` の `seconds`（余白）を広げる
2. `metrics.d_median`（起床の書き込みと出力の費用の中央値）をもとに、`cache.wake-cost` の `count` を直す。`count` は 1 以上の整数しか受け付けないため、レポートの値をそのまま写さない。中央値が数値なら、小数点以下を切り上げ、1 未満なら 1 にした整数を `count` に設定する。`null`（費用の分かる起床が 1 件も無い）なら設定を変えない
3. `metrics.by_version` で、作業を待つ Stop（`work_stops`）が 20 回以上たまったバージョンの `lost_ratio` が 0.1 を越えたら、`cache.rewake` の `versions` からそのバージョンを外す Pull Request を作る
4. `estimated_avoided_*` は避けた書き直しの推定（起床しなかった場合は観測できない）で、確かめた額として読まない。`unpriced_waits`（費用の分からない起床を持つ待ち）は推定に入らない
