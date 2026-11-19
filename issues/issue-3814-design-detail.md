# #3814 の詳細設計: 回答待ちの質問のあいだ子を止めず、子の端末へ書かない

設計書（[issue-3814-design.md](./issue-3814-design.md)）の「構成要素」を、ファイルと関数の粒度へ下ろした文書である。決定・不変条件（I1〜I8）・受け入れ条件の番号は、設計書と要求仕様（[issue-3814-requirements.md](./issue-3814-requirements.md)）の番号を指す。パスは `plugins/ndf/` からの相対で書く。新設する関数が 2 つ以上あるので、設計書に畳まずにこのファイルに置く。

## 地図

```text
plugins/ndf/
├── src/ndf/domains/relay/
│   ├── wrapper.py         ポーリングで質問を読み・消し、子を一時停止させて確かめてから止める
│   ├── switch_decide.py   転送した回答待ちの質問で wait を返す
│   ├── signals.py         質問のファイルを読む・ロックの中で消す・解除した ID を残す
│   ├── observe.py         会話の記録から回答と回答の無い質問を探す
│   └── cli.py             手動の解除のサブコマンド question-done
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
| [質問のファイルの読み](#質問のファイルの読みsignalsread_questionquestion_id) | `signals.py` | 決定 1 |
| [ロックの中で消す](#ロックの中で消すsignalsdrop_question) | `signals.py` | 決定 1・3 |
| [解除と解除した ID](#解除と解除した-idsignalsrelease_questionread_released) | `signals.py` | 決定 3・5 |
| [子の一時停止と確かめ](#子の一時停止と確かめwrapper_kill_if_clearfreeze_child) | `wrapper.py` | 決定 2 |
| [会話の記録の読み](#会話の記録の読みobservequestion_statusopen_question) | `observe.py` | 決定 2・3 |
| [ポーリングの質問の扱い](#ポーリングの質問の扱いwrapper_question_state_aware_time) | `wrapper.py` | 決定 1・2・3・5 |
| [質問を転送する操作と待ちの理由](#質問を転送する操作と待ちの理由wrapper_on_wait_likerelease_line) | `wrapper.py` | 決定 1・3 |
| [子を止める操作](#子を止める操作wrapper_on_switch_on_resume_on_handover) | `wrapper.py` | 決定 2 |
| [例外の経路](#例外の経路wrapper_loop) | `wrapper.py` | 決定 5 |
| [手動の解除](#手動の解除ndf-relay-question-done) | `cli.py` | 決定 3・5 |
| [手順書](#手順書relaymd) | `relay.md` | 決定 4 |

## 判定（`switch_decide`）

満たす決定: 決定 1、受け入れ条件 1・2・3・5・9、I1・I2

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `decide` | `switch_decide.py` | 変更 | 変わらない | `question` が真のとき、`question_forwarded` が偽なら `forward`、真なら `wait` を返す。同じ状態に同じ操作を返す |

処理の手順:

| 条件 | 結果 |
| --- | --- |
| `question` 真・`question_forwarded` 偽 | `Step("forward", FORWARD.format(id=state.question_id))`。`question_id` が None なら今と同じ文 |
| `question` 真・`question_forwarded` 真 | `Step("wait", QUESTION_PENDING)` |
| `question` 偽 | 今と同じ（上限と認証の失敗 → マーカー → 子の終わり → 文脈の上限 → 止まり） |

内部のデータと定数:

| 名前 | 種類 | 既定値 |
| --- | --- | --- |
| `SectionState.question_forwarded` | フィールド `bool`（今の質問を転送したか） | False |
| `SectionState.question_id` | フィールド `Optional[str]`（今の質問の ID） | None |
| `FORWARD` | 定数 `AskUserQuestion の質問 {id} を人かオーガナイザーへ転送する（hook は待たない）` | — |
| `QUESTION_PENDING` | 定数 `回答待ちの質問がある。子を止めず、子の端末へ書かない` | — |

2 つのフィールドに既定値を置くので、`ndf relay decide` は今の入力の JSON も読める。`question_id` が `forward` の理由の文に入り、質問ごとに理由が変わるため、直前と同じ理由を省く `Wrapper._note` でも別の質問の `forward` の行が残る（I2）。

単体テストの観点:

| 正常 | 境界値 |
| --- | --- |
| 転送した質問で `wait`。転送していない質問の理由に質問の ID が入る | `question_forwarded`・`question_id` の無い JSON（`ndf relay decide`）で今と同じ文の `forward` |

## 質問のファイルの読み（`signals.read_question`・`question_id`）

満たす決定: 決定 1、受け入れ条件 4・5・6

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `read_question` | `signals.py` | 新設 | `(directory: Path) -> Optional[dict[str, Any]]` | 質問のファイルの中身を返す。無ければ None。読むだけ |
| `question_id` | `signals.py` | 新設 | `(question: Mapping[str, Any]) -> Optional[str]` | 質問の ID。`tool_use_id` が文字列ならそれ、無ければ `at` が文字列ならそれ、どちらも無ければ None |

使う既存の部品: `signals._read`（壊れたら `SignalError`、無ければ None）。`read_next` と同じ形で読む。

異常系:

| 失敗 | 原因 | 扱い | 利用者に見える出力と終了コード |
| --- | --- | --- | --- |
| 質問のファイルが壊れている | 手動の編集（hook は `replace_json` で置き換えるので書きかけは読まない） | `SignalError` を呼び出し元へ返す。呼び出し元の `Wrapper._question` は回答待ちとして扱い、`_loop` の例外の経路へは渡さない（決定 5。[ポーリングの質問の扱い](#ポーリングの質問の扱いwrapper_question_state_aware_time)） | 終了しない。待ちの理由に `--broken` の解除の 1 行 |

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 中身を返す | 無ければ None | 壊れていれば `SignalError` |

## ロックの中で消す（`signals.drop_question`）

満たす決定: 決定 1・3、受け入れ条件 4・6・10、I5

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `drop_question` | `signals.py` | 新設 | `(directory: Path, session_id: Optional[str], question_id: Optional[str]) -> bool` | ロック `question.json.lock` を待たずに取得し、読み直した質問の `session_id` と `question_id` が渡した値と同じときだけ消す |

- 事後条件: 消したか、既に無ければ真。ロックを取得できない・置き換わっていた・壊れていたら偽で、ファイルは残る
- 副作用: `question.json` の削除

使う既存の部品: `signals.held`（ロックのファイルの排他。`wait` 0 で待たない）と、hook の `write_json` が使うロックの名前 `<path>.lock`。hook と同じロックの名前を同じモジュールで作る。

並行と排他: hook（`ask-forward`）は `signals.write_json` のロックの中で `replace_json` で置き換える。置き換えは原子的なので、ラッパーはロックを取得せずに読んでも書きかけを読まない。消す操作だけはロックの中で読み直す。順序の例: ラッパーが回答済みの A を読む → hook が B へ置き換える → ラッパーがロックを取得して読み直すと B → 消さない。消した後に hook が書いた質問は、次のポーリングで新しい質問として転送する。

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 同じ質問なら消して真 | 既に無ければ真 | 読み直すと別の質問（A を渡し B が置いてある）なら消さず偽。ロックを別のスレッドが保持していれば消さず偽 |

## 解除と解除した ID（`signals.release_question`・`read_released`）

満たす決定: 決定 3・5、受け入れ条件 10、I5・I7、非機能の条件「可用性」

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `release_question` | `signals.py` | 新設 | `(directory: Path, session_id: str, question_id: Optional[str], broken: bool = False) -> Released` | ロック `question.json.lock` を待たずに取得し、質問のファイルを読み直して、下の表のとおり消すか残す。解除したら `question.released.json` へ `{"session_id", "question_id", "at"}` を `replace_json` で書く |
| `read_released` | `signals.py` | 新設 | `(directory: Path) -> Optional[dict[str, Any]]` | `question.released.json` を読む。無ければ None、壊れていれば None（解除の記録は守りを緩める側なので、読めなければ使わない） |
| `Released` | `signals.py` | 新設 | `NamedTuple(ok: bool, current: Optional[str], why: str)` | `ok` は解除したか、`current` は読み直した今の質問の ID（別の質問のとき）、`why` は解除しなかった理由の種類（`replaced`・`locked`・`broken`・`not_broken`） |

処理の手順（ロックの中で読み直した質問のファイルで決まる）:

| 読み直した質問のファイル | `broken` 偽（`--question-id`） | `broken` 真（`--broken`） |
| --- | --- | --- |
| `session_id` と質問の ID が渡した値と同じ | 消し、解除した ID を残す。`ok` | 消さない。`why=not_broken`・`current` に ID |
| 無い | 消すものは無い。解除した ID を残す（I7 の質問の解除）。`ok` | 何もしない。`ok` |
| 別の質問 | 消さない。`why=replaced`・`current` に ID | 消さない。`why=not_broken`・`current` に ID |
| 壊れている | 消さない。`why=broken` | 消す（解除した ID は残さない）。`ok` |
| ロックを取得できない | 消さない。`why=locked` | 同じ |

- 事後条件: `ok` が偽ならファイルは何も変わらない
- 副作用: `question.json` の削除・`question.released.json` の置き換え

並行と排他: 消す操作と解除した ID を残す操作を同じロックの中で行うので、hook が別の質問へ置き換えた後に古い ID の解除を残すことは無い。`question.released.json` は 1 件だけを持ち、新しい解除で置き換わる。ラッパーは読んだ ID を `Section.released` の集まりへ足すので、置き換わっても前の解除は失われない（ラッパーがポーリングのあいだに 2 回解除されたときだけ前の 1 件を読み落とし、その質問はもう一度解除の 1 行を出す）。

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 同じ質問なら消して ID を残す。`--broken` で壊れたファイルを消す | 質問のファイルが無ければ ID だけを残す | 別の質問・ロックの使用中・`--question-id` で壊れたファイル・`--broken` で読める質問では、何も変えず `ok` が偽 |

## 子の一時停止と確かめ（`Wrapper._kill_if_clear`・`freeze_child`）

満たす決定: 決定 2・5、受け入れ条件 1・2・3・7、I4

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `freeze_child` | `wrapper.py` | 新設 | `(child: Any, wait: float) -> bool` | 子へ `kill(SIGSTOP)` を送り、`os.waitid(P_PID, child.pid, WSTOPPED \| WEXITED \| WNOWAIT \| WNOHANG)` を `wait` 秒まで短い間隔で繰り返して、止まったら真。終わっていた・`wait` 秒のうちに止まらなければ偽（偽のときは呼び出し元が SIGCONT を送る） |
| `Wrapper._kill_if_clear` | `wrapper.py` | 新設 | `(self, sec: Section) -> bool` | 子を一時停止させ、確かめが通れば止めたまま `kill(SIGKILL)` を送って真。外れたら `kill(SIGCONT)` を送って偽 |

`Wrapper` は `freeze` を引数で受け（既定は `freeze_child`）、テストは偽の子に合わせた関数を渡す。

処理の手順（`_kill_if_clear`）:

1. `freeze(sec.child, self.s.tick)` が偽なら、`kill(SIGCONT)` を送って偽を返す
2. 擬似端末を `select` の待ち 0 で読めなくなるまで読み（`_from_child`。端末へそのまま渡し、`sec.last_output` を進める）
3. 次のどれかなら、`kill(SIGCONT)` を送って偽を返す
   - `sec.last_output` が判定のときの値（`sec.seen_output`。`_state` が残す）と違う（判定の後に子が出力した）
   - 質問のファイルが在る（`has_question`。壊れていても在る）
   - `observe.open_question(transcript, size, exclude=sec.released)` が id を返す
4. `kill(SIGKILL)` を送って真を返す（回収の `terminate(force=True)` は呼び出し元が `_stop_child` で後に行う）

- 事前条件: 同じポーリングの `_state` が `sec.seen_output` を残している。`_state` と `_kill_if_clear` のあいだに `_pump` を呼ばない
- 事後条件: 真なら子は SIGKILL を受けている。偽なら子は SIGCONT を受けて動いている（`freeze` が子の終わりを見たときも SIGCONT は害が無い）
- 副作用: 子へのシグナル・端末への出力の受け渡し

並行と排他（判定の後の選択肢を止める議論）:

- 子が選択肢を画面に出すには擬似端末へ書く必要がある。1 の後、子は止まっていて書けない。止まる前に子が書いたバイトは擬似端末の出力の待ち行列に残っているので、2 で読み切れる。3 の確かめから 4 の SIGKILL まで、ラッパーがスケジューラーにどれだけ待たされても、子は書けないので、確かめた後に選択肢は出ない
- hook の `ask-forward` は子とは別のプロセスで、止まっていない。3 の後に hook が質問のファイルを書いても、子は止まったまま SIGKILL を受けるので選択肢は出ない。残った質問のファイルは、次のセグメントの最初のポーリングがセッションの違いで消す（`stale_question`）
- hook が質問を書けずに返った（ロックの待ちを越えた・`OSError`）後に子が選択肢を描いたなら、その出力が 2 で読めて 3 で外れる
- 判定より前に描かれた選択肢は、静かな時間のあいだ会話の記録に書かれた `tool_use` を 3 の `open_question` で見る（I7）
- 時間の制約は無い。`freeze` の `wait` は止まらない子を待ち続けないための上限で、外れたら止めない側に倒れる

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 出力も質問も無ければ `SIGSTOP`・`SIGKILL` の順で真 | 偽の子の `kill(SIGSTOP)` の中で出力を足すと偽で `SIGCONT`。`SIGSTOP` の後に偽の時計を `hook.ask-forward` の秒より進めても真 | 質問のファイルが在る・壊れている・記録に回答の無い質問がある・`freeze` が偽なら、`SIGKILL` を送らず `SIGCONT` |

## 会話の記録の読み（`observe.question_status`・`open_question`）

満たす決定: 決定 2・3、受け入れ条件 4、I3・I7、非機能の条件「性能・拡張性」「可用性」

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `question_status` | `observe.py` | 新設 | `(path: Path, tool_use_id: str, size: int) -> Literal["answered", "open", "unknown"]` | 会話の記録の末尾 `size` バイトの行で、`type` が `user` で `message.content` が配列の行に、`type` が `tool_result` で `tool_use_id` が等しい要素があれば `answered`。無く、`assistant` の行の `message.content[]` に `type: tool_use` で `id` が等しい要素があれば `open`。どちらも無いか、記録のファイルが無いか読めなければ `unknown` |
| `open_question` | `observe.py` | 新設 | `(path: Path, size: int, exclude: frozenset[str] = frozenset()) -> Optional[str]` | 会話の記録の末尾 `size` バイトの行で、`assistant` の行の `message.content[]` の `type: tool_use`・`name: AskUserQuestion` の要素のうち、後の `user` の行に同じ `tool_use_id` の `tool_result` が無く、id が `exclude` に無いものの id（最後のもの）。無いか、記録が無いか読めなければ None |

どちらも読むだけである。`size` は `relay.transcript-tail` で、文脈の量の読み（`observe.context_tokens`）と同じ量を読む。`question_status` を呼ぶのは、回答待ちの質問が `tool_use_id` を持つポーリングだけである（`at` だけの質問は `unknown` として扱う）。

使う既存の部品: `ndf.adapters.agents.tail`。`context_tokens` は量だけを返すので別の関数にする。

異常系:

| 関数 | 失敗 | 原因 | 扱い |
| --- | --- | --- | --- |
| `question_status` | 会話の記録が無い・読めない | 保存先の違い・権限（`path.is_file()` が偽・`agents.tail` の `OSError`） | `unknown` を返す。ポーリングは質問を残し、`question_unconfirmed` の行を書く（[ポーリングの質問の扱い](#ポーリングの質問の扱いwrapper_question_state_aware_time)） |
| `question_status` | 読めたが質問も回答も無い | 末尾 `size` の外にある・別の会話の記録を読んでいる | `unknown` を返す（同じ扱い） |
| `question_status`・`open_question` | 行が読めない・形が違う | 書き途中で末尾が切れた・別の種類の行（`json.loads` の `ValueError`・`dict` でない・`message.content` が配列でない） | その行を飛ばす |
| `open_question` | 会話の記録が無い・読めない | 保存先の違い・権限（`OSError`） | None を返す（判定の後の選択肢は I4 の出力の確かめが守る） |

単体テストの観点:

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `question_status` | 実際の形の行（`{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_A", "content": "Your questions have been answered: …"}]}, "toolUseResult": {…}, "sessionId": …}`）で `answered`。`assistant` の行の `tool_use`（`id` が `toolu_A`）だけなら `open`。フィクスチャは手元の記録からコピーした行の形を使う | 記録のファイルが無いと `unknown`。読めた行に質問も回答も無ければ `unknown` | 別の質問（`toolu_X`）の `tool_result`・最上位にだけ `tool_use_id` がある行では `answered` にならない。末尾で切れた行は読み飛ばす |
| `open_question` | `tool_result` の無い `AskUserQuestion` の `tool_use` の id | 記録が無いと None。id が `exclude` にあれば None | `tool_result` のある `tool_use`・別の Tool の `tool_use`・`user` の行の要素では None |

## ポーリングの質問の扱い（`Wrapper._question`・`_state`・`_aware_time`）

満たす決定: 決定 1・2・3・5、受け入れ条件 4・5・6、I3・I7・I8、非機能の条件「可用性」

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._question` | `wrapper.py` | 新設 | `(self, sec: Section, nxt: Optional[dict]) -> tuple[bool, bool, Optional[str]]` | 解除した ID を読み、質問のファイルを読み、消してよければ消し、（回答待ちか, 転送したか, 質問の ID）を返す。回答待ちなら `sec.waiting_on` に解除の 1 行の材料を置く |
| `Wrapper._state` | `wrapper.py` | 変更 | 変わらない | `question` を `signals.has_question` から `_question` の結果へ替え、`question_forwarded` と `question_id` を渡す。`sec.seen_output = sec.last_output` を残す（I4 の確かめの基準） |
| `_aware_time` | `wrapper.py` | 新設 | `(value: object) -> Optional[datetime]` | 完了の検知に使う時刻を読む。タイムゾーンを持つ `datetime` か None を返し、例外を出さない |

- `_question` の事前条件: `nxt` は同じポーリングで読んだマーカー（`_next_mark` の結果）
- `_question` の副作用: `question.json` の削除・`log.jsonl` の行・`sec.released` への追加

使う既存の部品: `Wrapper._next_mark`（前の実行のマーカーを消す形。読み、違えば消して 1 行）と同じ形に揃える。`Wrapper._age` は `first_written_at` を読めなければ `SignalError` にするので使わない。`written_at` と `at` は完了の検知に使うかの判断だけで、読めなくても止めないため、例外にせず None を返す `_aware_time` を別に置く。

処理の手順（`_question` は、最初に `signals.read_released` の記録の `session_id` が `sec.session_id` と同じなら `question_id` を `sec.released` へ足し、その後は上から順に最初に当たった行で決まる）:

| 条件 | 結果 |
| --- | --- |
| `read_question` が `SignalError`（壊れている） | 子が生きていなければ、`release_question(…, broken=True)` で消して `(False, False, None)`。生きていれば、`sec.broken` が偽なら `log.jsonl` に `{"kind": "question_broken", "reason": …}` を書いて立てる。`sec.waiting_on = Waiting(broken=True)`、`(True, True, None)`（転送せず `wait`。決定 5） |
| 質問のファイルが無い・子が生きていて `observe.open_question(…, exclude=sec.released)` が id を返す | `sec.pending = None`。`sec.unrecorded` が id と違えば `log.jsonl` に `{"kind": "unrecorded_question", "reason": …}` を書いて id を残す。`sec.waiting_on = Waiting(id)`、`(True, True, id)`（転送せず `wait`。I7） |
| 質問のファイルが無い・`open_question` が None | `sec.forwarded_question = None`・`sec.pending = None`・`sec.unconfirmed = None`・`sec.unrecorded = None`・`sec.broken = False`・`sec.waiting_on = None`、`(False, False, None)` |
| ファイルの `session_id` が `sec.session_id` と違う | `drop_question` で消し、消せたら `log.jsonl` に `{"kind": "stale_question", "reason": …}`、`(False, False, None)` |
| 子が生きていない | `drop_question` で消す、`(False, False, None)` |
| `tool_use_id` があり `observe.question_status` が `answered` | `drop_question` で消し、消せたら `log.jsonl` に `{"kind": "question_answered", "reason": "会話の記録に回答がある"}`、`(False, False, None)` |
| `_aware_time(nxt.get("written_at"))` と `_aware_time(質問の at)` がどちらも時刻で、前が後より後 | `drop_question` で消し、消せたら `log.jsonl` に `{"kind": "question_answered", "reason": "質問の後に Stop がマーカーを書き直した"}`、`(False, False, None)` |
| 上のどれにも当たらない | `sec.pending` に読んだ中身を置き、`sec.waiting_on = Waiting(qid)`、`(True, sec.forwarded_question == qid, qid)`。`qid` は `signals.question_id` が返す質問の ID。`sec.forwarded_question == qid` で、`question_status` が `unknown`（`at` だけの質問を含む）なら、下の異常系の `question_unconfirmed` の行を書く |
| `drop_question` が偽（ロックを取得できない・置き換わっていた） | ファイルは残る。そのポーリングは `(False, False, None)`。`switch` などが出ても `_kill_if_clear` が質問のファイルを見て子を止めない。次のポーリングで読み直し、ID が違えば新しい質問として転送する |

`_aware_time` は、値が文字列でない・`fromisoformat` が `ValueError`・`tzinfo` が None のとき None、タイムゾーンのある ISO の時刻なら `datetime` を返す。

繰り返しの終わる条件: ポーリングの繰り返し（`_loop`）は今と同じ。回答待ちの間は `wait` が続き、上の消す条件か解除のどれかで抜ける。

異常系:

| 失敗 | 原因 | 扱い | 利用者に見える出力と終了コード |
| --- | --- | --- | --- |
| 質問の `at` かマーカーの `written_at` を時刻として使えない | キーが無い・null・文字列でない（`read_next` が保証するのは `first_written_at` と `block` だけ）・ISO の形でない・タイムゾーンが無い | `_aware_time` が None を返し、マーカーの書き直しを完了の検知に使わず質問を残す（会話の記録・子の終わり・手動の解除で消す）。`nxt["written_at"]` の添字では読まず（`KeyError`）、文字列かを先に確かめ（`TypeError`）、タイムゾーンの無い時刻はある時刻と比べない（`TypeError`）。例外をポーリングへ漏らさない | 無し。次のポーリングで読み直す |
| 回答を確かめられない | `question_status` が `unknown`（記録を読めない・読めても質問も回答も末尾に無い・質問が `at` だけ） | 質問を残す。`sec.unconfirmed` が `qid` と違えば `log.jsonl` に `question_unconfirmed` の行（`path` に会話の記録のパス、`command` に解除の 1 行）を 1 つ書いて `qid` を残す | `wrapper.json` の `reason` に解除の 1 行（回答待ちの間はいつも。[質問を転送する操作と待ちの理由](#質問を転送する操作と待ちの理由wrapper_on_wait_likerelease_line)）。終了コードは無い（動き続ける） |
| 質問のファイルが壊れている | 手動の編集 | 回答待ちとして扱い、転送しない。`question_broken` の行を 1 つ | `wrapper.json` の `reason` に `--broken` の解除の 1 行。終了コードは無い |

内部のデータと定数:

| 名前 | 種類 | 既定値 |
| --- | --- | --- |
| `Section.pending` | フィールド `Optional[dict]`（このポーリングで読んだ質問） | None |
| `Section.unconfirmed` | フィールド `Optional[str]`（`question_unconfirmed` を書いた質問の ID） | None |
| `Section.unrecorded` | フィールド `Optional[str]`（`unrecorded_question` を書いた質問の id） | None |
| `Section.broken` | フィールド `bool`（`question_broken` を書いたか） | False |
| `Section.released` | フィールド `frozenset[str]`（人が解除した質問の ID） | 空 |
| `Section.waiting_on` | フィールド `Optional[Waiting]`（回答待ちの質問の ID か、壊れたファイル） | None |
| `Section.seen_output` | フィールド `float`（判定のときの `last_output`） | 0.0 |

ログ: `log.jsonl` へ `stale_question`・`question_answered`・`question_unconfirmed`・`unrecorded_question`・`question_broken` の行を書く。質問の本文と選択肢は書かない。

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 転送した質問が回答まで残る | 質問の ID が `tool_use_id` に無く `at` だけ（`unknown` として解除の 1 行と `question_unconfirmed`） | セッションが違う・子が終わった・回答の行・質問の後のマーカーで消える。質問の前のマーカーでは消えない。`written_at` が無い・null・数・タイムゾーンの無い時刻・読めない文字列では例外を出さず残る |
| 会話の記録が無いまま転送した質問と、記録は読めるが末尾に質問も回答も無い転送した質問で、`question_unconfirmed` の行が 1 つ | 解除で `question.json` が消えると、記録に同じ質問の `tool_use` が回答の無いまま残っていても、次のポーリングで今と同じ判断（マーカーがあれば `switch`） | 壊れたファイルで `wait` と `question_broken` の行が 1 つ。`config_broken` へ落ちない |
| 質問 A を転送した後、A の回答と質問 B が同じポーリングの間に出ると、`forward` の行が A・B で 1 つずつ | 同じ質問では `forward` の行が 1 つ | 別のセッションの `question.released.json` は読まない |

## 質問を転送する操作と待ちの理由（`Wrapper._on_wait_like`・`release_line`）

満たす決定: 決定 1・3・5、受け入れ条件 5、I2・I8

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._on_wait_like` | `wrapper.py` | 変更 | 変わらない | `forward` のとき、`sec.pending` のコピーを作業ディレクトリの一時のファイル（`FORWARD_FILE`、`replace_json`）へ書いて `ask_plan` へ渡し、終えたら一時のファイルを消す。質問のファイルは消さず、`sec.forwarded_question` へ `sec.pending` の質問の ID を残す。`sec.pending` が無ければ転送しない。`forward`・`wait` のどちらでも `sec.waiting_on` があれば、待ちの理由の後ろへ `release_line` の 1 行を足して状態のファイルへ書く |
| `release_line` | `wrapper.py` | 新設 | `(directory: Path, session_id: str, waiting: Waiting) -> str` | そのまま打てる解除の 1 行を返す。引数はすべて `shlex.quote` で引用する（`shlex.join`） |

`release_line` が返す形（`QUESTION_RELEASE`。オプション名は `cli.py` の `question-done` の定義と同じで、テストは `shlex.split` した 1 行を `cli.main` の引数解析へ通して確かめる）:

| 待ち | 1 行 |
| --- | --- |
| 質問の ID がある | `答えたなら次の 1 行を打つと続く（ほかの質問は消さない）: ndf relay question-done --dir <dir> --session <session_id> --question-id <id>` |
| 壊れたファイル | `質問のファイル <path> が壊れている。中身を確かめて消すなら次の 1 行を打つ: ndf relay question-done --dir <dir> --session <session_id> --broken` |

`question_unconfirmed` の行の `reason` は `QUESTION_UNCONFIRMED`（`会話の記録 {path} で質問 {id} の回答を確かめられない`）で、`command` に同じ 1 行を入れる。

- 事前条件: `forward` のとき `sec.pending` がある
- 事後条件: `sec.forwarded_question` が質問の ID、質問のファイルは残る
- 副作用: 計画のリクエスト（今と同じ）・一時のファイル・状態のファイル

内部のデータと定数:

| 名前 | 種類 | 既定値 |
| --- | --- | --- |
| `Section.forwarded_question` | フィールド `Optional[str]`（転送した質問の ID） | None |
| `FORWARD_FILE` | 定数（計画のリクエストへ渡すコピーの名前 `.question.forward.json`） | — |
| `Waiting` | `NamedTuple(question_id: Optional[str], broken: bool = False)` | — |

異常系:

| 失敗 | 原因 | 扱い | 利用者に見える出力 |
| --- | --- | --- | --- |
| 計画のリクエストへ載せられない | `ndf lanes question` の失敗（`ask_plan` の戻り値） | 今と同じく理由を状態と `log.jsonl` に書く。質問の ID は残す（同じ質問を転送し直さない） | 状態 `waiting` の理由（解除の 1 行も続く） |

ログ: `forward` の行は `Wrapper._note`（変えない）が書く。理由の文に質問の ID が入るので、続けて来た別の質問の行も省かない。

## 子を止める操作（`Wrapper._on_switch`・`_on_resume`・`_on_handover`）

満たす決定: 決定 1・2、受け入れ条件 1・2・3・7、I4・I6

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._on_switch` | `wrapper.py` | 変更 | 変わらない | 止める前に `account_decide.before_limit` でアカウントを選び（記録の行は書かない）、`_kill_if_clear` が真なら `account` の行・マーカーの削除・`_stop_child`（回収）・`_before_start`・`_start` を行う |
| `Wrapper._on_resume` | `wrapper.py` | 変更 | 変わらない | `_kill_if_clear` が真なら、起動の回数を数え、`_stop_child`・`_before_start`・`_start` を呼ぶ |
| `Wrapper._on_handover` | `wrapper.py` | 変更 | 変わらない | `_kill_if_clear` が真なら、`_stop_child`・`_before_start(update=False)` の後、同じ会話を `Launch(sec.number, sec.session_id, sec.account, step.prompt, resume=True)` で起動し、新しい `Section` の `handover_asked` を立てる。起動の回数は数えない。子の端末へは書かない |
| `Wrapper._send` | `wrapper.py` | 削除 | — | 呼び出し元が無くなる（I6） |

`_start` が起動の失敗を `Ended` で返し、`_on_switch`・`_on_resume` がそれをそのまま返す変更は #3842 の設計が受け持つ。`_on_handover` が `_start` を呼ぶのはこの設計からなので、同じ形で返すことはこの設計が受け持つ。

- 事後条件: `_kill_if_clear` が偽なら、何もせず None（マーカー・子・`account` の行・起動の回数・`handover_asked` はそのまま。`log.jsonl` にも書かない。次のポーリングの `forward` か `wait` の行が残る）
- 副作用: 子へのシグナル（`SIGSTOP`・`SIGKILL` か `SIGCONT`）。子の端末へは書かない

並行と排他: 確かめと SIGKILL のあいだの議論は [子の一時停止と確かめ](#子の一時停止と確かめwrapper_kill_if_clearfreeze_child) にある。回収（`terminate(force=True)`）・マーカー・記録の行は SIGKILL の後に行う。しなかった操作は、次のポーリングの判断で決め直す。

ログ: `account` の行は `Wrapper._account_for_next`・`_note`（変えない）が `{"at", "section", "kind": "account", "reason"}` の形で書く。SIGKILL の後に書く。

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 切り替えで子の `written` が空のまま `SIGSTOP`・`SIGKILL`・`terminate` の順に呼ばれる（`/exit` を書かない）。`handover` で `written` が空のまま、`--resume <同じ ID> <引継ぎの 1 行>` で起動され `handover_asked` が真 | `_state` の直後に hook の `write_question` で質問を書くか、偽の子が選択肢の出力を出すと、`switch`・`account`・`restart`・`handover` のどれでも子が生き、`SIGCONT` が呼ばれ、`terminate` が呼ばれない | 確かめが外れたとき、マーカー・`account` の行・起動の回数・`handover_asked` が変わらない |

## 例外の経路（`Wrapper._loop`）

満たす決定: 決定 5、受け入れ条件 1・2・3

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._loop` | `wrapper.py` | 変更 | 変わらない | `decide`（`_state` を含む）が `SignalError`・`AccountError` を出したら、`_stop_child()` を直接呼ばず `_halt_broken(exc)` を返す |
| `Wrapper._halt_broken` | `wrapper.py` | 新設 | `(self, exc: Exception) -> Ended` | 状態のファイルを `waiting`（理由は例外の文と「質問の回答か子の終わりを待ってから止める」）にし、ポーリングごとに `_pump` で出力と利用者の入力をそのまま渡し、子が生きている間は `_question` で質問を読み直して `sec.seen_output` を残してから `_kill_if_clear` を試す。真になるか子が終わったら `_stop_child` で回収し、`Ended("config_broken", str(exc))` を返す |

- `_halt_broken` の中では `decide` を呼ばず、切り替え・起動し直し・引継ぎ・転送をしない
- `_question` がまた例外を出したら、質問が在るとみなして `_kill_if_clear` を試さない（子の終わりを待つ）

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 質問が無いとき `next.json` を壊すと、今と同じく子を止めて `config_broken`（終了コード 2） | 回答待ちの質問がある間に `next.json` を壊すと、子を止めず `waiting`。回答の `tool_result` を足すと止めて `config_broken` | 子が自分で終わると `config_broken` で終わる |

## 手動の解除（`ndf relay question-done`）

満たす決定: 決定 3・5、受け入れ条件 10、I5、非機能の条件「可用性」

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `_question_done` とサブコマンド `question-done` | `cli.py` | 新設 | `--dir PATH --session ID (--question-id ID \| --broken)`（どちらか 1 つ。`argparse` の相互排他のグループで必須） | `signals.release_question(dir, session, question_id, broken)` を呼び、結果を終了コードと 1 行にする |

オプション名は `--dir`・`--session`・`--question-id`・`--broken` の 4 つに統一する。要求仕様の「影響」にあった `--mark` は使わない（2026-11-19 の設計レビューの指摘で、要求の本文 #3814 も同じ名前へ直した）。

- 事前条件: 引数は待ちの理由か `question_unconfirmed` の行の `command` が示した 1 行
- 事後条件: 解除したか既に無ければ終了コード 0。解除しなければ `ndf-relay:` の 1 行と終了コード 1
- 副作用: `question.json` の削除・`question.released.json` の置き換え

異常系:

| 失敗 | 原因 | 扱い | 利用者に見える出力と終了コード |
| --- | --- | --- | --- |
| 質問の ID が一致しない | 回答済みと確かめた後に hook が別の質問を置いた（`why=replaced`） | 消さない。新しい質問は次のポーリングで転送される | `ndf-relay: 質問が置き換わっている（今の質問の ID <ID>）。消さない`、終了コード 1 |
| ロックを取得できない | hook が書いている（`why=locked`） | 消さない。打ち直せる | `ndf-relay: 質問のファイルのロックを取得できない。打ち直す`、終了コード 1 |
| `--question-id` で質問のファイルが壊れている | 手動の編集（`why=broken`） | 消さない | `ndf-relay: 質問のファイルが壊れている。中身を確かめて --broken で消す`、終了コード 1 |
| `--broken` で質問のファイルが読める | hook が正しい質問へ置き換えた（`why=not_broken`） | 消さない | `ndf-relay: 質問のファイルは壊れていない（今の質問の ID <ID>）。消さない`、終了コード 1 |

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 質問の ID が一致すれば消して 0。待ちの理由の 1 行（作業ディレクトリのパスに空白を含む）を `shlex.split` して `cli.main` へ渡すと 0 | 既に無ければ ID を残して 0 | ID が違えば消さず、今の質問の ID を含む `ndf-relay:` の 1 行と 1。`--question-id` と `--broken` を両方渡す・どちらも渡さないと引数エラー |

## 手順書（`relay.md`）

満たす決定: 決定 4、受け入れ条件 8

- 「承認ゲートを越えない守り」の表を、`decide` の判断の順（質問 → 上限と認証の失敗 → マーカー → 子の終わり → 文脈の上限 → 止まり）と、hook の `ask-forward`・`relay-marker`・`stop-wait` の振る舞いで書き直す。回答待ちの間は `wait` を返し、回答の済みは会話の記録の `tool_result` か質問の後のマーカーの書き直しで知ることを書く
- `question.lock`・`asked`・`wait-held.json`・`/exit` の書き込み・`log.jsonl` の `prose_wait` の行・`ndf-gate` の自動の付与の記述を消す
- 「落ちたときの続け方」に、回答を確かめられず質問が残ったときの 1 行（`ndf relay question-done --dir <NDF_RELAY_DIR> --session <session_id> --question-id <tool_use_id>`。`wrapper.json` の待ちの理由に引用済みの形で出るので、それをそのまま打つ）と、質問のファイルが壊れたときの `--broken` の 1 行を載せる。`rm` で消す手順は書かない
- 引継ぎが子の端末への書き込みでなく、同じ会話の起動し直し（`--resume` の引数）で届くことを書く
- 確かめは受け入れ条件 8 の `grep` と `python3 scripts/check-markdown-links.py` で行い、文言のテストは書かない
