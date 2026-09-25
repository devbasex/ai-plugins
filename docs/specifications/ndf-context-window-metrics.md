# 会話の記録から context window を 3 層で測る

conductor / supervisor / worker の会話の記録を層の単位で読み、記録 1 件ごとの固定費・最大充填・
実作業と、層ごとの合計、フェーズごとの worker の使い方を出せるようにした。同じ読み取りで、利用上限
（429）で中断した記録の一覧と解除の待ちも行う。この文書は、記録を読む部品（`transcript_agents.py`）
の値の取り方と、`skill-stats --agents` の出力の契約を残す。**値の取り方はこの文書が正である。**

| 何を読むか | 正本 |
| --- | --- |
| 記録の値の取り方、コマンドの引数と終了コード、出力の列 | この文書 |
| `skill-stats` の使い方 | `plugins/ndf/skills/skill-stats/SKILL.md` |
| 振り返りの記録へ貼る表 | `plugins/ndf/skills/retrospective/SKILL.md` の記録の雛形「context window の大きさ」 |
| 中断の点検の手順（誰がいつ `interrupted` / `wait-reset` を呼ぶか） | [ndf-agent-layers-unattended-run.md](ndf-agent-layers-unattended-run.md) と `development-workflow/references/agent-layers.md` の「中断と再開」 |
| 粒度の比の基準、劣化の目安の値 | `plugins/ndf/skills/development-workflow/references/context-window.md` |

## 概要

**新しい保存先を作らず、Claude Code が書く会話の記録だけを読む。** 記録（`<セッション>.jsonl` と
`subagents/agent-<id>.jsonl` と `.meta.json`）が層・フェーズ・モデル・トークン・時刻・中断・起動元を
すべて持つ。部品は標準ライブラリだけで、ネットワークを呼ばない。

**層は深さで決め、フェーズと作業の種類は `description` の先頭語から取る。** 出力に出すのは語彙の値
だけで、`: ` の後ろ・パス・本文・プロンプトは出さない。

**粒度の基準は supervisor に当て、worker には別の目印を置く。** 「実作業が固定費を上回るなら単独、
下回るなら束ねる」はフェーズ（分割と結合の単位）に当てる。worker は使い捨ての読解で実作業が固定費を
下回ってよく、代わりに 1 つのフェーズについて supervisor と worker の固定費の合計が supervisor の
実作業を上回ったら `worker を使いすぎ` の目印を付ける。

**層ごとの合計を出し、層を増やした費用を見えるようにする。** 総消費を下げること自体は目的にしない。
目的は 1 つの context window を劣化の域へ入れないことで、総消費はその代償である。

## 用語

| 用語 | 意味 |
| --- | --- |
| 固定費（`fixed`） | context window が作業を始める前に既に埋まっている量。合成でない最初の応答の入力トークン（`input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens`） |
| 最大充填（`peak`） | 応答ごとの同じ合計の最大 |
| 実作業（`work`） | 最大充填 − 固定費 |
| 合成の応答 | API を呼ばずに Claude Code が記録へ書いた応答。`message.model` が `<synthetic>` |
| 上限の中断 | 利用上限（429）で層が途中で終わること。`ending` が `rate_limit` |
| 解除時刻（`resets_at`） | 上限が解ける時刻。記録の `quotaLimits.resetsAt`（UNIX 秒）を ISO 8601 で持つ |
| 起動元（`parent_agent_id`） | その記録を起動した相手。`.meta.json` の `toolUseId` と同じ id の `tool_use`（`name` が `Agent`）を含む記録 |
| 余白（`--margin`） | 解除時刻の後、実際に呼べるまでの猶予。既定 60 秒 |

層・フェーズ・作業の種類の語彙は [ndf-agent-layers-unattended-run.md](ndf-agent-layers-unattended-run.md) の
「用語」と同じである。

## 背景

#550 は工程ごとに context window の大きさを記録し、固定費と実作業を分離して `retrospective` の出力に
集計として出すことを求めた。1 つの応答は複数の行に分かれて記録される（1 本で 212 行、固有の応答
126 件）ため、行で数えると応答数が 7 割近く多く出る。合成の応答はトークンがすべて 0 で、最初の行が
それだと固定費が 0 になる。#657 は、上限で落ちた担当を通知ではなく記録から見分けることを求めた。

## 決定と理由

**記録を読む部品を共通層（`plugins/ndf/scripts/lib/transcript_agents.py`）に 1 つ置く。** `skill-stats`
が集計に使い、`development-workflow` が中断した記録の一覧と解除の待ちに使う。別の Skill の `scripts/`
を呼ぶと、配る Skill を絞る配布先で解決できない（`scripts/check-cross-skill-refs.py`）。起動のたびに
台帳を書く案は、記録が既に必要な値を持つため採らなかった。

**層は深さで決め、`description` へ書かせない。** `spawnDepth` が既に深さを持ち、conductor の記録は
`subagents/` の外にある。書かせると、書き忘れた相手が層の分からない行になる。conductor が直接起動する
worker（読解）は深さ 1 に現れるため、`description` の先頭語が作業の種類の語彙にあることを 1 つの
手がかりにする。

**固定費は最初の合成でない応答で取り、応答は `message.id` で数える。** `SendMessage` で続けた記録は
同じファイルへ追記され、固定費は最初の値のまま、最大充填は続けた後も含めた最大になる。上限の中断から
続けた回数は `interruptions` として別の列に出す。

**判定は記録ごとの比で行い、中央値どうしを比べない。** 固定費はセッションの設定で変わるため、別の
セッションの記録を束ねた中央値どうしを比べると、どの記録の比でもない値で判断することになる。

**解除時刻を持たない上限の中断は `resets_passed` を真にする。** 待つ先が無いまま止まるのを避ける。
記録から取れないだけで、上限そのものは解けているかもしれない。固定の間隔で待たないこととは矛盾しない。

**`wait-reset` は、まだ来ていない解除時刻のうち最も早いもの + 余白まで眠る。** 過ぎたものは眠る理由に
ならず、固定の間隔で待たない。

**識別子は層の側を揃え、量を指す名前は `window` のまま残す。** `--agents` は conductor も supervisor も
出るため `--workers` としない。`--window-limit` は劣化の目安で context window の長さを指す。

## 仕様

### 常に成り立つ条件

- `work` は `peak - fixed` に一致する
- 出力にプロンプト・応答の本文・パス・`description` の `: ` より後ろが現れない。`agent_id` は `list` と
  `interrupted` にだけ出し、`skill-stats` の集計には出さない
- ネットワークを呼ぶ経路を持たない
- `interrupted` は `ending` が `rate_limit` の記録しか返さない。`in_progress`（続けさせた直後）は返らない
- `--agents` を付けない `skill-stats` の出力は変わらない

### 読む場所

| 対象 | パス |
| --- | --- |
| conductor | `~/.claude/projects/*/<セッション>.jsonl`（並べて最初に見つかった 1 件） |
| supervisor / worker | `~/.claude/projects/*/<セッション>/subagents/agent-<ID>.jsonl` と `agent-<ID>.meta.json` |

`~/.claude` は環境変数 `CLAUDE_CONFIG_DIR` があればそちらを使う。読めない行・壊れたファイルは飛ばし、
飛ばした件数を標準エラーへ 1 行（`[transcript-agents] 読めない行を飛ばした: N 件`）出す。JSON の出力にも
`skipped` として出す。終了コードは変えない。

### `AgentRecord` の値

| キー | 型 | 取り方 |
| --- | --- | --- |
| `layer` | 文字列 | 深さと `description` から決める。0 = `conductor`。2 以上 = `worker`。1 は、先頭語が作業の種類の語彙にあれば `worker`、それ以外は `supervisor` |
| `role` | 文字列 | conductor は `-`。supervisor は `.meta.json` の `description` の最初の `: ` より前がフェーズの語彙にあればその値、工程名（`transcript_agents.STEP_PHASES`。`agent-layers.md` のフェーズの表の「通す工程」の列を写したもの）にあればその工程を通すフェーズ。worker は同じ位置が作業の種類の語彙にあればその値。当たらなければ `その他`。`: ` が無ければ文字列全体を先頭語にする |
| `agent_id` | 文字列 / null | ファイル名の `agent-<ID>`。conductor は null |
| `depth` | 整数 | conductor 0。ほかは `.meta.json` の `spawnDepth`（整数でなければ 0） |
| `model` | 文字列 / null | 合成でない応答の `message.model` のうち最も多いもの |
| `fixed` | 整数 / null | 合成でない最初の応答（`usage` を持つもの）の `input_tokens + cache_read_input_tokens + cache_creation_input_tokens` |
| `peak` | 整数 / null | 合成でない応答の同じ合計の最大 |
| `work` | 整数 / null | `peak - fixed` |
| `responses` | 整数 | 合成でない応答の `message.id` の異なる数 |
| `started_at` / `ended_at` | ISO 8601 | 記録の最初と最後の `timestamp` |
| `duration_seconds` | 整数 | `ended_at - started_at` |
| `ending` | 文字列 | 下の表 |
| `interruptions` | 整数 | `apiErrorStatus` が 429 の合成の応答のうち、後ろに合成でない応答が続くものの数。500 / 529 / 認証の失敗は数えない |
| `resets_at` | ISO 8601 / null | `ending` が `rate_limit` のとき、最後の合成の応答の `quotaLimits.resetsAt`（UNIX 秒の数値）を UTC で |
| `rate_limit_type` | 文字列 / null | 同じ応答の `quotaLimits.rateLimitType` |
| `parent_agent_id` | 文字列 / null | 起動元。conductor の記録に見つかったときと、見つからないときは null |
| `session` | 文字列 | 記録が属するセッションの ID（JSON にだけ出す） |

`fixed` / `peak` / `work` / `model` は、合成でない応答が 1 件も無いとき null になる。

**`ending` は上から順に判定し、最初に当たった値にする。** 429 で中断した記録へ `SendMessage` した
直後は、最後の assistant の行が 429 の合成の応答のまま `user` の行が追記されるため `in_progress` に
なり、`interrupted` に再び現れない。

| 順 | `ending` | 条件 |
| ---: | --- | --- |
| 1 | `in_progress` | 最後の assistant の行より後に `user` の行がある |
| 2 | `rate_limit` | 最後の assistant の行が合成の応答で、`apiErrorStatus` が 429 |
| 3 | `api_error` | 最後の assistant の行が合成の応答で、それ以外（500 / 529 / 認証の失敗など） |
| 4 | `completed` | 上のどれでもない（assistant の行が無い記録を含む） |

### コマンド

呼び方は `python3 "$SCRIPTS/lib/transcript_agents.py" <コマンド> ...` である。

| コマンド | 引数 | 出力 | 終了コード |
| --- | --- | --- | --- |
| `list` | `--session <ID>`（必須、繰り返し可）/ `--layer <層>` / `--format md\|json` | そのセッションの 3 層すべての記録。列は 層 / フェーズ / 深さ / モデル / 固定費 / 最大充填 / 実作業 / 応答数 / 所要（分） / 終わり方 / 中断 と `agent_id` | 0。記録が 1 件も無ければ 0 で空の一覧と理由 1 行 |
| `interrupted` | `--session` / `--layer` / `--depth <数>` / `--agent <agent_id>`（繰り返し可）/ `--parent <agent_id>` / `--now <ISO 8601>`（テスト用）/ `--format` | `ending` が `rate_limit` の記録だけ。`resets_passed`（真偽）を足す。`--depth 1` は conductor の直下（supervisor と直接起動した worker） | 0 |
| `wait-reset` | `--session` / `--layer` / `--depth` / `--margin <秒>`（既定 60）/ `--max-sleep <秒>`（既定なし） | 眠った秒数と、起きた時点で読み直した中断した記録の数を 1 行 | 0 = 解除時刻を過ぎた。3 = `--max-sleep` で区切った（まだ解除前。`--max-sleep 0` でも未来の解除時刻があれば 3）。2 = 引数の誤り（負の秒数を含む） |

`resets_passed` は `resets_at <= now` で真、`resets_at` が無ければ真である。`wait-reset` は `resets_at`
の無い記録を待たない（`resets_passed` が真であることと整合する）。眠る秒数は切り上げる。

### `skill-stats --agents`

| 引数 | 意味 | 既定 |
| --- | --- | --- |
| `--agents` | 3 層の測定を出す。付けないと従来の Skill の統計だけを出す | 付けない |
| `--session <ID>` | セッションに絞る。繰り返し可。`--agents` を付けないと、Skill の統計をそのセッションの conductor の記録だけで数える | 絞らない（`--agents` では全セッションを読み、`--days` / `--from` / `--to` は効かない） |
| `--layer <層>` | `--agents` の出力を 1 つの層に絞る | 全層 |
| `--window-limit <トークン>` | 割る候補の目印を付ける最大充填の目安 | 200000（`context-window.md` の「遅くとも切る」値。一致をテストが固定する） |

出力（`md`）は 4 つの表である。

| 表 | 列 | 出す条件 |
| --- | --- | --- |
| 記録ごとの context window | `list` の列（`agent_id` を除く） | `--session` のときだけ |
| 層・フェーズ・モデルごとの束ね | 層 / フェーズ / モデル / 件数 / 固定費の中央値 / 実作業の中央値 / 実作業 < 固定費 / 最大充填の最大 / 目印 | 常に。応答数が 3 に満たない記録と `fixed` が無い記録はこの表からだけ外し、外した件数を表の下に 1 行出す |
| 層ごとの合計 | 層 / 件数 / 固定費の合計 / 実作業の合計 / 総消費 | 常に。最後の行は 3 層を合算した `合計`。件数は外す前の全件。記録が無い層の行は出さない |
| フェーズごとの worker の使い方 | フェーズ / supervisor / supervisor の実作業 / worker の件数 / supervisor と worker の固定費の合計 / 目印 | `--session` のときだけ |

`実作業 < 固定費` は、実作業が**同じ記録の**固定費を下回った記録の件数である。

| 目印 | 条件 |
| --- | --- |
| `束ねる候補` | 層が `supervisor` の行にだけ付く。`実作業 < 固定費` の件数 > 件数の半分。フェーズが `設計` の行には付けない（設計のフェーズはどのモードでも関門を返すため作る） |
| `割る候補` | 最大充填の最大 > `--window-limit`。両方付くときは `・` で連結する |
| `worker を使いすぎ` | supervisor と配下の worker の固定費の合計 > その supervisor の実作業 |

**フェーズごとの worker の使い方の行は、起動元（`parent_agent_id`）ごとに 1 つで、配下の worker を
1 件以上持つ supervisor だけを行にする。** 同じフェーズを工程の頭からやり直したときは supervisor が 2 つに
なるため 2 行になる。`supervisor` の列にはフェーズの中の連番（起動の早い順）を出し、`agent_id` は出さない。
連番は worker を持たない supervisor も消費する。conductor が直接起動した worker（起動元が null）は
どの行にも入らない。

`json` は `agents`（記録ごとの行、`agent_id` を除く）/ `agent_summary` / `layer_totals` / `totals`
（`records` / `fixed_sum` / `work_sum` / `total_spend`）/ `role_usage` / `excluded` / `unphased_supervisors` / `meta.window_limit`
を持つ。`markdown` は束ねの表の下に、フェーズが読めなかった（`その他` に落ちた）supervisor の件数を
1 行出す（`json` の `unphased_supervisors`）。

### 振り返りの記録へ貼る表

`retrospective` の記録の雛形「何が起きたか」の後ろに `## context window の大きさ` を置き、
`skill-stats --agents --session <conductor のセッション>` の 2 つ目・3 つ目・4 つ目の表をそのまま貼る。
目印の付いた行ごとに、束ねる・割る・worker を減らす・そのままのどれにするかと理由を 1 行書く。
ミッションを複数のセッションで通したときは `--session` を繰り返して 1 つの表にする。目印の判定は記録ごとの
比で行うため、固定費の違うセッションを束ねても判定の意味は変わらない。

## テスト観点

| 観点 | 確かめ方 |
| --- | --- |
| 深さ 0 / 1 / 2、深さ 1 の worker、重複行、先頭の合成の応答、429 で終わる記録、続けて完了した記録、壊れた行、語彙に無い `description` で各列の値が固定される | `plugins/ndf/scripts/tests/test_transcript_agents.py`（実物を最小化した記録のフィクスチャ） |
| `as_json` の 18 個のキー、`format_list` の `agent_id` 列の有無 | 同上 |
| `interrupted` が `rate_limit` だけを `resets_at` 付きで返し、`--layer` / `--depth` / `--agent` / `--parent` で絞れる。自動の継続が追記された conductor の記録から解除済みを返す。解除時刻が `now` と等しい境界で真 | 同上 |
| `wait-reset` が過ぎていれば直ちに終わり、未来なら差の秒数だけ眠り、2 件では早い方まで眠る。直接起動した worker だけでも眠る。`--max-sleep` の終了コード 3。負の秒数で 2 | 同上（眠る関数と時刻を差し替える） |
| `socket` を塞いだ実行で終了コード 0 | 同上 |
| conductor の行、複数セッション、2 つの supervisor の worker が起動元ごとに分かれる、層ごとの合計、束ねる候補が supervisor の行にだけ付く、worker を使いすぎの目印、`--agents` を付けない既定の出力が変わらない、`--window-limit` の既定が `context-window.md` と一致 | `plugins/ndf/skills/skill-stats/tests/test_agents_report.py` |
| 手順 2 の観点に context window の行、雛形に 3 つの表があり、パス・本文・`agent_id` の列を持たない | 文書を読んで確かめる（照合していたテストは #885 で削除） |

## 運用

**`.meta.json` が読めない配下の記録は深さ 0 になり、conductor として数えられる。** 通常は
Claude Code が記録と同時に書くため起きないが、欠けた記録は層の分類が誤る。

**`quotaLimits.resetsAt` は UNIX 秒の数値を前提にする。** 別の形で記録されると `resets_at` が null に
なり、`resets_passed` が真として扱われる（待たずに再開へ回る）。

**層ごとにモデルを変えたとき、上限を共有しない場合があるかは分かっていない。** `rate_limit_type` の
値の種類を集めて判断する。

## 関連リンク

- [issue #550](https://github.com/devbasex/ai-plugins/issues/550) — 工程ごとの context window の測定
- [issue #657](https://github.com/devbasex/ai-plugins/issues/657) — 上限で落ちた担当の検知
- [PR #749](https://github.com/devbasex/ai-plugins/pull/749) — 測定
- [PR #757](https://github.com/devbasex/ai-plugins/pull/757) — 中断と再開
- [ndf-agent-layers-unattended-run.md](ndf-agent-layers-unattended-run.md) — 3 層の運転
- 要求・設計・契約・計画の元の文書は `issues/old/milestone-18-unattended/` にある
