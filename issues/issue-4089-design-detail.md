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

依存の向き: `domains/hooks` と `domains/relay` が `adapters/transcript.py` を読み、`adapters/transcript.py` は標準ライブラリ（`json`・`os`・`dataclasses`・`datetime`）だけを読む。下の層から上の層への向きは無く、循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `src/ndf/adapters/transcript.py` | 新設 | Claude Code の会話の記録の末尾を読み、最後の応答の文脈の量と古さを返す | 標準ライブラリ | `ToolUse`・`Reply`・`Tail`・`ContextRead` | `last_context`・`timestamp_seconds`（新設）・`read_tail`（移す） |
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
| `timestamp_seconds` | 同上 | 新設 | `timestamp_seconds(text: str) -> Optional[float]` | 記録の `timestamp` を UNIX 秒に直す。`last_context` の形の確かめと #4068 の `last_turn` が同じ関数を使う |
| `read_tail` | 同上 | 移して先頭の行の扱いを直す | `read_tail(path: str, size: int) -> str` | 末尾 `size` バイトを読み、行の途中から始まるときだけ先頭の切れた行を捨てる。読めなければ空 |

- `last_context`
  - 事前条件: `tail` は `read_tail` の返した文字列。`anchor` は `ToolUse`・`Reply`・`Tail` のどれか（`Anchor = Union[ToolUse, Reply, Tail]`）
  - 事後条件: `state` が `current` か `behind` なら `tokens`・`message_id`・`timestamp`・`entry` がそろい、どれも最後の応答の採用行（最も新しい物理の行）から読んだ値で、`entry` はその行を `json.loads` した object そのものである。`timestamp_seconds(timestamp)` は None でない。`none` ならどれも None で `reason` がある。`current` のとき `reason` は None
  - 副作用: 無し（ファイルを開かない）
- `timestamp_seconds`
  - 事後条件: 末尾の `Z` を `+00:00` に替えて `datetime.fromisoformat` で解く。時差を持つ時刻なら `.timestamp()` の値、`ValueError` か時差が無い（`tzinfo` が None）なら None。例外を投げない
- `read_tail`
  - 事後条件: `start = max(0, 大きさ − size)`。`start` が 0 なら全体。`start > 0` なら `start − 1` から読み、先頭のバイトが改行なら残りの全体（先頭の行は完全な行として残す）、改行でなければ最初の改行の次から。読んだ範囲に改行が無ければ空。`OSError` なら空
  - 今の実装との違い: 今は `end > size` で改行があれば、読み始めが行頭でも先頭の行を捨てる。窓の先頭にだけ応答があり後を Tool の結果が占める記録で、ラッパーが上限以上の量を読めなくなるので直す（設計文書の「入出力のコントラクト」）。`ctx.transcript_tail` を使うほかの読み手には、完全な行が 1 行増えるだけである

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `last_context` | 行が JSON でない | 書きかけの末尾・壊れた行 | `json.loads` の `ValueError` | 最後の応答より新しければ `none`（`partial`）、古ければ無視 | 無し | 次の読み取りで読み直せる |
| `last_context` | 採用行の `usage` が object でない・`input_tokens` が整数でない・キャッシュの項目が整数以外の値、`message.id` が無い・空・文字列でない、`timestamp` が無い・文字列でない・`timestamp_seconds` が None（解けない・時差が無い） | 記録の形の変化 | 採用行の 1 行だけを確かめる | `current` の候補なら `none`（`form`）、`behind` の候補なら `none`（照らしの理由のまま） | 無し | 形が戻るまで量は無し |
| `last_context` | 読む範囲に応答が無い | 圧縮の直後・末尾に収まらない大きな行・空の文字列 | 読み終えたとき | `none`（境に会ったなら `compacted`、それ以外は `empty`） | 無し | 次の応答の後に読める |
| `read_tail` | 読めない | ファイルが無い・権限・入出力の失敗（`OSError`） | `open` | 空を返す | 無し | 次の読み取りで読み直せる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `last_context` | 範囲に応答が無い・境に会った | `none`・`compacted` |
| `last_context` | 範囲に応答が無い・境に会わない | `none`・`empty` |
| `last_context` | 最後の応答より新しい行に読めない行 | `none`・`partial` |
| `last_context` | 照らしを通った最後の応答（`current` の候補）の採用行の形（`usage`・`message.id`・`timestamp`）が違う | `none`・`form` |
| `last_context` | 照らしに外れた最後の応答（`behind` の候補）の採用行の形が違う | `none`・`unrecorded`（または `after`） |
| `last_context` | `ToolUse`・その ID の `tool_use` を持つ応答が範囲にある | 最後の応答の量・`current` |
| `last_context` | `ToolUse`・無い | 最後の応答の量・`behind`・`unrecorded` |
| `last_context` | `Reply`・文が None | 最後の応答の量・`behind`・`unrecorded` |
| `last_context` | `Reply`・最後の応答の文と同じ・その後に利用者の行が無い | 最後の応答の量・`current` |
| `last_context` | `Reply`・ほか | 最後の応答の量・`behind`・`unrecorded` |
| `last_context` | `Tail`・最後の応答の後に利用者の行が無い | 最後の応答の量・`current` |
| `last_context` | `Tail`・ある | 最後の応答の量・`behind`・`after` |

形を確かめるのは、手がかりで `current` か `behind` を決めた後の、最後の応答の採用行（最も新しい物理の行）の 1 行だけである。確かめる項目は `message.usage`（設計文書の「文脈の量」の形）・`message.id`（空でない文字列）・`timestamp`（`timestamp_seconds` が None でない文字列）で、量・ID・時刻はこの行から読み、同じ応答の前の行へ替えない。この 3 つが `form` の条件のすべてで、要求の受け入れ条件 6・10 と設計文書の決定 6 も同じ 3 つを指す。採用行の object は `ContextRead.entry` に入れて返す。`message.id` が文字列でない採用行は、その行だけを 1 つの応答として照らす（照らしは形に依らずに決まる）。`form` は `current` の候補の形の違いだけに出し、`behind` の候補の形の違いは照らしの理由（`unrecorded`・`after`）のまま `none` にする。#4068 が今回でない応答の欠けで会話ごと止めないためである。

文はまとまりの各行の `message.content` が list のときの、`type` が `text` で `text` が文字列の要素をファイルの順につなぐ。`tool_use` の要素は `type` が `tool_use` で `id` が文字列の要素である。list でない `content` と、形の違う要素は飛ばす（照らしに外れる側へ倒れる）。

- 繰り返しの終わる条件: 末尾から 1 行ずつ新しい順に読む。次の 3 つの読みを順に行い、手がかりごとに必要な所で止める
  1. **読み 1・最も新しい応答の行まで**: 各行を `json.loads` し、書きかけの印・利用者の行の有無を数える。`compact_boundary` の行に会えば止める（`compacted`）。`tail` の先頭に達すれば止める（`empty`）。合成の応答・`isSidechain`・会話でない行を除く最初の `assistant` の行（採用行）で次へ進む。`Tail` はここで終わる（量・ID・時刻・利用者の行の有無がそろう）
  2. **読み 2・最後の応答のまとまりまで**（`Reply`・`ToolUse`）: さらに古い行を `json.loads` し、同じ `message.id` の行を集める。別の `message.id` の会話の `assistant` の行（前の応答。合成の応答は除く）・`compact_boundary` の行・`tail` の先頭で止める。1 つの応答の行が別の応答の行をまたいで再び現れることは無い（設計文書の「測った結果」で 130,047 応答のうち 0 件）。`Reply` はここで終わる。`ToolUse` は、まとまりにその ID の `tool_use` があればここで終わる（`current`）。ID が空の文字列なら探さずに `behind`（`unrecorded`）で終わる
  3. **読み 3・ID を持つ応答の探索**（`ToolUse` で、最後の応答にその ID が無いとき）: 残りの行は、その ID の文字列か `compact_boundary` の文字列を含む行だけを `json.loads` する。その ID の `tool_use` を持つ会話の `assistant` の行に会えば `current`、`compact_boundary` の行か `tail` の先頭で `behind`（`unrecorded`）で終わる。最後の応答より古い行の書きかけ・壊れた行は結果を変えないので、解釈しない
- 計算量の注意: `tail` は最大 256 KiB（`hook.transcript-tail`・`relay.transcript-tail`）。まとまりは最後の応答の行だけを持ち、応答ごとの辞書を作らない。代表の末尾（このマシンの大きい 200 本の末尾 256 KiB。行は中央 134.5・最大 341）での時間は、読み 1 で止める `Tail` が中央 0.26・最大 0.73 ms、読み 3 を最後まで走る `ToolUse` の最悪の経路が中央 0.43・p95 0.68・最大 1.23 ms（全行を解釈すると中央 1.59・最大 2.98 ms）。PreToolUse・PostToolUse の予算（`hook.budget.*` の p95 100 ms）の 1.3% 以下、ラッパーの見回りの間隔（`relay.tick` 0.5 秒）の 0.15% 以下で、今の読み手（最初の `usage` の行で止める）と同じ桁である

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
| `last_context` | 設計文書のテスト設計 1〜5・17。`entry` が採用行の object で、前の行の object でない | 境の行の直後に応答が 1 つだけある記録・応答の後の利用者の行が 0 行と 1 行 | 設計文書のテスト設計 6〜8・16。空の文字列で `empty` |
| `timestamp_seconds` | `"2026-11-22T12:00:00.123Z"`・`"2026-11-22T21:00:00+09:00"` が同じ秒 | 小数秒の無い時刻 | `"yesterday"`・`""`・`"2026-11-22T12:00:00"`（時差が無い）で None |
| `read_tail` | 今のテストを `tests/adapters/test_transcript.py` へ移す | 設計文書のテスト設計 15（読み始めの直前が改行・行の途中・ファイルより大きい `size`） | 権限の無いファイルで空。改行の無い 1 行が `size` を超えるファイルで空 |

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
| `check` | `src/ndf/domains/hooks/guards/context_limit.py` | 変更 | 変えない | #3995 の `check` の分岐を保ち、会話の記録から量を読む所だけを `last_context(ctx.transcript_tail, ToolUse(<入力の tool_use_id>))` にして、判定の記録に量と状態を残す |

この設計は #3995 の実装と同時かその後に入るので、下の表は #3995 の詳細設計の `check`（`_next_step` を置き換えた `_passes` を使う形）を土台にする。#3995 の表から変えるのは、量を読む所（`last_context`）・覚える値（`state` を足す）・`notes`（`context_state` を足す）の 3 つだけで、早い終わり・読み取りの有無・判定の条件は #3995 のままである。#3995 より前に入れる場合も、`_next_step` は使わない（#3995 と同じ変更の中で `_passes` へ置き換える）。

- `check`
  - 事前条件: 入力に `tool_use_id` が無い（空の）呼び出しは `ToolUse("")` を渡し、照らしは必ず外れる（`behind` か `none`）
  - 事後条件: イベントごとに下の 2 つの表のとおり。`state` が `none` の量は「量が読めない」（None）として扱う。`passing`・`deny`・`advise` の `notes` は `context_tokens` と `context_state` を持つ
  - 副作用: PreToolUse で覚える値を `{"tokens": …, "state": …}` にする（#3995 の詳細設計の `remember` の `value`）。PostToolUse は `take` が返した値の `state` を使い、無ければ `behind` とみなす。覚える条件・消す規則は #3995 のまま

PreToolUse（運用上限の止め）:

| 条件の組み合わせ（上から順に当てる） | 結果 |
| --- | --- |
| 入力に `agent_id` がある | None（#3995 のまま） |
| `_main(ctx.cwd)` が None | None（会話の記録を読まない。#3995 のまま） |
| `last_context(ctx.transcript_tail, ToolUse(id))` が `none` | None |
| 読めた・入力に `tool_use_id` がある・この入力の後にまだ知らせていない | `remember(MEMO_KEY, id, {"tokens", "state"}, MEMO_KEEP)` で覚えてから下の行を判定する。当たらなければ覚えずに下の行を判定する（#3995 のまま） |
| `tokens ≥ limit`・`_passes` が偽（状態は問わない） | `deny(_reason(...))`。notes は `context_tokens`・`context_state`・`under_relay` |
| `tokens ≥ limit`・`_passes` が真 | `passing`（notes は `context_tokens`・`context_state`） |
| `tokens < limit`（`current`・`behind` とも） | `passing`（同上） |

PostToolUse（判断開始の値の知らせ。拒否はしない）:

| 条件の組み合わせ（上から順に当てる） | 結果 |
| --- | --- |
| 入力に `agent_id` がある | None（#3995 のまま） |
| `_main(ctx.cwd)` が None | None（会話の記録を読まない。#3995 のまま） |
| 数え手が使えないか、この入力の後に知らせ済み | None（会話の記録を読まない。#3995 のまま） |
| `take(MEMO_KEY, 入力の tool_use_id)` が値を返す | 覚えた `tokens` と `state`（無ければ `behind`）で下の行を判定する（会話の記録を読まない） |
| 覚えた値が無い・ID が無い | `last_context(ctx.transcript_tail, ToolUse(id))` で読む。`none` なら None |
| `decide_from ≤ tokens < limit`・`bump(NOTICE_KEY) == 1` | `advise(_notice(...), notes={context_tokens, context_state})` |
| ほか（`tokens ≥ limit` を含む） | `passing`（notes は `context_tokens`・`context_state`）。上限以上でも PostToolUse は止めず知らせない（#3995 のまま） |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 量が無い | `last_context` の `none` | `state` | None を返す（判定しない） | 無し | 次の呼び出しで読み直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 設計文書のテスト設計 9・10・14 | 上限 − 1・上限、判断開始の値 − 1・判断開始の値、上限 − 1 の PostToolUse で知らせ、上限の PostToolUse（`current`・`behind` とも）は `deny` でなく `passing`。知らせ済みの PostToolUse と覚えた値の無い PostToolUse は #3995 のテストのまま（読み取り 0 回と 1 回） | `none`（`compacted`・`partial`・`form`）で None |

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
| `SectionState` の組み立て | 記録が読めない | ファイルが無い・権限 | `read_tail` が `OSError` を受けて空を返し、`last_context` が `none`（`empty`） | 量を None にして見回りを続ける | 無し | 次の見回りで読み直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `SectionState` の組み立て → `switch_decide.decide` | 設計文書のテスト設計 11 | 上限 − 1・上限（`behind` の量で） | 権限の無い記録のファイルで例外にならず量は None |

## #4068 との受け渡し

満たす決定: 決定 6、受け入れ条件 10

#4068 の `keep_cache.last_turn`（#4068 の詳細設計）は、返り値の型（`TurnRead`）を変えずに、中で `last_context(tail, Reply(last_message))` を 1 回だけ呼ぶ。`tail` を自分で走査し直さず、採用行を探し直さない。引数からは `known_ttl` を外し、`last_turn(tail: str, last_message: Optional[str], started: float) -> TurnRead` にする（排他の外で読んだ寿命で補わない。下の「寿命が決められないとき」）。#4068 の実装は、次の 3 点を #4068 の詳細設計から替える。

- **末尾の読み取り**: #4068 の `rewake.run`（手順 3）は、会話の記録の末尾を `agents.tail` でなく共有の `read_tail(transcript_path, <hook.transcript-tail の値>)` で読み、その文字列を `last_turn` の `tail` に渡す。`last_context` は `read_tail` が返す文字列だけを受け付ける（設計文書の「入出力のコントラクト」）ので、`agents.tail` の文字列（任意のバイトの位置から始まり、先頭の切れた行を捨てない）を渡さない
- **読めないとき**: 共有の `read_tail` は `OSError` を投げず空の文字列を返す。#4068 の手順 3 の「読めない（`OSError`）なら」の分岐は無くなり、空の文字列は `last_context` の `none`（`empty`）から下の表の `TurnRead(None, "unrecorded")` へ進む。呼び手の振る舞い（起こさず止めずに 0、`noted` に無ければ `stop_waking`（`scope` `once`）を書く）は、今の「読めない」と「`unrecorded`」の扱いと同じである
- **寿命が決められないとき**: #4068 の設計の決定 2 の表で、寿命別の書き込みが両方 0 なら前に分かった寿命（状態の `last.ttl`）を使い、それも None なら「決められないので起こさない」である。#4068 の `rewake.run` は手順 2〜3 で状態を排他の外で読むので、そこで読んだ `last.ttl` で補うと、先行する Stop がその後に排他の中で待ちを始めて `last.ttl` を `1h` にしても、新しい Stop は古い None を持ったまま (4)〜(6) へ進み、`last.ttl` を None に戻す。このため補うのは排他の中の `_settle` だけにし、#4068 の詳細設計の次を替える。#4068 の詳細設計の `LastTurn.ttl` は `1h`・`5m` だけなので、`ttl` に None を許す（会話を止める `form` にも、`unrecorded` にもしない）。倍率の宣言（`cache.write-ratio` の `ratios`）は `1h`・`5m` でしか引けないので、`ttl` が None の値を倍率へ渡さない。
  - `last_turn`: 採用行の寿命別の書き込みが両方 0 なら `LastTurn.ttl` を None にして返す（補わない）。`wake_reply` が返す起床の最初の呼び出しの `LastTurn` も同じ規則で、両方 0 なら `ttl` は None である
  - `_settle`: (1)・(2) の照らしを通った後、(3) の前に、`turn.ttl` が None なら排他の中で読んだ状態の `last.ttl` を入れた `turn`（`turn._replace(ttl=…)`）に替える（補完）。(3)〜(6) はすべて補完後の `turn` を使う。補完後も None なら、(1)〜(4) と (6) は今のまま行い（`last.ttl` は None のまま）、(5) は行わない。待ちを始めず、`wait_id`・`k`・`n_max` を書かず、`wait_start` の行も書かず、`q_for` を打たない。起こさない理由は「寿命が決められない」を返す。`Settled` に補完後の `turn` を持たせる（項目 `turn` を足す。`stale` なら None）
  - `rewake.run` の手順 4〜6: `_settle` が返した補完後の `turn` を使う。その `ttl` が None なら 0（起こさず止めずに。`stop_waking` も書かない）で、手順 5 の `wake_at` へ進まない。手順 5 の `wake_at` と手順 6 の `_finish` には補完後の `turn` を渡す。`wake_at` も `ttl` が None なら None を返す（守りの重ね）
  - 上限を決める時点: 寿命が判明した後続の Stop（採用行の寿命別の書き込みのどちらかが正）では、`_settle` の (5) が待ちの無い待つ子として、その Stop の `turn.ttl` と `S` で `n_max` を式から決め、`wait_start` を書く。寿命の分からない Stop の間の待ちは、待ちとして数えない
  - 寿命の分からない待ちは生じない: 待ちは (5) で `ttl` が分かったときだけ始まり、同じ排他の区間の (6) でその `ttl` が `last.ttl` に残る。以後の Stop は排他の中で最新の `last.ttl` から補うので、状態に待ちがある間は補完後の `turn.ttl` も `last.ttl` も常に `1h`・`5m` で、(6) が `last.ttl` を None に戻すことは無い。起こした印も待ちの間にだけあるので、(3)〜(5) の時点で `last.ttl` は既知である
  - `wake_result` の寿命と倍率: 待ちの寿命と起床の書き込みの寿命は別に持つ。起床の後の呼び出しは、待ちを始めた Stop と違う寿命のキャッシュへ書くことがある（例: `last.ttl` が `1h` の待ちで、起床後に 5 分のキャッシュへ 1,000 token 書くと、#4068 の宣言の倍率では 1,250 token 相当で、`1h` の倍率の 2,000 token 相当ではない）。`Reply.usage` は起床に属する全呼び出しの合計なので、最初の呼び出しの寿命（`Reply.turn.ttl`）でも待ちの寿命でも 1 つの倍率を当てられない。このため #4068 の詳細設計の次を替える
    - `Usage`: 項目 `cache_write_1h`・`cache_write_5m` を足す。起床に属する各呼び出しの採用行の `u` の `cache_creation` の `ephemeral_1h_input_tokens`・`ephemeral_5m_input_tokens` の合計である。各呼び出しの `cache_creation` の形の確かめは、下の表の `ttl` の「欠け・型違い」と同じで、1 つでも当たれば `wake_reply` は `Reply("unknown", None, None)` を返す（費用を寿命に分けられないので判じない。#4068 の `_settle` の (3) の `unknown` の扱いのまま）。`cache_write` は今のまま `cache_creation_input_tokens` の合計である
    - `wake_result` の項目: `ttl` は待ちの寿命で、補完の前に排他の中で読んだ状態の `last.ttl`（その待ちを始めた・続けた Stop で決まった寿命。上のとおり既知）を使う。`Reply.turn.ttl` は使わない。`w`・`r`・`out` はその `ttl` と `model`（`Reply.turn.model`）で引く `ratios(get, model, ttl)` の値で、`w` は避けた書き直しの `(w − r) × tokens` にだけ使う。項目 `cache_write_1h`・`cache_write_5m`（`Reply.usage` から）と、`w_1h`・`w_5m`（同じ `get` で読んだ `cache.write-ratio` の `ratios` の `1h`・`5m` の値。表の読み取りは `ratios` の 1 回と同じ辞書から取り、増やさない）を足す
    - `cli_cache.report` の `d` と実費: `d` は `cache_write_1h × w_1h + cache_write_5m × w_5m + output × out`、起床 1 回の実費は `cache_read × r + cache_write_1h × w_1h + cache_write_5m × w_5m + output × out` にする（#4068 の詳細設計の `report` の表の `cache_write × w` を置き換える。避けた書き直しの費用の差で引く実費も同じ式）。`cache_write_1h`・`cache_write_5m` が 0 以上の整数でない、または `w_1h`・`w_5m` が数でない `wake_result` は、下の寿命の不正な行と同じに扱い、`bad_ttl_rows` に数える
    - (4) の `wait_end`・(5) の `wait_start` の `ttl` は補完後の `turn.ttl` を使う。このため `ratios` に None が渡る経路は無く、`ratios` のシグネチャ（`ttl: str`）は替えない
  - `cli_cache.report` と `run`: `wait_start`・`wake_result`・`wait_end` の行の `ttl` は `1h`・`5m` だけを受ける。`run` は JSON として読める行を捨てずに `report` へ渡し、寿命の確かめは `report` が行う。`report` は `ttl` がそれ以外（None を含む）の行を見つけたら、その行の (`session_id`, `wait_id`) を不正な待ちとして覚え、その待ちに属する全ての行（`wait_start`・`wake`・`wake_result`・`wait_end`・`stop_waking`）を、正常な `wait_start` があっても、どの集計（待ちの数・起こした回数・当たりの率・`d`・実費・待ちの長さ・避けた書き直し）にも、`unmatched_wakes`・`orphan_results`・`unpriced_waits` にも入れず、`partial_waits` に 1 つと数える。寿命の不正な行の数は `report` の返り値の `metrics.bad_ttl_rows` に出し、`run` は JSON として読めない行の数に足して `metrics.skipped` に出す。`wait_id` の無い不正な行は待ちに結べないので、`bad_ttl_rows` にだけ数える。上のとおり書き手は None を書かないので、手で壊した記録でだけ起きる
  - テスト（#4068 の `_settle`・`wake_reply`・`report` の行に足す）:
    - 両方 0 で状態の `last.ttl` も None の Stop で、世代と `last`（`ttl` None）は書かれ、`wait_start`・`n_max` は書かれず、`q_for` が呼ばれず、終了コード 0。続く Stop で 1 時間の書き込みが正なら、その Stop の `S` で `n_max` を決めた `wait_start`（`ttl` `1h`）が 1 行
    - 競合: 状態の `last.ttl` が None の時に排他の外で状態を読んだ両方 0 の Stop の `_settle` を、先行の Stop が `ttl` `1h` の待ちを始めた後に打つと、補完後の `ttl` が `1h` で、`wait_end` を書く経路でも待ちを保つ経路でも `last.ttl` が `1h` のまま、手順 4 が `wake_at` へ進む
    - 起床の最初の呼び出しの書き込みが両方 0（`wake_reply` の `Reply.turn.ttl` が None）で、`wake_result` の `ttl` が状態の `last.ttl`（`1h`）で、`w` が `1h` の倍率
    - 状態の `last.ttl` が `1h` の待ちで、起床の最初の呼び出しの書き込みが 0、2 つ目の呼び出しが 5 分のキャッシュへ 1,000 token 書く見本で、`wake_result` の `ttl` が `1h`・`cache_write_1h` が 0・`cache_write_5m` が 1,000・`w_5m` が 1.25 で、`report` の `d` と実費の書き込みの分が 1,250（`1h` の倍率の 2,000 でない）。呼び出しの 1 つの `cache_creation` が object でない見本で `wake_reply` が `unknown`。`cache_write_5m` の欠けた `wake_result` の待ちを `partial_waits` に数え、`bad_ttl_rows` に 1
    - `report`: `ttl` が None の `wait_start` の待ちを `partial_waits` に数え、`skipped` に 1。正常な `wait_start` と `ttl` が None の `wake_result` を持つ待ち、正常な `wait_start` と `ttl` が None の `wait_end` を持つ待ちで、どちらも待ちの数・起こした回数・当たりの率・避けた書き直しに入らず、`unmatched_wakes`・`unpriced_waits` が増えず、`partial_waits` が 1

状態から `TurnRead` への対応は次のとおりで、ここが対応の唯一の保存先である。

| `last_context` の結果 | `last_turn` の結果 |
| --- | --- |
| `current` | 下の表の項目をすべて `ContextRead` とその `entry`（量と同じ採用行）から読む。項目の 1 つでも「欠け・型違い」に当たれば `TurnRead(None, "form")`。そろい、`at > started` なら `TurnRead(None, "newer")`、ほかは `TurnRead(LastTurn, None)`（`form` を `newer` より先に確かめる） |
| `none`（`form`） | `TurnRead(None, "form")` |
| `behind`・`none`（`unrecorded`・`compacted`・`partial`・`empty`） | `TurnRead(None, "unrecorded")`（一時的） |

`current` のときの `LastTurn` の項目の取り出し元（`u` は `entry["message"]["usage"]`）:

| 項目 | 取り出し元 | 欠け・型違い（`form`） |
| --- | --- | --- |
| `message_id` | `ContextRead.message_id` | 無い（`last_context` が確かめ済み） |
| `at` | `timestamp_seconds(ContextRead.timestamp)` | 無い（`last_context` が None でないことを確かめ済み。None が来たら `form`） |
| `tokens` | `ContextRead.tokens` | 無い（`last_context` が確かめ済み） |
| `cache_read` | `u` の `cache_read_input_tokens`。無ければ 0（`tokens` の和と同じ規則） | 無い（`last_context` が確かめ済み） |
| `cache_write` | `u` の `cache_creation_input_tokens`。無ければ 0（同上） | 無い（同上） |
| `output` | `u` の `output_tokens` | 無い・整数でない |
| `model` | `entry["message"]["model"]` | 無い・空・文字列でない |
| `ttl` | `u` の `cache_creation` の `ephemeral_1h_input_tokens`（1 時間の書き込み）と `ephemeral_5m_input_tokens`（5 分の書き込み）を #4068 の設計の決定 2 の表に当てる。1 時間が正なら `1h`、1 時間が 0 で 5 分が正なら `5m`、両方 0 なら None（`_settle` が排他の中で状態の `last.ttl` から補う。上の「寿命が決められないとき」） | `cache_creation` が object でない・2 つの項目のどちらかが無い・整数でない・負 |

同じ `message.id` の前の行は、どの項目でも読まない。前の行と採用行で `usage` が違う複数行の応答でも、文脈の量（`tokens`・`S`）と起床の費用（`cache_read`・`cache_write`・`output`・`ttl`）は同じ採用行の値になる。
