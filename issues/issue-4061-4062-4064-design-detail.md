# #4061 #4062 #4064 の詳細設計: 子の会話の見分け・中継の変数の契約・締め切りでの確定した拒否

設計文書 [issue-4061-4062-4064-design.md](./issue-4061-4062-4064-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。#4049 の実装（`relay/signals.py` の `relay_session`・`SESSION_ENV`・`launch_env(directory, launch, session_id)`、`hooks/guards/relay_busy.py`）がマージされた後の形を前提にする。

## 地図

パッケージ・モジュール構成（触るファイルだけ。新設のファイルは無い）:

```text
plugins/ndf/
├── contracts/
│   ├── domains/env.json                  # 中継の 3 行（#4062）
│   └── hooks/guards.json                 # summary の fail_closed と締め切りの説明（#4064）
├── docs/contracts/{env.md,hooks.md}      # 生成文書（ndf check --write-spec）
├── skills/development-workflow/references/relay.md   # 質問の hook の節と hook の一覧
└── src/ndf/domains/
    ├── relay/signals.py                  # relay_role（#4061）
    └── hooks/
        ├── main.py                       # _Run.decided・_deadline_out（#4064）
        ├── dispatch.py                   # GuardServices.denied・run の decided（#4064）
        └── guards/
            ├── ask.py                    # relay_role・denied（#4061・#4064）
            ├── ask_clear.py              # relay_role（#4061）
            ├── relay_mark.py             # relay_role（#4061）
            ├── relay_busy.py             # relay_role・denied（#4061・#4064。#4049 が新設）
            └── context_limit.py          # relay_role（#4061）
```

依存の向き: guard → `relay.signals`（標準ライブラリだけ）、`hooks.main` → `hooks.dispatch` → guard の一方向で、今と変わらない。`signals` は hook の経路から呼ぶので `ndf.contracts` を import しない（決定 111）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/domains/relay/signals.py` | 変更 | 中継の会話の役割を決める | 標準ライブラリ | モジュール | `relay_role`（新設） |
| `src/ndf/domains/hooks/guards/ask.py` | 変更 | 子の会話で先の拒否が無いときだけ質問を転送する | `signals` | モジュール | `check`・`on_deadline`（変更） |
| `src/ndf/domains/hooks/guards/ask_clear.py` | 変更 | 子の会話だけが閉じた質問を消す | `signals` | モジュール | `check`（変更） |
| `src/ndf/domains/hooks/guards/relay_mark.py` | 変更 | 子の会話だけが次のセグメントのマーカーを書く | `signals` | モジュール | `check`（変更） |
| `src/ndf/domains/hooks/guards/relay_busy.py` | 変更 | 子の会話で先の拒否が無いときだけ開始を記録する | `signals` | モジュール | `check`・`on_deadline`（変更）・`_target`（削除） |
| `src/ndf/domains/hooks/guards/context_limit.py` | 変更 | 子の会話だけに中継の案内を出す | `signals` | モジュール | `check`（変更） |
| `src/ndf/domains/hooks/dispatch.py` | 変更 | 確定した拒否を積み、後の guard へ渡す | `tables`・`verdict` | `GuardServices`・モジュール | `run`（変更） |
| `src/ndf/domains/hooks/main.py` | 変更 | 締め切りで確定した拒否を出す | `dispatch`・`tables` | `_Run`・モジュール | `_guards`・`_deadline_out`（変更） |
| `contracts/domains/env.json` | 変更 | 中継の 3 行 | — | — | — |

## 子の会話の見分け（`signals.relay_role`）

満たす決定: 決定 1・2・3・7、受け入れ条件 1〜5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `relay_role` | `src/ndf/domains/relay/signals.py` | 新設 | `relay_role(env: Mapping[str, str], session_id: object) -> str` | 中継の中での会話の役割（`OUTSIDE`・`CHILD`・`NESTED`）を返す |

- `relay_role`
  - 事前条件: 無い（どんな値でも例外を上げない）
  - 事後条件: `env.get("NDF_RELAY_DIR")` が空か無ければ `OUTSIDE`。そうでなく、`relay_session(env)` が None でなく、`session_id` が `str` でそれと等しければ `CHILD`。ほかは `NESTED`。保存先がディレクトリかは見ない
  - 副作用: 無い（環境変数と入力の比べだけ。ファイルに触れない）

各 guard の頭の判定（どれも `relay_role(ctx.env, ctx.session_id)` を 1 回だけ呼ぶ）:

| guard | `OUTSIDE` | `NESTED` | `CHILD` |
| --- | --- | --- | --- |
| `ask-forward`（`check`） | 中継へ書かない。オーガナイザー・知らせは今のとおり | 中継・オーガナイザーへ書かない。知らせは今のとおり | 今のとおり（`relay_dir` で開き、起動の ID をロックの中で照らして書く。開けなければ拒否） |
| `ask-forward`（`on_deadline`） | None | None | 今のとおり（`relay_dir` が None なら拒否） |
| `ask-answered`・`ask-closed` | 通す（消さない） | 通す（消さない） | 今のとおり |
| `relay-marker` | 何もしない | 何もしない | 今のとおり（#4049 の Stop の件数の書き込みを含む） |
| `relay-busy` | 何もしない | 何もしない | #4049 のとおり（`_target` の代わり） |
| `context-limit` | 中継の外の案内 | 中継の外の案内 | 中継の案内 |

`ask-forward` の `NESTED` で知らせ（`outbox`）を残すのは、知らせが利用者の通知の宛先へ送るだけで中継とオーガナイザーの状態に触れないためである。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `relay_role` | `session_id` が無い・文字列でない | 入力の形の違い | `relay_role` | `NESTED` を返す（中継へ書かない側へ倒す） | 無し | 無し |
| `ask.check`（`CHILD`） | 保存先が開けない | 保存先が消えた | `relay_dir` が None | 拒否（`QUESTION_NOT_RECORDED`）。今の `relay_dir` が None の「中継の外」とは区別する | 拒否の理由 | モデルが呼び直す |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `OUTSIDE`・`CHILD`・`NESTED` | 定数（文字列） | `src/ndf/domains/relay/signals.py` | `"outside"`・`"child"`・`"nested"` |
| `DIR_ENV` | 定数 | 同上 | `"NDF_RELAY_DIR"`（`relay_dir`・`launch_env` が使う文字列を 1 か所に寄せる） |

### 既存の部品の再利用

| 使う部品 | 場所 | 使い方 |
| --- | --- | --- |
| `relay_session` | `relay/signals.py`（#4049） | `NDF_RELAY_SESSION` を読む |
| 起動の ID の照らし | `signals.write_question`・`drop_question`・`record_start` | 変えない（ロックの中で `launch.json` と照らす） |

### 単体テストの観点

| 対象 | 正常 | 境界 | 異常 |
| --- | --- | --- | --- |
| `relay_role` | DIR・SESSION・同じ `session_id` で `CHILD` | SESSION が空文字で `NESTED`。DIR が空文字で `OUTSIDE` | `session_id` が None・数で `NESTED` |
| 6 つの guard | `CHILD` の入力で今のとおり書く・消す | `agent_id` つきの `CHILD` も書く | `NESTED` の入力で中継の保存先とオーガナイザーの保存先のファイルが変わらない（中身とファイルの一覧を前後で比べる）。`NESTED` の Stop で先に置いた子の質問が残る |

## 中継の変数の契約（`env.json`）

満たす決定: 決定 4、受け入れ条件 6〜8

`contracts/domains/env.json` の `NDF_RELAY_DIR` の行を設計文書の「データ構造」の形に直し、その直後に `NDF_RELAY_LAUNCH`・`NDF_RELAY_SESSION`（#4049 が足した行）を同じ形で並べる。3 行とも `"child": "drop"`。`docs/contracts/env.md` は `ndf check --write-spec` で作り直す。

`session_env.child_env` は変えない（`facts.ids("env", child="drop")` が 3 つを含むようになり、受け継いだ値が外れる）。ラッパーの `_start` は今のとおり `child_env` の後に `launch_env` を重ねるので、子には自分の値だけが入る。

### 単体テストの観点

| 対象 | 正常 | 異常 |
| --- | --- | --- |
| 契約と `launch_env` | `set(signals.launch_env(d, "a"*32, "s"))` が、`env.json` の行のうち `area == "relay"`・`set_by == "ndf"`・`child == "drop"` の `id` の集まりと等しい | 行を消す・`child` を外すと落ちる |
| `child_env` | 3 つを入れた環境から作った返りに 3 つが無い | — |
| ラッパーの `_start` | 差し替えた `spawn` が受けた環境の 3 つが、親の環境の値でなくラッパーの値 | 親の `NDF_RELAY_DIR` が残ると落ちる |

## 確定した拒否の受け渡し（`dispatch.run`・`main`）

満たす決定: 決定 5・6・7、受け入れ条件 9〜11・14

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `GuardServices` | `src/ndf/domains/hooks/dispatch.py` | 変更 | `GuardServices(state_dir, counter, params, denied: tuple[str, ...] = ())` | guard へ、それまでに拒否を確定した guard の名前を渡す |
| `run` | 同上 | 変更 | `run(ctx, specs, budget, counter, state_dir, decided: list[tuple[str, str]], clock=time.monotonic) -> Outcome` | guard を順に呼び、案内へ下げない `deny` を `decided` へ積む |
| `_Run` | `src/ndf/domains/hooks/main.py` | 変更 | `decided: list = field(default_factory=list)` を足す | 1 回の実行の確定した拒否を持つ |
| `_guards` | 同上 | 変更 | 変えない | `dispatch.run` に `run.decided` を渡す |
| `_deadline_out` | 同上 | 変更 | 変えない | 確定した拒否と、まだ拒否していない `fail_closed` の guard の `on_deadline` で出力を作る |

- `run`
  - 事前条件: `decided` は呼び出し元が作った空のリスト（`_Run.decided`）
  - 事後条件: 各 guard を呼ぶとき `GuardServices.denied` は `tuple(name for name, _ in decided)`。guard の判定が `deny` で、(a) `fail_closed` か、(b) `counter.allow_deny` が真（案内へ下げない）なら、判定を確定した直後に `decided.append((spec.name, got.reason))` を打つ。案内へ下げた拒否・変更要求・案内・通すは積まない。`Outcome` の作り方と `combine` は変えない
  - 副作用: 引数の `decided` へ追加する（ほかは今のとおり）
- `_deadline_out`
  - 事前条件: `run.payload` が None なら今のとおり空を返す
  - 事後条件: `reasons` は `run.decided` の理由を積んだ順に並べたものから始まる。続けて `fail_closed` の guard のうち、名前が `run.decided` に無く、今の選び方（事象・Tool・`NDF_HOOK_OFF`・ランタイムで外した行）に当たるものの `on_deadline(os.environ, run.payload, denied)` を呼び、返った理由を足す（`denied` は `run.decided` の名前のタプル）。`reasons` が 1 つ以上なら `record["verdict"] = "deny"`・`record["carried"] = [名前…]`（`run.decided` の名前。空なら書かない）を書き、理由を空行でつないだ拒否を出す
  - 副作用: 計測の 1 行に `carried` が増える

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_deadline_out` | `on_deadline` が例外 | guard の誤り・引数の数の食い違い（`TypeError`） | `_deadline_out` の `except Exception` | 今のとおり `GUARD_FAILED` の拒否にする | 拒否の理由・終了コード 0 | 呼び直せる |
| `run` | 割り込みが guard の返りと `append` の間に来た | `SIGALRM` | 検出しない | その 1 件は積まれない。`fail_closed` なら `on_deadline` が拾い、ほかは通す（今のとおり） | 無し | 無し |
| `run` | `counter.allow_deny` の中で割り込み | 同上 | 検出しない | 積まれない（数えを終えていない拒否は確定していない） | 無し | 無し |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `_deadline_out` | `decided` 空・`on_deadline` の拒否無し | 空（今と同じ） |
| 同上 | `decided` 空・`on_deadline` の拒否あり | `on_deadline` の理由だけの拒否（今と同じ） |
| 同上 | `decided` あり・`on_deadline` の拒否無し | `decided` の理由の拒否 |
| 同上 | `decided` あり・`on_deadline` の拒否あり | 両方の理由を 1 つの拒否に |
| 同上 | `decided` に `fail_closed` の guard の名前がある | その guard の `on_deadline` は呼ばない（理由を 2 度出さない） |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| `dispatch.run` と `SIGALRM` の割り込み | `_Run.decided` | 取らない（同じスレッドの割り込み。`list.append` は 1 回の操作で、割り込みは操作の間にしか入らない） | 割り込みの後は `_deadline_out` だけが読む |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `_deadline_out` | `carried`（引き継いだ guard の名前の列） | 計測の 1 行（`hooks/metrics`） | 理由の文（`guards` の列と同じく名前だけ） |

### 単体テストの観点

| 対象 | 正常 | 境界 | 異常 |
| --- | --- | --- | --- |
| `run` | `deny` を返す guard の後の guard の `svc.denied` がその名前。`decided` に `(名前, 理由)` | 2 回目まで（`allow_deny` が真）は積み、案内へ下げた 3 回目は積まない（`deny_key` が `none` の行は下げずに積む） | `pushback`・`advise` は積まない |
| `run_entry`（入口） | `full-test` の拒否の後の guard で `_Deadline` を上げる差し替え → 出力が `full-test` の理由の拒否・計測の `carried` が `["full-test"]` | 拒否の前に割り込む → 今と同じ出力 | `on_deadline` が 2 引数のままの差し替え → `GUARD_FAILED` の拒否 |

## 先の拒否を見る guard（`ask-forward`・`relay-busy`）

満たす決定: 決定 6・7、受け入れ条件 12・13

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/domains/hooks/guards/ask.py` | 変更 | 変えない | `svc.denied` が空でなければ何も書かずに通す |
| `on_deadline` | 同上 | 変更 | `on_deadline(env, payload, denied: tuple[str, ...]) -> Optional[str]` | `denied` が空でないか `CHILD` でなければ None |
| `check` | `src/ndf/domains/hooks/guards/relay_busy.py` | 変更 | 変えない | `svc.denied` が空でなければ記録を書かずに None |
| `on_deadline` | 同上 | 変更 | `on_deadline(env, payload, denied: tuple[str, ...]) -> Optional[str]` | `denied` が空でなければ None。ほかは #4049 のとおり |

- `ask.check`
  - 事後条件: 判定の順は (1) `svc.denied` が空でなければ `passing(forwarded=[])`（中継・オーガナイザー・知らせのどれにも書かない）、(2) `relay_role` で上の表のとおり。`_recorded` は (2) で中継へ書けたときだけ置く（今のとおり）
- `ask.on_deadline`
  - 事後条件: `denied` が空でなければ None（先の拒否が出力に出るので、この guard は理由を足さない。`check` は書いていない）。`relay_role(env, payload.get("session_id"))` が `CHILD` でなければ None。ほかは今のとおり（記録し終えた payload なら None、そうでなければ同じ `tool_use_id` の質問を消して拒否）
- `relay_busy.check`・`relay_busy.on_deadline`
  - 事後条件: `denied` が空でなければ、#4049 の先に行う判定より前に None（ファイルに触れず、guard の表の読み込みと構文解析もしない）。#4049 の `_target(env, session_id)` は `relay_role(env, session_id) == CHILD` に置き換える

### 単体テストの観点

| 対象 | 正常 | 境界 | 異常 |
| --- | --- | --- | --- |
| `ask.check` | `denied` 空の `CHILD` で `question.json` を書く | `denied` が `("context-limit",)` で `question.json`・`questions/<レーン>.json`・送信待ちが増えない | — |
| `ask.on_deadline` | `denied` 空・記録前の `CHILD` で拒否 | `denied` ありで None・質問のファイルに触れない | `NESTED` で None |
| `relay_busy` | `denied` 空の背景の Bash で「1 件以上」 | `denied` が `("heredoc",)` で `busy-<起動>.json` が変わらない | `denied` ありの `on_deadline` で None（#4049 の `BUSY_START_FAILED` を返さない） |

## 手順書と表の説明

満たす決定: 決定 1・5

- `skills/development-workflow/references/relay.md` の「質問の hook とラッパーは、同じロックで順番をそろえる」の節に、hook が書く・消すのは子の会話（入力の `session_id` が `NDF_RELAY_SESSION`）だけで、入れ子の claude は中継の外として扱うことを 1 段落足す
- `contracts/hooks/guards.json` の `summary` の `fail_closed` の文に、「締め切りで打ち切られた実行は、それまでに確定した拒否（案内へ下げないもの）を出し、`on_deadline` は先に拒否した guard の名前を第 3 引数で受ける」を足す。`docs/contracts/hooks.md` は `ndf check --write-spec` で作り直す
- `hooks/main.py` と `hooks/dispatch.py` の冒頭の docstring の締め切りの記述を同じ約束に直す
