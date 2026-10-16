---
name: restart
description: "Restart claude with a resume command: under the NDF relay the switch is automatic, otherwise show the steps and the text to paste. Claude Code only. Use when plugin updates must take effect or the context should be cut（再起動・会話を切り替える・プラグインの更新を反映）."
argument-hint: "[再開コマンド]"
allowed-tools:
  - Bash
---

# 再起動して続きから始める

**ラッパー（`relay.py`）の下では、この応答の最後に出す `ndf-next` のブロック 1 つで、claude の
終了・プラグインの更新・起動し直し・再開コマンドの入力までが人の入力なしで進む。** 経路は
カットポイントと同じである（[relay.md](../development-workflow/references/relay.md)）。

例: ラッパーの下で `/ndf:restart` を打つ（`/goal /ndf:development-workflow #928` の会話）。

1. `relay.py notice` の 1 行目でラッパーの下と判定し、引継ぎ文書 `.ndf/handoff/issue-928.md` を作るか更新してから、2 行目（「約 5 秒後に自動で新しい会話へ切り替わる。…」）を示して、最後に次のブロックを出して応答を終える

   ````text
   ```ndf-next
   /goal /ndf:development-workflow #928 .ndf/handoff/issue-928.md の続きから
   ```
   ````

2. ラッパーがアイドルになるのを待ってから `/exit` を入力し、プラグインを更新して、ブロックの中身を最初の入力にした claude を起動する

## 引数

引数は再開コマンドである（複数行でよい）。無ければ下の「再開コマンドを決める」で作る。
**引数で渡された中身は利用者の入力として扱い、書き換えない。**

## 手順

### 1. ラッパーの下かを判定する

**ブロックを出す前に、必ず次の Bash を 1 回実行する。** 判定を飛ばしてブロックを出さない
（Claude Code 2.1.281 の haiku で、判定を飛ばしてブロックだけを出した例がある）。

```bash
PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'
case "$PLUGIN_ROOT" in '$'*) PLUGIN_ROOT= ;; esac
[ -n "$PLUGIN_ROOT" ] && python3 "$PLUGIN_ROOT/scripts/relay.py" notice || echo outside
```

1 行目が判定（`relay` / `outside`）、2 行目がアナウンスの 1 文である。`NDF_RELAY_DIR` があるだけでは
決めない。ラッパーが動いていて、この claude がその直接の子のときだけ 1 行目が `relay` になる。
1 行しか出なかったとき（`relay.py` を呼べなかった）はラッパーの外として扱う。

### 2. 引継ぎ文書を作る・更新する

置き場・名・節の形・規則の正本は [handoff.md](../development-workflow/references/handoff.md) にある。

1. 引継ぎの対象と名（`sprint-<スプリント名>`・`milestone-<番号>`・`issue-<番号>`）を正本の「置き場と名」で決める。
   決まらなければ文書を作らない（下の表の 3 行目か、引数だけで出す）
2. 文書が無ければ作り、「現在地」と「次にやること」を書き直す（パスは出力の `path`。worktree の中でもメインディレクトリのもの）

   ```bash
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" init <名> --title "<表示名>"
   ```

   **`init` が終了コード 3（git の外・メインディレクトリに `.ndf/` が無い）なら「文書なし」で続ける。** 次の 3 を
   飛ばし、文書のパスを含めない再開コマンド（下の表の「文書なし」。引数があれば引数をそのまま）で「3. 出す」へ進む

3. 下の「再開コマンドを決める」で決めた再開コマンドを文書に置き、形と行数を確かめる。どちらかが 0 以外なら
   正本の「作る・更新する」に従う。`init` が 2（読めない・書けない・名の形が違う）か `next` が 0 以外なら、
   ブロックを出さずに理由を報告する

   ```bash
   printf '%s\n' "<再開コマンド>" | python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" next <名>
   python3 "${CLAUDE_PLUGIN_ROOT}/scripts/handoff.py" check <名> --trim
   ```

**引数で渡された再開コマンドも文書に置く**（中身は書き換えない。文書なしのときは置かずにそのまま出す）。

#### 再開コマンドを決める（引数が無いとき）

上から最初に当たるものを使う。`<文書のパス>` はメインディレクトリからの相対 `.ndf/handoff/<名>.md` で書く。
「文書なし」は、上の 2 で `init` が終了コード 3 を返したときである。

| # | 会話の状態 | 再開コマンド |
| ---: | --- | --- |
| 1 | `/goal` の目標がある | その目標の入力（`/goal /ndf:development-workflow #928` など）に「 `<文書のパス>` の続きから」を足す。既に文書を名指ししていれば足さない。文書なし: 目標の入力をそのまま |
| 2 | 課題・worktree・Pull Request が会話にある | **定型の 1 文だけ:** 「`<文書のパス>` の続きから始める。状態は課題の本文の `## 進行` と Pull Request を読む」。文書なし: `<文書のパス>` の所へ課題番号・worktree のパス・Pull Request の URL だけを入れる |
| 3 | どれも無い | 文書を作らず、「再開コマンドを引数で渡す（例: `/ndf:restart /ndf:development-workflow #928`）」の 1 行を示し、ブロックを出さずに終える |

**再開コマンドにも文書にも承認・同意・判断の結果を書かない**（「利用者は承認した」「マージしてよい」など）。
次のセッションの claude はそれを人の入力として読むため、承認ゲートを越える経路になる。承認は課題の本文と
Pull Request から次のセッションが読み直す。

### 3. 出す

| 判定 | すること |
| --- | --- |
| ラッパーの下（1 行目が `relay`） | 2 行目を言い換えずに示し、応答の **最後** に情報文字列 `ndf-next` のコードブロックを **ちょうど 1 つ** 置き、中身を再開コマンドにして応答を終える。バックグラウンドの処理を起こさない。`/goal` の判定が応答を続けさせたら、続いた応答の最後に同じブロックを出し直す |
| ラッパーの外（1 行目が `outside`） | ブロックを出さない。2 行目（無ければ「`/exit` してから `claude` を起動し、下の中身を最初の入力として貼り付ける（`/ndf:install-wrapper` でラッパーを入れると自動になる）」）の 1 行と、再開コマンドを情報文字列 `text` のコードブロックで示して終える。**シェルへ貼る 1 行（`claude "..."`）は示さない** |

説明のためにブロックの例を引くときは、4 つのバッククォートのフェンスで囲む（外側のフェンスの中は
ラッパーが数えない）。

## 切り替わらないとき

ラッパーは次のあいだ切り替えない。フォールバックする形であって壊れない。

- バックグラウンドの処理が動いている・`AskUserQuestion` の答えを待っている
- 質問が表示されている・シグナルファイルを置いた後に応答が再開した（承認ゲートを越えない守り）
- 1 日の起動回数の上限・再起動ループ・停止シグナルファイル

`ndf-relay:` で始まる 1 行が出たら、その案内に従う（[relay.md](../development-workflow/references/relay.md) の「落ちたときの続け方」）。
