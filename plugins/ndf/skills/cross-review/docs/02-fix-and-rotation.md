# 02: 修正 (Step 5) + PR ローテーション (Step 6) + 終了処理 (Step 8)

主要処理は `scripts/` 配下に切り出し済み:

| script | 役割 |
|---|---|
| (Agent) | Step 5 — 修正サブエージェント起動（メインからの責務） |
| `scripts/state.py merge-fix` | Step 5 後段 — fix 戻り値マージ + CI 分類 |
| `scripts/state.py should-rotate` | Step 6 — rotate 要否判定 |
| `scripts/rotate-pr.sh prepare` | Step 6a — 旧 PR の素材を `rotate-pr<STATE_PR>-prepare.json` に dump |
| (Agent) | Step 6b — light モードのみ。新 PR の title/body を再生成して `rotate-pr<STATE_PR>-newtext.json` に書き出し |
| `scripts/rotate-pr.sh execute` | Step 6c — 旧 PR close + 新 PR 作成 (light は同ブランチ / squash は新ブランチ) |
| `scripts/state.py set-current-pr` | Step 6 — rotation 後の state 更新 |
| (Agent) | Step 7.5 — 最終スイープ（残った未解決の指摘の片づけ） |
| `scripts/state.py verify-sweep` | Step 7.5 後段 — 未解決の指摘が 0 件かを GitHub 側で確認 |
| `scripts/state.py report` | Step 8 — deferred nit + ラウンドサマリ + スイープ結果 |

## Step 5: 修正 — **必ずサブエージェント経由**

**メインセッションでは修正コードを書かない。** `/ndf:fix` を
`general-purpose` サブエージェントで起動する。

**修正の手順・方針・戻り値ファイルの形は `/ndf:fix` が持つ**（`skills/fix/SKILL.md`）。駆動
（`scripts/drive.py`）は fix で止まるとき、worker へ渡す指示を `prompt_file` に書く。指示が持つのは
`/ndf:fix <PR> --defer-nit` の呼び出しと PR 固有の値だけである。

| 値 | 出どころ |
| --- | --- |
| リポジトリ・PR・ラウンド | state.json の `repo` / `current_pr` / `rounds[-1].round` |
| 作業ディレクトリ・ブランチ・ベース | state.json の `worktree_path` / `head_branch` / `base_branch` |
| 前ラウンドのレビュー | `rounds[-1]` の担当ごとの `intent` / `posted_as` / `comments` / `review_url`。件数はそのラウンドで投稿した数で、対応の対象は `/ndf:fix` が PR の未解決のスレッドから数え直す |
| コメントのスナップショット | `$TMP_DIR/cross-review-pr<PR>-existing-comments.txt` |
| 戻り値ファイル | `$TMP_DIR/fix-pr<PR>-result.json`。環境変数 `CROSS_REVIEW_TMP_DIR` を渡すと `/ndf:fix` がここへ書く |

**送信・返信・決着・まとめは取り込み（`state.py merge-fix`）が行う**。worker は
GitHub と git へ書かない。取り込みは現在の頭を指定して送り（`git push origin HEAD:<ブランチ名>`）、
戻り値ファイルが報告したコミットが送り先に載ったことを確かめてから、`resolved_threads` /
`deferred` / `rejected` の配列から返信と決着を、件数からまとめを組み立てて投稿キューで送る。
載っていなければ記録も投稿もせずに止まる。担当が送ると、切り離された頭ではブランチ名だけの
送信が何も送らずに終了コード 0 で終わり、送ったという報告と実物が食い違う。

**レビュー本文の指摘は `thread_id` を `null` にし、`comment_id` にはレビューの ID を書く。**
スレッドを持たない要素には返信を積まず、`summary` と理由をまとめのコメントへ載せる。
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

1. `$TMP_DIR/fix-pr<PR>-result.json` を読んで `state.rounds[-1].fix` にマージ
2. `deferred` を `state.deferred_nits` に追記
3. **CI 失敗の分類**:
   - code-fail（チェックジョブの名前がテスト・lint・型検査・ビルドを指す。語の一覧は `state.py` の `CI_CODE_PATTERNS`）: `final=error` で中断 (exit 3)
   - meta-only (`check_pr_requirements` / `assignees` / `reviewers` / `labels` / `meta`): `ci_note` に記録して継続
   - 不明: 保守的に code-fail 扱い

meta-only の語は**区切りで挟まれた語として**一致したときだけ拾う。部分一致にすると
`metabase tests` や `metadata lint` のようなコードチェックまで meta-only になり、失敗した
まま収束する。

**例**: `check_pr_requirements`（Assignees 未設定）はループ継続、
lint や型検査の失敗は即中断してユーザ判断。

**収束の判定（Step 3）も同じ振り分けを使う**（#327）。両方の AI が承認したラウンドは、
収束を返す前に `commits/{HEAD_OID}/check-runs` を **1 度だけ** 照会する。code-related の
失敗があれば**中断せず**終了コード 2 で修正のラウンドへ回す。収束の直前は修正の機会が
残っている段であり、そこで中断すると直せる失敗まで人手へ戻すことになる。照会できない
とき（`gh` の失敗 / `HTTP 422` / チェックジョブ 0 件）は収束させ、`rounds[-1].ci.verdict` へ
`unverified` と理由を残す。**進行を止めない側へ倒す。** 同名のチェックジョブは名前ごとの
最新の実行（`completed_at` と `started_at` の新しい方）へ畳んでから振り分ける。本文の編集や
再実行で別の実行が成功したチェックの、前の実行の失敗は数えない。

## Step 6: PR ローテーション (prepare → Agent → execute の 3 段)

`rotate-pr.sh` は **light モード (default) と squash モード (opt-in)** を持つ。
両者ともメインからは `prepare → (light のみ Agent) → execute` の 3 段で呼ぶ。

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

- **最終 APPROVE ラウンドのインラインコメント**: skill のレビュー方針上、`minor` 以下しか
  無ければ `APPROVE` で良い。つまり **APPROVE でも minor/nit のインラインコメントが
  投稿されている**ことがあり、両者 APPROVE でループを抜けるとこれらが未対応のまま残る。
- **ループ中に deferred 記録した nit**: `state.deferred_nits` に積まれたまま reply のみで
  Resolve されていないスレッド。

これらを放置すると、PR レビュー画面に「Unresolved」スレッドが残り、人間のレビュアーや
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
> 1. 修正可能な `minor`/`nit` → コード修正 + コミット（**送らない**）し、`resolved_threads` へ入れる。
> 2. 修正しない（好み・判断保留）`nit` → 見送りの理由を添えて `deferred` へ入れ、
>    **`"resolve": true`** を付ける（スレッドを open のまま残さない）。
> 3. bot 誤指摘 → 却下理由を添えて `rejected` へ入れ、`"resolve": true` を付ける。
>
> **GitHub と git へ書かない。** 返信・決着・送信は、メインがこの結果ファイルを読んで
> 共通ライブラリの 1 行（`result_posts.py fix`）で行う。
>
> **修正をコミットした場合は、対象リポジトリの検証を 1 度通すこと。** 何を実行するかは
> 対象リポジトリを見て決める。**コマンドを推測して組み立てない。**
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
`commit` の 5 つだけで、この項目は読まない。記録は結果ファイルとサブエージェントの
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
- 残 deferred nit 一覧

`verify-sweep` を通していれば、`report` の出力に「## 最終スイープ」の節が入り、
GitHub 側で数え直した残件数と、0 件にできなかった場合の理由が含まれる。
メインはこれに `sweep-pr<STATE_PR>-result.json` の `resolved` / `fixed_in_sweep` /
`verification`（実行した検証コマンドと終了コード、実行しなかった場合はその理由）を
添えて最終報告する。

> **方針変更（v4.11.0）**: 従来は deferred nit を「AskUserQuestion で 1 回問い合わせ」て
> いたが、未解決スレッドを残さない方針に変更。**Step 7.5 で nit も含め全 open thread を
> Resolve する**ため、Step 8 のユーザ問い合わせは原則不要。deferred nit は「対応見送りの
> 記録」として report に **参考列挙**するに留める（再対応が要るものがあればユーザが
> その場で指示できる）。`remaining_open > 0` の場合のみ、残った理由を添えて報告する。
