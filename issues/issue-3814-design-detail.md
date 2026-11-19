# #3814 の詳細設計: 回答待ちの質問のあいだ子を止めず、子の端末へ書かない

設計書（[issue-3814-design.md](./issue-3814-design.md)）の「構成要素」を、ファイルと関数の粒度へ下ろした文書である。決定・不変条件（I1〜I7）・受け入れ条件の番号は、設計書と要求仕様（[issue-3814-requirements.md](./issue-3814-requirements.md)）の番号を指す。パスは `plugins/ndf/` からの相対で書く。新設する関数が 2 つ以上あるので、設計書に畳まずにこのファイルに置く。

## 地図

```text
plugins/ndf/
├── src/ndf/domains/relay/
│   ├── wrapper.py         見回りで質問を読み・消し、手を錠の中で打つ
│   ├── switch_decide.py   回した回答待ちの質問で wait を返す
│   ├── signals.py         質問のファイルを読む・錠の中で確かめる・錠の中で消す
│   ├── observe.py         会話の記録から回答と回答の無い質問を探す
│   └── cli.py             手での解除のサブコマンド question-done
├── skills/development-workflow/references/
│   └── relay.md           承認ゲートを越えない守りを 11 系へ書き直す
└── tests/domains/relay/
    ├── test_relay_wrapper.py
    ├── test_relay_decide.py
    ├── test_relay_parts.py
    └── test_relay_cli.py
```

新設のファイルは無い。依存の向きは今のまま一方向である。`wrapper` と `cli` が `switch_decide`・`signals`・`observe` を呼び、`switch_decide` は何も読まない（純粋な関数）。`observe` は `signals` と `ndf.adapters.agents` を呼ぶ。

| 構成要素 | 保存先 | 満たす決定 |
| --- | --- | --- |
| [判定](#判定switch_decide) | `switch_decide.py` | 決定 1 |
| [質問のファイルの読みと印](#質問のファイルの読みと印signalsread_questionquestion_mark) | `signals.py` | 決定 1 |
| [錠の中で消す](#錠の中で消すsignalsdrop_question) | `signals.py` | 決定 1・3 |
| [手の前の確かめ](#手の前の確かめsignalsquestion_clear) | `signals.py` | 決定 2 |
| [会話の記録の読み](#会話の記録の読みobserveansweredopen_question) | `observe.py` | 決定 2・3 |
| [見回りの質問の扱い](#見回りの質問の扱いwrapper_question_state_aware_time) | `wrapper.py` | 決定 1・2・3 |
| [質問を回す手](#質問を回す手wrapper_on_wait_like) | `wrapper.py` | 決定 1 |
| [子を止める手と子の端末へ書く手](#子を止める手と子の端末へ書く手wrapper_on_switch_on_resume_on_handover) | `wrapper.py` | 決定 2 |
| [手での解除](#手での解除ndf-relay-question-done) | `cli.py` | 決定 3 |
| [手順書](#手順書relaymd) | `relay.md` | 決定 4 |

## 判定（`switch_decide`）

満たす決定: 決定 1、受け入れ条件 1・2・3・5・9、I1・I2

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `decide` | `switch_decide.py` | 変更 | 変わらない | `question` が真のとき、`question_forwarded` が偽なら `forward`、真なら `wait` を返す。同じ状態に同じ手を返す |

処理の手順:

| 条件 | 結果 |
| --- | --- |
| `question` 真・`question_forwarded` 偽 | `Step("forward", FORWARD.format(id=state.question_id))`。`question_id` が None なら今と同じ文 |
| `question` 真・`question_forwarded` 真 | `Step("wait", QUESTION_PENDING)` |
| `question` 偽 | 今と同じ（上限と認証の失敗 → マーカー → 子の終わり → 文脈の上限 → 止まり） |

内部のデータと定数:

| 名前 | 種類 | 既定値 |
| --- | --- | --- |
| `SectionState.question_forwarded` | フィールド `bool`（今の質問を回したか） | False |
| `SectionState.question_id` | フィールド `Optional[str]`（今の質問の印） | None |
| `FORWARD` | 定数 `AskUserQuestion の質問 {id} を人かオーガナイザーへ回す（hook は待たない）` | — |
| `QUESTION_PENDING` | 定数 `回答待ちの質問がある。子を止めず、子の端末へ書かない` | — |

2 つのフィールドに既定値を置くので、`ndf relay decide` は今の入力の JSON も読める。`question_id` が `forward` の理由の文に入り、質問ごとに理由が変わるため、直前と同じ理由を省く `Wrapper._note` でも別の質問の `forward` の行が残る（I2）。

単体テストの観点:

| 正常 | 境界値 |
| --- | --- |
| 回した質問で `wait`。回していない質問の理由に印が入る | `question_forwarded`・`question_id` の無い JSON（`ndf relay decide`）で今と同じ文の `forward` |

## 質問のファイルの読みと印（`signals.read_question`・`question_mark`）

満たす決定: 決定 1、受け入れ条件 4・5・6

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `read_question` | `signals.py` | 新設 | `(directory: Path) -> Optional[dict[str, Any]]` | 質問のファイルの中身を返す。無ければ None。読むだけ |
| `question_mark` | `signals.py` | 新設 | `(question: Mapping[str, Any]) -> Optional[str]` | 質問の印。`tool_use_id` が文字列ならそれ、無ければ `at` が文字列ならそれ、どちらも無ければ None |

使う既存の部品: `signals._read`（壊れたら `SignalError`、無ければ None）。`read_next` と同じ形で読む。

異常系:

| 失敗 | 原因 | 扱い | 利用者に見える出力と終了コード |
| --- | --- | --- | --- |
| 質問のファイルが壊れている | 書きかけ・手での編集 | `SignalError` を呼び出し元へ返す（`_loop` が今と同じく `config_broken` で止める） | `ndf-relay: シグナルファイルが壊れている（…）`、終了コード 2 |

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 中身を返す | 無ければ None | 壊れていれば `SignalError` |

## 錠の中で消す（`signals.drop_question`）

満たす決定: 決定 1・3、受け入れ条件 4・6・10、I5

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `drop_question` | `signals.py` | 新設 | `(directory: Path, session_id: Optional[str], mark: Optional[str]) -> bool` | 錠 `question.json.lock` を待たずに取り、読み直した質問の `session_id` と `question_mark` が渡した値と同じときだけ消す |

- 事後条件: 消したか、既に無ければ真。錠を取れない・置き換わっていた・壊れていたら偽で、ファイルは残る
- 副作用: `question.json` の削除

使う既存の部品: `signals.held`（錠のファイルの排他。`wait` 0 で待たない）と、hook の `write_json` が使う錠の名前 `<path>.lock`。hook と同じ錠の名前を同じモジュールで作る。

並行と排他: hook（`ask-forward`）は `signals.write_json` の錠の中で `replace_json` で置き換える。置き換えは原子的なので、ラッパーは錠を取らずに読んでも書きかけを読まない。消す手だけは錠の中で読み直す。順序の例: ラッパーが回答済みの A を読む → hook が B へ置き換える → ラッパーが錠を取って読み直すと B → 消さない。消した後に hook が書いた質問は、次の見回りで新しい質問として回す。

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 同じ質問なら消して真 | 既に無ければ真 | 読み直すと別の質問（A を渡し B が置いてある）なら消さず偽。錠を別のスレッドが持てば消さず偽 |

## 手の前の確かめ（`signals.question_clear`）

満たす決定: 決定 2、受け入れ条件 1・2・3、I4・I6

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `question_clear` | `signals.py` | 新設 | `(directory: Path, wait: float, released_at: Optional[float]) -> ContextManager[Optional[Callable[[], bool]]]` | 錠を取る前に、`released_at`（ラッパーが前に錠を解いた `time.monotonic()`）から `wait` 以上たっているかを確かめ、たっていなければ錠を取りにいかずに None を渡す。たっていれば錠 `question.json.lock` を待たずに取り（`held(…, 0)`）、取れて質問のファイルが無ければ `in_time()`（取ってからの経過が `wait / 2` 未満なら真）を渡す。取れない・質問のファイルがあれば None を渡す。取れたときは抜けるまで錠を持つ |

- 事前条件: `wait` は `hook.ask-forward` の秒（hook と同じ値を読む）
- 事後条件: 抜けたら錠は解けている。錠を取れたときだけ、呼び出し元は抜けた時刻を `Wrapper._released_at` へ残す。待ちの前で戻ったときと錠を取れなかったときは `_released_at` を変えない
- 副作用: 錠のファイル

内部のデータ:

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `Wrapper._released_at` | フィールド `Optional[float]`（ラッパーが前に `question.json.lock` を解いた `time.monotonic()`。`drop_question` と `question_clear` が錠を取れたときだけ、解いた後に残す） | `wrapper.py` | None |

並行と排他（hook が待ちを越える前にラッパーの手が終わることの議論）:

`AskUserQuestion` の選択肢が画面に出るのは PreToolUse の hook が返った後である。hook が錠を取れずに返る（質問を記録せずに選択肢が出る）のは、錠を取りにいった最初の時刻から 0.3 秒のあいだ、試すたびに錠がふさがっていたときだけである。錠を持つのは hook とラッパーだけで、ラッパーの錠の持ち方は I6 で縛る。

- ラッパーが錠を取る前の 0.3 秒、ラッパーは錠を持っていない。その前から待っていた hook は、その 0.3 秒のうちに錠を取るか待ちを終える（hook は待ちを終える前に必ずもう一度試す）
- ラッパーが錠を取った後に待ち始めた hook は、早くても取った時刻から 0.3 秒後まで返らない。ラッパーは取ってから 0.15 秒（hook の待ち 0.3 秒の半分。`in_time()` の `wait / 2`）未満で `SIGKILL` を送り終えるか書き終えるので、hook が返るより先に子は止まる合図を受けているか、引継ぎの文が端末へ入っている
- `SIGKILL` は送った時点で子が利用者のコードへ戻らないので、止める手のあとに選択肢は描かれない。強制終了の続き（`terminate(force=True)` の回収）は錠の外で行い、錠の時間に数えない
- 前に解いた時刻の確かめは錠を取る前に行い、錠を取りにいかなかったときと取れなかったときは解いた時刻を変えない。錠を取ってから確かめると、見回りの間隔（`relay.tick`）が hook の待ちより短い設定では、見回りのたびに錠を取り直して待ちを延ばし、手がいつまでも打たれない

この議論の前提は、hook のプロセスが 0.3 秒のあいだ一度も動けないことは無い、である（設計書の「未確認のまま残ること」）。前提が崩れて hook が記録せずに返っても、選択肢が出たままなら会話の記録の確かめ（I7）が次の手を止める。

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 質問が無く錠を取れたら `in_time` を渡す | 前に解いてから `wait` 未満なら None。偽の時計で `wait / 2` を越えると `in_time()` が偽 | 錠を別のスレッドが持つ・質問のファイルがあれば None |

## 会話の記録の読み（`observe.answered`・`open_question`）

満たす決定: 決定 2・3、受け入れ条件 4、I3・I7、非機能の条件「性能・拡張性」「可用性」

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `answered` | `observe.py` | 新設 | `(path: Path, tool_use_id: str, size: int) -> Optional[bool]` | 会話の記録の末尾 `size` バイトの行のうち、`type` が `user` で `message.content` が配列の行に、`type` が `tool_result` で `tool_use_id` が等しい要素があれば真。読めた行に無ければ偽。記録のファイルが無いか読めなければ None |
| `open_question` | `observe.py` | 新設 | `(path: Path, size: int) -> Optional[str]` | 会話の記録の末尾 `size` バイトの行で、`assistant` の行の `message.content[]` の `type: tool_use`・`name: AskUserQuestion` の要素のうち、後の `user` の行に同じ `tool_use_id` の `tool_result` が無いものの id（最後のもの）。無いか、記録が無いか読めなければ None |

どちらも読むだけである。`size` は `relay.transcript-tail` で、文脈の量の読み（`observe.context_tokens`）と同じ量を読む。`answered` を呼ぶのは、回答待ちの質問が `tool_use_id` を持つ見回りだけである。

使う既存の部品: `ndf.adapters.agents.tail`。`context_tokens` は量だけを返すので別の関数にする。

異常系:

| 関数 | 失敗 | 原因 | 扱い |
| --- | --- | --- | --- |
| `answered` | 会話の記録が無い・読めない | 保存先の違い・権限（`path.is_file()` が偽・`agents.tail` の `OSError`） | None を返す。見回りは質問を残し、解除の手順を出す（[見回りの質問の扱い](#見回りの質問の扱いwrapper_question_state_aware_time)） |
| `answered`・`open_question` | 行が読めない・形が違う | 書き途中で末尾が切れた・別の種類の行（`json.loads` の `ValueError`・`dict` でない・`message.content` が配列でない） | その行を飛ばす |
| `open_question` | 会話の記録が無い・読めない | 保存先の違い・権限（`OSError`） | None を返す（I6 の時間の縛りだけが守る） |

単体テストの観点:

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `answered` | 実際の形の行（`{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_A", "content": "Your questions have been answered: …"}]}, "toolUseResult": {…}, "sessionId": …}`）で真。フィクスチャは手元の記録から写した行の形を使う | 記録のファイルが無いと None。読めた行に無ければ偽 | 別の質問（`toolu_X`）の `tool_result`・`assistant` の行の `tool_use`（`id` が `toolu_A`）・最上位にだけ `tool_use_id` がある行では偽。末尾で切れた行は読み飛ばす |
| `open_question` | `tool_result` の無い `AskUserQuestion` の `tool_use` の id | 記録が無いと None | `tool_result` のある `tool_use`・別の Tool の `tool_use`・`user` の行の要素では None |

## 見回りの質問の扱い（`Wrapper._question`・`_state`・`_aware_time`）

満たす決定: 決定 1・2・3、受け入れ条件 4・5・6、I3・I7、非機能の条件「可用性」

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._question` | `wrapper.py` | 新設 | `(self, sec: Section, nxt: Optional[dict]) -> tuple[bool, bool, Optional[str]]` | 質問のファイルを読み、消してよければ消し、（回答待ちか, 回したか, 印）を返す |
| `Wrapper._state` | `wrapper.py` | 変更 | 変わらない | `question` を `signals.has_question` から `_question` の結果へ替え、`question_forwarded` と `question_id` を渡す |
| `_aware_time` | `wrapper.py` | 新設 | `(value: object) -> Optional[datetime]` | 完了の印に使う時刻を読む。タイムゾーンを持つ `datetime` か None を返し、例外を出さない |

- `_question` の事前条件: `nxt` は同じ見回りで読んだマーカー（`_next_mark` の結果）
- `_question` の副作用: `question.json` の削除・`log.jsonl` の行

使う既存の部品: `Wrapper._next_mark`（前の実行のマーカーを消す形。読み、違えば消して 1 行）と同じ形に揃える。`Wrapper._age` は `first_written_at` を読めなければ `SignalError` にするので使わない。`written_at` と `at` は完了の印に使うかの判断だけで、読めなくても止めないため、例外にせず None を返す `_aware_time` を別に置く。

処理の手順（`_question` は上から順に最初に当たった行で決まる）:

| 条件 | 結果 |
| --- | --- |
| 質問のファイルが無い（手での解除で消したときも）・子が生きていて `observe.open_question` が id を返す | `sec.pending = None`。`sec.unrecorded` が id と違えば `log.jsonl` に `{"kind": "unrecorded_question", "reason": …}` を書いて id を残す。`(True, True, id)`（回さず `wait`。I7） |
| 質問のファイルが無い・`observe.open_question` が None | `sec.forwarded_question = None`・`sec.pending = None`・`sec.unconfirmed = False`・`sec.unrecorded = None`、`(False, False, None)` |
| ファイルの `session_id` が `sec.session_id` と違う | `drop_question` で消し、消せたら `log.jsonl` に `{"kind": "stale_question", "reason": …}`、`(False, False, None)` |
| 子が生きていない | `drop_question` で消す、`(False, False, None)` |
| `tool_use_id` があり `observe.answered` が真 | `drop_question` で消し、消せたら `log.jsonl` に `{"kind": "question_answered", "reason": "会話の記録に回答がある"}`、`(False, False, None)` |
| `_aware_time(nxt.get("written_at"))` と `_aware_time(質問の at)` がどちらも時刻で、前が後より後 | `drop_question` で消し、消せたら `log.jsonl` に `{"kind": "question_answered", "reason": "質問の後に Stop がマーカーを書き直した"}`、`(False, False, None)` |
| 上のどれにも当たらない | `sec.pending` に読んだ中身を置き、`(True, sec.forwarded_question == 印, 印)`。印は `signals.question_mark`。`observe.answered` が None で `sec.forwarded_question == 印` なら、下の異常系の `question_unconfirmed` を打つ |
| `drop_question` が偽（錠を取れない・置き換わっていた） | ファイルは残る。その見回りは `(False, False, None)`。`switch` などが出ても手の中の `question_clear` が質問を見て打たない。次の見回りで読み直し、印が違えば新しい質問として回す |

`_aware_time` は、値が文字列でない・`fromisoformat` が `ValueError`・`tzinfo` が None のとき None、タイムゾーンのある ISO の時刻なら `datetime` を返す。

繰り返しの終わる条件: 見回りの繰り返し（`_loop`）は今と同じ。回答待ちの間は `wait` が続き、上の消す条件のどれかで抜ける。

異常系:

| 失敗 | 原因 | 扱い | 利用者に見える出力と終了コード |
| --- | --- | --- | --- |
| 質問の `at` かマーカーの `written_at` を時刻として使えない | キーが無い・null・文字列でない（`read_next` が保証するのは `first_written_at` と `block` だけ）・ISO の形でない・タイムゾーンが無い | `_aware_time` が None を返し、マーカーの書き直しを完了の印として使わず質問を残す（会話の記録・子の終わり・手での解除で消す）。`nxt["written_at"]` の添字では読まず（`KeyError`）、文字列かを先に確かめ（`TypeError`）、タイムゾーンの無い時刻はある時刻と比べない（`TypeError`）。例外を見回りへ漏らさない | 無し。次の見回りで読み直す |
| 会話の記録を読めず回答を確かめられない | `observe.answered` が None | 質問を残す。`sec.unconfirmed` が偽なら `log.jsonl` に `question_unconfirmed` の行（`path` に会話の記録のパス）を 1 つ書いて立て、`waiting` の理由へ解除の手順を書く | `wrapper.json` の `reason` が `QUESTION_UNCONFIRMED` の文。終了コードは無い（動き続ける）。人がその 1 行を打つと、印が一致するときだけ質問が消えて待ちが解ける |

内部のデータと定数:

| 名前 | 種類 | 既定値 |
| --- | --- | --- |
| `Section.pending` | フィールド `Optional[dict]`（この見回りで読んだ質問） | None |
| `Section.unconfirmed` | フィールド `bool`（`question_unconfirmed` を書いたか） | False |
| `Section.unrecorded` | フィールド `Optional[str]`（`unrecorded_question` を書いた質問の id） | None |
| `QUESTION_UNCONFIRMED` | 定数 `会話の記録 {path} を読めず、質問 {id} の回答を確かめられない。答えたなら ndf relay question-done --dir {dir} --session {session} --mark {id} を打つと続く（ほかの質問は消さない）` | — |

ログ: `log.jsonl` へ `stale_question`・`question_answered`・`question_unconfirmed`・`unrecorded_question` の行を書く。質問の本文と選択肢は書かない。

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 回した質問が回答まで残る | 印が `tool_use_id` に無く `at` だけ | セッションが違う・子が終わった・回答の行・質問の後のマーカーで消える。質問の前のマーカーでは消えない。`written_at` が無い・null・数・タイムゾーンの無い時刻・読めない文字列では例外を出さず残る |
| 会話の記録が無いまま回した質問で `question_unconfirmed` の行が 1 つ、`waiting` の理由に解除の手順 | 解除で `question.json` が消えると、次の見回りで今と同じ判断（マーカーがあれば `switch`） | — |
| 質問 A を回した後、A の回答と質問 B が同じ見回りの間に出ると、`forward` の行が A・B で 1 つずつ | 同じ質問では `forward` の行が 1 つ | — |

## 質問を回す手（`Wrapper._on_wait_like`）

満たす決定: 決定 1、受け入れ条件 5、I2

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._on_wait_like` | `wrapper.py` | 変更 | 変わらない | `forward` のとき、`sec.pending` の写しを作業ディレクトリの一時のファイル（`FORWARD_FILE`、`replace_json`）へ書いて `ask_plan` へ渡し、終えたら一時のファイルを消す。質問のファイルは消さず、`sec.forwarded_question` へ `sec.pending` の印を残す |

- 事前条件: `sec.pending` がある
- 事後条件: `sec.forwarded_question` が印、質問のファイルは残る
- 副作用: 計画のリクエスト（今と同じ）・一時のファイル

内部のデータと定数:

| 名前 | 種類 | 既定値 |
| --- | --- | --- |
| `Section.forwarded_question` | フィールド `Optional[str]`（回した質問の印） | None |
| `FORWARD_FILE` | 定数（計画のリクエストへ渡す写しの名前 `.question.forward.json`） | — |

異常系:

| 失敗 | 原因 | 扱い | 利用者に見える出力 |
| --- | --- | --- | --- |
| 計画のリクエストへ載せられない | `ndf lanes question` の失敗（`ask_plan` の戻り値） | 今と同じく理由を状態と `log.jsonl` に書く。印は残す（同じ質問を回し直さない） | 状態 `waiting` の理由 |

ログ: `forward` の行は `Wrapper._note`（変えない）が書く。理由の文に質問の印が入るので、続けて来た別の質問の行も省かない。

## 子を止める手と子の端末へ書く手（`Wrapper._on_switch`・`_on_resume`・`_on_handover`）

満たす決定: 決定 1・2、受け入れ条件 1・2・3・7、I4・I6

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._on_switch` | `wrapper.py` | 変更 | 変わらない | 錠の前に `account_decide.before_limit` でアカウントを選び（記録の行は書かない）、`signals.question_clear` の錠の中では `in_time()` が真なら子へ `kill(SIGKILL)` だけを送り（今の `terminate(force=True)` の SIGHUP からではなく、最初から SIGKILL。設計書の決定 2 の代償）、錠の外で `account` の行・マーカーの削除・`_stop_child`（回収）・`_before_start`・`_start` を行う |
| `Wrapper._on_resume` | `wrapper.py` | 変更 | 変わらない | `signals.question_clear` の錠の中では `in_time()` が真なら `kill(SIGKILL)` だけを送り、錠の外で起動の回数を数え、`_stop_child`・`_before_start`・`_start` を呼ぶ |
| `Wrapper._on_handover` | `wrapper.py` | 変更 | 変わらない | 錠の前にマーカーを立て（`sec.handover_rest` が残っていれば立て直さない）、`signals.question_clear` の錠の中で、書く残り（`sec.handover_rest`、無ければ引継ぎの文と `\r` の全体）を `O_NONBLOCK` の `os.write` で書く |

`_start` が起動の失敗を `Ended` で返し、`_on_switch`・`_on_resume` がそれをそのまま返す変更は #3842 の設計が受け持つ。

- `_on_switch`・`_on_resume` の事後条件: 錠を取れない・質問がある・`in_time()` が偽なら、何もせず None（マーカー・子・`account` の行はそのまま。`_on_resume` は起動の回数を数えない）
- `_on_handover` の事後条件: 末尾の `\r` まで書けたら `sec.handover_rest = None` にして `handover_asked` を立てる。1 バイトも書かずに終えた（錠を取れない・質問がある・`in_time()` が偽・最初の `os.write` が `EAGAIN`）なら、立てたマーカーを `markers.take` で消し、`handover_asked` も立てない。途中まで書いたなら、書けなかった残りのバイト列を `sec.handover_rest` に残し、マーカーも残し、`handover_asked` は立てない
- 副作用: 子へのシグナル・子の端末への書き込み（今と同じ）。`question_clear` が偽のときは `log.jsonl` にも書かない（次の見回りの `forward` の行が残る）

処理の手順（引継ぎの文の短い書き込み）: 引継ぎの文（約 180 バイト）は待たない形（`O_NONBLOCK`）の書き込みで擬似端末へ入れる。1 バイトも書けなければ（`EAGAIN`）書かずに戻る。擬似端末は全長より短い数を返すことがある（入力の待ち行列の空きが足りない）。そのときは `in_time()` が真で `EAGAIN` でない間だけ続きを書き、書き切れなければ残りを `sec.handover_rest` に残して錠を解く。端末に入った前半は末尾の `\r` が無いので送信されず入力欄に残る。次の見回りで同じ確かめ（錠・質問のファイル・時間）を通ってから残りだけを書き足し、`\r` まで書けたときに初めて引継ぎを頼んだ（`handover_asked`）とする。前半は消さない（消す手も端末へ書く手で、同じ縛りを受けるため、書き足す方が手が少ない）。

内部のデータ:

| 名前 | 種類 | 既定値 |
| --- | --- | --- |
| `Section.handover_rest` | フィールド `Optional[bytes]`（短い書き込みで書けなかった残り。子が替わると Section ごと捨てる） | None |

並行と排他: 錠の中で打つのは `kill(SIGKILL)` か `os.write`（短い書き込みの続きを含む）だけである。回収（`terminate(force=True)`）・マーカー・記録の行・アカウントの選択は錠の外で行う（I4）。打たなかった手は、次の見回りの判断で決め直す。

ログ: `account` の行は `Wrapper._account_for_next`・`_note`（変えない）が `{"at", "section", "kind": "account", "reason"}` の形で書く。錠の後に書く。

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 切り替えで子の `written` が空のまま `terminate` が呼ばれる（`/exit` を書かない） | `_state` の直後に hook の `write_question` で質問を書くと、`switch`・`account`・`restart` で子が生き `terminate` が呼ばれず、`handover` で `written` が空。`switch` で `kill(SIGKILL)` の時点だけ錠がふさがっている | 別のスレッドが `question.json.lock` を持つ間は手を打たず、解けた後に打つ。`handover` の書き込みが `EAGAIN` なら書かずに戻る。短い書き込みの後は残りだけを次の見回りで書く |

## 手での解除（`ndf relay question-done`）

満たす決定: 決定 3、受け入れ条件 10、I5、非機能の条件「可用性」

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_question_done` とサブコマンド `question-done` | `cli.py` | 新設 | `--dir PATH --session ID --mark MARK` | `signals.drop_question(dir, session, mark)` を呼び、印が一致するときだけ消す |

- 事前条件: 印は `question_unconfirmed` の理由の 1 行が示した値
- 事後条件: 消したか既に無ければ終了コード 0。消さなければ `ndf-relay:` の 1 行と終了コード 1
- 副作用: `question.json` の削除

異常系:

| 失敗 | 原因 | 扱い | 利用者に見える出力と終了コード |
| --- | --- | --- | --- |
| 印が一致しない | 回答済みと確かめた後に hook が別の質問を置いた（`drop_question` が偽で、読み直した質問の印が渡した印と違う） | 消さない。新しい質問は次の見回りで回る | `ndf-relay: 質問が置き換わっている（今の印 <印>）。消さない`、終了コード 1 |
| 錠を取れない | hook が書いている（`drop_question` が偽で、読み直した質問の印が同じか無い） | 消さない。打ち直せる | `ndf-relay: 質問のファイルの錠を取れない。打ち直す`、終了コード 1 |

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 印が一致すれば消して 0 | 既に無ければ 0 | 印が違えば消さず、今の印を含む `ndf-relay:` の 1 行と 1 |

## 手順書（`relay.md`）

満たす決定: 決定 4、受け入れ条件 8

- 「承認ゲートを越えない守り」の表を、`decide` の判断の順（質問 → 上限と認証の失敗 → マーカー → 子の終わり → 文脈の上限 → 止まり）と、hook の `ask-forward`・`relay-marker`・`stop-wait` の振る舞いで書き直す。回答待ちの間は `wait` を返し、回答の済みは会話の記録の `tool_result` か質問の後のマーカーの書き直しで知ることを書く
- `question.lock`・`asked`・`wait-held.json`・`/exit` の書き込み・`log.jsonl` の `prose_wait` の行・`ndf-gate` の自動の付与の記述を消す
- 「落ちたときの続け方」に、会話の記録を読めず質問が残ったときの 1 行（`ndf relay question-done --dir <NDF_RELAY_DIR> --session <session_id> --mark <tool_use_id>`）を載せる。`rm` で消す手順は書かない
- 確かめは受け入れ条件 8 の `grep` と `python3 scripts/check-markdown-links.py` で行い、文言のテストは書かない
