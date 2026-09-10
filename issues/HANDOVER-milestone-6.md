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

### 1. #156 の 3 本目の実装（着手済み）

**ブランチ**: `feature/issue-156-pr3-impl`（`develop` を取り込み済み、push 済み）
**計画**: `issues/issue-156-pr3-plan.md`
**設計**: `issues/issue-156-pr3-design.md` + `issues/issue-156-pr3-contracts.md`

Task の順序は **0 → 1（1 段目）→ 2 → 3 → 1（2 段目）→ 4 → 5** である。統合が 2 回
現れるのは、機械の判定が反証より前、申告の統合が反証より後に来るためである。

| Task | 中身 | 状態 |
| --- | --- | --- |
| 0 | `finding_id` の採番（`<担当>-r<ラウンド>-<索引>`） | **完了**（`cc41f0b`） |
| 1（1 段目） | `_merge_duplicates`（近傍かつ本文の一致） | **完了**（`12278da`） |
| 2 | `_verify_findings`（実行検証） | **完了**（`026916d`） |
| 3 | `critique.sh` と `cmd_collect_critiques`（反証） | **完了** |
| 1（2 段目） | 相互 `duplicate` の統合（反証の後） | **完了** |
| 4 | `_classify_finding` と新規性の絞り込み | **完了** |
| 5 | `docs/06-evidence.md` と `docs/04-contracts.md` | **完了** |

**受け入れ条件は 14 件。** 計画の表にある。**実装はすべて完了し、テストは
2910 件が通る。** 残るのは Pull Request の作成とレビューである。

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

## 判断の記録（次のセッションで蒸し返さないもの）

- **`.ndf/cross-review.json` は作らない。** 実行してよいコマンドは `--verify-command` の
  引数で受け取る（`cross-refactoring` の `--baseline-test` と揃える。宣言ファイルには
  「作る手段」と「作る時期」が別に要り、#544 の前例がある）
- **`majority` は 3 本目の `origin_runtimes` を読む。** 位置の一致を数え直さない
- **確定仕様化は 4 本目の後に 1 本へまとめる**
- **`--max-outer-rounds` の既定（3）は変えない。** 採用 25 件がすべて適用され見送りが
  0 件だったため、上限を上げる根拠がまだ無い
