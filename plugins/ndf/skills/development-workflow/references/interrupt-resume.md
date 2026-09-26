# 中断と再開

**利用上限（429）で落ちた相手を、上の層が記録から見分けて再開する。**
対象は**上の層が起動した相手**である。無人の運転でなくても、人が指示して
サブエージェントを起動した形にそのまま当たる。

`$SCRIPTS` の決め方は [scripts-lookup.md](scripts-lookup.md) にある。起動の指示・「フェーズの一覧」・「conductor の報告」・
「報告が無いまま終わったとき」は [agent-layers.md](agent-layers.md) にある。

## レートリミット中断は記録で見分ける

**記録で見分け、通知では見分けない。** 失敗の通知（`<status>failed</status>`）の本文は人が読む
文言で、書式を約束していない。記録の合成応答は `apiErrorStatus` と
`quotaLimits.resetsAt` を値として持つ。**通知は目を覚ます契機にだけ使う。**

| 目を覚ます契機 | 何が届くか |
| --- | --- |
| 失敗の通知 | 配下の `<status>failed</status>` |
| 自動継続 | リセット時刻に Claude Code が積む入力 |
| 人の入力 | 「続けて」の 1 通 |
| 背景の待ちの終わり | `wait-reset` の終わりの通知 |

**どの契機でも、行うのは同じ「中断の点検」1 回である。**

```bash
python3 "$SCRIPTS/lib/transcript_agents.py" interrupted \
  --session "$CLAUDE_CODE_SESSION_ID" --depth 1 --format json
```

| 種別 | 出力の `ending` | 扱い |
| --- | --- | --- |
| 利用上限（429） | `rate_limit` | **レートリミット中断。** 解除を待って再開する |
| `server_error`（500 / 529） | `api_error` | 上限に当たっていない。待たずに起こし直してよい |
| `authentication_failed` | `api_error` | 上限に当たっていない。人へ報告する |

**リセット時刻は記録から取る**（`quotaLimits.resetsAt`）。**固定の間隔で待たない。**
解除を過ぎたかどうかは出力の `resets_passed` が持つ。

**`StopFailure` フックは使わない。** Claude Code は API の失敗で応答が終わったときに
`StopFailure` を発火するが、出力も終了コードも無視されるため conductor を起こせない。
残したい値は会話の記録に既にある。

## 落ちた層ごとの割り当て

| 落ちた層 | 検知する側 | 再開する側 | 起こす手段 |
| --- | --- | --- | --- |
| worker | supervisor（失敗の通知）。supervisor も落ちていれば、起こされた supervisor が見る | supervisor（conductor が直接起動した worker なら conductor） | supervisor が動いていれば `SendMessage`。動いていなければ conductor → supervisor → worker の順 |
| supervisor | conductor（失敗の通知） | conductor | `SendMessage`。続けられなければ `最後に記録した工程` の頭から新しい supervisor（`subagent_type` は起動の指示の `subagent_type` の欄で選ぶ） |
| プラン | conductor（`wait` の終わり・キューの done・進捗ログ） | conductor | 進捗ログ（`<プラン>-state/progress.jsonl`）の最後のステップの id から `supervise.py run <プラン> --from <ステップの id>`。落ちたプランごとに打ち、終わったプランは流し直さない |
| conductor | 人か Claude Code（自動継続） | conductor 自身 | 自動継続・人の 1 通・背景の待ちの終わり（「解除を待つ手段」） |

**3 層は同じ割り当てを共有するため、同時に落ちるのが普通である。** そのときは conductor が
起きた後、上から順に 1 層ずつ再開する。

**再開するのは直下だけである。** conductor は supervisor と、自分が直接起動した worker を
再開する。supervisor は自分が起動した worker を再開する。
**conductor が supervisor の下の worker を直接再開しない。**
直接再開すると、起こされた supervisor と worker が同じ作業と同じ書き込みを
重ねてしまうためである。

## 解除を待つ手段

| 順 | 何が conductor を起こすか | いつ使うか |
| ---: | --- | --- |
| 1 | Claude Code の自動継続（リセット時刻に入力が積まれる） | conductor も上限に当たったとき。conductor は何もできないため、これに頼るしかない |
| 2 | 背景で起動した待ち（`wait-reset`）の終わりの通知 | conductor は動けるが、配下だけが中断したとき |
| 3 | 人が送る 1 通（例:「続けて」） | 1 と 2 のどちらも起きなかったとき |

**3 の 1 通は承認ゲートの数に数えない。**

**`/goal` の見回りには頼らない。** 見回りの間隔は伸び、やがて止まる。上限が解ける前に
尽きると、待っているつもりのまま進まない。

## conductor の中断の点検

| 順 | 行うこと | 使うもの |
| ---: | --- | --- |
| 1 | 中断した supervisor と、自分が直接起動した worker を一覧する | `interrupted --session "$CLAUDE_CODE_SESSION_ID" --depth 1 --format json` |
| 2 | `resets_passed` が真の記録ごとに `SendMessage` で続けさせる。supervisor へは「利用上限で中断していた。解除されたので続ける。書く前に既に書いたものを確かめる。自分の worker の中断も点検する」、直接起動した worker へは同じ作業を続ける指示を送る | 出力の `agent_id` |
| 3 | 2 が失敗した相手の後段は層で分かれる。**supervisor** は `最後に記録した工程` の頭から、同じフェーズの名前で起動し直す（起動の指示へ旧 supervisor の `agent_id` を渡す。`subagent_type` は起動の指示の `subagent_type` の欄で、`最後に記録した工程` から選ぶ）。**直接起動した worker** は、同じ作業の起動の指示をもう一度組んで起動する。**失敗とは、`SendMessage` の結果が `"success": true` を持たないことである** | issue の `## 進行` |
| 4 | まだリセット時刻を過ぎていない相手があれば、待ちを背景で起動して応答を終える | `wait-reset --session "$CLAUDE_CODE_SESSION_ID" --depth 1`（背景で実行する） |

**手順 4 は `--max-sleep` を付けない。** 背景の待ちを数時間続けられないと分かったときだけ
`--max-sleep 540` を付け、終了コード 3 で起きるたびに手順 1 と手順 4 だけを行う。
**解除の前に `SendMessage` を送らない。**

**プランは `interrupted` の一覧に出ない。** 流していたプランは、ミッション状態ファイルのステージと
キューの done で点検し、終わっていないプランを「落ちた層ごとの割り当て」のプランの行で再開する。

**応答を終える前に「フェーズの一覧」を 1 回出す**（「conductor の報告」の 3 つ目の時点）。

## supervisor の worker の点検

**起こされた supervisor は、同じ点検を自分の worker に対して行う。**

| 順 | 行うこと | 使うもの |
| ---: | --- | --- |
| 1 | 中断した worker を一覧する | 起動し直された初回は `interrupted … --layer worker --parent <旧 supervisor の agent_id>`、自分で起動した後は `… --agent <id> …` |
| 2 | `resets_passed` が真の worker を `SendMessage` で続けさせる | 出力の `agent_id` |
| 3 | 続けられない worker は、同じ作業をもう一度 `Agent` で起動する。作業は 1 つに絞ってあるため、やり直しの費用はフェーズより小さい | 起動の指示を作り直す |

**`<id>` は worker を起動したときの結果に出た `agentId` で、supervisor が自分で持ち回る。**
`CLAUDE_AGENT_ID` のような環境変数は無いため、自分の `agent_id` からは絞れない。
`CLAUDE_CODE_SESSION_ID` は、conductor でもサブエージェントでも conductor のセッションの
ID を持つ。

## 済んだ書き込みを重ねない

**再開した相手は、外部へ書く前に既に書いたものを確かめる。** 中断は書き込みの後に
起きていることがある。

| 確かめるもの | 見方 |
| --- | --- |
| Pull Request | `gh pr list --head <ブランチ>` |
| コメント | `gh pr view <番号> --json comments` |
| 進捗記録 | issue の `## 進行` |

**この 1 文を再開の指示に必ず入れる。** 起動し直す worker の指示にも入れる。
