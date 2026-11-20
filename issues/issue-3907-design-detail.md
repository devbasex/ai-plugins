# #3907 の詳細設計: 学習に使う設定を Off へ書き換えてから起動する

設計文書 [issue-3907-design.md](./issue-3907-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。新設する関数が 2 つ以上（`settle` ほか）で、`Reading` と `Endpoint` の形を変えるため、別ファイルにした。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── src/ndf/adapters/
│   ├── training.py            # 外部の API との接続（読む・書く・読み直す）と ndf training check
│   └── training_cache.py      # 記録の読み書きと、記録を読んで起動を断る規則
├── contracts/
│   ├── limits.json            # codex-training.budget を消す
│   └── schemas/io/training.result.schema.json   # items に rewrites を足す
├── docs/contracts/commands.md # スキーマから生成（ndf check --write-spec）
├── README.md                  # 「学習の設定を確かめる」の節
└── tests/adapters/
    └── test_training_optout.py *
```

依存の向きは今のまま `training.py` → `training_cache.py` の一方向である。起動の口（`launch_slot`）は記録を読むときは `training_cache.py` だけを読み、記録が無いか期限切れのときだけ `training.py` を読む。`training_cache.py` は httpx を読まない。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/adapters/training.py` | 変更 | 学習の設定を読み、学習に使う設定なら書き換えて読み直す | `training_cache`・httpx・`run_log` | モジュール `training`、型 `Endpoint`・`Patch` | 新設: `Patch`・`settle`・`_claude_headers`・`_auth`・`_values`・`_send_off`・`codex_values`・`_grove_values`。変更: `Endpoint`・`_read_over_http`・`read_claude`・`read_codex`・`codex_training`・`_read_now`・`check`・`main`（docstring）。削除: `_grove_enabled` |
| `plugins/ndf/src/ndf/adapters/training_cache.py` | 変更 | 書き換えを含む記録を書き、学習に使う設定の断りに手で直す場所を載せる | `identity`・`run_log`・`contracts` | モジュール `training_cache`、型 `Reading`・`Rewrite` | 新設: `Rewrite`・`off_by_hand`・定数 `MANUAL_OFF`。変更: `Reading`・`Reading.item`・`_cached_verdict` |
| `plugins/ndf/contracts/schemas/io/training.result.schema.json` | 変更 | `items` の要素に任意の `rewrites` を足す | — | — | — |
| `plugins/ndf/contracts/limits.json` | 変更 | 使われていない `codex-training.budget` を消す | — | — | — |
| `plugins/ndf/docs/contracts/commands.md` | 変更 | `ndf check --write-spec` で作り直す | スキーマ | — | — |
| `plugins/ndf/README.md` | 変更 | 「読むだけ（書き換えません）」と「10 系の書き換えは 11.0.0 にはありません」を、書き換える振る舞いへ直す | — | — | — |
| `plugins/ndf/tests/adapters/test_training_optout.py` | 新設 | 書き換えの受け入れ条件 1〜6 を縛る | `training`・httpx の `MockTransport` | — | — |

## 書き換え（`training.py`）

満たす決定: 決定 1・2・3・7・9、受け入れ条件 1・2・3・4・5・6

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Patch` | `training.py` | 新設 | `class Patch(NamedTuple): url: str; params: Optional[dict[str, str]]; body: Optional[dict[str, bool]]` | 1 つのキーを false へ書き込む要求の形 |
| `Endpoint` | `training.py` | 変更 | `class Endpoint(NamedTuple): url: str; policy: str; values: Callable[[dict], Optional[dict[str, bool]]]; source: str; patch: Callable[[str], Patch]` | 読む口と書く口の組。`interpret`（真偽値 1 つ）を `values`（キーごとの真偽値）に替え、`patch` を足す |
| `_grove_values` | `training.py` | 新設（`_grove_enabled` を置き換える） | `(body: dict) -> Optional[dict[str, bool]]` | `{"grove_enabled": <真偽値>}`。真偽値でなければ None |
| `codex_values` | `training.py` | 新設 | `(settings: object) -> Optional[dict[str, bool]]` | 3 つのキーのうち応答にあるものの真偽値。形の判定は今の `codex_training` と同じ（`training_allowed` が真偽値で、ほかのキーはあれば真偽値） |
| `codex_training` | `training.py` | 変更（シグネチャは同じ） | `(settings: object) -> Optional[bool]` | `codex_values` が None なら None、そうでなければどれかが true か |
| `_claude_headers` | `training.py` | 新設 | `(env: Mapping[str, str], now: float) -> tuple[Optional[dict[str, str]], str]` | `read_claude` から、OAuth でない接続の判定とトークンと見出しの組み立てを切り出す |
| `_auth` | `training.py` | 新設 | `(runtime: str, env: Mapping[str, str], now: float) -> tuple[Optional[dict[str, str]], str]` | 担当の見出し（claude は `_claude_headers`、codex は今の `_codex_auth`） |
| `_values` | `training.py` | 新設 | `(client: httpx.Client, endpoint: Endpoint, headers: Mapping[str, str]) -> tuple[Optional[dict[str, bool]], str]` | `GET` を 1 回送り、キーごとの真偽値か読めない理由を返す |
| `_read_over_http` | `training.py` | 変更（シグネチャは同じ） | `(runtime, auth, client, endpoint) -> Reading` | `_values` を呼び、どれかが true なら `training` を true にする |
| `read_claude`・`read_codex` | `training.py` | 変更（シグネチャは同じ） | `(env, client, now) -> Reading` | `_auth` と担当の `Endpoint` 定数で `_read_over_http` を呼ぶ。読むだけ |
| `_send_off` | `training.py` | 新設 | `(client: httpx.Client, endpoint: Endpoint, headers: Mapping[str, str], key: str) -> str` | 1 つのキーを false へ書き込む。2xx なら空文字、そうでなければ失敗の理由 |
| `settle` | `training.py` | 新設 | `(runtime: str, env: Mapping[str, str], client: httpx.Client, now: float, clock: Callable[[], float] = time.time) -> Reading` | 読み、学習に使う設定なら true のキーだけを書き込んで 1 回読み直し、結果と `rewrites` を返す |
| `_read_now` | `training.py` | 変更（シグネチャは同じ） | `(runtime, env, now) -> Reading` | `read_setting` の代わりに `settle` を呼ぶ（確かめて書き換える） |
| `check` | `training.py` | 変更（シグネチャは同じ） | `(runtimes, env, client, now) -> dict[str, object]` | `settle` で確かめ、学習に使う設定の `summary` に担当ごとの理由と `off_by_hand` を載せる |

担当ごとの口は定数に置く（`_readers()` と同じ担当の集合）。

| 定数 | 中身 |
| --- | --- |
| `CLAUDE_ENDPOINT` | `Endpoint(CLAUDE_URL, "claude-training.call", _grove_values, "oauth/account/settings.grove_enabled", lambda key: Patch(CLAUDE_URL, None, {key: False}))` |
| `CODEX_ENDPOINT` | `Endpoint(CODEX_URL, "codex-training.call", lambda body: codex_values(body.get("settings")), "chatgpt/settings/user.training_allowed", lambda key: Patch(CODEX_PATCH_URL, {"feature": key, "value": "false"}, None))` |
| `CODEX_PATCH_URL` | `https://chatgpt.com/backend-api/settings/account_user_setting` |

- `settle`
  - 事前条件: `runtime` は確かめる口のある担当か、そうでない担当（そのときは `UNSUPPORTED` の `Reading` を返し、何も送らない）
  - 事後条件: 返す `Reading` は次のどれか。(a) 書き込みを送っていない: `read_setting` と同じ値で `rewrites` が空。(b) 送って読み直したら false: `training` が false、`reason` が `<source>（Off へ書き換えた: <キー>・…）`、`rewrites` が受け付けられたキーごとに 1 要素。(c) それ以外: `training` が true、`reason` が失敗の中身、`rewrites` は受け付けられたキーの分だけ
  - 副作用: ネットワーク（`GET` 1 回、true のキーがあれば `PATCH` をその数だけと `GET` 1 回）。書き込みを送ったときは `run_log.record("training", runtime=…, state="rewritten" か "rewrite-failed", keys=[…])`
- `_send_off`
  - 副作用: ネットワーク（`PATCH` 1 回。`follow_redirects=False`、上限は `endpoint.policy` の秒）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_send_off` | 応答が 2xx でない | スコープ不足（403）・知らないキー（422）・組織の設定 | `response.status_code` | 呼び出し元へ返す（`<キー> が HTTP <状態>`） | `settle` の `reason` に入り、`check` は `stopped`（`refused`）・終了コード 0 以外、起動の口は断る | 後始末なし。利用者が手で Off にして `ndf training check` を打てば再実行できる |
| `_send_off` | 通信の失敗 | 時間切れ・接続の失敗 | `httpx.HTTPError` | 呼び出し元へ返す（`<キー> が通信の失敗（<例外の型名>）`） | 同上 | 同上 |
| `settle` | 読み直しても true | 書き込みが保存されない・反映が遅れる | 読み直しの `values` | 呼び出し元へ返す（`書き換えた後も学習に使う設定のまま: <キー>`。`after` は true） | 同上 | 同上 |
| `settle` | 読み直せない | 読み直しの `GET` が 2xx でない・形が違う | `_values` の理由 | 呼び出し元へ返す（`書き換えた後に読み直せない（<理由>）`。`after` は null）。`training` は true | 同上 | 同上 |
| `settle` | 最初の読み取りで確かめられない | トークンが無い・OAuth でない接続・形が違う | `_auth`・`_values` | 今と同じ `training: None` の `Reading` を返し、書き込まない | 今と同じ（`precondition`） | 今と同じ |

`settle` は例外を出さない。`reason` に入れるのはキーの名前・HTTP の状態・例外の型名・`_values` の理由だけで、見出し・応答の本文・URL の問い合わせの文字列は入れない（受け入れ条件 6）。

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `settle` | 見出しが無い・最初の読み取りが読めない | (a) `training: None`。送らない |
| `settle` | 読めて true のキーが無い | (a) `training: False`、`reason` は `source`。送らない |
| `settle` | true のキーがあり、すべて受け付けられ、読み直して全キーが false | (b) `training: False` |
| `settle` | 1 つでも受け付けられなかった | (c) `training: True`。読み直しは行い、`after` を埋める。`reason` は受け付けられなかったキーの理由を `・` でつなぐ |
| `settle` | すべて受け付けられ、読み直しで true のキーが残る | (c) `training: True`、`書き換えた後も学習に使う設定のまま: <残ったキー>` |
| `settle` | すべて受け付けられ、読み直せない | (c) `training: True`、`書き換えた後に読み直せない（<理由>）` |

- 繰り返しの終わる条件: `PATCH` は最初の読み取りで true だったキーの数（claude は 1、codex は最大 3）だけ送り、読み直しは 1 回で終える。再試行しない
- 時間: 1 回の要求は `claude-training.call`・`codex-training.call`（3 秒）で打ち切る。最悪は claude 9 秒・codex 15 秒（決定 7）

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 並列のレーンの起動の口が、記録の無い同じ担当でそれぞれ `ensure` を呼ぶ | アカウントの学習の設定・`training.json` | 取らない | 書き込みは false を書くだけで、何度送っても結果が同じ。`training.json` は今の `save` が一時ファイルからの置き換えで書くので、壊れずに後の書き手の内容が残る |

### ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `settle` | 担当・書き込んだキー・成否（`rewritten` / `rewrite-failed`） | 実行の記録（`run_log.record`。開いているときだけ） | トークン・`account_id`・応答の本文 |
| `check` | 担当ごとの `rewrites` と `reason`、`summary` に書き換えた担当か失敗の理由と `off_by_hand` | 結果 JSON | 同上 |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `_values` | `_get`（`GET` と JSON の読み取り） | `training.py` を読んだ | 使う |
| `_send_off` | `_get` | 同上 | 使えない（`GET` 専用で、200 だけを成功とし本文を JSON として読む）。httpx の `client.request` を直接呼ぶ |
| `_claude_headers`・`_auth` | `_claude_token`・`_codex_auth` | 同上 | 使う（今の `read_claude` の見出しの組み立てを切り出すだけ） |
| `codex_values` | `codex_training` の形の判定 | 同上 | 判定を `codex_values` へ移し、`codex_training` がそれを呼ぶ |
| `settle` | `read_setting`・`_read_over_http` | 同上 | 最初の読み取りの形は `_read_over_http` と同じ規則（`_values` を共有する） |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `settle`（claude） | true → 202 → false で `training: False`・`rewrites` 1 要素・送った本文が `{"grove_enabled": false}`・見出しが読むときと同じ | false なら要求は `GET` 1 回だけ | 403・通信の失敗・読み直しても true・読み直しが 500 の 4 つで `training: True` と理由 |
| `settle`（codex） | 2 キーが true → その 2 キーだけ `feature`・`value=false` で送られ、`rewrites` 2 要素 | 3 キーとも true・1 キーだけ true・`codex_training_allowed_v2` が応答に無い | 1 キー目が 422 でも残りを送る、`rewrites` は受け付けられたキーだけ |
| `settle`（送らない） | — | — | トークンが無い・API キーのログイン・OAuth でない接続・形が違う応答で、要求が `GET` だけか 0 件 |
| `check` | 書き換えて `ok`、`items` に `rewrites`、出力がスキーマを通る | — | 失敗で `stopped`（`refused`）、`summary` に `MANUAL_OFF` の場所と `ndf training check --runtime <担当>` |
| `_read_now` | 記録が無いときの起動の口が、差し替えなしの `_read_now` で書き換えて起動を続け、記録に `rewrites` が残る（HTTP は `httpx.Client` の差し替えで作る） | — | 書き換えに失敗した後の起動の口の理由に、記録の `reason` と場所とコマンドが載る |
| 秘密 | 記録・出力・理由の文にトークンと `account_id` が無い | — | — |

## 記録（`training_cache.py`）

満たす決定: 決定 4・5・6、受け入れ条件 1・2・4・6・7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Rewrite` | `training_cache.py` | 新設 | `class Rewrite(NamedTuple): key: str; before: bool; after: Optional[bool]; at: str` と `item() -> dict[str, object]` | 書き換えた 1 つのキーの記録 |
| `Reading` | `training_cache.py` | 変更 | `class Reading(NamedTuple): runtime: str; training: Optional[bool]; reason: str; rewrites: tuple[Rewrite, ...] = ()` | 4 つ目の項目を既定値つきで足す。今の 3 引数の呼び出しはそのまま通る |
| `Reading.item` | `training_cache.py` | 変更 | `(checked_at: str) -> dict[str, object]` | `rewrites` が空でなければ `"rewrites": [r.item() for r in rewrites]` を足す。空なら今と同じ 4 項目 |
| `off_by_hand` | `training_cache.py` | 新設 | `(runtime: str) -> str` | `MANUAL_OFF[runtime]` と `ndf training check --runtime <担当>` から、手で Off にして確かめ直す案内の文を作る |
| `_cached_verdict` | `training_cache.py` | 変更（シグネチャは同じ） | `(runtime, entry, now, expected) -> Verdict` | 学習に使う設定の記録の断りを `<担当> のアカウントが入力を学習に使う設定なので起動しない（<記録の reason>）。<off_by_hand>` に変える。ほかの分岐は変えない |

- `off_by_hand`
  - 事前条件: `runtime` は `CHECKED_RUNTIMES` の担当（`MANUAL_OFF` のキーと `CHECKED_RUNTIMES` が食い違えば import で止める。今の `_readers()` の照らし合わせと同じ形）

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `MANUAL_OFF` | 定数（担当 → 手で Off にする場所の文） | `training_cache.py` | claude: claude.ai の設定の「プライバシー」の「Help improve Claude」。codex: ChatGPT の設定の「データコントロール」の「すべての人のためにモデルを改善する」と、Codex の設定のデータの学習（環境を含める設定） |
| `Reading.rewrites` | 型の項目 | `training_cache.py` | `()` |

`training.json` の項目は `Reading.item()` に `context` を足したもので、`rewrites` の形は設計文書の「入出力のコントラクト」と同じである。読む側（`_cached_verdict`）は `rewrites` を読まない。

### 異常系の処理

`Rewrite`・`off_by_hand`・`Reading.item` は純粋な処理で、失敗の形を持たない。`_cached_verdict` の失敗の扱いは変えない。

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `Reading.item` | `rewrites` があるとき 5 項目、無いとき今と同じ 4 項目 | `after` が null の要素 | — |
| `_cached_verdict` | 学習に使う設定の記録で、理由に記録の `reason`・`MANUAL_OFF` の場所・`ndf training check --runtime <担当>` が載る | `reason` が空の今の記録 | — |
| `off_by_hand` | claude・codex で場所とコマンドが載る | — | — |

## コントラクトと文書

満たす決定: 決定 4・7、受け入れ条件 1・2

| ファイル | 変える中身 |
| --- | --- |
| `training.result.schema.json` | `items.items.properties` に `rewrites` を足す。型は配列、要素は `additionalProperties: false` で `key`（string）・`before`（boolean）・`after`（boolean か null）・`at`（string）を `required` にする。`items` の要素の `required` は今の 4 つのまま |
| `limits.json` | `codex-training.budget` の行を消す。`claude-training.call`・`codex-training.call` は値も説明（「読み書き」）も変えない |
| `commands.md` | `ndf check --write-spec` で作り直す（`items` の要素の一覧に `rewrites`（array）が足される） |
| `README.md` | 「学習の設定を確かめる」の節を、学習に使う設定なら Off へ書き換えて読み直すこと・失敗したら断って手で直す場所を示すことへ直し、「10 系の書き換えは 11.0.0 にはありません」の行を消す |
| `training.py` の冒頭の docstring | 「設定を読むだけで書き換えない」を、読んで学習に使う設定なら書き換えて読み直す振る舞いへ直す |

### 単体テストの観点

| 対象 | 正常 | 異常 |
| --- | --- | --- |
| スキーマ | `rewrites` を持つ `check` の出力が通る（全コマンドの出力をスキーマで確かめる今のテストの仕組みに乗る） | 要素に `at` が無い・知らない項目がある `rewrites` が通らない |
| `limits.json` | 今の `limits` の検査が通る | — |
