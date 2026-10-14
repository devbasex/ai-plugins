# 02: 修正 (Step 5) + PR ローテーション (Step 6) + 終了処理 (Step 8)

主要処理は `scripts/` 配下に切り出し済み:

| script | 役割 |
|---|---|
| (Agent) | Step 5 — 修正サブエージェント起動（メインからの責務） |
| `scripts/state.py merge-fix` | Step 5 後段 — fix 戻り値マージ + CI 分類 |
| `scripts/state.py record-fix` | Step 5 の代わり — ホストが自分で直して送った修正を確かめて記録にする |
| `scripts/state.py should-rotate` | Step 6 — rotate 要否判定 |
| `scripts/rotate-pr.sh prepare` | Step 6a — 旧 PR の素材を `rotate-pr<STATE_PR>-prepare.json` に dump |
| (Agent) | Step 6b — light モードのみ。新 PR の title/body を再生成して `rotate-pr<STATE_PR>-newtext.json` に書き出し |
| `scripts/rotate-pr.sh execute` | Step 6c — 旧 PR close + 新 PR 作成 (light は同ブランチ / squash は新ブランチ) |
| `scripts/state.py set-current-pr` | Step 6 — rotation 後の state 更新 |
| (Agent) | Step 7.5 — 最終スイープ（残った未解決の指摘の片づけ） |
| `scripts/state.py verify-sweep` | Step 7.5 後段 — 未解決の指摘が 0 件かを GitHub 側で確認 |
| `scripts/state.py report` | Step 8 — deferred nit + ラウンドサマリ + スイープ結果 |

**目次:** [Step 5: 修正 — 必ずサブエージェント経由](#step-5-修正--必ずサブエージェント経由) / [Step 6: PR ローテーション (prepare → Agent → execute の 3 ステップ)](#step-6-pr-ローテーション-prepare--agent--execute-の-3-ステップ) / [Step 7: 次ラウンドへ](#step-7-次ラウンドへ) / [Step 7.5: 最終スイープ（必須）— 取りこぼし防止](#step-75-最終スイープ必須-取りこぼし防止) / [Step 8: 終了処理 — ラウンドサマリ + 残 deferred の参考列挙](#step-8-終了処理--ラウンドサマリ--残-deferred-の参考列挙)

## Step 5: 修正 — **必ずサブエージェント経由**

**メインセッションでは修正コードを書かない。** `/ndf:fix` を
`general-purpose` サブエージェントで起動する。

**修正の手順・方針・戻り値ファイルの形は `/ndf:fix` が持つ**（`skills/fix/SKILL.md`）。駆動
（`scripts/drive.py`）は fix で止まるとき、worker へ渡す指示を `prompt_file` に書く。指示が持つのは
`/ndf:fix <PR>` の呼び出しと PR 固有の値だけである。**自動で直すのは `critical` / `major` だけで、
`minor` / `nit` は直さず見送りの返信を付けて閉じる**（振り分けの規則は `/ndf:fix` の「重要度の判定」、
基準の正本は `scripts/lib/review_criteria.py`）。

| 値 | 出どころ |
| --- | --- |
| リポジトリ・PR・ラウンド | state.json の `repo` / `current_pr` / `rounds[-1].round` |
| 作業ディレクトリ・ブランチ・ベース | state.json の `worktree_path` / `head_branch` / `base_branch` |
| 前ラウンドのレビュー | `rounds[-1]` の担当ごとの `intent` / `posted_as` / `comments` / `review_url`。件数はそのラウンドで投稿した数で、対応の対象は `/ndf:fix` が PR の未解決のスレッドと、状態ファイルの決着していない総評だけの指摘から数え直す |
| コメントのスナップショット | `$TMP_DIR/cross-review-pr<PR>-existing-comments.txt` |
| 戻り値ファイル | `$TMP_DIR/fix-pr<PR>-result.json`。環境変数 `CROSS_REVIEW_TMP_DIR` を渡すと `/ndf:fix` がここへ書く |
| 指摘の基準 | 環境変数 `CROSS_REVIEW_STATE=$TMP_DIR/cross-review-pr<STATE_PR>-state.json`。`fix-steps.py context` が状態ファイルの `review_criteria`（`init` がコピーした重点とレビューのプロファイル）を読み、担当と同じ基準で振り分ける |

**送信・返信・決着・まとめは取り込み（`state.py merge-fix`）が行う**。worker は
GitHub と git へ書かない。取り込みは現在の頭を指定して送り（`git push origin HEAD:<ブランチ名>`）、
戻り値ファイルが報告したコミットが送り先に載ったことを確かめてから、`resolved_threads` /
`deferred` / `rejected` の配列から返信と決着を、件数からまとめを組み立てて投稿キューで送る。
載っていなければ記録も投稿もせずに止まる。担当が送ると、切り離された頭ではブランチ名だけの
送信が何も送らずに終了コード 0 で終わり、送ったという報告と実物が食い違う。

**レビュー本文の指摘は `thread_id` を `null` にし、`comment_id` にはレビューの ID を書く。**
スレッドを持たない要素には返信を積まず、`summary` と理由をまとめのコメントの「レビュー本文の指摘への返事」へ載せる。

**総評だけの指摘（`review_findings[]` の `posted_to` が `body`）は、スクリプトが修正担当の雛形へ入れる。**
差分の外を指す指摘と、`HTTP 422` で総評へ移した指摘を区別しない。`fix-steps.py context` は状態ファイルの
全ラウンドから、決着の記録（`body_settlements[]`）が無く、統合済み（`merged_into`）でなく、前のラウンドで
`fixed` 以外に決着した総評だけの指摘の再提出（照合は新しい指摘の数え方と同じ位置・近傍・本文）でないものを選び、
`thread_id: null` と `finding_id` を持つ要素として雛形へ足す。`finalize` はそれらが振り分けに無ければ止まり、
戻り値ファイルの要素へ `finding_id` を写す。判定の正本は `scripts/lib/body_findings.py` にある。
投稿キューは、送り直しても届かない項目（`HTTP 400` / `404` / `410` / `422`、レビューの
投稿を除く）を飛ばして投稿キューの `dropped/` へ移し、後ろの決着とまとめを送る。飛ばした数は
`DROPPED=` / `PENDING_DROPPED=` に出る。

### Step 5 後段: fix 戻り値マージ + CI 分類

```bash
if "$SCRIPTS/state.py" merge-fix "$STATE_PR"; then
  : # exit 0 = continue
elif [ $? -eq 3 ]; then
  exit 3  # final=error（コード関連 CI 失敗 or fix 戻り値ファイル欠落）
fi
```

`state.py merge-fix` が内部で行う処理:

1. `$TMP_DIR/fix-pr<PR>-result.json` を読んで `state.rounds[-1].fix` にマージ（段階 `recorded`）
2. `deferred` を `state.deferred_nits` に追記し、`finding_id` を持つ要素の決着を `state.body_settlements[]` へ記録する（`source: fix`。同じ `finding_id` は重ねない）
3. 返信・決着・まとめを投稿する（段階 `posted`）
4. **CI 失敗の分類**（段階 `done`）:
   - code-fail（チェックジョブの名前がメタのチェック（`review_lib/ci.py` の `CI_META_PATTERNS`）に当たらない。テスト・lint・型検査・ビルドはここに入る）: `final=error` で中断 (exit 3)
   - meta-only (`check_pr_requirements` / `assignees` / `reviewers` / `labels` / `meta`): `ci_note` に記録して継続
   - 不明: 保守的に code-fail 扱い

meta-only の語は**区切りで挟まれた語として**一致したときだけ拾う。部分一致にすると
`metabase tests` や `metadata lint` のようなコードチェックまで meta-only になり、失敗した
まま収束する。

**例**: `check_pr_requirements`（Assignees 未設定）はループ継続、
lint や型検査の失敗は即中断してユーザ判断。

**同じコミットを 2 回取り込まない。** 段階は `rounds[-1].fix.merge` に書き進める。最後のラウンドの `fix.commit` と
戻り値ファイルのコミットが同じなら、`done` のときは何も書かず投稿もせずに記録した終了コード（0 / 3）で抜け、
`done` でなければ済んでいない段階（投稿・CI の分類）から続ける。投稿の途中で止まった取り込みを打ち直しても、
送信と記録はやり直さない。

**ホストが `/ndf:fix` を通さずに自分で直したとき**は、コミットを送り、返信と Resolve を済ませてから
`state.py record-fix <PR> --resolved-thread <ID>...` を打つ。コミットが PR の head から辿れることと、申告したスレッドが
解決済みであることを GitHub で確かめ、戻り値ファイルを書いて同じ取り込みを通す（送信は飛ばす）。確かめられなければ
記録を作らずに exit 5。形は [04-contracts.md](04-contracts.md) の「`record-fix`」にある。

**収束の判定（Step 3）も同じ振り分けを使う**（#327）。両方の AI が承認したラウンドは、
収束を返す前に `commits/{HEAD_OID}/check-runs` を **1 度だけ** 照会する。code-related の
失敗があれば**中断せず**終了コード 2 で修正のラウンドへ回す。決着していない `critical` / `major` の総評だけの指摘
（反証で棄却されたものを除く）が残るときも、全員が承認したラウンドを収束させず終了コード 2 で修正のラウンドへ回し、
`BODY_BLOCKING=<件数>` を出す（モデルレビューのラウンドでも `MODEL_CONFIRMED` を出さない）。`minor` / `nit` は収束を止めず、
雛形と最終スイープの残数にだけ入る。収束の直前は修正の機会が
残っている段階であり、そこで中断すると直せる失敗まで人手へ戻すことになる。照会できない
とき（`gh` の失敗 / `HTTP 422` / チェックジョブ 0 件）は収束させ、`rounds[-1].ci.verdict` へ
`unverified` と理由を残す。**進行を止めない側へ倒す。** 同名のチェックジョブは名前ごとの
最新の実行（`completed_at` と `started_at` の新しい方）へ畳んでから振り分ける。本文の編集や
再実行で別の実行が成功したチェックの、前の実行の失敗は数えない。

## Step 6: PR ローテーション (prepare → Agent → execute の 3 ステップ)

`rotate-pr.sh` は **light モード (default) と squash モード (opt-in)** を持つ。
両者ともメインからは `prepare → (light のみ Agent) → execute` の 3 ステップで呼ぶ。

```bash
if "$SCRIPTS/state.py" should-rotate "$STATE_PR"; then
  # Step 6a: 旧 PR の素材 dump (title / body / isDraft / git log / git diff --stat)
  PREPARE_VARS=$("$SCRIPTS/rotate-pr.sh" prepare "$STATE_PR") || exit $?
  eval "$PREPARE_VARS"

  # Step 6b: light モードのみ。Agent(subagent_type="general-purpose") で
  # 現状の差分・実装を反映した新 title/body を生成し、
  # $TMP_DIR/rotate-pr<STATE_PR>-newtext.json に書き出させる。
  # (squash モードでは Step 6b は不要)

  # Step 6c: 実行 (NEW_PR / NEW_PR_URL / NEW_BRANCH を取り込む)
  ROTATE_VARS=$("$SCRIPTS/rotate-pr.sh" execute "$STATE_PR" --mode "$ROTATE_MODE") || exit $?
  eval "$ROTATE_VARS"

  "$SCRIPTS/state.py" set-current-pr "$STATE_PR" "$NEW_PR" --head-branch "$NEW_BRANCH"
  # NOTE: STATE_PR は **絶対に変えない**。次ループの scripts も $STATE_PR で呼ぶ。
fi
```

`should-rotate` は `round_in_pr >= rotate_after && total_rounds < max_rounds` で
exit 0 を返す（rotate 要）。それ以外は exit 2（keep）。

### Step 6a: `rotate-pr.sh prepare <STATE_PR>`

state.json から旧 PR / worktree を解決し、以下の素材を 1 つの JSON に dump する:

```json
{
  "state_pr": 217,
  "old_pr": 217,
  "old_pr_url": "https://github.com/.../pull/217",
  "worktree_path": "/tmp/ndf-worktrees/owner--name/pr217",
  "head_branch": "feature/...",
  "base_branch": "release/...",
  "is_draft": true,
  "round_in_pr": 5,
  "old_title": "...",
  "old_body": "...",
  "git_log": "abc1234 メッセージ\n...",
  "git_diff_stat": " path/to/file | 12 +-\n ..."
}
```

ファイル: `$TMP_DIR/rotate-pr<STATE_PR>-prepare.json`。
stdout にも `OLD_PR=` / `HEAD_BRANCH=` / `BASE_BRANCH=` / `IS_DRAFT=` / `PREPARE_JSON=` を出すので
`eval` で取り込める。

### Step 6b: Agent (general-purpose) で新 title/body を生成 (light モードのみ)

メインセッションから以下のように Agent を起動する。プロンプトは
**書いて良いこと / 禁止事項** を必ず明示する
(外側の prompt フェンスは内側に ```json を含むため 4 連バッククォートで囲む):

````python
Agent(
    subagent_type="general-purpose",
    description=f"Generate light-rotation PR text for PR #{OLD_PR}",
    prompt=f"""
PR rotation の light モードで作成する新 PR の title / body を生成してください。

## 素材
- prepare.json: $TMP_DIR/rotate-pr{STATE_PR}-prepare.json
  - 元 PR の title / body / git log $BASE..HEAD / git diff --stat
- 必要なら worktree 内のファイルを直接読んで実装内容を確認してよい
  (worktree: {WORKTREE_PATH})

## 出力ファイル
$TMP_DIR/rotate-pr{STATE_PR}-newtext.json に JSON で書き出してください:

```json
{{
  "title": "新 PR の title (元 PR の title をそのままコピーしない。現状の実装を反映)",
  "body":  "新 PR の body (Markdown)。以下のセクションを含む:\\n## 何のために\\n## 何を\\n## Test plan"
}}
```

## 書いて **良い** こと
- 何のために (背景・動機) — 元 PR の背景セクションは再利用可
- 何を (変更内容) — 現在のブランチの実態を git log / git diff から反映
- Test plan — 元 PR から継承可

## 書いて **はいけない** こと (内部用語の漏洩防止)
- 「round N で〜」「cross-review で〜」「レビュー指摘で〜」
- 「(rotated)」のような automated suffix
- 「fix された問題」の列挙 / レビューサイクルの存在自体への言及
- 「旧 PR」「巻き直し」等の rotation 内部用語

PR を読む人は cross-review の存在を意識しないため、最終 PR を初めて見る読者向けに
書く。元 title / body をそのままコピーするのは **禁止** (現状の実装を反映)。
""",
)
````

> ⚠ `rotate-pr.sh` 内部から `claude` / `codex` / `agy` CLI を直接呼んで生成
> させてはならない (環境依存・コスト管理外)。**メイン側の Agent tool で行う**。

### Step 6c: `rotate-pr.sh execute <STATE_PR> --mode light|squash`

`--mode` で実際の rotation を実行する:

| mode | 振る舞い |
|---|---|
| `light` (default) | prepare.json と newtext.json を読み、**同ブランチ・同 base** で旧 PR を close → 新 PR を作成。`is_draft=true` なら新 PR も Draft で作る。title/body は newtext から流す |
| `squash` (opt-in) | `<branch>-rHHMMSS` の新ブランチを作って `git reset --soft origin/$BASE` で squash 統合 → 旧 PR close → 新 PR (`(rotated)` suffix + automated body) |

stdout には両モードとも以下を KEY=VALUE で出す:

- `NEW_PR=<number>`
- `NEW_PR_URL=<url>`
- `NEW_BRANCH=<branch>`  (light モードでは元ブランチと同じ)

`state.py set-current-pr` が `state.json` の `current_pr` / `pr_history` を更新する。
state.json の **キーは元 PR 番号 (STATE_PR) のまま** なので、light/squash どちらでも
後続スクリプトへの第 1 引数は `$STATE_PR` を渡し続ければよい。

**巻き直しの後の最初のラウンドでは、`start-round` が新しい PR の既存コメントでスナップショットを取り直す**
（#542）。

### 後方互換: 旧 1 引数形式

`rotate-pr.sh <STATE_PR>` (引数 1 つ) は `execute --mode squash` 相当として動くが、
stderr に deprecation warning を出す。新規呼び出しは必ず prepare → execute 形式へ移行。

> ⚠ **重要**: state.json のファイル名は **最初に init した PR 番号** がキー
> (`$STATE_PR`)。rotation 後も全 scripts の **第 1 引数には常に `$STATE_PR`** を渡す。
> 内部的に `state.json.current_pr` を読んで「現在の PR」を解決する設計。
> `PR=$NEW_PR` 等で shell 変数の側を切り替えると、次ループの `state.py start-round`
> が `$TMP_DIR/cross-review-pr<NEW_PR>-state.json` を探して `state.json not found` で
> 止まる。

## Step 7: 次ラウンドへ

Step 1 に戻る。

## Step 7.5: 最終スイープ（必須）— 取りこぼし防止

**目的**: ループを抜けた時点で PR 上に **未解決 (open) のレビュースレッドを 1 件も残さない**。

### なぜ必要か

ループ内の修正フェーズ (Step 5) は「一方でも REQUEST_CHANGES」のラウンドでしか走らない。
そのため以下が取りこぼされる:

- **最終 APPROVE ラウンドのインラインコメント**: 担当は指摘の基準に当たるものしか書かないが、
  bot や人のレビューが `minor` / `nit` のインラインコメントを残していることがあり、両者
  APPROVE でループを抜けるとこれらが未対応のまま残る。
- **ループ中に deferred 記録した nit**: `state.deferred_nits` に積まれたまま reply のみで
  Resolve されていないスレッド。

これらが残ったままだと、PR レビュー画面に「Unresolved」スレッドが残り、人間のレビュアーや
後続作業者が「未対応の指摘がある」と誤認する。

### 実行（メインが Agent を駆動）

ループを抜けたら（`final` がどの値でも）、**メインが `Agent(subagent_type="general-purpose")`
を起動**し、`/ndf:fix <current_pr>` を再実行させる。bash 単体では Agent を呼べないため、
while ループ脱出後にメインが以下のプロンプトでサブエージェントを起動する。

> **対象 PR**: `<current_pr>`（state.json の `current_pr`。rotation していれば最新 PR）
> **worktree**: state.json の `worktree_path`
> **結果ファイル**: `$TMP_DIR/sweep-pr<STATE_PR>-result.json`（`<STATE_PR>` は最初に
> init した PR 番号。rotation しても変えない。後段の `verify-sweep` はこの名前で探すため、
> 対象 PR が `<current_pr>` へ移っていてもファイル名は `<STATE_PR>` のままにする）
>
> ⚠ **各ラウンドの投稿数（`comments_count`）を対象の数として使わないこと。** それは
> そのラウンドで新しく投稿された件数であり、PR 上に残っている未解決の指摘の数ではない。
> **GraphQL の `reviewThreads` を `isResolved == false` で数え直して対象を決める。**
>
> PR の **全 open review thread**（インライン / レビュー body / PR レベルコメント）を
> `gh api` で洗い出し、cross-review の codex/agy が残したものを中心に **すべて解消**せよ:
> 振り分けは `/ndf:fix` の「重要度の判定」をそのまま使う（スイープ独自の重要度の規則を持たない）:
> 1. `major` 以上（指摘の基準に当たる）→ 対象のファイルを直してコミット（**送らない**）し、`resolved_threads` へ入れる。
>    **ファイルを直すのは `major` 以上が残っていたときだけである**
> 2. `minor` / `nit`（基準に当たらない）→ 対象のファイルを変えず `waived` にする。`finalize` が見送りの返信と
>    `"resolve": true` を付けて `deferred` へ入れるので、そのまま結果ファイルへ写す
> 3. 判断保留（`deferred`）と bot 誤指摘（`rejected`）→ 理由を添えて入れ、**`"resolve": true`** を付ける
>    （スレッドを open のまま残さない）
>
> `fix-steps.py context` の雛形に入る総評だけの指摘（`finding_id` を持つ要素。スレッドを持たない）も同じ規則で
> 振り分け、`finalize` が写した `finding_id` ごと結果ファイルの `resolved_threads` / `deferred` / `rejected` へ入れる。
>
> **GitHub と git へ書かない。** 返信・決着・送信は、メインがこの結果ファイルを読んで
> 共通ライブラリの 1 行（`result_posts.py fix`）で行う。
>
> **修正をコミットした場合は、対象リポジトリの検証を 1 度通すこと。** 何を実行するかは
> 対象リポジトリを見て決める。**コマンドを推測して組み立てない。**
>
> 対象リポジトリに `.ndf/project.json` の `test` があれば、駆動（`drive.py`）がプロンプトに宣言の全体テスト
> （テストと静的解析の両方の suite の `command`）を並べる。そのときは並んだコマンドを順に実行し、下の探し方は使わない。
> 起動の引数 `--verify-command`（実行検証 Step 2.5 の許可の一覧）は、ここでは使わない。宣言が無いときだけ次の手順で探す。
>
> 1. 実行手段を探す。`Makefile` の `test` / `lint` / `check` ターゲット、`package.json` の
>    `scripts`、`pyproject.toml` の `[tool.pytest.ini_options]`、`composer.json` の
>    `scripts`、継続的統合の定義（`.github/workflows/*.yml`）が実行しているコマンド。
>    **順序ではなく一覧である。** どれが最も強い検証かはリポジトリで変わるため、
>    上から順に 1 つ見つけて打ち切らない
> 2. 見つかったもののうち、**変更したファイルに掛かるもの**を実行する
> 3. 1 つも見つからないときは実行しない
>
> **終了コードが 0 でない実行を残したまま完了としない。** その修正が原因なら直して
> コミットし直し、もう一度実行して 0 を確かめる。修正の前から落ちていたなら直さず、
> 何が落ちているかを最終メッセージへ書く（この工程の範囲外である）。どちらの場合も
> `commands` には**最後に実行した結果**を残す。
>
> 実行したコマンドと終了コード、実行しなかった場合はその理由を、結果ファイルの
> `verification` へ書く。0 でない終了コードが残るときは、直せなかった理由も最終
> メッセージへ書く。
>
> 完了後、上の**結果ファイル**（`$TMP_DIR/sweep-pr<STATE_PR>-result.json`）に
> `{"resolved": N, "fixed_in_sweep": M, "commit": "<SHA|null>", "fix_commit": "<SHA|null>",
>   "resolved_threads": [...], "deferred": [...], "rejected": [...], "remaining_open": K,
>   "remaining_reason": "<K>0 のときの理由|null>", "items": ["<1行要約>", ...],
>   "verification": {"commands": [{"command": "<実行したコマンド>", "exit": <終了コード>}],
>                    "skipped_reason": "<実行しなかった理由|null>"}}` を
> 書き出し、最終メッセージで内訳を日本語報告せよ。
> 検証を実行したときは `commands` に実行順で並べ、`skipped_reason` を `null` にする。
> 実行手段が見つからなかったときは `commands` を空にし、`skipped_reason` に**何を探して
> 見つからなかったか**を書く。コミットしなかったときも `commands` を空にし、
> `skipped_reason` に「コミットなし」と書く。
> **`remaining_open` は 0 とする。** 0 にできない場合は `remaining_reason` に理由を書く。
> この値は申告であり、次の `verify-sweep` が GitHub 側の実数と突き合わせる。

**`verification` は突き合わせる相手を持たない。** `verify-sweep` が結果ファイルから
取り出すのは `remaining_open` / `remaining_reason` / `resolved` / `fixed_in_sweep` /
`commit` の 5 つと、決着を記録する `resolved_threads` / `deferred` / `rejected` だけで、この項目は読まない。記録は結果ファイルとサブエージェントの
最終メッセージに残り、メインが Step 8 の完了報告へ書き写す。

### Step 7.5 後段: 最終スイープの結果を検証する（必須）

申告のまま完了報告へ進むと、未解決の指摘が残ったまま「0 件」と報告される。
GitHub 側の実数で数え直してから Step 8 へ進む。

```bash
if "$SCRIPTS/state.py" verify-sweep "$STATE_PR"; then
  : # exit 0 = 未解決の指摘なし
else
  RC=$?
  # exit 6 = 残っている。件数と理由を完了報告に含めて続行する。
  # それ以外は検証そのものの失敗なので、報告へ進まずここで止める。
  [ "$RC" -eq 6 ] || exit "$RC"
fi
```

`|| [ $? -eq 6 ]` の形では書かない。この手順の bash には `set -e` が無いため、
結果ファイルの不在や JSON の不正で `verify-sweep` が exit 1 を返しても、その行が
失敗のステータスを返すだけで次の `report` が実行される。最終検証を通していない
まま完了報告へ進むことになる。

| 申告 | GitHub 側 | 記録する残件数 | exit |
|---|---|---|---|
| 0 件 | 0 件 | 0 | 0 |
| 0 件 | 2 件 | 2 | 6 |
| 1 件 | 1 件 | 1 | 6（件数と理由を完了報告へ） |
| 0 件 | 取得できない | 0（申告のまま） | 0（確認できなかったことを stderr へ） |

**総評だけの指摘は残件数に足す。** `verify-sweep` は結果ファイルの `finding_id` を持つ要素の決着を
`body_settlements[]` へ記録し（`source: sweep`）、それでも決着していない総評だけの指摘の件数を、
未解決のスレッドの件数（取得できなければ申告）へ足して `REMAINING_OPEN` にする。1 件以上なら
`REMAINING_BODY=<件数>` を出し、`sweep.remaining_body` に件数を、`remaining_reason` が空なら
「決着していない総評だけの指摘 N 件」を残す。駆動のまとめの `unresolved` はこの `remaining_open` である。

結果は state.json の `sweep` に残り、`state.py report` が完了報告へ折り込む。

> ⚠ 最終スイープは「修正の追加」ではなく **後始末**。新しい設計変更や大きな
> リファクタは行わない（行う必要があれば deferred として report に残す）。
> push が走った場合でも、それに対する再レビューはループ終了後のため行わない
> （次回 cross-review か通常レビューに委ねる）。

### 再開性

sweep 中にメインが落ちても、`sweep-pr<STATE_PR>-result.json` が無ければ Step 7.5 から
再実行すれば良い（Resolve は冪等。既 Resolve スレッドは skip される）。

## Step 8: 終了処理 — ラウンドサマリ + 残 deferred の参考列挙

最終スイープ (Step 7.5) 完了後、ラウンドサマリを表示:

```bash
"$SCRIPTS/state.py" report "$STATE_PR"
```

`report` は以下を Markdown で吐く:

- 最終ステータス（`approved` / `max_rounds` / `oscillation` / `error`）
- PR 履歴
- ラウンドサマリ表
- 基準外の見送りの件数（`waived`。返信を付けて閉じたもので、残 deferred の一覧には入れない）
- 残 deferred nit 一覧
- レビューの重点の宣言が読めなかったときは、その理由（基準 1・2・4 だけで続けた）

`verify-sweep` を通していれば、`report` の出力に「## 最終スイープ」の節が入り、
GitHub 側で数え直した残件数と、0 件にできなかった場合の理由が含まれる。
メインはこれに `sweep-pr<STATE_PR>-result.json` の `resolved` / `fixed_in_sweep` /
`verification`（実行した検証コマンドと終了コード、実行しなかった場合はその理由）を
添えて最終報告する。

> **Step 7.5 で全 open thread を Resolve する**ため、Step 8 のユーザ問い合わせは原則不要。
> deferred は「対応見送りの記録」として report に **参考列挙**するに留める（再対応が要るものが
> あればユーザがその場で指示できる）。`remaining_open > 0` の場合のみ、残った理由を添えて報告する。

## レビューの宣言（`.ndf/review.json`）

PR の head の worktree の `.ndf/review.json` を、`init`（新規は worktree を用意した後、再開は同期した後）が読む。
読み取りと検査の正本は `scripts/lib/review_criteria.py`（`load_review_decl`）で、`/ndf:fix` も同じ部品で読む。

| 鍵 | 型 | 規則 |
| --- | --- | --- |
| `version` | 数 | `1`・`2`・`3`。`1` は `focus` だけを読み、ほかの鍵を無視する。`2` はプロファイルを `profile` に 1 つ、`3` は `profiles` に名前つきで複数書く。ほかの値は宣言全体が読めない（重点を `unreadable`、プロファイルなし） |
| `focus` | 文字列の配列 | 重点の名前（基準 3）。`version: 1` では必須、`version: 2`・`3` では任意で、`3` ではどのプロファイルを選んでも効く。形が違えば重点だけが `unreadable` |
| `profile` | オブジェクト | `version: 2` だけで読むレビューのプロファイル。無いか `{}` ならプロファイルなし。下の鍵のどれか 1 つでも形が違う・知らない鍵があれば、プロファイル全体を捨てて既定の基準で続け、理由を標準エラーと `report` に出す（重点はそのまま効く） |
| `profile.criteria` | オブジェクト（番号の文字列 → 文） | 1 件以上。既定の基準 1・4 と置き換わる。番号は 1 以上の整数で、`2`（外せない基準: 秘密・認証認可・利用者のデータ・戻せない操作）と `3`（重点）は使えない。基準 2 と、重点があるときの基準 3 はそのまま残る |
| `profile.not_written` | 文字列の配列 | 「書かないもの」。`[]` なら行を出さない |
| `profile.waive_kinds` | オブジェクト（種類 → 名前） | 1 件以上。種類は英小文字・数字・`_`。既定の見送りの種類と置き換わる |
| `profile.reply` | 文字列 | 見送りの返信の雛形。`{inner}` をちょうど 1 回含み、そこへ見送りの種類の名前が入る。重点の句は足さない |
| `profile.categories` | オブジェクト（名前 → `{paths, instructions}`） | `paths` は glob の配列で、`.ndf/pace.json` の `areas` と同じ規則（`*` と `?` は `/` をまたがず、`**` はまたぐ）。当たった分類の名前が `auto_review_categories` に、`instructions` が自動レビュー観点に入る |
| `profile.replace_auto_categories` | 真偽値 | 真なら既定の自動カテゴリの観点を渡さない。既定は偽 |
| `profiles` | 配列 | `version: 3` だけで読む、名前つきのプロファイルの列。無いか `[]` ならどれも選ばない。`version: 3` で `profile` を書くと壊れている |
| `profiles[].name` | 文字列 | 必須。英小文字・数字・`_`。宣言の中で重複しない |
| `profiles[].paths` | 文字列の配列 | 任意。空でない glob の配列で、`profile.categories` の `paths` と同じ規則。書かないプロファイルはどの PR にも当たり、高々 1 つを末尾に置く |
| `profiles[]` のほかの鍵 | — | `profile` の鍵（`criteria`・`not_written`・`waive_kinds`・`reply`・`categories`・`replace_auto_categories`）と同じ形と規則。知らない鍵があれば壊れている |
| `criteria`（最上位） | オブジェクト（番号の文字列 → 文） | `version: 3` だけで読む、宣言の側で外せない基準。任意。形は `profile.criteria` と同じ（`2` と `3` は使えない）。選んだプロファイルの基準（選ばなかったとき・`criteria` を書かないプロファイルでは既定の 1・4）に足す。番号が、どれかのプロファイルの `criteria` か、既定の 1・4 が効きうるとき（`criteria` を書かないプロファイルがある・`paths` を持たないプロファイルが無い）のその番号と重なれば壊れている |
| `not_written`（最上位） | 文字列の配列 | `version: 3` だけで読む。任意。選んだプロファイルの書かないもの（書かなければ既定）の前に足し、同じ文は 1 つにする |

状態ファイルの `review_criteria` には、プロファイルがあるときだけ `profile_status`（`declared` / `unreadable`）を置き、
`declared` では `profile`（検査を通ったプロファイルの写し。宣言の `profile` と同じ形）を、`unreadable` では
`profile_error`（理由）を持つ。プロファイルが無ければこの 3 項目を置かない。`init` の出力の
`REVIEW_PROFILE=<declared|none|unreadable>` に同じ値が出る。`fix-steps.py context` は `profile` を振り分けの JSON の
`review_profile` へ写し、`finalize` はその写しで振り分けを検査して見送りの返信を組む。

`version: 3` では、`init` が PR の変更ファイル（`changed_files` のパス。名前を変えたファイルは前の名前も含む）で
プロファイルを 1 つ選ぶ。`profiles` の先頭から見て、変更ファイルのどれかに `paths` が当たる最初のプロファイルを選び
（`review_criteria.select_profile`）、選んだものだけが上の `profile` と同じに効く。2 つ以上のプロファイルに当たる PR でも
止めず、先に書いた方を選び、ほかに当たった名前を `⚠️ 変更ファイルが複数のプロファイル（…）に当たるため、先に書いた <名前> を使う`
で知らせる。どれにも当たらなければプロファイルなしと同じ既定で続く。`profiles` のどれか 1 つでも形が違えば、`profiles` を
全部捨てて既定で続け（`profile_status: unreadable`）、理由の頭に `profiles[<番号>]（<名前>）:` を付けて標準エラーと `report` に出す。
最上位の `criteria`・`not_written` が壊れている（番号の重なりを含む）ときも同じく、`profiles` と最上位の基準を全部捨てる。
選んだ名前は状態ファイルの `review_criteria.profile_name` と `init` の出力の `REVIEW_PROFILE_NAME=<名前>` に出る。
`profile_name` は `version: 3` のときだけ置き、選ばなかった・壊れていたときは空文字。`REVIEW_PROFILE_NAME=` は
`version: 3` で選んだときだけ値を持ち、ほかは空。ほかのプロファイルにも当たったときだけ、その名前を
`review_criteria.profile_others`（宣言の順の配列）に置く。名前と `paths` だけを書いたプロファイルを選ぶと、名前だけが残り、
プロファイルの項目は既定のまま（`profile_status` を置かない）。

最上位の `criteria`・`not_written` を書いた宣言では、選んだプロファイル（選ばなかったときは既定の組）にそれを足した組が
効き、状態ファイルの `profile` と振り分けの JSON の `review_profile` にも足した組が写る（`profile_status: declared`）。
どの PR にも効かせたい基準は最上位に 1 か所だけ書き、プロファイルごとに写さない。

`report` の要約は、`profile_name` があるときだけ `## レビューのプロファイル: <名前>`（空文字なら `既定`）の行を出し、
`profile_others` があれば同じ行に `（ほかに <名前>, … にも当たった。先に書いた <名前> を使った）` を足す。

状態ファイルが無いまま `/ndf:fix` を単独で使うときは、`fix-steps.py context` が宣言を読み、`version: 3` のときだけ
`gh api repos/<repo>/pulls/<PR>/files --paginate` で変更ファイルを取って同じ規則で選ぶ。取れなければ理由を標準エラーへ出し、
変更ファイルなしで選ぶ（`paths` を持たないプロファイルか、既定）。`review_criteria.py reviewer|fixer` は
`--changed-file <パス>`（繰り返し可）で選ぶ。

書いた鍵だけが既定を差し替え、書かない鍵は既定のまま。プロファイルがあると、修正担当への節の見送りの句は
「対象のファイルを変えずに」になる。収束の判定・投稿の形・ラウンドの段・`review_kind` はプロファイルで変わらない。

```json
{
  "version": 2,
  "profile": {
    "criteria": {"1": "設定（人物・世界観・年表）と本文が食い違う", "4": "文が日本語として不自然で読み手が引っかかる", "5": "人物の口調・呼び方が設定とずれる"},
    "not_written": ["好みの文体", "設定に無い新しい案"],
    "waive_kinds": {"preference": "好みの文体", "out_of_scope": "設定を変える提案"},
    "reply": "直しません。{inner}に当たるためです。",
    "categories": {"manuscript": {"paths": ["works/*/chapters/*/text.md"], "instructions": "## 自動追加レビュー観点: 原稿\n- ..."}},
    "replace_auto_categories": true
  }
}
```

`version: 3` の例（原稿のパスを 1 つでも変えた PR は `manuscript`、ほかの PR はすべて `system`。基準 9 はどちらにも効く）:

```json
{
  "version": 3,
  "criteria": {"9": "取得したデータとその分析の中身を、公開の置き場や外部の API へ出す"},
  "profiles": [
    {
      "name": "manuscript",
      "paths": ["works/*/chapters/*/text.md"],
      "criteria": {"5": "人物の口調・呼び方が設定とずれる"},
      "waive_kinds": {"taste": "好みの文体"},
      "reply": "直しません。章を読んで困る所ではないためです（{inner}）。"
    },
    {
      "name": "system",
      "criteria": {"1": "利用者が普通に使う経路で、誤動作する・止まる・データを壊す"},
      "waive_kinds": {"wording": "字句や言い回しの修正"},
      "reply": "直しません。この指摘は、使って困る不具合ではないためです（{inner}）。"
    }
  ]
}
```

再開の前に宣言を書き換えると、`init` が保存した `changed_files` からプロファイルを選び直し、観点と指摘の基準を組み直して、
保存した値と違うときだけ状態ファイルを書き直す。再開では変更ファイルを GitHub から取り直さない。次のラウンドから新しい宣言が効く。
