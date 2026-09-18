# #550: context window の測定（実装 1 本目）

## 関連リンク

- 要求: [issue-550-657-requirements.md](issue-550-657-requirements.md)（AC20〜AC37 がこの Pull Request の範囲）
- 設計: [issue-550-657-design.md](issue-550-657-design.md)（決定 1 が実装の 3 分割を定める）
- 契約: [issue-550-657-design-contracts.md](issue-550-657-design-contracts.md)（データ構造と出力の形の正本）

## モード

`standard`。設計 Pull Request が済んでおり、この計画は設計の決定 1 の 1 本目（測定）を実装に落とす。

## 目的と非目的

達成したい状態:

- 会話の記録から conductor / supervisor / worker の 3 層を 1 件 1 行で読める
- 層ごとの合計・持ち場ごとの束ね・持ち場ごとの worker の使い方が `skill-stats` から出る
- 振り返りの記録の雛形に context window の表が載る

やらないこと:

- 無人の運転（`agent-layers.md` の運転の節・`/goal` の節・`context-window.md`・`cross-review` の「メイン」）。実装 2 本目が持つ
- 中断と再開（`interrupted` / `wait-reset` のコマンド、中断の点検の規約）。実装 3 本目が持つ
- 統計の送信（#159）

## 前提

- 前提 1: 記録の場所と形は設計の契約の「読む場所」「`AgentRecord` の値」のとおりである（実物の記録で確かめた）
- 前提 2: `plugins/ndf/scripts/lib/` は既にあり、Python は標準ライブラリだけを使う
- 前提 3: `transcript_agents.py` の `interrupted` / `wait-reset` は 3 本目が足す。1 本目は `list` だけを持ち、`ending` / `resets_at` / `interruptions` の値そのものは `AgentRecord` に持たせる（測定の列であるため）

## 受け入れ条件

要求の AC20〜AC37 をそのまま使う。検証手段は次のとおり。

- [ ] AC20〜AC26・AC28・AC30・AC31・AC34: `plugins/ndf/scripts/tests/test_transcript_agents.py`
- [ ] AC21・AC27・AC29・AC32・AC35・AC36・AC37: `plugins/ndf/skills/skill-stats/tests/test_agents_report.py`
- [ ] AC33・AC34（雛形の列）: `plugins/ndf/skills/retrospective/tests/test_context_window_section.py`
- [ ] AC63: `python3 scripts/check-skill-frontmatter.py`・`python3 scripts/check-doc-line-limit.py`・`uv run --with pytest pytest scripts/tests plugins/ndf -q`

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 読む部品を `scripts/lib/transcript_agents.py` に置き、`skill-stats` が読み込む | 採用 | 設計の決定 13。別の Skill の `scripts/` を呼ばない規約に合う |
| B | `skill-stats.py` の中に測定を書く | 不採用 | 3 本目の `development-workflow` が同じ読み取りを使う |
| C | 起動のたびに台帳を書く | 不採用 | 設計の決定 13。記録が既に必要な値を持つ |

## ドメイン用語

要求の「用語」の表が正本である。この計画で使う語は `conductor` / `supervisor` / `worker` / 持ち場 /
作業の種類 / 固定費（`fixed`）/ 最大充填（`peak`）/ 実作業（`work`）である。

## 不変条件

- `work` は `peak - fixed` に一致する（AC22）
- 出力にプロンプト・応答の本文・パス・`description` の持ち場以外の部分が現れない（AC30）
- `--agents` を付けない `skill-stats` の出力が変わらない（AC32）
- ネットワークを呼ぶ経路を持たない（AC34）

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `skill-stats` の引数 | `--agents` / `--session` / `--layer` / `--window-limit` を足す | 追加のみ。既定の振る舞いは変えない |
| データスキーマ | 無し | 新しい保存先を作らない |

## 修正対象

- `plugins/ndf/scripts/lib/transcript_agents.py`（新設）
- `plugins/ndf/scripts/lib/README.md`（表の 1 行）
- `plugins/ndf/scripts/tests/test_transcript_agents.py`（新設）
- `plugins/ndf/scripts/tests/fixtures/transcript_agents/`（新設）
- `plugins/ndf/skills/skill-stats/scripts/skill-stats.py`
- `plugins/ndf/skills/skill-stats/SKILL.md`
- `plugins/ndf/skills/skill-stats/tests/test_agents_report.py`（新設）
- `plugins/ndf/skills/retrospective/SKILL.md`
- `plugins/ndf/skills/retrospective/tests/test_context_window_section.py`（新設）

## タスク分解

### Task 1: 記録 1 件を読む

- **対象ファイル:** `plugins/ndf/scripts/lib/transcript_agents.py`、`plugins/ndf/scripts/tests/test_transcript_agents.py`、フィクスチャ
- **変更内容:** `AgentRecord` と `read_file` / `role_of` / `read_session`。層・持ち場・固定費・最大充填・実作業・応答数・所要・終わり方・中断の回数・解除時刻・起動元を契約の表のとおりに取る
- **満たす受け入れ条件:** AC20〜AC26・AC31
- **進め方:** 実物から作ったフィクスチャで失敗するテストを先に書く

### Task 2: `list` のコマンドと出力

- **対象ファイル:** 同上
- **変更内容:** `list` サブコマンド（`--session` 繰り返し可・`--layer`・`--format md|json`）。壊れた行の件数を標準エラーへ 1 行。出力にパス・本文を含めない
- **満たす受け入れ条件:** AC27・AC30・AC31・AC34
- **進め方:** テスト先行

### Task 3: `skill-stats --agents` の 4 つの表

- **対象ファイル:** `skill-stats.py`、`skill-stats/tests/test_agents_report.py`
- **変更内容:** 記録ごとの行・層と持ち場とモデルの束ね（印）・層ごとの合計・持ち場ごとの worker の使い方。`--session` は `--agents` を付けないときも conductor の記録だけで数える
- **満たす受け入れ条件:** AC21・AC27〜AC29・AC32・AC35〜AC37
- **進め方:** テスト先行

### Task 4: 文書（`skill-stats` / `retrospective` / `lib/README.md`）

- **対象ファイル:** `skill-stats/SKILL.md`、`retrospective/SKILL.md`、`scripts/lib/README.md`、`retrospective/tests/test_context_window_section.py`
- **変更内容:** 使い方と集計項目、振り返りの手順 2 の観点と記録の雛形の 3 つの表
- **満たす受け入れ条件:** AC33・AC34
- **進め方:** テスト先行（文書テスト）

## 影響範囲

- `skill-stats` を呼ぶ既存の手順（既定の出力は変えない）
- `retrospective` の記録の雛形（PR #747 が同じ `SKILL.md` を触る。競合は後からマージする側が解く）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `skill-stats.py` が 608 行で、測定を足すと肥大する | 読み取りを `transcript_agents.py` へ寄せ、`skill-stats.py` には集計と整形だけを置く。構造改善の工程で見直す |
| テストファイルの基底名が既存と衝突すると収集で落ちる（#748） | 設計の決定 25 が指す名前で新設し、`git ls-files` で同名が無いことを確かめる |
| `retrospective/SKILL.md` の競合 | 足す節を末尾寄りの 1 か所に閉じ、既存の節を書き換えない |

## 切り戻し手順

コードの追加だけで、保存先も既存の出力も変えない。Pull Request を戻せば元に戻る。

## 完了の定義

- [ ] AC20〜AC37 に検証手段と結果が対応している
- [ ] `quality-gates` の証跡（テスト・静的解析・`claude plugin validate .`）が残っている
