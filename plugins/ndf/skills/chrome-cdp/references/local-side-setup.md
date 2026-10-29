# 指示書: 利用者の PC 側の設定（PC で動くエージェントへ渡す）

この文書は、**利用者の PC で動くエージェント**（Windows の WSL の Claude など）へそのまま渡す。目的は、リモートのホストの上の開発コンテナから、この PC の Chrome を CDP で操作できるようにすること。コンテナ側（中継）の準備は、コンテナ側のエージェントが済ませている。

## 全体の形

```
この PC の Chrome（127.0.0.1:9222）
   ↑ SSH の RemoteForward（エディタの Remote-SSH の接続に相乗りする）
リモートのホストの 127.0.0.1:9222
   ↑ 中継コンテナ cdp-relay（ブリッジのゲートウェイで受ける）
開発コンテナ
```

## やること

### 1. エディタが使っている SSH の設定ファイルと Host を特定する

- Windows の VS Code の Remote-SSH は、普通は **Windows の OpenSSH** と `C:\Users\<名前>\.ssh\config` を使う。WSL の `~/.ssh/config` ではない
- VS Code の設定 `remote.SSH.configFile` と `remote.SSH.path` を見る（WSL からは `/mnt/c/Users/<名前>/AppData/Roaming/Code/User/settings.json`）。指定が無ければ既定の場所
- その設定の中で、リモートのホストを指す `Host` の節（VS Code のリモートエクスプローラーに出ている名前と一致するもの）を探す
- macOS なら `~/.ssh/config`

### 2. その Host の節に 1 行足す

```
    RemoteForward 9222 127.0.0.1:9222
```

- 先に `config.bak-<日付>` としてバックアップを取る
- 既存の行は変えない。改行コード（Windows は CRLF）と文字コードを保つ
- 既に `RemoteForward 9222` があれば足さない

### 3. CDP 用の Chrome を起動する手段を用意する

Chrome 136 以降は、**既定のプロファイルではリモート操作の口が開かない。** 専用のプロファイルで別に起動する。

```
"C:\Program Files\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir=%LOCALAPPDATA%\chrome-cdp
```

- Windows はデスクトップに「Chrome (CDP)」のショートカットを作る。WSL からは `powershell.exe -NoProfile -Command` で `WScript.Shell` の `CreateShortcut` を呼ぶ
- Chrome の場所が違えば `C:\Program Files (x86)\...` や `%LOCALAPPDATA%\Google\Chrome\Application\` を探す
- macOS は `open -na "Google Chrome" --args --remote-debugging-port=9222 --user-data-dir="$HOME/.chrome-cdp"`

### 4. 確かめる

1. CDP 用の Chrome を起動する
2. PC の側で 9222 が開いたことを確かめる。Windows は `powershell.exe -NoProfile -Command "(Invoke-WebRequest -UseBasicParsing http://127.0.0.1:9222/json/version).Content"` で `webSocketDebuggerUrl` を含む JSON が返る（WSL の `curl localhost:9222` は WSL のネットワークの設定によっては Windows に届かないので、判定に使わない）
3. 利用者に頼む（このエージェントにはできない）
   - **エディタのウィンドウを閉じて、リモートのホストへ接続し直す**（転送は SSH の接続を張り直すまで効かない）
   - CDP 用の Chrome で、対象のサイトにログインする
4. 利用者に「コンテナ側のエージェントに、PC 側の設定が済んだと伝えてください」と案内して終える

## やらないこと

- 9222 をネットワークへ開く設定（`--remote-debugging-address=0.0.0.0`・`netsh portproxy`・ファイアウォールの許可・Tailscale での公開）。CDP には認証が無く、届けば誰でもログイン済みのブラウザを操作できる
- いつものプロファイルに `--remote-debugging-port` を付けること
- SSH の設定の、`RemoteForward` 以外の行の変更

## うまくいかないとき

| 症状 | 見るところ |
| --- | --- |
| 9222 が開かない | 同じ `--user-data-dir` の Chrome が既に動いていないか。CDP 用だけを閉じて起動し直す |
| 接続のときに `remote port forwarding failed for listen port 9222` | ホストの 9222 を、残っている古い SSH の接続が使っている。古いエディタのウィンドウを閉じる |
| コンテナ側で `socket hang up` | SSH の転送か Chrome のどちらかが無い。2 と 3 を確かめる |
