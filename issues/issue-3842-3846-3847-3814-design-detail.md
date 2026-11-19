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
| `src/ndf/domains/relay/wrapper.py` | `Section` | フィールド `forwarded_question` を足す（新設） |
| `src/ndf/domains/relay/wrapper.py` | `Wrapper` | `_start`（変更）・`_loop`（変更）・`_on_switch`（変更）・`_on_resume`（変更）・`_state`（変更）・`_question`（新設）・`_on_wait_like`（変更） |
| `src/ndf/domains/relay/switch_decide.py` | `SectionState` | フィールド `question_forwarded` を足す（新設） |
| `src/ndf/domains/relay/switch_decide.py` | モジュール | `decide`（変更） |
| `src/ndf/domains/relay/signals.py` | モジュール | `read_question`（新設） |
| `src/ndf/domains/relay/observe.py` | モジュール | `answered`（新設） |

## 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `Wrapper._start` | `wrapper.py` | 変更 | `(self, req: Launch) -> Optional[Ended]` | 子を起動し、起動できなければ理由を持つ `Ended` を返す | 前の子は止めてある | 起動したら `self.section` が新しい子を持ち `running` が書かれて None。起動できなければ `self.section` は前のまま、`Ended("precondition", <理由>)` を返し、このとき立てたマーカーは消してある | 擬似端末の起動・`wrapper.json`・`log.jsonl`・マーカーのファイル |
| `Wrapper._loop` | `wrapper.py` | 変更 | 変わらない | 最初の `_start` が `Ended` を返したら `_end` して返す | — | — | 今と同じ |
| `Wrapper._on_switch`・`_on_resume` | `wrapper.py` | 変更 | 変わらない | `_start` が返した `Ended` をそのまま返す | — | — | 今と同じ |
| `Wrapper._question` | `wrapper.py` | 新設 | `(self, sec: Section, nxt: Optional[dict]) -> tuple[bool, bool]` | 質問のファイルを読み、消してよければ消し、（回答待ちか, 回したか）を返す | `nxt` は同じ見回りで読んだマーカー（`_next_mark` の結果） | 消す条件（下の「処理の手順」）に当たればファイルは無く `(False, False)`。当たらなければファイルは残る | `question.json` の削除・`log.jsonl` |
| `Wrapper._state` | `wrapper.py` | 変更 | 変わらない | `question` を `has_question` から `_question` の結果へ替え、`question_forwarded` を渡す | — | — | 今と同じ＋`_question` の副作用 |
| `Wrapper._on_wait_like` | `wrapper.py` | 変更 | 変わらない | `forward` のとき、質問のファイルを消さずに `sec.forwarded_question` へ印を残す | 質問のファイルがある | `sec.forwarded_question` が印、ファイルは残る | 計画のリクエスト（今と同じ） |
| `switch_decide.decide` | `switch_decide.py` | 変更 | 変わらない | `question` が真のとき、`question_forwarded` が偽なら `forward`、真なら `wait` を返す | — | 同じ状態に同じ手 | 無し |
| `signals.read_question` | `signals.py` | 新設 | `(directory: Path) -> Optional[dict[str, Any]]` | 質問のファイルの中身を返す | — | 無ければ None | 無し（読むだけ） |
| `observe.answered` | `observe.py` | 新設 | `(path: Path, tool_use_id: str, size: int) -> bool` | 会話の記録の末尾 `size` バイトに、`tool_use_id` を持つ `tool_result` の行があれば真 | — | 記録が無い・読めない行は偽として数える | 無し（読むだけ） |

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `Wrapper._start` | 作業ディレクトリが無い | ワークツリーの片付け・削除 | `self.s.cwd.is_dir()` が偽 | 止める（`spawn` を呼ばない） | `ndf-relay: 子の claude を起動できない（作業ディレクトリ <cwd> が無い）`、終了コード 3 | 何も残らない。ディレクトリを戻して `claude` を打ち直す |
| `Wrapper._start` | `spawn` が `OSError` | `claude` が PATH に無い・実行できない・擬似端末を開けない | `spawn` の呼び出し | 止める | `ndf-relay: 子の claude を起動できない（<例外の文>）`、終了コード 3 | 立てたマーカーを `markers.take` で消す。原因を直して打ち直す |
| `Wrapper._question` | 質問のファイルが壊れている | 書きかけ・手での編集 | `signals.read_question` の `SignalError` | 呼び出し元へ返す（`_loop` が今と同じく `config_broken` で止める） | `ndf-relay: シグナルファイルが壊れている（…）`、終了コード 2 | 今のマーカーの壊れと同じ |
| `Wrapper._question` | 質問の `at` か マーカーの `written_at` を読めない | 形の違う時刻 | `datetime.fromisoformat` の `ValueError` | 印の 1 つとして使わない（会話の記録と子の終わりで消す） | 無し | 次の見回りで読み直す |
| `observe.answered` | 会話の記録が無い・行が読めない | 書き途中・保存先の違い | `agents.tail` が空・`json.loads` の `ValueError` | 既定値で続ける（偽） | 無し | 次の見回りで読み直す |
| `Wrapper._on_wait_like` | 計画のリクエストへ載せられない | `ndf lanes question` の失敗 | `ask_plan` の戻り値 | 今と同じく理由を状態と `log.jsonl` に書く。印は残す（同じ質問を回し直さない） | 状態 `waiting` の理由 | 回し直さない |

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `Wrapper._question` | 質問のファイルが無い | `sec.forwarded_question = None`、`(False, False)` |
| `Wrapper._question` | ファイルの `session_id` が `sec.session_id` と違う | ファイルを消し `log.jsonl` に `{"kind": "stale_question", "reason": …}`、`(False, False)` |
| `Wrapper._question` | 子が生きていない | ファイルを消す、`(False, False)` |
| `Wrapper._question` | `tool_use_id` があり `observe.answered` が真 | ファイルを消し `log.jsonl` に `{"kind": "question_answered", "reason": "会話の記録に回答がある"}`、`(False, False)` |
| `Wrapper._question` | `nxt` があり、`nxt["written_at"]` が質問の `at` より後 | ファイルを消し `log.jsonl` に `{"kind": "question_answered", "reason": "質問の後に Stop がマーカーを書き直した"}`、`(False, False)` |
| `Wrapper._question` | 上のどれにも当たらない | `(True, sec.forwarded_question == 印)`。印は `tool_use_id`、無ければ `at` |
| `switch_decide.decide` | `question` 真・`question_forwarded` 偽 | `Step("forward", …)`（今と同じ文） |
| `switch_decide.decide` | `question` 真・`question_forwarded` 真 | `Step("wait", "回答待ちの質問がある。子を止めず、子の端末へ書かない")` |
| `switch_decide.decide` | `question` 偽 | 今と同じ |

- 繰り返しの終わる条件: 見回りの繰り返し（`_loop`）は今と同じ。回答待ちの間は `wait` が続き、上の消す条件のどれかで抜ける
- 計算量の注意: `observe.answered` を呼ぶのは、回答待ちの質問が `tool_use_id` を持つ見回りだけである。読むのは会話の記録の末尾 `relay.transcript-tail` バイトで、文脈の量の読みと同じ量である

## 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| `LAUNCH_FAILED` | 定数（理由の文の形 `子の claude を起動できない（{why}）`） | `wrapper.py` | — |
| `NO_CWD` | 定数（`作業ディレクトリ {cwd} が無い`） | `wrapper.py` | — |
| `QUESTION_PENDING` | 定数（`wait` の理由の文） | `switch_decide.py` | — |
| `Section.forwarded_question` | 型のフィールド `Optional[str]` | `wrapper.py` | None |
| `SectionState.question_forwarded` | 型のフィールド `bool` | `switch_decide.py` | False |

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 質問の hook（`ask-forward`）とラッパーの見回り | `question.json` | hook は `signals.write_json` の錠（`question.json.lock`）で書く。ラッパーは読むと消すだけで、置き換えは原子的（`replace_json`）なので書きかけを読まない | 消した後に hook が次の質問を書けば、次の見回りで新しい質問として読む（印が違うので回し直す） |
| Stop の hook（`relay-marker`）とラッパー | `next.json` | 今と同じ | 今と同じ |

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `Wrapper._start` → `_end` | `end` の行（`cause: precondition`・`reason`）・`wrapper.json` の `stopped` | `log.jsonl`・`wrapper.json` | 環境変数の値 |
| `cli._ended`（変えない） | `ndf-relay: <理由>` | 標準エラー | — |
| `Wrapper._question` | `stale_question`・`question_answered` の行 | `log.jsonl` | 質問の本文と選択肢 |

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `signals.read_question` | `signals._read`（壊れたら `SignalError`、無ければ None） | `grep -n "def _read" src/ndf/domains/relay/signals.py` | 使う（`read_next` と同じ形） |
| `observe.answered` | `ndf.adapters.agents.tail`・`observe.context_tokens` の読み方 | `grep -n "def tail" src/ndf/adapters/agents.py` | `tail` を使う。`context_tokens` は量だけを返すので別の関数にする |
| `Wrapper._question` | `Wrapper._next_mark`（前の実行のマーカーを消す形） | `wrapper.py` を読む | 同じ形（読み、違えば消して 1 行）に揃える |
| 起動の失敗の後始末 | `markers.take` | `grep -n "def take" src/ndf/domains/relay/markers.py` | 使う |

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `Wrapper._start` | 在る作業ディレクトリで起動する（今と同じ引数） | 最初・`switch`・`restart` の 3 つの手 | 無い作業ディレクトリで `spawn` を呼ばない。`spawn` の `FileNotFoundError` で `precondition` とマーカーの削除 |
| `Wrapper._question` | 回した質問が回答まで残る | 印が `tool_use_id` に無く `at` だけ | セッションが違う・子が終わった・回答の行・質問の後のマーカーで消える。質問の前のマーカーでは消えない |
| `switch_decide.decide` | 回した質問で `wait` | `question_forwarded` の無い JSON（`ndf relay decide`） | — |
| `signals.read_question` | 中身を返す | 無ければ None | 壊れていれば `SignalError` |
| `observe.answered` | `tool_result` の行で真 | 記録が無いと偽 | 末尾で切れた行は読み飛ばす |
