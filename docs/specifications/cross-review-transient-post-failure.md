# cross-review: GitHub への投稿の一時的な失敗を投稿キューに積み、担当を起動し直さずに流し直す

## 概要

**GitHub への送りが一時的な失敗（HTTP 500・502・503・504、`(HTTP nnn)` の無い本文なしの応答、ネットワークの失敗）で
終わったときは、上限（rate limit）と同じく投稿キューに積んで先へ進む。** `read-result` は担当の結果を記録へ取り込み
（`queued` が真）、終了コード 0 で終わる。判定は投稿が残っていれば終了コード 8 を返し、`drive.py` はステージ
`posts` で担当を起動し直さずに投稿キューを流し直す。戻らなければ「投稿待ち」として止まり、打ち直すと同じステージから
続ける。

この文書は、その形に**なぜしたか**（背景・決定と理由・常に成り立つ条件・境界の決まり・既知の限界・テスト観点）を
残す。**振る舞いと出力の正は Skill とスクリプトにある。**

| 何を | どこを読む |
| --- | --- |
| 応答ごとに積むか止めるかの表・流すきっかけ・`state.py flush` の出力の行 | [`cross-review` の投稿と記録](../../plugins/ndf/skills/cross-review/docs/07-posts-and-records.md) の「GitHub が使えないあいだの投稿（投稿キュー）」 |
| 判定の出口（終了コード 8）・ステージ `posts` の待ち方・投稿待ちの止まりの形 | [`cross-review` の状態と判定](../../plugins/ndf/skills/cross-review/docs/01-state-and-review.md) の「判定の出口」 |
| 一時的な失敗の語・項目のキー（`last_transient`）・待ちの定数 | [`post_queue.py`](../../plugins/ndf/scripts/lib/post_queue.py) の冒頭の説明と `is_transient_failure` / `reflush` |

決定の番号は #1843 の設計の番号である。コードのコメントの「決定 3」「決定 4・5」はこの番号を指す。

## 用語

| 用語 | 意味 |
| --- | --- |
| 一時的な失敗 | 送り直せば届く見込みのある投稿の失敗。上限とは別に見分ける |
| 待てば流れる失敗 | 上限か一時的な失敗。`FlushResult.waitable` が真のもの |
| 投稿待ち | 担当の結果は記録へ取り込んであり、投稿だけが投稿キューに残っている状態 |
| 結果なし | 起動した担当が使える結果を残さなかった状態（`NO_RESULT`）。投稿待ちとは別である |

## 背景

PR 1842 の round 1（2026-10-07 16:57 UTC）で、codex と kiro はレビューを書き終え、結果ファイルと payload が手元に
残っていた。GitHub の REST API への POST が「HTTP 500・本文なし」を返し、`gh api` は `unexpected end of JSON input`
（終了コード 1）で失敗した。

当時（ndf 10.17.67）の投稿キューは上限だけを「待てば流れる」側に数えていた。この失敗は恒久の失敗と同じ経路へ入り、
`result_posts.post_review` が `failed` を返し、`read-result` が終了コード 1 で止まった。`drive.py` は終了コードが 0 で
ない担当を欠けたものとして扱い、判定は 2 者を結果なし（理由 `missing`）と数えて、起動し直し → 振り替え → 中断
（`final=error`）へ進んだ。起動し直した担当の投稿（0003・0004）も投稿キューの後ろへ積まれ、同じラウンド・同じ席の
レビューが 2 件並ぶ形になった。

**積んで後で流す仕組みは既にあった**（上限のため、#291）。足すのは見分ける対象だけで済んだ。

## 決定と理由

| # | 決定 | 理由 | 採らなかった案 |
| ---: | --- | --- | --- |
| 1 | 一時的な失敗の判定を `post_queue.is_transient_failure` の 1 か所に置き、`gh` の出力の形（標準エラーの `(HTTP nnn)`・標準出力の本文・標準エラーの語）だけで決める。上限の語を持つものは先に除き、残り回数は引かない | `result_posts`・`state.py flush`・`post_queue.py post` が同じ関数を通る。5xx とネットワークの失敗は上限の状態（403・429）を持たないため、残り回数の照会は要らない | cross-review の `review_lib` に判定を足す（`/ndf:fix` の単独の送りと巻き直しのコメントも同じ失敗に当たり、同じ役割の関数が Skill ごとに分かれる） |
| 2 | `FlushResult` に `transient` を足し、呼び出し側は `waitable`（`rate_limited or transient`）だけを読む。`rate_limited` の意味は広げない | `rate_limited` を広げると、`state.py flush` の「まだ上限です」が 5xx のときにも出て、止まった理由を読み違える。欄を分ければ表示と待ち方（決定 5）を理由ごとに分けられる | `rate_limited` を「待てば流れる」の意味へ改名する（上限の語は用語集と cross-review の文書で上限だけを指しており、語の意味が 2 つになる） |
| 3 | 前の送りが一時的な失敗で終わった項目（`last_transient` が真）は、既投稿の照会ができない（`posted_match` が `None`）とき送らずに残す | HTTP 500 は GitHub 側でレビューが作られた後に返ることがある。照会できないまま送ると、照会と送りが続けて戻った瞬間に同じラウンド・同じ席のレビューが 2 件並ぶ | 照会できないときは常に送らない（上限で積んだ項目は照会も上限で落ちる。上限の応答は作成を伴わず二重にならないため、送る側へ倒す既存の理由「項目が永久に残る」が今も効く） |
| 4 | 流し直しの待ちは合計 300 秒・30 秒おき（`TRANSIENT_MAX_WAIT` / `TRANSIENT_INTERVAL`、`post_queue` の定数） | 上限の再実行の既定（900 秒・30 秒おき）を超えない。投稿待ちで止まっても結果は記録に入っており再開で同じラウンドへ戻れるため、長く待つ利点は小さく、待つ間は検査のプランが進まない | 900 秒（上限は時間の窓で解けるため長く待つ理由があるが、一時的な失敗は戻らなければ人の調査が要り、伸ばしても止まるまでが延びるだけ） |
| 5 | 待ちは `drive.py` のステージ `posts`（判定が 8 を返した後）の 1 か所だけに置き、`flush` は待たない。待ちとやり直しは `post_queue.reflush`（`lib/waits.retry_call` の包み）が持つ。上限で残っているときは待たない | `flush` の中で待つと、`read-result` が担当ごとに呼ぶため 2 席なら待ちが 2 回重なり、`_auto_flush` を呼ぶ `start-round`・`init` まで待つ。ステージにすれば待ちは 1 ラウンドに 1 回で、止まりと再開も耐久ワークフローのステージとして扱える | 8 を受けたら 1 度流して判定を打ち直すだけにする（なお 8 なら同じステージ `round` を続きでやり直す。続きが `start-round` から始まり、前のラウンドが `queued` のまま新しいラウンドを開いて担当を起動する） |
| 6 | 投稿待ちの止まりは `final` を書かず、止まりの結果 JSON の `metrics.exit` を 8 にする。プロセスの終了コードは止まり共通の 1（`EXIT_STOPPED`）のまま | `final=error` は終わったループの印で、結果なしの中断（判定の 1）と同じ値になる。投稿待ちは結果がそろっており打ち直すだけで進む。`metrics.exit` に止まった元の終了コードを入れるのは `drive_pause.stopped` の規約。呼ぶ側（検査のプラン）の止まりの扱いを変えない | 新しい止まりの種類（`pause`）を足す（`pause` は人か AI が結果ファイルを書いて進める地点で、投稿待ちには書くものが無い） |
| — | `state.py flush` は `PENDING_LAST_ERROR=`（先頭で止まった項目の最後の失敗。`shlex.quote` で 1 語にする）も出す | 投稿待ちの止まりの `summary` に最後の失敗を載せるため（`drive.py` が読む） | — |

## 仕様

### 常に成り立つ条件

| 条件 | 破れたときの扱い |
| --- | --- |
| 一時的な失敗で送れなかった項目は消えずに残り、上限・恒久の失敗・位置の拒否のどれとも見分けられる | 流した結果に `transient` を立てて返す。印の無い失敗は呼び出し側が止める |
| `transient` と `rate_limited` は同時に立たない。一時的な失敗は恒久の失敗（`dropped/` へ控える）にも数えない | 上限の判定を先に行い、上限に当たれば一時的な失敗を見ない |
| 前の送りが一時的な失敗で終わった項目は、既投稿の照会ができないときは送らない | 送らずに残し、`transient` を立てて返す |
| 担当の結果ファイルが使え、投稿が待てば流れる失敗で投稿キューに残っているときに限り、投稿が送れていないことを理由にその担当を結果なしに数えない | `read-result` は担当の記録に `queued` を真で書き、終了コード 0 で終わる。恒久の失敗（400・404・410・422 と印の無い失敗）は終了コード 1 |
| 投稿だけが残っているラウンドで、担当を起動し直さない・新しいラウンドを開かない | ステージ `posts` は `start-round` も担当の起動も通らない。止まった後の再開もこのステージから始まる |
| 流し直しの待ちの合計は、上限の再実行の既定（900 秒）を超えない | 定数は `post_queue` の 1 か所にあり、`drive.py` はそれを読む |

### 境界の決まり

**一時的な失敗の形は次の 3 つに限る。** 語の一覧の正は `post_queue.py` の `TRANSIENT_STATUSES` / `_EMPTY_BODY_WORDS` /
`_NETWORK_WORDS` である。

| 形 | 見る場所 |
| --- | --- |
| `(HTTP nnn)` が 500・502・503・504 | 標準エラー |
| `(HTTP nnn)` が無く、標準出力が空で、標準エラーが応答を読めなかったことを示す | 標準出力と標準エラー |
| `(HTTP nnn)` が無く、標準エラーにネットワークの失敗の語がある | 標準エラー |

**この変更で振る舞いが変わる呼び出し元と、変わらない呼び出し元。** 判定は共通の層にあるため、投稿キューを通る
送りのすべてに効く。

| 呼び出し元 | 呼ぶもの | 一時的な失敗のとき |
| --- | --- | --- |
| cross-review の `read-result` | `result_posts.post_review` | 結果を取り込み、終了コード 0（`QUEUED=1`） |
| cross-review の `drive.py` の修正の取り込み・単独の `/ndf:fix` | `result_posts.py fix`（`post_fix`） | 終了コード 0、`QUEUED=` に残りが出る |
| `rotate-pr.sh` の旧 PR へのコメント | `post_queue.py post` | 積んで終了コード 0 |
| cross-review の `judge`・`start-round`・`init` | `posts._auto_flush`（`Queue.flush`） | 変わらない（流せなくても止めない経路のまま） |
| backlog-refinement の `upkeep_gh.py` | `post_queue.run`・`is_rate_limited` | 変わらない（上限の判定は変えていない） |
| cross-refactoring | 投稿キューを呼ばない（計画のコメントは `gh api` を直接呼ぶ） | 変わらない |

**変えていないもの。** 上限・恒久の失敗・位置の拒否の判定、巻き直し（PR の作成・close・reopen）の `retry` の経路、
`read-result` が送れたときの出力（`POSTED review_url=` / `INLINE= BODY= QUEUED=` / `FINDINGS=`）。投稿キューの項目には
キー `last_transient` が 1 つ加わった（決定 3 の材料）。

## 運用

### 既知の限界

| 項目 | 内容 |
| --- | --- |
| HTTP 5xx の実際の出力 | 500・本文なしの形は PR 1842 の実例だけで確かめた。502・503・504 で GitHub が本文を返したとき `gh` が `(HTTP nnn)` を付けるかは、障害を起こせないため実測していない |
| ネットワークの失敗の語 | 実測したのは名前解決の失敗（`error connecting to`）と接続の拒否（`dial tcp … connection refused`）だけである（gh 2.101.0）。`i/o timeout`・`connection reset`・`TLS handshake timeout`・`context deadline exceeded` は Go の標準の言い回しから採った |
| 待ちの上限 300 秒 | 実測の根拠は無く、「GitHub の 5xx の多くは数分で戻る」という前提に拠る |
| GraphQL の送り（`thread-resolve`）の 5xx | `gh api graphql` が 5xx で `(HTTP nnn)` を付けるかは実測していない。付かなければ本文なしかネットワークの語の形で拾う |
| 判定が 1 か所にあることの確かめ | テストでは縛らない。5xx の番号の並びが `post_queue.py` の外に無いことはレビューで見る |

## テスト観点

| 観点 | 確かめ方 |
| --- | --- |
| HTTP 500・502・503・504、`(HTTP nnn)` 無しで本文が空の `unexpected end of JSON input`、ネットワークの失敗で `flush` を流すと、項目が残り `failed` が立ち `transient` と `waitable` が真になること | `plugins/ndf/scripts/tests/test_post_queue.py` |
| 上限（403・429・上限の語）で `transient` が偽・`rate_limited` が真、400・404・410・422 で `transient` が偽であること | 同上 |
| 前回が一時的な失敗の項目で、照会が失敗するときは送らずに残し、照会が同じラウンド・同じ席の既存のレビューを見つけると送らずに消えること | 同上 |
| 待ちの定数が上限の再実行の既定を超えないこと | 同上 |
| `post()` と CLI の `post` が一時的な失敗で積んで `QUEUED` を返し 0 で終わり、CLI の `flush` が `PENDING_TRANSIENT=` を出すこと | 同上 |
| `post_review` と `post_fix`（返信・決着）が一時的な失敗で `failed` を偽にして残りを `queued` に入れ、400 の拒否は失敗のままであること | `plugins/ndf/scripts/tests/test_result_posts.py` |
| `read-result` が一時的な失敗で結果を取り込んで 0 で終わり、判定が担当を結果なしに数えず 8（`verdict=queued`）を返すこと。`state.py flush` が一時的な失敗と上限を書き分けること | `plugins/ndf/skills/cross-review/tests/test_state_queue_judge.py` |
| ステージ `posts` で流し直して送れたら担当を起動し直さずに判定へ進むこと。送れないままなら待ちの合計 300 秒で投稿待ちとして止まり（`summary` が `投稿待ち:` で始まる・`metrics.exit` が 8・`final` が空）、打ち直すとラウンドを増やさずに続けること。上限で残っているときは待たないこと | `plugins/ndf/skills/cross-review/tests/test_review_drive_resume.py` |
| 送れたときの `read-result` の出力と記録の形が変わらないこと | `plugins/ndf/skills/cross-review/tests/test_read_result_posts.py` |

## 関連リンク

- [`cross-review` の投稿と記録](../../plugins/ndf/skills/cross-review/docs/07-posts-and-records.md)（積むかどうかの表と流すきっかけの正）
- [`cross-review` の状態と判定](../../plugins/ndf/skills/cross-review/docs/01-state-and-review.md)（判定の出口とステージ `posts` の正）
- [`post_queue.py`](../../plugins/ndf/scripts/lib/post_queue.py)（投稿キューの形と一時的な失敗の語）
- [cross-review-writes-to-conductor.md](cross-review-writes-to-conductor.md)（担当は投稿せず、オーケストレーターが投稿キューを通して送る形）
- [cross-review-launch-outcome.md](cross-review-launch-outcome.md)（結果なしの理由と起動し直しの可否）
- [cross-assignee-reassignment-on-no-result.md](cross-assignee-reassignment-on-no-result.md)（結果なしの担当の振り替え）
- #1843（この形を決めた課題）、PR #1860（実装）、PR #1842（見つけた検査）、#291（上限のときに積む投稿キュー）
