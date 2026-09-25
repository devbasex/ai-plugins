# 区間の切れ目の告知（`relay.py notice`）と、切れ目で承認を挟まない規則

中継（`plugins/ndf/scripts/relay.py`）の下で conductor が `ndf-next` のブロックを出すとき、ブロックの
直前の告知を「約 N 秒後に自動で新しい会話へ切り替わる。キー入力やスクロールをせずに、そのまま待つ」に
固定する。N は静まりの秒数（`NDF_RELAY_QUIET`、既定 5）である。文面と秒数は `relay.py notice` が出し、
conductor・`/ndf:restart`・文脈量の hook（`token-guard.sh`）はそれを写す。あわせて、区間の切れ目の
再起動は関門ではなく、ブロックの前に承認・確認を挟まない。Claude Code だけが対象である（#980）。

**中継の本体の契約は [ndf-relay-segment-restart.md](ndf-relay-segment-restart.md)、`/ndf:restart` と
関門を越えない守りは [ndf-relay-install-and-restart.md](ndf-relay-install-and-restart.md) が持つ。**
この文書は、告知の入出力の契約と、切れ目で承認を挟まない規則と、それぞれをそう決めた理由を残す。

| 何を読むか | 正本 |
| --- | --- |
| 告知の書き方・切れ目で承認を挟まないこと（conductor の規則） | `plugins/ndf/skills/development-workflow/references/context-window.md` の「新しい会話で戻す」 |
| `notice` の呼び方・区間の切れ目の再起動が関門でないこと | `plugins/ndf/skills/development-workflow/SKILL.md`（中継の段落と「人手の承認を求める関門」） |
| `/ndf:restart` の判定と示し方 | `plugins/ndf/skills/restart/SKILL.md` |
| 文面と秒数 | `plugins/ndf/scripts/relay.py` の `notice_lines` |

## 概要

**例: 区間 2 の conductor が文脈量の上限で止められ、区間を切る（静まりが既定の 5 秒のとき）。**
最後の応答は次のようになる。

````text
約 5 秒後に自動で新しい会話へ切り替わる。キー入力やスクロールをせずに、そのまま待つ
（切り替わらずに ndf-relay: で始まる 1 行が出たら、その案内に従う）

```ndf-next
/goal /ndf:development-workflow #270
```
````

利用者は何もせずに待ち、中継が静まりの後に `/exit` → 更新 → 次の区間の起動を行う。

告知を定めていなかったときは、conductor が「新しい会話で続きから始めるには、次の 1 行を使って
ください」と締め、利用者がそれを手順と読んで `/ndf:restart ...` を手で入力した（2026-09-24、
devbase#270）。入力で応答が再開したため最初の印は無効になり、切り替えが 30 秒余り遅れた。

| 順 | 誰が | 何をする |
| ---: | --- | --- |
| 1 | supervisor | フェーズの報告を返す（関門でない） |
| 2 | conductor | 引継ぎ文書を更新する |
| 3 | conductor | `relay.py notice` を実行する（hook に止められたときは拒否文の 1 文を写してよい） |
| 4 | conductor | 承認・確認を挟まず、2 行目の告知と `ndf-next` のブロックを応答の最後に出して終える |
| 5 | 利用者 | 何もせずに待つ |
| 6 | 中継 | 印を拾い、静まりを待って次の区間へ切り替える（中継の本体は変えていない） |

## 対象範囲

| 含む | 含まない |
| --- | --- |
| 中継の下・外で、区間の切れ目の告知に何を書くか（conductor の 4 つの切れ目・文脈量の hook に止められたとき・`/ndf:restart`） | 静まりの数え方（端末のどの入力でも数え直す） |
| 告知の文面と秒数を出す `relay.py notice` | 中継が待っている間に端末へ 1 行を出すこと |
| 区間の切れ目の再起動が関門ではないこと | 関門の前に会話を切らない規則（`context-window.md` のまま） |
| | Codex / Kiro / agy（中継は Claude Code だけ） |

## 決定と理由

| 決定 | 理由 |
| --- | --- |
| 告知の文面と秒数は `relay.py notice` が出し、LLM は写す | 判定・秒数・文面はどれも判断が要らない。conductor に書かせると区間ごとに言い回しが揺れ、手順と読める文になる。文書に秒数を書くと利用者の設定とずれる |
| LLM に残すのは「いまが切れ目か」と「再開用のコマンドの中身」の 2 つ | どちらも報告の `結果`・`/goal` の目標・引継ぎ文書の名指しに依る判断である。告知とブロックをまとめて出すスクリプトは作らない（コマンドを引数で渡すと写すだけになり、減る判断が無い） |
| 区間の切れ目の再起動は関門ではなく、ブロックの前に確認を挟まない | 状態は課題の本文と Pull Request に残り取り消せる。中継の下で `AskUserQuestion` を出すと質問の印が切り替えを止め、人の入力が 1 回増える。中継の外でも、確認の答えと貼り付けで入力が 2 度になる |
| `/goal` の文面の「承認を求める」は関門 2 つだけを指す | 切れ目の再起動の前に「再起動してよいか」を尋ねたのは、`/goal` の文面をこの切れ目にも当てたためである |
| N は静まりの秒数そのもので、切り替えの時間を足さない | 静まり自体が人の入力と重ならないための余白で、そこへ `/exit` → 更新 → 起動の時間を足すと余白に余白を重ねる。伝えたいのは「待っている間は操作しない」ことである。背景の処理などで延びるため「約」を付け、表示は切り上げず最も近い整数にする |
| 中継の本体は、待っている間に端末へ 1 行を出さない | 子の claude の TUI が画面を持っている間に書くと、再描画で消えるか入力欄と重なる。中継が出すのは今と同じく `ndf-relay:` の 1 行だけで、告知の 2 行目はその 1 行を案内する |
| 静まりは端末のどの入力でも数え直すまま残す | 入力の種類を見分けて一部を無視すると、人が打ち始めた入力へ `/exit` を重ねる経路ができる。告知で「操作せずに待つ」と伝えるほうが壊れる経路を作らない |
| 1 行目は `is-child` の判定と常に一致させ、自動で切り替わるかは 2 行目で表す | 1 行目を切り替えの可否で変えると、`token-guard.sh` が中継の子を外と読み、上限を超えた起動を 1 度通してしまう |

フェーズをどこで切るかは変えない。フェーズの切れ目が増えても、増えた切れ目の告知はこの規則に従う（#954 と接する点）。

## 仕様

### 常に成り立つ条件

- 告知の文面と秒数の定義は `relay.py` の `notice_lines` の 1 箇所だけにある。例外は `relay.py` を呼べないときの外の文面 2 つ（`restart/SKILL.md` の手順 3、`context-window.md` の「新しい会話で戻す」）
- `notice` の 1 行目は `is-child` の判定と一致する。どちらも `under_relay` の 1 つの判定を使う
- `notice` は常に終了コード 0 で終わる。判定の途中の例外は `outside` として出す
- 中継の下（1 行目が `relay`）の告知に、手で入力させる文（「次の 1 行を使って」「貼り付けて」）を書かない。例外は 2 行目そのものが貼り付けの手順を示すとき（`NDF_RELAY_QUIET=inf`）
- `is-child` と中継本体（`run`）の振る舞い・端末への出力は変わらない

### `relay.py notice` の入出力の契約

| 項目 | 内容 |
| --- | --- |
| 呼び方 | `python3 <scripts>/relay.py notice`（引数なし） |
| 読むもの | `NDF_RELAY_DIR`、`NDF_RELAY_QUIET`、中継の作業ディレクトリ（`is-child` と `question` と同じ `under_relay`） |
| 標準出力 1 行目 | `relay` か `outside` |
| 標準出力 2 行目 | 告知の 1 文（下の表） |
| 終了コード | 常に 0 |

| 1 行目 | 条件 | 2 行目 |
| --- | --- | --- |
| `relay` | N ≥ 1 | `約 {N} 秒後に自動で新しい会話へ切り替わる。キー入力やスクロールをせずに、そのまま待つ（切り替わらずに ndf-relay: で始まる 1 行が出たら、その案内に従う）` |
| `relay` | N = 0 | `まもなく自動で新しい会話へ切り替わる。キー入力やスクロールをせずに、そのまま待つ（切り替わらずに ndf-relay: で始まる 1 行が出たら、その案内に従う）` |
| `relay` | `NDF_RELAY_QUIET=inf` | `NDF_RELAY_QUIET が有限でないため、中継は自動で切り替えない。/exit してから claude を起動し、下の中身を最初の入力として貼り付ける` |
| `outside` | `NDF_RELAY_DIR` が無い・中継が動いていない・直接の子でない・判定で例外 | `/exit してから claude を起動し、下の中身を最初の入力として貼り付ける（/ndf:install-wrapper で中継を入れると自動になる）` |

### 秒数の決め方

`quiet` は中継本体の待ちと同じ `quiet_seconds()`（`_num("NDF_RELAY_QUIET", 5)`）で読む（数でなければ 5）。`N = round(q)` で、
Python の `round`（最も近い整数、ちょうど半分は偶数へ。切り上げない）を使う。`q` は次のとおり
中継本体の実際の待ちに合わせる。

| `NDF_RELAY_QUIET` | 中継本体の待ち | `q` / 出力 |
| --- | --- | --- |
| 有限で 0 より大きい | その秒数を待つ | その値 |
| 0・負 | 待たずに切り替える | 0（N = 0 の行）。既定 5 には置き換えない |
| `nan` | 待たずに切り替える（`経過 < nan` が偽） | 0（N = 0 の行）。`max(quiet, 0)` は `nan` を返しうるため使わない |
| `-inf` | 待たずに切り替える | 0（N = 0 の行） |
| `inf` | 切り替えない | 1 行目 `relay`、2 行目は `inf` の行 |

### 告知を使う 3 箇所

| 使う側 | 振る舞い |
| --- | --- |
| conductor（`development-workflow`） | ブロックの前に `SKILL.md` の中継の段落の Bash を 1 回実行し、2 行目を言い換えずにブロックの直前へ写す。`PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'` を Claude Code が置き換えた絶対パスで解く（references の文書では置き換わらないため `$SCRIPTS` に頼らない）。解決できないか 1 行目が `outside` なら、人がブロックの中身を新しい会話へ貼り付けると書く。Codex / Kiro / agy は今までどおり貼り付けると書く |
| 文脈量の hook（`token-guard.sh`） | `NDF_RELAY_DIR` があり上限を超えたときだけ、以前の `is-child` の代わりに `notice` を 1 回起動する（起動の数は変わらない）。1 行目が `relay` なら中継の下として止め続け（`NDF_RELAY_QUIET=inf` でも 1 度の通しをしない）、拒否文に 2 行目をそのまま埋め込み、「承認や確認を挟まずに出して終える」と書く。中継の外の拒否文は変えない。conductor は拒否文の 1 文を実行し直さずに写してよい |
| `/ndf:restart` | 手順 1 で `notice` を 1 回実行する。1 行目が `relay` なら 2 行目を示してブロックを出し、`outside` なら 2 行目と再開用のコマンドを `text` の囲みで示す。1 行しか出ない（`relay.py` を呼べない）ときは中継の外として扱い、Skill に残した外の文面を示す |

### 区間の切れ目で承認を挟まない規則

`context-window.md` の「新しい会話で戻す」と `development-workflow` の `SKILL.md` の「人手の承認を求める関門」が
同じ規則を持つ。

- 区間の切れ目の再起動は関門ではない。引継ぎ文書を更新したら、承認・確認（`AskUserQuestion` を含む）を挟まずに告知とブロックを出して応答を終える
- 中継の外でも挟まない
- `/goal` の文面が「承認を求める」と書いていても、指すのは関門 2 つ（設計 Pull Request のマージ・本番の系へ届く操作）だけである
- 関門の報告（`結果: 関門`）を受けた切れ目では、関門の承認と取り込みの後にブロックを出す（関門の前に会話を切らない）

## 運用

| 項目 | 内容 |
| --- | --- |
| スクロールが入力として届くか | 端末と tmux の設定（マウスの報告）で変わる。届かない環境では告知の「スクロールをせずに」は余分だが害は無い |
| LLM が 2 行目をそのまま写すか | 規則は「写す」と書くが、言い換えを機械では止めない |
| 実機での確認 | 中継の下で `/ndf:restart` を打ち、告知の 1 行に秒数が出て、何も押さずに切り替わるのを見る（リリース後テスト） |

## テスト観点

単体テストは `plugins/ndf/scripts/tests/test_relay.py` と `test_token_guard.py`。`.md` の文言は固定しない（AGENTS.md）。

| 観点 | 確かめ方 |
| --- | --- |
| 中継の直接の子で 1 行目 `relay`・終了コード 0 で、N が既定 5、`6.4` で 6、`abc` で 5、`2.5` で 2（偶数丸め）、`0`・`-3`・`nan`・`-inf` で N = 0 の行（「約 0 秒後」を含まない）、`inf` で `inf` の行になること | `test_notice_under_relay` |
| `NDF_RELAY_DIR` 無し・中継停止・直接の子でない・作業ディレクトリが壊れている、で 1 行目 `outside`・終了コード 0 になること | `test_notice_without_relay_dir`・`test_notice_relay_not_running`・`test_notice_not_direct_child`・`test_notice_broken_relay_dir_is_outside` |
| 中継の下で上限を超えたとき、拒否文に 2 行目が含まれ、`NDF_RELAY_QUIET=inf` でも同じ起動を 2 度続けて止めること | `test_context_under_relay_reason_carries_notice` |
| 中継の外の拒否文に告知が入らないこと | `test_context_outside_relay_reason_has_no_notice` |
| `notice` の 1 行目が `is-child` の判定と一致すること | `test_is_child_matches_notice` |
| `is-child` と `run` の振る舞いが変わらないこと | 既存の `test_relay.py` のテスト |
| Skill の規則（告知の写し方・承認を挟まないこと・`/goal` の文面の読み方・`/ndf:restart` の示し方） | レビューで読む |

## 関連リンク

- [#980](https://github.com/devbasex/ai-plugins/issues/980)（設計は [PR #983](https://github.com/devbasex/ai-plugins/pull/983)、実装は [PR #988](https://github.com/devbasex/ai-plugins/pull/988)）
- [ndf-relay-segment-restart.md](ndf-relay-segment-restart.md) — 中継の本体（#895）
- [ndf-relay-install-and-restart.md](ndf-relay-install-and-restart.md) — `/ndf:restart` と関門を越えない守り（#928）
- [ndf-token-waits-and-context-cut.md](ndf-token-waits-and-context-cut.md) — 文脈量の hook
