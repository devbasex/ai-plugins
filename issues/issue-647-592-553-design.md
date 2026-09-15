# #647 / #592 / #553: 適用ラウンドに試行の上限を置き、帰属行の後ろのトレーラーを読む

要求と受け入れ条件は [issue-647-592-553-requirements.md](issue-647-592-553-requirements.md) にある。この文書は
「どう作るか」だけを扱う。

**実装は 1 本の Pull Request（P6）にまとめる**（決定 1）。マイルストーン 21 の順序では P3 の後に載せ、
P4・P5 とは並行してよい。

## 機能一覧

| # | 機能 | 誰が使うか |
| --- | --- | --- |
| 1 | 結果を残さない担当の群を、担当を替えて 1 回だけ開き直し、2 回目も残さなければ取り消す | cross-refactoring を回す進行側 |
| 2 | 採用 0 件の提案ラウンドと項目の無い群で、適用担当を起動せずに次へ進む | 同上 |
| 3 | 修正の結果を群の担当から読み、結果が無ければ修正ラウンドを 1 つ進める | 同上 |
| 4 | 帰属行の段落が後ろに付いたコミットから必須トレーラーを読む | 同上（claude が適用担当の群） |
| 5 | 適用・修正の監視が、テストの実行中の無出力で担当を打ち切らない | 同上 |

## 決定の記録

決定 1〜8 は #647 と修正ラウンド、決定 9〜10 は #592、決定 11〜12 は #553、決定 13 は無進捗の打ち切りを扱う。

### 決定 1: 3 課題と `merge-fix` の担当の食い違いを 1 本の Pull Request で直す

#647 と #592 は同じ `next-apply-round` → `merge-apply` の繰り返しで止まらず、#553 は同じ適用の検証で群を落とす。
`merge-fix` の食い違いは、#647 を直した後に同じ形（結果を取り込めない担当で修正と検証が往復する）で残る。
いずれも `refactor_lib/commands/apply.py` と `SKILL.md` の骨組みを触るため、分けると同じ箇所を 2 度変える。

`merge-fix` を別の issue に起票する形は採らない。起票しても直す場所と時期が P6 と同じになる。

### 決定 2: 同じ群の試行の上限を 2 回の固定値にし、引数を足さない

2 回目は別の担当が試すため（決定 3）、2 回とも結果を残さなければ担当ではなく群の側を疑える。3 回以上に
しても、輪番の 4 者のうち壊れた者に当たる確率が上がるだけである。値は `vocabulary.py` の
`MAX_APPLY_ATTEMPTS` に置く。

`--max-apply-attempts` を足す形は採らない。`SKILL.md` は上限を 2 つ置くとどちらで止まったかを読み解く
必要が出るとしており、利用者が変えたい理由も見当たらない。止まった理由は群の記録（`drop_reason`）が持つ。

### 決定 3: 2 回目の試行は次の輪番の担当が行い、利用上限でも進行全体を止めない

結果を残さない原因の多くは担当の CLI の側にある（rf646 の agy の STALLED 4 回、claude の 429 の 3729 回）。
同じ担当で開き直しても直らない。担当を替えれば、壊れた CLI が 1 者でも他の者が群を適用できる。
替える先は `apply_seq` を 1 進めた輪番の担当で、群を割り当てたときと同じ式を通す（決定 8）。

利用上限（429）で進行全体を止める形は採らない。止めると他の 3 者で進められる群まで止まり、再開すると同じ者に
また当たる。429 は監視が早期の致命として 15 秒前後で打ち切る（D-A の P3）ため、担当を替える 1 回の費用は小さい。
利用上限だった事実は理由の名前（`usage_limit`）として群の記録と見送りの理由に残る。

### 決定 4: 開き直しの判定は `merge-apply` が持ち、骨組みは監視の終了コードで分岐しない

`merge-apply` は結果ファイルを読めたかどうかを自分で知っている。監視の終了コードで分岐しても、
結果ファイルが後から書かれた場合（#584）や、監視は `OK` でも JSON が壊れている場合は結果ファイルの側で
決めるしかない。判定を 1 か所に置くと、骨組みは `|| continue` のまま変わらない。

骨組みで監視の終了コードを受けて `merge-apply` へ渡す形は採らない。渡しても振る舞いは変わらず、
使い道は理由の記録だけである。理由は D-A が P1 で残す監視の結果の記録から読む（「他の設計との契約」）。

### 決定 5: 試行番号は `next-apply-round` が進め、前の試行が失敗で閉じたときだけ進める

`next-apply-round` が `pending` の群を開くとき、失敗した試行の記録の数が試行番号と等しければ試行番号を
1 進める。等しくなければ（取り込みの前に進行が止まった再開）そのまま開く。

`merge-apply` は同じ試行番号の失敗の記録が既にあれば、記録を足さずに同じ終了コード 2 を返す。進行は
どこで止まっても叩き直せることが前提である（`docs/02-apply-and-review.md`「叩き直しても同じ判定を返す」）。
叩き直しを 2 回目の試行と数えると、1 回の失敗で群を落とす。

開いた回数だけを数える形は採らない。進行側が落ちて再開しただけで試行が進む。

### 決定 6: 結果を残さない試行のコミットは、開き直す前に取り消す

`next-apply-round` は `pending` の群を開くたびに起点を HEAD へ置き直す（`apply.py:304-309`）。
結果を残さない担当がコミットだけ作って止まると、そのコミットは次の試行の起点より前に入り、
検証を受けないまま次の群の push で Pull Request に出る。範囲にコミットがあれば、
既存の `_revert_unverified_apply_round` と同じ全範囲の取り消しを通し、起点を取り消し後の HEAD にする。

### 決定 7: 着手前テストの未確認と範囲の未確定は中断（4）にする

`init` は着手前テストが失敗していれば止まるため、`merge-apply` で `green` でないのは状態ファイルが壊れた
ときだけである。範囲を確定できないのも git の状態が壊れたときである。どちらも群を替えても直らず、
`SKILL.md` の終了コードの表は既に「範囲を確定できない」を 4 と書いている。

試行の上限へ含める形は採らない。全ての群が 2 回ずつ同じ理由で落ち、全件が見送りになってから気づく。

### 決定 8: 担当の決定は 1 つの補助関数を通す

`_impl_for_seq(state, seq)` を新設し、`assignment.assign` を呼ぶのはその中だけにする。群を割り当てるとき
（`_assign_apply_rounds_to_state`）と担当を替えるときの両方が、これを呼ぶ。D-B が除外（#478）を足すとき、変える呼び出しは 1 か所で済む。
`assignment.py` は変えない。

### 決定 9: 採用 0 件の提案ラウンドでは群を作らない

`rounds.apply_groups` は、`apply_rounds` が空の配列のときも古い版の状態として扱う（`rounds.py:108-110` の
`if groups:`）。そのため、ラウンド全体を 1 つの群にしている。`merge-proposals` は採用 0 件で
`apply_rounds = []` を書くため、ここで項目 0 件の群が生まれる。鍵が無い（`None`）ときだけ古い版として
扱い、空の配列はそのまま返す。

`merge-proposals` がテスト整備の採用 0 件で終了コード 2 を返す形は採らない。2 は構造改善の繰り返しを
終える合図で、テスト整備では構造改善へ進む前に抜けてしまう。

### 決定 10: 項目の無い群は開かずに取り消し、取り込み済みで採用 0 件の群も取り消しへ直す

決定 9 の後も、既に項目の無い群を持つ状態ファイル（rf587）は残る。`next-apply-round` は `items` が空の
`pending` の群を `dropped`（`drop_reason: empty`）にして次の群を探す。`merge-apply` の取り込み済みの判定で
採用 0 件だったときは、群が `dropped` でなければ `dropped` にしてから終了コード 2 を返す。

### 決定 11: トレーラーは、末尾から続くトレーラーの段落を git の判定で読む

`commit_trailers` はメッセージを空行で段落に分け、末尾の段落から前へ向かって、1 段落ずつ
`git interpret-trailers --parse` に掛ける。git がトレーラーの段落と判定しなかった段落で止め、それまでに
読んだ段落の値を合わせる。同じ鍵は末尾に近い段落の値を採る。

| issue の案 | 採否 | 理由（「実測」の節） |
| --- | --- | --- |
| `git interpret-trailers --parse` へ替える | 採らない | `%(trailers:only,unfold)` と同じく最後の段落しか読まない |
| 全文から `^<Key>: ` を拾う | 採らない | 散文の段落にある `Round: …` の形の行を拾う |
| 雛形に最後の段落へ置くよう書く | 補助として採る（決定 12） | 実装担当が従わなければ落ちる |
| 進行側が `--amend` でトレーラーを足し直す | 採らない | SHA が変わり、結果ファイルの申告との対応が切れる |

段落ごとの判定を git に任せるため、トレーラーの定義（区切り文字・折り返し・25% の規則）を自前で持たない。
散文の段落で止まるため、本文中の `Key: value` の形の行は読まない。

`claude -p` に `--settings` で帰属行を消させる形は採らない。帰属行を書くのはモデルで、利用者の
`CLAUDE.md` の指示でも足される。共通層の `launch-cli.sh` は cross-review も使う。

### 決定 12: 雛形のコミットの規約にも、必須トレーラーを最後の段落に置くことを書く

進行側の検証は決定 11 で通る。一方、人が `git log --format='%(trailers:key=Impl-Model,valueonly)'` で集計すると、
最後の段落しか読まない。`docs/02-apply-and-review.md` は、この集計を理由にトレーラー形式を選んでいる。
帰属行を同じ段落に続けて書けば、git の標準の読み方でも取れる。

### 決定 13: 無進捗の許容を `--test-timeout` + 900 秒にし、雛形に進捗マーカーを足す

適用・修正の担当はテストを 1 回実行し、その間は何も出力しない。テストの上限は `--test-timeout`（既定 900）である。
claude は `--output-format json` のため、完了まで出力しない（監視の既定の許容 900 秒はこのため）。
2 つを足した値が、正常に動いていても出力が無い最長の時間になる。`init` がこの値を `IMPL_STALL_TIMEOUT` として
出し、骨組みの適用・修正・最終ゲートの修正の監視が `--stall-timeout` に渡す。`monitor.py` は変えない。

雛形の進捗マーカーは、次に STALLED が出たときに担当が動いていたかを読むためにも足す。rf646 の agy のログは
作業ツリーとともに消えており、この設計の時点では確かめられなかった（未確認 1）。

進捗マーカーだけで足りるとする形は採らない。テストの実行中はマーカーを書けない。
`MONITOR_STALL_AGY` などの環境変数に委ねる形も採らない。担当ごとに値を覚えさせることになり、cross-review にも効く。

## 実測

### #647: 結果を残さない群が開き直され続ける

一時のテストファイルで `cmd_next_apply_round` → `cmd_merge_apply` を直接 4 回呼んだ（コミットしない）。
補助は `tests/conftest.py` の `no_git` / `patch_lib` と、`test_merge_apply.py` の `_state_with_items` / `git_facts` である。

| 状態 | 開いた群 | `merge-apply` の終了コード | 群の `status` |
| --- | --- | --- | --- |
| 群 2 つ（agy / codex）、結果ファイルなし | `[1, 1, 1, 1]` | `[2, 2, 2, 2]` | `pending`, `pending` |
| 同上で着手前テストが `red`（2 回） | `[1, 1]` | `[2, 2]` | `pending`, `pending`（項目は `blocked`） |

### 範囲へ入れたもの: `merge-fix` が提案ラウンドの担当を読む

群の担当 agy、提案ラウンドの担当 codex で `agy-fix-r1-result.json` を置き、`cmd_merge_fix` を 3 回呼んだ。
3 回とも終了コード 2 で `❌ codex の結果ファイルがありません`、`fix_rounds` は 0 のままだった。
`merge-fix` は `entry["impl"]`（`converge.py:463`）を読み、骨組みは `launch-cli.sh "$IMPL" fix` で群の担当を起動する。

### #592: 採用 0 件で項目の無い群が開く

テスト整備ラウンドで提案 0 件の状態から `cmd_merge_proposals`（終了コード 0）→ 上と同じ 4 回を呼んだ。

| 結果ファイル | 開いた群 | 終了コード | 群 |
| --- | --- | --- | --- |
| なし | `[1, 1, 1, 1]` | `[2, 2, 2, 2]` | `items: []`、`status: pending` |
| `{"items": []}` | `[1, 1, 1, 1]` | `[2, 2, 2, 2]` | `items: []`、`status: applied`（rf587 と同じ形） |

`apply_groups` を「鍵が `None` のときだけ群を作る」に差し替えて、同じ手順を試した。`next-apply-round` は 1 回目で
終了コード 1 を返し、`apply_rounds` は `[]` のままだった。

### #553: 段落ごとの読み取り

git 2.53.0 の一時リポジトリでコミットを作り、2 つの読み方を比べた。1 つは `git log -1 --format='%(trailers:only,unfold)'`
である。もう 1 つは決定 11 の読み方の試作で、Python から段落ごとに `git interpret-trailers --parse` を呼ぶ。

| メッセージの末尾 | `%(trailers:only,unfold)` | 試作 |
| --- | --- | --- |
| 必須 4 つ / 空行 / `Co-Authored-By` | `Co-Authored-By` だけ | 5 つ |
| 必須 4 つ / 空行 / `Co-Authored-By` + `Claude-Session` | 帰属行 2 つだけ | 6 つ |
| 必須 4 つと `Co-Authored-By` が同じ段落 | 5 つ | 5 つ |
| `Round: 本文の説明行` / 散文 / 必須 4 つ / 帰属行 | 帰属行だけ | 帰属行と必須 4 つ（`Round` は `4`） |
| 散文 + `Item-Id: R9-999` の段落 / 帰属行 | 帰属行だけ | 帰属行だけ |

`git log -1 --format=%B | git interpret-trailers --parse` は 1 行目と同じく帰属行だけを返した。`interpret-trailers --parse` へ替えるだけでは効かない。
`git interpret-trailers --parse` は題名の無い入力（段落 1 つだけ）ではトレーラーを返さなかったため、試作は
`<題名>\n\n<段落>` の形で渡している。

## 構成要素

| 要素 | 責務 | 課題 |
| --- | --- | --- |
| `rounds.apply_groups`（変更） | 鍵が無いときだけ古い版として群を 1 つ作る。空の配列はそのまま返す | #592 |
| `rounds.current_group`（変更） | 群が 1 つも無いときは中断（4）する | #592 |
| `apply.cmd_next_apply_round`（変更） | 項目の無い `pending` の群を取り消して飛ばす。開くときに試行番号を進める（決定 5） | #647 #592 |
| `apply.cmd_merge_apply`（変更） | 取り込み済みで採用 0 件の群を取り消しへ直す。着手前テストと範囲の検査を結果の読み取りより前に置き、4 で中断する。結果を読めなければ `_close_failed_attempt` へ渡す | #647 #592 |
| `apply._close_failed_attempt`（新設） | 範囲のコミットを取り消し、失敗した試行を記録する。上限未満なら担当を替え、上限なら群を取り消して項目を見送る | #647 |
| `apply._impl_for_seq`（新設） | 輪番の通し番号から担当と要求モデルを返す。`assignment.assign` を呼ぶ唯一の場所 | #647 |
| `apply._monitor_reason`（新設） | 監視の結果の記録から、担当と段に当たる理由の名前を返す。無ければ `missing` | #647 |
| `gitfacts.load_result`（新設） | 結果ファイルを読み、`(payload, problem)` を返す。中断しない | #647 |
| `gitfacts.read_result`（変更なし） | 最終ゲートの修正（`gate.py`）が引き続き使う | — |
| `converge.cmd_merge_fix`（変更） | 群の担当の結果を読む。結果を読めなければ範囲を取り消し、修正ラウンドを 1 つ進めて 2 で終わる | 範囲へ入れたもの |
| `gitfacts.commit_trailers`（変更） | 末尾から続くトレーラーの段落を読む（決定 11） | #553 |
| `vocabulary.py`（変更） | `MAX_APPLY_ATTEMPTS = 2` と `IMPL_STALL_MARGIN = 900` | #647 |
| `setup._emit_init`（変更） | `IMPL_STALL_TIMEOUT` を出す | #647 |
| `prompts/apply.md` / `fix.md`（変更） | 必須トレーラーを最後の段落に置く。進捗マーカー | #553 #647 |
| `prompts/final-fix.md`（変更） | 進捗マーカー | #647 |
| `SKILL.md`（変更） | 語の表の適用ラウンドの上限、「別の上限を置かない」の段落の削除、骨組みの監視の引数と終了コード 2 の注記 | #647 |
| `docs/02-apply-and-review.md` / `docs/04-fix-and-report.md`（変更） | Step 4 の骨組みと開き直しの規則、トレーラーの読み方、修正の結果が無いとき | 全体 |

```mermaid
graph TD
    N[next-apply-round] --> G[rounds.apply_groups<br/>current_group]
    A[merge-apply] --> G
    A --> LR[gitfacts.load_result]
    F[merge-fix] --> LR
    A --> C[_close_failed_attempt]
    C --> S[_impl_for_seq]
    S --> AS[assignment.assign]
```

```mermaid
graph TD
    I[init] -->|IMPL_STALL_TIMEOUT| M[monitor.py]
    M --> MR[監視の結果の記録<br/>D-A の P1]
    C[_close_failed_attempt] --> R[_monitor_reason]
    R --> MR
```

上の図は群の判定、下の図は監視との関係を描く。雛形・文書と、`commit_trailers`（`merge-apply` の検証が呼ぶ 1 本だけ）は含めない。`assignment.assign` と監視の結果の記録は変えない要素である。

### 文脈と配置

```mermaid
graph TD
    利用者 --> ホスト[ホストの CLI セッション]
    ホスト -->|骨組みの bash| RF[refactor.py]
    ホスト -->|launch-cli.sh| 担当[適用・修正の担当 CLI]
    ホスト --> 監視[monitor.py]
    RF --> WORK[work/ の git]
    担当 --> WORK
    担当 --> 結果[結果ファイル<br/>progress.log]
    監視 --> 結果
    監視 --> 記録[監視の結果の記録]
    RF --> 記録
```

**配置は変えない。** すべて利用者の機械のホストのセッションから起動するプロセスで、常駐しない。図は状態ファイルと Pull Request への push を含めない。
この変更で増える辺は、`refactor.py` が監視の結果の記録を読む 1 本だけである。

### 変わるファイル

```text
plugins/ndf/skills/cross-refactoring/
├── SKILL.md                              # 語の表・段落の削除・骨組み
├── docs/02-apply-and-review.md           # Step 4 の骨組み・開き直し・トレーラー
├── docs/04-fix-and-report.md             # 修正の結果が無いとき
├── prompts/apply.md                      # トレーラーの段落・進捗マーカー
├── prompts/fix.md                        # 同上
├── prompts/final-fix.md                  # 進捗マーカー
├── scripts/refactor_lib/
│   ├── rounds.py                         # apply_groups / current_group
│   ├── gitfacts.py                       # load_result / commit_trailers
│   ├── vocabulary.py                     # MAX_APPLY_ATTEMPTS / IMPL_STALL_MARGIN
│   └── commands/
│       ├── apply.py                      # next-apply-round / merge-apply / 補助 3 つ
│       ├── converge.py                   # merge-fix
│       └── setup.py                      # _emit_init
└── tests/
    ├── test_apply_attempts.py            # 新設: AC1〜AC12、AC16
    ├── test_apply_rounds.py              # AC13〜AC15
    ├── test_abandon_items.py             # AC17〜AC19
    ├── test_commit_trailers_git.py       # 新設: AC20〜AC25（一時リポジトリ）
    ├── test_init.py                      # AC27
    ├── test_merge_apply.py               # AC33
    └── test_skill_terms.py               # AC26、AC28〜AC31
# dev.kiro / dev.agy の配布物は bash scripts/build-runtime-plugins.sh で同期する
```

## データ構造（状態ファイルの群）

状態ファイル（`cross-refactoring-rf<番号>-state.json`）の `rounds[].apply_rounds[]` に項目を足す。**版は上げない。**
足す項目が無い状態ファイルは、試行番号 0・失敗の記録なしとして読む。

| 項目 | 型 | 値 | 空のときの意味 |
| --- | --- | --- | --- |
| `attempt` | 整数 | いま開いている試行の番号。1 から | 鍵なし = 0（まだ開いていない）。`merge-apply` は 0 を 1 回目として記録し、値を 1 に書く（変更前の版で開いた群を再開したとき） |
| `failed_attempts` | 配列 | 結果を残さなかった試行。1 件 = `{attempt, impl, reason, at, reverted}` | 鍵なし = 失敗なし |
| `failed_attempts[].reason` | 文字列 | 監視の理由の名前（`stalled` など）。記録が無ければ `missing` | — |
| `failed_attempts[].reverted` | 整数 | その試行の範囲から取り消したコミットの数 | 0 = コミットなし |
| `drop_reason` | 文字列 | `no_result`（試行の上限）/ `empty`（項目なし） | 鍵なし = 既存の経路で取り消した、または取り消していない |
| `impl` / `impl_model` | 既存 | 担当を替えたときに書き換える。**前の担当は `failed_attempts[].impl` に残る** | — |

`impl` を書き換えても、どの担当がどの試行で失敗したかは `failed_attempts` から読める。
群を取り消したときの見送りの理由（`deferred_items[].defer_reason`）は、次の形にする。

```text
実装担当が結果を残しませんでした（agy: stalled → codex: missing）
```

この理由は、改修計画の「見送った項目」の表にそのまま出る。

ラウンドの項目（`rounds[]`）は `fix_merged_keys` に `"<fix_attempts>:missing"` の形の鍵を足す（AC19）。

### 群の状態の遷移

```mermaid
stateDiagram-v2
    [*] --> pending: merge-proposals
    pending --> dropped: next-apply-round（items が空）
    pending --> pending: merge-apply（結果を残さない・attempt < 2）<br/>担当を替える
    pending --> dropped: merge-apply（結果を残さない・attempt = 2）
    pending --> dropped: merge-apply（未割当・検証の失敗）
    pending --> applied: merge-apply（取り込んだ）
    applied --> dropped: merge-apply（取り込み済み・採用 0 件）
    applied --> verified: verify-round（通った）
    applied --> dropped: abandon-items / merge-test-judgements
    verified --> [*]
    dropped --> [*]
```

**`pending` のまま同じ担当で開き直す遷移は無い。** この変更で足す遷移は 4 本で、`items` が空の取り消し、
担当の交代、試行の上限での取り消し、取り込み済み・採用 0 件の取り消しである。

## 入出力の契約

### コマンドの終了コード

| コマンド | 0 | 1 | 2 | 4 |
| --- | --- | --- | --- | --- |
| `next-apply-round` | 群を開いた（変更なし） | 残りの群が無い（変更なし） | — | 群が無いラウンドで `current_group` を呼んだ（新） |
| `merge-apply` | 取り込んだ（変更なし） | — | **この群を取り消した、または担当を替えて開き直す**（意味を広げる） | 着手前テストが `green` でない・範囲を確定できない（2 から変更） |
| `merge-fix` | 取り込んだ（変更なし） | — | 範囲を確定できない（変更なし）・**結果を読めない（新。修正ラウンドは進む）** | — |

骨組みは `merge-apply` の 2 で `continue` し、`merge-fix` の終了コードを見ない。**どちらも変えない。**

### `init` の出力

`IMPL_STALL_TIMEOUT=<test_timeout + 900>` を足す。`test_timeout` は状態ファイルの値で、再開時も同じ値を出す。

### 骨組み（`SKILL.md` の「実行」の差分）

```bash
    "$LIB/monitor.py" "$ID" --agents "$IMPL" --tmp-dir "$TMP_DIR" \
        --stem-template "{agent}-apply-r$ROUND" --timeout 3600 \
        --stall-timeout "$IMPL_STALL_TIMEOUT"
    # 終了コード 2 = この群を取り消した、または担当を替えて開き直す。修正ラウンドは回さない
    rf merge-apply "$ID" "$ROUND" || continue
```

修正（`{agent}-fix-r$ROUND`）と最終ゲートの修正（`{agent}-final-fix`）の監視にも同じ `--stall-timeout` を足す。

### 雛形に足す文

| 雛形 | 足す文 |
| --- | --- |
| `apply.md` / `fix.md` のコミットの規約 | 4 つのトレーラーは**メッセージの最後の段落**に置く。実行環境が帰属行（`Co-Authored-By:` など）を足すときは、空行を挟まず同じ段落に続ける |
| `apply.md` / `fix.md` / `final-fix.md` | 作業段階が進むたびに `$RF_STEM-progress.log` へ 1 行追記する（`start` / `edit` / `test` / `commit` / `done` と対象だけ。推論は書かない）。形は cross-review の `launch-reviewer.sh` の「進捗マーカー」に揃える |

## 処理の流れ

### `next-apply-round`

```mermaid
graph TD
    S[群を順に見る] --> P{status が pending か<br/>applied の群がある}
    P -->|無い| E1[終了コード 1]
    P -->|ある| K{status}
    K -->|applied| O[開き直す<br/>起点・試行番号を動かさない]
    K -->|pending| EM{items が空}
    EM -->|はい| D[dropped<br/>drop_reason: empty] --> S
    EM -->|いいえ| Q{失敗の記録の数 ==<br/>attempt}
    Q -->|はい| INC[attempt を 1 進め<br/>起点を HEAD にする]
    Q -->|いいえ・再開| OUT[APPLY_ROUND / IMPL を出す]
    INC --> OUT
    O --> OUT
```

再開で試行番号を進めないときも、`apply_round` と `apply_base_sha` は群の値から書き直す。

### `merge-apply`

```mermaid
graph TD
    B[置き土産を捨てる / 取り消しと push の再開] --> G{取り込み済みか}
    G -->|採用あり| R0[終了コード 0]
    G -->|採用 0 件| DR[群が dropped でなければ dropped] --> R2[終了コード 2]
    G -->|未取り込み| BL{着手前テストが green}
    BL -->|いいえ| A4[終了コード 4]
    BL -->|はい| RG{範囲を確定できる}
    RG -->|いいえ| A4
    RG -->|はい| LD{load_result が読めた}
    LD -->|はい| V[既存の検証と取り込み]
    LD -->|いいえ| DUP{同じ attempt の失敗の記録がある}
    DUP -->|はい| R2
    DUP -->|いいえ| CF[_close_failed_attempt] --> R2
```

`_close_failed_attempt` は次の順で行う。

1. 範囲にコミットがあれば全範囲を新しい順に取り消し、群と記録の起点を取り消し後の HEAD にする（push の印を先に立てる）
2. `failed_attempts` へ `{attempt, impl, reason: _monitor_reason(...), at, reverted}` を足す（`attempt` が 0 なら 1 として記録する）
3. `attempt < MAX_APPLY_ATTEMPTS` なら `apply_seq` を 1 進め、`_impl_for_seq` の担当と要求モデルで `impl` / `impl_model` を書き換える
4. `attempt >= MAX_APPLY_ATTEMPTS` なら群の項目を `abandoned` にして `deferred_items` へ入れる。群は `dropped`（`drop_reason: no_result`）にし、`apply.merged_at` を立て、局面を `phase_after_group` にする
5. 保存し、取り消しがあったときだけ push する（`push_with_retry_marker`）

### `merge-fix`

担当は `current_group(entry)["impl"]`（無ければ `entry["impl"]`）から読む。`load_result` が読めなければ、
`fix_merged_keys` に `"<fix_attempts>:missing"` があるときは何もせず 2 を返す。無ければ、範囲
（`fix_base_sha`..HEAD）にコミットがあるときだけ `revert_unverified_range` で取り消す。続けて鍵を足し、
`fix_rounds` を 1 進めて保存し、2 を返す。範囲を確定できないときの既存の扱い（`_resolve_fix_range`）と同じ形である。

## 非機能の実現方式

| 大項目 | 要求の条件 | 実現方式 | 確かめ方 |
| --- | --- | --- | --- |
| 性能・拡張性 | 適用担当の起動が群の数 × 2 回、修正担当の起動が群の数 × `--max-fix-rounds` 回を超えない | 試行番号の上限（決定 2・5）と、結果が無い修正でも `fix_rounds` を進めること | AC3（開く回数）と AC18（`should-abandon` が上限で 0） |
| 運用・保守性 | 群を取り消した理由が改修計画の「見送った項目」から読める | `defer_reason` に担当と理由の名前を並べる。`plan.py` の表は `defer_reason` をそのまま出す | AC2 で `defer_reason` に `agy` と理由の名前が入ることを見る |

## 他の設計との契約

| 相手 | 契約 | D-C の扱い |
| --- | --- | --- |
| D-A（P1） | 監視が担当ごとの状態と理由をファイルへ残す。**D-C は置き場所と形に依存する** | `_monitor_reason` だけが読む。記録が無い・読めない・該当が無いときは `missing` を返し、振る舞いは変えない |
| D-A（P3） | 理由の名前は #619 の語彙を基本とし、claude の `"api_error_status":429` を `usage_limit` として早期に打ち切る | 名前を解釈しない。記録へ写すだけ |
| D-A（P1 の計測） | 骨組みの監視の呼び出しの前後に計測を足す | P6 は P3 の後に載せるため、D-A の変更の上で `--stall-timeout` を足す |
| D-A（全体） | 監視の終了コードの意味（2 / 3 / 4 / 5 / 6）と `--stall-timeout` の引数は変えない | 骨組みは終了コードで分岐しない（決定 4） |
| D-B（P4 / P5） | 除外（#478）は `assignment.assign` か、その呼び出し側に入る | 担当を決める呼び出しは `_impl_for_seq` の 1 か所。D-B が先に入れば P6 がそこへ寄せ、P6 が先なら D-B がそこを変える |

## テスト設計

実行は `uv run --with pytest pytest plugins/ndf/skills/cross-refactoring/tests -q`。状態の遷移は関数を直接呼ぶ既存の形
（`no_git` / `patch_lib` / `git_facts`）、トレーラーは一時リポジトリで実際に git を実行する。

| 受け入れ条件 | 何で確かめるか | 置き場所 |
| --- | --- | --- |
| AC1、AC2、AC4、AC5 | 群 2 つの状態で結果ファイルを置かずに（AC4 は壊れた JSON と配列で）呼び、`status` / `impl` / `failed_attempts` / `apply_seq` / `deferred_items` を見る | `test_apply_attempts.py` |
| AC3 | `next-apply-round` が 1 を返すまで繰り返し、呼び出し回数 5 と両群の `dropped` | `test_apply_attempts.py` |
| AC6、AC7 | `merge-apply` の 2 度呼び、`next-apply-round` の 2 度呼びで、記録の件数と `attempt` が変わらない | `test_apply_attempts.py` |
| AC8 | `commits_in_range` が 1 件返す状態で、`no_git` の記録に `git revert` が出て、群の `base_sha` が変わる | `test_apply_attempts.py` |
| AC9、AC10 | 着手前テスト `red`、`commits_in_range` が `None` で、`SystemExit` の値が 4 | `test_apply_attempts.py` |
| AC11 | 4 つの経路でパラメータ化し、終了コード 2 の後の群が `dropped` か失敗の記録付き `pending` | `test_apply_attempts.py` |
| AC12 | `_monitor_reason` を差し替えて `stalled` を返させる場合と、記録が無い場合 | `test_apply_attempts.py` |
| AC13 | テスト整備ラウンドの採用 0 件から `merge-proposals` → `next-apply-round` が 1、`apply_rounds == []` | `test_apply_rounds.py` |
| AC14 | `apply_rounds` の鍵を消した状態で群が 1 つ作られる（既存のテストを確かめ直す） | `test_apply_rounds.py` |
| AC15 | rf587 の形の状態で `merge-apply` → 2、群 `dropped`、`next-apply-round` → 1 | `test_apply_rounds.py` |
| AC16 | `items: []` の `pending` の群と、項目のある群を並べて `next-apply-round` | `test_apply_attempts.py` |
| AC17〜AC19 | 群の担当と提案ラウンドの担当を分けた状態で `merge-fix`。AC18 は `should-abandon` まで回す | `test_abandon_items.py` |
| AC20〜AC24 | 一時リポジトリで各形のコミットを作り、`commit_trailers` の戻り値を比べる | `test_commit_trailers_git.py` |
| AC25 | 同じ一時リポジトリで `collect_commit_facts` → `verify_apply_round` が `None` | `test_commit_trailers_git.py` |
| AC26、AC29 | 雛形の文言を `grep` で探す | `test_skill_terms.py` |
| AC27 | `_emit_init` の出力に `IMPL_STALL_TIMEOUT=1800`（`test_timeout` 900 の状態） | `test_init.py` |
| AC28、AC30 | `SKILL.md` の骨組みから `monitor.py` の呼び出し 4 つを取り出し、提案以外の 3 つが `--stall-timeout "$IMPL_STALL_TIMEOUT"` を持つ。語の表の行と段落の有無 | `test_skill_terms.py` |
| AC31 | `docs/02-apply-and-review.md` の Step 4 の `monitor.py` の引数が `SKILL.md` と一致する | `test_skill_terms.py` |
| AC32 | トレーラーの節の記載をレビューで見る | 手動 |
| AC33 | 既存の `test_a_verified_apply_round_marks_every_item_applied` に `failed_attempts` が無いことを足す | `test_merge_apply.py` |
| AC34、AC35 | 全体のテスト、`bash scripts/build-runtime-plugins.sh --check`、`claude plugin validate .`、`python3 scripts/check-skill-frontmatter.py` | 手動 |

## 未確認のまま残ること

| # | 項目 | 内容 | 決める時点 |
| --- | --- | --- | --- |
| 1 | rf646 で agy が打ち切りの時点で動いていたか | ログは作業ツリーとともに消えていた（`find / -name 'agy-apply-r*'` で該当なし）。決定 13 は動いていた場合も止まっていた場合も成り立つ | 次の実行で `progress.log` を読む |
| 2 | 担当が雛形の進捗マーカーに従うか | cross-review の agy は従っている（heartbeat に作業段階が出る）。claude / codex / kiro の適用で従うかは起動して確かめていない。従わなくても決定 13 の許容で打ち切られないのはテスト 1 回分まで | 実装後の最初の実行 |
| 3 | 監視の結果の記録の置き場所と形 | D-A の P1 が決める。`_monitor_reason` の読み方はそれに合わせる | 実装（P1 の後） |
| 4 | 担当を替えた後の担当も結果を残さない割合 | 2 回目で救える群の数は測っていない。#662 の計測で `failed_attempts` を数えて見る | 配布後 |
| 5 | Claude Code 以外の担当が帰属行を足すか | codex / agy / kiro のコミットで帰属行の段落を見ていない。決定 11 は誰が足しても同じに読む | 確かめなくてよい |
| 6 | 帰属行を同じ段落に続ける指示に claude が従うか | 決定 12 は補助で、従わなくても決定 11 で検証は通る | 実装後の最初の実行 |
