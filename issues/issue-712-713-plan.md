# #712 / #713: 課題を根本原因の場所で直せるようにする（実装計画）

## 関連リンク

- 受け入れ条件: [issue-712-713-requirements.md](issue-712-713-requirements.md)
- 設計: [issue-712-713-design.md](issue-712-713-design.md)
- 決定の記録: [issue-712-713-decisions.md](issue-712-713-decisions.md)
- 設計 Pull Request: #714（マージ済み）

## モード

`standard`。配布物の Skill の手順（棚卸の判定）を変え、4 つの Skill の境界にまたがる。

## 目的と非目的

達成したい状態:

- 棚卸が「ルートコーズ」を判定し、修正レイヤーと採る手を記録できる
- 構造の判断の担い手が `issue-upkeep` であることを、4 つの Skill のどこから読んでもたどれる

やらないこと（要求の「触らないもの」と同じ）:

- 4 クラスタすべての処理、親 issue そのものの修正、子 issue を閉じること
- その場の判断（`out-of-scope`）を 3 択から増やすこと
- 重要度が高い課題を主題によらず直近へ入れる規則そのものを変えること。実地の試行で直近が `03 研究基盤` になり、利用者は `05` を選んだ。範囲外の課題として起票する

## 修正対象

| ファイル | 受け入れ条件 |
| --- | --- |
| `plugins/ndf/skills/issue-upkeep/SKILL.md` | AC1〜AC7 / AC15 / AC16 / AC25 / AC26 / AC31 / AC50 |
| `plugins/ndf/skills/issue-upkeep/references/grouping.md`（新設） | AC8〜AC14 / AC35 / AC36 / AC43 / AC45〜AC48 |
| `plugins/ndf/skills/issue-upkeep/references/no-work.md` | AC41 |
| `plugins/ndf/skills/issue-upkeep/references/milestones.md` | AC14 / AC33 / AC38 / AC39 |
| `plugins/ndf/skills/out-of-scope/SKILL.md` | AC27 |
| `plugins/ndf/skills/problem-solving/SKILL.md` | AC28 / AC30 |
| `plugins/ndf/skills/retrospective/SKILL.md` | AC29 |
| `plugins/ndf/skills/issue-upkeep/tests/test_issue_upkeep_layout.py` | AC17〜AC21 / AC30 / AC32 / AC37 / AC40 / AC42 / AC44 / AC49 |

## タスク分解

**判定の単位で分ける。** 各タスクで、設計の「テスト設計」が挙げたテストを先に書いて失敗を
確かめ、文書を直して通す。

### Task 1: 判定を 8 つにし、参照を新設する

- **対象:** `SKILL.md` の判定の表・用語・扱う 4 つ・自動で反映してよい変更・完了報告、`references/grouping.md`
- **満たす受け入れ条件:** AC1〜AC4 / AC8〜AC16 / AC31 / AC35 / AC36 / AC43 / AC45〜AC48
- **テスト:** `test_the_verdict_table_lists_eight_in_order` ほか、設計の「テスト設計」の AC1〜AC16・AC31〜AC37・AC43〜AC49 の行

### Task 2: 段 1 / 2A / 2B / 3 の手順を直す

- **対象:** `SKILL.md` の段 1 の経路・段 2A の確かめる点と控え・段 2B の突き合わせと注記・段 3
- **満たす受け入れ条件:** AC5 / AC6 / AC7 / AC50
- **テスト:** `test_stage_2a_records_the_shared_cause` / `test_stage_2b_matches_issues_by_shared_cause` / `test_stage_2b_note_branches_to_cluster` / `test_stage_1_picks_up_children_of_a_closed_parent`

### Task 3: 重複とマイルストーンの既存規則を直す

- **対象:** `references/no-work.md` / `references/milestones.md`
- **満たす受け入れ条件:** AC14 / AC33 / AC38〜AC42
- **テスト:** 既存の `test_no_work_has_two_necessary_conditions` を拡張、`test_milestones_use_one_word_for_the_timing` / `test_milestones_pick_the_nearest_by_sequence`

### Task 4: 4 つの Skill の境界を書く

- **対象:** `issue-upkeep` の境界の節（2 × 2 の表の正本）、`out-of-scope` / `problem-solving` / `retrospective`
- **満たす受け入れ条件:** AC25〜AC30
- **テスト:** `test_the_boundary_table_covers_both_judgements` / `test_problem_solving_separates_upstream_from_cluster` / `test_retrospective_declines_cluster_discovery` / `test_the_callers_point_here`

### Task 5: 実地で試す（承認を得てから）

- **対象:** GitHub の issue（親 issue 2 件以上の起票、子 issue の結び付け、#712 / #713 の本文）
- **満たす受け入れ条件:** AC22〜AC24 / AC34
- **進め方:** 書き込む内容を一括で提示し、承認を得てから行う。テスト駆動は当たらない（リポジトリの外の状態である）。確認の出力を Pull Request の本文へ残す

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `SKILL.md` が 500 行の上限に近づく（現在 254 行） | 条件と書き方は `references/grouping.md` へ置き、本体には判定の行と手順の分岐だけを書く |
| 判定の並びを検査する正規表現が、太字の行で割れる | 既存の `test_the_verdict_table_lists_seven_in_order` と同じ抽出を使い、8 つで照合する |
| 段 2B の注記の書き換えで、重複の判定の説明が失われる | 注記の既存の文を残し、分岐の文を足す |

実装の後の構造改善で足りる。触る範囲は文書 7 本とテスト 1 本で、テストが並びと文言を照合する。

## 切り戻し手順

文書とテストだけの変更であり、Pull Request の revert で戻る。GitHub へ書き込んだ親 issue は
閉じ、サブイシュー関係は `DELETE /repos/<所有者>/<リポジトリ>/issues/<親>/sub_issue` で外す。

## 完了の定義

- [ ] AC1〜AC50 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `python3 scripts/check-skill-frontmatter.py` / `python3 scripts/check-doc-line-limit.py --root .` / `python3 scripts/check-markdown-links.py` / `claude plugin validate .` が終了コード 0
