# ラッパーでカットポイントを自動にする（Claude Code だけ）

**`/ndf:development-workflow` のカットポイントで人が行っていた「`/exit`・起動し直し・
次のコマンドの貼り付け」を、ラッパー（`ndf relay`）が行う。** 人が入力するのは承認ゲートの回答だけになる。

例: 設計承認ゲートをまたいで実装へ進む。

1. 利用者がいつもどおり `claude` と打つ（`/ndf:install-wrapper` で入れた関数がラッパーを挟む）。その中で `/ndf:development-workflow #895` を入力する
2. conductor が設計承認ゲートで `AskUserQuestion`（`metadata.source` は `ndf-gate:1`）を出し、利用者が「承認」と回答する
3. conductor が設計 Pull Request をマージし、最後の応答に次のブロックを出して応答を終える

   ```ndf-next
   /ndf:development-workflow #895
   ```

4. ラッパーがそのブロックを拾い、claude を強制終了して、プラグイン（マーケットプレイスと NDF）を最新のバージョンへ更新し、
   区切りの 1 行（`── ndf-relay: セグメント 2 ──`）を出して、同じ端末で `claude "<ブロックの中身>"` を起動する。
   次のセグメントの claude は更新後のバージョンの hook と Skill で動く。更新に失敗しても止まらず、今のバージョンで起動する

ブロックの形は [context-window.md](context-window.md) の「新しい会話で戻す」が定める。

**目次:** [始め方](#始め方) / [外し方・戻し方](#外し方戻し方) / [ラッパーを挟まない起動](#ラッパーを挟まない起動) / [止め方](#止め方) / [好きな時点で切り替える（/ndf:restart）](#好きな時点で切り替えるndfrestart) / [プラグインの更新とラッパーのバージョン](#プラグインの更新とラッパーのバージョン) / [承認ゲートを越えない守り](#承認ゲートを越えない守り) / [上限](#上限) / [利用上限でアカウントを替えて続ける](#利用上限でアカウントを替えて続ける) / [カットポイントの引継ぎ文書と ndf-next](#カットポイントの引継ぎ文書と-ndf-next) / [落ちたときの続け方](#落ちたときの続け方) / [fork したセッションで進めたとき](#fork-したセッションで進めたとき) / [セッションをまたいで設定を保つ](#セッションをまたいで設定を保つ) / [記録の読み方](#記録の読み方) / [付則: /goal を付けた場合](#付則-goal-を付けた場合)

## 始め方

**ラッパーを使うかは利用者が決める。** claude の中で `/ndf:install-wrapper` を 1 度打つ。SessionStart hook は
新しく置かない（置いた後の向け直しだけを行う）。`ndf relay install` が次の 2 つを置き、シェルの設定は書かない。

| 置くもの | 中身 |
| --- | --- |
| 複製と根の指し先 | `${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/` の下の起動の部品と `relay.root`（プラグインの根のパス 1 行）。部品は `relay.root` が指すプラグインの `bin/ndf relay` を呼ぶ。部品の中身はどのバージョンでも同じである。プラグインを更新したときは `relay.root` を書き換えるだけで済む |
| PATH のエントリポイント | `${XDG_BIN_HOME:-~/.local/bin}/ndf-relay`。複製を呼ぶ 2 行のシェルのスクリプト |

- `ndf-relay run` と打つと、ラッパーを通って claude が起動する。`~/.local/bin` が PATH に無ければ、`install` の `warnings` が足す 1 行を示す
- **プラグインを更新した後の最初のセッションで、SessionStart hook が `relay.root` を更新後のバージョンへ向け直す。** 旧いバージョンの保存先は更新で消えないため、向け直さないと次の `claude` も旧いバージョンのラッパーで起動する。向け直すのは NDF が置いた複製があり、`relay.version` より新しいバージョンのときだけで、同じか旧いバージョンのセッションは向き先を戻さない
- プラグインの更新で `relay.root` の指すバージョンの保存先が消えたら、複製は同じ親の下の最も新しいバージョンの保存先へ向け直す。見つからなければ理由と打ち直しのコマンドを 1 行出して止まる
- 旧い NDF（10.x）の複製は残す。旧いラッパーを止めてから `ndf relay install --replace-old` で置き換える
- `/ndf:install-wrapper status` で、複製と PATH のエントリポイントの有無と状態を見られる
- **devbase では `~/.claude` が同じアカウントグループのコンテナで共有される。** 導入・取り外しの効果は同じグループの全コンテナに及ぶ

## 外し方・戻し方

| したいこと | 手段 |
| --- | --- |
| 外す | `/ndf:install-wrapper uninstall`（`ndf relay uninstall`）。自分のマーカーのある複製と PATH のエントリポイントだけを消す |
| 過去のバージョンへ戻した | `/ndf:install-wrapper` を打ち直す（`relay.root` を今のバージョンへ向ける） |

## ラッパーを挟まない起動

`claude` と打っても、次の起動はラッパーを挟まずに本物の claude をそのまま exec する（パススルー）。
振る舞いも終了コードも直接打ったのと同じである。

| 起動 | 例 |
| --- | --- |
| 非対話 | `claude -p ...`・`--help`・`--version` |
| サブコマンド | `claude mcp ...`・`claude doctor` など |
| 端末でない | パイプ・リダイレクト |
| ラッパーの下 | conductor が Bash から起動する `claude -p` |
| 止めた | `NDF_RELAY=0 claude` |
| ラッパーが続けられない会話の選び方 | `-c`/`--continue`・値の無い `-r`/`--resume`・`--fork-session`・`--from-pr`・`--teleport`・`--cloud`・`--bg`/`--background`・`--tmux`・`-w`/`--worktree`・`--desktop`、プロンプトの語が 2 つ以上・`--resume` と `--session-id` の両方 |

最後の行だけは `ndf-relay: ラッパーを始めない（<理由>）。claude をそのまま起動する` の 1 行を標準エラーへ出す。ほかは黙って
パススルーする（`claude -p` の標準出力にラッパーの結果を足さない）。パースは `claude_args.parse` が行い、alias が先頭に付けた
オプション（`--dangerously-skip-permissions` など）があってもサブコマンドと非対話の起動を見分ける。

擬似端末を作れない環境（Windows）と、導入済みのプラグインの一覧（`plugin list --json`）から ndf の
名前とバージョンを読めないときは、`ndf-relay: ラッパーを始めない（<理由>）…` の 1 行を出してからパススルーする。

`ndf-next` のブロックを出さない普段の利用では、シグナルファイルが書かれないのでラッパーは何もせず、claude を終えると
何も出さずに終了コード 0 でシェルへ戻る。ラッパーが止まったとき（同じ名前のラッパーが既に動いているなど）だけ、
`ndf-relay:` で始まる 1 行で理由を標準エラーへ出し、停止理由の種類の終了コードで終わる。

## 止め方

| 手段 | 効き目 |
| --- | --- |
| `ndf relay stop --all`（別の端末から） | 動いているラッパーすべてに停止シグナルファイルを置く。次のセグメントのマーカーがあっても今の claude を止めず、利用者が終えるのを待つ。動いているラッパーが無ければ終了コード 1 |
| `ndf relay stop <名前>` | 名前つきのラッパー 1 本だけを止める（停止シグナルファイルを置き、tmux のセッションを終える。下の「複数のループを並べる」） |
| `touch <作業ディレクトリ>/stop` | 同じ（1 つのラッパーだけ） |
| セッションの中で `/exit` か Ctrl-C を 2 回 | ラッパーも次のセッションを起動せずに終わる |

サブコマンドの一覧は `ndf relay --help` が、1 つのサブコマンドの書式と役割は `ndf relay <サブコマンド> --help` が出す（サブコマンドは動かさない。`run --help` は claude の help）。

作業ディレクトリは `${XDG_STATE_HOME:-~/.local/state}/ndf/relay/<時刻>-<pid>-<乱数>/` で、
起動ごとに新しく作る。セッションの中では環境変数 `NDF_RELAY_DIR` が指す。

## 複数のループを並べる（リレーハブ）

例: 同じコンテナで `l20261031`・`lroot`・`lflow` の 3 本の開発ループを回す。1 つ目の端末で `claude` を打つと、
今どおり前景でラッパーが動き、そのラッパーが**リレーハブ**を兼ねる。2 つ目の端末で `NDF_RELAY_NAME=lroot claude` を打つと、
リレーハブが tmux の中に名前つきのラッパー `lroot` を起動し、その端末が結び付く。3 本目は conductor が応答に起動のブロックを置いて始める:

````markdown
```ndf-spawn lflow
/goal .ndf/handoff/loop-lflow.md を読んで続ける
```
````

`ndf relay ls` で 3 本の状態と承認待ちが 1 つの表に出て、`ndf relay stop lroot` で 1 本だけを止められる。

並列開発をまとめて始めるときは、起動のブロックを並べずに並列の計画を書き（[issue-plan-strategy の parallel-plan.md](../../issue-plan-strategy/references/parallel-plan.md)）、
`--parallel <計画名>` でオーガナイザーに起動させる（[parallel.md](parallel.md)）。リリースがレーンどうしでぶつからない。

| 操作 | 効き目 |
| --- | --- |
| リレーハブが居るときに端末で `claude` | リレーハブに起動を頼み、tmux の名前つきのラッパーへ結び付く。名前は `NDF_RELAY_NAME`、無ければ `t2`・`t3`… |
| `ndf relay spawn --name <名前> -- <最初の入力>` | 端末の結び付いていない名前つきのラッパーを起動する。最初のセグメントの claude が入力を受け取る |
| 応答の起動のブロック（情報文字列 `ndf-spawn <名前>`、中身が最初の入力） | Stop hook がリレーハブへリクエストを置く。置いたセグメントは止まらない。1 つの応答に複数置ける |
| `ndf relay ls` | 名前・状態（`running` / `awaiting_approval` / `stopped`）・セグメント・費用（記録が無ければ空）・最後に動いた時刻・アカウント・端末の結び付き・リレーハブかを出す。結果は 1 行の JSON |
| `tmux -L ndf-lanes attach -t <名前>` | 打った端末をそのラッパーへ結び付ける。端末を閉じてもラッパーは動き続ける |
| `ndf relay restart <名前>` | 1 本を止め、最後のセグメントの入力と作業ディレクトリで起動し直す |
| `ndf relay stop <名前>` | 1 本だけを止める。すべては `ndf relay stop --all` |

- **同時に動かす数の上限**は `NDF_RELAY_MAX_NAMED`（既定 3）。リレーハブ・名前つき・名前の無いラッパーを合わせて数え、
  AI の起動（`ndf relay spawn`・`ndf relay restart`・起動のブロック）が超えるときは理由（上限と動いている名前）を出して終了コード 3 で断る。
  名前が重なる・名前の形（`^[a-z0-9][a-z0-9_-]{0,31}$`）が違うときも 3
- **端末で打った `claude`（人の起動）は上限で断らず、上限にも数えない。** 上限を超えていたら
  `ndf-relay: 同時に動かすラッパーの上限 3 を超えて起動する（動いている: …）` の 1 行を出して起動し、リレーハブの記録
  （`hub-log.jsonl`）の `started` の行に `over_limit` を残す。一覧（`ndf relay ls`）には出る。`ndf relay restart` で起動し直すと AI の起動として数える
- **リレーハブが落ちても**名前つきのラッパーは止まらない（tmux のサーバが親）。次に端末で打った `claude` がリレーハブになる。
  リレーハブが `NDF_RELAY_HUB_WAIT`（既定 10 秒）回答なければ、2 本目は 1 行出して今の前景の起動になる
- **起動の確かめ**: リレーハブは `spawn`・`restart` でセッションを作った後、`NDF_RELAY_LAUNCH_CHECK`（既定 2 秒）待ってセッションが残っているかを見る。
  消えていれば登録せず `tmux-failed`・1 を返す
- **tmux が PATH に無い**（`NDF_RELAY_TMUX` で実行ファイルを替えられる）ときは、2 本目は 1 行出して今の前景の起動になる
- **承認待ちの知らせ**は各ラッパーの待ち通知が出し、名前つきのラッパーなら `ラッパー: <名前>（tmux -L ndf-lanes attach -t <名前>）` の 1 行が付く
- tmux は専用のソケット `-L ndf-relay` だけを使い、利用者の tmux サーバに触れない。リレーハブの保存先は
  `${XDG_STATE_HOME:-~/.local/state}/ndf/relay/hub/`（登録簿 `registry.json`・起動のリクエスト `requests-v2/`・結果 `results/`・
  記録 `hub-log.jsonl`。所有者だけが読み書きできる）

## 好きな時点で切り替える（`/ndf:restart`）

**ラッパーの下で `/ndf:restart [再開コマンド]` を打つと、カットポイントと同じ経路で切り替わる。**
claude が再開コマンドを `ndf-next` のブロックで出して応答を終え、ラッパーが静かな時間を待ってから
claude の強制終了 → プラグインの更新 → 起動し直しを行う。出たバージョンの hook と Skill を次のセグメントから使いたいとき・文脈を切りたいときに使う。
更新に失敗しても止まらず、今のバージョンで次のセグメントを起動する（「[プラグインの更新とラッパーのバージョン](#プラグインの更新とラッパーのバージョン)」）。
引数が無ければ、課題番号・worktree・Pull Request を差し込む定型の 1 文で作る。
ラッパーの外では、手順の 1 行と貼り付ける中身を示すだけで終わる。

## プラグインの更新とラッパーのバージョン

**ラッパーは、新しい会話で始めるセグメントの claude を起動する前に、プラグインを最新のバージョンへ更新する。**
次のセグメントの claude は更新後の導入先を読むので、出したバージョンの hook と Skill は、リリースの後の次のカットポイントから効く。

例: 11.0.8 のラッパーの下で、マーケットプレイスに 11.0.9 が出た後に conductor が `ndf-next` のブロックを出す。ラッパーは子を止めて
2 つのコマンドを打ち、セグメント 2 の claude を 11.0.9 の導入先で起動する。`log.jsonl` に
`{"kind": "plugin_update", "result": "updated", "from": "11.0.8", "to": "11.0.9", "launched": "11.0.9", ...}` の 1 行が残る。
ラッパー自身は 11.0.8 のコードのまま動き続ける。

**更新の処理を持つのは 11.0.8 以後のラッパーである。** 11.0.7 以前のラッパーはカットポイントでも最初のセグメントの前でも更新しない
（`plugin_update` の行も書かない）。そこから移るときは次の順で 1 度だけ手で上げる。

1. ラッパーと同じ `CLAUDE_CONFIG_DIR` の下で、ラッパーと同じ作業ディレクトリから、下の 2 つのコマンドを手で打つ。
   対象は、そこで `claude plugin list --json` が返す有効な `ndf@` の行すべてで、行ごとにその ID とスコープ（`--scope`）を指定する。
   打ち直した一覧で、どの行も 11.0.8 以後になったことを確かめる。別の設定・別の作業ディレクトリ（`project`・`local` のスコープはプロジェクトに紐づく）で
   打つと、別の導入が上がり、次のセグメントは旧いバージョンの導入先で起動して `relay.root` も向け直らない
2. 旧いラッパーのまま `ndf-next` のブロックか `/ndf:restart` で次のセグメントへ切り替える。上げた導入先で始まるそのセグメントの
   SessionStart hook が `relay.root` を上げたバージョンへ向け直す（「[始め方](#始め方)」）。切り替える前に抜けると `relay.root` は旧いバージョンのままである
3. `/exit` でラッパーを抜けて `claude` を打ち直すと、上げたバージョンのラッパーが起動する。以後はカットポイントで更新する

| いつ | 振る舞い |
| --- | --- |
| 新しい会話で始める最初のセグメント | 更新してから起動する |
| カットポイントの切り替え（`ndf-next` のブロック・`/ndf:restart`） | 今の子を止める → 更新 → 次のセグメントを起動する |
| `claude --resume <ID>` で始めた最初のセグメント | 更新しない（同じ会話の続きの途中でバージョンを替えない）。起動先のバージョンを確かめるだけ |
| 上限でアカウントを替えて同じ会話を再開する・子の終了や止まりから同じ会話を起動し直す | 同上 |
| 停止シグナルファイルがある | 次のセグメントを起動しないので、更新も確認もしない。更新・確認を待つ間に置かれたときも、起動の直前に読み直して起動せずに止まる |

**更新は 2 つのコマンドをこの順で 1 回ずつ打つ。** マーケットプレイスの名前・プラグインの ID・スコープは、
`claude plugin list --json` のうち、ラッパーが動いている導入先と一致する有効な `ndf@` の 1 件から読む。
一致する導入が無いときは、ラッパーと同じプラグインのキャッシュ・同じマーケットプレイスの下の有効な `ndf@` の導入が
ちょうど 1 件あれば、それを読む（ラッパーの外で導入が上がった後。#3909）。
1 つ目（マーケットプレイスの更新）が失敗したら、2 つ目は打たずに失敗として扱う。

```bash
claude plugin marketplace update <マーケットプレイス> --json
claude plugin update ndf@<マーケットプレイス> -y --json --scope <スコープ>
```

- **更新に失敗しても止まらない。** コマンドが 0 以外で終わる・時間切れ・起動できない・出力が読めないときは、
  `log.jsonl` に `result` が `failed` の行を残し、端末へ
  `ndf-relay: プラグインを更新できなかった（<理由>）。<バージョン> で次のセグメントを起動する` の 1 行を出して、
  起動先（下の確認で読んだバージョン。読めなければ「不明なバージョン」）で次のセグメントを起動する。
  起動先のメジャーバージョンが違う・停止シグナルファイルがあるなどで起動しないときは、この 1 行を出さない（`failed` の行は残す）
- **プラグインのキャッシュから動いていないラッパーは更新しない。** `--plugin-dir` や開発のチェックアウトの `bin/ndf relay run`
  で起動したときと、一致する導入が 2 件以上（同じ導入先を複数のスコープが指す）のとき、一致する導入が無く同じキャッシュの下の
  有効な導入も 2 件以上あるときは、コマンドを打たず `result` が `skipped` の行を残す
- **導入元は錠を待つ前に決めて保持する。** 待つ間に先のラッパーが同じ導入を上げて導入先が変わっても、同じ ID・スコープの導入を更新する。
  保持した導入が一覧から消えた・無効になったときは、コマンドを打たず `skipped` の行を残し、その導入のバージョンを起動先として扱わない
- **同じ設定の保存先（`${CLAUDE_CONFIG_DIR:-~/.claude}`）を使うラッパーは 1 本ずつ更新する。** 錠は `<設定の保存先>/ndf/plugin-update.lock`。
  後のラッパーは先の更新の後に打つので `up_to_date` で終わる。上限（`contracts/limits.json` の `relay.plugin-update`、120 秒）まで
  錠を取れなければ `failed` として続ける。コマンドもそれぞれ同じ上限で打ち切る
- **起動先の確認。** 更新の後と、更新しない手の起動の前に、導入の一覧を読み直して次の子が読むバージョンを決め、`launched` に書く。
  更新しない手では、`plugin_update` の行の `result` は `skipped` になる

### 変更が効く時点

| 変更の場所 | 効く時点 |
| --- | --- |
| hook と Skill | 次のセグメントから（次のセグメントの claude が更新後の導入先を読む） |
| ラッパー自身のコード（`ndf relay`） | `/exit` でラッパーを抜けて `claude` を打ち直した後から |

**ラッパー自身のコードは、動いている間は入れ替わらない。** 次のセグメントの SessionStart hook が `relay.root` を
更新後のバージョンへ向け直す（「[始め方](#始め方)」）ので、利用者が次に `claude` を打ち直すと更新後のバージョンのラッパーが起動する。
11 系どうしでバージョンの違うラッパーが並んで動いていても、ラッパーの起動・`ndf relay spawn`・`ndf relay restart` は
バージョンの違いを理由に断らない。断るのは、旧い NDF（10 系）かメジャーバージョンの違うラッパーが動いているときだけである。

**メジャーバージョンが変わったら、次のセグメントを起動せずに止まる。** 起動先のメジャーバージョンがラッパーのメジャーバージョンと
違う（例: 11.0.8 のラッパーで、導入が 12.0.0 へ上がった）ときは、更新した手でも更新しない手でも、別のラッパーが上げた場合でも同じである。
ラッパーは `relay.root` を起動先へ向け直してから、端末へ次の 1 行を出して終わる（停止理由 `precondition`、終了コード 3）。
`<ラッパー>` と `<起動先>` にはバージョンをそのまま入れ（例: `11.0.8 から 12.0.0`）、`<起動先の導入先>` には起動先のプラグインの根のパスを入れる。

| 場合 | 1 行 |
| --- | --- |
| `relay.root` を向け直せた | `ndf-relay: メジャーバージョンが <ラッパー> から <起動先> に変わった。次のセグメントを起動しない。claude を打ち直す` |
| 複製が無い（`ndf relay install` をしていない）・向け直せない | `ndf-relay: メジャーバージョンが <ラッパー> から <起動先> に変わった。次のセグメントを起動しない。<起動先の導入先>/bin/ndf relay install を打ってから claude を打ち直す` |

案内どおり `claude` を打ち直すと、新しいメジャーバージョンのラッパーで続く。同じメジャーバージョンの中では止まらずに起動する。

動いているラッパーのバージョンは、`<状態の保存先>/relay/wrappers/<名前>/wrapper.json` の `version` で見られる
（保存先は「[記録の読み方](#記録の読み方)」）。

### 自動更新を止めて、ラッパーが自分で上げる経路を確かめる

Claude Code は、自動更新が有効なマーケットプレイスのプラグインを、ラッパーと関係なく裏で上げる
（[公式: plugins/loading](https://code.claude.com/docs/en/plugins/loading)）。外で先に上がると、ラッパーが自分で上げる
（`result` が `updated`）行は残らない。その経路をリリースの後に確かめるときは、次の順で自動更新を止めて戻す。
NDF のコードは自動更新の設定を読みも書きもしない。

| 項目 | 中身 |
| --- | --- |
| 既定と決まる順 | サードパーティのマーケットプレイス（`ai-plugins` を含む）は既定で無効（公式のマーケットプレイスと claude.ai から足したものは有効）。設定ファイルの `extraKnownMarketplaces.<名前>.autoUpdate` → `known_marketplaces.json` の `autoUpdate` → 既定、の順で決まる（[公式: plugins/loading](https://code.claude.com/docs/en/plugins/loading)・[plugins/install](https://code.claude.com/docs/en/plugins/install)・[plugins/org](https://code.claude.com/docs/en/plugins/org)）。設定ファイルどうしは管理設定 → ローカルのプロジェクト（`.claude/settings.local.json`）→ 共有のプロジェクト（`.claude/settings.json`）→ 利用者（`${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json`）の順に勝つ |
| 対象のスコープ | この手順が止めて戻すのは、プロジェクトの設定ファイル（`.claude/settings.local.json`・`.claude/settings.json`）・利用者の設定ファイル・`known_marketplaces.json` のうち、値を変えた場所である。下の 1 つ目の `jq` はこの 4 か所を読む（管理設定は読まない）ので、その `effective` が実効値になるのは、管理設定が `extraKnownMarketplaces` に `ai-plugins` を設定していないときだけである |
| 上のスコープを先に見る | 止める前に、ラッパーと同じ作業ディレクトリで下の「上のスコープを見る」コマンドを打ち、Claude Code の中で `/status` を打って `Setting sources` の行を見る（[公式: managed-settings](https://code.claude.com/docs/en/managed-settings#read-the-source-in-status)）。`Enterprise managed settings` が無ければ、下の「作業ディレクトリの信頼を見る」を済ませてから、プロジェクトのファイルに設定があってもなくても、下の「止める前に残す」へ進んで 4 か所の値を書き留める（信頼済みならプロジェクトの設定はそこで値として残り、「止める」で最も勝つファイルを変える）。`Enterprise managed settings (file)`・`(drop-ins)`・`(file + drop-ins)` なら、コマンドが読む管理設定のファイルの行で値を見る。`(remote)`・`(plist)`・`(HKLM)` など手元のファイルで読めない管理設定なら、管理者に `extraKnownMarketplaces.ai-plugins.autoUpdate` の値を確かめる |
| 上のスコープが設定していたら | 管理設定が `autoUpdate` を持てば、その値が勝ち、利用者の `/plugin` の切り替えは `Auto-update for 'ai-plugins' is set by` で断られる。`true` ならこの端末では止められないので、管理設定の無い端末で確かめるか、管理者に `false` にしてもらう（戻すのも管理者）。`false` なら止めずに進める。管理設定が `ai-plugins` を設定して `autoUpdate` を持たなければ、利用者の設定ファイルの同じ名前の設定は置き換えられて効かないので、`/plugin` の Marketplaces タブで切る（`known_marketplaces.json` に残る）。プロジェクトのファイルが `autoUpdate` を持てば、利用者の設定ファイルの代わりにそのファイル（両方が持てば、勝つ `.claude/settings.local.json`）の値を止めて戻す対象にする。下の 1 つ目の `jq` はプロジェクトの 2 ファイルも読むので、バックアップと戻した後の確かめにそのまま使える |
| 作業ディレクトリの信頼を見る | Claude Code は、信頼していない作業ディレクトリではプロジェクトの設定ファイルの `extraKnownMarketplaces` を無視する（[公式: plugins/org](https://code.claude.com/docs/en/plugins/org#require-plugins-per-repository)）。プロジェクトの 2 ファイルを判定に使う前に、ラッパーと同じ作業ディレクトリで `claude` を対話で起動し、信頼を問うダイアログ（`Do you trust the files in this folder?`）が出ないことを見る。出たら未信頼である。そのまま信頼してよいなら受け入れてから進む。信頼しないなら、下の 1 つ目の `jq` を `trusted=no` を付けて打つ（プロジェクトの 2 ファイルを `"（未信頼で無視）"` と出し、`effective` の決定から外す）。例えば共有のプロジェクトが `false`・利用者が `true` のとき、未信頼のまま `trusted=no` を付けずに打つと `effective: false` と出るが、実際は自動更新が有効のままである。未信頼なら「止める」「戻す」の対象もプロジェクトのファイルでなく、利用者の設定ファイルか `known_marketplaces.json` にする |
| 止める前に残す | ラッパーと同じ作業ディレクトリで下の 1 つ目の `jq` を打ち、ローカルのプロジェクト（`local`）・共有のプロジェクト（`project`）・利用者（`settings`。`${CLAUDE_CONFIG_DIR:-~/.claude}/settings.json`）の `extraKnownMarketplaces.ai-plugins.autoUpdate` と `known_marketplaces.json`（`known`）の `ai-plugins.autoUpdate` の、それぞれの値とキーの有無（`（キー無し）`）、4 か所から決まる値を書き留めてバックアップにする。どのスコープを変えても、変える前の値がここに残る。戻すときはこのバックアップに合わせる |
| 止める | 設定ファイルにキーがあれば、キーを持つうちで最も勝つファイル（上の順）を `false` にする（`known_marketplaces.json` だけを変えても、設定ファイルの値が勝つので止まらない）。キーが無ければ `/plugin` の Marketplaces タブで `ai-plugins` の自動更新を切るか、設定ファイルにキーを `false` で足す |
| 止まったかを見る | 下の 1 つ目の `jq` の `effective` が `false` であることを見る（管理設定が設定していないときに限る。設定していれば、その値を見る）。1 つのファイルだけを見ない（設定ファイルが `false` なら `known_marketplaces.json` が `true` でも止まっている。設定ファイルが `true` なら `known_marketplaces.json` が `false` でも止まっていない） |
| 確かめる | リリースの前に止める → リリースの後、カットポイントを通す直前にそのラッパーの `log.jsonl` の行数を残す → ラッパーの下でカットポイントを 1 回通す → その後に追記された行のうち `plugin_update` の行が `result: updated`・`from`=旧・`to`=新・`launched`=新・`wrapper`=旧であることを見る（下の「確かめる `jq`」） |
| 戻す | バックアップのとおりに戻す。キーがあった場所はバックアップの値を書き戻し、キーが無かった場所に足したキーは消す（`true` を書かない。`jq 'del(.extraKnownMarketplaces["ai-plugins"].autoUpdate)'` の出力を元のファイルへ書き戻す）。`/plugin` で切ったときは同じ画面で戻す。最後にラッパーと同じ作業ディレクトリで下の `jq` を打ち、4 つの場所の値とキーの有無、実効値がバックアップと同じであることを見る |

上のスコープを見るコマンド（ラッパーと同じ作業ディレクトリで打つ。管理設定のファイルは Linux・WSL の保存先で、macOS では
`/Library/Application Support/ClaudeCode/` の下を読む）。ファイルごとに `ai-plugins` の設定を出し、無いファイルは出さない。

```bash
for f in .claude/settings.local.json .claude/settings.json /etc/claude-code/managed-settings.json /etc/claude-code/managed-settings.d/*.json; do
  if [ -e "$f" ]; then printf '%s: ' "$f"; jq -c '.extraKnownMarketplaces["ai-plugins"] // "（設定無し）"' "$f"; fi
done
```

止まったかと戻ったかを見る `jq`（ラッパーと同じ作業ディレクトリで打つ。プロジェクトの 2 ファイル・利用者の設定ファイル・`known_marketplaces.json` の値とキーの有無、4 か所から決まる値を出す）。無いファイルだけを空の設定（`{}`）として読む。
読めないファイル（権限・ディレクトリ・切れたシンボリックリンク）と JSON でないファイルは、空と読まずに 0 でない終了コードで止まる。
作業ディレクトリが未信頼なら、先頭に `trusted=no` を付けて打つ（「作業ディレクトリの信頼を見る」）。このときプロジェクトの 2 ファイルは
開かずに空の設定として渡すので、Claude Code が無視するファイルが読めなくても JSON でなくても止まらない。

```bash
cfg="${CLAUDE_CONFIG_DIR:-$HOME/.claude}"
read_cfg() { if [ -e "$1" ] || [ -L "$1" ]; then cat -- "$1" || { echo "設定を読めない: $1" >&2; return 1; }; else echo '{}'; fi; }
if [ "${trusted:-yes}" = no ]; then l='{}' p='{}'; else l=$(read_cfg .claude/settings.local.json) && p=$(read_cfg .claude/settings.json); fi &&
s=$(read_cfg "$cfg/settings.json") && k=$(read_cfg "$cfg/plugins/known_marketplaces.json") &&
jq -n --argjson l "$l" --argjson p "$p" --argjson s "$s" --argjson k "$k" --arg trusted "${trusted:-yes}" '
  def at($e): ($e // {}) | if has("autoUpdate") then {v: .autoUpdate} else {} end;
  def proj($e): if $trusted == "no" then {skip: true} else at($e) end;
  [proj($l.extraKnownMarketplaces["ai-plugins"]), proj($p.extraKnownMarketplaces["ai-plugins"]),
   at($s.extraKnownMarketplaces["ai-plugins"]), at($k["ai-plugins"])] as $all
  | ($all | map(if has("v") then .v elif has("skip") then "（未信頼で無視）" else "（キー無し）" end)) as $shown
  | {local: $shown[0], project: $shown[1], settings: $shown[2], known: $shown[3],
     effective: ([$all[] | select(has("v")) | .v] + [false])[0]}'
```

例: 設定ファイルがどれも無く `known_marketplaces.json` が `true` なら
`{"local": "（キー無し）", "project": "（キー無し）", "settings": "（キー無し）", "known": true, "effective": true}`、
利用者の設定ファイルが `false` なら `{…, "settings": false, "known": true, "effective": false}`、`.claude/settings.local.json` が
`true` で利用者の設定ファイルが `false` なら `{"local": true, …, "settings": false, …, "effective": true}`、どれも無ければ
すべて `"（キー無し）"` で `"effective": false`（いずれも終了コード 0）。バックアップを取るときと戻した後に
見るときは、出力が JSON で終了コードが 0 であることを見てから値を読む。0 でなければ、標準エラーに出たパスのファイルを読めるように
してから打ち直す。実効値の最後の `false` はサードパーティの既定（無効）である。`jq` の `//` は `false` も欠けとして扱うので、
決まる順は `has` で比べる。

確かめる `jq`（リリースの後のカットポイントの行）。保存先は「[記録の読み方](#記録の読み方)」と同じ規則で、ラッパーと同じ
`NDF_STATE_HOME`・`XDG_STATE_HOME` の下で求める。読むのは今回カットポイントを通したラッパー 1 本の `log.jsonl` だけで、
`<名前>` にはその名前（`NDF_RELAY_NAME`、無ければ `w<ラッパーの pid>`。`ndf relay ls` に出る）を、`<新しいバージョン>` には
リリースしたバージョンを入れる。そのラッパーの下のセッションの中なら、`log` は `"$NDF_RELAY_DIR/log.jsonl"` でもよい。
`log.jsonl` は追記だけで、同じ名前のラッパーで同じリリースを確かめ直すと前の試行の成功行も `to` の条件を満たす。そこで
カットポイントを通す直前に行数を `n` に残し、通した後は `n` 行より後ろに追記された行だけを読む。そのうち `to` が今回の
バージョンの行だけを出す。1 行も出なければ、今回のカットポイントで自分で上げる経路は確かめられていない。

```bash
# カットポイントを通す直前（同じシェルで続けて打つ）
state="${NDF_STATE_HOME:-${XDG_STATE_HOME:-$HOME/.local/state}}/ndf"
log="$state/relay/wrappers/<名前>/log.jsonl"
n=$(if [ -e "$log" ]; then wc -l < "$log"; else echo 0; fi)
# カットポイントを 1 回通した後
tail -n "+$((n + 1))" "$log" | jq -c --arg to '<新しいバージョン>' 'select(.kind == "plugin_update" and .result == "updated" and .to == $to) | {at, result, wrapper, from, to, launched, outside}'
```

## 承認ゲートを越えない守り

**ラッパーは、質問（`AskUserQuestion`）の回答を代わりに送らず、回答待ちの質問がある間は子を止めない。** 子の端末へ書くのは
利用者の入力を渡すときだけで、切り替え・起動し直し・引継ぎはどれも子を強制終了してから `claude` を起動し直して行う。
質問の表示中に端末へ何かを書けば選択肢 1 を決めてしまい、子を止めれば質問が消えるためである。

例: 子が `ndf-next` のブロックを書いた後に設計承認ゲートの質問を出すと、ラッパーは質問を 1 回だけ人かオーガナイザーへ転送し、
回答されるまで次のセグメントを起動しない。回答で質問のファイルが消えた後の見回りで切り替える。

**ラッパーは見回りごとに、次の順で最初に当たった条件の手を打つ**（`switch_decide.decide`）。

| 順 | 条件 | 手 |
| --- | --- | --- |
| 1 | 回答待ちの質問がある（質問のファイルが在る。壊れていても・読めなくても在るとみなす） | まだ転送していない質問なら 1 回だけ転送する（`forward`）。転送した後は待つ（`wait`）。子を止めず、子の端末へ書かない |
| 2 | 利用上限・認証の失敗が画面に出た | 別のアカウントで同じ会話を起動し直す・上限が解けるのを待つ・`stalled` にする |
| 3 | 次のセグメントのマーカー（`next.json`）がある | 静かな時間を待ってから、新しいセッションで次のセグメントを起動する |
| 4 | 子が終わった | 終了コード 0 なら終わる。それ以外は同じ会話を起動し直す |
| 5 | 文脈の量が上限を超えた | 子を止め、同じ会話を引継ぎを頼む文で起動し直す（`--resume <同じ ID> <文>`）。1 つのセグメントで 1 回だけ |
| 6 | 子の出力が止まりの秒（`relay.stall`）止まった | 同じ会話を起動し直す |

**質問の hook とラッパーは、同じロック（作業ディレクトリの `question.json.lock`）で順番をそろえる。**

- 質問の hook（`ask-forward`、PreToolUse）は、ロックの中で今の起動の記録（`launch.json`）と照らして質問のファイル（`question.json`）を書く。
  中継の下で書けなければ質問を拒否する（モデルが呼び直す）。この guard の失敗は止める側へ倒す
- ラッパーは子を止める前に、ロックを待たずに取り、質問のファイルが無いと確かめてから、ロックを持ったまま子を止め終える。
  ロックを取れない・質問のファイルが在るときは何もせず、次の見回りで決め直す。止めたら止めた子の起動を今の起動から外すので、
  止めた子の遅れた hook は次の子の質問を書けない
- 強制終了しても `relay.child-exit` の秒のうちに子が終わらなければ、次の子を起動せずにラッパーが止まる
- 設定（マーカー・アカウント）が壊れたときも、回答待ちの質問がある間は子を止めず `waiting` にして待ち、確かめが通るか子が終わってから止まる

**質問が閉じたことは hook が知らせ、ロックの中で質問のファイルを消す。**

| 閉じ方 | 消す hook |
| --- | --- |
| 選択・自由入力で回答した | `ask-answered`（PostToolUse）が、同じ `tool_use_id` の質問を消す |
| 回答せずに閉じた（Chat about this の後に応答を終えた） | `ask-closed`（Stop）が消す |
| Esc で閉じた | `ask-closed`（UserPromptSubmit）が、人が次に入力を送ったときに消す。それまでは切り替えない |

hook が消せなかった質問は、次の閉じる事象か子の終わりまで残り、その間ラッパーは子を止めない。ラッパーが自分で消すのは、
子が終わったときと、質問が今の子のものでないとき（`log.jsonl` の `stale_question` の行）だけである。

**Stop の hook は、承認を本文で待って終えた応答とマーカーを次のように扱う。**

- `stop-wait`: 最後の応答が `AskUserQuestion` を使わずに本文で人の回答を待つなら、変更を要求して `AskUserQuestion` で問わせる。
  変更要求の上限の後は通して待ちの知らせを書く
- `relay-marker`: 最後の応答が `ndf-next` のブロックを持てば、マーカーを書く。背景の作業が残る間はマーカーを書かずに変更を要求し、
  終わるのを待たせる。マーカーを書いた後の利用者の入力では取り消さない（その後に出た質問は順 1 で守る）

守りのうちラッパー自身のコードが受け持つ部分の変更は、`/exit` でラッパーを抜けて `claude` と打ち直した後から効く。hook が受け持つ部分の
変更は、次のセグメントから効く（「[プラグインの更新とラッパーのバージョン](#プラグインの更新とラッパーのバージョン)」の「変更が効く時点」）。

## 上限

| 上限 | 既定 | 変え方 | 超えたとき |
| --- | --- | --- | --- |
| 再起動ループの検出（起動から短い時間で次のセグメントへ移ったセグメントの続き） | 3 つ・120 秒 | `contracts/limits.json` の `relay.spin`（`count`・`seconds`）、環境変数 `NDF_RELAY_SPIN`（秒） | 次のセグメントを起動する前に、今のセグメントを含めて起動から設定後の `seconds` 秒（`NDF_RELAY_SPIN` があればその値）未満のセグメントの続きを数え、`count` に達していれば起動しない（既定の 3 つ・120 秒では、短いセグメントが 2 つ続いた後の 3 つ目から 4 つ目への起動を止める）。子が動いていれば今の会話のまま中継を続け（そのセグメントが起動から `seconds` 秒に達するまで区切り・替えでも起動しない）、子が終わっていればラッパーが子の終了コードで終わる |
| 静かな時間（子の出力も利用者の入力も無い秒数） | 5 | `contracts/limits.json` の `relay.quiet` | 子の出力と利用者の入力がこの秒数止まるか、次のセグメントのマーカーを書いてからこの秒数がたつまで、子を止めず次のセグメントを起動しない。利用者が子へ入力すると、出力が止まっていても静かな時間を数え直す。マーカーを書いてからの秒数は数え直さないので、待つのは長くてもマーカーを書いてから静かな時間まで |

**数えるのは、起動から短い時間で終わったセグメントの続きである。** カットポイントの区切り（`ndf-next` のブロック・`/ndf:restart`）、
子が 0 以外で終わった・出力が `relay.stall`（30 分）止まったときの起動し直し、文脈の上限で引継ぎを頼む起動し直し、続きの中で
既に起動した起動先へアカウントを替える起動を数える。続きの中で初めて使う起動先（アカウント・従量の接続）への替えは数えないので、
上限で次々にアカウントを替えて続ける正しい替えでは当たらない（短くても数を足さず、それまでの数はそのまま残す）。
起動から `seconds` 秒以上動いたセグメントがあれば、数を 0 に戻す。`seconds` と `count` はどれも設定後の値で、例えば `NDF_RELAY_SPIN=30` なら 30 秒以上動いたセグメントで数を 0 に戻し、保持も解く。1 日の起動の回数の
上限は持たない。`ndf relay restart` は名前つきのラッパーの数（`relay.max-named`）だけで断り、起動の回数では断らない。
`NDF_RELAY_SPIN` は 0 以上の数で、0 で検出を切る。数でない値と負の値は誤りになる（ラッパーを起動せず、`ndf relay restart` は対象を止めずに断る）。

**文脈の上限（`NDF_CONTEXT_LIMIT`、既定 200,000）はラッパーの下で強くなる。** ラッパーの直接の子の
conductor では、コンテキスト量の hook が工程へ入る起動を 1 度の通しなしに止め続ける。conductor は動いて
いる supervisor の報告を待ってから、引継ぎ文書を更新し、ブロックを出して終える
（[context-window.md](context-window.md) の「上限を超えたら hook が止める」）。バックグラウンドの処理
（supervisor を含む）が動いているあいだの応答ではシグナルファイルを書かない。

## 利用上限でアカウントを替えて続ける

claude のアカウントを 2 つ以上登録しておくと、利用上限で止まらずに別のアカウントで続く。登録・指定・切り替えの条件と設定は [relay-accounts.md](relay-accounts.md) にある。

## カットポイントの引継ぎ文書と ndf-next

**カットポイントでは、conductor が引継ぎ文書の「現在地」と「次にやること」を書き直し、`ndf records handoff check <本体> --trim --history <履歴>`
で形と行数を確かめてから、再開コマンドを `ndf-next` のブロックで出して応答を終える。** 引継ぎ文書の保存先・名・セクションの形・
規則は [handoff.md](handoff.md) が SSOT である。スプリントの進み具合は文書へ写さず、課題と Pull Request の URL と、スプリントの
ステートマシン（`ndf progress show <ID>`）を指す。

- **カットポイントの再起動は承認ゲートに数えない。** `ndf-next` のブロックの前に承認・確認（`AskUserQuestion` を含む）を挟まない
- 引継ぎを始めた時点と、hook が止めた理由は `ndf relay context record` で記録する（[context-window.md](context-window.md) の「判定と再起動を記録する」）
- **次のセッションを止めずに続けるには、`/goal` のテンプレートを特定の課題に縛らない。** テンプレートには「効果の順の残りから次の
  スプリントを選び、最後に同じテンプレートで `ndf-next` を出す」ことを書く。止まるのは 2 つの承認ゲートだけになる

## 落ちたときの続け方

ラッパーが次のセッションを起動しないと決めたときと、プラグインを更新できなかったときは、`ndf-relay:` で始まる 1 行が画面に出る。

| 1 行 | 状態 | 続け方 |
| --- | --- | --- |
| `次のセグメントを起動しない（停止シグナルファイルがある）。このまま続けるか、/exit する` | 停止シグナルファイルがある。今のセッションは動いたまま | そのまま続けるか、`/exit` してから conductor のブロックの中身で `claude` を起動する |
| `プラグインを更新できなかった（<理由>）。<バージョン> で次のセグメントを起動する` | 更新に失敗した。ラッパーは止まらず、そのバージョンで次のセグメントを起動した | 何もしなくてよい。ラッパーが次のカットポイントで更新を打ち直す |
| `メジャーバージョンが <ラッパー> から <起動先> に変わった。次のセグメントを起動しない。…` | ラッパーは終わった（終了コード 3）。`relay.root` は起動先へ向け直してある | 1 行の案内どおり `claude` を打ち直し、conductor のブロックの中身を最初の入力にする |
| `子の claude を起動できない（<理由>）` | ラッパーは終わった（終了コード 3）。子は起動していない | 理由（作業ディレクトリが無い・`claude` が PATH に無いなど）を直してから、conductor のブロックの中身で `claude` を打ち直す |
| `起動から <秒> 秒未満で次のセグメントへ移ったセグメントが <数> つ続いた（再起動ループ）。次のセグメントを起動しない。…` | 再起動ループの検出（「[上限](#上限)」）に当たった。今のセッションは動いたまま | 原因を直してから `/exit` し、`ndf relay` で起動し直す |
| 上の 5 つ以外（`ndf-relay: <理由>`） | ラッパーは終わった。終了コードは停止理由の種類（`contracts/causes.json`）のとおり。再起動ループの検出で、子が終わった後に止まったときもこの行になる（終了コードは子の終了コード） | 理由を直してから、conductor のブロックの中身で `claude` を起動する |

## fork したセッションで進めたとき

例: ラッパーがセッション 2 を子 pid 1883（セッション ID `ba764798`）で起動し、利用者が別のエントリポイントから同じ会話を
続けたため、会話が fork したセッション `38cf7ead`（`claude daemon` → `bg-pty-host … --fork-session`
の下にあり、親はラッパーでない）で進んだ。

**ラッパーは `child.pid` の会話だけを見る。** fork した側にも `NDF_RELAY_DIR` と `NDF_RELAY_DEPTH` は
受け継がれるが、hook を呼んだ claude がラッパーの直接の子でないため、次のとおりラッパーの外と判定される。

- `ndf relay notice` の `metrics.inside` は 1 を返し、`ndf relay notice` の 1 行目は `outside` になる
- Stop hook の `mark` はシグナルファイルを書かない。`ndf-next` のブロックを出してもセッションは切り替わらない

**アナウンスは `ndf relay notice` の 2 行目が原因と対処を書く。** 外である理由ごとに文が変わる。

| 理由 | 2 行目 |
| --- | --- |
| `NDF_RELAY_DIR` が無い | `/exit してから claude を起動し、下の中身を最初の入力として貼り付ける（/ndf:install-wrapper でラッパーを入れると自動になる）` |
| ラッパーが動いていない | `ラッパーは既に終わっている。/exit してから claude を起動し、下の中身を最初の入力として貼り付ける` |
| ラッパーの直接の子でない（fork したセッション・`bg-pty-host` の下・別のエントリポイント） | `ラッパーは元の会話（子 pid <child.pid>）しか見ていないため、この会話で出した ndf-next は自動では拾われない。元の会話へ戻って同じ ndf-next を出すか、元の会話を /exit してから claude を起動し、下の中身を最初の入力として貼り付ける` |

**続け方は 2 つある。** 元の会話（ラッパーの画面）へ戻って同じ `ndf-next` のブロックを出せば、
ラッパーが切り替える。戻れないときは、元の会話を `/exit` してから `claude` を起動し、ブロックの
中身を最初の入力として貼り付ける。fork した側の会話をラッパーは自動で拾わない。

## セッションをまたいで設定を保つ

**2 つ目以降のセッションは、最初の `claude` に付けた起動オプションを先頭に付け、ブロックの
中身で起動する。** alias（devbase の `--dangerously-skip-permissions` など）や手で付けた
`--model`・`--permission-mode`・`--settings`・`--add-dir`・`--mcp-config`・`--plugin-dir` は
すべてのセッションに効く。**会話ごと・セッションごとの引数は最初のセッションだけに効かせ、引き継がない。**

| 引数 | 最初のセッション | 2 つ目以降 |
| --- | --- | --- |
| 最初のプロンプト（`--` 以後を含む） | 最初の入力にする | 付けない（ブロックの中身が入力） |
| `-r`/`--resume <ID>` | その会話を `--resume` で続ける | 付けない |
| `--session-id <ID>` | その ID で始める | 付けない（新しい ID） |
| `-n`/`--name <名前>` | 付ける | 付けない |
| 「ラッパーを挟まない起動」の表の会話の選び方 | ラッパーを挟まない | — |

次のセッションは新しい会話を始めるので、付けると前の会話へ戻るか、同じ ID を 2 度使うか、セッションが端末に出ない。
引数と値の区切りは `claude --help` と同じ規則で読み（`--名前=値` は 1 語、値を複数取るオプションは次のオプションまで）、
知らない選択肢は値ごと引き継ぐ。

## 記録の読み方

ラッパー 1 本ごとに `<状態の保存先>/relay/wrappers/<名前>/log.jsonl` へ 1 行ずつ残る。状態の保存先は
`${NDF_STATE_HOME:-${XDG_STATE_HOME:-~/.local/state}}/ndf`（既定は `~/.local/state/ndf`）で、名前は
`NDF_RELAY_NAME`、無ければ `w<ラッパーの pid>` である。どの行も `at`（時刻）・`section`（その時点のセグメントの番号。
最初のセグメントの前は null）・`kind`（行の種類）を持つ。

| `kind` | いつ | ほかのキー |
| --- | --- | --- |
| `section` | セグメントの claude を起動した | `session_id`・`account`（起動したアカウント。指定が無ければ null）・`resume`（同じ会話を `--resume` で続けたら真） |
| `plugin_update` | プラグインを更新した・更新しない手で起動先を確かめた（「[プラグインの更新とラッパーのバージョン](#プラグインの更新とラッパーのバージョン)」） | `result`（`updated` / `up_to_date` / `skipped` / `failed`）・`from`・`to`（更新の前と後のバージョン。分からなければ null）・`launched`（次の子が読む起動先のバージョン。確かめられなければ null）・`reason`（1 行の理由。導入先のパスを含めない）・`seconds`（錠を待ち始めてから起動先を確かめ終わるまでの秒）・`wrapper`（ラッパー自身のバージョン）・`outside`（ラッパーの外で上がっていたか。下の「外で上がったかの読み方」） |
| `wait`・`forward`・`wait_reset`・`stalled`・`switch`・`account`・`restart`・`handover`・`finish`・`stop` | 切り替えの判定の結果（手の種類）。理由が前の見回りと変わったときだけ書く | `reason` |
| `account` | カットポイントで使用率を見てアカウントを替えた（上の判定の行と同じ種類の名前） | `reason` |
| `halt` | 停止シグナルファイルがあり、次のセグメントを保留した | `reason` |
| `stale_next` | 前の実行のセグメントが書いたマーカーを消した | `reason` |
| `plan_question` | 計画のレーンで、質問を計画のリクエストへ載せた・載せられなかった | `ok`・`reason` |
| `end` | ラッパーが終わった | `cause`（停止理由の種類。済んだときは空）・`reason` |

```bash
state="${NDF_STATE_HOME:-${XDG_STATE_HOME:-$HOME/.local/state}}/ndf"
cat "$state"/relay/wrappers/*/log.jsonl | jq -c 'select(.kind == "end")'
cat "$state"/relay/wrappers/*/log.jsonl | jq -c 'select(.kind == "switch" or .kind == "section")'   # 切り替えと起動
cat "$state"/relay/wrappers/*/log.jsonl | jq -c 'select(.kind == "plugin_update") | {at, section, result, from, to, launched, reason}'
```

**外で上がったかの読み方。** `outside` は、ラッパーが前に知っていたバージョン（最初はラッパー自身のバージョン、以後は前の行の
`launched`）と、この行の更新の前のバージョン（`from`。null なら `launched`）を比べ、更新のコマンドが成功した行では
`to` と `launched` も比べて決まる。

| `outside` | 読み方 |
| --- | --- |
| 真 | ラッパーの外（Claude Code の自動更新・手で打った更新など）で導入が上がっていた |
| 偽 | 外では上がっていない。`result` が `updated` なら、ラッパーが自分で上げた。ラッパーが自分で上げた後の次の行も偽になる |
| null | 判定できない（比べる値が無い・更新のコマンドが失敗した行でバージョンが変わった）。「外で上がっていない」とは読まない。キーの無い行（このキーを書く前のラッパーの行）も、`jq` では null として読まれる |

外で上がった行は 1 行目の `jq` で出す（`outside` が真の行だけが返る）。判定できない行は 2 行目で並べて見る。

```bash
state="${NDF_STATE_HOME:-${XDG_STATE_HOME:-$HOME/.local/state}}/ndf"
cat "$state"/relay/wrappers/*/log.jsonl | jq -c 'select(.kind == "plugin_update" and .outside == true) | {at, result, wrapper, from, to, launched, outside}'
cat "$state"/relay/wrappers/*/log.jsonl | jq -c 'select(.kind == "plugin_update" and .outside == null) | {at, result, wrapper, from, to, launched}'
```

入出力のコントラクトと決定の理由は、NDF のリポジトリの確定仕様 `docs/specifications/ndf-relay-segment-restart.md` にある。

## 付則: `/goal` を付けた場合

セッションの最初の入力に `/goal ` を付けると（`/goal /ndf:development-workflow #895`）、その入力は Claude Code の
目標になる。付けない場合との違いは次の表のとおりで、ラッパーの切り替えは目標の判定を見ない。

| 項目 | 振る舞い |
| --- | --- |
| 次のセッションへ引き継ぐ | `ndf-next` のブロックの中身の先頭に `/goal ` を付ける（[context-window.md](context-window.md) の「新しい会話で戻す」） |
| 目標が未達のとき | 判定が止めを拒んで応答が続いても、最後の応答が `ndf-next` のブロックを持てば切り替える（目標の判定を待たない）。子の出力が静かな時間（`contracts/limits.json` の `relay.quiet`）止まるか、ブロックを最初に書いてから静かな時間が過ぎたら、子のプロセスを終わらせて次のセグメントを起動する。Esc や `/exit` は書かない。回していない `AskUserQuestion` の質問か、停止シグナルファイルがあれば切り替えない |
| `/ndf:restart` の引数が無いとき | 目標の入力をそのまま再開コマンドにする |
| 次のセッションで条件の Skill を読み込む | 条件の先頭の語が `/<プラグイン>:<Skill>` のとき（`/goal /ndf:development-workflow #895`）、Claude Code は作業の最初の Tool の呼び出しで、その Skill を条件の残りを引数に Skill ツールで読み込む（2026-11-19 に 11.0.4 で確かめた。#3817）。hook の案内は無い。最初の入力と目標の条件は前のセッションと同じ文面のままでよい |
