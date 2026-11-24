# #4089 の詳細設計: 文脈の量の読み手を 1 つにし、どの応答の量かと古さを返す

設計文書 [issue-4089-design.md](./issue-4089-design.md) の構成要素を、ファイルと関数の粒度へ下ろした文書である。パスは `plugins/ndf/` からの相対で書く。文中の #4068 は、キャッシュの寿命が切れる前に、裏の作業を待つ子を起こして保つ機能で、この部品を使う。

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

依存の向き: `domains/hooks` と `domains/relay` が `adapters/transcript.py` を読む。`adapters/transcript.py` は標準ライブラリ（`json`・`os`・`dataclasses`・`datetime`）だけを読む。下の層から上の層への向きは無く、循環は無い。

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
| `last_context` | `src/ndf/adapters/transcript.py` | 新設 | `last_context(tail: str, anchor: Anchor) -> ContextRead` | 圧縮の境より後の最後の応答の文脈の量と、手がかりと突き合わせた古さを返す |
| `timestamp_seconds` | 同上 | 新設 | `timestamp_seconds(text: str) -> Optional[float]` | 記録の `timestamp` を UNIX 秒に直す。`last_context` の形の確かめと #4068 の `last_turn` が同じ関数を使う |
| `read_tail` | 同上 | 移して先頭の行の扱いを直す | `read_tail(path: str, size: int) -> str` | 末尾 `size` バイトを読み、行の途中から始まるときだけ先頭の切れた行を捨てる。読めなければ空 |

- `last_context`
  - 事前条件: `tail` は `read_tail` の返した文字列。`anchor` は `ToolUse`・`Reply`・`Tail` のどれか（`Anchor = Union[ToolUse, Reply, Tail]`）
  - 事後条件: `state` が `current` か `behind` なら `tokens`・`message_id`・`timestamp`・`entry` がそろう。どれも最後の応答の採用行（最も新しい物理の行）から読んだ値で、`entry` はその行を `json.loads` した object そのものである。`timestamp_seconds(timestamp)` は None でない。`none` なら `reason` があり、`tokens` は None である。`none` の `form`（突き合わせを通った応答の採用行の形が違う）では、読める項目だけを持つ。採用行の `message.id` が空でない文字列なら、`message_id` に持つ。`timestamp` が `timestamp_seconds` で直せる文字列なら、`timestamp` に持つ。`entry` には採用行の object をそのまま入れる。`message_id` と `timestamp` は読めたほうだけを持ち、読めないほうは None にする。#4068 の `last_turn` が、この 2 つから Stop と応答を結ぶ `ref` を作る。`form` 以外の `none` では 3 つとも None。`current` のとき `reason` は None
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
| `last_context` | 採用行の `usage` が object でない・`input_tokens` が整数でない・キャッシュの項目が整数以外の値、`message.id` が無い・空・文字列でない、`timestamp` が無い・文字列でない・`timestamp_seconds` が None（解けない・時差が無い） | 記録の形の変化 | 採用行の 1 行だけを確かめる | `current` の候補なら `none`（`form`。読めた `message_id`・`timestamp` と `entry` は持つ）、`behind` の候補なら `none`（突き合わせの理由のまま。どれも None） | 無し | 形が戻るまで量は無し |
| `last_context` | 読む範囲に応答が無い | 圧縮の直後・末尾に収まらない大きな行・空の文字列 | 読み終えたとき | `none`（境に会ったなら `compacted`、それ以外は `empty`） | 無し | 次の応答の後に読める |
| `read_tail` | 読めない | ファイルが無い・権限・入出力の失敗（`OSError`） | `open` | 空を返す | 無し | 次の読み取りで読み直せる |

### 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `last_context` | 範囲に応答が無い・境に会った | `none`・`compacted` |
| `last_context` | 範囲に応答が無い・境に会わない | `none`・`empty` |
| `last_context` | 最後の応答より新しい行に読めない行 | `none`・`partial` |
| `last_context` | 突き合わせを通った最後の応答（`current` の候補）の採用行の形（`usage`・`message.id`・`timestamp`）が違う | `none`・`form` |
| `last_context` | 突き合わせに外れた最後の応答（`behind` の候補）の採用行の形が違う | `none`・`unrecorded`（または `after`） |
| `last_context` | `ToolUse`・その ID の `tool_use` を持つ応答が範囲にある | 最後の応答の量・`current` |
| `last_context` | `ToolUse`・無い | 最後の応答の量・`behind`・`unrecorded` |
| `last_context` | `Reply`・文が None | 最後の応答の量・`behind`・`unrecorded` |
| `last_context` | `Reply`・最後の応答の文と同じ・その後に利用者の行が無い | 最後の応答の量・`current` |
| `last_context` | `Reply`・ほか | 最後の応答の量・`behind`・`unrecorded` |
| `last_context` | `Tail`・最後の応答の後に利用者の行が無い | 最後の応答の量・`current` |
| `last_context` | `Tail`・ある | 最後の応答の量・`behind`・`after` |

上の表と下の走査で使う行の語（正本は設計文書の「入出力のコントラクト」）:

- **会話の行**: `type` が `user` か `assistant` で、`isSidechain` が真でない行
- **会話でない行**: 会話の行でない行のすべて。`type` が `user`・`assistant` でない行（`last-prompt`・`api-request`・`attachment`・`system` など。ただし `system` の `compact_boundary` の行は境として別に扱う）と、`isSidechain` が真の行（サブエージェントの会話）である。走査では飛ばし、利用者の行にも応答にも数えない
- **利用者の行**: `type` が `user` の会話の行のすべてである。入力の行だけでなく、Tool の結果の行（`content` が `tool_result` の要素の行）と `isMeta` の行も含む。どれも応答の後に足された文脈なので、その後に応答が無ければ最後の応答の量は古い。`Reply` と `Tail` の「その後に利用者の行が無い」は、Tool の結果の行が 1 行でもあれば満たさない（`Reply` では `behind`、`Tail` では `behind`（`after`））
- #4068 の `wake_reply`・`opening` の「入力の行」は、利用者の行から Tool の結果の行・圧縮の要約の行と、付随する `isMeta` の行を除いたものである。`isMeta` の行は一律には除かない。Tool の結果でない `user` の行には `isMeta` を見る前に #4068 の `is_wake_input` を当て、真（起床の文）か偽（利用者の入力・作業の知らせ）を返した行は、`isMeta` が真でも入力の行にする。None を返した `isMeta` の行だけを付随する行として除く（判定の順の正本は #4068 の詳細設計の「`wake_reply` と `opening` が数える行」）。#4068 は起床と待ちを終えた独立した入力を探すので付随する行を入力に数えないが、この部品の「利用者の行」は量の古さを決めるので Tool の結果を含める。2 つは別の語で、食い違いではない（#4068 の詳細設計の「`wake_reply` と `opening` が数える行」）

形を確かめるのは、手がかりで `current` か `behind` を決めた後の、最後の応答の採用行（最も新しい物理の行）の 1 行だけである。確かめる項目は次の 3 つである。`message.usage` は設計文書の「文脈の量」の形か。`message.id` は空でない文字列か。`timestamp` は `timestamp_seconds` が None でない文字列か。量・ID・時刻はこの行から読み、同じ応答の前の行へ替えない。この 3 つが `form` の条件のすべてで、要求の受け入れ条件 6・10 と設計文書の決定 6 も同じ 3 つを指す。採用行の object は `ContextRead.entry` に入れて返す。`message.id` が文字列でない採用行は、その行だけを 1 つの応答として突き合わせる（突き合わせは形に依らずに決まる）。`form` は `current` の候補の形の違いだけに出す。`behind` の候補の形の違いは、突き合わせの理由（`unrecorded`・`after`）のまま `none` にする。#4068 が今回でない応答の欠けで会話ごと止めないためである。

文はまとまりの各行の `message.content` が list のときの、`type` が `text` で `text` が文字列の要素を、ファイルの順に区切りの文字なしでつなぐ。`Reply.text` との比べは、両方から空白文字を除いた（`"".join(s.split())`）文字列の完全一致で行う。比べ方の正本は設計文書の「入出力のコントラクト」の「`Reply` の文の比べ方」である。`tool_use` の要素は `type` が `tool_use` で `id` が文字列の要素である。list でない `content` と、形の違う要素は飛ばす（突き合わせに外れる側へ倒れる）。

- 繰り返しの終わる条件: 末尾から 1 行ずつ新しい順に読む。次の 3 つの読みを順に行い、手がかりごとに必要な所で止める
  1. **読み 1・最も新しい応答の行まで**: 各行を `json.loads` し、書きかけのフラグ・利用者の行の有無を数える。`compact_boundary` の行に会えば止める（`compacted`）。`tail` の先頭に達すれば止める（`empty`）。合成の応答・`isSidechain`・会話でない行を除く最初の `assistant` の行（採用行）で次へ進む。`Tail` はここで終わる（量・ID・時刻・利用者の行の有無がそろう）
  2. **読み 2・最後の応答のまとまりまで**（`Reply`・`ToolUse`）: さらに古い行を `json.loads` し、同じ `message.id` の行を集める。別の `message.id` の会話の `assistant` の行（前の応答。合成の応答は除く）・`compact_boundary` の行・`tail` の先頭で止める。1 つの応答の行が別の応答の行をまたいで再び現れることは無い（設計文書の「測った結果」で 130,047 応答のうち 0 件。これは #4089 の測定（2026-11-23）で、このマシンの会話の記録 6,045 本を期間とモデルで分けずに数えた応答の数である。#4068 の測った結果の 127,870 応答は、2026-10-24〜11-22 のメインの会話のうち Opus 5.5 の応答だけを数えた別の集計で、期間とモデルの範囲が違う。130,047 はこの性質の標本の大きさとしてだけ使い、2 つの値を突き合わせない）。`Reply` はここで終わる。`ToolUse` は、まとまりにその ID の `tool_use` があればここで終わる（`current`）。ID が空の文字列なら探さずに `behind`（`unrecorded`）で終わる
  3. **読み 3・ID を持つ応答の探索**（`ToolUse` で、最後の応答にその ID が無いとき）: 読み 2 が止まるのに使った行（前の応答の `assistant` の行）から始める。この行は読み 2 で解釈済みなので、その `tool_use` の要素を先に突き合わせる。逆順の読み取りで読み 2 がこの行を消費し、読み 3 を次の行から始めると、直前の応答の最後の物理の行にだけある ID を見落とす。その後の行は、その ID の文字列か `compact_boundary` の文字列を含む行だけを `json.loads` する。その ID の `tool_use` を持つ会話の `assistant` の行に会えば `current` で終わる。`compact_boundary` の行か `tail` の先頭に会えば `behind`（`unrecorded`）で終わる。最後の応答より古い行の書きかけ・壊れた行は結果を変えないので、解釈しない
- 計算量の注意: `tail` の大きさは呼ぶ側が決める。hook は `hook.transcript-tail`、ラッパーは `relay.transcript-tail` の設定の値を `read_tail` の `size` に渡す。`read_tail` と `last_context` は渡された大きさをそのまま使い、読み手の中に上限を持たない。256 KiB は 2 つの宣言の今の既定値で、下の時間を測った条件である。まとまりは最後の応答の行だけを持ち、応答ごとの辞書を作らない。代表の末尾（このマシンの大きい 200 本の末尾 256 KiB。行は中央 134.5・最大 341）での時間は次のとおりである。読み 1 で止める `Tail` が中央 0.26・最大 0.73 ms である。読み 3 を最後まで走る `ToolUse` の最悪の経路が中央 0.43・p95 0.68・最大 1.23 ms である（全行を解釈すると中央 1.59・最大 2.98 ms）。これは PreToolUse・PostToolUse の予算（`hook.budget.*` の p95 100 ms）の 1.3% 以下である。ラッパーの見回りの間隔（`relay.tick` 0.5 秒）に対しては 0.15% 以下である。今の読み手（最初の `usage` の行で止める）と同じ桁である

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
| `read_tail` | 今のテストを `tests/adapters/test_transcript.py` へ移す。512 KiB を越える記録で `size` に 512 KiB を渡すと 256 KiB より前の行も返る（読み手に固定の上限が無い）。guard とラッパーのテストで、`hook.transcript-tail`・`relay.transcript-tail` の宣言の値を差し替えると、`read_tail` に渡る `size` がそれぞれの差し替えた値になる | 設計文書のテスト設計 15（読み始めの直前が改行・行の途中・ファイルより大きい `size`） | 権限の無いファイルで空。改行の無い 1 行が `size` を超えるファイルで空 |

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

この設計は #3995 の実装と同時かその後に入るので、下の表は #3995 の詳細設計の `check`（`_next_step` を置き換えた `_passes` を使う形）を基盤にする。#3995 の表から変えるのは、量を読む所（`last_context`）・覚える値（`state` を足す）・`notes`（`context_state` を足す）の 3 つだけである。早い終わり・読み取りの有無・判定の条件は #3995 のままである。#3995 より前に入れる場合も、`_next_step` は使わない（#3995 と同じ変更の中で `_passes` へ置き換える）。

- `check`
  - 事前条件: 入力に `tool_use_id` が無い（空の）呼び出しは `ToolUse("")` を渡し、突き合わせは必ず外れる（`behind` か `none`）
  - 事後条件: イベントごとに下の 2 つの表のとおり。`state` が `none` の量は「量が読めない」（None）として扱う。`passing`・`deny`・`advise` の `notes` は `context_tokens` と `context_state` を持つ
  - 副作用: PreToolUse で覚える値を、今の `_remember` の `{"tokens": …, "cwd": <_cwd_digest(ctx.cwd)>}` に `state` を足した `{"tokens": …, "cwd": …, "state": …}` にする（#3995 の詳細設計の `remember` の `value`。`cwd` のダイジェストは落とさない）。PostToolUse は今の `_remembered` と同じく、`take` が返した値の `cwd` が `_cwd_digest(ctx.cwd)` と同じで `tokens` が整数のときだけ有効な保存値として再利用する。有効な保存値の `state` が `current`・`behind` のどちらでもなければ（無い・別の値）`behind` とみなす。`cwd` が違う・`tokens` が整数でない保存値は今のとおり無効で、覚えた値が無いときと同じく会話の記録を読む。覚える条件・消す規則は #3995 のまま
  - `state` を覚える目的: 判定（止める・知らせる）は状態を問わず量だけで決まる（設計文書の決定 5）。`state` は判定に使わず、PostToolUse の判定の記録（`notes` の `context_state`）に、読み直さずに PreToolUse の時点の古さを残すためだけに覚える。古い量で止めた・知らせた回数を後から記録で数え、決定 5 の代償（古い量は厳密な下限でない）を見直す材料にする

PreToolUse（運用上限の止め）:

| 条件の組み合わせ（上から順に当てる） | 結果 |
| --- | --- |
| 入力に `agent_id` がある | None（#3995 のまま） |
| `_main(ctx.cwd)` が None | None（会話の記録を読まない。#3995 のまま） |
| `last_context(ctx.transcript_tail, ToolUse(id))` が `none` | None |
| 読めた・入力に `tool_use_id` がある・最後の利用者の入力の後にまだ知らせていない | `remember(MEMO_KEY, id, {"tokens", "cwd", "state"}, MEMO_KEEP)`（`cwd` は今の `_cwd_digest(ctx.cwd)`）で覚えてから下の行を判定する。当たらなければ覚えずに下の行を判定する（#3995 のまま） |
| `tokens ≥ limit`・`_passes` が偽（状態は問わない） | `deny(_reason(...))`。notes は `context_tokens`・`context_state`・`under_relay` |
| `tokens ≥ limit`・`_passes` が真 | `passing`（notes は `context_tokens`・`context_state`） |
| `tokens < limit`（`current`・`behind` とも） | `passing`（同上） |

PostToolUse（判断開始の値の知らせ。拒否はしない）:

| 条件の組み合わせ（上から順に当てる） | 結果 |
| --- | --- |
| 入力に `agent_id` がある | None（#3995 のまま） |
| `_main(ctx.cwd)` が None | None（会話の記録を読まない。#3995 のまま） |
| 数え手が使えないか、最後の利用者の入力の後に知らせ済み | None（会話の記録を読まない。#3995 のまま） |
| `take(MEMO_KEY, 入力の tool_use_id)` が値を返し、その `cwd` が `_cwd_digest(ctx.cwd)` と同じで `tokens` が整数（今の `_remembered` の照合） | 覚えた `tokens` と `state`（無いか別の値なら `behind`）で下の行を判定する（会話の記録を読まない） |
| 覚えた値が無い・ID が無い・照合に外れた（`cwd` が違う・`tokens` が整数でない） | `last_context(ctx.transcript_tail, ToolUse(id))` で読む。`none` なら None |
| `decide_from ≤ tokens < limit`・`bump(NOTICE_KEY) == 1` | `advise(_notice(...), notes={context_tokens, context_state})` |
| ほか（`tokens ≥ limit` を含む） | `passing`（notes は `context_tokens`・`context_state`）。上限以上でも PostToolUse は止めず知らせない（#3995 のまま） |

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `check` | 量が無い | `last_context` の `none` | `state` | None を返す（判定しない） | 無し | 次の呼び出しで読み直す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `check` | 設計文書のテスト設計 9・10・14 | 上限 − 1・上限、判断開始の値 − 1・判断開始の値、上限 − 1 の PostToolUse で知らせ、上限の PostToolUse（`current`・`behind` とも）は `deny` でなく `passing`。知らせ済みの PostToolUse と覚えた値の無い PostToolUse は #3995 のテストのまま（読み取り 0 回と 1 回）。同じ `cwd` の PreToolUse が覚えた値（`tokens`・`cwd`・`state`）で、PostToolUse の会話の記録の読み取りが 0 回で、`notes` の `context_state` が覚えた `state`。PreToolUse と `cwd` が違う PostToolUse では覚えた値を使わず読み取り 1 回。`state` の無い保存値で `behind` | `none`（`compacted`・`partial`・`form`）で None |

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

#4068 の `keep_cache.last_turn` は、シグネチャと返り値の型を #4068 の詳細設計のまま使う。シグネチャは `last_turn(tail, last_message, started) -> TurnRead` である。中で `last_context(tail, Reply(last_message))` を 1 回だけ呼ぶ。状態の寿命（以前の `known_ttl`）は引数に取らない。ロックの外で読んだ状態で寿命を補うと、並んで動く hook の順で古い値を使うためである（#4068・#4199 の設計の決定 13）。`tail` を自分で走査し直さず、採用行を探し直さない。#4068 の詳細設計は、この部品を使うために次の 2 点を反映済みである（#4068 の詳細設計の「処理の手順」の 3）。寿命の補い・起こす状態・起こす記録と集計の規則は #4068 の設計のまま変えない（範囲の外）。

- **末尾の読み取り**: #4068 の `rewake.run`（手順 3）は、会話の記録の末尾を `agents.tail` で読まない。共有の `read_tail(transcript_path, <hook.transcript-tail の値>)` で読む。その文字列を `last_turn` の `tail` に渡す。`last_context` は `read_tail` が返す文字列だけを受け付ける（設計文書の「入出力のコントラクト」）
- **読めないとき**: 共有の `read_tail` は `OSError` を投げず空の文字列を返す。#4068 の手順 3 の「読めない（`OSError`）なら」の分岐は無くなる。空の文字列は `last_context` の `none`（`empty`）から下の表の `TurnRead(None, "unrecorded")` へ進む。呼び手の振る舞い（起こさず止めずに 0、理由の行を会話で 1 度だけ書く）は、今の「読めない」と「`unrecorded`」の扱いと同じである

今回の応答を決める手がかりは `Reply`（Stop の入力の `last_assistant_message` の文）だけで、応答の ID の手がかりは持たない。#4068・#4199 の設計の決定 4 の「応答の ID が取れたとき」に当たり、ID の手がかりは実測の後に別の設計で足す。

状態から `TurnRead` への対応は次のとおりで、ここが対応の唯一の保存先である。

`ref` は、突き合わせを通った応答（`current` か `none` の `form`）の `(message_id, at)` の組である。`at` は `timestamp_seconds(ContextRead.timestamp)` である。`ContextRead.message_id` か `at` のどちらかが None なら `ref` は None。`current` では 2 つとも必ずそろう。照合の結果は `ContextRead.state`・`reason` が渡し、ID と時刻は `ContextRead.message_id`・`timestamp` が渡す。`form` でも読めた値を持つ（上の「記録の読み手」の事後条件）。

| `last_context` の結果 | `last_turn` の結果（上から順に当てる） |
| --- | --- |
| `current` か `none`（`form`）で、`ref` が在り `ref.at > started` | `TurnRead(None, "newer")`（遅れて動いた古い hook が次の応答を読んだ。形の違いより先に確かめる。#4068 の詳細設計の `last_turn`） |
| `none`（`form`） | `TurnRead(None, "form", ref)` |
| `current` | 下の表の項目をすべて `ContextRead` とその `entry`（量と同じ採用行）から読む。項目の 1 つでも「欠け・型違い」に当たれば `TurnRead(None, "form", ref)`、ほかは `TurnRead(LastTurn, None)` |
| `behind`（理由を問わない）か、`none`（`unrecorded`・`compacted`・`partial`・`empty`） | `TurnRead(None, "unrecorded")`（一時的）。`last_turn` は `Reply` を渡すので、理由 `after`（`Tail` のときだけ）は返らない |

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
| `ttl` | `u` の `cache_creation` の `ephemeral_1h_input_tokens`（1 時間の書き込み）と `ephemeral_5m_input_tokens`（5 分の書き込み）を #4068 の設計の決定 2 の表に当てる。1 時間が正なら `1h`、1 時間が 0 で 5 分が正なら `5m`、両方 0 なら None（未定）。補うのは呼び手の `rewake._settle` だけで、子を止めるロックの中で読み直した状態の `ttl` を使う。それでも未定なら待ちを始めない（#4068・#4199 の設計の決定 13） | `cache_creation` が object でない・2 つの項目のどちらかが無い・整数でない・負 |

同じ `message.id` の前の行は、どの項目でも読まない。前の行と採用行で `usage` が違う複数行の応答でも、使う値は同じ採用行の値になる。文脈の量（`tokens`・`S`）も、起床の費用（`cache_read`・`cache_write`・`output`・`ttl`）も同じである。
