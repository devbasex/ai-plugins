# #4089 の詳細設計: 文脈の量の読み手を 1 つにし、どの応答の量かと古さを返す

設計文書 [issue-4089-design.md](./issue-4089-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。

## 地図

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
src/ndf/
├── adapters/
│   └── transcript.py *        last_context・Anchor・ContextRead・read_tail（移す）
└── domains/
    ├── hooks/
    │   ├── context.py         context_tokens を消し、read_tail を adapters へ移す
    │   ├── verdict.py         advise に notes を足す
    │   └── guards/
    │       └── context_limit.py
    └── relay/
        ├── observe.py         context_tokens・_usage_total を消す
        └── wrapper.py         SectionState の組み立て
tests/
├── adapters/test_transcript.py *
└── domains/hooks/test_context.py・test_guards.py・domains/relay/ の該当テスト
```

依存の向き: `domains/hooks` と `domains/relay` が `adapters/transcript.py` を読み、`adapters/transcript.py` は標準ライブラリ（`json`・`os`・`dataclasses`）だけを読む。下の層から上の層への向きは無く、循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/adapters/transcript.py` | 新設 | Claude Code の会話の記録の末尾を読み、最後の応答の文脈の量と古さを返す | 標準ライブラリ | `ToolUse`・`Reply`・`Tail`・`ContextRead` | `last_context`（新設）・`read_tail`（移す） |
| `src/ndf/domains/hooks/context.py` | 変更 | hook の 1 回の事象の文脈 | `adapters/transcript.py` | `HookContext` | `context_tokens`（削除）・`read_tail`（削除して import へ） |
| `src/ndf/domains/hooks/verdict.py` | 変更 | guard の判定の値 | — | `Verdict` | `advise`（変更） |
| `src/ndf/domains/hooks/guards/context_limit.py` | 変更 | 文脈の量の guard | `adapters/transcript.py` | モジュール | `check`（変更） |
| `src/ndf/domains/relay/observe.py` | 変更 | ラッパーが子の画面と記録から読む事実 | — | モジュール | `context_tokens`・`_usage_total`（削除） |
| `src/ndf/domains/relay/wrapper.py` | 変更 | 中継のラッパー | `adapters/transcript.py` | `Wrapper` | `SectionState` の組み立て（変更） |

## 記録の読み手（`adapters/transcript.py`）

満たす決定: 決定 1・2・3・4、受け入れ条件 1〜6・11

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `last_context` | `src/ndf/adapters/transcript.py` | 新設 | `last_context(tail: str, anchor: Anchor) -> ContextRead` | 圧縮の境より後の最後の応答の文脈の量と、手がかりに照らした古さを返す |
| `read_tail` | 同上 | 移す（中身は変えない） | `read_tail(path: str, size: int) -> str` | 末尾 `size` バイトを読み、先頭の切れた行を捨てる。読めなければ空 |

- `last_context`
  - 事前条件: `tail` は `read_tail` の返した文字列。`anchor` は `ToolUse`・`Reply`・`Tail` のどれか（`Anchor = Union[ToolUse, Reply, Tail]`）
  - 事後条件: `state` が `current` か `behind` なら `tokens`・`message_id`・`timestamp` がそろう。`none` ならどれも None で `reason` がある。`current` のとき `reason` は None
  - 副作用: 無し（ファイルを開かない）

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `last_context` | 行が JSON でない | 書きかけの末尾・壊れた行 | `json.loads` の `ValueError` | 最後の応答より新しければ `none`（`partial`）、古ければ無視 | 無し | 次の読み取りで読み直せる |
| `last_context` | 返す応答の `usage` が object でない・`input_tokens` が整数でない・キャッシュの項目が整数以外の値 | 記録の形の変化 | 返す応答の 1 つを確かめる | `current` の候補なら `none`（`form`）、`behind` の候補なら `none`（照らしの理由のまま） | 無し | 形が戻るまで量は無し |
| `last_context` | 読む範囲に応答が無い | 圧縮の直後・末尾に収まらない大きな行・空の文字列 | 読み終えたとき | `none`（境に会ったなら `compacted`、それ以外は `empty`） | 無し | 次の応答の後に読める |
| `read_tail` | 読めない | ファイルが無い・権限・入出力の失敗（`OSError`） | `open` | 空を返す | 無し | 次の読み取りで読み直せる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `last_context` | 範囲に応答が無い・境に会った | `none`・`compacted` |
| `last_context` | 範囲に応答が無い・境に会わない | `none`・`empty` |
| `last_context` | 最後の応答より新しい行に読めない行 | `none`・`partial` |
| `last_context` | 照らしを通った最後の応答（`current` の候補）の `usage` の形が違う | `none`・`form` |
| `last_context` | 照らしに外れた最後の応答（`behind` の候補）の `usage` の形が違う | `none`・`unrecorded`（または `after`） |
| `last_context` | `ToolUse`・その ID の `tool_use` を持つ応答が範囲にある | 最後の応答の量・`current` |
| `last_context` | `ToolUse`・無い | 最後の応答の量・`behind`・`unrecorded` |
| `last_context` | `Reply`・文が None | 最後の応答の量・`behind`・`unrecorded` |
| `last_context` | `Reply`・最後の応答の文と同じ・その後に利用者の行が無い | 最後の応答の量・`current` |
| `last_context` | `Reply`・ほか | 最後の応答の量・`behind`・`unrecorded` |
| `last_context` | `Tail`・最後の応答の後に利用者の行が無い | 最後の応答の量・`current` |
| `last_context` | `Tail`・ある | 最後の応答の量・`behind`・`after` |

`usage` を確かめるのは、手がかりで `current` か `behind` を決めた後の、返す応答の 1 つだけである。`form` は `current` の候補の形の違いだけに出し、`behind` の候補の形の違いは照らしの理由（`unrecorded`・`after`）のまま `none` にする。#4068 が今回でない応答の欠けで会話ごと止めないためである。

- 繰り返しの終わる条件: 末尾から 1 行ずつ新しい順に読み、`compact_boundary` の行か `tail` の先頭で止める
- 計算量の注意: 行の数に比例する 1 回の走査で、`tail` は最大 256 KiB（`hook.transcript-tail`）である。`message.id` ごとのまとまりは辞書で持つ

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `ToolUse`・`Reply`・`Tail` | 型（凍結した dataclass） | `src/ndf/adapters/transcript.py` | — |
| `ContextRead` | 型（凍結した dataclass） | 同上 | 項目は設計文書の「データ構造」 |
| `USAGE_KEYS` | 定数 | 同上（`observe.py` から移す） | `("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens")` |
| `SYNTHETIC_MODEL` | 定数 | 同上 | `"<synthetic>"` |
| `COMPACT_SUBTYPE` | 定数 | 同上 | `"compact_boundary"` |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `last_context` | `domains/relay/observe.py` の `_lines_backward`・`_talk` | `grep -n "def _lines_backward\|def _talk" src/ndf/domains/relay/observe.py` | 使えない。ファイルを開いて読み、読めない行で例外（`_Unsure`）を投げる。止めてよい時点の判定の責務で、範囲の外 |
| `last_context` | `domains/hooks/tables.py` の `json_object` | `grep -n "def json_object" src/ndf/domains/hooks/tables.py` | 使えない。下の層から `domains` を読むことになる。`json.loads` で足りる |
| `last_context` | `domains/hooks/context.py` の `_record_text` | 同上の grep | 使えない（同じ理由）。文をつなぐ規則は `last_context` の中に置き、`_record_text` は `last_reply` のために残す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `last_context` | 設計文書のテスト設計 1〜5 | 境の行の直後に応答が 1 つだけある記録・応答の後の利用者の行が 0 行と 1 行 | 設計文書のテスト設計 6〜8。空の文字列で `empty` |
| `read_tail` | 今のテストを `tests/adapters/test_transcript.py` へ移す | `size` がファイルより大きい・ちょうど行の境 | 権限の無いファイルで空 |

## 判定の値（`verdict.advise`）

満たす決定: 決定 5、受け入れ条件 7

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `advise` | `src/ndf/domains/hooks/verdict.py` | 変更 | `advise(context: str, notes: Optional[dict] = None) -> Verdict` | 案内の判定に、計測に残すマーカーを載せられるようにする（`deny` と同じ形） |

- `advise`
  - 事後条件: `notes` を渡さなければ今と同じ `Verdict("advise", context=context)`、渡せば `notes` を持つ

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `advise` | `notes` を渡すと判定の記録（dispatch の後の行）に残る | `notes` を渡さない呼び出しは今と同じ記録 | — |

## 文脈の量の guard（`context_limit.check`）

満たす決定: 決定 5、受け入れ条件 7・8

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `src/ndf/domains/hooks/guards/context_limit.py` | 変更 | 変えない | `last_context(ctx.transcript_tail, ToolUse(<入力の tool_use_id>))` の量で判定し、判定の記録に量と状態を残す |

- `check`
  - 事前条件: 入力に `tool_use_id` が無い（空の）呼び出しは `ToolUse("")` を渡し、照らしは必ず外れる（`behind` か `none`）
  - 事後条件: `state` が `none` なら None（今の「量が読めない」と同じ）。それ以外は、量が上限以上で `_next_step` が偽なら `deny`、ほかは `passing`。#3995 の実装の後は、PostToolUse の帯（`decide_from ≤ tokens < limit`）の最初の 1 回だけ `advise`。`passing`・`deny`・`advise` の `notes` は `context_tokens` と `context_state` を持つ
  - 副作用: #3995 の実装の後は、PreToolUse で覚える値を `{"tokens": …, "state": …}` にする（#3995 の詳細設計の `remember` の `value`）。PostToolUse は `take` が返した値の `state` を使い、無ければ `behind` とみなす

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 量が無い | `last_context` の `none` | `state` | None を返す（判定しない） | 無し | 次の呼び出しで読み直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 設計文書のテスト設計 9・10 | 上限 − 1・上限、判断開始の値 − 1・判断開始の値、上限 − 1 の PostToolUse で知らせ上限で知らせない | `none`（`compacted`・`partial`・`form`）で None |

## ラッパーの読み取り（`wrapper.py`）

満たす決定: 決定 1・5、受け入れ条件 9

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `SectionState` の組み立て（今の `observe.context_tokens(...)` を呼ぶ箇所） | `src/ndf/domains/relay/wrapper.py` | 変更 | 変えない | `last_context(read_tail(str(path), self.s.transcript_bytes), Tail()).tokens` を `context_tokens` に渡す |

`switch_decide` は変えない。`state` が `none` なら `tokens` は None で、今の「量が読めない」と同じく引継ぎを求めない。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `SectionState` の組み立て | 記録が読めない | ファイルが無い・権限 | `read_tail` の `OSError` | 量を None にして見回りを続ける | 無し | 次の見回りで読み直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `SectionState` の組み立て → `switch_decide.decide` | 設計文書のテスト設計 11 | 上限 − 1・上限（`behind` の量で） | 権限の無い記録のファイルで例外にならず量は None |

## #4068 との受け渡し

満たす決定: 決定 6、受け入れ条件 10

#4068 の `keep_cache.last_turn`（#4068 の詳細設計）は、シグネチャと返り値を変えずに、中で `last_context(tail, Reply(last_message))` を呼ぶ。状態から `TurnRead` への対応は次のとおりで、ここが対応の唯一の保存先である。

| `last_context` の結果 | `last_turn` の結果 |
| --- | --- |
| `current` | 寿命とモデルを同じ `message_id` の行から読む。そろえば `TurnRead(LastTurn, None)`、欠ければ `TurnRead(None, "form")`。時刻が `started` より後なら `TurnRead(None, "newer")` |
| `none`（`form`） | `TurnRead(None, "form")` |
| `behind`・`none`（`unrecorded`・`compacted`・`partial`・`empty`） | `TurnRead(None, "unrecorded")`（一時的） |
