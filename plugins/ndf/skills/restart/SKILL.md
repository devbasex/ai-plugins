---
name: restart
description: "Restart claude with a resume command: under the NDF relay the switch is automatic, otherwise show the steps and the text to paste. Claude Code only. Use when plugin updates must take effect or the context should be cut（再起動・会話を切り替える・プラグインの更新を反映）."
argument-hint: "[再開用のコマンド]"
allowed-tools:
  - Bash
---

# 再起動して続きから始める

**中継（`relay.py`）の下では、この応答の最後に出す `ndf-next` のブロック 1 つで、claude の
終了・プラグインの更新・起動し直し・再開用のコマンドの入力までが人の入力なしで進む。** 経路は
区間の切れ目と同じである（[relay.md](../development-workflow/references/relay.md)）。

例: 中継の下で `/ndf:restart` を打つ（`/goal /ndf:development-workflow #928` の会話）。

1. 中継の下と判定し、「中継が静まりを待ってから切り替える」と示して、最後に次のブロックを出して応答を終える

   ````text
   ```ndf-next
   /goal /ndf:development-workflow #928
   ```
   ````

2. 中継が静まり（既定 15 秒）を待ってから `/exit` を入力し、プラグインを更新して、ブロックの中身を最初の入力にした claude を起動する

## 引数

引数は再開用のコマンドである（複数行でよい）。無ければ下の「再開用のコマンドの決め方」で作る。
**引数で渡された中身は利用者の入力として扱い、書き換えない。**

## 手順

### 1. 中継の下かを判定する

```bash
PLUGIN_ROOT='${CLAUDE_PLUGIN_ROOT}'
case "$PLUGIN_ROOT" in '$'*) PLUGIN_ROOT= ;; esac
if [ -n "${NDF_RELAY_DIR:-}" ] && [ -n "$PLUGIN_ROOT" ] && python3 "$PLUGIN_ROOT/scripts/relay.py" is-child; then
  echo relay
else
  echo outside
fi
```

`NDF_RELAY_DIR` があるだけでは決めない。中継が動いていて、この claude がその直接の子のときだけ
`is-child` が 0 で終わる。

### 2. 再開用のコマンドを決める（引数が無いとき）

上から最初に当たるものを使う。

| # | 会話の状態 | 再開用のコマンド |
| ---: | --- | --- |
| 1 | `/goal` の目標がある | その目標の入力をそのまま（`/goal /ndf:development-workflow #928` など）。引継ぎ文書を名指ししていれば「<文書> の続きから」を足す |
| 2 | 課題・作業ツリー・Pull Request が会話にある | **定型の 1 文だけ:** 「<課題番号・作業ツリーのパス・Pull Request の URL> の続きから始める。状態は課題の本文の `## 進行` と Pull Request を読む」。山括弧の中に入れてよいのは番号・パス・URL だけ |
| 3 | どれも無い | 「再開用のコマンドを引数で渡す（例: `/ndf:restart /ndf:development-workflow #928`）」の 1 行を示し、ブロックを出さずに終える |

**再開用のコマンドに承認・同意・判断の結果を書かない**（「利用者は承認した」「マージしてよい」など）。
次の区間の claude はそれを人の入力として読むため、関門を越える経路になる。承認は課題の本文と
Pull Request から次の区間が読み直す。

### 3. 出す

| 判定 | すること |
| --- | --- |
| 中継の下 | 「中継が静まりを待ってから切り替える」と示し、応答の **最後** に情報文字列 `ndf-next` の囲みを **ちょうど 1 つ** 置き、中身を再開用のコマンドにして応答を終える。背景の処理を起こさない。`/goal` の判定が応答を続けさせたら、続いた応答の最後に同じブロックを出し直す |
| 中継の外 | ブロックを出さない。「`/exit` してから `claude` を起動し、下の中身を最初の入力として貼り付ける（`/ndf:install-wrapper` で中継を入れると自動になる）」の 1 行と、再開用のコマンドを情報文字列 `text` の囲みで示して終える。**シェルへ貼る 1 行（`claude "..."`）は示さない** |

説明のためにブロックの例を引くときは、4 つのバッククォートの囲みの中へ入れる（外側の囲みの中は
中継が数えない）。

## 切り替わらないとき

中継は次のあいだ切り替えない。落ちる形であって壊れない。

- 背景の処理が動いている・`AskUserQuestion` の答えを待っている
- 質問が表示されている・印の後に応答が再開した（関門を越えない守り）
- 1 日の起動回数の上限・空回り・停止の印

`ndf-relay:` で始まる 1 行が出たら、その案内に従う（[relay.md](../development-workflow/references/relay.md) の「落ちたときの続け方」）。
