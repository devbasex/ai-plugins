# `/goal` の条件の Skill の案内と、本文で承認を待って終えた応答の差し戻し

`/goal /ndf:development-workflow #<番号>` で始めた会話をラッパー（`plugins/ndf/scripts/relay.py`）が次のセッションへ
切り替えても、次のセッションの conductor が作業の最初に条件の Skill を読み込み、Skill が定める承認ゲートの止まり方
（`AskUserQuestion`）で止まるようにする。あわせて、ラッパーの直接の子の応答が `AskUserQuestion` を呼ばずに本文で承認を
待って終わったら、Stop を 1 度止めて `AskUserQuestion` で出し直させる（#1492）。

例: `/goal /ndf:development-workflow #895` で始めた会話が、カットポイントで `ndf-next`（中身 `/goal /ndf:development-workflow #895`）を出したとき。

| 順 | 起きること |
| --- | --- |
| 1 | ラッパーが次のセッションを `claude "/goal /ndf:development-workflow #895"` で起動する。最初の入力は前のセッションと同じ文面である |
| 2 | `UserPromptSubmit` hook（`hook.py goal-skill`）が入力の 1 行目を読み、`ndf:development-workflow` を引数 `#895` で Skill ツールから読み込むよう案内する文を `additionalContext` に足す |
| 3 | Claude Code が `/goal` で目標を設定し、conductor は案内に従って Skill を読み込んでから作業を始める |
| 4 | 承認ゲートで conductor が「設計 PR の承認を待ちます。」と本文に書いて応答を終えると、Stop hook（`relay.py mark`）が Stop を止め、同じ問いを `AskUserQuestion` で出し直すよう伝える。`log.jsonl` に `prose_wait` の行が 1 行残る |
| 5 | 続いた応答が再び本文で待って終わっても、その Stop は止めない（同じ区間で 2 度続けては止めない） |

この文書は、その形に**なぜしたか**（背景・決定と理由・常に成り立つ条件・境界・データの形・既知の限界・テスト観点）を残す。
**手順・振る舞いの説明・入出力の契約の正は Skill・スクリプト・本体の確定仕様にある。**

| 何を読むか | 正本 |
| --- | --- |
| 利用者向けの説明（付則「`/goal` を付けた場合」・「承認ゲートを越えない守り」・「記録の読み方」の `prose_wait`） | [`development-workflow` の `references/relay.md`](../../plugins/ndf/skills/development-workflow/references/relay.md) |
| `ndf-next` の中身の先頭に `/goal ` を付ける決まり | [`references/context-window.md`](../../plugins/ndf/skills/development-workflow/references/context-window.md) の「新しい会話で戻す」 |
| `mark` の判定の 9（差し戻し）・`wait-held.json`・`log.jsonl` の `prose_wait` の行・`UserPromptSubmit` の hook の行 | [ndf-relay-segment-restart.md](ndf-relay-segment-restart.md) |
| 案内の文とパターン | [`hook_lib/goal_skill.py`](../../plugins/ndf/scripts/hook_lib/goal_skill.py) の冒頭の説明と定数 |
| 差し戻しの文と判定の順序 | [`relay_lib/mark.py`](../../plugins/ndf/scripts/relay_lib/mark.py) の `WAIT_REASON` と `prose_wait_reason` |
| 本文の待ちの判定の語 | [`lib/wait_notice.py`](../../plugins/ndf/scripts/lib/wait_notice.py) の `classify_reply_wait` と語の定数 |

## 用語

「条件の Skill」「承認待ちの文」「承認待ちの差し戻し」の定義は用語集（[docs/glossary.md](../glossary.md)）が正である。

## 背景

relay が次のセッションを始める最初の入力は `/goal <条件>` か定型の文だけで、Claude Code の `/goal` は条件の中の Skill を
展開しない。条件に書いた `/ndf:development-workflow` を読まずに進んだ conductor は、承認ゲートで `AskUserQuestion` を出さず、
本文で承認を待って応答を終えた。`/goal` の判定はターンの終わりに走るため、本文の待ちでは止まらず、承認のないまま先へ進む
（`approval-request.md` の「止まる手段」）。利用者はメモリ（「再開したら goal の Skill を読み込む」）で回避していた。

次のセッションの最初の入力を作る経路は 3 つある。カットポイントの `ndf-next`、利用上限での切り替え（`--resume <会話>` を前に付ける）、
`/ndf:restart`（引数が無いとき目標の入力を再開コマンドにする。ラッパーの外では人が貼り付ける）。

実測（Claude Code 2.1.295 の `claude -p`）で分かったこと:

- `/goal` の入力でも `UserPromptSubmit` hook は働き、`prompt` に `/goal …` の文字列そのものを受ける
- hook が `additionalContext` で Skill を読み込む案内を返すと、目標は設定され（`Goal set: …`）、続く応答が Skill ツールを呼んだ
- 目標の判定が止めを拒んだ後の Stop は、すべて `stop_hook_active: true` になる

## 決定と理由

| # | 決定 | 理由 | 採らなかった案 |
| ---: | --- | --- | --- |
| 1 | 条件の Skill の読み込みは、入力を受けた側の `UserPromptSubmit` hook の案内で行う | 3 つの経路のどれから来た `/goal` も同じ形で受け、1 か所で直せる。最初の入力と `ndf-next` の形を変えないため、既存の引継ぎ文書と互換が崩れない | 最初の入力を `/goal` と Skill の呼び出しの 2 つに分けて端末へ打つ（ラッパーの中でしか働かず、書き込みの時機を新しく持つ）。`SessionStart` hook（最初の入力を受け取らず、ラッパーの外で条件が分からない） |
| 2 | 案内はラッパーの外の会話でも出す | Skill が展開されないのは `/goal` の振る舞いで、ラッパーの有無に依らない。`/goal /<プラグイン>:<Skill>` で始まる入力にだけ出るため、ほかの入力は変わらない | `NDF_RELAY_DIR` のある会話だけで動かす |
| 3 | 承認待ちの文の判定は `lib/wait_notice.py` に置き、回答待ちと承認待ちの両方を差し戻す | 本文の待ちの判定を待ち通知と同じモジュールの 1 か所に置く。`/goal` の会話では判断を問う本文も承認と同じく止まらない。判定は通知の `classify_text` より狭い `classify_reply_wait` で、操作の案内（「アプリを再起動してください」）は待ちに数えない（スプリント PR のレビューで狭めた） | 承認待ちだけを差し戻す。別の語の一覧を持つ |
| 4 | 差し戻した直後の Stop は、`stop_hook_active` でなく作業ディレクトリの `wait-held.json` で見分ける | `/goal` の会話では目標の判定の後の Stop が常に `stop_hook_active: true` になり、それで見分けると 2 回目以降の応答をまったく差し戻さない。間に差し戻さない Stop を挟めば、後の承認ゲートで再び差し戻せる | `stop_hook_active` を使う。本文のハッシュで 1 度だけにする（続きが別の文面で待つと 2 度止める） |
| 5 | 質問を出した応答は、質問シグナルファイル（`question`）と `asked` の時刻を前の Stop の時刻と比べて見分ける | 会話の記録を読まずに決まり、既存の `asked_after` と同じ材料を使う | 会話の記録の Tool の呼び出しを遡る（Stop の時点で最後の行がまだ書かれていない・`/goal` の続きで応答の始まりが定まらない） |
| 6 | `ndf-next` のブロックを含む応答と、シグナルファイル（`next.json`）が保留中の Stop では差し戻さない | 切り替えの判定（`mark`）の入力と結果を変えないため。ブロックの後に目標が未達で続いた応答を差し戻すと、`AskUserQuestion` で `asked_after` が真になり切り替えが取り消される。`ndf-next` と同じ応答の問いは #1286 の範囲 | ブロックの有無だけを見る |

## 仕様

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | 案内を出すのは、入力の 1 行目が `/goal ` で始まり、条件の先頭の語が `/<プラグイン>:<Skill>` の形のときだけ | 出力が無く、入力は今と同じに扱われる |
| I2 | 案内は入力を書き換えない。次のセッションの最初の入力と目標の条件は、前のセッションと同じ文面のまま | — |
| I3 | 案内の hook は、入力が読めない・例外・Skill が導入されていないときも終了コード 0 で終わり、入力を止めない | 何も出さずに通す |
| I4 | 差し戻すのはラッパーの直接の子の Stop だけ。ラッパーの外と `claude -p` の子では `wait-held.json` を読み書きしない | — |
| I5 | 差し戻した直後の同じ区間の Stop では差し戻さない（その応答が再び承認待ちの文で終わっても） | — |
| I6 | 応答の中で `AskUserQuestion` を出した（`question` が在る、または `asked` の時刻が前の Stop 以後）ときは差し戻さない | — |
| I7 | `ndf-next` のブロックを含む応答と、シグナルファイルが保留中（在り、その後に質問が出ていない）の Stop では差し戻さず、シグナルファイルの扱いは差し戻しの無いときと同じ | — |
| I8 | 差し戻すたびに `log.jsonl` へ `prose_wait` の行を 1 行足す。本文と抜粋は書かない | 記録に失敗しても差し戻しの文は出す |
| I9 | 判定・状態の読み書きが例外になったら差し戻さない | 何も出さずに通す |
| I10 | `stop_hook_active` の値で判定を変えない | — |

### 境界

| 入力 | 案内 |
| --- | --- |
| `/goal /ndf:development-workflow #895` | 出す（引数 `#895`） |
| `/goal /ndf:development-workflow #928 .ndf/handoff/issue-928.md の続きから` | 出す（引数は条件の残りすべて） |
| 2 行以上の入力 | 1 行目だけでパターンを見る。引数は 1 行目の残りと 2 行目以降 |
| `/goal /nosuch:skill #1` | 出す（案内の文が「見つからなければ読み込まずに続ける」を含む） |
| `/goal 引継ぎ文書の続きから`・`/goal ndf:x`（先頭の `/` なし）・`/goal /x`（プラグイン名なし）・`/ndf:development-workflow #895`（`/goal` なし）・空の入力 | 出さない |

| Stop | 差し戻し |
| --- | --- |
| 直接の子・ブロックなし・保留中のシグナルファイルなし・質問なし・本文が回答待ちか承認待ち | する（`stop_hook_active` が真でも） |
| 差し戻した直後の同じ区間の Stop | しない。その次の Stop で改めて判定する |
| 区間が替わった最初の Stop | 判定する（前の Stop の時刻は今の区間の `start` の行の `at`） |
| 操作の案内だけの依頼（再起動・インストールなどの動詞を含み、回答・判断・承認の語を含まない） | しない |

### データの形

`wait-held.json`（作業ディレクトリ。`mark` が直接の子の Stop のうち判定したものごとに書き直す）:

```json
{"section": 2, "at": "2026-10-10T04:00:00+00:00", "held": true}
```

`log.jsonl` の `prose_wait` の行（`RelayRecord.prose_wait` だけが書く）:

```json
{"event": "prose_wait", "at": "2026-10-10T04:00:00+00:00", "section": 2, "kind": "承認待ち"}
```

`kind` は `承認待ち` か `回答待ち`。数え方は `relay.md` の「記録の読み方」にある。

ラッパーの複製（`relay_lib/`）は `version_dir.py` の `LIB_FILES` にある `lib/` しか import できないため、`lib/wait_notice.py` を
`LIB_FILES` に含める。

## 運用

### 既知の限界

| 項目 | 内容 |
| --- | --- |
| 案内で読み込みが増える割合 | 誤りが起きた場面（Opus の対話・長い会話の `--resume`）の再現の割合は測っていない。`claude -p --model haiku` では案内が無くても読んだ |
| 承認待ちの文の誤検知 | 判定は文面のヒューリスティックである。`prose_wait` の行と直後の応答が `AskUserQuestion` を呼んだかを振り返りで数え、多ければ `lib/wait_notice.py` の語の一覧を見直す。誤検知の害は 1 回分の応答の続きに抑える（I5） |
| Slack の待ち通知との重なり | 差し戻した Stop でも待ち通知の Stop hook は承認待ちを送りうる。続く `AskUserQuestion` でもう 1 度送る（1 回の待ちにつき 1 件の重なり） |
| `ndf-next` と同じ応答の問い | 差し戻さない（I7）。扱いは #1286 |
| 背景の作業での止め（`hold_once`）が `/goal` の続きで働かない | `stop_hook_active` に頼る既存の止めで、#1877 にある |
| Codex / Kiro / agy | relay の対象外で、案内も差し戻しも持たない |

## テスト観点

結合は偽の claude（[`tests/fixtures/relay_fake_claude.py`](../../plugins/ndf/scripts/tests/fixtures/relay_fake_claude.py)。区間のプロンプトを
`UserPromptSubmit` の形で `hook.py goal-skill` へ渡し、出力を残す）を使う [`test_relay.py`](../../plugins/ndf/scripts/tests/test_relay.py)、
案内の単体は [`test_hook_goal_skill.py`](../../plugins/ndf/scripts/tests/test_hook_goal_skill.py) にある。

| 観点 | 満たすこと |
| --- | --- |
| カットポイント（I2） | `ndf-next`（中身 `/goal /ndf:development-workflow #895`）で切り替えると、区間 2 の起動の引数の最後が同じ文面で、`goal-skill` の出力に `ndf:development-workflow` を読み込む案内が入ること |
| 利用上限（I2） | 最後の `goal_status` が未達の会話で上限のシグナルファイルを書くと、区間 2 が `--resume <会話> "/goal /ndf:development-workflow #895"` で起動し、同じ案内が入ること |
| `/ndf:restart` | 再開コマンドの形を `goal-skill` に渡すと、条件の残りすべてを引数にした案内が出ること |
| 目標の文面 | 区間 2 の `start` の行の `command` が前の区間の目標の入力と同じ文面であること |
| 退行しない（I1） | 「境界」の表の出さない入力で、何も出ず、区間 2 の起動の引数が今と同じであること |
| 失敗で止めない（I3） | Skill が無い条件で案内が出て区間が起動すること。JSON でない入力・`prompt` の無い入力で終了コード 0・出力なし |
| 差し戻す（I10） | 直接の子の Stop で「設計 PR の承認を待ちます。」で終わる応答に `decision: block` と差し戻しの文が出ること。`stop_hook_active: true` でも同じ |
| 2 度止めない（I5） | 差し戻した直後の Stop では何も出ず、その次の Stop では再び差し戻すこと |
| 差し戻さない（I4・I6・I7） | ラッパーの外（`wait-held.json` を作らない）・待ちでない応答・操作の案内だけの応答・質問を出した応答・ブロックを含む応答（シグナルファイルは今と同じに書く）・保留中のシグナルファイルの後の応答（`next.json` が残る）で何も出ないこと |
| 記録（I8） | 差し戻すと `prose_wait` の行が 1 行足され、`section` と `kind` を持ち、本文を持たないこと。`log.jsonl` に書けなくても文は出ること |
| 例外（I9） | 判定が例外を出すとき、何も出さずに終了コード 0 |
| 手動 | ラッパーの下で `/goal /ndf:development-workflow #<番号>` の会話から `ndf-next` で切り替え、次のセッションの会話の記録に Skill の読み込みがあること（リリース後テスト） |

## 関連リンク

- [ndf-relay-segment-restart.md](ndf-relay-segment-restart.md)（ラッパーの本体の契約と付則「`/goal` を付けた場合」）
- [ndf-relay-install-and-restart.md](ndf-relay-install-and-restart.md)（承認ゲートを越えない守り・`/ndf:restart`）
- [`hook_lib/goal_skill.py`](../../plugins/ndf/scripts/hook_lib/goal_skill.py) / [`relay_lib/mark.py`](../../plugins/ndf/scripts/relay_lib/mark.py) / [`lib/wait_notice.py`](../../plugins/ndf/scripts/lib/wait_notice.py)
- 課題 [#1492](https://github.com/devbasex/ai-plugins/issues/1492)・`ndf-next` と同じ応答の問いは [#1286](https://github.com/devbasex/ai-plugins/issues/1286)・背景の作業での止めは [#1877](https://github.com/devbasex/ai-plugins/issues/1877)
