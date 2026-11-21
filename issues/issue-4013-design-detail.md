# #4013 の詳細設計: 修正担当の会話を続ける

設計文書 [issue-4013-design.md](./issue-4013-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。型の関係は設計文書の「構造」のクラス図、要素をまたぐ順序は「処理の流れ」にあり、ここでは描き直さない。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   └── limits.json
├── skills/cross-review/docs/
│   └── 02-fix-and-rotation.md
├── src/ndf/
│   ├── adapters/
│   │   └── claude.py
│   └── domains/crossreview/
│       ├── drive.py
│       └── resume_decide.py *
└── tests/
    ├── adapters/
    │   ├── test_claude_args.py
    │   └── test_claude_reply.py
    └── domains/crossreview/
        ├── test_crossreview_resume.py *
        └── test_crossreview_run.py
```

依存の向き: `drive.py` → `resume_decide.py`・`claude.py`・`ndf.adapters.agents`・`ndf.contracts.limits` の一方向である。`resume_decide.py` は標準ライブラリだけに依存し、`claude.py` も `drive.py` も読まない（純粋な関数）。`claude.py` は今の依存（`agents.usage_limit` など）に何も足さない。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `plugins/ndf/src/ndf/adapters/claude.py` | 変更 | 会話の指定から `--session-id`・`--resume` を組み、結果の行から文脈の大きさと会話が見つからない印を読む | 今のまま | `ClaudeCall`（変更）・`Session`（新設）・`NO_CONVERSATION`（新設の定数） | `session_args`（新設）・`result_line`（新設）・`context_tokens`（新設）・`conversation_missing`（新設）・`minimal_args`（変更）・`_full_args`（変更）・`claude_launch`（変更） |
| `plugins/ndf/src/ndf/domains/crossreview/resume_decide.py` | 新設 | 前の修正担当の記録と上限から、会話を続けるかと続けない理由を決める | 無し（標準ライブラリだけ） | `Previous`・`Choice`・理由の定数 | `choose`（新設） |
| `plugins/ndf/src/ndf/domains/crossreview/drive.py` | 変更 | 修正担当の会話の指定を決めて起動し、会話が見つからなければ 1 度だけ新しい会話で起動し直し、記録を `_Run` に残す | `resume_decide`・`claude`・`agents`・`limits` | `_Run`（`fixer_last` を足す）・`FRESH_SUFFIX`・`RESUME_CONTEXT`（新設の定数） | `_fixer_launch`（変更）・`_fix`（変更） |
| `plugins/ndf/contracts/limits.json` | 変更 | 続ける文脈の上限 `cross-review.fixer.resume-context`（`count` 150000）を足す | — | — | — |
| `plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md` | 変更 | 「修正」に会話の続け方・続けない条件・起動し直しの表の行と、`process` の文を足す | — | — | — |
| `plugins/ndf/tests/adapters/test_claude_args.py` | 変更 | 会話の指定の引数と `resume` の置き換え | — | — | — |
| `plugins/ndf/tests/adapters/test_claude_reply.py` | 変更 | 結果の行・文脈の大きさ・会話が見つからない印の読み取り | — | — | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_resume.py` | 新設 | `choose` の条件ごとの判断 | — | — | — |
| `plugins/ndf/tests/domains/crossreview/test_crossreview_run.py` | 変更 | 駆動の 2 ラウンドの引数・続けない条件・起動し直し・状態ファイルの形・相手役の引数 | — | — | — |

## claude の起動の引数

満たす決定: 決定 2、受け入れ条件 1・5

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Session` | `plugins/ndf/src/ndf/adapters/claude.py` | 新設（型） | `class Session(NamedTuple): id: str; resume: bool` | 1 回の起動が使う会話の ID と、その会話を続けるか（偽なら新しく始める）を持つ |
| `ClaudeCall` | 同上 | 変更（型） | `resume: Optional[str]` を `session: Optional[Session] = None` に置き換える | 起動の中身。`__post_init__` から `resume` の権限の検査を外し、`tools` の検査だけを残す |
| `session_args` | 同上 | 新設 | `session_args(session: Optional[Session]) -> list[str]` | 会話の指定の引数。None は空、`resume` が真なら `["--resume", id]`、偽なら `["--session-id", id]` |
| `minimal_args` | 同上 | 変更 | `minimal_args(system: str, stream: bool = False, session: Optional[Session] = None) -> list[str]` | 最小構成の引数。`session` が None なら今のとおり `--no-session-persistence` を付け、指定があれば付けずに `session_args(session)` を `--system-prompt <system>` の後ろに足す |
| `_full_args` | 同上 | 変更 | 今のまま | 末尾の `["--resume", call.resume]` を `session_args(call.session)` に置き換える（`full` は今も `--no-session-persistence` を付けない） |
| `claude_launch` | 同上 | 変更 | 今のまま | 最小構成の起動で `minimal_args(call.system, call.stream, call.session)` を呼ぶ |

- `minimal_args`
  - 事後条件: 返す列に `--no-session-persistence` と `--session-id`・`--resume` が同時に入ることは無い
- `Session`
  - 事前条件: `id` は呼び手が `uuid.uuid4()` から作る文字列か、前の起動に渡した `id`。形の検査は claude が行う（STD2: must be a valid UUID）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `ClaudeCall` | `ValueError` | `tools` の権限の組み合わせの誤り（今のまま） | `__post_init__` | 呼び出し元へ返す | 呼び手の誤り（プログラムの誤り） | 再実行の対象ではない |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `session_args` | 続ける指定で `--resume <ID>`、新しい会話で `--session-id <ID>` | None で空 | — |
| `minimal_args`・`claude_args` | 権限 `tools`・`judge` に会話の指定を渡すと `--no-session-persistence` が無く会話の引数がある。権限 `full` に続ける指定を渡すと末尾が `--resume <ID>` | 指定を渡さない起動の引数が今と同じ（`MINIMAL_HEAD` のまま） | `tools` を権限 `tools` の外で渡すと `ValueError`（今のまま）。権限 `judge` に会話の指定を渡しても断らない |

## 結果の行の読み取り

満たす決定: 決定 3・決定 4、受け入れ条件 2・3

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `result_line` | `plugins/ndf/src/ndf/adapters/claude.py` | 新設 | `result_line(text: str) -> Optional[dict[str, Any]]` | 出力の最後の行が `type: result` の JSON のオブジェクトならそれを返し、そうでなければ None |
| `context_tokens` | 同上 | 新設 | `context_tokens(result: Optional[Mapping[str, Any]]) -> Optional[int]` | 結果の行の `usage.iterations` の最後の要素の `input_tokens`・`cache_read_input_tokens`・`cache_creation_input_tokens` の和。どれかが無い・整数でない・`iterations` が空なら None |
| `conversation_missing` | 同上 | 新設 | `conversation_missing(out_text: str, err_text: str) -> bool` | 結果の行の `errors` の文字列か標準エラーに `NO_CONVERSATION` があるか |

- `result_line`
  - 行の区切りは LF だけで割る（`agents._denials_warning` と同じ。`splitlines()` は本文の U+2028・U+2029 でも割る）。末尾の空白を落としてから最後の行を読む

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `result_line` | 最後の行が JSON でない・オブジェクトでない・`type` が `result` でない | 修正担当が途中で止まった（タイムアウト・監視の停止）・出力が空 | `json.loads` の `JSONDecodeError` を捕まえる | 既定値で続ける（None） | 無し。呼び手の `choose` が「前の会話の文脈を読めない」で新しい会話にする | 読むだけ。再実行できる |
| `context_tokens` | 和を出せない | `usage` の形の違い（claude の版の変化） | 型の確かめ | 既定値で続ける（None） | 同上 | 同上 |
| `conversation_missing` | — | — | — | 偽を返す（印が見つからなければ起動し直さない） | 無し | — |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `NO_CONVERSATION` | 定数 | `plugins/ndf/src/ndf/adapters/claude.py` | `"No conversation found with session ID"` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `result_line` | `agents._denials_warning`（最後の行の JSON を読む）・`claude._payload`（1 行の JSON を読む） | `grep -n 'json.loads(lines\[-1\])' plugins/ndf/src/ndf/adapters/agents.py`・`claude.py` を読んだ | 使えない。前者は非公開で拒否の警告だけを返し、後者は `--output-format json` の 1 行の出力を前提に誤りの回答を返す。読み方（LF で割る）だけを合わせる |
| `context_tokens` | 無し | `grep -rn 'iterations' plugins/ndf/src` が 0 件 | 新設する |
| `conversation_missing` | `agents.usage_limit`（出力の中の印を探す） | `agents.py` を読んだ | 使えない。利用上限の正規表現の一覧で、会話の欠けの印を持たない |
| `session_args` | `domains/relay/wrapper.py` の `--resume`・`--session-id` の組み立て（`head = ["--resume", ...] if req.resume else ["--session-id", ...]`） | `grep -rn 'session-id' plugins/ndf/src` | 使えない。relay の対話のラッパーの起動の要求の型に結びつき、`adapters` から `domains` を読むと依存の向きが逆になる。同じ形を `claude.py` に置く |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `result_line` | stream-json の 1 歩ごとの行の後の結果の行を返す。`--output-format json` の 1 行も返す | 末尾に改行が続く。本文に U+2028 を持つ行 | 最後の行が `type: assistant`・JSON でない・空の出力で None |
| `context_tokens` | 実測の形（`input_tokens` 2・`cache_read_input_tokens` 130418・`cache_creation_input_tokens` 695）で 131115 | `iterations` が 1 要素 | `usage` が無い・`iterations` が空・値が文字列で None |
| `conversation_missing` | 結果の行の `errors` に印がある・標準エラーに印がある | 印の後ろに ID が続く文 | どちらにも無いと偽。`errors` がリストでなくても例外にならない |

## 続けるかの判断

満たす決定: 決定 3、受け入れ条件 2

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Previous` | `plugins/ndf/src/ndf/domains/crossreview/resume_decide.py` | 新設（型） | `class Previous(NamedTuple): session_id: str; finished: bool; taken: bool; context: Optional[int]` | その起動で最後に起動した修正担当の会話の ID・DONE で終わったか・結果を取り込めたか・最後の文脈のトークン数 |
| `Choice` | 同上 | 新設（型） | `class Choice(NamedTuple): resume: bool; reason: Optional[str]` | 続けるか。続けないときは理由（続けるときは None） |
| `choose` | 同上 | 新設 | `choose(previous: Optional[Previous], cap: int) -> Choice` | 前の記録と上限から、続けるかと理由を決める（純粋な関数） |

### 処理の手順

上の行から順に見て、最初に当たった行の結果を返す。

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `choose` | `previous` が None | 新しい会話（`FIRST`「その起動で初めて」） |
| `choose` | `finished` が偽 | 新しい会話（`FAILED`「前の修正担当が正常に終わらなかった」） |
| `choose` | `taken` が偽 | 新しい会話（`NOT_TAKEN`「前の修正の結果を取り込めなかった」） |
| `choose` | `context` が None | 新しい会話（`UNREAD`「前の会話の文脈を読めない」） |
| `choose` | `context > cap` | 新しい会話（`LARGE`「前の会話の文脈が上限を超えた」） |
| `choose` | それ以外（`context <= cap`） | 続ける（理由 None） |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `FIRST`・`FAILED`・`NOT_TAKEN`・`UNREAD`・`LARGE` | 定数（理由の文） | `plugins/ndf/src/ndf/domains/crossreview/resume_decide.py` | 上の表の括弧の文 |
| `cross-review.fixer.resume-context` | 設定値（`count`） | `plugins/ndf/contracts/limits.json` | 150000（`summary` に「cross-review の修正担当が前のラウンドの会話を続ける、前の会話の最後の文脈の上限（トークン）。超えたら新しい会話で始める」） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `choose` | `fixgate_decide.require_fix_for_changes` | `plugins/ndf/src/ndf/domains/crossreview/fixgate_decide.py` を読んだ | 使えない。ラウンドを始める前に修正の記録が要るかを決める判断で、入力（前のラウンドの判定・修正の記録・残った結果ファイル）も結果（マージする・止める）も違う |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `choose` | DONE・取り込めた・文脈 131115・上限 150000 で続ける | 文脈が上限ちょうどで続け、上限 + 1 で `LARGE`。文脈 0 で続ける | None・`finished` 偽・`taken` 偽・`context` None のそれぞれで、表の理由の新しい会話。`finished` と `taken` が両方偽のときは `FAILED`（表の上の行が先） |

## 修正担当の起動

満たす決定: 決定 1・決定 4・決定 5、受け入れ条件 1・2・3・4

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_Run` | `plugins/ndf/src/ndf/domains/crossreview/drive.py` | 変更（型） | `fixer_last: Optional[resume_decide.Previous] = None` を足す | 1 回の起動の中で最後に起動した修正担当の記録を持つ。状態ファイルへは書かない |
| `_fixer_launch` | 同上 | 変更 | `_fixer_launch(run: _Run, round_no: int, prompt: str, session: claude.Session, suffix: str = "-fix") -> agents.Launch` | 今の起動に会話の指定を渡し、出力の名前を `round<n><suffix>` にする。system・Tool・逐次の出力・監視の上限は今のまま |
| `_fix` | 同上 | 変更 | 今のまま | 会話の指定を決めて起動し、会話が見つからなければ 1 度だけ起動し直し、記録と過程の行を残す。結果の取り込みと返り値は今のまま |

- 事後条件: `run.fixer_last` は修正担当を起動したラウンドの後、必ずそのラウンドで最後に起動した会話を指す。`run.data`（状態ファイルの中身）に会話の ID は入らない
- 副作用: 外部コマンド（`claude -p`）を最大 2 回起動する。状態の保存先に `round<n>-fix.*`、起動し直したときは `round<n>-fix-new.*` の出力を書く。claude の設定の保存先に会話の記録が残る

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `_fix` | 続けた起動が会話を見つけられない | アカウントの切り替えで claude の設定の保存先が変わった・会話の記録が消された | `claude.conversation_missing` と結果ファイルの有無 | 再試行（同じラウンドで新しい会話で 1 度だけ） | `process` の修正担当の行に「新しい会話: 前の会話が見つからない」。終了コードは今のまま | 1 回目の出力は `round<n>-fix.*` に残る。起動し直しは 1 回まで |
| `_fix` | 起動し直した起動も結果ファイルを書かない | 起動し直した修正担当の失敗 | 結果ファイルの有無（今のまま） | 呼び出し元へ返す（None。ラウンドは `errored`） | 今の注意「修正担当が結果を書かなかった」 | 次の起動で記録無しから新しい会話で始まる |
| `_fix` | 続けた起動がタイムアウト・誤りで終わり結果ファイルが無い | 修正担当の失敗（会話の欠けの印が無い） | 結果ファイルの有無 | 呼び出し元へ返す（None。起動し直さない） | 今の注意 | 同上 |
| `_fix` | 上限の値を読めない | `limits.json` に方針が無い | `limits.value` の `LimitsError` | 止める（例外を呼び出し元へ） | 今の `limits` の誤りと同じ | `limits.json` を直して打ち直す |

### 処理の手順

`_fix` は次の順で進む。この手順は、今の「起動の前に残った結果ファイルを退避する」の後、「結果ファイルが無ければ注意に載せて None」の前に入る。

1. `choice = resume_decide.choose(run.fixer_last, int(limits.value(RESUME_CONTEXT, "count")))`
2. 会話の指定で `_fixer_launch` を組み、起動する。続けるなら `claude.Session(run.fixer_last.session_id, True)`、続けないなら `claude.Session(str(uuid.uuid4()), False)` を渡す
3. 続けた起動で結果ファイルが無く、`claude.conversation_missing(agents.tail(out), agents.tail(err))` が真なら、1 度だけ起動し直す。新しい `Session(str(uuid.uuid4()), False)` を `FRESH_SUFFIX` で組み、理由を「前の会話が見つからない」にする
4. 最後に起動した方の起動の記録を置く。式は `run.fixer_last = Previous(<その ID>, status.state == DONE, False, claude.context_tokens(claude.result_line(agents.tail(out))))` である
5. 結果を取り込めたら `run.fixer_last` の `taken` を真にする。過程の行（`_note`）の文の後ろに `（会話を続けた）` か `（新しい会話: <理由>）` を付ける。結果無しの行にも付ける

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `FRESH_SUFFIX` | 定数 | `plugins/ndf/src/ndf/domains/crossreview/drive.py`（`RETRY_SUFFIX` の隣） | `"-fix-new"`（コメント「会話が見つからず新しい会話で起動し直した修正担当の出力の名前の後ろ（`round<n>-fix-new`）」） |
| `RESUME_CONTEXT` | 定数（上限の設定の名前） | 同上（`DOC_LINES` の隣） | `"cross-review.fixer.resume-context"` |
| `MISSING` | 定数（理由の文） | 同上 | `"前の会話が見つからない"` |

### 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じ worktree で動く別の claude（利用者のセッション・別の cross-review） | claude の設定の保存先の会話の記録 | 取らない。会話は NDF が作った uuid4 の ID で指し、作業ディレクトリの最後の会話を拾わない（決定 1） | 中断した起動の会話は続けない（`_Run` のメモリだけで持つ）。打ち直した起動は記録無しから始まる |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `_fixer_launch` | 渡した会話の指定が引数に載り、出力の名前が `round<n><suffix>` | 既定の `suffix` で `round<n>-fix` | — |
| `_fix`（駆動のテスト） | 2 ラウンド目が 1 ラウンド目の ID の `--resume` で起動され、過程に「会話を続けた」 | 1 ラウンド目の文脈が上限ちょうどで続け、上限 + 1 で新しい会話 | 会話の欠けの印で `round<n>-fix-new` の起動が 1 回だけ走り修正を取り込む。印の無い失敗・起動し直しの失敗で起動し直さない。状態ファイルの `rounds`・`findings` に会話の ID が無い。相手役の引数に会話の引数が無い |
