# #3242 の詳細設計: 人のいない実行の区間の報告と結果の行の差し戻し

設計文書 `issues/issue-3242-design.md` の「構成要素」と「構造」を、ファイルと関数の粒度へ下ろした文書である。語の意味は設計文書の「用語」の表と用語集（`docs/glossary.md`）に従う。

この文書で使う語:

| 語 | 意味 |
| --- | --- |
| 読み手の部品 | `plugins/ndf/scripts/lib/unattended_report.py`。区間の報告の形を契約のファイルから読み、応答の文を判定する純粋な関数を持つ |
| hook の部品 | `plugins/ndf/scripts/hook_lib/unattended.py`。人のいない実行の印を作り・数え・消し、差し戻すかを決める |
| 形の宣言 | 契約のファイル `unattended-report.json` の `shape` のキー |
| 忙しい Stop | Stop の入力の `background_tasks` か `session_crons` が空でない Stop（不変条件 I4） |

## ファイル配置

パッケージ・モジュール構成（触るファイルだけ。`*` は新設）:

```text
plugins/ndf/
├── contracts/
│   ├── forms/unattended-report.json *
│   ├── files/unattended-mark-json.json *
│   ├── entrypoints/plugins__ndf__scripts__hook.py.json
│   ├── env/NDF_FACTORY_RUN.json
│   ├── limits.json
│   └── index.md
├── hooks/claude.json
├── scripts/
│   ├── hook.py
│   ├── hook_lib/unattended.py *
│   ├── lib/unattended_report.py *
│   └── tests/
│       ├── test_unattended_report.py *
│       └── test_hook_unattended.py *
└── skills/development-workflow/
    ├── SKILL.md
    └── references/{asking.md, agent-layers.md}
scripts/
├── contracts_lib/model.py
└── tests/test_check_ndf_contracts.py
docs/ndf-plugin-reference.md
```

依存の向きは一方向で、循環は無い。`hook.py` が `hook_lib/unattended.py` を import し、`hook_lib/unattended.py` が `lib/unattended_report.py`・`lib/limits.py`・`lib/state_home.py`・`lib/jsonio.py` を import する。`lib/unattended_report.py` は標準ライブラリだけを import し、hook の部品を知らない。契約の検査（`scripts/contracts_lib/model.py`）は CI の側の部品で、プラグインの実行時の部品を import しない。読み手の部品も契約の検査を import しない（形の宣言の型は 2 か所で別の目的に確かめる。下の「内部のデータと定数」）。

| パス | 種類 | 役割 1 文 | 依存する先 |
| --- | --- | --- | --- |
| `plugins/ndf/contracts/forms/unattended-report.json` | 新設 | 区間の報告の形の正本（規則の文・形の宣言・例の組） | 無し |
| `plugins/ndf/contracts/files/unattended-mark-json.json` | 新設 | 人のいない実行の印のファイルを契約の項目として宣言する | 無し |
| `plugins/ndf/contracts/entrypoints/plugins__ndf__scripts__hook.py.json` | 変更 | `hook.py` が読む環境変数に `NDF_FACTORY_RUN` を、書くファイルに `unattended-mark-json` を足す | 無し |
| `plugins/ndf/contracts/env/NDF_FACTORY_RUN.json` | 変更 | 要約に、`hook.py` の人のいない実行の hook が読むことを足す | 無し |
| `plugins/ndf/contracts/limits.json` | 変更 | 方針 `hook.unattended-pushback`（`attempts: 2`）と `hook.unattended-mark`（`seconds: 604800`）を足す | 無し |
| `plugins/ndf/contracts/index.md` | 変更 | 契約の項目から生成し直す（手で書かない） | 契約の項目 |
| `plugins/ndf/hooks/claude.json` | 変更 | UserPromptSubmit・Stop・SessionEnd に、`NDF_FACTORY_RUN` が空なら sh だけで終える行を 1 つずつ足す | `scripts/hook.py` |
| `plugins/ndf/scripts/hook.py` | 変更 | 副命令 `unattended-start`・`unattended-stop`・`unattended-end` を hook の部品へ振り分ける | `hook_lib/unattended.py` |
| `plugins/ndf/scripts/hook_lib/unattended.py` | 新設 | 印の作成・回数の更新・削除と、差し戻すかの決定と、案内の文・差し戻しの文 | `lib/unattended_report.py`・`lib/limits.py`・`lib/state_home.py`・`lib/jsonio.py` |
| `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | 契約のファイルを読み、応答の文から結果の値と形の誤りの一覧を返す | 無し（標準ライブラリだけ） |
| `plugins/ndf/scripts/tests/test_unattended_report.py` | 新設 | 読み手の部品を 2 つの契約の例の組と形の宣言の壊し方で縛る | `lib/unattended_report.py` |
| `plugins/ndf/scripts/tests/test_hook_unattended.py` | 新設 | hook の部品と登録の行を、印・回数・忙しい Stop・失敗の扱いで縛る | `hook.py`・`hook_lib/unattended.py`・`hooks/claude.json` |
| `scripts/contracts_lib/model.py` | 変更 | 受け渡しの形に `shape` のキーと区間の報告の例の組の形を許し、`shape` の型を確かめる | 無し |
| `scripts/tests/test_check_ndf_contracts.py` | 変更 | `shape` の正しい形と壊れた形を契約の検査に通す | `scripts/contracts_lib/model.py` |
| `plugins/ndf/skills/development-workflow/references/asking.md` | 変更 | 節「人のいない実行」を足し、`allow` の表の行からそこを指す | 契約のファイル（参照だけ） |
| `plugins/ndf/skills/development-workflow/SKILL.md` | 変更 | 「自走で工程を通す」から `asking.md` の節を指す | `asking.md` |
| `plugins/ndf/skills/development-workflow/references/agent-layers.md` | 変更 | 「conductor の報告」から `asking.md` の節を指す | `asking.md` |
| `docs/ndf-plugin-reference.md` | 変更 | hook の節に、人のいない実行の hook を 1 行ずつ足す | 無し |

## クラスと関数の構造

型の関係は設計文書の「構造」の節のクラス図を指し、ここでは描き直さない。クラス図の `UnattendedReportForm` は `ReportForm`、`ReportCheck` は読み手の部品のモジュール、`UnattendedHook` は hook の部品のモジュール、`Mark` は `Mark` に当たる。

| 置き場 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/unattended_report.py` | `ReportForm`（frozen の dataclass） | 新設: `values`・`items`・`material`・`required`（属性） |
| `plugins/ndf/scripts/lib/unattended_report.py` | `FormError`（例外） | 新設 |
| `plugins/ndf/scripts/lib/unattended_report.py` | モジュール | 新設: `load_form`・`form_from_dict`・`result_value`・`report_items`・`report_errors`・`_missing_items`・`_material_errors` |
| `plugins/ndf/scripts/hook_lib/unattended.py` | `Mark`（frozen の dataclass） | 新設: `session_id`・`started_at`・`pushbacks`（属性） |
| `plugins/ndf/scripts/hook_lib/unattended.py` | モジュール | 新設: `is_target`・`mark_path`・`read_mark`・`write_mark`・`prune_marks`・`next_count`・`pushback_reason`・`start`・`stop`・`end` |
| `plugins/ndf/scripts/hook.py` | モジュール | 変更: `dispatch`（3 つの副命令の振り分けを足す）。名前だけ: `COMMANDS`（3 語を足す。`_args` と `main` は変えない） |
| `scripts/contracts_lib/model.py` | モジュール | 変更: `_check_form`。新設: `_check_shape`。名前だけ: `CASE_SHAPES`（3 つ目の形を足す） |

## 関数の仕様

| 関数 | 置き場 | 種類 | シグネチャ | 責務 1 文 | 事前条件 | 事後条件 | 副作用 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| `load_form` | `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | `load_form(path: Path = FORM_PATH) -> ReportForm` | 契約のファイルを読み、形の宣言を `ReportForm` にする | 無し | 返した `ReportForm` は `form_from_dict` の事後条件を満たす | ファイルの読み取り |
| `form_from_dict` | `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | `form_from_dict(data: dict) -> ReportForm` | 契約のファイルの中身から `shape` を取り出し、読み手が使う形へ直す | `data` は JSON から読んだ辞書 | `values` は空でない文字列のタプル。`required` のキーは `values` と同じ集合。`required` の項目名と `material.item` はすべて `items` に含まれる | 無し |
| `result_value` | `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | `result_value(text: str) -> str` | 応答の文の最後の結果の行の値を返す | 無し（文字列でない値は空の文字列を返す） | 行があれば最後の行の値（前後の空白を除く）、無ければ空の文字列 | 無し |
| `report_items` | `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | `report_items(text: str) -> dict[str, str]` | 最後の結果の行より後ろの `- <項目>: <値>` の行を項目の辞書にする | 無し | 同じ項目が 2 つあれば後ろの値。結果の行が無ければ空の辞書 | 無し |
| `report_errors` | `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | `report_errors(text: str, form: ReportForm) -> list[str]` | 応答の文が区間の報告の形に合わない理由を並べる | `form` は `form_from_dict` が作ったもの | 形に合えば空のリスト。誤りの文は `ERR_*` の定数から作り、例の組の `errors` と一致する。並びは結果の値・要る項目・材料の順 | 無し |
| `_missing_items` | `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | `_missing_items(value: str, items: dict[str, str], form: ReportForm) -> list[str]` | 結果の値ごとに要る項目のうち、無いか `無し` のものを返す | `value` は `form.values` のどれか | `always` と、条件に当たった `when` の `need` を宣言の順に並べ、重複を除いた欠けの一覧 | 無し |
| `_material_errors` | `plugins/ndf/scripts/lib/unattended_report.py` | 新設 | `_material_errors(text: str, items: dict[str, str], form: ReportForm) -> list[str]` | 材料の値が本文か `https://` の URL か、本文なら見出しが報告より前にあるかを確かめる | 無し | 材料が無いか `無し` なら空のリスト（欠けは `_missing_items` が持つ） | 無し |
| `is_target` | `plugins/ndf/scripts/hook_lib/unattended.py` | 新設 | `is_target(prompt: object) -> bool` | プロンプトの 1 行目が人のいない実行の conductor の起動かを決める | 無し | 1 行目が `/ndf:development-workflow`（前に `/goal ` を許す）で始まり、その後が空白か行末のときだけ真 | 無し |
| `mark_path` | `plugins/ndf/scripts/hook_lib/unattended.py` | 新設 | `mark_path(session_id: object) -> Path \| None` | セッションの識別子から印のファイルのパスを決める | 無し | `SESSION_RE` に合えば `container_home()/unattended/<session_id>.json`、合わなければ None | 無し |
| `read_mark` | `plugins/ndf/scripts/hook_lib/unattended.py` | 新設 | `read_mark(path: Path) -> Mark \| None` | 印のファイルを読む | 無し | ファイルが無いか形が違えば None | ファイルの読み取り |
| `write_mark` | `plugins/ndf/scripts/hook_lib/unattended.py` | 新設 | `write_mark(path: Path, mark: Mark) -> None` | 印を原子的に書く | 無し | ファイルの中身が `mark` と一致する。途中で落ちても半端なファイルが残らない | ファイルの書き込み |
| `prune_marks` | `plugins/ndf/scripts/hook_lib/unattended.py` | 新設 | `prune_marks(directory: Path, now: float, max_age: float) -> int` | 更新時刻から `max_age` 秒を過ぎた印を消す | 無し | 過ぎた印が無い。返り値は消した数 | ファイルの削除 |
| `next_count` | `plugins/ndf/scripts/hook_lib/unattended.py` | 新設 | `next_count(errors: list[str], pushbacks: int, limit: int, busy: bool) -> tuple[bool, int]` | 差し戻すかと、次の回数を決める | `pushbacks` と `limit` は 0 以上 | 「処理の手順」の判定表のとおり | 無し |
| `pushback_reason` | `plugins/ndf/scripts/hook_lib/unattended.py` | 新設 | `pushback_reason(errors: list[str], remaining: int) -> str` | 差し戻しの文を作る | `errors` は空でない | 文が誤りの一覧・契約のファイルのパス（リポジトリの中の相対パス）・残りの差し戻しの回数を持つ | 無し |
| `start` | `plugins/ndf/scripts/hook_lib/unattended.py` | 新設 | `start(hook_input: dict, now: float) -> str \| None` | 対象のセッションに印を作り、案内の文を返す | `NDF_FACTORY_RUN` が空でない（登録の行が確かめる） | 対象なら印（`pushbacks: 0`）があり案内の文を返す。対象でなければ印を作らず None。どちらでも過ぎた印は消えている | ファイルの書き込みと削除 |
| `stop` | `plugins/ndf/scripts/hook_lib/unattended.py` | 新設 | `stop(hook_input: dict) -> dict \| None` | 印のあるセッションの Stop で、差し戻すかを決めて回数を更新する | 同上 | 差し戻すなら `{"decision": "block", "reason": …}` を返し、印の回数は 1 増えている。通すなら None。印は消さない | ファイルの読み書き |
| `end` | `plugins/ndf/scripts/hook_lib/unattended.py` | 新設 | `end(hook_input: dict) -> None` | セッションの印を消す | 同上 | そのセッションの印が無い | ファイルの削除 |
| `dispatch` | `plugins/ndf/scripts/hook.py` | 変更 | `dispatch(command: str, runtime: str, raw: dict \| None) -> dict \| str \| None`（変えない） | 副命令 `unattended-*` を hook の部品へ渡し、その出力 1 つだけを返す（`_merge` と guard と待ちの通知を通さない） | 無し | `unattended-start` は対象なら `{"hookSpecificOutput": {"hookEventName": "UserPromptSubmit", "additionalContext": …}}`、`unattended-stop` は部品の返り値、`unattended-end` は None。`raw` が None なら 3 つとも None | hook の部品の副作用 |
| `_check_form` | `scripts/contracts_lib/model.py` | 変更 | `_check_form(f: Path, d: dict) -> None`（変えない） | 受け渡しの形に任意のキー `shape` を許し、あれば `_check_shape` へ渡す | 無し | `shape` の無い既存の 4 つの形の判定は変わらない | 無し |
| `_check_shape` | `scripts/contracts_lib/model.py` | 新設 | `_check_shape(f: Path, shape: dict) -> None` | 形の宣言のキーと型を確かめる | `shape` は辞書 | 「内部のデータと定数」の `shape` の形に合わなければ `ContractError` | 無し |

`hook.py` の `main` は変えない。`dispatch` が返した辞書を 1 行の JSON で出し、例外を飲み込んで終了コード 0 を返す既存の振る舞いを、3 つの副命令もそのまま使う。

## 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load_form` | 契約のファイルが読めない | ファイルが無い・権限・JSON が壊れている | `jsonio.read` の `JsonReadError` | `FormError` にして呼び出し元へ返す | 無し（呼び出し元が決める） | 後始末は無い。何度呼んでもよい |
| `form_from_dict` | 形の宣言が違う | `shape` が無い・型が違う・`required` のキーが `values` と違う・項目名が `items` に無い | 関数の中の型と集合の比較 | `FormError` を上げて呼び出し元へ返す | 無し（呼び出し元が決める） | 後始末は無い |
| `start` | 印が書けない・過ぎた印が消せない | 状態の根に書けない・ディスクの不足 | `OSError` | 呼び出し元へ返す（`hook.py` の `main` が飲み込む） | 標準エラーへ `ndf: unattended-start: <例外の型>` の 1 行。標準出力は空で、案内は渡らない。終了コード 0 | 半端な印は残らない（`write_mark` の原子的な書き込み）。次のプロンプトで作り直す |
| `start` | 案内を返さない | 入力が辞書でない・`prompt` が文字列でない・セッションの識別子が `SESSION_RE` に合わない | `is_target` と `mark_path` | 既定値で続ける（対象でない扱い） | 何も出さない。終了コード 0 | 後始末は無い |
| `stop` | 契約のファイルが読めない・形の宣言が違う | 配布物の破損・宣言の誤り | `load_form` の `FormError` | 止める側でなく通す側へ倒す（差し戻さない。回数も変えない） | 標準エラーへ `ndf: unattended-stop: 区間の報告の契約を読めない: <理由>` の 1 行。標準出力は空。終了コード 0 | 印は残る。次の Stop で読み直す |
| `stop` | 上限の宣言が読めない | `limits.json` の破損・方針 `hook.unattended-pushback` が無い | `limits.LimitsError` | 通す側へ倒す（差し戻さない） | 標準エラーへ `ndf: unattended-stop: <LimitsError の文>` の 1 行。終了コード 0 | 印は残る |
| `stop` | 印が読めない | 印が無い（対象でないセッション・消された）・壊れている | `read_mark` が None | 既定値で続ける（差し戻さない） | 何も出さない。終了コード 0 | 壊れた印はそのまま。SessionEnd か次の `start` の掃除で消える |
| `stop` | 回数が書けない | 状態の根に書けない | `write_mark` の `OSError` | 差し戻しの判定は返す。書けなかったことを標準エラーへ残す | 差し戻すときは `decision: "block"` を出す。標準エラーへ `ndf: unattended-stop: 回数を書けない: <理由>` の 1 行。終了コード 0 | 回数が増えないため、差し戻しが Claude Code の上限（8 回続けて）まで続きうる。残リスクとして下の注記に置く |
| `end` | 印が消せない | 権限・状態の根の不在 | `OSError`（`FileNotFoundError` は失敗に数えない） | 呼び出し元へ返す（`main` が飲み込む） | 標準エラーへ `ndf: unattended-end: <例外の型>` の 1 行。終了コード 0 | 次の `start` の掃除で `hook.unattended-mark` の秒を過ぎたものが消える |
| `prune_marks` | 1 つの印が消せない | 権限・別のプロセスが先に消した | `OSError` | 既定値で続ける（そのファイルを飛ばして残りを消す） | 何も出さない | 次の `start` で再び試す |
| `read_mark` | 読めない・形が違う | ファイルが無い・JSON が壊れている・キーか型が違う | `jsonio.read` の `missing`・`broken` と型の比較 | 既定値で続ける（None を返す） | 何も出さない | 呼び出し元の扱いに従う |
| `write_mark` | 書けない | 状態の根に書けない | `jsonio.write_atomic` の `OSError` | 呼び出し元へ返す | 無し（呼び出し元が決める） | 一時ファイルは `write_atomic` が消す |
| `dispatch` | 標準入力が JSON でない | Claude Code の入力の形の変化・空の入力 | `main` の `json.loads` | 既定値で続ける（`raw` を None にし、3 つの副命令は何も返さない） | 何も出さない。終了コード 0 | 後始末は無い |
| `_check_shape` | 形の宣言が違う | 契約のファイルの書き誤り | 関数の中の型と集合の比較 | 止める（`ContractError`） | `check-ndf-contracts.py` が `<ファイル名>: shape.<キー> …` を出し、終了コード 2 | 契約のファイルを直して打ち直す |

`stop` で回数が書けないときに差し戻しを返すのは、形に合わない応答を Factory へそのまま渡すより、差し戻して報告を書かせる方が受け入れ条件 7 に沿うためである。回数が増えない間は Claude Code の上限（8 回続けて。STD1）が上限になる。これは状態の根に書けない環境に限られ、その環境では問い合わせの記録（`ask.py decide`）も書けず工程が先に止まる。

## 処理の手順

| 関数 | 条件の組み合わせ | 結果 |
| --- | --- | --- |
| `next_count` | 忙しい Stop | 差し戻さない。回数は変えない |
| `next_count` | 忙しくない・誤りが無い | 差し戻さない。回数を 0 にする |
| `next_count` | 忙しくない・誤りがある・回数が上限未満 | 差し戻す。回数に 1 を足す |
| `next_count` | 忙しくない・誤りがある・回数が上限以上 | 差し戻さない。回数は変えない |
| `stop` | 印が無い | 何もしない（`next_count` を呼ばない） |
| `stop` | 印がある・`next_count` が回数を変えない | 印を書き直さない |
| `stop` | 印がある・`next_count` が回数を変える | 新しい回数で印を書き直す |
| `report_errors` | 結果の行が無い | `ERR_NO_RESULT` だけを返す |
| `report_errors` | 結果の値が `values` に無い | `ERR_BAD_VALUE` だけを返す |
| `report_errors` | 結果の値が `values` にある | `_missing_items` の欠けごとに `ERR_MISSING`、続けて `_material_errors` の誤り |
| `_missing_items` | `when` の条件が `prefix` | 項目の値が接頭辞で始まれば `need` を足す |
| `_missing_items` | `when` の条件が `equals` | 項目の値が等しければ `need` を足す |
| `_material_errors` | 材料が無いか `無し` | 誤り無し |
| `_material_errors` | 材料が `本文` で、最後の結果の行より前に見出し「## 判断の材料」の行がある | 誤り無し |
| `_material_errors` | 材料が `本文` で、見出しが無いか結果の行より後ろにある | `ERR_NO_MATERIAL_HEADING` |
| `_material_errors` | 材料の値を空白で区切った語がすべて `https://` で始まる | 誤り無し |
| `_material_errors` | それ以外（ファイルのパス・`http://` など） | `ERR_BAD_MATERIAL` |
| `is_target` | 1 行目の前後の空白を除いた文が `TARGET_RE` に合う | 真 |
| `is_target` | 2 行目以降にだけ `/ndf:development-workflow` がある・`/ndf:development-workflowx` のように語が続く | 偽 |

- 繰り返しの終わる条件: `report_items` と `result_value` は応答の文の行を 1 回だけ走査する。`prune_marks` は印のディレクトリの `*.json` を 1 回だけ列挙する。差し戻しの繰り返しは `next_count` の上限で終わる
- 計算量の注意: 応答の文の長さに比例する。正規表現は行頭に錨を置き、入れ子の繰り返しを持たない（承認資料を含む長い応答でも後戻りで時間が延びない）

## 内部のデータと定数

| 名前 | 種類 | 置き場 | 既定値 |
| --- | --- | --- | --- |
| `FORM_PATH` | 定数 | `plugins/ndf/scripts/lib/unattended_report.py` | `<プラグインの根>/contracts/forms/unattended-report.json`（モジュールのパスから決める） |
| `RESULT_RE` | 定数 | `plugins/ndf/scripts/lib/unattended_report.py` | `^\s*-\s*結果:\s*(.*?)\s*$`（複数行。`ndf-next-block.json` の規則の後半と同じ読み方。プラグインの中の唯一の置き場） |
| `ITEM_RE` | 定数 | `plugins/ndf/scripts/lib/unattended_report.py` | `^\s*-\s*([^:\n]+?):\s*(.*?)\s*$`（項目名に `:` を含まない。値の中の URL の `:` は値に入る） |
| `NONE_VALUE` | 定数 | `plugins/ndf/scripts/lib/unattended_report.py` | `無し` |
| `ERR_NO_RESULT` | 定数 | `plugins/ndf/scripts/lib/unattended_report.py` | `結果の行が無い` |
| `ERR_BAD_VALUE` | 定数 | `plugins/ndf/scripts/lib/unattended_report.py` | `結果の値が {values} のどれでもない: {value}`（`values` は ` / ` で結ぶ） |
| `ERR_MISSING` | 定数 | `plugins/ndf/scripts/lib/unattended_report.py` | `「{item}」が無いか無しになっている（結果が {value} のとき要る）` |
| `ERR_NO_MATERIAL_HEADING` | 定数 | `plugins/ndf/scripts/lib/unattended_report.py` | `「材料」が本文なのに、区間の報告より前に「{heading}」の節が無い` |
| `ERR_BAD_MATERIAL` | 定数 | `plugins/ndf/scripts/lib/unattended_report.py` | `「材料」が本文でも {url_prefix} の URL でもない: {value}` |
| `ReportForm` | 型 | `plugins/ndf/scripts/lib/unattended_report.py` | `values: tuple[str, ...]`・`items: tuple[str, ...]`・`material: dict[str, str]`（`item`・`body`・`heading`・`url_prefix`）・`required: dict[str, dict]`（`always: tuple[str, ...]`・`when: tuple[dict, ...]`） |
| `shape` | 形の宣言 | `plugins/ndf/contracts/forms/unattended-report.json` | 設計文書の「契約のファイル」のとおり。値・項目・値ごとに要る項目の唯一の置き場 |
| `CASE_SHAPES` の 3 つ目 | 定数 | `scripts/contracts_lib/model.py` | `{"text": str, "result": str, "errors": list}`（`errors` は文字列の並び） |
| `TARGET_RE` | 定数 | `plugins/ndf/scripts/hook_lib/unattended.py` | `^(?:/goal\s+)?/ndf:development-workflow(?:\s\|$)` |
| `SESSION_RE` | 定数 | `plugins/ndf/scripts/hook_lib/unattended.py` | `^[A-Za-z0-9-]{1,128}$` |
| `MARK_DIR_NAME` | 定数 | `plugins/ndf/scripts/hook_lib/unattended.py` | `unattended`（`state_home.container_home()` の下） |
| `Mark` | 型 | `plugins/ndf/scripts/hook_lib/unattended.py` | `session_id: str`・`started_at: str`（UTC の ISO 8601）・`pushbacks: int`。ファイルの形は設計文書の「人のいない実行の印」の表 |
| `GUIDE` | 定数 | `plugins/ndf/scripts/hook_lib/unattended.py` | 人のいない実行であること・区分の無い問いは決めて `ask.py decide --question` で記録して続けること・区間の終え方の正本（`asking.md` の「人のいない実行」と契約のファイルのパス）を指す文。形の表は写さない |
| `hook.unattended-pushback` | 設定値 | `plugins/ndf/contracts/limits.json` | `attempts: 2`（`limits.value(..., "attempts")` で引く） |
| `hook.unattended-mark` | 設定値 | `plugins/ndf/contracts/limits.json` | `seconds: 604800`（7 日。消し残した印を消すまでの秒。`limits.seconds(...)` で引く） |

- 値・項目・値ごとに要る項目・材料の見出しは形の宣言だけが持つ。読み手の部品はそこから読み、コードに書き写さない（不変条件 I2）。誤りの文の定数は文の型だけを持ち、項目名と値は形の宣言から埋める
- 形の宣言の型は 2 か所で確かめる。契約の検査（`_check_shape`）は CI で宣言の誤りを止め、読み手の部品（`form_from_dict`）は実行時に壊れた配布物を通す側へ倒すために確かめる。確かめる中身（キーの集合と型）は同じ表から書くが、2 つの部品は依存の向きが別（CI の側とプラグインの側）のため、1 つの関数にまとめない
- 上限の秒を `hook.unattended-mark` に置くのは、構造の検査 `limit-literal` がコードの中の時間の数値を止めるためである。既存の待ちの通知の `RECORD_DAYS` とは別の方針にし、印の寿命を独立に変えられるようにする

## 並行と排他

| 同時に走りうる処理 | 共有する資源 | 排他の取り方 | 冪等性と中断からの再開 |
| --- | --- | --- | --- |
| 同じセッションの UserPromptSubmit・Stop・SessionEnd の hook | そのセッションの印のファイル | 取らない。Claude Code は 1 つのセッションの事象を順に起こし、同じ事象の同じ登録の行を並べて起動しない | `write_mark` は原子的に書くため、中断しても前の中身か新しい中身のどちらかが残る。`end` は何度呼んでもよい（無ければ何もしない） |
| 同じ Stop に重なる別の hook（待ちの通知・relay の印・`/goal` の条件の判定） | 無し（印は人のいない実行の hook だけが読み書きする） | 不要 | NDF の回数は人のいない実行の hook が差し戻したときだけ増える（不変条件 I5） |
| 別のセッション（worker・レビューの `claude -p`・並行のレーンの conductor） | 印のディレクトリ | ファイル名がセッションの識別子で分かれるため取らない | `prune_marks` が他のセッションの印を消すのは `hook.unattended-mark` の秒を過ぎたものだけで、動いているセッションの印は更新時刻が新しく残る |
| `prune_marks` と別のセッションの `end` が同じ印を消す | 印のファイル | 取らない | 先に消えていれば `FileNotFoundError` を失敗に数えずに次へ進む |

印の更新時刻は `write_mark` のたびに新しくなる。7 日を超えて Stop の起きない人のいない実行は、Factory の実行ジョブの区間として想定しない。超えて印が消えた場合は、その後の Stop を差し戻さずに通す（`stop` の「印が無い」の行）。

## ログと記録の出力

| 関数 | 出すもの | レベルまたは書き先 | 出さないもの |
| --- | --- | --- | --- |
| `start` | 案内の文 | 標準出力の `additionalContext`（conductor の文脈） | プロンプトの本文・環境変数の値 |
| `stop` | 差し戻しの文（誤りの一覧・契約のファイルのパス・残りの回数） | 標準出力の `reason`（conductor の文脈） | 応答の文のコピー（誤りの文に載るのは項目名と値だけ） |
| `stop`・`start`・`end` | 失敗の 1 行（`ndf: unattended-<副命令>: <理由>`） | 標準エラー（Claude Code のデバッグの記録） | 応答の文・プロンプト・状態の根の絶対パスの外の値 |
| `stop` | 印の回数 | 印のファイル（`<状態の根>/unattended/<session_id>.json`） | 応答の文・結果の値 |

区分の無い問いの記録（`ask.py decide --question`）は conductor が打ち、hook は書かない。進捗の記録（`projects-sync.sh`）も hook は書かない。秘密を扱う関数は無い。基準は `logging-guidelines` Skill にある。

## 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `load_form` | `lib/jsonio.py` の `read` | `grep -n "^def " plugins/ndf/scripts/lib/jsonio.py` | 使う（`missing` と `broken` を `RAISE` にして `JsonReadError` を `FormError` へ直す） |
| `form_from_dict` | `scripts/contracts_lib/model.py` の `_need` | `grep -n "def _need" scripts/contracts_lib/model.py` | 使えない。CI の側の部品で、プラグインの実行時の経路から import すると配布物の外を読む |
| `result_value` | `tests/test_ndf_next_form.py` の `RESULT_LINE` と `decision_needed`・`relay_lib/mark.py` | `grep -rn "結果:" --include=*.py plugins/ndf/scripts/relay.py plugins/ndf/scripts/relay_lib plugins/ndf/scripts/lib` | 使えない。プラグインのコードに結果の行の読み手は無く（検索の当たりは `lib/dist_record.py` の `CD の結果:` だけ）、テストの中の正規表現は import する部品でない。新設の `RESULT_RE` を唯一の置き場にする |
| `report_items` | `hook_lib/goal_skill.py` の `parse_goal` | `Read plugins/ndf/scripts/hook_lib/goal_skill.py` | 使えない。`/goal` の 1 行目だけを読む関数で、項目の行を読まない |
| `report_errors` | 無し（区間の報告は新しい形） | `grep -rn "区間の報告" plugins/ndf/scripts` | 使えない。当たりが無い |
| `_missing_items` | 無し | 同上 | 使えない。当たりが無い |
| `_material_errors` | 無し | 同上 | 使えない。当たりが無い |
| `is_target` | `hook_lib/goal_skill.py` の `PATTERN` | `Read plugins/ndf/scripts/hook_lib/goal_skill.py` | 使えない。`/goal` が無いプロンプトに当たらず、任意の Skill に当たる。読み方（1 行目の前後の空白を除く）だけを揃える |
| `mark_path` | `lib/state_home.py` の `container_home`・`hook_lib/wait_notify.py` の `_session_file` | `grep -rn "def container_home\|def _session_file" plugins/ndf/scripts` | `container_home` を使う。`_session_file` は合わない文字を `_` に置き換えるため、別のセッションが同じ名前になりうる。合わなければ印を作らない |
| `read_mark` | `lib/jsonio.py` の `read` | `grep -n "^def " plugins/ndf/scripts/lib/jsonio.py` | 使う（`missing=None`・`broken=None`・`want=dict`） |
| `write_mark` | `lib/jsonio.py` の `write_atomic` | 同上 | 使う |
| `prune_marks` | `hook_lib/wait_notify.py` の `_prune`・`lib/durable.py` の `purge_old` | `grep -rn "def _prune\|def purge_old" plugins/ndf/scripts` | 使えない。`_prune` は日数の定数 `RECORD_DAYS` と `.json.lock` を前提にした非公開の関数で、`purge_old` は耐久の記録の置き場の形を前提にする。同じ形（更新時刻と `unlink`）で書く |
| `next_count` | 無し | `grep -rn "pushback\|差し戻" plugins/ndf/scripts/hook_lib` | 使えない。当たりが無い |
| `pushback_reason` | 無し | 同上 | 使えない。当たりが無い |
| `start` | `hook_lib/goal_skill.py` の `context` | `Read plugins/ndf/scripts/hook_lib/goal_skill.py` | 使えない。状態を持たない案内で、印を作らない。`dispatch` で出力を `UserPromptSubmit` の形に包む扱いだけを揃える |
| `stop` | `hook_lib/wait_notify.py` の `hook` | `grep -n "^def " plugins/ndf/scripts/hook_lib/wait_notify.py` | 使えない。待ちの Slack 通知で、差し戻しを返さない |
| `end` | 無し（`hooks/claude.json` に SessionEnd の登録が無い） | `grep -n "SessionEnd" plugins/ndf/hooks/claude.json` | 使えない。当たりが無い |
| `_check_shape` | `scripts/contracts_lib/model.py` の `_need`・`_strs` | `grep -n "^def " scripts/contracts_lib/model.py` | 使う（`shape` と `material`・`required` の各値のキーと型を確かめる） |

上限の回数と秒は `lib/limits.py` の `value` と `seconds` で引き、状態の根は `lib/state_home.py` の `container_home` で決める（`stop`・`start` が呼ぶ）。

## 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `load_form` | プラグインの契約のファイルを読み、`values` が 3 つになる | 無し | ファイルが無い・JSON が壊れている一時ファイルで `FormError` |
| `form_from_dict` | 契約のファイルの中身で `ReportForm` を返す | `when` が空の値（`完了`） | `required` のキーが `values` と違う・項目名が `items` に無い・`shape` が無いもので `FormError` |
| `result_value` | `unattended-report.json` の例の組の `result` と一致する | 結果の行が 2 つある文は後ろの値。値の前後の空白を除く。`ndf-next-block.json` の例の組で、値が `判断が要る` のものは `launch` が偽 | 行が無い文・文字列でない値で空の文字列 |
| `report_items` | 結果の行より後ろの項目を辞書にする | 同じ項目が 2 つあれば後ろ。結果の行より前の `- 課題:` は読まない。値の中の URL の `:` が値に入る | 結果の行が無い文で空の辞書 |
| `report_errors` | `unattended-report.json` の例の組の `errors` と一致する。3 つの値の正しい形で空 | 形の宣言の `required` を書き換えた `ReportForm` で判定が変わる（要る項目を関数に書き写していない） | 3 つの外の値・要る項目の欠け・承認ゲートで Pull Request が無い・人の操作が要る区分で人に頼むことが無い |
| `_missing_items` | `判断が要る` で区分・問い・材料が揃えば空 | 区分が `ndf-gate:1` のときだけ承認ゲートと Pull Request が要る | 値が `無し` の項目を欠けとして返す |
| `_material_errors` | 材料が `本文` で見出しが報告より前にある・`https://` の URL が 2 つ | 見出しが報告より後ろにある | 見出しが無い・ファイルのパス・`http://` の URL で誤りを返す |
| `is_target` | `/ndf:development-workflow #12`・`/goal /ndf:development-workflow #12` で真 | 前後の空白・引数の無い `/ndf:development-workflow` で真 | `/ndf:design #1`・普通の文・2 行目だけに名前がある文・`/ndf:development-workflowx`・文字列でない値で偽 |
| `mark_path` | 英数字と `-` の識別子で状態の根の下のパス | 128 文字で真、129 文字で None | `/`・`.`・空の文字列・文字列でない値で None |
| `read_mark` | 書いた印を読み戻す | `pushbacks` が 0 | ファイルが無い・JSON が壊れている・`pushbacks` が整数でないもので None |
| `write_mark` | 書いた中身を `read_mark` で読める | 状態の根のディレクトリが無いときに作る | 書けないディレクトリで `OSError` が呼び出し元へ上がり、一時ファイルが残らない |
| `prune_marks` | 秒を過ぎた印だけを消し、数を返す | 秒ちょうどの印は残す | 消せないファイルを飛ばして残りを消す |
| `next_count` | 誤りが無ければ（偽, 0） | 回数 1 で誤りがあれば（真, 2）、回数 2 で誤りがあれば（偽, 2） | 忙しい Stop は誤りがあっても（偽, 回数のまま） |
| `pushback_reason` | 誤りの一覧と契約のファイルのパスと残りの回数を持つ | 残りが 0 の文 | 無し |
| `start` | 対象のプロンプトで印（`pushbacks: 0`）ができ、案内の文を返す | 過ぎた印が消え、新しい印は残る | 対象でないプロンプト・識別子の合わない入力で印を作らず None。書けない状態の根で `OSError` が上がる（`hook.py` が飲み込み終了コード 0） |
| `stop` | 印があり結果の行の無い応答で `decision: "block"` と誤りの一覧を返し、回数が 1 になる | 続けて 3 回目の形に合わない Stop を通し印を残す。形に合った Stop で回数が 0 に戻り、次の形に合わない Stop を再び差し戻す。`stop_hook_active` が真の入力でも回数は 1 だけ増える。形に合った Stop の後も印が残る | 印が無いセッションで何もしない。忙しい Stop で差し戻さず回数を変えない。契約のファイルが読めない・上限の宣言が読めないときに差し戻さず標準エラーへ 1 行。回数が書けないときに差し戻しを返し標準エラーへ 1 行 |
| `end` | 印が消える | 印が無くても何も出さない | 消せないときに `OSError` が上がる（`hook.py` が飲み込み終了コード 0） |
| `dispatch` | 3 つの副命令がそれぞれ部品の出力だけを返し、待ちの通知を送らない | 無し | 入力が JSON でないとき 3 つとも何も出さず終了コード 0 |
| `_check_shape` | プラグインの契約のファイルを通す | `when` が空の値 | `required` の値に `always` が無い・`material` に `heading` が無い・`values` が辞書でないもので `ContractError` と終了コード 2 |
| `_check_form` | `shape` の無い既存の 4 つの形を今どおり通す | 区間の報告の例の組の形（`text`・`result`・`errors`）を通す | `errors` が文字列の並びでない例の組で `ContractError` |

登録の行（`hooks/claude.json`）は関数ではないため、`test_hook_unattended.py` が `sh -c` の行を `NDF_FACTORY_RUN` の空と非空で起動して確かめる。空のときは、存在しない python のパスを渡しても終了コード 0 で何も出さない（Python を起動していない）。既存の hook・問いの区分・`ndf-next` のブロックのテストのファイルは変えない。
