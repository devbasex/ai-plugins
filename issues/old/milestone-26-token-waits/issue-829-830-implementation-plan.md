# #829 / #830: 待つ間の問い合わせをやめ、conductor の会話を工程の切れ目で切る — 実装計画

## 関連リンク

- 要求と受け入れ条件: [issue-829-830-requirements.md](issue-829-830-requirements.md)（AC1〜AC26）
- 設計: [issue-829-830-design.md](issue-829-830-design.md)
- 決定の記録: [issue-829-830-design-decisions.md](issue-829-830-design-decisions.md)（決定 1〜10）
- 設計 Pull Request: https://github.com/devbasex/ai-plugins/pull/843 （マージ済み）
- 課題: #829 / #830（親は #827、マイルストーン 26「17 トークン消費の削減」）

## モード

standard（hook の新設と複数の Skill 文書の変更。本番の系へ届く操作を含まず、配布は別の工程）。

## 目的と非目的

達成したい状態:

- Claude Code で、前景の `sleep` の待ちと変わらないファイルの読み直しを hook が止め、代わりの待ち方を示す
- 文脈が上限を超えた conductor が工程へ入る起動を 1 度止め、新しい会話で打つ 1 行を示す
- 待ち方の規約と、新しい会話で状態を戻す手順が 1 か所ずつにある

やらないこと:

- **AC25 / AC26（効果の数値）はこの持ち場では確かめない。** 本番へ配布した後に `release-verification` で #827 の `measure.py` / `poll.py` / `extra.py` を回して確かめる（要求の前提 5）
- #731 / #656 / #345 / #828 / #680 の範囲、supervisor / worker の文脈量の上限（#768 / #773）
- Codex / Kiro / agy の hook の登録（決定 5）

## 前提

- 前提 1: 設計の「未確認」5 件のうち、hook の入力（`agent_id` の有無・`transcript_path` の指す先・記録の書き込みの時点）は Task 0 で実測して決める。実測できなければ設計の既定（`agent_id` が無く `/subagents/` を含まなければ conductor とみなす）で進める
- 前提 2: 「Codex / Kiro の起動の書き方」は各ランタイムの README の記載に合わせる（Task 6）
- 前提 3: 「通知の届き方」（AC11）と「背景の Bash の上限」は Task 7 の実機確認で決める

## 受け入れ条件

要求の AC1〜AC24 をこの Pull Request で満たす。AC11 と AC20 は実機の確認で、手順と結果を各 issue に残す。
AC25 / AC26 は配布後（上の「やらないこと」）。条件ごとの検証手段は設計の「テスト設計」の表に従う。

## 代替案と採否

設計の決定 1〜10 のとおり。実装で新たに選ぶものは次の 1 つ。

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | `sleep` の判定（引用・コメント・ヒアドキュメントの除去、`-c` / `eval` の中身の取り出し、ループの本体の対応）を hook の中の `python3` で書く | 採用 | bash の正規表現では入れ子の `do` / `done` の対応と引用の除去を読める形で書けない。`python3` は既存のスクリプト（`progress-record.sh` など）が既に使っている |
| B | すべて bash と `jq` で書く | 不採用 | 上記。`python3` が無いときは判定を通す（AC9 の扱い）ため可用性は落ちない |

## 修正対象

- 新設: `plugins/ndf/scripts/token-guard.sh`、`plugins/ndf/scripts/lib/token_guard_sleep.py`（sleep の判定。案 A）、`plugins/ndf/scripts/lib/token-guard-stages.txt`、`plugins/ndf/scripts/tests/test_token_guard.py`、`plugins/ndf/skills/development-workflow/references/waiting.md`
- 変更: `plugins/ndf/hooks/claude.json`、`development-workflow/SKILL.md`、`development-workflow/references/agent-layers.md`、`development-workflow/references/context-window.md`、`external-ai/references/cli-codex.md`、`external-ai/references/cli-agy.md`、`qa-security-scan/03-report-template.md`、`release/references/completion-check.md`、`plugins/ndf/README.md`

## タスク分解

### Task 0: hook の入力を実測する（未確認 1・2）

- **変更内容:** 入力を書き出すだけの hook を `claude -p --settings` で一時的に登録し、本体とサブエージェントの PreToolUse の入力（`agent_id`・`transcript_path`）と、その時点で記録に呼び出しの assistant 行が書かれているかを見る。利用者の設定は書き換えない
- **満たす受け入れ条件:** AC13 の判定方法の根拠
- **進め方:** 調査（テスト駆動の対象外）
- **結果（2026-09-23、Claude Code 2.1.280、`claude -p --settings` に入力を書き出す hook を登録）:**
  - サブエージェントの中の PreToolUse の入力には `agent_id` と `agent_type` が付く。本体の入力には付かない
  - サブエージェントの `transcript_path` は**親の記録を指す**（`/subagents/` を含まない）。区別は `agent_id` で行う
  - PreToolUse の時点で、その呼び出しを出した assistant 行はまだ記録に書かれていない。hook は 1 つ以上前の呼び出しの文脈量を読む（設計の既定どおり）

### Task 1: sleep の判定

- **対象ファイル:** `token-guard.sh`、`test_token_guard.py`
- **変更内容:** `Bash` の入力で、背景でない・`-c` / `eval` の中身も含め・コメントと引用とヒアドキュメントを除いた残りで、コマンドの位置の `sleep <数>` がループの本体にあるか上限を超えれば拒否する
- **満たす受け入れ条件:** AC5 / AC6 / AC8 / AC9 / AC10（sleep の分）
- **進め方:** 設計の AC5 / AC6 の例を失敗するテストとして書く → 最小実装 → 整理

### Task 2: 連続 Read の判定

- **対象ファイル:** 同上
- **変更内容:** `guards/` の解決（`wf_state_dir` と同じ順）、session ごとのロック、`read-<session_id>.json` の控えの置き換え、7 日より古い控えの削除
- **満たす受け入れ条件:** AC7 / AC8 / AC9 / AC10（Read の分）
- **進め方:** テスト先行

### Task 3: 文脈量の判定と工程 Skill の一覧

- **対象ファイル:** `token-guard.sh`、`token-guard-stages.txt`、`test_token_guard.py`
- **変更内容:** `Skill`（一覧にある工程 Skill）と `Agent` / `Task`（先頭語が持ち場の語彙）で、conductor の文脈量が上限を超えれば拒否し、印で次の同じ起動を 1 度通す
- **満たす受け入れ条件:** AC12〜AC17、AC22（既定値）
- **進め方:** テスト先行

### Task 4: hook の登録

- **対象ファイル:** `hooks/claude.json`
- **変更内容:** PreToolUse に matcher `Bash|Read|Skill|Agent|Task` で `token-guard.sh` を足す。既存の `worktree-guard.sh` の登録と順序は変えない
- **満たす受け入れ条件:** AC5 / AC7 / AC12 の実行経路、AC24
- **進め方:** 登録の形を確かめるテスト → 変更 → `claude plugin validate .`

### Task 5: 待ち方の規約

- **対象ファイル:** `waiting.md`、`agent-layers.md`、external-ai の 2 文書、`qa-security-scan/03-report-template.md`、`release/references/completion-check.md`
- **満たす受け入れ条件:** AC1〜AC4
- **進め方:** 文書の検査を先に書く → 文書を書く

### Task 6: 会話を切る規約と README

- **対象ファイル:** `context-window.md`、`development-workflow/SKILL.md`、`plugins/ndf/README.md`
- **満たす受け入れ条件:** AC18 / AC19 / AC21 / AC22 / AC23
- **進め方:** 文書の検査を先に書く → 文書を書く

### Task 7: 実機の確認

- **変更内容:** AC11（サブエージェントが背景の処理を残して応答を終えたとき、完了通知で再開されるか）と AC20（1 行だけで新しい会話から戻せるか）を実機で確かめ、手順と結果を #829 / #830 に残す
- **進め方:** 実機（テスト駆動の対象外）

## 影響範囲

- Claude Code の全層の Bash / Read / Skill / Agent の起動の前に hook が 1 本増える
- Codex / Kiro / agy の配布物の hook は変わらない（AC24）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| sleep の判定の誤検知で通常の Bash が止まる | タスクごとにテストを通す。通す例（AC6）を拒否の例と同数以上そろえ、環境変数で種類ごとに止められる（AC10） |
| hook の失敗でツールが止まる | 判定の失敗は常に 0 で通す（AC9）。登録に `continueOnError: true` |
| 実装が触る対象の構造 | 新設のスクリプトが中心で、既存の構造に手を入れない。実装の後の構造改善で足りる |

## 切り戻し手順

- `hooks/claude.json` の登録を 1 つ外せば hook は動かなくなる。利用者は環境変数（`NDF_SLEEP_GUARD=0` など）で種類ごとに止められる。データの移行は無い

## 完了の定義

- [ ] AC1〜AC24 を満たし、条件ごとに検証手段と結果が対応している（AC11 / AC20 は issue に記録）
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q`、`python3 scripts/check-skill-frontmatter.py`、`claude plugin validate .` が終了コード 0
- [ ] AC25 / AC26 は配布後の `release-verification` へ引き継ぐことを Pull Request の本文に書く
