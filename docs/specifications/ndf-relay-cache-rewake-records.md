# 中継のキャッシュの起床: 設定・状態・起床の記録・集計

[ndf-relay-cache-rewake.md](ndf-relay-cache-rewake.md)（起床の hook の仕様）が使う値と、ファイルの形と、
`ndf relay cache report` の入出力の契約を持つ文書である（#4068・#4199）。語は本体の「用語」のとおりである。

## データ・設定

### 上限の設定（`plugins/ndf/contracts/limits/<方針の名前>.json`）

方針 1 つにつき 1 ファイルで、スキーマは `contracts/schemas/limits.schema.json`、読み手は `contracts/limits.py` の `Policy` である。

| 方針 | 値 | 意味 | 読み手 |
| --- | --- | --- | --- |
| `cache.wake` | `seconds` 600・`interval` 60 | 起床の時点の余白（1 時間の寿命からこの秒を引く）と、眠る hook が世代を確かめる間隔 | 起床の hook・予算の検査 |
| `cache.write-ratio` | `ratios` の `1h` 2.0・`5m` 1.25 | 寿命ごとの書き込みの倍率 | 起床の hook |
| `cache.read-ratio` | `default` 0.1。`ratios` の `claude-opus-5-5`・`claude-sonnet-5-5` は 0.05、`claude-fable-5-1`・`claude-fable-5-2`・`claude-mythos-5-1`・`claude-mythos-5-2` は 0.025 | モデルごとの読み出しの倍率。表に無いモデルは `default` | 起床の hook |
| `cache.output-ratio` | `default` 5 | 出力の倍率（集計で起床の応答の費用を出す） | 起床の hook |
| `cache.wake-cost` | `count` 2,000 | 起床の応答 1 回の費用 `d`（入力の token に換算した量） | 起床の hook |
| `cache.hit` | `ratio` 0.9 | 起床か待ちを終えた入力の最初の呼び出しの読み出しが、前の応答の `S` のこの割合以上なら当たり | 起床の hook |
| `cache.rewake` | `versions` 空の配列 | 起床の hook を登録してよい Claude Code のバージョンの一覧（文字列の完全一致）。空ならどの子にも登録しない | ラッパー（`limits.texts`） |
| `hook.budget.stop-rewake` | `timeout` 3,300・`deadline` 3,300・`margin` 240・`p95_ms` 500・`inject_chars` 1,000 | 起床の hook の予算。`timeout` は起床の時点の最大 3,000 秒 + 間隔 60 秒 + `margin` 240 秒 | 予算の検査・登録 |
| `hook.transcript-tail` | `count` 262,144 | 会話の記録の末尾を読む大きさ（バイト）。起床の hook も読む | 既存の読み手と起床の hook |

- 起床の hook は `hooks/tables.policy_value` で読み、`ratios` は辞書のまま届く。判定の関数 `keep_cache.ratios` は `(方針, キー) -> 値` の取得の関数を受ける。バージョンの一覧は読まない
- ラッパーは `contracts/limits` の `limits.texts` で文字列の配列を読む。`limits.value` は数でない値に `LimitsError` を上げる
- 起床の文・`WAKE_HEAD`・寿命の秒（`TTL_SECONDS`）・確かめる回数の上限 300（`MAX_CHECKS`）は値段や閾値でなく振る舞いなので、`keep_cache.py` の定数に置く
- `contracts/hooks/runtimes.json` の claude の Stop に `async_rewake`（予算 `stop-rewake`・バックグラウンドの登録だけの `timeout_max` 3,300）がある。プラグインの登録の生成には使わず、`rewake_entry` と予算の検査が読む
- SessionStart の予算の検査（guard `session`）は、起床の hook の行を外して検査する（`budget.registrations_of(..., rewake=False)`）。その予算と `cache.wake` は、使う時点のラッパーの起動（`registration.rewake_unreachable`）と `ndf hooks check-budget` が読む
- 値の読み口と各方針の `read_by` は、`ndf check` の規則 `limit-readers` が突き合わせる（`src/ndf/checks/rules.json` の `limits`）

### 環境変数

| 変数 | 渡す側 | 意味 |
| --- | --- | --- |
| `NDF_RELAY_REWAKE` | ラッパー | 起床の hook の登録を渡した子にだけ `1` で渡す。起床の hook と guard はこれが `1` でなければ起床の状態を読まない（`contracts/domains/env.json` のキー `relay-rewake`） |
| `NDF_RELAY_SESSION`・`NDF_RELAY_LAUNCH`・`NDF_RELAY_DIR` | ラッパー | 子の会話の ID・起動の ID・中継の保存先（#4049 と #3814 の既存の変数） |

### 起床の状態（`<中継の保存先>/<ラッパーの名前>/keep-<会話の session_id>.json`）

会話ごとに 1 つ置く（`contracts/domains/ndf-files.json` の `relay-keep`）。起動ごとにしないのは、アカウントの替えなどで同じ会話を
起動し直しても、回数とやめた理由を引き継ぐためである。書くのは起床の hook と UserPromptSubmit の guard だけで、
子を止めるロックの中で書く。

| 項目 | 意味 |
| --- | --- |
| `generation` | 世代。`at`（時刻）・`by`（書き込み元の ID。起床の hook の乱数の ID・`turn`・`contested`・`arrival`）・`message_id`（起床の hook の Stop が取った応答。ほかは null）・`sleeps`（その世代を取った hook が眠ると決めたか） |
| `wait_id` | 今の待ちの ID（待ちが始まった応答の時刻）。待ちを終えるか取り消すと消す |
| `k`・`n_max`・`spent` | 今の待ちで起床させた回数・その待ちの回数の上限・起床の費用の見積りの累計（起床のたびに判定の時の `S` で `r × S + d` を足す） |
| `wait_stop` | 今の待ちで起床をやめた理由（`limit`・`unmatched`）。待ちを終えると消す |
| `opened` | 待ちの間に guard が受けた通常の入力の時刻。待ちを終えると消す |
| `ttl` | 最後に分かった寿命（`1h`・`5m`）。分かった寿命でだけ書き換える |
| `last` | 突き合わせ終えた最後の応答の `message_id`・`tokens`・`model`・`ttl`・`at`。`wait_end` の値に使う |
| `pending` | 起床のフラグ。`wake_id`・`tokens`（起床の前の `S`）・`at`（起床の時刻）・`after`（起床の前の最後の応答の ID）・`model`・`ttl` |
| `stopped` | この会話で起床をやめた理由（`form`・`miss`）。在れば二度と起床させない |
| `noted` | 一時的な理由のうち、`stop_waking` を書き終えた理由の並び |

待ちの項目（`wait_id`・`k`・`n_max`・`spent`・`wait_stop`・`opened`）は `signals.WAIT_KEYS` で、待ちを終えるか取り消すときにまとめて消す。

### 今の起動の記録（`launch.json`）

既存の `launch` に、`rewake`（その起動に起床の hook の登録を渡したか）と `claude_version`（その起動の claude のバージョン。
読まなかった起動は null）を足した。ラッパーの `log.jsonl` の `account` の行も、同じ `rewake`・`claude_version` と
`rewake_ready`（利用者の `--settings` が無く、起床の時点が届く起動か）を持つ。

ラッパーが `log.jsonl` に足す行:

| `kind` | いつ | 項目 |
| --- | --- | --- |
| `rewake_off` | `cache.wake` の起床の時点が `timeout` の内に届かない起動 | `reason`（越えた秒などの文） |
| `version_probe` | 別の実行ファイルを指す、キーでない従量の接続の候補でバージョンを確かめる時点（一覧が空でも、打つはずだった候補を書く） | `pid`・`section`・`account`・`claude_path`・`mtime_ns`・`size`・`cached`（バージョンの記録に在ったか）・`ran`（`--version` を打ったか） |

### 起床の記録（`<中継の保存先>/<ラッパーの名前>/cache-log.jsonl`）

1 行が 1 つの出来事で、追記だけで持つ（`ndf-files.json` の `relay-cache-log`）。どの行も `kind`・`at`・`launch`・`session_id`・
`claude_version`（今の起動の記録の値）・`wait_id` を持つ。

| `kind` | いつ | 足す項目 |
| --- | --- | --- |
| `wait_start` | 待つ子の Stop で待ちが始まった | `tokens`・`ttl`・`model`・`n_max` |
| `wake` | 起床させた（終了コード 2） | `wake_id`・`tokens`・`age`（最後の応答から起床までの秒）・`k`・`n_max`・`spent`（この起床を足した後の累計） |
| `wake_result` | 起床のフラグを突き合わせた Stop | `wake_id`・`outcome`（`woke`・`preempted`・`truncated`・`unknown`）・`hit`（`woke` のほかは null）・`tokens`（起床の前の `S`）・`first_read`・`calls`・`cache_read`・`cache_write_1h`・`cache_write_5m`・`output`（起床に属する全呼び出しの合計）・`priced`・`model`・`ttl`（フラグの値）・`w_1h`・`w_5m`・`r`・`out`（費用に使った倍率） |
| `wait_end` | 通常の入力で始まった応答で待ちを終えた | `opened_at`（待ちを終えた入力の時刻）・`age`（`last` の時刻から `opened_at` まで）・`k`・`hit`・`cache_read`（待ちを終えた最初の呼び出しの読み出し）・`tokens`・`model`・`ttl`・`w`・`r`（待ちが終わる直前の応答のもの） |
| `stop_waking` | 起床をやめた・一時的に起床させなかった | `reason`・`scope`（`session`・`wait`・`once`）・`k`・`n_max` |
| `wait_void` | 取り合いか到着による失効で、起床前の待ちを取り消した | `reason`（`contested`・`arrival`） |
| `contest` | 同じ応答を別の書き込み元が既に取っていた Stop（取り合った Stop ごとに 1 行） | `message_id`・`works`（後から来た hook の作業の件数） |
| `arrival` | 今回の応答がまだ記録に無い Stop で、作業が 1 件以上か、生きた予約を失効させた | `message_id`・`works`・`revoked`（予約を失効させたか）・`voided`（待ちを取り消したか） |
| `work_stop` | 作業が 1 件以上の Stop の hook が世代を取った | `message_id` |

`wake_result` の規則:

- `priced` は、起床に属する全呼び出しで費用に使う量を確かめられたときだけ真である。寿命別の書き込みが欠ける・整数でない・負、書き込みの合計（`cache_creation_input_tokens`）が欠ける・整数でない・負・寿命別の和と違う、出力の量が欠ける・整数でない・負、読み出しの量が整数でない・負のどれかがあれば偽で、寿命別の書き込みの合計を書かない。読み出しの量の項目が無いときは 0 とする
- 最初の呼び出しの寿命別の書き込みが両方 0 でも行を書き、当たりは `first_read` だけで決める
- `preempted` は呼び出し 0・費用 0・`priced` 真で書く
- 書き込みの費用は `cache_write_1h × w_1h + cache_write_5m × w_5m` である。寿命 1 時間の待ちで 5 分へ 1,000 だけ書けば 1,250、1 回目が 1 時間へ 300・2 回目が 5 分へ 1,000 なら 1,850 になる

## `ndf relay cache report`

全ラッパーの起床の記録（`<中継の保存先>/*/cache-log.jsonl`）を合わせて集計する。引数と結果のスキーマは
`contracts/schemas/io/relay.args.schema.json`・`relay.result.schema.json` にある。

| 引数 | 意味 |
| --- | --- |
| `--since <日時>`（任意） | ISO 8601 の日時。`wait_start` がそれ以後の待ちと、`at` がそれ以後の Stop の行（`work_stop`・`contest`・`arrival`）だけを数える。途中から始まる待ちは `partial_waits` に数え、どの集計にも入れない |

| 終了コード | 意味 |
| --- | --- |
| 0 | 集計した（`summary` に待ち・起床・避けた書き直しの推定・費用の分からない待ちの数） |
| 1 | 起床の記録を読めない（停止理由 `unknown`） |
| 2 | `--since` が ISO 8601 でない（停止理由 `invalid`） |

結果の `metrics`:

| キー | 意味 |
| --- | --- |
| `waits` | 集計した待ちの数（会話と `wait_id` の組。取り消した待ちと途中の待ちを除く） |
| `wakes` | 起床させた回数。`wake` と `wake_result` に出る `wake_id` の重複を除いた数で、片方だけの起床も 1 回に数える |
| `limit_stopped_waits`・`unmatched_stopped_waits` | 上限で止めた待ち（`stop_waking` の `scope` `wait`・`reason` `limit`）・起床を突き合わせられなかった待ち（`outcome` `unknown`）の数 |
| `hit_rate_by_age` | `age` の幅（`30m`・`45m`・`50m`・`55m`・`60m`・`60m+`）ごとの `wakes`・`hits`・`rate`。対象は `wake` と結べた `outcome` `woke` で `hit` が null でない起床 |
| `d_median` | 書き込みの費用 + `output × out` の中央値。対象は結べた `woke` で `priced` 真の起床。無ければ null |
| `wake_cost_median` | 起床 1 回の実費（`cache_read × r` + 書き込みの費用 + `output × out`）の中央値 |
| `unmatched_wakes`・`orphan_results`・`preempted_wakes` | `wake_result` の無い `wake`・`wake` の無い `wake_result`・`outcome` `preempted` の数 |
| `wait_seconds` | 待ちの長さ（`wait_id` から `wait_end` の `opened_at` まで）の `p50`・`p75`・`p90` |
| `estimated_avoided_rewrites`・`estimated_avoided_tokens`・`estimated_avoided_cost` | 避けた書き直しの推定（下の規則） |
| `voided_waits`・`partial_waits`・`unpriced_waits` | 取り消した待ち・途中の待ち・費用の分からない起床（`wake_result` の無い `wake` か `priced` が偽の `wake_result`）を持つ待ちの数 |
| `by_version` | `claude_version` ごとの `contests`・`arrivals`・`work_stops`・`contested_work_stops`・`arrival_work_stops`・`lost_work_stops`・`lost_ratio` |
| `skipped`・`logs` | 読めずに飛ばした行の数・読んだ記録の数 |

避けた書き直しの推定は、待ち 1 つで最大 1 回と数え、次の 4 つがそろう待ちだけを数える。費用の分からない待ちは入れない。

- 待ちの長さが待ちの始まりの寿命を越えた
- 当たった起床（結べた `woke` で `hit` 真）が 1 回以上ある
- 最後に当たった起床から `opened_at` までが、`wait_end` の `ttl` 以内である
- `wait_end` の `hit` が真である

token は `wait_end` の `tokens`、費用の差は `(w − r) × tokens` からその待ちの全ての `wake_result` の実費を引いた値である。
起床させなかった場合は観測できないので、この値は反実仮想の推定で、確かめた額として読まない。前提は「起床が無ければ、
待ちの始まりの寿命が切れた時点でキャッシュも切れた」である。起床の無いまま寿命を越えて自然に当たった待ちは数えない。

`by_version` の数え方: 分母の `work_stops` は、`work_stop` の行と、作業が 1 件以上の `contest`・`arrival` の行である。
分子の `lost_work_stops` は、そのうちの `contest`・`arrival` の行と、同じ応答（`session_id` と `message_id` の組）に `contest` の行か
`revoked` 真の `arrival` の行がある `work_stop` の行である。費用は各行に書いた倍率で出し、今の上限の設定で計算し直さない。

## 既定値の根拠（集計の値）

2026-10-24〜11-22 の会話の記録（メインの会話とサブエージェント。直前の文脈が 20,000 token 以上の応答の組）と、
2026-11-22 の `asyncRewake` の実測（Claude Code 2.1.330・Haiku 5.5）から決めた。

| 値 | 根拠 |
| --- | --- |
| 余白 600 秒 | 応答の間が 50〜55 分の組は 39 組すべてが当たり、55〜60 分は 120 組のうち 106 組だった |
| `d` 2,000 | 実測の起床は書き込み 319・出力 139 token（Haiku 5.5 で 1,333 相当）。Opus 5.5 の考える出力の増え分の見積り 665 を足して切りのよい値にした。Opus 5.5 では測っていない |
| 当たりの境 0.9 | 実測の起床は直前の文脈の全体（54,969 token）を読み出し、新しく書いたのは文脈の 0.6% だった。起床の文と 1 行の返事を許し、前置きだけを読んだ外れと分ける |
| 寿命の扱い | メインの会話の書き込みは 1 時間へ 477,384,213 token・5 分へ 11,982,796 token、サブエージェントは 1 時間へ 7,964,964 token・5 分へ 207,235,478 token。寿命別の書き込みが両方 0 の応答は、メインの会話の 140,647 応答のうち 84 だった |
| 1 時間を越える `timeout` | `timeout` 1,200 の hook で 660 秒眠らせて起床させられた |

3,000 秒以上の待ち 435 回に起床の規則を当てた見積り（Opus 5.5・`d` 2,000）では、起床の費用は何もしない費用の 0.115
（13,351,993 ÷ 116,007,698）で、上限で止まる待ちは 4 回だった。この 435 回は起床させない待ち（質問の表示中・何も残っていない）を
含み、同じ期間を待ちの種類で数え直すと、起床させる待ち（作業が残る 291 回）だけの比は 0.083 で、監視だけの待ちは 0 回だった。
