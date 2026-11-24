# 報告の保存先: supervisor が `ndf worker prepare` の 1 行で用意し、確認のダイアログでレーンが止まらない

## 概要

supervisor が worker へ作業を出すとき、worker が報告を書くファイル（保存先）・完了マーカー・作業の結果ファイルを
前の作業の残りが無い状態にしてから渡す。この用意を `ndf worker prepare --dir <作業ディレクトリ> --name <worker の名前>` の
1 回の呼び出しが行い、報告の書き込みは `ndf worker append`、報告の読み取りは `ndf worker collect` が行う。
supervisor と worker が打つ Bash に、パスを組み立てるシェルの変数も `rm` も現れない。

あわせて、空になりうる変数をパスに含む `rm` を `PreToolUse` の guard（`rm-variable`）が止め、書き直しの形を
ツールの結果として返す。Claude Code が端末の画面に出す確認のダイアログはモデルへ渡らないため、ダイアログが
開くとレーンは人の入力を待って止まる。guard は確認のダイアログより先に働くので、止まりがダイアログから
ツールの結果へ替わる。

3 つのサブコマンドは、報告の保存先の読み書きをすべてシンボリックリンクとハードリンクをたどらない経路で行う。
作業ディレクトリは supervisor が渡す任意のディレクトリで、その中の名前が作業ディレクトリの外のファイルを
指していても、その先を読まず、書かず、消さない。

## 用語

| 用語 | 意味 |
| --- | --- |
| 報告の保存先 | 1 人の worker が読み書きする 4 つのファイル（起動指示・報告・完了マーカー・作業の結果ファイル）のパスの組 |
| 保存先 | 起動指示の欄の名前。報告のファイル（`<worker の名前>.report.md`）の絶対パスを指す |
| 完了マーカー | worker が報告を書き終えたことを示す空のファイル（`<保存先>.done`）。supervisor はこのファイルが現れるのを待つ |
| 作業の結果ファイル | worker の作業の成否を持つ結果 JSON のファイル（`<worker の名前>.result.json`）。成否はこのファイルの `status` だけで決まる |
| 作業ディレクトリ | supervisor が worker ごとの 4 つのファイルを置くディレクトリ。`ndf worker prepare --dir` で受ける |

## 背景

supervisor が報告の保存先を用意する手順は、手順書の 2 行（`agent-layers.md` の起動指示の「保存先」の欄と
`waiting.md` の待ち方）が `: > <保存先>; rm -f <保存先>.done` として持っていた。supervisor は worker ごとの
分をまとめて用意するため、この 2 つをシェルの変数とループで書く。その形が Claude Code の
「空になりうる変数のパスへの `rm`」の確認に当たり、確認のダイアログが開いている間、そのツールの呼び出しは
結果を返さない。ラッパーのプロセスは動いたままなのでレーンの生死の判定は「動いている」のままで、利用者か
オーガナイザーが画面を見るまで誰も気づかない。

**手順書の文言では振る舞いを保証できない。** 症状を知っている supervisor でも、変数とループで書けば同じ確認に
当たる。そこで、パスの決め方とファイルの始末をコードへ移し、手順書はコマンド 1 行だけを書く。

**パスの決め方は 1 つの関数が持つ。** supervisor がサブエージェントで worker を起動する経路と、工程のプランが
`ndf answer work` で起動する経路の両方が同じ関数を呼ぶ。経路ごとにパスを組むと、片方だけが変わって
前の作業の完了マーカーを今の作業の完了と読み違える。

**サブコマンドの名前に `slot` を使わない。** `slot` は重い処理の同時実行数のスロットで既に使っている語である。

## 対象範囲

含む:

- 報告の保存先を用意するサブコマンド `ndf worker prepare`、報告へ書き足すサブコマンド `ndf worker append`、
  報告を読むサブコマンド `ndf worker collect`
- パスを組む処理と、リンクをたどらない読み・書き・削除（`plugins/ndf/src/ndf/contracts/worker_paths.py`・
  `worker_paths_open.py`）
- `ndf worker run` の作業の結果ファイルと `ndf contracts emit --out` の書き込みを、同じ経路に寄せること
- 空になりうる変数をパスに含む `rm` を止める guard（`rm-variable`）
- 手順書（`agent-layers.md`・`waiting.md`）がコマンド 1 行だけを書くこと

含まない:

- Claude Code の確認のダイアログそのものの振る舞い
- worker を別のプロセス（`claude -p`）で起動して画面の無い実行にすること
- 止まったレーンを知らせること・自動で起こすこと

## 仕様

### 構成要素と責務

| ファイル | 責務 |
| --- | --- |
| `plugins/ndf/src/ndf/contracts/worker_paths.py` | 4 つのパスの唯一の決め方（`report_paths`・`prepare_paths`・`report_name`）と、開いた作業ディレクトリに対するリンクをたどらない読み・書き・削除（`entry_state`・`read_file`・`write_file`・`staged_write`・`remove_file`・`open_lock`）と、報告の保存先の初期化（`reset_report`） |
| `plugins/ndf/src/ndf/contracts/worker_paths_open.py` | 作業ディレクトリを根から 1 要素ずつリンクを拒みながら開く処理（`open_workdir`・`open_resolved`・`open_or_create`・`open_subdir`）と、開いた場所の同一性の突き合わせ（`dir_id`・`check_dir_id`・`held_dir_id`・`check_unshared`・`check_owned`） |
| `plugins/ndf/src/ndf/core/worker.py` | サブコマンド `prepare`・`append`・`collect` と、`ndf worker run` の結果ファイルの扱い |
| `plugins/ndf/src/ndf/core/worker_append.py` | `ndf worker append` の排他。報告の読み取り・公開と完了マーカーの作成を、呼んだ利用者の追記どうしで直列にする |
| `plugins/ndf/src/ndf/domains/hooks/guards/rm_variable.py` | 空になりうる変数をパスに含む `rm` の判定と拒否 |
| `plugins/ndf/src/ndf/contracts/cli.py` | `ndf contracts emit --out` の書き込みを同じ経路で行う |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md`・`waiting.md` | supervisor と worker が打つコマンド 1 行 |

依存の向きは `contracts` → `core`・`domains` の一方向で、`contracts/worker_paths.py` は標準ライブラリだけに
依存する。guard は hook の判定の部品だけを読み、`worker_paths` を読まない。

### 常に成り立つ条件

- 報告・完了マーカー・作業の結果ファイル・起動指示の 4 つは、同じ作業ディレクトリの直下にあり、名前は
  worker の名前に接尾辞を足したものである。パスは常に絶対パスで返る
- worker の名前は `[A-Za-z0-9._-]` の 1〜64 文字だけを許す。合わない名前はファイルを 1 つも触らずに断る
- どの操作も、作業ディレクトリの直下の 1 要素の名前に対してだけ行う。空・`.`・`..`・区切りを含む名前は断る
- 既存のシンボリックリンクとハードリンクをたどらない。既存の inode を切り詰めず、書き換えない。書き込みは
  私有の一時ディレクトリに作った新しい inode を名前へ公開する形で行う
- 「前の作業の完了マーカーが残っているのに報告が空」という組は、どこで止まっても起きない
- 失敗したときは `metrics.paths` を返さない。supervisor は `status` が `ok` の結果 JSON からだけパスを読む

### 報告の保存先のパス

`report_paths(directory, name)` が 4 つの絶対パスを組む。区切りはすべて `.` でそろえる。

| キー | パス | 使う側 |
| --- | --- | --- |
| `prompt` | `<作業ディレクトリ>/<名前>.prompt.md` | `ndf answer work`（工程のプランの経路）だけ |
| `report` | `<作業ディレクトリ>/<名前>.report.md` | 起動指示の「保存先」 |
| `done` | `<作業ディレクトリ>/<名前>.report.md.done` | supervisor が待つパス |
| `result` | `<作業ディレクトリ>/<名前>.result.json` | 起動指示の「作業の結果ファイル」 |

リンクを解くのは、`ndf worker prepare` が利用者の `--dir` を受ける 1 回だけである。解くのは作業ディレクトリ
までで、名前を足した後の 4 つのパスには当てない。解くときは存在する最も深い要素までを解き、無い残りの要素は
解かずに足す。参照先の無いリンクとリンクの循環は、作る前に断る。`ndf worker append`・`collect` は `prepare` が
返した解決済みのパスを受けるので、親を解き直さずに絶対パスにするだけである（用意の後に親をリンクへ
差し替えられても、リンクの先のパスを開かない）。

`report_name(report)` は逆向きの処理で、起動指示の「保存先」から作業ディレクトリと worker の名前を取り出す。

### 報告の保存先を用意する（`ndf worker prepare`）

```text
ndf worker prepare --dir <作業ディレクトリ> --name <worker の名前>
```

| 引数 | 型・必須 | 意味 |
| --- | --- | --- |
| `--dir` | string・必須 | supervisor の作業ディレクトリ。無ければ作る |
| `--name` | string・必須 | worker ごとの名前。`[A-Za-z0-9._-]` の 1〜64 文字 |

処理の順序は、確かめ → 報告の一時ファイルを作る → 完了マーカーと作業の結果ファイルを一時ディレクトリへ
退避する → 報告を公開する → 退避したものを消す、である。

| 条件 | 結果 |
| --- | --- |
| 作業ディレクトリが無い | 根からたどりながら無い要素だけを作って開く（リンクの先には作らない） |
| 作業ディレクトリがある | 作らずに開く |
| 3 つのどれかがリンク・ディレクトリ・ほかの種類 | 何も変えずに `stopped`（`invalid`） |
| 解いた時点か最初の呼び出しで無かった作業ディレクトリに、3 つのどれかがある | 既存のディレクトリへ差し替えられたとみなし、何も変えずに `stopped`（`invalid`） |
| 完了マーカー・作業の結果ファイルが無い / ある | 無ければ何もしない（`absent`）/ あれば退避し、報告を公開できた後に消す（`removed`）。作らない |
| 報告が無い / ある | どちらも 0 バイトの新しいファイルで置き換える（`created`） |

完了マーカーを報告より先に退避するのは、「前の完了の状態が残るのに前の報告が無い」組を作らないためである。
退避したものを消すのは報告を公開できた後だけで、それより前に止まれば、退避したものを元の名前が空いている
ときだけ元へ戻す。名前が埋まっていれば上書きせずに一時ディレクトリに残し、回収先のパスを `summary` で知らせる。

結果 JSON（`status: ok`）:

| 場所 | 中身 |
| --- | --- |
| `metrics.paths` | `report`・`done`・`result` の 3 つの絶対パス |
| `items` | 3 件。`{"kind": "report" / "done" / "result", "path": <絶対パス>, "action": "created" / "removed" / "absent"}` |
| `summary` | 「報告の保存先を用意した（報告 …・完了マーカー …・結果 …）」 |

同じ `--dir` と `--name` で 2 回打つと、1 回目の完了マーカーと作業の結果ファイルが消え、報告が空になる。
supervisor が 2 回目の完了マーカーを待つとき、1 回目のものを完了と読むことはない。

**返りに結果 JSON の `outputs` を使わない。** `outputs` は工程の文脈のキーの閉じた語彙で、
`plugins/ndf/contracts/schemas/result.schema.json` の `$defs/outputs` が `additionalProperties: false` を持つ。
`metrics` は型だけを決める自由な形なので、スキーマの語彙を増やさずに 3 つのパスを渡せる。

### 報告を書く（`ndf worker append`）

```text
ndf worker append --report <保存先> [--done]
```

本文は標準入力から受ける。起動指示は引用符で囲んだ区切りのヒアドキュメント（`<<'NDF_REPORT'`）で渡す形を
書く。囲んだ区切りなので本文の `$` も展開されず、ヒアドキュメントを見る既存の guard にも止められない。

- 標準入力の本文を報告の末尾へ足す。`--done` があれば、続けて完了マーカーを 0 バイトのファイルとして作る
- 報告を書けなければ完了マーカーを作らない（報告の無い完了の状態を作らない）
- 報告が無ければ `stopped`（`precondition`）。用意を打たずに起動されたときである
- 結果 JSON は `summary` だけを返す（`items` と `metrics` は空）

**追記は排他で直列にする。** 読み取りと公開の 2 回の操作に分かれるため、同じ利用者の追記どうしが重なると
片方の本文が消える。ロックは利用者ごとの状態の保存先（`PluginIdentity.state_dir`）の 1 つのファイル
`worker-append.lock` で取る。報告そのものを使うと、報告を読めるだけの利用者もロックを取って追記を
止められる。報告の隣に置くと、グループが書き込める共有の保存先ではほかの利用者がロックの名前を消せる。
待つ上限は上限の設定の `durable.lock-wait`（300 秒）で、当たれば何も書かずに `stopped`（`store_in_use`）。

### 報告を読む（`ndf worker collect`）

```text
ndf worker collect --report <保存先>
```

完了マーカーを確かめ（無ければ `stopped`（`precondition`））、報告を読んで最後の `## 作業の報告` から末尾までを
`metrics.report` に入れて返す。見出しは行全体が `## 作業の報告` と一致するものだけを数える。見出しが無ければ
`stopped`（`precondition`）で、supervisor は「報告が無いまま終わったとき」の規則へ回す。

supervisor が `collect` を打つのは、中間通知を受けて完了マーカーを待ち、報告を回収するときだけである。
通常の完了通知（最後の応答で報告を受け取る）では打たない。

### リンクをたどらない読み書き

作業ディレクトリは supervisor が渡す任意のディレクトリで、その中に前の作業やほかの手が作った名前が残りうる。
名前の検査はパスの字面を直下に限るだけなので、その名前がシンボリックリンクなら読み書きと削除はリンクの先に
当たる。ハードリンクも同じで、同じ inode を共有していれば切り詰めと書き換えが利用者のファイルに及ぶ
（`O_NOFOLLOW` はシンボリックリンクだけを拒み、ハードリンクを拒まない）。検査の後に作業ディレクトリそのものを
別のディレクトリへのリンクへ差し替えられると、名前で引き直す操作は差し替え先に当たる。

そこで、4 つのファイルを読む・書く・消す処理はすべて次の形を通す。

1. 作業ディレクトリを根から 1 要素ずつ `O_DIRECTORY | O_NOFOLLOW` で開く。どの要素も検索権限だけで開ける
   フラグ（`O_PATH`、無い環境では `O_SEARCH`・`O_RDONLY`）で開くので、一覧を読めない書き込み用の保存先でも
   使える。要素がリンクならシンボリックリンクとして断り、ディレクトリでなければほかの種類として断る
2. 以後の検査と操作は、開いた記述子に対する相対の操作（`dir_fd`）だけで行う。開いた後に作業ディレクトリの
   名前を差し替えられても、記述子は開いた時点のディレクトリを指す
3. 読む側は検査した記述子そのものから読む。書く側は私有の一時ディレクトリ（`0o700`）に新しい inode を作り、
   アクセス権を整えてから名前へ公開する。消す側は最後の要素をたどらない削除で、リンクの名前だけを消す

書き込みで公開を拒む条件:

| 条件 | 扱い |
| --- | --- |
| 既存のファイルが呼んだ利用者に書き込めない | 何も作らずに権限の失敗にする |
| 既存のファイルの所有者か所有グループを新しい inode で保てない | 公開する前に断る（所有者に与えていた権限を別の利用者へ、グループに与えていた権限を別のグループへ与えない） |
| 既存のファイルが拡張 ACL を持つ・持つかを確かめられない | 公開する前に断る（拡張 ACL のあるファイルでは、モードのグループのビットは所有グループの権限ではなく ACL のマスクなので、引き継ぐと読める人が広がる） |
| 新しい inode が親の既定の ACL から項目を継いだ | 公開の前に外す。外せない・確かめられなければ公開しない |
| 作業ディレクトリの権限がグループに開いている（0775・sticky bit なし） | 拒まない。共有の出力先にも書ける |

既存のファイルを置き換えるときは、確かめた時点のアクセス権（setuid などを除くモードと所有グループ）を
新しい inode へ引き継いでから公開する。グループ権限で読んでいた利用者が、置き換えの後に読めなくならないためである。

一時ディレクトリの後始末は、作った名前を先に突き合わせ、それで片付けられないとき（同じ親の別名へ移された
とき）だけ開いた記述子から今の名前を引き、開いた記述子と同じ inode の名前だけを消す。公開した後の後始末の
失敗は上げず、中身の無い一時ディレクトリを 1 つ残して正常に返す（名前は既に新しい inode を指しており、
上げると呼び出し側は書き込みが起きなかったと読み違える）。

### 空になりうる変数の `rm` を止める guard（`rm-variable`）

`PreToolUse` の guard が Bash のコマンドを読み、`rm` の削除の対象に、展開が効く位置（引用の外か二重引用の中）の
空になりうる変数を含む語があれば `deny` を返す。拒否の文には、見つけた変数の名前と書き直しの形
（`ndf worker prepare` の打ち方、`"${NAME:?}"` の形、固定のパス）を添える。

判定は展開前の字面だけで行い、値を展開しない。

| 入力の形 | 判定 |
| --- | --- |
| `rm -f "$W/$n.report.md.done"`・`${DIR:-$FALLBACK}`・`${DIR:+/tmp}`・`${DIR:-}` | 止める |
| `rm -f "${W:?}/x"`・`${NAME:-/tmp}`・固定のパスだけ | 通す |
| `$` が単引用の中（`'a$b'`）・ANSI-C 引用の中・`\$` で逃がされている・特殊パラメータ（`$$`・`$1`） | 通す（ファイル名の文字か、空にならない値か、名前付きの変数でない） |
| `--` より前の、名前が字面で決まるオプション（`--interactive="$mode"`） | 通す（削除の対象でない） |
| `-` の後ろが変数で決まる語（`-"$NAME"`） | 止める（空なら `-` というファイルを指す） |
| `rm` を含まない・語に分けられない・コマンド名が `rm` でない | 通す |

見るのは、ラッパー（`command`・`timeout` など）を外した後のコマンド名が `rm` のものと、`bash -c` 系・`eval` の
中身である。

**`PreToolUse` の guard は確認のダイアログより先に働く。** 2026-11-23 に Claude Code v2.1.330 で確かめた。
一時の設定で Bash を `deny` するだけの `PreToolUse` の hook を入れ、受け入れ条件の原文と同じ形
`rm -f $W/$n.md.done $W/$n.result.json` を対話の Claude Code に打たせた。hook を入れない同じ起動では
「Dangerous rm operation on possibly-empty variable path」の確認が出て「Do you want to proceed?」で止まったが、
hook を入れた同じ起動では確認が出ず、`Not run · permission denied` と hook の理由がツールの結果として返り、
対象のファイルは消えなかった。NDF の guard に替えた確かめでも同じで、モデルは理由を読んで固定のパス・
`"${W:?}/${n:?}.md.done"`・`ndf worker prepare` の 3 つを提案した。

公式の文書は「`PreToolUse` hooks run before the permission prompt.」と、どのモードでも自動承認しない行為の
一覧に「Recognized `rm` and `rmdir` critical-path removals, which no allow rule or `PreToolUse` hook `"allow"`
approves」を定める。したがって `permissionDecision` を `"allow"` にして確認を飛ばす手は採れず、`deny` で
止めるほかない。`PermissionRequest` のイベントは、終了コード 2 が効かず、使っている guard の実績も無いため
採らない。

## データ・設定

| 名前 | 保存先 | 値 |
| --- | --- | --- |
| worker の名前の形 | `plugins/ndf/src/ndf/contracts/worker_paths.py` の `NAME_RE` | `[A-Za-z0-9._-]{1,64}` |
| 4 つの接尾辞 | 同上 | `.prompt.md`・`.report.md`・`.done`（報告のパスの後ろに付ける）・`.result.json` |
| 追記のロックのファイル名 | `plugins/ndf/src/ndf/core/worker_append.py` の `LOCK_FILE` | `worker-append.lock`（利用者ごとの状態の保存先の直下） |
| ロックを待つ上限 | `plugins/ndf/contracts/limits/durable.lock-wait.json` の `durable.lock-wait` | 300 秒 |
| guard の設定 | `plugins/ndf/contracts/hooks/guards.json` の `rm-variable` の行 | `events: ["PreToolUse"]`・`tools: ["Bash"]`・`order: 26`・`strict: false`・`deny_key: "none"`・`fail_closed: false`・`suggests` に `ndf worker prepare` の見本 |

`deny_key` を `"none"` にするのは、同じ拒否を数えて上限の後に案内へ下げる仕組みから外すためである。
数える設定にすると、supervisor が同じ `rm` を繰り返したときに上限の後で拒否が案内に下がり、Bash が走って
確認のダイアログでレーンが止まる。`fail_closed` は偽で、判定の中の例外は通す側へ倒す（報告の保存先の用意を
guard の不具合で止めない）。

結果 JSON の形は `plugins/ndf/contracts/schemas/io/worker.result.schema.json` が持つ。`metrics.paths`（`report`・
`done`・`result` の 3 つが必須）・`metrics.report`・`items` の `kind` の `report`・`done`・`result`・`path` を
この仕様で足した。

## 外部連携

| 相手 | 連携 |
| --- | --- |
| `ndf worker run` | 作業の結果ファイルの保存先を起動の前に 1 回だけ開き、委譲の間と委譲の後の読み取りをその記述子で行う。前の試行の結果ファイルは起動の前に消し、消せなければ worker を起動せずに `stopped`（`invalid`）。保存先を開けないときも起動せずに `invalid` にする。委譲中に結果ファイルがリンクへ差し替わる・UTF-8 で読めなければ、再起動せずに `stopped`（`bad_report`）。結果ファイルを読むのは委譲中の判定で 1 回と委譲の後に 1 回だけで、読んで解いた中身を成否の判定と記録の探索の両方へ渡す |
| `ndf contracts emit --out` | worker が作業の結果ファイルを書く通常の経路。同じ書き込みの処理で新しい inode として書く。`--out` がシンボリックリンク・ハードリンク・ディレクトリなら、標準出力へ行を出さずに `stopped`（`invalid`） |
| `ndf answer work`（工程のプランの経路） | 同じパスの決め方（`report_paths`）と同じ初期化（`reset_report`）を使い、起動指示を報告の保存先へ書く。報告の保存先の決め方をこの経路と supervisor の経路で分けない |
| 手順書 | `plugins/ndf/skills/development-workflow/references/agent-layers.md` の起動指示の「保存先」「作業の結果ファイル」の欄と worker の守る規則、`plugins/ndf/skills/development-workflow/references/waiting.md` の「中間通知を受けたとき」が、3 つのサブコマンドの 1 行だけを書く。パスを組み立てる変数・`rm`・リダイレクト（`>`・`>>`・`: >`）は書かない |
| サブコマンドの一覧 | `plugins/ndf/docs/contracts/commands.md` は `ndf check --write-spec` の生成物で、手で書かない |

起動の回数は、worker 1 件につき、通常の完了通知では `prepare` と `append --done` の 2 回である。中間通知を
受けて報告を回収するときだけ `collect` を足して 3 回になる。Tool の呼び出しの回数は変わらない（`: >`・`Write`・
`Read` が置き換わる）。リンクをたどらずに読み書きするのに要る起動で、これより増やさない。長い出力も報告と
同じ 1 回の `append --done` の本文にまとめる。

## エラー処理

| 失敗 | 返り | 止まった時点の状態 |
| --- | --- | --- |
| worker の名前の形が合わない・「保存先」が `<名前>.report.md` の形でない | `stopped`（`invalid`） | ファイルを 1 つも触らない。作業ディレクトリも作らない |
| 作業ディレクトリを作れない・開けない・同名のファイルがある | `stopped`（`invalid`。`summary` にパスと理由） | 何も作っていない |
| 3 つのどれかがリンク・ディレクトリ・ほかの種類 | `stopped`（`invalid`。`summary` にパスと見つけた種類） | 1 つも変えていない。リンクの先も触らない |
| 用意した作業ディレクトリが、開く前に既存のディレクトリへ差し替えられた | `stopped`（`invalid`） | 差し替え先の報告・完了マーカー・結果ファイルがそのまま残る |
| 報告の一時ファイルを用意できない（書き込めない・所有者を保てない・拡張 ACL・容量不足） | `stopped`（`invalid`） | 1 つも退避していない。前の 3 つがそろって残る |
| 完了マーカーか結果ファイルを退避できない | `stopped`（`invalid`） | 退避したものを元へ戻す。前の 3 つがそろって残る |
| 報告を公開できない | `stopped`（`invalid`） | 退避した 2 つを元へ戻し、前の報告は触らない |
| 退避したものを戻す先の名前が埋まっている | `stopped`（`invalid`。`summary` に回収先のパス、確かめられなければその旨） | 埋めたファイルを上書きせず、一時ディレクトリに残す |
| 報告が無いまま `append` を打った | `stopped`（`precondition`） | 何も作らない |
| 報告を書けない（権限・容量） | `stopped`（`invalid`） | 完了マーカーを作らない |
| 追記のロックを上限まで待っても取れない | `stopped`（`store_in_use`） | 何も書かない |
| 完了マーカーが無い・報告に `## 作業の報告` が無い | `stopped`（`precondition`） | 何も変えない。「報告が無いまま終わったとき」の規則へ回す |
| 報告か完了マーカーを読めない・UTF-8 として解けない | `stopped`（`invalid`。`summary` に名前と理由） | リンクの先を触らない |

`append` が完了マーカーを作れなければ、supervisor の待ちは上限まで続き、点検の規則（`waiting.md`）へ回る。

## セキュリティ

- 作業ディレクトリの中の名前が作業ディレクトリの外のファイルを指していても、その先を読まず、書かず、消さない。
  シンボリックリンクと、リンク数が 2 以上の通常のファイル（ハードリンク）を、どの操作も断る
- 名前を扱うのは、開いた作業ディレクトリの記述子に対する相対の操作だけである。検査と操作の間に親の名前を
  差し替えられても、操作は開いた時点のディレクトリに当たる
- `--name` の形を限ることで、`../` を含む名前が作業ディレクトリの外のファイルを消すことを防ぐ。日本語の名前は
  使えない
- 用意の途中で作ったディレクトリは、開いた記述子で持ち主を確かめる。呼んだ利用者の持ち物でなければ初期化しない。
  持ち主が一致しても同一とは言えないため（sticky bit の無い共有の親では、ほかの利用者が既存のディレクトリを
  同じ親の中でこの名前へ改名できる）、無かった要素を経て開いたときは、新しく作ったディレクトリには無いはずの
  名前（自分の worker の名前のファイル）が無いことも確かめる
- 置き換えでは、既存のファイルのアクセス権を保てないとき・拡張 ACL を持つときに、何も公開せずに断る
- 同じ利用者が同じ時刻に別の手で同じ名前を操作する競合は塞がない（同じ権限の手は記述子も一時ディレクトリも
  操作できる）

## 運用

- supervisor は worker ごとに 1 回ずつ `ndf worker prepare` を打つ。worker の名前は、同じ作業ディレクトリで
  稼働中の worker と重ならないものにする。エージェントの名前をそのまま使わず、`<作業種別の英字>-<Issue 番号>-<連番>`
  （例 `survey-550-2`）のように起動ごとに変える
- `prepare` は同じ名前の報告・完了マーカー・結果ファイルを消して使い回す。稼働中の worker と同じ名前で打つと、
  その worker の報告が消える
- リリースの途中で古い手順書を読んだ supervisor と新しい worker が混ざっても、報告のパスの形と完了マーカーの
  名前は変えていないので、古い手順で作ったファイルも `collect` が読める
- 手順書の外で `rm` を打つときは、`"${NAME:?}"` の形か固定のパスにする。guard が勧める形である。手順書の中で
  この形を使っている箇所は `plugins/ndf/skills/release/references/form-package-plugin.md` の 1 件
  （`rm -rf "${ISO:?}"`）で、`grep -rn ': > |rm -f|rm -rf' plugins/ndf/skills/ plugins/ndf/agents/` の結果は
  この 1 件だけである

## テスト観点

| 観点 | 縛る振る舞い | テスト |
| --- | --- | --- |
| 用意が 1 回の呼び出しで済む | `status: ok` を返し、`metrics.paths` に 3 つの絶対パスが入り、報告が 0 バイトで在り、完了マーカーと結果ファイルが無い | `plugins/ndf/tests/core/test_worker_prepare.py` |
| 確認のダイアログの条件に当たらない | 空のファイルの作成と古い完了マーカーの削除を、呼び出し側にシェルの変数も `rm` も求めずにコマンドが行う。手順書の文言はテストにせず、コードレビューで見る | 同上 |
| 2 回目で前の完了マーカーを読まない | 同じ `--dir`・`--name` で 2 回目を打つと、1 回目の完了マーカーと結果ファイルが消え、報告が空になる | 同上（`test_second_prepare_clears_the_first_done_and_result`） |
| パスが絶対パスで同じディレクトリの下にある | 相対パスの `--dir` でも、打った場所を基準に解いた絶対パスが 3 つとも返る。無いディレクトリは作る | 同上 |
| 名前の形 | `../x`・空文字・65 文字で `stopped`（`invalid`）になり、作業ディレクトリが作られていない | `plugins/ndf/tests/contracts/test_worker_paths.py`・`test_worker_prepare.py` |
| リンクをたどらない | 3 つのどれかが外のファイルへのシンボリックリンクかハードリンクのとき、`invalid` でリンクの先の中身が変わらず消えもせず、前の報告も残る。読み書き・削除の各関数と、親を差し替える競合でも同じ | `plugins/ndf/tests/contracts/test_worker_paths.py`・`test_worker_paths_resolve.py` |
| 途中で失敗したときの状態 | 完了マーカーだけ・結果ファイルだけ・報告の公開を失敗させると、退避したものが元へ戻り、前の 3 つがそろって残る | `plugins/ndf/tests/core/test_worker_prepare.py` |
| 追記と完了マーカー | 空の報告に本文が足され、`--done` で完了マーカーが 0 バイトで現れる。2 回足すと順に残る。`--done` 無しでは作らない。報告を書けなければ完了マーカーを作らない | 同上 |
| 追記の排他 | ほかの追記がロックを持つ間は何も書かずに止まる。ロックを取り直した側は報告を読み直す。ほかの利用者が開ける・消せるロックは断る | 同上 |
| 報告の読み取り | 最後の `## 作業の報告` から末尾までを返す。見出しが 2 つあれば後ろだけ。完了マーカーが無ければ `precondition` | 同上 |
| 結果ファイルの扱い | 外へのリンクの `--result-file` で worker を起動せずに `invalid`。委譲中の差し替えと不正な UTF-8 で `bad_report`。読み取りは委譲中に 1 回と委譲の後に 1 回だけ | `plugins/ndf/tests/worker/test_delegate.py` |
| 結果 JSON の書き込み | `ndf contracts emit --out` が外へのリンク・ハードリンクで `invalid` になり、リンクの先の中身が変わらない。読み取り専用のファイルは失敗し、中身が変わらない | `plugins/ndf/tests/contracts/test_contracts_cli.py` |
| guard の判定 | 止める形で `deny` を返し、理由に書き直しの形が入る。固定のパスは通す。判定の中の例外は呼び出しを通す。拒否の上限の後も案内に下がらない | `plugins/ndf/tests/domains/hooks/test_rm_variable.py` |

## 関連リンク

- [ndf-token-waits-and-context-cut.md](ndf-token-waits-and-context-cut.md) — supervisor が worker の報告を待つ手順と中間通知
- [ndf-worker-agent-and-skill-excerpts.md](ndf-worker-agent-and-skill-excerpts.md) — worker の起動指示の欄と実行方式の選び方
- [ndf-supervisor-worker-skills-guard.md](ndf-supervisor-worker-skills-guard.md) — 起動指示を見るほかの guard
- Issue #3877
