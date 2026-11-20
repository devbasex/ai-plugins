# #3814 の詳細設計: 回答待ちの質問のあいだ子を止めず、子の端末へ書かない

設計書（[issue-3814-design.md](./issue-3814-design.md)）の「構成要素」を、ファイルと関数の粒度へ下ろした文書である。決定・不変条件（I1〜I7）・受け入れ条件の番号は、設計書と要求仕様（[issue-3814-requirements.md](./issue-3814-requirements.md)）の番号を指す。パスは `plugins/ndf/` からの相対で書く。新設する関数が 2 つ以上あるので、設計書に畳まずにこのファイルに置く。

## 地図

```text
plugins/ndf/
├── src/ndf/domains/relay/
│   ├── wrapper.py         ポーリングで質問を読み・古い質問を消し、ロックの中で確かめてから子を止める
│   ├── switch_decide.py   転送した回答待ちの質問で wait を返す
│   └── signals.py         質問のファイルを読む・起動の ID を書く・ロックの中で消す
├── src/ndf/domains/hooks/
│   ├── dispatch.py        fail_closed の guard を締め切り・例外・拒否の数えから外す
│   └── guards/
│       ├── ask.py         書けなければ AskUserQuestion を拒否する
│       └── ask_clear.py   （新設）質問が閉じたら質問のファイルを消す
├── contracts/
│   ├── hooks/guards.json  列 fail_closed と、ask-answered・ask-closed の行
│   └── limits.json        relay.child-exit（SIGKILL の後に子の終わりを待つ上限）
├── skills/development-workflow/references/
│   └── relay.md           承認ゲートを越えない守りを 11 系へ書き直す
└── tests/domains/
    ├── relay/             test_relay_wrapper.py・test_relay_decide.py・test_relay_parts.py
    └── hooks/             ask-forward・ask-answered・ask-closed・fail_closed のテスト
```

新設のファイルは `hooks/guards/ask_clear.py` だけである。依存の向きは今のまま一方向である。`wrapper` が `switch_decide`・`signals` を呼び、`switch_decide` は何も読まない（純粋な関数）。hook の guard は `ndf.domains.relay.signals` を呼ぶ（`ask.py` が今そうしている）。

| 構成要素 | 保存先 | 満たす決定 |
| --- | --- | --- |
| [判定](#判定switch_decide) | `switch_decide.py` | 決定 1 |
| [質問のファイルの読みと書き](#質問のファイルの読みと書きsignalsread_questionquestion_idwrite_question) | `signals.py` | 決定 1・2 |
| [ロックの中で消す](#ロックの中で消すsignalsdrop_question) | `signals.py` | 決定 2・3 |
| [質問の hook](#質問の-hookask-forward) | `hooks/guards/ask.py` | 決定 2 |
| [質問を消す hook](#質問を消す-hookask-answeredask-closed) | `hooks/guards/ask_clear.py`・`guards.json` | 決定 3・5 |
| [失敗したら止める側へ倒す guard](#失敗したら止める側へ倒す-guarddispatchrunfail_closed) | `hooks/dispatch.py`・`guards.json` | 決定 2 |
| [ロックの中で子を止める](#ロックの中で子を止めるwrapper_stop_if_clear) | `wrapper.py` | 決定 2・5 |
| [ポーリングの質問の扱い](#ポーリングの質問の扱いwrapper_question_state) | `wrapper.py` | 決定 1・3・5 |
| [質問を転送する操作](#質問を転送する操作wrapper_on_wait_like) | `wrapper.py` | 決定 1 |
| [子を止める操作](#子を止める操作wrapper_on_switch_on_resume_on_handover_start) | `wrapper.py` | 決定 2 |
| [例外の経路](#例外の経路wrapper_loop) | `wrapper.py` | 決定 5 |
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

## 質問のファイルの読みと書き（`signals.read_question`・`question_id`・`write_question`）

満たす決定: 決定 1・2、受け入れ条件 4・5・6

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `read_question` | `signals.py` | 新設 | `(directory: Path) -> Optional[dict[str, Any]]` | 質問のファイルの中身を返す。無ければ None。読むだけ。読めない失敗はすべて `SignalError` にそろえる（下の境界） |
| `question_present` | `signals.py` | 新設 | `(directory: Path) -> bool` | 質問のファイルが在るか。`os.lstat` が `FileNotFoundError` なら偽、成功かほかの `OSError`（権限・I/O）なら真（読めなくても在る側へ倒す）。`has_question` の `is_file()` は `OSError` で偽を返すので、ラッパーの止める確かめには使わない |
| `question_id` | `signals.py` | 新設 | `(question: Mapping[str, Any]) -> Optional[str]` | 質問の ID。`tool_use_id` が文字列ならそれ、無ければ `at` が文字列ならそれ、どちらも無ければ None |
| `write_question` | `signals.py` | 変更 | `(path: Path, session_id: str, payload: Mapping[str, Any], wait: float, launch: Optional[str] = None) -> bool` | 今の項目に `launch`（起動の ID）を足して書く。ロックの中で `current_launch` を読み、`launch` と同じ文字列のときだけ書く。違う・None・今の起動が無いなら書かずに偽 |
| `write_json` | `signals.py` | 変更 | 引数 `allow: Optional[Callable[[], bool]] = None` を足す | ロックを取った後、書く前に `allow` を呼び、偽なら書かずに偽を返す |
| `set_launch` | `signals.py` | 新設 | `(directory: Path, launch: Optional[str]) -> None` | 今の起動の ID を `launch.json`（`LAUNCH_FILE`）へ `replace_json` で書く。None は「今の起動が無い」。None は `question.json.lock` を持って呼ぶ（止めた子の hook がロックを取る前に外す）。新しい ID はロックなしでよい（止めた子の hook の ID と一致しないので、どの順でも書かせない） |
| `current_launch` | `signals.py` | 新設 | `(directory: Path) -> Optional[str]` | `launch.json` の `launch` が文字列ならそれ。無い・壊れている・読めないなら None（書く側は書かない側へ倒れる） |
| `launch_env` | `signals.py` | 新設 | `(directory: Path, launch: str) -> dict[str, str]` | 子へ渡す環境変数。`dir_env` の `NDF_RELAY_DIR` に `NDF_RELAY_LAUNCH` を足す（`dir_env` は消し、呼び出し元の `Wrapper._start` をこちらへ替える） |
| `relay_launch` | `signals.py` | 新設 | `(env: Mapping[str, str]) -> Optional[str]` | hook の環境の `NDF_RELAY_LAUNCH`。無ければ None |

使う既存の部品: `signals._read`（壊れたら `SignalError`、無ければ None）。`_read` は JSON の誤りと形の誤りだけを `SignalError` にし、`read_text` が出す `FileNotFoundError` 以外の `OSError`（`PermissionError`・`IsADirectoryError`・I/O の誤り）と不正な UTF-8 の `UnicodeDecodeError` はそのまま出す。そこで `read_question` は `_read` を `except (OSError, UnicodeDecodeError)` で包み、`SignalError(f"質問のファイルを読めない（{path}）: {exc}")` で上げ直す（`_read` 自体は `read_next` の振る舞いを変えないよう触らない）。これで呼び出し元が受けるのは None・中身・`SignalError` の 3 つだけになり、`Wrapper._question` の `SignalError` の 1 か所で回答待ちへ倒せる。`drop_question` の読み直しも同じ境界で読み、読めなければ壊れているとみなす

異常系:

| 失敗 | 原因 | 扱い | 利用者に見える出力と終了コード |
| --- | --- | --- | --- |
| 質問のファイルが壊れている | 手動の編集（hook は `replace_json` で置き換えるので書きかけは読まない） | `SignalError` を呼び出し元へ返す。呼び出し元の `Wrapper._question` は回答待ちとして扱い、`_loop` の例外の経路へは渡さない（決定 5） | 終了しない。`log.jsonl` に `question_broken` の行 |
| 質問のファイルを読めない | 権限（`PermissionError`）・ディレクトリになっている（`IsADirectoryError`）・I/O の誤り・不正な UTF-8（`UnicodeDecodeError`） | `read_question` が `SignalError` に変えて返す。以後は壊れているときと同じ（回答待ち・`question_broken`） | 同上 |

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 中身を返す。`launch` を渡し、`set_launch` が同じ ID なら書かれる | 無ければ None。`launch` が None・`launch.json` が無い・壊れているなら書かずに偽。`question_present` は無ければ偽、在れば真 | 壊れていれば `SignalError`。不正な UTF-8 のバイト列・読む権限の無いファイル（0o000。root では飛ばす）・`question.json` という名前のディレクトリでも `SignalError`（`PermissionError`・`UnicodeDecodeError`・`IsADirectoryError` を出さない）。`question_present` は親のディレクトリを辿れない（`os.lstat` が `PermissionError`）とき真。`set_launch` が別の ID なら書かずに偽で、前の中身は変わらない |

## ロックの中で消す（`signals.drop_question`）

満たす決定: 決定 2・3、受け入れ条件 4・6、I5

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `drop_question` | `signals.py` | 新設 | `(directory: Path, match: Optional[Mapping[str, Any]], wait: float, launch: Optional[str] = None) -> str` | ロック `question.json.lock` を `wait` 秒まで待って取り、ロックの中で読み直した質問を、下の条件のときだけ消す。結果は、消した・既に無いなら `"dropped"`、条件に合わなければ `"kept"`、ロックを取れなければ `"busy"`、消せなければ `"failed"` |

消す条件（すべてロックの中で見る）:

1. `launch` を渡したら、`current_launch(directory)` が同じ文字列のときだけ進む。違えば `"kept"`（止めた子の遅れた hook は何も消さない）
2. 読み直した質問が壊れている（`read_question` が `SignalError`）なら消す
3. `match` が None なら消す。`match` があれば、キーごとに質問の同じキーの値と等しいときだけ消す

呼び出し元:

| 呼び出し元 | `wait` | `match` | `launch` |
| --- | --- | --- | --- |
| `ask-answered`（PostToolUse） | `hook.ask-forward` の秒 | `{"tool_use_id": 入力の tool_use_id}` | `signals.relay_launch(ctx.env)`（None なら呼ばない） |
| `ask-closed`（Stop・UserPromptSubmit） | 同上 | None | 同上 |
| `Wrapper._question`（子の終わり） | 0 | None | None |
| `Wrapper._question`（古い質問） | 0 | 読んだ質問の `{"tool_use_id": …, "at": …, "launch": …}` | None |

`ask-closed` が質問を選ばずに消してよい理由: 起動の ID が今の記録と同じ hook は今の子のもので、今の子が Stop か UserPromptSubmit を起こした時点では、その子の画面に質問は出ていない（Stop は応答を終えたとき、UserPromptSubmit は質問の表示中には起きない。文脈と制約の実測）。止めた子の hook は 1 で締め出す。`ask-forward` も同じ記録で書き込みを絞るので（`write_question`）、質問のファイルに止めた子の質問が書かれることも無い。

削除の失敗: 消すのは `Path.unlink` だけで、ディレクトリを再帰で消さない。`unlink` が `OSError`（`question.json` がディレクトリの `IsADirectoryError`・権限など）を出したら、例外を呼び出し元へ出さず `"failed"` を返す。

- 事後条件: `"dropped"` なら質問は無い。`"kept"`・`"busy"`・`"failed"` ならファイルは残る
- 副作用: `question.json` の削除
- 呼び出し元の扱い: hook は結果によらず通し、`"busy"`・`"failed"` は計測の `notes` に残す。ファイルが残ると、ラッパーは回答待ちのまま子を止めない（止めない側に倒れる）。残った質問は、次の閉じる事象の hook（回答の PostToolUse の後には、応答を終えたときの Stop が必ず続く。Stop の後は人が次に入力を送ったときの UserPromptSubmit）か、子の終わりで消える。`busy` が起きるのは、ほかの hook が書いている・ラッパーが読み直して消している短い間だけである（ラッパーは質問のファイルが在れば子を止めずにすぐロックを解く）

使う既存の部品: `signals.held`（ロックのファイルの排他）と、hook の `write_json` が使うロックの名前 `<path>.lock`。hook と同じロックの名前を同じモジュールで作る。

並行と排他: hook（`ask-forward`）は `signals.write_json` のロックの中で `replace_json` で置き換える。置き換えは原子的なので、ラッパーはロックを取らずに読んでも書きかけを読まない。消す操作だけはロックの中で読み直す。順序の例: `ask-answered` が質問 A の回答で呼ばれる → その前に同じ子の hook が B へ置き換えていた → ロックの中で読み直すと B で `tool_use_id` が違う → 消さない。

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| `match` に合えば消して `"dropped"`。`match` が None なら消す。壊れたファイルは消す | 既に無ければ `"dropped"`。`launch` が `launch.json` と違う・`launch.json` が無いと消さず `"kept"` | 合わなければ消さず `"kept"`。ロックを別のスレッドが保持していれば消さず `"busy"`。`question.json` がディレクトリのとき例外を出さず `"failed"`、ディレクトリが残り、`Wrapper._question` は回答待ち（`question_broken`）で `terminate` が呼ばれない |

## 質問の hook（`ask-forward`）

満たす決定: 決定 2、受け入れ条件 10、I7

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `hooks/guards/ask.py` | 変更 | 変わらない | 中継の下（`signals.relay_dir` が値を返す）で、`write_question(…, launch=signals.relay_launch(ctx.env))` が偽を返すか `OSError` を出したら `deny(QUESTION_NOT_RECORDED)` を返す。書けたら今と同じくオーガナイザーへの書き込みと待ちの知らせへ進み、`passing` |

- 中継の外では今と同じ（書けなくても通す）。オーガナイザーの質問の保存先と待ちの知らせの失敗は、今と同じく通す（ラッパーの守りに関わらない）
- 書く前に拒否の理由を決めない。書けたかどうかだけで決める
- 今の起動でない hook は書けない。`write_question` がロックの中で `current_launch` と `launch` を照らし、違えば書かずに偽を返す（ラッパーは子を止めたロックの中で今の起動を外し、次の子の起動の前に新しい ID を記録する）。止めた子の残った hook のプロセスがロックを待っていた・遅れて動いたときも、新しい子の質問を上書きしない。その hook の質問は拒否になるが、その子は SIGKILL を受けているので選択肢は出ない

内部のデータと定数:

| 名前 | 種類 | 既定値 |
| --- | --- | --- |
| `QUESTION_NOT_RECORDED` | 定数 `中継が子を止めている途中で、質問を記録できなかった。止まらずに続いていれば、もう一度 AskUserQuestion を呼ぶ` | — |

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 中継の下で書けたら通し、質問のファイルに `launch` が入る | 中継の外ではロックを別のプロセスが持っていても通す（今と同じ） | 中継の下で、ロックを別のプロセスが持つ・`write_question` が `OSError` を出す・今の起動と `NDF_RELAY_LAUNCH` が違うと拒否。PreToolUse の出力が `permissionDecision: deny` |
| 競合（偽の子 2 つ、本物の `ask.check`）: 起動 L1 の子を `switch` で止め、起動 L2 の子の hook が質問 Q2 を書いた後に、止めていた L1 の hook（環境の `NDF_RELAY_LAUNCH=L1`、ロックの待ちの中で止めておいたスレッド）を再開する。L1 の hook は拒否し、`question.json` は Q2 のまま、次のポーリングは Q2 の `forward` で L2 の子が生きている（`terminate` が呼ばれない） | 同じ競合を L1 の hook が L2 の書き込みの前に再開する順でも、L1 の hook は拒否して `question.json` は無いまま | — |

## 質問を消す hook（`ask-answered`・`ask-closed`）

満たす決定: 決定 3・5、受け入れ条件 4、I5

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `check` | `hooks/guards/ask_clear.py` | 新設 | `(ctx, svc) -> Verdict` | 中継の下で、hook の起動の ID（`signals.relay_launch(ctx.env)`）があれば、`ctx.event` が `PostToolUse` なら `match={"tool_use_id": 入力の tool_use_id}`、`Stop`・`UserPromptSubmit` なら `match=None` で `signals.drop_question(…, launch=起動の ID)` を呼ぶ。起動の ID が無ければ何もしない。いつも `passing(cleared=<結果>)` を返す |

guard の表（`contracts/hooks/guards.json`）に 2 行を足す。Tool を持つ行は Tool の無い事象に当たらない（`GuardSpec.applies`）ので、同じモジュールを 2 行で登録する。

| 名前 | `events` | `tools` | `order` | `fail_closed` |
| --- | --- | --- | --- | --- |
| `ask-answered` | `PostToolUse` | `AskUserQuestion` | 60 | 偽 |
| `ask-closed` | `Stop`・`UserPromptSubmit` | 空 | 10（`relay-marker` より前） | 偽 |

- PostToolUse の入力の `tool_use_id` は、ランタイムのコントラクト表（`contracts/hooks/runtimes.json`）の PostToolUse の `optional` に足す（今は `reads` と `optional` に無い）
- 中継の外・質問のファイルが無いときは何もしない（ロックも取らない）
- Codex の行の `skipped` に、2 つとも `ask-forward` と同じ理由で足す

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 実際の入力の形（2026-11-20 の実測の JSON）を `hooks.main` へ通すと、PostToolUse は同じ `tool_use_id` で、Stop と UserPromptSubmit は在る質問を、hook の `NDF_RELAY_LAUNCH` が `launch.json` と同じとき消す | 中継の外・ファイルが無いと何もしない。壊れたファイルはどれでも消える | PostToolUse は別の `tool_use_id` では消えない。hook の `NDF_RELAY_LAUNCH` が `launch.json` と違う（`--resume` で起動し直した後に、前の起動の hook が遅れて動いた）・無いと消えない。ロックを別のプロセスが持つ・`unlink` が `OSError` を出すと消えずに通し、`notes` に `busy`・`failed` が残る |
| 回答の PostToolUse の間だけロックを別のスレッドが持ち（`busy`）、質問が残っても、続く Stop で消え、次のポーリングでマーカーがあれば `switch` | — | — |

## 失敗したら止める側へ倒す guard（`dispatch.run`・`fail_closed`）

満たす決定: 決定 2、受け入れ条件 10、I7

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `GuardSpec.fail_closed` | `hooks/dispatch.py` | 新設 | フィールド `bool`（表の列 `fail_closed`、無ければ偽） | 真の guard を、締め切り・例外・拒否の数えから外す |
| `run` | `hooks/dispatch.py` | 変更 | 変わらない | 下の 3 点を `fail_closed` の guard だけに当てる |
| `_call` | `hooks/dispatch.py` | 変更 | 変わらない | `fail_closed` の guard の例外は `deny(GUARD_FAILED.format(name=…, error=…))` にする |

`run` の変更:

| 今 | `fail_closed` の guard |
| --- | --- |
| 締め切りを過ぎたら後の guard を呼ばずに抜ける | 締め切りを過ぎても、残りの `fail_closed` の guard だけは呼ぶ |
| guard の例外は通す（`PASS`） | 拒否にする |
| 同じ拒否を上限より多く返すか数え手が使えなければ、案内へ下げる | 下げない（`counter.allow_deny` を呼ばない） |

- guard の表の `summary` と、表を読むスキーマ（あれば）に `fail_closed` を書く
- `ask-forward` の行の `fail_closed` を真にする。ほかの行は偽（今と同じ）

内部のデータと定数:

| 名前 | 種類 | 既定値 |
| --- | --- | --- |
| `GUARD_FAILED` | 定数 `guard {name} が失敗したので止める（{error}）` | — |

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| `fail_closed` の guard の拒否を上限より多く繰り返しても拒否のまま。数え手が使えなくても拒否のまま | 先の guard が締め切りを使い切っても `fail_closed` の guard は呼ばれ、ほかの guard は呼ばれない | `fail_closed` の guard が例外を出すと拒否。偽の guard は今と同じ（例外で通す・案内へ下げる） |

## ロックの中で子を止める（`Wrapper._stop_if_clear`）

満たす決定: 決定 2・5、受け入れ条件 1・2・3・7・10、I4

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._stop_if_clear` | `wrapper.py` | 新設 | `(self, sec: Section) -> str` | ロックを待たずに取り、質問のファイルが無ければ、ロックを持ったまま `_stop_child` で子を止める。子の終わりを確かめたら `"stopped"`、取れない・在るなら `"held"`、止めても子が終わらなければ `"alive"` |
| `Wrapper._stop_child` | `wrapper.py` | 変更 | `(self) -> None` から `(self) -> bool` へ | `terminate(force=True)` の戻り値を捨てない。偽（ptyprocess 0.7.0 は SIGKILL の後の 0.1 秒で子が終わっていなければ偽を返す）なら、`relay.tick` の間隔で `isalive()` を見て `relay.child-exit` の秒まで待つ。子が終わったと確かめたときだけ真 |

処理の手順:

1. `with signals.held(<question.json.lock>, 0) as got:` で取る。`got` が偽なら `"held"` を返す
2. `signals.question_present(self.dir)` が真（壊れていても・読めなくても在る）なら `"held"` を返す
3. `self._stop_child()` を呼び、結果に依らず `signals.set_launch(self.dir, None)` で止めた子の起動を今の起動から外す（ロックの中で外すので、止めた子の残った hook は以後書けない）。真なら `"stopped"`、偽なら `"alive"` を返す。ロックは `with` を抜けるときに解く

`"alive"` を受けた呼び出し元（`_on_switch`・`_on_resume`・`_on_handover`・`_halt_broken`）は、次の子を起動せず、`Ended("refused", CHILD_ALIVE.format(pid=…, seconds=…))` を返す（`_loop` の `_end` が `stopped` と `end` の行を書き、終了コードは `refused` の種類のもの）。前の子が生きたまま次の子を起動しないことを、待ち続けるより先に置く。SIGKILL を送った後の子は利用者のコードを実行しないので、選択肢を出すことも質問を書くことも無い。終わらないのは割り込めない待ち（D 状態）にある間だけで、ラッパーが終わった後にカーネルが回収する。

- 事後条件: `"stopped"` なら子は終わっている。`"held"` なら子にも質問のファイルにも何もしていない。`"alive"` なら子へ SIGKILL を送ったが終わりを確かめられず、次の子は起動しない
- 副作用: 子へのシグナル（`terminate(force=True)` の SIGHUP・SIGCONT・SIGINT・SIGKILL）
- 時間: ロックを持つのは `terminate` の最大 0.4 秒と、終わらなければ `relay.child-exit` の秒まで。その間に hook はロックを待ちきれず質問を拒否するが、子は SIGKILL を受けているので選択肢は出ない

内部のデータと定数:

| 名前 | 種類 | 既定値 |
| --- | --- | --- |
| `relay.child-exit` | `contracts/limits.json` の行（SIGKILL の後に子の終わりを待つ上限の秒） | 5 |
| `CHILD_ALIVE` | 定数 `子（pid {pid}）を強制終了しても {seconds} 秒のうちに終わらない。次の子を起動せずに止める` | — |

並行と排他:

- hook（`ask-forward`）が先にロックを取れば、2 で質問のファイルが見えて止めない
- ラッパーが先に取れば、hook はロックを待つ（最大 0.3 秒）。3 が終わってロックを解いた後に hook が書いても、その子は終わっている。待ちを越えれば hook は拒否する。どちらでも選択肢は出ない
- 子が終わった後に残った hook のプロセスは、ロックを取っても 3 で今の起動から外れているので書けず、新しい子の質問を上書きしない（[質問の hook](#質問の-hookask-forward)）。古い `launch` の質問を次の子の最初のポーリングが消す経路（[ポーリングの質問の扱い](#ポーリングの質問の扱いwrapper_question_state)）は、`launch` の無い質問とラッパーの前の実行が残したファイルのために残す
- 時間の制約は無い。ラッパーが 1 と 3 のあいだでスケジューラーに待たされても、hook はロックを待つか拒否するだけである

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 質問が無ければ `terminate` が呼ばれて `"stopped"`。偽の子の `terminate` の中で、別のスレッドの `write_question`（待ち 0.3 秒）が偽を返す（ロックを持ったまま止めている） | 偽の子の `terminate` の中で偽の時計を進めても結果は変わらない。`terminate` が偽を返しても、待ちのうちに `isalive()` が偽になれば `"stopped"` | 質問のファイルが在る・壊れている・ロックを別のスレッドが持つと、`terminate` を呼ばず `"held"`。`terminate` が偽を返し `isalive()` が真のままなら `"alive"` で、`switch`・`restart`・`handover` のどれでも `spawn` が呼ばれず、`refused` で終わり `log.jsonl` の最後が `end` |

## ポーリングの質問の扱い（`Wrapper._question`・`_state`）

満たす決定: 決定 1・3・5、受け入れ条件 4・5・6、I3

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._question` | `wrapper.py` | 新設 | `(self, sec: Section) -> tuple[bool, bool, Optional[str]]` | 質問のファイルを読み、古ければ消し、（回答待ちか, 転送したか, 質問の ID）を返す |
| `Wrapper._state` | `wrapper.py` | 変更 | 変わらない | `question` を `signals.has_question` から `_question` の結果へ替え、`question_forwarded` と `question_id` を渡す |

処理の手順（上から順に最初に当たった行で決まる）:

| 条件 | 結果 |
| --- | --- |
| `read_question` が `SignalError`（壊れている・読めない） | 子が生きていなければ `drop_question(…, None, 0)` で消して `(False, False, None)`。生きていれば、`sec.broken` が偽なら `log.jsonl` に `{"kind": "question_broken", "reason": …}` を書いて立て、`(True, True, None)`（転送せず `wait`。決定 5） |
| 質問のファイルが無い | `sec.forwarded_question = None`・`sec.pending = None`・`sec.broken = False`、`(False, False, None)` |
| 子が生きていない | `drop_question` で消す、`(False, False, None)` |
| `launch` が文字列で `sec.launch` と違う。または `launch` が無く `session_id` が `sec.session_id` と違う | `drop_question` で消し、消せたら `log.jsonl` に `{"kind": "stale_question", "reason": …}`、`(False, False, None)` |
| 上のどれにも当たらない | `sec.pending` に読んだ中身を置き、`(True, sec.forwarded_question == qid, qid)`。`qid` は `signals.question_id` が返す質問の ID |
| `drop_question` の結果（1 つ目）が `"busy"`・`"kept"`・`"failed"`（ロックを取れない・置き換わっていた・消せない） | ファイルは残る。そのポーリングは `(False, False, None)`。`switch` などが出ても `_stop_if_clear` が質問のファイルを見て子を止めない。次のポーリングで読み直す |

繰り返しの終わる条件: ポーリングの繰り返し（`_loop`）は今と同じ。回答待ちの間は `wait` が続き、hook が消すか子が終わると抜ける。

内部のデータと定数:

| 名前 | 種類 | 既定値 |
| --- | --- | --- |
| `Section.pending` | フィールド `Optional[dict]`（このポーリングで読んだ質問） | None |
| `Section.broken` | フィールド `bool`（`question_broken` を書いたか） | False |
| `Section.launch` | フィールド `str`（この子の起動の ID。`Wrapper._start` が `uuid.uuid4().hex` で作る） | — |

ログ: `log.jsonl` へ `stale_question`・`question_broken` の行を書く。質問の本文と選択肢は書かない。

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 転送した質問が、hook が消すまで残る | 同じ `session_id` で起動し直した後、前の子の `launch` を持つ質問は消えて `stale_question` の行が 1 つ | 子が終わった・`launch` の無いセッションの違う質問で消える。壊れたファイル・不正な UTF-8 のファイル・読む権限の無いファイル（root では飛ばす）で、どれも `wait` と `question_broken` の行が 1 つ、`terminate` が呼ばれない。`config_broken` へ落ちず、`_loop` の例外の経路へ渡らない |
| 質問 A を転送した後、hook が A を消して質問 B を書くと、`forward` の行が A・B で 1 つずつ | 同じ質問では `forward` の行が 1 つ | — |

## 質問を転送する操作（`Wrapper._on_wait_like`）

満たす決定: 決定 1、受け入れ条件 5、I2

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._on_wait_like` | `wrapper.py` | 変更 | 変わらない | `forward` のとき、`sec.pending` のコピーを作業ディレクトリの一時のファイル（`FORWARD_FILE`、`replace_json`）へ書いて `ask_plan` へ渡し、終えたら一時のファイルを消す。質問のファイルは消さず、`sec.forwarded_question` へ `sec.pending` の質問の ID を残す。`sec.pending` が無ければ転送しない |

- 事前条件: `forward` のとき `sec.pending` がある
- 事後条件: `sec.forwarded_question` が質問の ID、質問のファイルは残る
- 副作用: 計画のリクエスト（今と同じ）・一時のファイル・状態のファイル

内部のデータと定数:

| 名前 | 種類 | 既定値 |
| --- | --- | --- |
| `Section.forwarded_question` | フィールド `Optional[str]`（転送した質問の ID） | None |
| `FORWARD_FILE` | 定数（計画のリクエストへ渡すコピーの名前 `.question.forward.json`） | — |

異常系:

| 失敗 | 原因 | 扱い | 利用者に見える出力 |
| --- | --- | --- | --- |
| 計画のリクエストへ載せられない | `ndf lanes question` の失敗（`ask_plan` の戻り値） | 今と同じく理由を状態と `log.jsonl` に書く。質問の ID は残す（同じ質問を転送し直さない） | 状態 `waiting` の理由 |

ログ: `forward` の行は `Wrapper._note`（変えない）が書く。理由の文に質問の ID が入るので、続けて来た別の質問の行も省かない。

## 子を止める操作（`Wrapper._on_switch`・`_on_resume`・`_on_handover`・`_start`）

満たす決定: 決定 1・2、受け入れ条件 1・2・3・7・10、I4・I6

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._on_switch` | `wrapper.py` | 変更 | 変わらない | 止める前に `account_decide.before_limit` でアカウントを選び（記録の行は書かない）、`_stop_if_clear` が `"stopped"` なら `account` の行・マーカーの削除・`_before_start`・`_start` を行う |
| `Wrapper._on_resume` | `wrapper.py` | 変更 | 変わらない | `_stop_if_clear` が `"stopped"` なら、起動の回数を数え、`_before_start`・`_start` を呼ぶ |
| `Wrapper._on_handover` | `wrapper.py` | 変更 | 変わらない | `_stop_if_clear` が `"stopped"` なら、`_before_start(update=False)` の後、同じ会話を `Launch(sec.number, sec.session_id, sec.account, step.prompt, resume=True)` で起動し、新しい `Section` の `handover_asked` を立てる。起動の回数は数えない。子の端末へは書かない |
| `Wrapper._start` | `wrapper.py` | 変更 | 変わらない | 起動の ID を作り、`spawn` の前に `signals.set_launch` で今の起動として記録し、`signals.launch_env` で子へ渡し、新しい `Section.launch` に残す。起動できなければ `set_launch(None)` に戻す |
| `Wrapper._send` | `wrapper.py` | 削除 | — | 呼び出し元が無くなる（I6） |

`_start` が起動の失敗を `Ended` で返し、`_on_switch`・`_on_resume` がそれをそのまま返す変更は #3842 の設計が受け持つ。`_on_handover` が `_start` を呼ぶのはこの設計からなので、同じ形で返すことはこの設計が受け持つ。

- 事後条件: `_stop_if_clear` が `"alive"` なら、次の子を起動せず `Ended("refused", CHILD_ALIVE…)` を返す（[ロックの中で子を止める](#ロックの中で子を止めるwrapper_stop_if_clear)）。`"held"` なら、何もせず None（マーカー・子・`account` の行・起動の回数・`handover_asked` はそのまま。`log.jsonl` にも書かない。次のポーリングの `forward` か `wait` の行が残る）
- 副作用: 子へのシグナル。子の端末へは書かない

並行と排他: 確かめと止め終えるまでの議論は [ロックの中で子を止める](#ロックの中で子を止めるwrapper_stop_if_clear) にある。マーカー・記録の行は止めた後に行う。しなかった操作は、次のポーリングの判断で決め直す。

ログ: `account` の行は `Wrapper._account_for_next`・`_note`（変えない）が `{"at", "section", "kind": "account", "reason"}` の形で書く。止めた後に書く。

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 切り替えで子の `written` が空のまま `terminate` が呼ばれる（`/exit` を書かない）。`handover` で `written` が空のまま、`--resume <同じ ID> <引継ぎの 1 行>` で起動され `handover_asked` が真。起動ごとに `NDF_RELAY_LAUNCH` が違う | `_state` の直後に hook の `write_question` で質問を書くと、`switch`・`account`・`restart`・`handover` のどれでも子が生き、`terminate` が呼ばれない | 確かめが外れたとき、マーカー・`account` の行・起動の回数・`handover_asked` が変わらない |

## 例外の経路（`Wrapper._loop`）

満たす決定: 決定 5、受け入れ条件 1・2・3

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `Wrapper._loop` | `wrapper.py` | 変更 | 変わらない | `decide`（`_state` を含む）が `SignalError`・`AccountError` を出したら、`_stop_child()` を直接呼ばず `_halt_broken(exc)` を返す |
| `Wrapper._halt_broken` | `wrapper.py` | 新設 | `(self, exc: Exception) -> Ended` | 状態のファイルを `waiting`（理由は例外の文と「質問が閉じるか子の終わりを待ってから止める」）にし、ポーリングごとに `_pump` で出力と利用者の入力をそのまま渡し、子が生きている間は `_stop_if_clear` を試す。`"stopped"` になるか子が終わったら `Ended("config_broken", str(exc))`、`"alive"` なら `Ended("refused", CHILD_ALIVE…)` を返す |

- `_halt_broken` の中では `decide` を呼ばず、切り替え・起動し直し・引継ぎ・転送をしない

単体テストの観点:

| 正常 | 境界値 | 異常 |
| --- | --- | --- |
| 質問が無いとき `next.json` を壊すと、今と同じく子を止めて `config_broken`（終了コード 2） | 回答待ちの質問がある間に `next.json` を壊すと、子を止めず `waiting`。hook が質問を消すと止めて `config_broken` | 子が自分で終わると `config_broken` で終わる |

## 手順書（`relay.md`）

満たす決定: 決定 4、受け入れ条件 8

- 「承認ゲートを越えない守り」の表を、`decide` の判断の順（質問 → 上限と認証の失敗 → マーカー → 子の終わり → 文脈の上限 → 止まり）と、hook の `ask-forward`・`ask-answered`・`ask-closed`・`relay-marker`・`stop-wait` の振る舞いで書き直す。回答待ちの間は `wait` を返すこと、hook とラッパーが同じロックを使い、hook は書けなければ質問を拒否し、ラッパーはロックの中で質問が無いと確かめて止めることを書く
- 質問が閉じたことを hook が知らせ、Esc の後は人が次に入力を送るまで切り替えないことを書く
- `question.lock`・`asked`・`wait-held.json`・`/exit` の書き込み・`log.jsonl` の `prose_wait` の行・`ndf-gate` の自動の付与の記述を消す
- 引継ぎが子の端末への書き込みでなく、同じ会話の起動し直し（`--resume` の引数）で届くことを書く
- 確かめは受け入れ条件 8 の `grep` と `python3 scripts/check-markdown-links.py` で行い、文言のテストは書かない
