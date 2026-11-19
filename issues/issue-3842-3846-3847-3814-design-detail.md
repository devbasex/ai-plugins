# #3842 #3846 #3847 #3814 の詳細設計: 中継の起動の失敗と回答待ちの質問

設計文書の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。設計文書は [issue-3842-3846-3847-3814-design.md](./issue-3842-3846-3847-3814-design.md) にある。パスは `plugins/ndf/` からの相対で書く（`docs/` の下はリポジトリの根からの相対）。

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。新設のファイルは無い）:

```text
plugins/ndf/
├── src/ndf/domains/relay/
│   ├── wrapper.py
│   ├── switch_decide.py
│   ├── signals.py
│   └── observe.py
├── skills/development-workflow/references/
│   ├── relay.md
│   └── relay-accounts.md
└── tests/domains/relay/
    ├── test_relay_wrapper.py
    ├── test_relay_decide.py
    └── test_relay_parts.py
docs/specifications/
├── ndf-relay-segment-restart.md
├── ndf-development-loop-limits.md
├── ndf-relay-multi-session-supervisor.md
├── ndf-relay-lane-parent-session-env.md
├── ndf-relay-auth-failure.md
├── ndf-relay-install-and-restart.md
└── ndf-relay-launch-target.md
```

依存の向きは今のまま一方向である。`wrapper` が `switch_decide`・`signals`・`observe`・`markers` を呼び、`switch_decide` は何も読まない（純粋な関数）。`observe` は `signals` と `ndf.adapters.agents` を呼ぶ。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `src/ndf/domains/relay/wrapper.py` | 変更 | 起動の前に作業ディレクトリを確かめて起動の失敗を `Ended` で返し、質問のファイルを回答が済むまで残す | `switch_decide`・`signals`・`observe`・`markers` |
| `src/ndf/domains/relay/switch_decide.py` | 変更 | 回した回答待ちの質問があれば `wait` を返す | 無し |
| `src/ndf/domains/relay/signals.py` | 変更 | 質問のファイルを読む関数を足す | 無し（同じモジュールの `_read`） |
| `src/ndf/domains/relay/observe.py` | 変更 | 会話の記録の末尾に回答の `tool_result` があるかを返す関数を足す | `ndf.adapters.agents` |
| `skills/development-workflow/references/relay.md` | 変更 | 「承認ゲートを越えない守り」を 11 系へ書き直し、「落ちたときの続け方」に起動の失敗の行を足す | 無し |
| `skills/development-workflow/references/relay-accounts.md` | 変更 | 記録の行とキーの記述を 11 系の `log.jsonl` に合わせる | 無し |
| `tests/domains/relay/test_relay_wrapper.py` | 変更 | 起動の失敗と回答待ちの質問の振る舞いのテストを足す | `wrapper` |
| `tests/domains/relay/test_relay_decide.py` | 変更 | `question_forwarded` の判断のテストを足す | `switch_decide` |
| `tests/domains/relay/test_relay_parts.py` | 変更 | `read_question` と `observe.answered` のテストを足す | `signals`・`observe` |
| `docs/specifications/` の 7 本 | 変更 | 入れ替え・`update-failed`・`start-failed` の記述を消すか 11 系へ書き直す | 無し |

## クラスと関数の構造

型の関係は設計文書の「構造」のセクションのクラス図を指す。

| 保存先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `src/ndf/domains/relay/wrapper.py` | モジュール | `spawn_pty`（変更）・`_enter`（新設）・`_aware_time`（新設） |
| `src/ndf/domains/relay/wrapper.py` | `Section` | フィールド `forwarded_question`・`pending`・`unconfirmed`・`unrecorded` を足す（新設） |
| `src/ndf/domains/relay/wrapper.py` | `Wrapper` | `_start`（変更）・`_loop`（変更）・`_on_switch`（変更）・`_on_resume`（変更）・`_on_handover`（変更）・`_state`（変更）・`_question`（新設）・`_on_wait_like`（変更） |
| `src/ndf/domains/relay/switch_decide.py` | `SectionState` | フィールド `question_forwarded`・`question_id` を足す（新設） |
| `src/ndf/domains/relay/switch_decide.py` | モジュール | `decide`（変更） |
| `src/ndf/domains/relay/signals.py` | モジュール | `read_question`・`question_mark`・`question_clear`・`drop_question`（新設） |
| `src/ndf/domains/relay/observe.py` | モジュール | `answered`・`open_question`（新設） |
| `src/ndf/domains/relay/cli.py` | モジュール | `_question_done` とサブコマンド `question-done`（新設） |

## 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `spawn_pty` | `wrapper.py` | 変更 | 変わらない | 擬似端末で子を起動し、作業ディレクトリへは子の `preexec_fn` で入る | — | `PtyProcess.spawn` へ `cwd=None` と `preexec_fn=functools.partial(_enter, cwd)` を渡す。子が入れなければ親で `OSError`（型は子の `chdir` の例外のまま） | 擬似端末の起動 |
| `_enter` | `wrapper.py` | 新設 | `(cwd: Path) -> None` | fork した子で `os.chdir(cwd)` し、`OSError` を `type(exc)(NO_ENTER.format(cwd=cwd, why=exc.strerror))` で上げ直す | fork した子の中だけで呼ぶ | 入れたら今の作業ディレクトリが `cwd` | `chdir` だけ（ロックも入出力もしない） |
| `_aware_time` | `wrapper.py` | 新設 | `(value: object) -> Optional[datetime]` | 完了の印に使う時刻を読む。使えなければ None | — | 戻り値はタイムゾーンを持つ `datetime` か None。例外を出さない | 無し |
| `Wrapper._start` | `wrapper.py` | 変更 | `(self, req: Launch) -> Optional[Ended]` | 子を起動し、起動できなければ理由を持つ `Ended` を返す | 前の子は止めてある | 起動したら `self.section` が新しい子を持ち `running` が書かれて None。起動できなければ `self.section` は前のまま、`Ended("precondition", <理由>)` を返し、このとき立てたマーカーは消してある | 擬似端末の起動・`wrapper.json`・`log.jsonl`・マーカーのファイル |
| `Wrapper._loop` | `wrapper.py` | 変更 | 変わらない | 最初の `_start` が `Ended` を返したら `_end` して返す | — | — | 今と同じ |
| `Wrapper._on_switch` | `wrapper.py` | 変更 | 変わらない | 錠の前に `account_decide.before_limit` でアカウントを選び（記録の行は書かない）、`signals.question_clear` の錠の中では `in_time()` が真なら子へ `kill(SIGKILL)` だけを送り、錠の外で `account` の行・マーカーの削除・`_stop_child`（回収）・`_before_start`・`_start` を行い、`_start` の `Ended` をそのまま返す | — | 錠を取れない・質問がある・`in_time()` が偽なら、何もせず None（マーカー・子・`account` の行はそのまま） | 今と同じ |
| `Wrapper._on_resume` | `wrapper.py` | 変更 | 変わらない | `signals.question_clear` の錠の中では `in_time()` が真なら `kill(SIGKILL)` だけを送り、錠の外で起動の回数を数え、`_stop_child`・`_before_start`・`_start` を呼ぶ | — | 錠を取れない・質問がある・`in_time()` が偽なら、何もせず None（起動の回数を数えない） | 今と同じ |
| `Wrapper._on_handover` | `wrapper.py` | 変更 | 変わらない | 錠の前にマーカーを立て（`sec.handover_rest` が残っていれば立て直さない）、`signals.question_clear` の錠の中で、書く残り（`sec.handover_rest`、無ければ引継ぎの文と `\r` の全体）を `O_NONBLOCK` の `os.write` で書く。戻り値が残りの長さ未満なら、`in_time()` が真で `EAGAIN` でない間だけ続きを書き直す | — | 末尾の `\r` まで書けたら `sec.handover_rest = None` にして `handover_asked` を立てる。1 バイトも書かずに終えた（錠を取れない・質問がある・`in_time()` が偽・最初の `os.write` が `EAGAIN`）なら、立てたマーカーを `markers.take` で消し、`handover_asked` も立てない。途中まで書いた（短い書き込みの後に `EAGAIN` か `in_time()` が偽）なら、書けなかった残りのバイト列を `sec.handover_rest` に残し、マーカーも残し、`handover_asked` は立てない。端末に入った前半は `\r` が無いので送信されず入力欄に残り、次の見回りの `_on_handover` が同じ `question_clear` の確かめの後に残りだけを書き足す（前半を消して書き直さない） | 子の端末へ書く（今と同じ） |
| `ndf relay question-done`（`cli._question_done`） | `cli.py` | 新設 | `--dir PATH --session ID --mark MARK` | 手での解除。`signals.drop_question(dir, session, mark)` を呼び、印が一致するときだけ消す | 印は `question_unconfirmed` の理由の 1 行が示した値 | 消したか既に無ければ終了コード 0。消さなければ `ndf-relay:` の 1 行と 1 | `question.json` の削除 |
| `observe.open_question` | `observe.py` | 新設 | `(path: Path, size: int) -> Optional[str]` | 会話の記録の末尾 `size` バイトの行で、`assistant` の行の `message.content[]` の `type: tool_use`・`name: AskUserQuestion` の要素のうち、後の `user` の行に同じ `tool_use_id` の `tool_result` が無いものの id（最後のもの） | — | 無ければ None。記録が無いか読めなければ None | 無し（読むだけ） |
| `Wrapper._question` | `wrapper.py` | 新設 | `(self, sec: Section, nxt: Optional[dict]) -> tuple[bool, bool, Optional[str]]` | 質問のファイルを読み、消してよければ消し、（回答待ちか, 回したか, 印）を返す | `nxt` は同じ見回りで読んだマーカー（`_next_mark` の結果） | 消す条件（下の「処理の手順」）に当たり `drop_question` が消せたら `(False, False, None)`。消せなかった（置き換わっていた）ら、その見回りは `(False, False, None)` で次の見回りに読み直す。当たらなければファイルは残り、`sec.pending` が読んだ中身 | `question.json` の削除・`log.jsonl` |
| `Wrapper._state` | `wrapper.py` | 変更 | 変わらない | `question` を `has_question` から `_question` の結果へ替え、`question_forwarded` と `question_id` を渡す | — | — | 今と同じ＋`_question` の副作用 |
| `Wrapper._on_wait_like` | `wrapper.py` | 変更 | 変わらない | `forward` のとき、`sec.pending` の写しを作業ディレクトリの一時のファイル（`.question.forward.json`、`replace_json`）へ書いて `ask_plan` へ渡し、終えたら一時のファイルを消す。質問のファイルは消さず、`sec.forwarded_question` へ `sec.pending` の印を残す | `sec.pending` がある | `sec.forwarded_question` が印、質問のファイルは残る | 計画のリクエスト（今と同じ）・一時のファイル |
| `switch_decide.decide` | `switch_decide.py` | 変更 | 変わらない | `question` が真のとき、`question_forwarded` が偽なら `forward`、真なら `wait` を返す | — | 同じ状態に同じ手 | 無し |
| `signals.read_question` | `signals.py` | 新設 | `(directory: Path) -> Optional[dict[str, Any]]` | 質問のファイルの中身を返す | — | 無ければ None | 無し（読むだけ） |
| `signals.question_mark` | `signals.py` | 新設 | `(question: Mapping[str, Any]) -> Optional[str]` | 質問の印（`tool_use_id` が文字列ならそれ、無ければ `at` が文字列ならそれ、どちらも無ければ None） | — | — | 無し |
| `signals.question_clear` | `signals.py` | 新設 | `(directory: Path, wait: float, released_at: Optional[float]) -> ContextManager[Optional[Callable[[], bool]]]` | 錠を取る前に、`released_at`（ラッパーが前に錠を解いた `time.monotonic()`）から `wait` 以上たっているかを確かめ、たっていなければ錠を取りにいかずに None を渡す。たっていれば錠 `question.json.lock` を待たずに取り（`held(…, 0)`）、取れて質問のファイルが無ければ `in_time()`（取ってからの経過が `wait / 2` 未満なら真）を渡す。取れない・質問のファイルがあれば None を渡す。取れたときは抜けるまで錠を持つ | `wait` は `hook.ask-forward` の秒（hook と同じ値を読む） | 抜けたら錠は解けている。錠を取れたときだけ、呼び出し元は抜けた時刻を `Wrapper._released_at` へ残す。待ちの前で戻ったときと錠を取れなかったときは `_released_at` を変えない（取り直して待ちを延ばし続けない） | 錠のファイル |
| `signals.drop_question` | `signals.py` | 新設 | `(directory: Path, session_id: Optional[str], mark: Optional[str]) -> bool` | 錠 `question.json.lock` を待たずに取り、読み直した質問の `session_id` と `question_mark` が渡した値と同じときだけ消す | — | 消したか、既に無ければ真。錠を取れない・置き換わっていた・壊れていたら偽でファイルは残る | `question.json` の削除 |
| `observe.answered` | `observe.py` | 新設 | `(path: Path, tool_use_id: str, size: int) -> Optional[bool]` | 会話の記録の末尾 `size` バイトの行のうち、`type` が `user` で `message.content` が配列の行に、`type` が `tool_result` で `tool_use_id` が等しい要素があれば真 | — | 記録のファイルが無いか読めない（`OSError`）なら None。読めた行に無ければ偽。`json.loads` できない行・形の違う行は飛ばす | 無し（読むだけ） |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `Wrapper._start` | 作業ディレクトリが無い | ワークツリーの片付け・削除 | `self.s.cwd.is_dir()` が偽 | 止める（`spawn` を呼ばない） | `ndf-relay: 子の claude を起動できない（作業ディレクトリ <cwd> が無い）`、終了コード 3 | 何も残らない。ディレクトリを戻して `claude` を打ち直す |
| `Wrapper._start` | `spawn` が `OSError` | `claude` が PATH に無い・実行できない・擬似端末を開けない | `spawn` の呼び出し | 止める | `ndf-relay: 子の claude を起動できない（<例外の文>）`、終了コード 3 | 立てたマーカーを `markers.take` で消す。原因を直して打ち直す |
| `spawn_pty`（子の `_enter`） | 子が作業ディレクトリへ入れない | `is_dir()` の後に消えた・検索の権限が無い・ディレクトリでなくなった | 子の `os.chdir` の `OSError`。ptyprocess が型と文を親へ返し、親の `spawn` が同じ型で上げる | 止める（上の `OSError` と同じ経路） | `ndf-relay: 子の claude を起動できない（作業ディレクトリ <cwd> に入れない: <strerror>）`、終了コード 3。`log.jsonl` の最後は `end`（`cause: precondition`）、`wrapper.json` は `stopped` | 子は claude を実行せずに終わっている（ptyprocess が `os._exit(1)` し、親が回収する）。立てたマーカーを消す。ディレクトリか権限を直して打ち直す |
| `Wrapper._question` | 質問のファイルが壊れている | 書きかけ・手での編集 | `signals.read_question` の `SignalError` | 呼び出し元へ返す（`_loop` が今と同じく `config_broken` で止める） | `ndf-relay: シグナルファイルが壊れている（…）`、終了コード 2 | 今のマーカーの壊れと同じ |
| `Wrapper._question` | 質問の `at` か マーカーの `written_at` を時刻として使えない | キーが無い・null・文字列でない（`read_next` が保証するのは `first_written_at` と `block` だけ）・ISO の形でない・タイムゾーンが無い | `_aware_time(value)`（下の「処理の手順」）が None を返す。`nxt["written_at"]` の添字では読まない（`KeyError`）。文字列かを先に確かめる（`TypeError`）。タイムゾーンの無い時刻はある時刻と比べない（`TypeError`） | マーカーの書き直しを完了の印として使わず、質問を残す（会話の記録・子の終わり・手での解除で消す）。例外を見回りへ漏らさない | 無し | 次の見回りで読み直す |
| `observe.answered` | 会話の記録が無い・読めない | 保存先の違い・権限 | `path.is_file()` が偽・`agents.tail` の `OSError` | None を返す。`_question` は質問を残し、`sec.unconfirmed` が偽なら `question_unconfirmed` の行を 1 つ書いて立て、`waiting` の理由へ解除の手順を書く | `wrapper.json` の `reason` が `QUESTION_UNCONFIRMED`（記録のパスと `ndf relay question-done --dir <NDF_RELAY_DIR> --session <session_id> --mark <tool_use_id>`）。終了コードは無い（動き続ける） | 次の見回りで読み直す。人がその 1 行を打つと、印が一致するときだけ質問が消えて待ちが解ける |
| `ndf relay question-done` | 印が一致しない | 回答済みと確かめた後に hook が別の質問を置いた | `signals.drop_question` が偽で、読み直した質問の印が渡した印と違う | 消さない | `ndf-relay: 質問が置き換わっている（今の印 <印>）。消さない`、終了コード 1 | 新しい質問は次の見回りで回る |
| `ndf relay question-done` | 錠を取れない | hook が書いている | `signals.drop_question` が偽で、読み直した質問の印が同じか無い | 消さない | `ndf-relay: 質問のファイルの錠を取れない。打ち直す`、終了コード 1 | 打ち直せる |
| `observe.open_question` | 会話の記録が無い・読めない | 保存先の違い・権限 | `OSError` | None を返す（I9 の時間の縛りだけが守る） | 無し | 次の見回りで読み直す |
| `observe.answered` | 行が読めない・形が違う | 書き途中で末尾が切れた・別の種類の行 | `json.loads` の `ValueError`・`dict` でない・`message.content` が配列でない | その行を飛ばす | 無し | 次の見回りで読み直す |
| `Wrapper._on_wait_like` | 計画のリクエストへ載せられない | `ndf lanes question` の失敗 | `ask_plan` の戻り値 | 今と同じく理由を状態と `log.jsonl` に書く。印は残す（同じ質問を回し直さない） | 状態 `waiting` の理由 | 回し直さない |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `Wrapper._question` | 質問のファイルが無い（`ndf relay question-done` で解除したときも）・子が生きていて `observe.open_question` が id を返す | `sec.pending = None`。`sec.unrecorded` が id と違えば `log.jsonl` に `{"kind": "unrecorded_question", "reason": …}` を書いて id を残す。`(True, True, id)`（回さず `wait`） |
| `Wrapper._question` | 質問のファイルが無い・`observe.open_question` が None | `sec.forwarded_question = None`・`sec.pending = None`・`sec.unconfirmed = False`・`sec.unrecorded = None`、`(False, False, None)` |
| `Wrapper._question` | ファイルの `session_id` が `sec.session_id` と違う | `drop_question` で消し、消せたら `log.jsonl` に `{"kind": "stale_question", "reason": …}`、`(False, False, None)` |
| `Wrapper._question` | 子が生きていない | `drop_question` で消す、`(False, False, None)` |
| `Wrapper._question` | `tool_use_id` があり `observe.answered` が真 | `drop_question` で消し、消せたら `log.jsonl` に `{"kind": "question_answered", "reason": "会話の記録に回答がある"}`、`(False, False, None)` |
| `Wrapper._question` | `_aware_time(nxt.get("written_at"))` と `_aware_time(質問の at)` がどちらも時刻で、前が後より後 | `drop_question` で消し、消せたら `log.jsonl` に `{"kind": "question_answered", "reason": "質問の後に Stop がマーカーを書き直した"}`、`(False, False, None)` |
| `Wrapper._question` | 上のどれにも当たらない | `sec.pending` に読んだ中身を置き、`(True, sec.forwarded_question == 印, 印)`。印は `signals.question_mark`。`observe.answered` が None で `sec.forwarded_question == 印` なら、上の異常系の `question_unconfirmed` を打つ |
| `Wrapper._question` | `drop_question` が偽（錠を取れない・置き換わっていた） | ファイルは残る。その見回りは `(False, False, None)`。`switch` などが出ても手の中の `question_clear` が質問を見て打たない。次の見回りで読み直し、印が違えば新しい質問として回す |
| `_aware_time` | 値が文字列でない・`fromisoformat` が `ValueError`・`tzinfo` が None | None |
| `_aware_time` | タイムゾーンのある ISO の時刻 | `datetime` |
| `Wrapper._on_switch`・`_on_resume`・`_on_handover` | `question_clear` が偽 | 何もせず None。`log.jsonl` にも書かない（次の見回りの `forward` の行が残る） |
| `switch_decide.decide` | `question` 真・`question_forwarded` 偽 | `Step("forward", FORWARD.format(id=state.question_id))`。`question_id` が None なら今と同じ文 |
| `switch_decide.decide` | `question` 真・`question_forwarded` 真 | `Step("wait", "回答待ちの質問がある。子を止めず、子の端末へ書かない")` |
| `switch_decide.decide` | `question` 偽 | 今と同じ |

- 繰り返しの終わる条件: 見回りの繰り返し（`_loop`）は今と同じ。回答待ちの間は `wait` が続き、上の消す条件のどれかで抜ける
- 計算量の注意: `observe.answered` を呼ぶのは、回答待ちの質問が `tool_use_id` を持つ見回りだけである。読むのは会話の記録の末尾 `relay.transcript-tail` バイトで、文脈の量の読みと同じ量である

## 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `LAUNCH_FAILED` | 定数（理由の文の形 `子の claude を起動できない（{why}）`） | `wrapper.py` | — |
| `NO_CWD` | 定数（`作業ディレクトリ {cwd} が無い`） | `wrapper.py` | — |
| `NO_ENTER` | 定数（`作業ディレクトリ {cwd} に入れない: {why}`） | `wrapper.py` | — |
| `QUESTION_UNCONFIRMED` | 定数（`会話の記録 {path} を読めず、質問 {id} の回答を確かめられない。答えたなら ndf relay question-done --dir {dir} --session {session} --mark {id} を打つと続く（ほかの質問は消さない）`） | `wrapper.py` | — |
| `Section.handover_rest` | 型のフィールド `Optional[bytes]`（`_on_handover` が短い書き込みで書けなかった残り。子が替わると Section ごと捨てる） | `wrapper.py` | None |
| `Section.unrecorded` | 型のフィールド `Optional[str]`（`unrecorded_question` を書いた質問の id） | `wrapper.py` | None |
| `Wrapper._released_at` | 型のフィールド `Optional[float]`（ラッパーが前に `question.json.lock` を解いた `time.monotonic()`。`drop_question` と `question_clear` が錠を取れたときだけ、解いた後に残す。錠を取りにいかなかった・取れなかったときは変えない） | `wrapper.py` | None |
| `FORWARD_FILE` | 定数（`forward` で計画のリクエストへ渡す写しの名前 `.question.forward.json`） | `wrapper.py` | — |
| `QUESTION_PENDING` | 定数（`wait` の理由の文） | `switch_decide.py` | — |
| `FORWARD` | 定数（`AskUserQuestion の質問 {id} を人かオーガナイザーへ回す（hook は待たない）`） | `switch_decide.py` | — |
| `Section.forwarded_question` | 型のフィールド `Optional[str]` | `wrapper.py` | None |
| `Section.pending` | 型のフィールド `Optional[dict]`（この見回りで読んだ質問） | `wrapper.py` | None |
| `Section.unconfirmed` | 型のフィールド `bool`（`question_unconfirmed` を書いたか） | `wrapper.py` | False |
| `SectionState.question_forwarded` | 型のフィールド `bool` | `switch_decide.py` | False |
| `SectionState.question_id` | 型のフィールド `Optional[str]`（今の質問の印） | `switch_decide.py` | None |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 質問の hook（`ask-forward`）とラッパーの読み | `question.json` | hook は `signals.write_json` の錠（`question.json.lock`、待ちは最大 0.3 秒）の中で `replace_json` で置き換える。置き換えは原子的なので、ラッパーは錠を取らずに読んでも書きかけを読まない | 読んだ後に置き換わっても、印で別の質問と分かる |
| 質問の hook とラッパーの消す手 | `question.json` | `signals.drop_question` が同じ錠を待たずに取り、錠の中で読み直して `session_id` と印が同じときだけ消す。順序の例: ラッパーが回答済みの A を読む → hook が B へ置き換える → ラッパーが錠を取って読み直すと B → 消さない | 錠を取れない・置き換わっていたら消さず、次の見回りで読み直す。消した後に hook が書いた質問は、次の見回りで新しい質問として回す |
| 質問の hook とラッパーの子を止める手・子の端末へ書く手 | `question.json`・子のプロセスと端末 | `signals.question_clear` が、前に解いてから hook の待ち（0.3 秒）以上たっているかを錠を取る前に確かめ、たっていれば同じ錠を待たずに取り、錠の中で質問のファイルが無いと確かめ、取ってから hook の待ちの半分未満の間だけ、錠を持ったまま `kill(SIGKILL)` か `O_NONBLOCK` の `os.write`（短い書き込みの続きを含む）だけを打つ。書き切れなかった残りは `sec.handover_rest` に残し、次の見回りで同じ確かめの後に書き足す。回収（`terminate(force=True)`）・マーカー・記録の行は錠の外。hook が先に錠を持てば手を打たない | 打たなかった手は、次の見回りの判断で決め直す。hook が待ちを越えて返るより先に手が終わる（設計文書「判定の後に質問が出る競合」）。hook が記録できなかった質問は `observe.open_question` で見つけて `wait` にする |
| `ndf relay question-done` とラッパー・hook | `question.json` | `signals.drop_question` の錠の中で印を比べてから消す | 印が違えば消さない。打ち直せる |
| Stop の hook（`relay-marker`）とラッパー | `next.json` | 今と同じ | 今と同じ |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `Wrapper._start` → `_end` | `end` の行（`cause: precondition`・`reason`）・`wrapper.json` の `stopped` | `log.jsonl`・`wrapper.json` | 環境変数の値 |
| `cli._ended`（変えない） | `ndf-relay: <理由>` | 標準エラー | — |
| `Wrapper._question` | `stale_question`・`question_answered`・`question_unconfirmed`（`path` に会話の記録のパス）の行 | `log.jsonl` | 質問の本文と選択肢 |
| `Wrapper._note`（変えない） | `forward` の行。理由の文に質問の印が入るので、続けて来た別の質問の行も省かない | `log.jsonl` | 質問の本文と選択肢 |
| `Wrapper._account_for_next`・`_note`（変えない） | `account` の行 `{"at", "section", "kind": "account", "reason"}`。`at` と `section` はどの行にも付く共通のキー、固有のキーは `kind` と `reason` | `log.jsonl` | — |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `signals.read_question` | `signals._read`（壊れたら `SignalError`、無ければ None） | `grep -n "def _read" src/ndf/domains/relay/signals.py` | 使う（`read_next` と同じ形） |
| `observe.answered` | `ndf.adapters.agents.tail`・`observe.context_tokens` の読み方 | `grep -n "def tail" src/ndf/adapters/agents.py` | `tail` を使う。`context_tokens` は量だけを返すので別の関数にする |
| `Wrapper._question` | `Wrapper._next_mark`（前の実行のマーカーを消す形） | `wrapper.py` を読む | 同じ形（読み、違えば消して 1 行）に揃える |
| `signals.question_clear`・`drop_question` | `signals.held`（錠のファイルの排他。`wait` 0 で待たない）・hook の `write_json` が使う錠の名前 `<path>.lock` | `grep -n "def held\|def write_json" src/ndf/domains/relay/signals.py` | 使う（hook と同じ錠の名前を同じモジュールで作る） |
| `spawn_pty` の `chdir` | ptyprocess の `preexec_fn`（例外の型と文を親へ返す） | `inspect.getsource(PtyProcess.spawn)` と打って確かめた（設計文書の根拠の表） | 使う |
| `_aware_time` | `Wrapper._age`（`first_written_at` を読めなければ `SignalError`） | `wrapper.py` を読む | 使わない。`written_at` と `at` は完了の印に使うかの判断だけで、読めなくても止めないため、例外にせず None を返す別の関数にする |
| 起動の失敗の後始末 | `markers.take` | `grep -n "def take" src/ndf/domains/relay/markers.py` | 使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `Wrapper._start` | 在る作業ディレクトリで起動する（今と同じ引数） | 最初・`switch`・`restart` の 3 つの手 | 無い作業ディレクトリで `spawn` を呼ばない。`spawn` の `FileNotFoundError` で `precondition` とマーカーの削除 |
| `spawn_pty` | 本物の擬似端末で子の `pwd` が作業ディレクトリ | `is_dir()` の後・`spawn_pty` の前にディレクトリを消すと `FileNotFoundError` | 検索の権限の無いディレクトリ（0o600。root では飛ばす）で `PermissionError`。どちらも `Wrapper.run` が `precondition`・`end` の行・`stopped` で終わり、`spawn` は 1 回 |
| `Wrapper._question` | 回した質問が回答まで残る | 印が `tool_use_id` に無く `at` だけ | セッションが違う・子が終わった・回答の行・質問の後のマーカーで消える。質問の前のマーカーでは消えない。`written_at` が無い・null・数・タイムゾーンの無い時刻・読めない文字列では例外を出さず残る |
| `Wrapper._question` | 会話の記録が無いまま回した質問で `question_unconfirmed` の行が 1 つ、`waiting` の理由に解除の手順 | 人が `question.json` を消すと次の見回りで今と同じ判断（マーカーがあれば `switch`） | — |
| `Wrapper` の見回り | 質問 A を回した後、A の回答と質問 B が同じ見回りの間に出ると、`forward` の行が A・B で 1 つずつ | 同じ質問では `forward` の行が 1 つ | — |
| `Wrapper` の見回りと手 | — | `_state` の直後に hook の `write_question` で質問を書くと、`switch`・`account`・`restart` で子が生き `terminate` が呼ばれず、`handover` で `written` が空 | 別のスレッドが `question.json.lock` を持つ間は手を打たず、解けた後に打つ |
| `switch_decide.decide` | 回した質問で `wait`。回していない質問の理由に印が入る | `question_forwarded`・`question_id` の無い JSON（`ndf relay decide`）で今と同じ文の `forward` | — |
| `signals.read_question` | 中身を返す | 無ければ None | 壊れていれば `SignalError` |
| `signals.drop_question` | 同じ質問なら消して真 | 既に無ければ真 | 読み直すと別の質問（A を渡し B が置いてある）なら消さず偽。錠を別のスレッドが持てば消さず偽 |
| `signals.question_clear` | 質問が無く錠を取れたら `in_time` を渡す | 前に解いてから `wait` 未満なら None。偽の時計で `wait / 2` を越えると `in_time()` が偽 | 錠を別のスレッドが持つ・質問のファイルがあれば None |
| `observe.open_question` | `tool_result` の無い `AskUserQuestion` の `tool_use` の id | 記録が無いと None | `tool_result` のある `tool_use`・別の Tool の `tool_use`・`user` の行の要素では None |
| `cli._question_done` | 印が一致すれば消して 0 | 既に無ければ 0 | 印が違えば消さず、今の印を含む `ndf-relay:` の 1 行と 1 |
| `observe.answered` | 実際の形の行（`{"type": "user", "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_A", "content": "Your questions have been answered: …"}]}, "toolUseResult": {…}, "sessionId": …}`）で真。フィクスチャは手元の記録から写した行の形を使う | 記録のファイルが無いと None。読めた行に無ければ偽 | 別の質問（`toolu_X`）の `tool_result`・`assistant` の行の `tool_use`（`id` が `toolu_A`）・最上位にだけ `tool_use_id` がある行では偽。末尾で切れた行は読み飛ばす |
