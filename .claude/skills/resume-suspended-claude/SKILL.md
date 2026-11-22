---
name: resume-suspended-claude
description: "Bring a Claude Code session suspended with Ctrl+Z back to the foreground inside a tmux pane, including one running under ndf relay where typing fg does not work. Use when a tmux session shows 'Claude Code is suspended. Run `fg` to bring it back.'（フォアグラウンドに戻す・バックグラウンドに移った・suspended・fg が効かない・オーガナイザーが止まった）"
---

# 一時停止した Claude Code を前面へ戻す

tmux のペインで Claude Code が Ctrl+Z を受けると、次の表示で止まる。

```
Claude Code is suspended. Run `fg` to bring it back.
To undo typing, press ctrl+_.
```

**`ndf relay`（`ndf-relay run`）の中の claude には、ペインへ `fg` を打っても効かない。** relay が
外の端末の入力を内側の端末（claude の pty）へ流すため、`fg` はシェルに届かず、内側の端末に
文字としてたまる。たまった文字は claude が戻ったときに **入力欄へ入る**。ペインへ `fg` を送らず、
claude のプロセスへ直接 SIGCONT を送る。

## 手順

### 1. ペインの中の claude の PID を探す

`<session>` は tmux のセッション名（例: `ai-plugins-1`）。

```bash
S=<session>
p=$(tmux display -p -t "$S" '#{pane_pid}'); pids="$p"; all="$p"
while [ -n "$pids" ]; do pids=$(for x in $pids; do pgrep -P $x; done); all="$all $pids"; done
for x in $all; do ps -o pid=,stat=,tty=,comm= -p $x; done | awk '$4=="claude" && $3!="?"'
```

relay の中なら、claude は relay の python の子で、外のシェルとは別の tty（例: 外が `pts/2`、
claude が `pts/3`）にいる。tty が `?` の claude は背景のサブプロセスなので対象から外す。

### 2. 止まっていることを確かめる

```bash
stty -a -F /dev/<claude の tty> | grep -oE '(-)?(icanon|echo|isig) '
```

`icanon` `echo` `isig`（先頭に `-` が無い）なら、claude は端末を通常のモードへ戻したまま止まっている。
動いている claude はこの 3 つが `-icanon` `-echo` `-isig` になる。プロセスの状態（`ps` の `STAT`）は
`S` のままのことがあり、`T` でなくても止まっている場合がある。

### 3. SIGCONT を送って描き直す

```bash
kill -CONT <claude の PID>
sleep 2
tmux send-keys -t "$S" C-l     # 画面を描き直す
tmux capture-pane -p -J -t "$S" | grep -v '^$' | tail -8
```

戻ると、手順 2 のモードが `-icanon` `-echo` `-isig` に変わり、会話の画面と入力欄（`❯`）が出る。

### 4. 入力欄を確かめる

止まっている間にペインへ打った文字（`fg` や Ctrl+L など）は入力欄の末尾に入る。

- **Enter を送らない。** 入力欄に利用者の書きかけの文があれば、送るとその文ごと claude への指示になる
- 混ざった文字だけを `tmux send-keys -t "$S" BSpace` で 1 文字ずつ消す。**描画が遅れることがある**ので、
  消すたびに `C-l` で描き直してから `capture-pane` で確かめ、消しすぎない
- 書きかけの文の末尾が元どおりか確かめられないときは、その旨を利用者へ伝える

## 報告

戻したセッション名・claude の PID・入力欄の状態（書きかけの文の有無と、混ざった文字を消したか）を伝える。
