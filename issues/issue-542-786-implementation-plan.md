# #542 #786: cross-review のラウンドを減らす — 実装計画

## 関連リンク

- 要求と受け入れ条件: [issue-542-786-requirements.md](issue-542-786-requirements.md)（AC1〜AC20）
- 設計: [issue-542-786-design.md](issue-542-786-design.md)
- 決定の記録: [issue-542-786-design-decisions.md](issue-542-786-design-decisions.md)（決定 1〜12）
- 設計 PR: https://github.com/devbasex/ai-plugins/pull/920（develop へマージ済み）

## モード

standard（設計の持ち場で判定済み。複数の Skill の scripts と共通層・文書にまたがる機能の変更）。

## 目的と非目的

達成したい状態:

- cross-review の既定の母集合を claude / codex / kiro とホストにし、動かない担当で収束が止まるのをやめる（#786）
- 1 ラウンド目で出し切らせ、2 ラウンド目以降は前の修正が作った穴を同じラウンドで拾わせる（#542）

やらないこと: 収束の判定・振動の検知・`max_rounds`・起動し直しの規則の変更、#631、担当の振り替え（#919）、控えの圧縮。

## 修正対象

| 区分 | ファイル |
| --- | --- |
| 共通層 | `plugins/ndf/scripts/lib/assignment.py` |
| cross-review | `scripts/state.py` / `scripts/launch-reviewer.sh`（`launch-codex.sh`・`launch-agy.sh` のコメント） |
| fix | `scripts/fetch-pr-comments.sh`（`--strict`） |
| cross-refactoring | `refactor_lib/commands/setup.py` / `refactor_lib/commands/report.py` / `prompts/apply.md` |
| 文書 | cross-review の `SKILL.md`・`docs/01`・`02`・`04`・`05`・`06`、cross-refactoring の `docs/01`、確定仕様 2 本、`plugins/ndf/README.md`、`CLAUDE.md` の cross-review 節 |
| テスト | 設計の「テスト設計」の表のとおり（既存の期待の書き換えと新規 `test_state_round_changes.py`） |

## タスク分解

### Task 1: 既定の母集合から agy を外し、母集合に無い者の除外を無視する（F1 F2・決定 1 2 12）

- **対象:** `assignment.py`、cross-review の `_resolve_reviewers` と `_print_participants` と再開の作り直し、cross-refactoring の `setup.py`（`resolve_participants` と `_resume`）と `report.py`
- **変更:** `DEFAULT_REVIEW_RUNTIMES` と `review_pool`。`Participants.ignored_exclude`。`only` を足す者として扱う。呼び出し側の `ℹ` の 1 行・報告の 1 行・再開で `ignored_exclude` を足し戻す（`--include` と重なる名前を除く）
- **満たす受け入れ条件:** AC1〜AC4d
- **進め方:** 既存の期待（設計の「期待を書き換える既存のテスト」の表）を先に書き換えて失敗させ、実装で通す

### Task 2: 既存コメントの控えをラウンドごとに取り直す（F3・決定 6）

- **対象:** `fetch-pr-comments.sh`、`state.py`（`_fetch_existing_comments`、`init`、`start-round`）
- **満たす受け入れ条件:** AC12 AC12a AC12b AC13
- **進め方:** 偽の `fetch-pr-comments.sh` で `start-round` を動かすテストを先に書く

### Task 3: 前のラウンドからの変更の節（F4・決定 4）

- **対象:** `state.py`（`start-round`）、`launch-reviewer.sh`
- **満たす受け入れ条件:** AC9 AC10 AC11
- **進め方:** 一時の git リポジトリで 2 つの head を作るテストを先に書く

### Task 4: プロンプトの指示（F5 F7・決定 3 11）

- **対象:** `launch-reviewer.sh`、cross-refactoring の `prompts/apply.md`
- **満たす受け入れ条件:** AC6 AC7 AC8
- **進め方:** 文言は `.md` の文言テストを書かない方針（#885）に従い目で確かめる。既存のプロンプト組み立てのテストが通ることを確かめる

### Task 5: 設計 PR の観点（F6・決定 5）

- **対象:** `state.py`（`_is_design_doc_path`・`DESIGN_REVIEW_TEMPLATE`・2 つの表）
- **満たす受け入れ条件:** AC14 AC15 AC16
- **進め方:** 分類のテストを先に書く

### Task 6: 文書の更新

- **対象:** 上の「文書」の行
- **満たす受け入れ条件:** AC5（ほか各機能の説明）
- **進め方:** テスト駆動の対象外（文書）。`grep -rn "4 者"` で現行の説明が残らないことを確かめる

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 並行の 2''（PR #917）が cross-refactoring の `setup.py`・`report.py`・`prompts/apply.md`・`docs/01` を触る | 触る範囲を関数単位に絞る。後から入る側が develop を取り込んで衝突を解く |
| `state.py`（約 5,000 行）への追加 | 触る範囲が狭く、テストが厚い。タスクごとにテストを通す |

## 切り戻し手順

コードと文書だけの変更で、状態ファイルへの追加は `participants.ignored_exclude` の 1 項目（読まない旧版でも害が無い）。PR を revert すれば戻る。

## 完了の定義

- [ ] AC1〜AC20 を満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest . -q -n 4` が通る
- [ ] `claude plugin validate .` が終了コード 0
