# #424: gate が実行証跡を見て、承認の関門を 2 つへ集約する

## 関連リンク

- 課題: [#424](https://github.com/devbasex/ai-plugins/issues/424)
- 要求と受け入れ条件: [issue-418-417-workflow-gate/01-requirements.md](issue-418-417-workflow-gate/01-requirements.md) の C1〜C13 / E1〜E11 / F1〜F11
- 設計: [issue-418-417-workflow-gate/02-design.md](issue-418-417-workflow-gate/02-design.md) の決定 3・4・7・8・10・11・12
- 引継ぎ: [handoff-v10.5.1.md](handoff-v10.5.1.md) の本 3

## モード

`standard`。tool 実行前の hook の振る舞いと、宣言ファイルの読み取りを足す。
対象にはテストがある。

## 目的と非目的

達成したい状態:

- **判定したモードと、実際に通った工程の食い違いが機械で見える。** Pull Request を
  作る時点で、記録の無い必須の工程が案内に出る
- **人手の承認が 2 つの関門へ集まる。** 設計 Pull Request のマージと、本番のチャネルへ
  届く操作である
- **並行開発の方法を縛らず、下限を定める。** 手法は担当の判断に任せる

やらないこと:

- **拒否の範囲を広げること。** 案内にとどめる（決定 4）。拒否は設計 Pull Request の
  マージだけ
- モードの判定基準そのもの
- `release` の本番への配布の手順そのもの。要否の規則は `release` が持ったままにする
- `gh api .../pulls`（REST）で Pull Request を作る経路の観測。`gh pr create` に限る

## 前提

- 前提 1: 工程表で `Pull Request` より前にある必須の工程が検査の対象になる。
  **「レビュー」だけは除く**（`cross-review` は Pull Request が無いと回せない。決定 7）
- 前提 2: `closing-issues.sh` は shebang を持ち標準入力を読む実行スクリプトである。
  どちらの呼び出し元も source せず `bash` の副プロセスとして起動する（決定 3）
- 前提 3: このリポジトリの `base_branch` は `develop` で、本番のチャネル（`main`）とは
  別のブランチである。**流用しない**（決定 10）

## 受け入れ条件

`01-requirements.md` の C1〜C13 / E1〜E11 / F1〜F11 をそのまま採る。

### C. gate がモードごとの実行証跡を見る

- [ ] C1: `wf_is_candidate` が `gh pr create` を通す
- [ ] C2: 本文の閉じる語からリポジトリと番号の組を取り出す。取れなければ何も出さない
- [ ] C3: モードの記録が無いとき、案内へその旨を出す
- [ ] C4: `Pull Request` より前の必須の工程（「レビュー」を除く）の欠落を出す
- [ ] C5: どの場合も拒否しない（`permissionDecision` を出さない）
- [ ] C6: `gh` / `jq` が無い、番号を取れない、控えが読めないときは何も出さずに通す
- [ ] C7: 判定が 10 秒以内に終わる
- [ ] C8: `standard` を記録した課題を閉じる `gh pr create` で、欠落が案内に出る
- [ ] C9: 課題ごとにモードが食い違うとき、最も高いモードを取り、食い違いも案内へ出す
- [ ] C10: 別のリポジトリを指す閉じる語では、そのリポジトリの控えを見る
- [ ] C11: `light` を検査の対象から外さない
- [ ] C12: 控えの記録が 0 件であること自体を案内の対象にする
- [ ] C13: 案内の文面が、記録の無いことを工程を通していないことと断定しない

### E. 承認の関門を 2 つへ集約する

- [ ] E1: 関門が 2 つだけであることが書かれている
- [ ] E2: 設計 Pull Request のマージはマージ先のチャネルによらず承認が要る
- [ ] E2-b: 本番のチャネルへ届く操作の要否はマージ先のチャネルが決める。`merged` と
      `pr` から `release` の規則へ辿れる
- [ ] E3: `.ndf/worktree.json` へ `production_branch` を新設し、名前と読み取りの順序が
      `SKILL.md` に書かれている。`base_branch` は流用しない
- [ ] E4: 宣言が無いときは既定ブランチを本番のチャネルとして扱う
- [ ] E5: 並行して走った Pull Request は関門でまとめて 1 回の承認へ載せる
- [ ] E6: 対象の Pull Request の URL を全て、生の URL のまま明示する
- [ ] E7: 複数あるときの並べ方が `approval-request.md` にある
- [ ] E8: 依存の順序があるときにマージの順序も示すことが同じ参照にある
- [ ] E9: `/goal` の止まり方が 2 つの関門に合わせて書かれている
- [ ] E10: 分割・束ね方・並行度は担当の判断に任せると書かれている
- [ ] E11: 複数のマイルストーンは原則として順次にすると書かれている

### F. 並行開発に対応し、下限を定める

- [ ] F1: 複数の課題を渡されうることを前提として書いている
- [ ] F2: 4 つの形（1:1 / 1:N / N:1 / N:M）が挙げられている
- [ ] F3: `issue-plan-strategy` との境界が書かれている
- [ ] F4: 工程表の 15 行が、課題・Pull Request・まとまりのどの単位で動くかが読める
- [ ] F5: モードの違う課題を 1 本の Pull Request へ混ぜない
- [ ] F6: Pull Request ごとにモードを判定して記録する
- [ ] F7: 判定したモードの必須の工程を飛ばさない。飛ばすなら理由を本文へ残す
- [ ] F8: 依存の順序があるものを並行させない
- [ ] F9: 同じファイルを触るものを並行させない
- [ ] F10: 並行の本数は収束レビューが回る範囲に抑える
- [ ] F11: F5〜F10 のうち機械で見られるものが gate へ載り、見られないものは手順として
      書かれている。振り分けが読める

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 閉じる語の読み取りを共通層へ**移す** | 採用 | 写しは二重管理になる（決定 3） |
| B | 同じ規則を写して 2 箇所に持つ | 不採用 | 直し忘れた側が次の変更まで残る |
| C | 検査の終点を `_wf_frontier` に置く | 不採用 | Pull Request を作る時点では常に手前で、欠落が 1 件も出ない（決定 7） |
| D | 実装 Pull Request のマージを 3 つ目の関門にする | 不採用 | マージ先を見ずに一律で止めると検証への反映まで止まる（決定 10） |
| E | `base_branch` を本番のチャネルとして流用する | 不採用 | 開発版のチャネルへのマージが本番の扱いになる |

## ドメイン用語

| 用語 | この文書での意味 |
| --- | --- |
| 関門 | 人手の承認を求めて工程を止める場所 |
| 本番のチャネル | 配布した版が常用する利用者へ届くブランチ |
| 証跡 | 通過工程の控えに残った記録 |

## 不変条件

- **gate は Pull Request の作成を拒否しない。** 案内だけを出す
- **判定できないときは何も出さない。** 記録の側が遅れているだけの状態で作成が
  止まってはいけない（マージの拒否とは倒し方が逆である）

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `closing-issues.sh` の呼び出し | 置き場所が変わる | `merged/SKILL.md` の `CLOSING=` を向け直す。**写しは残さない** |
| `.ndf/worktree.json` | `production_branch` を足す | 既存の鍵の意味は変えない。宣言が無ければ既定ブランチ |
| 控えの形と CLI の引数 | 変えない | 変えない |

## 修正対象

- `plugins/ndf/scripts/lib/closing-issues.sh`（`merged/scripts/` から移設）
- `plugins/ndf/skills/merged/SKILL.md` / `plugins/ndf/skills/merged/tests/`
- `plugins/ndf/skills/development-workflow/scripts/lib/workflow-common.sh`
- `plugins/ndf/skills/development-workflow/scripts/workflow-guard.sh`
- `plugins/ndf/skills/development-workflow/SKILL.md`
- `plugins/ndf/skills/development-workflow/references/approval-request.md`
- `plugins/ndf/skills/development-workflow/references/parallel-work.md`（新設）
- `plugins/ndf/skills/development-workflow/references/stage-completeness.md`
- `plugins/ndf/skills/pr/SKILL.md`
- `plugins/ndf/scripts/lib/worktree-common.sh`（`wt_production_branch`）
- `plugins/ndf/skills/worktree/schemas/worktree.schema.json`
- `.ndf/worktree.json`
- 各テスト

## タスク分解

### Task 1: 閉じる語の読み取りを共通層へ移す

- **対象ファイル:** `plugins/ndf/scripts/lib/closing-issues.sh`（移設）/
  `plugins/ndf/skills/merged/SKILL.md` / `plugins/ndf/skills/merged/tests/`
- **変更内容:** `git mv` で移し、`merged/SKILL.md` の `CLOSING=` を
  `$SCRIPTS/lib/closing-issues.sh` へ向け直す。直前の説明も書き換える
- **満たす受け入れ条件:** C2 の土台（決定 3）
- **進め方:** 移設先から同じ結果が出ることをテストで固定してから移す

### Task 2: `gh pr create` を観測して証跡を見る

- **対象ファイル:** `workflow-common.sh`（`wf_is_candidate` / `wf_parse_pr_create` /
  `wf_evidence_report`）/ `workflow-guard.sh`
- **変更内容:** 走査の絞り込みへ `pr[[:space:]]+create` を足し、本文からリポジトリと
  番号の組を取り、モードと必須の工程の欠落を 1 つの案内へまとめる
- **満たす受け入れ条件:** C1〜C13
- **進め方:** 条件ごとに先にテストを書く。**分岐は既存の 3 つの後ろへ置く**（前に
  置くと、進行の記録が Pull Request の作成と誤って一致したときに記録が積まれない）

### Task 3: 本番のチャネルを宣言できるようにする

- **対象ファイル:** `worktree-common.sh` / `worktree.schema.json` / `.ndf/worktree.json`
- **変更内容:** `production_branch` を読む `wt_production_branch` を足す。宣言が無ければ
  既定ブランチ。**`base_branch` は読まない**
- **満たす受け入れ条件:** E3 / E4
- **進め方:** 宣言あり・無し・`base_branch` だけがある場合の 3 つを先にテストで固定する

### Task 4: 関門を 2 つと定め、提示物を複数へ対応させる

- **対象ファイル:** `development-workflow/SKILL.md` /
  `references/approval-request.md` / `plugins/ndf/skills/pr/SKILL.md` /
  `plugins/ndf/skills/merged/SKILL.md`
- **変更内容:** 関門を 2 つと定め、要否の決まり方の違いを**関門ごとに節を分けて**書く。
  複数の Pull Request の並べ方・生の URL・依存の順序を `approval-request.md` へ足す。
  `merged` と `pr` から `release` の規則への導線を置く
- **満たす受け入れ条件:** E1 / E2 / E2-b / E5〜E9
- **進め方:** 記述の有無をテストで固定する

### Task 5: 並行開発の参照を置く

- **対象ファイル:** `references/parallel-work.md`（新設）/ `SKILL.md` の参照
- **変更内容:** 4 つの形・工程の単位・下限 6 つ・`issue-plan-strategy` との境界・
  機械と手順の振り分けを置く
- **満たす受け入れ条件:** E10 / E11 / F1〜F11
- **進め方:** 記述の有無をテストで固定する

## 影響範囲

- `gh pr create` を実行するすべての場面（案内が 1 つ増える）
- `merged` の閉じ忘れの回収（読み込み先が変わる）
- 作業ツリー運用の宣言ファイル（鍵が 1 つ増える）

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 走査の絞り込みを広げた分、hook の費用が増える | `wf_is_candidate` は `grep` 1 回で、語の分割は当たったときだけ行う。C7 で 10 秒以内を測る |
| 案内が毎回出て読まれなくなる | 「レビュー」を検査の対象から外す（決定 7）。欠落が無ければ何も出さない |
| 移設で `merged` の経路が切れる | 移設先からの実行をテストで固定してから移す |

## 切り戻し手順

- Pull Request 単位で戻せる。データの移行は無い。控えの形も変わらない

## 完了の定義

- [ ] C1〜C13 / E1〜E11 / F1〜F11 に、テストか実行の証跡が対応している
- [ ] 検査 10 本とテストが終了コード 0
- [ ] `cross-review` が収束している（未解決スレッド 0 件）
