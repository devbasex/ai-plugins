# 引継ぎ: マイルストーン 6（02 cross-review の証拠ベース化）

**2026-09-10 時点。** 次のセッションはここから続ける。テストは 2862 件が通る。

## 起動のしかた

```
/ndf:development-workflow https://github.com/devbasex/ai-plugins/milestone/6
```

## 済んだこと

| 課題 | 状態 |
| --- | --- |
| #518 / #524 | **完了。** v10.9.1 として `main` へ配布し、リリース後テストと振り返りまで通した |
| #156 の設計（全体） | 完了（PR #531） |
| #156 の 1 本目（却下の記録の器） | 完了（PR #535） |
| #156 の 2 本目（指摘の構造化・独立発見） | 完了（PR #538） |
| #156 の 3 本目の設計 | 完了（PR #539。**10 巡で 39 件 + スイープ 5 件**） |

**配布した版は 10.9.1。** タグ `ndf--v10.9.1`（`c1e6c63`）。

## 残っていること

### 1. #156 の 3 本目（実装は完了。**PR #549 の構造改善が途中**）

**ブランチ**: `feature/issue-156-pr3-impl`（`develop` を取り込み済み、push 済み）
**計画**: `issues/issue-156-pr3-plan.md`
**設計**: `issues/issue-156-pr3-design.md` + `issues/issue-156-pr3-contracts.md`

Task の順序は **0 → 1（1 段目）→ 2 → 3 → 1（2 段目）→ 4 → 5** である。統合が 2 回
現れるのは、機械の判定が反証より前、申告の統合が反証より後に来るためである。

| Task | 中身 | 状態 |
| --- | --- | --- |
| 0 | `finding_id` の採番（`<担当>-r<ラウンド>-<索引>`） | **完了**（`cc41f0b`） |
| 1（1 段目） | `_merge_duplicates`（近傍かつ本文の一致） | **完了**（`12278da`） |
| 2 | `_verify_findings`（実行検証） | **完了** |
| 3 | `critique.sh` と `cmd_collect_critiques`（反証） | **完了** |
| 1（2 段目） | 相互 `duplicate` の統合（反証の後） | **完了** |
| 4 | `_classify_finding` と新規性の絞り込み | **完了** |
| 5 | `docs/06-evidence.md` と `docs/04-contracts.md` | **完了** |

**新しいテストは 77 件**（採番 3 / 統合 18 / 実行検証 17 / 反証 16 / 区分 23）。
構造改善のラウンドでさらに 6 件が足された。

**受け入れ条件は 14 件。** 計画の表にある。**実装はすべて完了し、テストは 2910 件が
通る。Pull Request は #549 として作成済み。**

**いま止まっているのは `cross-refactoring`（構造改善）の途中である。**

| ラウンド | 状態 |
| --- | --- |
| テスト整備 1 | 完了（5 件を適用） |
| テスト整備 2 | 完了（1 件を適用、4 件はトレーラー欠落で取り消し） |
| 構造改善 1 | **3 件中 2 件が適用済み**（`cmd_merge_fix` / `cmd_init`）。**残るのは R3-003（`cmd_report`）** |
| 構造改善 2・3 | 未着手 |

**再開のしかた。** 状態ファイルは `/tmp/ndf-worktrees/devbasex--ai-plugins/rf549/work/.cross_refactoring/`
にあり、そのまま続けられる。消えていれば `init` からやり直す（提案は残らない）。

```bash
SKILL_DIR=/home/ubuntu/.claude/plugins/cache/ai-plugins/ndf/<版>/skills/cross-refactoring
SCRIPTS="$SKILL_DIR/scripts"; LIB="$SKILL_DIR/../../scripts/lib"
TMP=/tmp/ndf-worktrees/devbasex--ai-plugins/rf549/work/.cross_refactoring
export CROSS_REFACTORING_TMP_DIR="$TMP"; WORK=/tmp/ndf-worktrees/devbasex--ai-plugins/rf549/work
# 群が尽きるまで繰り返す（merge-apply の終了コードを変数で受ける）
V=$("$SCRIPTS/refactor.py" next-apply-round 549 3) || echo "群が尽きた"
IMPL=$(echo "$V" | grep "^IMPL=" | cut -d= -f2)
"$SCRIPTS/launch-cli.sh" "$IMPL" apply 549 3
"$LIB/monitor.py" 549 --agents "$IMPL" --tmp-dir "$TMP" --stem-template "{agent}-apply-r3" --timeout 3600
out=$("$SCRIPTS/refactor.py" merge-apply 549 3); mrc=$?     # ← パイプで消さない
[ $mrc -eq 0 ] && "$SCRIPTS/refactor.py" verify-round 549 3
```

**構造改善を飛ばす判断もある。** 提案は 3 件（`cmd_merge_fix` / `cmd_init` /
`cmd_report` の長い関数の抽出）で、いずれも**この変更が触っていない既存の関数**である。
飛ばすなら `development-workflow` の `references/workflow-modes.md`「構造改善の退避先」に
従う。

その後は **実装レビュー（`cross-review`）→ 完了判定 → マージ**である。

### 2. #156 の 4 本目（効果の測定）

**ブランチ**: `design/issue-156-pr4`（設計を push 済み。**PR は未作成**）
**設計**: `issues/issue-156-pr4-design.md`（153 行）

3 本目のマージ後に PR を出す。3 本目で区分の名前が変われば追随が要る。

### 3. 確定仕様化

**1〜4 本目をまとめて 1 本にする。** 1 本目の計画（`issues/issue-156-pr1-plan.md`）へ
その判断を書いてある。4 本目が入った時点で `plan-to-spec` を通す。

### 4. 配布

**#156 の 4 本が入ってから 1 つの版で出す。** 利用者は「#518 / #524 で一度区切り、
#156 は次のまとまり」と決めている。

## この作業から起票した課題（9 件）

| 番号 | 内容 | 由来 |
| --- | --- | --- |
| #534 | 開発中のリポジトリで回すと、骨組みが指すのは配布済みの版 | PR #529 |
| #536 | 取り消された適用ラウンドの `status` が `pending` のまま残る | PR #535 |
| #537 | agy の `--print-timeout` が 600 秒固定で大きな差分が終わらない | PR #535 |
| #540 | 長い待ちの間、依存しない次の設計が止まる | issue #156 |
| #541 | マイルストーンの組み方と並行度（11 版中 9 版が 0.0%） | issue #156 |
| #542 | 設計 Pull Request のレビューが数ラウンドに分かれる | issue #156 |
| #543 | `issues/` がリンク検査を素通りし、31 件が壊れている | PR #539 |
| #544 | `.ndf/projects.json` に作る手段が無い | PR #539 |
| #545 | レビューで設計を変えても Pull Request の本文が古いまま残る | PR #539 |

**#487 へも由来を追記した**（通過工程の控えが 1 回の実行につき 1 件しか積まれない）。

## 次のセッションで踏む地雷

### agy が大きな差分で落ちる（#537）

`launch-reviewer.sh` の `PRINT_TIMEOUT=600` は固定値で、差分が大きいと agy が
`analyze` の段階で打ち切られ、結果ファイルを残さない。**`judge` が 2 度目に
`final=error` を書いて中断する。**

回避は `launch-cli.sh` を直接呼ぶ。

```bash
D=<tmp_dir>; W=$(python3 -c "import json;print(json.load(open('$D/cross-review-pr<PR>-state.json'))['worktree_path'])")
"$LIB/launch-cli.sh" agy "$W" "$D/agy-review-pr<PR>-prompt.md" "agy-review-pr<PR>" "" "$D" 1800
```

**`launch-reviewer.sh` でプロンプトを作らせてから、その pid を kill して起動し直す。**
`pkill -f "agy --prompt"` は**自分のシェルを巻き込む**（終了コード 144）。pid ファイル
（`<stem>.pid`）から読んで `kill` する。

### 進行の記録は 1 実行に 1 件（#487）

`projects-sync.sh` を 1 つの bash に並べると、控えへ積まれるのは 1 件目だけである。
番号を変数（`for n in ...`）で渡すと 1 件も積まれない。**控えへ直接積むなら
`stage-check.sh record <番号> stage "<工程名>"` を呼ぶ**（これは並べてよい）。

### `cross-refactoring` は配布済みの版で動く（#534）

開発中のリポジトリで直した `refactor.py` は使われない。母集合（`RUNTIMES`）は
状態ファイルの `runtimes` から読む。

```bash
RUNTIMES=$(python3 -c "import json;print(' '.join(json.load(open('$TMP/cross-refactoring-rf<PR>-state.json'))['runtimes']))")
```

### `merge-apply` の終了コードをパイプで消さない（#536）

`"$SCRIPTS/refactor.py" merge-apply ... | tail -2` は `tail` の値を返す。取り消し
（終了コード 2）を見落とし、同じ群を繰り返す。**変数で受けてから読む。**

### 設計 PR は長い（#542）

10 巡・39 件は例外ではない。**7 巡目で一度収束した後、設計を 1 点変えたことで
8〜10 巡目の指摘が生まれた。** 収束は「もう指摘が出ない」ことではなく、「いま出ている
指摘が尽きた」ことでしかない。

### claude が実装担当のときトレーラーが落ちる

`cross-refactoring` の適用で **claude が担当したラウンドは、コミットのトレーラー
（`Item-Id` / `Round` / `Impl-Runtime` / `Impl-Model`）が欠けて取り消される**ことが
繰り返し起きた（PR #529 と #549 で各 1 回）。取り消されると群が丸ごと落ちる。

**起票していない。** 再現の条件（claude のときだけか、プロンプトの読み落としか）を
確かめていないためである。次に見たら数えて起票する。

## 判断の記録（次のセッションで蒸し返さないもの）

- **`.ndf/cross-review.json` は作らない。** 実行してよいコマンドは `--verify-command` の
  引数で受け取る（`cross-refactoring` の `--baseline-test` と揃える。宣言ファイルには
  「作る手段」と「作る時期」が別に要り、#544 の前例がある）
- **`majority` は 3 本目の `origin_runtimes` を読む。** 位置の一致を数え直さない
- **確定仕様化は 4 本目の後に 1 本へまとめる**
- **`--max-outer-rounds` の既定（3）は変えない。** 採用 25 件がすべて適用され見送りが
  0 件だったため、上限を上げる根拠がまだ無い
