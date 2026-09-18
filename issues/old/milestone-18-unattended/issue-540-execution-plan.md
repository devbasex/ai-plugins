# #540: 実行計画（置き場所・形・見直し・閉じ方）

## 関連リンク

- 受け入れ条件: [issue-540-541-621-requirements.md](issue-540-541-621-requirements.md)（A の AC1〜AC14、AC23、AC37、AC41）
- 設計: [issue-540-541-621-design.md](issue-540-541-621-design.md)（決定 1〜6・9）
- 契約（写す形）: [issue-540-541-621-design-contracts.md](issue-540-541-621-design-contracts.md)

## モード

`standard`。設計は承認済みで、この Pull Request は設計の決定 1 の 2 本目（実行計画）を実装する。

## 目的と非目的

達成したい状態:

- 進行側が、まとまりを渡されたら最初の担当を起動する前に実行計画を書き、工程の終わりごとに読み直せる
- 依存を工程の対で見て、実装の順序の依存が次の設計を止めない
- 触る箇所の重なりを 3 区分で判定し、書き換えの重なりだけ実装を順にする

やらないこと:

- `parallel-work.md` の下限 6 と「機械が見るもの」の行 6（#621 が済ませた）
- `issue-upkeep/references/milestones.md`（#541 が書く側。この Pull Request は読む側だけ）
- `plugins/ndf/scripts/parallel-measure.py`（#621 が済ませた）
- `development-workflow/SKILL.md` の編集（工程表の行を増やさない。AC14）

## 前提

- 前提 1: `parallel-measure.py` は `develop` にあり、`capacity` / `concurrency` の出力は契約の文書のとおりである（実測で確認済み）
- 前提 2: 組の形は契約の文書が持ち、#541 のマージを待たずに読む側を書ける

## 受け入れ条件

- [ ] AC1〜AC4: `execution-plan.md` に、作る時点（複数の課題を渡されたとき、最初の担当の起動の前）・作らないとき・置き場所（`issues/execution-plan-<キー>.md`、コミットしない）・行の列・測った値の列がある
- [ ] AC5: `parallel-work.md` の下限 4 が工程の対の形になり、実装の順序の依存は設計を止めないと書かれている
- [ ] AC6: 下限 5 が重なりの目安の形になり、新しい節「重なりの目安」が 3 区分と並行の可否・待つものを持つ
- [ ] AC7: 後からマージする側が解き、解いた後の head でレビューを収束させてからマージすると書かれている
- [ ] AC8: 設計の工程どうしはどの区分でも並行してよいと書かれている
- [ ] AC9〜AC11: 見直しの契機 5 つ・見直しの手順・見直しの表（変えた理由を 1 行）がある
- [ ] AC12〜AC14: 関門の数と下限 1〜3 と機械の振り分けが変わらない。`development-workflow/SKILL.md` の差分が空である
- [ ] AC23: 組を束の初期値として読むことと、組が無いときの作り方が書かれている
- [ ] AC37: 担当を起動する前に `capacity` を実行し、出力を実行計画へ残すことが書かれている
- [ ] AC41: 閉じる手順（`concurrency`・投稿・削除）がある

## 代替案と採否

設計が決めた。この Pull Request では採否を新しく決めない。

## 修正対象

| ファイル | 変更 |
| --- | --- |
| `plugins/ndf/skills/issue-plan-strategy/references/execution-plan.md` | 新設 |
| `plugins/ndf/skills/issue-plan-strategy/SKILL.md` | 実行計画の節、Step 1 の分割計画の列、Step 5 の分担の記述 |
| `plugins/ndf/skills/issue-plan-strategy/tests/test_execution_plan_doc.py` | 新設 |
| `plugins/ndf/skills/development-workflow/references/parallel-work.md` | 下限 4・5 と「重なりの目安」の節、境界の表の行 |
| `plugins/ndf/skills/development-workflow/tests/test_approval_gates.py` | 下限の組 F8・F9 の文言 |
| `plugins/ndf/skills/development-workflow/tests/test_parallel_work_bounds.py` | 重なりの目安・競合の解き方のテストを足す |
| `issues/README.md` | `parallel-batch-<連番>/` の段落を実行計画へ置き換える |

## タスク分解

### Task 1: `parallel-work.md` の下限 4・5 と重なりの目安

- **対象ファイル:** `parallel-work.md`、`test_approval_gates.py`、`test_parallel_work_bounds.py`
- **変更内容:** 下限 4・5 の文言と理由を契約の文書から写す。「守る下限」の後ろに「重なりの目安」の節を足す。`issue-plan-strategy` との境界の表へ実行計画の行を足す
- **満たす受け入れ条件:** AC5〜AC8、AC12、AC13
- **進め方:** 先に `test_parallel_work_bounds.py` へ失敗するテストを足し、`test_approval_gates.py` の F8・F9 を新しい文言へ変えて落とし、文書を直して通す

### Task 2: `execution-plan.md` の新設

- **対象ファイル:** `references/execution-plan.md`、`tests/test_execution_plan_doc.py`
- **変更内容:** 置き場所・形（行・重なり・測った値・見直し・閉じたときの測定の表）・作る手順・見直しの契機と手順・閉じる手順・組の読み方を契約の文書から写す
- **満たす受け入れ条件:** AC1〜AC4、AC9〜AC11、AC23、AC37、AC41
- **進め方:** 先に `test_execution_plan_doc.py` を書いて落とし、文書を書いて通す

### Task 3: `SKILL.md` の配線と `issues/README.md`

- **対象ファイル:** `issue-plan-strategy/SKILL.md`、`issues/README.md`
- **変更内容:** `SKILL.md` に実行計画の節（入口と参照）を足し、Step 1 の分割計画の表を実行計画の列へ寄せ、Step 5 の「レビュー観点で分担」を重なりの目安へ置き換える。`issues/README.md` の `parallel-batch-<連番>/` の段落を実行計画の置き場所へ置き換える
- **満たす受け入れ条件:** AC1、AC3、AC6
- **進め方:** `test_execution_plan_doc.py` が `SKILL.md` の節と参照を見る

## 影響範囲

- `development-workflow` を読む担当の並行の判断が変わる（同じファイルを触っても節が別なら並行してよくなる）
- `issue-plan-strategy` を起動した進行側が実行計画を持つ
- `issues/` に `execution-plan-*.md` が現れる（コミットしない）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| `parallel-work.md` を #621 が直したばかりで、下限 6 を巻き戻す | 下限 4・5 と新しい節だけを触り、下限 6 の行と振り分けの行 6 は読み取り専用として扱う |
| `test_approval_gates.py` を G1（#561 #623）も触る | 触るのは下限の組（`LOWER_BOUNDS`）だけで、関門の節には触れない（設計の「他の束との境界」） |
| `execution-plan.md` が 500 行を超える | 契約の文書の形だけを写し、理由は設計文書へ譲る。`check-doc-line-limit.py` で見る |

## 切り戻し手順

文書とテストだけの変更で、ブランチを戻せば元へ戻る。生成物は `build-runtime-plugins.sh` で同期する。

## 完了の定義

- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `python3 scripts/check-skill-frontmatter.py` / `check-doc-line-limit.py` / `check-markdown-links.py` が終了コード 0
- [ ] `bash scripts/build-runtime-plugins.sh` の後に `bash scripts/validate-runtime-plugins.sh` が終了コード 0
- [ ] `claude plugin validate .` が終了コード 0
- [ ] `git diff --stat origin/develop -- plugins/ndf/skills/development-workflow/SKILL.md` が空（AC14）
