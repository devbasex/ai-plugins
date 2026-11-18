---
name: skill-stats
description: "Measure Skill usage from Claude Code transcripts: invocations, trigger hit rate, per-Skill breakdown. Use when auditing which Skills fire（skill統計・skill利用分析）."
allowed-tools:
  - Bash
  - Read
---

# Skill 利用統計スキル

Claude Code の会話の記録（`~/.claude/projects/<プロジェクト>/*.jsonl`）を AI が `jq` で読み、NDF の Skill の利用統計と、
3 層（conductor / supervisor / worker）の context window の使い方を数える。11 系にこの集計のサブコマンドは無い。
**ローカルの記録を読むだけで、外へ送らない。** 出すのは数値と Skill の名前だけで、プロンプト・応答の本文・ファイルのパスを出さない。

## 用途

- どの Skill がよく呼び出されているか把握する
- 呼び出されるべきだったのに呼ばれなかった Skill を見つける
- Skill の description と triggers の網羅性を直す材料にする
- 振り返りで、層ごとの固定費と実作業を測る（`retrospective` の「context window」）

## 引数

| 引数 | 意味 | 既定 |
| --- | --- | --- |
| 期間（`--days N` / `--from <日付>` / `--to <日付>`） | 数える記録の `timestamp` の範囲 | 直近 90 日 |
| `--skill <語>` | Skill の名前の部分一致で絞る | なし |
| `--project <語>` | プロジェクトのディレクトリ名の部分一致で絞る | すべて |
| `--agents --session <ID>` | 3 層の context window を測る（繰り返すと 1 つの表にまとめる） | なし |
| `--window-limit <トークン>` | `--agents` の `割る候補` の上限 | 200000 |

```
/ndf:skill-stats                                    # 直近 90 日の全プロジェクト
/ndf:skill-stats --days 30 --project <プロジェクト>
/ndf:skill-stats --agents --session <conductor のセッション>
```

## 呼び出しを数える

自動起動（`tool_use` の `Skill`）と明示起動（`<command-name>/ndf:<名前></command-name>`）を、記録の行の `timestamp` で絞って数える。

```bash
cd ~/.claude/projects
find . -maxdepth 2 -name '*.jsonl' -print0 | xargs -0 jq -r --arg from 2026-10-19 '
  select((.timestamp // "") >= $from)
  | if .type == "assistant" then (.message.content[]? | select(.type == "tool_use" and .name == "Skill") | "auto\t" + .input.skill)
    elif .type == "user" then (.message.content | if type == "string" then . else (.[]? | select(.type == "text") | .text) end
         | scan("<command-name>/([^<]+)</command-name>") | "explicit\t" + .[0])
    else empty end' 2>/dev/null | grep -P '\tndf:' | sort | uniq -c | sort -rn
```

プロジェクトで絞るときは `find` の起点を `./<プロジェクトのディレクトリ>` にする。`--to` があれば `select` に
`and .timestamp < $to` を足す。結果は次の表にまとめる。

| skill | 計 | 自動 | 明示 | 関連話題 | ヒット | ヒット率 |
|---|---:|---:|---:|---:|---:|---:|
| ndf:fix | 11 | 3 | 8 | 8 | 3 | 37.5% |

## 関連話題とヒット率

| 項目 | 定義 |
|---|---|
| **関連話題数** (triggers) | 利用者のメッセージのテキストに、Skill の `description` 末尾の全角括弧（`（語・語）`）の語が含まれる件数 |
| **ヒット数** (hits) | 関連話題を含む利用者のメッセージの後、次の利用者のメッセージまでに、その Skill が**自動起動**した件数。間にスラッシュコマンドが入ったらそこで打ち切る |
| **ヒット率** (hit_rate) | `hits / triggers` (%) |

1 つの会話の記録を、利用者のメッセージ（語を含むか・スラッシュコマンドか）と Skill の起動の並びにして読み、AI が数える。
`<system-reminder>` と tool_result は利用者のメッセージに数えない。

```bash
jq -r --arg w '<語>' '
  if .type == "user" and (.isSidechain | not) then
    (.message.content | if type == "string" then . else ([.[]? | select(.type == "text") | .text] | join(" ")) end
     | gsub("<system-reminder>[\\s\\S]*?</system-reminder>"; "")
     | if test("<command-name>") then "SLASH" elif test($w) then "TOPIC" else empty end)
  elif .type == "assistant" then (.message.content[]? | select(.type == "tool_use" and .name == "Skill") | "SKILL\t" + .input.skill)
  else empty end' <記録のファイル>
```

`TOPIC` の後、次の `TOPIC` か `SLASH` までに `SKILL	ndf:<名前>` があれば 1 ヒットである。

- **高い (80%+)**: description と triggers が適切で、該当文脈で正しく起動できている
- **低い (< 30%)**: 語が広すぎて関係ない話題にマッチしているか、モデルが Skill を起動しにくい description になっている
- **triggers が 0**: 語が利用者の入力に出ていない。使われていないか、語の見直しが要る

## 3 層の context window の測定

`--agents` は、conductor の `<セッション>.jsonl` と `<セッション>/subagents/agent-<識別子>.jsonl` を記録ごとに読む。
層は同じ名前の `.meta.json` の `agentType`（`ndf:supervisor` / `ndf:worker`）で決め、`.meta.json` の無い記録は conductor とする。

```bash
cd ~/.claude/projects/<プロジェクト>
S=<セッション>
for f in "$S.jsonl" "$S"/subagents/agent-*.jsonl; do
  m="${f%.jsonl}.meta.json"
  layer=$( [ -f "$m" ] && jq -r '.agentType' "$m" || echo conductor )
  jq -s -c --arg layer "$layer" '
    [.[] | select(.type == "assistant" and .message.model != "<synthetic>" and .message.usage != null)
         | {id: .message.id, t: (.message.usage | .input_tokens + .cache_read_input_tokens + .cache_creation_input_tokens)}]
    | if length == 0 then empty else
      {layer: $layer, fixed: .[0].t, peak: (map(.t) | max), work: ((map(.t) | max) - .[0].t), responses: (map(.id) | unique | length)} end' "$f"
done
```

| 語 | 意味 |
| --- | --- |
| 固定費（`fixed`） | 作業を始める前に既に埋まっている量。合成でない最初の応答の入力トークン（`input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens`） |
| ピーク使用量（`peak`） | 応答ごとの同じ合計の最大 |
| 実作業（`work`） | ピーク使用量 − 固定費 |
| 応答数（`responses`） | 合成でない応答の `message.id` の異なる数 |

記録ごとの行から、AI が層ごとの件数・固定費と実作業の合計と中央値をまとめ、次の目印を付ける。

| 目印 | 条件 |
| --- | --- |
| `束ねる候補` | **supervisor にだけ付く。** 実作業が同じ記録の固定費を下回った記録が過半数。フェーズ（`.meta.json` の `description`）が `設計` のものには付けない |
| `割る候補` | ピーク使用量が上限を超えた。上限は `--window-limit` で渡せばその値、無ければ 200000 |
| `worker を使いすぎ` | supervisor と配下の worker の固定費の合計が、その supervisor の実作業を上回った |

**判定は記録ごとの比で行う。** 中央値どうしを比べないため、固定費の水準が違うセッションをまとめても判定の意味は変わらない。
応答が 3 に満たない記録は目印の判定から外し、合計には含める。

## 前提条件

- `jq` が使える
- `~/.claude/projects/` に会話の記録がある。保持期間は `~/.claude/settings.json` の `cleanupPeriodDays` に依存する

## 制限事項

- **モデル起動型以外は関連話題数が出ないことがある**: `disable-model-invocation: true` の Skill は利用者がスラッシュコマンドで呼ぶのが通常で、語が無ければ関連話題が 0 になる
- **日本語の語の一致**: 単純な部分一致のため、文脈を考慮した判定ではない

## 関連スキル

- `document-style` — 結果を読みやすく整形するためのガイドライン
