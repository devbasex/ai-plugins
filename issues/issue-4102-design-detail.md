# #4102 の詳細設計: 起動ごとの次のセグメントのマーカーと、ラッパーの読み・引き継ぎ・掃除

設計文書 [issue-4102-design.md](./issue-4102-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。#4049 の実装（`relay/signals.py` の `LAUNCH_ID_RE`・`drop_other_busy`・`STOP_LOCK`、`relay_mark.py` の件数の書き込み）と、#4061・#4062 の実装（`signals.relay_role` と `relay_mark.py` の頭の判定）がマージされた後の形を前提にする。

## 地図

パッケージ・モジュール構成（触るファイルだけ。新設のファイルは無い）:

```text
plugins/ndf/
├── contracts/domains/ndf-files.json          # relay-next の path と writer
├── docs/contracts/files.md                   # 生成文書（ndf check --write-spec）
├── skills/development-workflow/references/
│   ├── relay.md                              # 判定の表の順 3・守りの節・記録の行の種類
│   └── glossary.md                           # シグナルファイルの行
├── src/ndf/domains/
│   ├── relay/signals.py                      # マーカーの読み書き・写し・掃除
│   ├── relay/wrapper.py                      # 今の起動のマーカーだけを読み、覚え、写す
│   ├── relay/registry.py                     # 冒頭の表の名前
│   └── hooks/guards/relay_mark.py            # 起動の ID のファイルへ書く
└── tests/
    ├── domains/relay/{test_relay_parts.py,test_relay_wrapper.py,test_relay_wrapper_question.py}
    ├── domains/hooks/test_guards.py
    └── support/relay_children.py             # 偽の子の Stop が起動の ID で書く
```

リポジトリの根の `docs/glossary/glossary.json`（シグナルファイルの意味）と生成文書 `docs/glossary/ndf-relay.md` も直す。

依存の向き: `relay_mark` → `relay.signals`、`wrapper` → `signals`・`switch_decide` で、今と変わらない。`signals` は標準ライブラリだけを使う（決定 111）。`switch_decide` は変えない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/signals.py` | 変更 | 起動ごとのマーカーを読み書きし、写し、ほかの起動のファイルを消す | 標準ライブラリ | モジュール `signals` | `write_next`・`read_next`（変更）、`carry_next`・`_next_path`（新設）、`drop_other_launches`（#4049 の `drop_other_busy` を置き換え）、`drop`（削除）、定数 `NEXT_PREFIX`・`LEGACY_NEXT_FILES`（新設）・`NEXT_FILE`（削除） |
| `src/ndf/domains/relay/wrapper.py` | 変更 | 今の起動のマーカーだけを読み、読んだものを覚え、同じ会話の起動し直しで写す | `signals`・`switch_decide` | `Launch`・`Section`・`Wrapper` | `_next_mark`・`_start`・`_on_switch`・`_on_resume`・`_on_handover`（変更）、`Section.next_mark`・`Launch.carry`（項目を足す）、定数 `CARRY_FAILED`・`CARRY_KEPT_LINE`（新設） |
| `src/ndf/domains/hooks/guards/relay_mark.py` | 変更 | 子の会話の Stop が自分の起動のファイルへマーカーを書く | `relay.signals`・`hooks.tables` | モジュール `relay_mark` | `check`（変更） |
| `src/ndf/domains/relay/registry.py` | 変更 | 冒頭の表のファイルの名前を直す | — | — | — |

## マーカーのファイル（`relay/signals.py`）

満たす決定: 決定 1・2・3・5、受け入れ条件 1・2・3・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_next_path` | `src/ndf/domains/relay/signals.py` | 新設 | `_next_path(directory: Path, launch: Optional[str]) -> Optional[Path]` | 起動の ID が `LAUNCH_ID_RE` に合えば `directory / f"{NEXT_PREFIX}{launch}.json"`、合わなければ None |
| `write_next` | 同上 | 変更 | `write_next(directory: Path, launch: Optional[str], session_id: str, block: str, goal_active: bool) -> bool` | 起動 `launch` のマーカーを書く。同じファイルの前の中身からだけ `first_written_at` を引き継ぐ |
| `read_next` | 同上 | 変更 | `read_next(directory: Path, launch: str) -> Optional[dict[str, Any]]` | 起動 `launch` のマーカーだけを読む |
| `carry_next` | 同上 | 新設 | `carry_next(directory: Path, mark: Mapping[str, Any], launch: str) -> bool` | 読んだマーカーを、`launch` だけを直して起動 `launch` のファイルへ写し、写せたかを返す |
| `drop_other_launches` | 同上 | 変更（#4049 の `drop_other_busy` を名前ごと置き換え） | `drop_other_launches(directory: Path, launch: str) -> None` | 起動 `launch` のもの以外の背景の作業の記録とマーカー、旧い名前の `next.json`・`next.json.lock` を消す |

- `_next_path`
  - 事前条件: 無い
  - 事後条件: 上の表のとおり。`None`・空・32 桁の 16 進でない値は None
  - 副作用: 無い
- `write_next`
  - 事前条件: 無い（`launch` の形は中で確かめる）
  - 事後条件: `_next_path` が None なら書かずに偽。そうでなければ同じファイルの前の中身を `_read` で読み（壊れていれば前の中身が無いとして扱う）、その `first_written_at` が文字列ならそれを、無ければ今の時刻を `first_written_at` にして `{"launch": launch, "session_id", "block", "goal_active", "first_written_at", "written_at": 今}` を `replace_json` で書き、真を返す。前の中身は同じ起動のファイルだけなので、ほかの起動の時刻は入らない。`carry_next` で写した時刻も同じ規則で保たれる
  - 副作用: `next-<launch>.json` を一時の名前に書いて名前を変える。排他を取らない（同じ起動の Stop は重ならない。#4049 の `write_busy` と同じ）。書けない理由の `OSError` は今のとおり呼び手へ送出する（guard の例外として dispatch が扱う）。子を止める排他と `launch.json` には触れない（決定 2）
- `read_next`
  - 事前条件: 無い
  - 事後条件: `_next_path` が None なら None。ファイルが無ければ None。中身が壊れている・`first_written_at` か `block` が文字列でない・`launch` が引数と違うか無いときは `SignalError`（ラッパーは今のとおり `config_broken` で止まる）。ほかは中身の辞書
  - 副作用: 無い（読むだけ）。ほかの起動のファイルと旧い `next.json` は開かない
- `carry_next`
  - 事前条件: `mark` は同じラッパーの `read_next` が返した辞書。呼ぶのは今の起動が無い間（止めた後・次の起動の記録の前）だけ
  - 事後条件: `_next_path(directory, launch)` が None か、書けない（`OSError`）なら偽。そうでなければ `{**mark, "launch": launch}` を `replace_json` で書いて真。`first_written_at`・`written_at`・`block`・`session_id` は `mark` のまま
  - 副作用: `next-<launch>.json` を一時の名前に書いて名前を変える。排他を取らない（`launch` は `spawn` まで子に渡らないので、この名前へ書く hook は無い）。`OSError` は偽にして外へ出さない
- `drop_other_launches`
  - 事前条件: `launch` は今から起動する子の起動の ID
  - 事後条件: 呼んだ時点で中継の保存先にあった `busy-*.json`・`next-*.json` のうち、名前が `busy-<launch>.json`・`next-<launch>.json` でないものと、旧い名前の `next.json`・`next.json.lock`（定数 `LEGACY_NEXT_FILES`）を消そうとし終えている（消せなかったもの・消した後に止めた子や旧い版の hook が書いたものは残りうる。どれも読まれず、次の起動の前に消し直す）
  - 副作用: `glob` で拾ったものと `LEGACY_NEXT_FILES` を、`unlink(missing_ok=True)` で消す。`OSError` は残して続ける（#4049 の `drop_other_busy` と同じ）。旧い名前の 2 つは読まずに消すだけである（設計の決定 3。役目を終えたマーカーと錠を残さない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検知 | 振る舞い | 利用者へ出すもの | 戻し方 |
| --- | --- | --- | --- | --- | --- | --- |
| `write_next` | 起動の ID が無い・形が違う | 手で起動した会話・旧い版のラッパー | `_next_path` | 書かずに偽 | 無し（`relay_mark` が `relay_next: false` を返す） | 無し |
| `read_next` | 壊れた・`launch` が名前と違う | 手での書き換え・ディスクの誤り | 読む場所 | `SignalError` | 今のとおり `config_broken` の止まり | ファイルを消して起動し直す |
| `carry_next` | 書けない | ディスク・権限 | `OSError` | 偽を返す。`_start` は子を起動せずに、`CARRY_KEPT_LINE` とブロックを端末へ出して `Ended("precondition", CARRY_FAILED)` を返す | 停止理由 `CARRY_FAILED` と、端末の `CARRY_KEPT_LINE` とブロック | 保存先を直し、出たブロックの中身を最初の入力にして `claude` を打ち直す（新しい会話。`--resume` で起動し直すと、最初の起動の掃除が止めた子のファイルを消し、ブロックは引き継がれない） |
| `drop_other_launches` | 消せない | 権限 | `unlink` | 残して続ける | 無し | 次の起動で消し直す |

### 内部のデータと定数

| 名前 | 種類 | 値 |
| --- | --- | --- |
| `NEXT_PREFIX` | 定数 | `next-`（ファイルは `next-<起動の ID>.json`） |
| `NEXT_FILE` | 定数 | 削除（読み手と書き手が無くなる） |
| `LEGACY_NEXT_FILES` | 定数 | `("next.json", "next.json.lock")`。`drop_other_launches` が消すだけで、読む関数は無い |
| `CARRY_FAILED` | 定数（`wrapper.py`） | `次の子へ次のセグメントのマーカーを引き継げなかった（{why}）。端末に出したブロックで claude を打ち直す` の形で、`why` に保存先のパスを入れない |
| `CARRY_KEPT_LINE` | 定数（`wrapper.py`） | `ndf-relay: 次のセグメントのブロックを下に出す。中継の保存先を直した後、この中身を最初の入力にして claude を打ち直す`。`_start` が `req.carry` を持ったまま止まる（写せない・`spawn` の失敗）ときに `_say` で出し、続けて `req.carry["block"]` をそのまま出す。ブロックは `log.jsonl` には書かない（記録の行は今のとおり停止理由だけ） |

冒頭のファイルの表の `next.json` の行を `next-<起動>.json`（書き手: 子の会話の Stop の hook `relay-marker`。ラッパーは同じ会話の起動し直しで写し、ほかの起動のものを消す。中身: 書いた起動の ID と次のセグメントのブロック）に直す。`first_written_at` の段落は「同じ起動のファイルの中で保つ」に直す。

### 既存の部品の再利用

| 部品 | 使い方 |
| --- | --- |
| `replace_json`・`_read` | 書き込みと読み取りに今のまま使う（`write_next` は錠を取る `write_json` を使わなくなる。錠を持たせて偽になる今のテスト `test_relay_parts.py` の 1 本は消す） |
| `LAUNCH_ID_RE`（#4049） | ファイルの名前を作る前の形の検査 |
| `drop_other_busy`（#4049） | 名前と対象を広げて置き換える。呼ぶ場所（`_start`）は同じ |

### 単体テストの観点

| 関数 | 正常 | 境界 | 異常 |
| --- | --- | --- | --- |
| `write_next` | L1 で 2 回書くと `first_written_at` が 1 回目のまま、`written_at` とブロックが 2 回目。`carry_next` で L2 へ写した後に L2 で書くと、写した時刻のまま | `next-L2.json` があっても、L1 の 1 回目の `first_written_at` は今の時刻 | `launch` が None・空・`../x`・31 桁で偽、ファイルが増えない |
| `read_next` | L1 のファイルを L1 で読む | L1 のファイルと旧い `next.json` だけがあると L2 で None | 中身の `launch` が L2 の `next-L1.json` を L1 で読むと `SignalError`。`{`・ブロックの無い中身で `SignalError` |
| `carry_next` | L1 の中身を L2 へ写すと、`launch` だけが L2 で、ほかの項目が同じ | — | 形の違う `launch` で偽、ファイルが増えない。書けない保存先（読み取り専用）で偽を返し例外を出さない |
| `drop_other_launches` | L2 を渡すと L1 の `busy-`・`next-` と旧い `next.json`・`next.json.lock` が消え、L2 の 2 つが残る | ファイルが 1 つも無くても例外を出さない | 消せない（読み取り専用のディレクトリ）ファイルを残して例外を出さない |

## 子の会話の Stop（`hooks/guards/relay_mark.py`）

満たす決定: 決定 1・2・5、受け入れ条件 2・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/domains/hooks/guards/relay_mark.py` | 変更 | `check(ctx, svc) -> Verdict \| None`（変えない） | 自分の起動のファイルへマーカーを書く |

- `check`
  - 事後条件: #4061 の頭の判定（子の会話か）と #4049 の件数の書き込み・作業が残るときの変更要求は変えず、その後に行う。起動の ID はマーカーを書く行でだけ照らし、その行を `signals.write_next(directory, signals.relay_launch(ctx.env), ctx.session_id, block, ctx.stop_hook_active)` に変える（形の照らしは `write_next` が持つ）。マーカーのための `hook.counter-lock` の秒の読み込みは無くす。返りは今のとおり `passing(relay_next=written, goal_active=…)`
  - 副作用: `next-<NDF_RELAY_LAUNCH>.json` だけを書く。`NDF_RELAY_LAUNCH` が無い・形が違えば書かない

### 単体テストの観点

| 観点 | 確かめること |
| --- | --- |
| 正常 | 子の会話で `NDF_RELAY_LAUNCH` が L1 の Stop をエントリポイントから流すと `next-L1.json` が書かれ、`next-L2.json` の中身が変わらない |
| 境界 | `NDF_RELAY_LAUNCH` が無い・空・形が違う Stop で、マーカーのファイルが 1 つも無い。同じ入力で背景の作業が残れば、今のとおり変更要求を返す。`/goal` の続きの Stop（`stop_hook_active` が真）で L1 の ID なら `goal_active` が真で書かれ、L2 の ID の同じ Stop は `next-L1.json` を変えない |
| 異常 | 背景の作業が残る Stop は今のとおり変更要求で、どのマーカーも書かない（#4049 のテストを起動の ID つきに直す） |

## ラッパーの読み・引き継ぎ・掃除（`relay/wrapper.py`）

満たす決定: 決定 1・3・4、受け入れ条件 1・4・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Section.next_mark` | `src/ndf/domains/relay/wrapper.py` | 変更（項目を足す） | `next_mark: Optional[dict[str, Any]] = None` | この見回りで読んだ今の起動のマーカー |
| `Wrapper._next_mark` | 同上 | 変更 | `_next_mark(self, sec: Section) -> Optional[dict[str, Any]]`（変えない） | 今の起動のマーカーを読み、`sec.next_mark` に覚えて返す |
| `Launch.carry` | 同上 | 変更（項目を足す） | `carry: Optional[dict[str, Any]] = None` | 起動し直す子へ写すマーカー。子を止めた見回りの `sec.next_mark` |
| `Wrapper._start` | 同上 | 変更 | `_start(self, req: Launch) -> Optional[Ended]`（変えない） | 起動の前に、渡されたマーカーを写し、ほかの起動のファイルを消す |
| `Wrapper._on_resume`・`_on_handover` | 同上 | 変更 | 変えない | 子を止められたときだけ、その見回りの `sec.next_mark` を `Launch.carry` に載せる |
| `Wrapper._on_switch` | 同上 | 変更 | 変えない | 止めた後の `next.json` の削除をやめる |

- `_next_mark`
  - 事後条件: 見回りごとに `sec.next_mark = None` に戻してから `nxt = signals.read_next(self.dir, sec.launch)` を置き、返す（無ければ None のまま）。会話の ID の照らしと `stale_next` の記録の行を無くす。`SignalError` は今のとおり `_loop` へ送る（`sec.next_mark` は None のまま）
- `_start`
  - 事後条件: 起動の ID を作り、今の前提の確かめ（作業ディレクトリ・`claude` の版）を通した後、次の順に打つ。(1) `req.carry` が None でなければ `signals.carry_next(self.dir, req.carry, launch)`。偽なら子を起動せず、`set_launch` も打たずに、`CARRY_KEPT_LINE` と `req.carry["block"]` を `_say` で出して `Ended("precondition", CARRY_FAILED)` を返す。(2) `signals.set_launch(self.dir, launch)`（今のとおり）。(3) `signals.drop_other_launches(self.dir, launch)`（#4049 の `drop_other_busy` と同じ位置で置き換える）。(4) `spawn`。`spawn` が失敗したら今のとおり `set_launch(self.dir, None)` で止まり、`req.carry` があれば同じく `CARRY_KEPT_LINE` とブロックを出す。写したファイルと止めた子のファイルは残るが、次にラッパーを起動したときの最初の `_start` は `carry` を持たず、新しい起動の ID で (3) が消すので、ラッパーはそれを再試行の引き継ぎに使わない（設計の決定 4。再試行は端末に出したブロックで利用者が新しい会話を始める）
  - 副作用: 上のファイルの操作
- `_on_resume`・`_on_handover`
  - 事後条件: #4049 のとおり `_stop_if_clear(sec, allow=_BUSY_ALLOW.get(step.busy))` で呼ぶ（`allow` と、排他の中の背景の作業の記録の読み直しを保つ。`_stop_if_clear` は変えない）。`"stopped"` を返したときだけ、`Launch(…, carry=sec.next_mark)` で `_start` を呼ぶ。`"held"`（質問のファイル・ロック・`allow` が偽）なら今のとおり何もせずに返り、次の見回りが読み直した値で決め直す。`_loop` の最初の `Launch` と `_on_switch` の `Launch` は `carry` を渡さない（None）
- `_on_switch`
  - 事後条件: `signals.drop(self.dir, signals.NEXT_FILE)` の行を消す。止めたマーカーは次の `_start` の (3) が消す。新しいセッションは `resume` が偽なので写さない

### 処理の手順

1. 見回りの `_state` が `_next_mark` で `next-<sec.launch>.json` を読み、`sec.next_mark` に置き直す
2. `switch_decide.decide` が手を決める（変えない）
3. 止める手なら `_stop_if_clear` が子を止めて `launch.json` を外す（変えない）。止められなければ手順 1 へ戻る
4. `_on_resume`・`_on_handover` が手順 1 の値を `Launch.carry` に載せ、`_start` が写し、`set_launch`、掃除、`spawn` の順に打つ

手順 1 と手順 3 の間に止める前の子が書いたマーカー（B2）は、手順 1 の読みに入っていないので写さない。手順 1 から手順 3 の強制終了までは同じ見回りの中で、間に待ちは無い。B2 を失う代償と、止める直前に読み直す案を採らない理由は設計の決定 4 にある。手順 3 の後に止めた子が書いたマーカーは、止めた子の起動のファイルにしか届かず、手順 4 の掃除か、その後に書かれたものは次の起動の掃除で消える。

### 並行と排他

| 場面 | 書き手 | 排他 | 重ならない理由 |
| --- | --- | --- | --- |
| 今の子の Stop | 子の hook | 取らない | 同じ起動の Stop は応答ごとに 1 回で重ならない |
| 止めた子の遅れた Stop | 止めた子の hook | 取らない | 今の起動のファイルと名前が違う |
| 写し | ラッパー | 取らない | 写す名前は次の起動のもので、その ID は `spawn` まで子に渡らない |
| 掃除 | ラッパー | 取らない | 消すのはほかの起動の名前だけで、今から起動する子の名前は消さない。止めた子が掃除の後に書いたファイルは次の起動まで残る |

### ログと記録の出力

記録の行の種類 `stale_next` を無くす（`relay.md` の記録の行の種類の表から行を消す）。ほかの行は変えない。

### 単体テストの観点

| 観点 | 確かめること |
| --- | --- |
| 遅れた書き込み（4 つの手） | `account`（利用上限の画面）・`restart`（出力の止まり）・`handover`（文脈の量）・`switch`（L1 のマーカー）のそれぞれで L2 を起動した後に、テストが L1 の ID で `write_next` を呼んでも、次の見回りの手が `switch` にならず、起動した引数に新しいセッションの `--session-id` が現れない。L2 の ID で書くと静かな時間の後に `switch` になる |
| 引き継ぎ（`account`） | L1 のマーカーがある見回りで利用上限の画面を見せると、L2 の起動の後に `next-L2.json` があり、ブロックと `first_written_at` が L1 のものと同じ。差し替えた時計でその時刻から静かな時間を数えて `switch` になる。`_stop_if_clear` の後・`_start` の前に L1 の ID で書いた別のブロック（B1'）は `next-L2.json` に入らない。`_state` の後・強制終了の前に L1 の ID で書いた別のブロック（B2。偽の子の `terminate` の差し替えの中で書く）も入らず、静かな時間を過ぎた時計で次の見回りが B1 の文で `switch` になる。質問のファイルで止めるのを見送った見回りの後に止めた見回りでは、止めた見回りの読みが写る。守り 1 の `account`（`busy="zero"`）で読み直しが 0 件でないと、子を止めず（`terminate` が呼ばれない）何も写さない |
| 写せない・起動できない | `carry_next` を偽にする差し替えで、子を起動せず（`spawn` が呼ばれない）、停止理由が `precondition` で、端末の出力に `CARRY_KEPT_LINE` と B1 のブロックがある。`spawn` が `OSError` を出す差し替えでも、同じ 1 行とブロックが出る |
| 失敗からの再試行 | 上の 2 つの後、同じ保存先で新しいラッパーを、出たブロックを最初の入力にした新しい会話で起動すると、最初の子の引数がそのブロックを持ち、`--resume` を持たない。前の起動の `next-` は最初の起動の掃除で消え、次の見回りが `switch` にならない |
| 最初の起動 | 前の実行の `next-<別の ID>.json` を置き、`--resume` でラッパーを起動すると、最初の子は何も写さない |
| `switch` | 覚えたマーカーのブロックが次のセグメントの文になり、新しい起動のマーカーのファイルが無い |
| 掃除 | 前の実行の `next-<別の ID>.json` と旧い `next.json`・`next.json.lock` を置いて始めると、最初の起動の後に消えていて、判定に入らない（`switch` にならない）。消せないファイルを残しても起動し、判定に入らない |
| 壊れたマーカー | 今の起動のファイルが壊れていると今のとおり `config_broken` で止まる（`test_relay_wrapper.py`・`test_relay_wrapper_question.py` の `NEXT_FILE` を使う 2 本を、今の起動のファイルへ書く形に直す） |

## 手順書と表の説明

| ファイル | 直すところ |
| --- | --- |
| `skills/development-workflow/references/relay.md` | 判定の表の順 3 の `next.json` を `next-<起動>.json`（今の起動のもの）に直す。「質問の hook とラッパーは、同じロックで順番をそろえる」の箇条の後に、マーカーは起動ごとのファイルで、止めた子の遅れた Stop は今の起動のマーカーに届かないこと・同じ会話の起動し直しでラッパーが写すことを 1 箇条で足す。記録の行の種類の表から `stale_next` を消す。「変更が効く時点」の後に、この変更を含む版へ上げたときは、旧いラッパーが新しい hook のマーカーを読まず切り替わらないので、上げた版のセグメントがブロックを出したら `/exit` でラッパーを抜け、そのブロックで `claude` を打ち直すこと（設計の「危険」の版の混在）を足す。同じ文を、この変更を出す版の利用者向けの変更（CHANGELOG）にも書く |
| `skills/development-workflow/references/glossary.md` | シグナルファイルの行の `next.json` を `next-<起動>.json` に直す |
| `docs/glossary/glossary.json` | シグナルファイルの `meaning` の `next.json` を `next-<起動>.json` に直し、`ndf records glossary render` で `docs/glossary/ndf-relay.md` を作り直す |
| `contracts/domains/ndf-files.json` | `relay-next` の `path` と `writer`（設計文書の「データ構造」）。`docs/contracts/files.md` は `ndf check --write-spec` で作り直す |
| `src/ndf/domains/relay/registry.py` | 冒頭の表の `next.json` を `next-<起動>.json` に直す |
| `tests/support/relay_children.py` | `Spawner` が `signals.write_next(relay_dir, env["NDF_RELAY_LAUNCH"], session_id, block, True)` で書く |
