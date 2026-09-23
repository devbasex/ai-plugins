# #828 / #680: サブエージェントに Skill 本文を丸ごと読ませず、仕事を分ける器を比べて選ぶ — 実装計画

## 関連リンク

- 要求と受け入れ条件: [issue-828-680-requirements.md](issue-828-680-requirements.md)
- 設計: [issue-828-680-design.md](issue-828-680-design.md)（設計 PR #890 でマージ済み）
- 決定の記録: [issue-828-680-design-decisions.md](issue-828-680-design-decisions.md)
- 課題: #828 #680（親 #827）

## モード

standard（conductor の判定。複数ファイルのスクリプトと文書の変更で、本番の系へ届く操作を含まない）

## 目的と非目的

達成したい状態:

- 進行の記録が `projects-sync.sh` の 1 行で issue の本文・盤面・通過工程の控えへ残る（AC5〜AC8）
- supervisor / worker の起動指示が Skill 本文の読み込みを求めず、worker は `ndf:worker` の定義で Skill と Agent を塞がれる（AC1〜AC4・AC14）
- 抜粋の形と規則が 1 か所にあり、`progress-tracking` の抜粋が見本として 1 つある（AC9・AC15）
- 器の比較表と小さな作業の線引きが `work-vessels.md` にある（AC10・AC11）

やらないこと:

- 工程の Skill の本文を縮めること、`progress-tracking` 以外の抜粋（#845 の子）
- 抜粋と本文の一致の検査（#855）、`$SCRIPTS` の 1 コマンド化（#847）
- 版を上げること（マイルストーン 26 の配布で行う）
- AC12 の計測（配布後の `release-verification`）

## 前提

- 前提 1: 通過工程の控え（`workflow-common.sh` の照合）は `projects-sync.sh` の呼び出しの形を変えなければ読み方を変えずに済む（決定 1）
- 前提 2: `projects-sync.sh` の既存のテストの偽の `gh` は `issue view` / `issue edit` に 0 を返すため、issue の本文の更新を足しても既存の期待は崩れない

## 受け入れ条件

要求文書の AC1〜AC11・AC13〜AC15 をこの Pull Request で満たす。AC12 は配布後。検証手段は設計の「テスト設計」の表に従う。

## 代替案と採否

設計の決定の記録（決定 1〜10）で決めた。この計画で新たに比べた案は無い。

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `projects-sync.sh` の呼び出し | 形は同じ。盤面の宣言が無くても issue の本文を更新するようになる | 追加のみ。誤りの終了コード 2 の契約は変えない |
| `progress-record.sh` の呼び出し | 工程名の位置に `-` を受ける | 追加のみ。既存の呼び出しはそのまま動く |
| エージェント定義 | `ndf:worker` が増える | 追加のみ |

## 修正対象

- `plugins/ndf/scripts/projects-sync.sh` / `plugins/ndf/scripts/progress-record.sh`
- `plugins/ndf/scripts/tests/test_projects_sync_record.py`（新設）/ `plugins/ndf/scripts/tests/test_progress_record.py`
- `plugins/ndf/agents/worker.md`（新設）/ `plugins/ndf/.claude-plugin/plugin.json` / エージェント数の記述 7 箇所
- `plugins/ndf/scripts/tests/test_worker_agent.py`（新設）
- `plugins/ndf/skills/development-workflow/references/agent-layers.md` / `work-vessels.md`（新設）/ `context-window.md` / `stage-completeness.md`
- `plugins/ndf/skills/development-workflow/SKILL.md`
- `plugins/ndf/skills/progress-tracking/SKILL.md` / `references/excerpt.md`（新設）
- `plugins/ndf/skills/AUTHORING.md`
- 工程の Skill の末尾の記録の文（19 Skill の定型文と `design` の 2 件）

## タスク分解

### Task 1: 記録のコマンドを 1 行にする（F1）

- **対象ファイル:** `projects-sync.sh` / `progress-record.sh` とテスト
- **変更内容:** `progress-record.sh` が工程名 `-` で見出し行だけを更新する。`projects-sync.sh` は引数の検査を宣言の読み込みより前へ移し、`stage` / `mode` / `worktree` / `plan` で先に `progress-record.sh` を呼んでから盤面を更新する。`status` は本文を書かない
- **満たす受け入れ条件:** AC5 / AC6 / AC8
- **進め方:** 失敗するテスト（宣言なしで本文だけ更新・`mode` で見出し行だけ・知らない値で本文を書かない・4 キーで 2 コマンドの組と同じ本文・`status` は本文を書かない・`-` の受け付け）→ 最小実装 → 既存の `test_stage_check.py` / `test_workflow_guard.py` / `test_projects_sync_cache.py` を変更なしで通す

### Task 2: worker のエージェント定義（F3）

- **対象ファイル:** `plugins/ndf/agents/worker.md` / `plugin.json` / エージェント数の記述 7 箇所 / テスト
- **変更内容:** `disallowedTools: Skill, Agent` の定義を置き、`plugin.json` の `agents` へ足す。説明の数を設計の表のとおりに直す
- **満たす受け入れ条件:** AC14（定義の検査。実機の確かめは #828 へ残す）
- **進め方:** 失敗するテスト（定義の frontmatter に `Skill` と `Agent` が拒否され、`plugin.json` に登録されている）→ 定義を置く → `claude plugin validate .` と実機で `ndf:worker` を起動して確かめる

### Task 3: 起動指示の雛形と器の比較（F2 / F3 / F5 / F6）

- **対象ファイル:** `agent-layers.md` / `work-vessels.md` / `context-window.md` / `stage-completeness.md` / `development-workflow/SKILL.md`
- **変更内容:** supervisor の起動指示へ「記録のコマンド」を足し、規則 3 と 7 を直す。worker の起動指示を `subagent_type: ndf:worker` と「手順」の項目にし、規則 6 を足す。`work-vessels.md` に器の比較表と線引きを置いて指す
- **満たす受け入れ条件:** AC1 / AC2 / AC3 / AC4 / AC10 / AC11
- **進め方:** 文書の変更のためテスト駆動を適用しない（.md の文言を固定するテストは書かない）。設計の「雛形の検査」の `grep` で確かめる

### Task 4: 抜粋の形と工程の Skill の末尾の文（F4）

- **対象ファイル:** `AUTHORING.md` / `progress-tracking/SKILL.md` / `progress-tracking/references/excerpt.md` / 20 Skill の末尾の文
- **変更内容:** 抜粋の規約（目印・見出し 3 つ・40 行かつ 2,000 文字・入れるもの / 入れないもの・超えたときの扱い）を `AUTHORING.md` へ置く。`progress-tracking` の抜粋を書き、本文から指す。末尾の文を記録のコマンド 1 行へ変える
- **満たす受け入れ条件:** AC7 / AC9 / AC15
- **進め方:** 文書の変更。`grep -rn "この工程に入ったら.*progress-tracking" plugins/ndf/skills/*/SKILL.md` が 0 件、抜粋が `wc -l` 40 以下・2,000 文字以下で確かめる

## 影響範囲

- 進行の記録を打つすべての経路（conductor・supervisor・対話の会話・工程の Skill）
- Claude Code と agy のエージェント一覧（agy は symlink で同じ定義が配られる）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `projects-sync.sh` が issue の本文も書くことで、既存のテストの偽の `gh` が想定しない呼び出しを受ける | タスクごとにテストを通す。既存のテストは変えずに通すことを AC8 の確かめとする |
| 20 Skill の末尾の文の一括変更で文面が崩れる | 置き換えの前後を `grep` で数え、`check-skill-frontmatter.py` と全体テストを通す |

## 切り戻し手順

- Pull Request を revert すれば戻る。データの移行は無い（issue の本文と盤面に残る値の形は変わらない）

## 完了の定義

- [ ] 上の受け入れ条件ごとに検証手段と結果が対応している
- [ ] 全体テスト・`check-skill-frontmatter.py`・`claude plugin validate .` が終了コード 0
