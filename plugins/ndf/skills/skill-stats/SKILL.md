---
name: skill-stats
description: "Measure Skill usage from Claude Code transcripts: invocations, trigger hit rate, per-Skill breakdown. Use when auditing which Skills fire（skill統計・skill利用分析）."
allowed-tools:
  - Bash
  - Read
---

# Skill 利用統計スキル

Claude Code の transcript JSONL ファイル (`~/.claude/projects/*.jsonl`) を解析し、NDFプラグインのskill利用統計を算出する。

## 用途

- どの skill がよく呼び出されているか把握
- 呼び出されるべきだったのに呼ばれなかった skill を発見
- skill の description / triggers の網羅性改善に役立てる

## 使用方法

```bash
# 過去90日分を全プロジェクト合算 (デフォルト、transcript保持期間に合わせる)
/ndf:skill-stats

# --- 期間フィルタ ---
/ndf:skill-stats --days 30                            # 直近30日
/ndf:skill-stats --from 2026-04-01                    # 2026-04-01 以降
/ndf:skill-stats --from 2026-04-01 --to 2026-04-30    # 絶対範囲 (両端inclusive)

# --- skill / プロジェクト フィルタ ---
/ndf:skill-stats --skill pr                           # skill名部分一致
/ndf:skill-stats --project carmo                      # プロジェクト名部分一致

# --- プロジェクト別集計 ---
/ndf:skill-stats --by-project                         # プロジェクトごとに表を分けて出力
/ndf:skill-stats --by-project --project carmo         # carmo を含むプロジェクトだけ分解

# --- 出力形式 ---
/ndf:skill-stats --format json                        # JSON (projects配列 + grand_skills)
/ndf:skill-stats --show-keywords                      # 抽出されたTriggersも併記

# --include-fallback: Triggers 未定義 skill でも description から語彙抽出してマッチ
# (ノイズが多いので通常は不要)
/ndf:skill-stats --include-fallback

# --- 3 層の context window の測定 (#550) ---
/ndf:skill-stats --agents --session <conductor のセッション>   # 4 つの表を出す
/ndf:skill-stats --agents --session A --session B              # ミッションを 1 つの表にする
/ndf:skill-stats --agents --layer supervisor                   # 1 つの層に絞る
/ndf:skill-stats --agents --session A --window-limit 150000    # 割る候補の目安を変える
```

内部的には以下のコマンドを実行する:

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/skills/skill-stats/scripts/skill-stats.py "$@"
```

### プロジェクトの決定方法

transcript JSONL 先頭の `cwd` フィールドを優先してプロジェクトラベルを決める (例: `/work/ai-plugins` → `ai-plugins`)。取得できない場合は transcript ディレクトリ名 (例: `-work-ai-plugins`) を復元 (`-` → `/`) して使用する。

## 集計項目

| 項目 | 定義 |
|---|---|
| **自動起動数** (auto) | `assistant` メッセージ内の `tool_use.name=="Skill"` で `input.skill=="ndf:<name>"` の件数 |
| **明示起動数** (explicit) | `user` メッセージの `<command-name>/ndf:<name></command-name>` の件数。プラグイン接頭辞のない `/name` 形式も同じ skill として数える |
| **呼び出し数** (invocations) | 自動起動数 + 明示起動数 |
| **関連話題数** (triggers) | `user` メッセージのテキストに、skillの `description` / `when_to_use` に列挙された Triggers キーワードが含まれる件数 |
| **ヒット数** (hits) | 関連話題を含むユーザーメッセージの直後 (次のユーザーメッセージまでの間) に該当skillが**自動起動**した件数。間にスラッシュコマンドが入った場合はそこで打ち切る (利用者が自分で打った時点でトリガは発火しなかったため) |
| **ヒット率** (hit_rate) | `hits / triggers` (%) |

トリガ語は `description` / `when_to_use` 末尾の全角括弧（`（語・語）`）から抽出する。

### ヒット率の解釈

- **高い (80%+)**: description/triggers が適切で、該当文脈で正しく起動できている
- **低い (< 30%)**: キーワードが広すぎて関係ない話題にマッチしているか、モデルがskillを起動しにくい description になっている可能性
- **triggers が 0**: description のキーワードがユーザー入力に出現していない → 該当用途で使われていないか、triggersの定義見直しが必要

## 出力例 (Markdown)

```
| skill | triggers源 | 計 | 自動 | 明示 | 関連話題 | ヒット | ヒット率 |
|---|---|---:|---:|---:|---:|---:|---:|
| ndf:pr | none | 12 | 2 | 10 | - | - | - |
| ndf:fix | explicit | 11 | 3 | 8 | 8 | 3 | 37.5% |
...
| **合計** | | **56** | **14** | **42** | **142** | **45** | **31.7%** |
```

## 3 層の context window の測定

`--agents` は、会話の記録（conductor の `<セッション>.jsonl` と
`<セッション>/subagents/agent-<識別子>.jsonl`）を **conductor / supervisor / worker** の
層の単位で読み、4 つの表を出す。読むのは `scripts/lib/transcript_agents.py` で、**ローカルの
記録を読むだけで送信の経路を持たない**（#159）。

| 表 | 何が出るか | `--session` が要るか |
| --- | --- | --- |
| 記録ごと | 記録 1 件につき 1 行（層・フェーズ・深さ・モデル・固定費・最大充填・実作業・応答数・所要・終わり方・中断） | 要る |
| 束ね | 層とフェーズ（worker は作業の種類）とモデルの組ごとの件数・中央値・`実作業 < 固定費` の件数・最大充填の最大・目印 | 要らない |
| 層ごとの合計 | 層ごとの件数・固定費の合計・実作業の合計と、3 層を合算した総消費 | 要らない |
| フェーズごとの worker の使い方 | supervisor 1 つと、その配下の worker の固定費の合計 | 要る |

| 語 | 意味 |
| --- | --- |
| 固定費 | 作業を始める前に既に埋まっている量。**合成でない最初の応答の入力トークン**（`input_tokens` + `cache_read_input_tokens` + `cache_creation_input_tokens`） |
| 最大充填 | 応答ごとの同じ合計の最大 |
| 実作業 | 最大充填 − 固定費 |
| 応答数 | 合成でない応答の `message.id` の異なる数（同じ id の行が並んでも 1 と数える） |
| 終わり方 | `completed` / `in_progress` / `rate_limit` / `api_error` |
| 中断 | 利用上限（429）で中断した後に続けた回数 |

| 目印 | 条件 |
| --- | --- |
| `束ねる候補` | **supervisor の行にだけ付く。** 実作業が同じ記録の固定費を下回った記録が過半数。フェーズが `設計` の行には付けない |
| `割る候補` | 最大充填の最大が `--window-limit`（既定 200000）を超えた |
| `worker を使いすぎ` | supervisor と配下の worker の固定費の合計が、その supervisor の実作業を上回った |

**判定は記録ごとの比で行う。** 中央値どうしを比べないため、固定費の水準が違うセッションを
束ねても判定の意味は変わらない。中央値と合計の列は分布の目安である。

**応答が 3 に満たない記録は束ねの表からだけ外す。** 層ごとの合計とフェーズごとの表には含める
（短命な記録も固定費を使うため）。外した件数は表の下に 1 行出る。

**出力は数値と語彙だけを持つ。** プロンプト・応答の本文・ファイルのパス・`description` の
フェーズ以外の部分を出さない。サブエージェントの識別子は
`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/lib/transcript_agents.py list --session <ID>` にだけ出る。

`--session` は `--agents` を付けないときにも効く。そのときは **Skill の統計をその
セッションの conductor の記録だけで数える**（conductor がどの Skill を起動したかを見る）。

## 前提条件

- Python 3.8+ (標準ライブラリのみ使用)
- `~/.claude/projects/` に transcript JSONL が存在
- transcript の保持期間は `~/.claude/settings.json` の `cleanupPeriodDays` に依存。NDFプラグインの保持期間フックが 90 日を確保する

## 制限事項

- **モデル起動型以外は関連話題数が計算できない場合がある**: `disable-model-invocation: true` の skill (例: `/ndf:pr` などのワークフロー系) は、ユーザーが明示的にスラッシュコマンドで呼び出すのが通常。triggers キーワードが `description` / `when_to_use` のどちらにも明示されていなければ「関連話題」が 0 となり、ヒット率も計算不能となる
- **ユーザーメッセージのパース**: 関連話題の判定では `<local-command-*>`, `<command-name>`, `<system-reminder>` タグを除外する (tool_result ブロックも除外)。明示起動数だけは `<command-name>` を対象に数える
- **日本語キーワードマッチ**: 単純な部分一致 (case-insensitive) のため、文脈を考慮した判定ではない

## 関連スキル

- `/ndf:markdown-writing` — 結果を読みやすく整形するためのガイドライン
