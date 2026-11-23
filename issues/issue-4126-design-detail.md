# #4126 の詳細設計: 環境変数の名前を契約のキーから引く

設計文書 [issue-4126-design.md](./issue-4126-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。どの PR に入れるかは設計文書の決定 4 の表にある。

## 地図

パッケージ・モジュール構成（触るファイルだけ）:

```text
plugins/ndf/
├── contracts/domains/env.json          … 列 key と中継の 3 行のキー
├── docs/contracts/env.md               … ndf check --write-spec で作り直す
├── src/ndf/
│   ├── identity.py                     … env_name・_env_keys・EnvNameError を足す
│   ├── checks/facts.py                 … 規則 env-key（env_key_findings）を足す
│   ├── checks/README.md                … 規則の表に env-key の行
│   ├── adapters/claude.py              … _inherited がキーで名前を得る
│   ├── domains/hooks/guards/
│   │   ├── relay_busy.py               … #4049 の _target が誤りを対象外にする
│   │   └── ask.py                      … #4049 の時点で relay_dir の誤りを書かずに通す
│   └── domains/relay/
│       ├── signals.py                  … LAUNCH_ENV を消し、読み手がキーで名前を得る。relay_role は誤りを NESTED に
│       ├── account_login.py            … AUTH_ENV を外し、関数の中で名前を引く
│       └── cli.py                      … ラッパーの起動の入口でキーを解き、誤りは config_broken
├── tests/identity/test_env_name.py     … 新設
└── tests/checks/test_facts.py          … env-key のテストを足す
```

依存の向き: `checks.facts` は今のとおり構文木と表を読み、`identity` を import しない（`env.json` は検査の走査で読む）。`identity` は標準ライブラリだけを import し、`contracts/domains/env.json` をファイルとして読む。`signals`・`account_login`（domains）と `claude`（adapters）は `identity` を import する。`identity` はどれも import しないので、輪は無い。`ndf.contracts` は import しない（hook の経路の import の閉包に jsonschema を入れない）。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `contracts/domains/env.json` | 変更 | 列 `key` を足し、中継の 3 行にキーを置く | — | — | — |
| `src/ndf/identity.py` | 変更 | 環境変数の名前を契約のキーから引く | 標準ライブラリ（`json`・`functools`） | モジュール `identity` | `env_name`・`_env_keys`（新設）、`EnvNameError`（新設） |
| `src/ndf/domains/relay/signals.py` | 変更 | 中継の名前をキーで得る | `identity` | モジュール `signals` | `relay_dir`・`relay_launch`・`launch_env`（変更）、#4049 の `relay_session`・#4061 の `relay_role` |
| `src/ndf/adapters/claude.py` | 変更 | 中継のマーカーの名前をキーで得る | `identity` | モジュール `claude` | `_inherited`（変更） |
| `src/ndf/domains/hooks/guards/relay_busy.py`・`ask.py` | 変更（#4049） | `fail_closed` の guard の頭の判定が契約の誤りを例外にしない | `relay.signals`・`identity` | モジュール `relay_busy`・`ask` | `_target`（#4049 の新設）、`check`・`on_deadline`（変更） |
| `src/ndf/domains/relay/account_login.py` | 変更 | ログインの子から外す名前を、子の環境を組む関数の中でキーから得る | `identity` | モジュール `account_login` | 定数 `AUTH_ENV`（削除）、子の環境を組む関数（変更） |
| `src/ndf/domains/relay/cli.py` | 変更 | ラッパーの起動の入口で 3 つのキーを解き、誤りを `config_broken` にする | `identity` | モジュール `cli` | ラッパーを起動する入口の関数（変更） |
| `src/ndf/checks/facts.py` | 変更 | キーの重なりと、コードが呼ぶキーを確かめる | 既存のまま | モジュール `facts` | `env_key_findings`（新設）、`check`（変更） |

## 契約のキーの列（`env.json`）

満たす決定: 決定 1・決定 4、受け入れ条件 3・4

`columns` に `"key": {"type": "string", "summary": "コードが名前を引くキー（表の中で 1 つの行だけが持つ）。キーを持つ行の名前は、コードが identity.env_name でキーから引き、文字列リテラルで書かない", "optional": true}` を足す。`facts.rows` は設定に無い列で絞ると `FactsError` を出すので、列は必ず `columns` に足す。行の値は設計文書の「データ構造」の表のとおりで、#4049 の実装が列と 2 行のキーを、#4062 の実装が `NDF_RELAY_LAUNCH` の行のキーを足す。生成文書 `docs/contracts/env.md` は `ndf check --write-spec` で作り直す。

## キーの読み手（`identity`）

満たす決定: 決定 2・決定 3、受け入れ条件 4・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `env_name` | `src/ndf/identity.py` | 新設 | `env_name(key: str) -> str` | キーの行の環境変数の名前を返す |
| `_env_keys` | `src/ndf/identity.py` | 新設 | `_env_keys() -> dict[str, tuple[str, ...]]` | `env.json` を読み、キー → そのキーを持つ行の名前の並びを返す（`functools.lru_cache` で 1 プロセスに 1 回） |
| `EnvNameError` | `src/ndf/identity.py` | 新設 | `class EnvNameError(LookupError)` | 契約が読めない・キーの行が 1 行でない |

- `env_name`
  - 事前条件: 無し（hook の経路から呼んでよい）
  - 事後条件: 返す名前は `env.json` の `key` が `key` の唯一の行の `id`
  - 副作用: 初回だけ `ROOT / "contracts/domains/env.json"` を読む
- `_env_keys`
  - 事後条件: `key` の列を持つ行だけを、表の順でキーごとに集める。`key` の無い行は返りに入らない。キーを持つ行の `id` が空でない文字列でなければ `EnvNameError`

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_env_keys` | 契約を読めない | ファイルが無い・読めない・JSON でない・`rows` が無い・キーを持つ行の `id` が空でない文字列でない | `read_text`・`json.loads`・`["rows"]` | `EnvNameError` を投げて呼び出し元へ返す | hook の経路は `relay_role` が受けて `NESTED` にする（中継とオーガナイザーの保存先へ書かない）。ラッパーと `relay status` へは届かない（`relay/cli.py` が import の時に読む `account_registry` などが `facts` で表を検証し、今のとおり `FactsError` で終わる。設計文書の決定 3） | 後始末なし。契約を直せば再実行できる |
| `env_name` | キーの行が 0 行 | キーの打ち間違い・行のキーの付け忘れ | `env_name` | `EnvNameError` を投げる（既定の名前へ戻さない） | hook の経路は上と同じ。ラッパーは起動の入口で `config_broken` で止まる。`relay status` は動く。ログインは子の環境を組む時点で誤りになる | 同上 |
| `env_name` | キーの行が 2 行以上 | 同じキーを 2 つの行に置いた | `env_name` | `EnvNameError` を投げる（先の行を使わない） | 同上 | 同上 |

`_env_keys` の失敗は `lru_cache` に残らない（例外は保存されない）ので、同じプロセスで呼び直すと読み直す。

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `env_name`・`_env_keys` | `ndf.contracts.facts.rows` | 新しいプロセスで import と呼び出しの時間を測った（設計文書の決定 2） | 使えない。jsonschema の読み込みで hook の 1 回の起動に約 54〜64 ms 足す |
| `env_name`・`_env_keys` | `ndf.domains.hooks.tables.load` | `rules.json` の `structure.layers` と `adapters/claude.py` の層を読んだ | 使えない。adapters は domains を import できない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `env_name` | キーが 1 行に当たれば、その行の `id` | `key` の無い行が並ぶ表でも、キーを持つ行だけを見る | 0 行・2 行以上で `EnvNameError` |
| `_env_keys` | 同じプロセスで 2 回呼んでもファイルを 1 回しか読まない | — | ファイルが無い・JSON でない・`rows` が無いで `EnvNameError` |

テストは `ROOT` を差し替えた一時の保存先に表を置いて打ち、`_env_keys.cache_clear()` で呼ぶごとに読み直させる。

## 名前を使う所（`signals`・`claude`・`account_login`）

満たす決定: 決定 1・決定 4、受け入れ条件 1・2・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `relay_dir` | `src/ndf/domains/relay/signals.py` | 変更（#4049） | 変えない | `env.get(identity.env_name("relay-dir"))` で読む |
| `relay_session` | 同上 | #4049 の新設 | #4049 の詳細設計のとおり | `env.get(identity.env_name("relay-session")) or None` で読む（無い・空は None。今の `relay_launch` と同じ利用者の入力の抑制コメントを付ける） |
| `launch_env` | 同上 | 変更（#4049・#4062） | #4049 の詳細設計のとおり | 返す辞書のキーを `identity.env_name` で得る。#4049 の時点では `NDF_RELAY_LAUNCH` の行がまだ無いので、そのキーだけ `LAUNCH_ENV` のまま |
| `relay_launch` | 同上 | 変更（#4062） | 変えない | `env.get(identity.env_name("relay-launch")) or None` で読む。`LAUNCH_ENV` を消す |
| `relay_role` | 同上 | #4061 の新設 | #4061 の詳細設計のとおり | `NDF_RELAY_DIR` と `NDF_RELAY_SESSION` の名前をキーで得て、`CHILD` と判定する前に `relay-launch`（#4062 の後）も解く。guard が `CHILD` の後に使うキーをここで解き切るので、後段の `relay_dir`・`relay_launch` は同じプロセスに残った表を引き、`EnvNameError` を上げない。どれかで `EnvNameError` を受けたら `NESTED` を返す（例外を上げない） |
| `_target` | `src/ndf/domains/hooks/guards/relay_busy.py` | #4049 の新設 | #4049 の詳細設計のとおり | `relay-dir` の名前の値と `relay_session`（`relay-session` のキー）を見る。`EnvNameError` を受けたら偽（対象外）。`check`・`on_deadline` の先に行う判定が例外を上げない。#4061 の後は `relay_role` へ移る |
| `ask.check`・`ask.on_deadline` | `src/ndf/domains/hooks/guards/ask.py` | 変更（#4049） | 変えない | `relay_dir` の呼び出しで `EnvNameError` を受けたら、中継にもオーガナイザーにも書かずに通す（`check` は `passing(forwarded=[])`、`on_deadline` は None）。#4061 の後は `relay_role` の `NESTED` に置き換わる |
| `_inherited` | `src/ndf/adapters/claude.py` | 変更（#4049） | 変えない | `env.get(identity.env_name("relay-dir"))` で読む |
| `AUTH_ENV` | `src/ndf/domains/relay/account_login.py` | 削除（#4049） | — | 最上位の組をやめ、今 `AUTH_ENV` を読む子の環境を組む関数の中で `(*facts.ids("env", child="account-drop"), "NDF_CLAUDE_ACCOUNT", identity.env_name("relay-dir"))` を組む。import の時に契約を読まない |
| ラッパーの起動の入口 | `src/ndf/domains/relay/cli.py` | 変更（#4049） | 変えない | 子を起動する前に `identity.env_name` で `relay-dir`・`relay-session`（#4062 の後は `relay-launch` も）を解く。`EnvNameError` なら `result.stopped(TOOL, "config_broken", …)` を返し、`launch.json` を書かず子も起こさない |

hook の経路は今も `signals` を通して `identity` を import しているので、import の閉包は変わらない。docstring とコメントの中の名前（`signals.py` の冒頭の表など）は説明の文として残してよい。受け入れ条件 3 の `git grep` は引用符を付けない名前で照らすので、当たりを実行に使う名前と説明の文に分けて読む。

## 規則 `env-key`（`checks/facts.py`）

満たす決定: 決定 3、受け入れ条件 5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `env_key_findings` | `src/ndf/checks/facts.py` | 新設 | `env_key_findings(scope: Scope, contracts_dir: str) -> list[Finding]` | `env.json` のキーの重なりと、コードの `identity.env_name` の呼び出しのキーを確かめる |
| `check` | 同上 | 変更 | 変えない | `env_key_findings` の結果を足す |

- `env_key_findings`
  - 事後条件: 次の 3 つを違反（規則 `env-key`）として返す。(a) `env.json` の `key` の値が 2 つ以上の行に現れる（`env.json` の行を指す）。(b) `scope.code()` の構文木で、`identity.env_name(...)`（`from ndf import identity` の `identity.env_name` と、`from ndf.identity import env_name` の `env_name`）の呼び出しの引数が 1 つの文字列リテラルでない。(c) その文字列リテラルのキーを持つ行が無い。`env.json` が読めないときは今の `fact-table` の違反に任せ、この関数は空を返す
  - 副作用: 無い

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `env_key_findings` | キーが重ならず、呼び出しのキーがどれも 1 行に当たれば空 | 別名の import（`from ndf.identity import env_name as en`）も呼び出しとして数える | 重なるキー・無いキー・変数を渡す呼び出しで違反 |

`checks/README.md` の規則の表に `env-key` の行を足す。
