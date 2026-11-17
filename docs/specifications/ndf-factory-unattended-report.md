# 人のいない実行の区間の報告と、報告の無い応答の差し戻し

Factory（Neural Development Factory）の実行ジョブで動く `/ndf:development-workflow` の conductor は、人へ問い返さずに
工程を進め、区間を終える最後の応答の末尾に、決まった形の**区間の報告**を置く。Factory のランナーは報告の結果の行の
値で区間の結果を決める。報告を書かずに応答を終えようとすると、Stop の hook が差し戻して書かせる。人がいる会話の
振る舞いは変わらない。

例: 標準のモードの課題 #12 を Factory が `claude -p "/ndf:development-workflow #12"` で流した。

| 順 | 起きること |
| --- | --- |
| 1 | UserPromptSubmit の hook（`hook.py unattended-start`）が、`NDF_FACTORY_RUN` があり 1 行目が `/ndf:development-workflow` なのを見て、セッションに人のいない実行の印を付け、決まりの案内を conductor の文脈へ渡す |
| 2 | conductor は「`.ndf/` をコミットするか」のような区分の無い問いを自分で決め、`ask.py decide --question` で記録して続ける |
| 3 | 設計 Pull Request を作り、設計承認ゲートに着く。承認資料を本文の「## 判断の材料」の節に書き、その後ろに区間の報告（`- 結果: 判断が要る`・区分 `ndf-gate:1`・承認ゲート・Pull Request の URL）を置いて応答を終える |
| 4 | Stop の hook（`hook.py unattended-stop`）が報告を契約の形と照らし、合っているので通す。Factory のランナーは `判断が要る` を読み、人の答えで次の区間を始める |

3 で conductor が報告を書かずに「確認したいこと」を並べて終えようとすると、4 の hook は形の誤りの一覧を添えて
差し戻す。

**区間の終え方の手順は Skill が、報告の形は契約のファイルが正である。** ここに書き写さない。

| 何を読むか | 正本 |
| --- | --- |
| 区分ごとの区間の終え方、判定結果の後に続けること、`.ndf/ask.json` を当てないこと | `plugins/ndf/skills/development-workflow/references/asking.md` の「人のいない実行」 |
| 区間の報告の形（結果の値・項目・値ごとに要る項目・材料の書き方・例の組） | `plugins/ndf/contracts/forms/unattended-report.json` |
| 結果の行の読み方（最後の `- 結果:` の行） | `plugins/ndf/contracts/forms/ndf-next-block.json` の `rule` の後半 |
| 自走の入口と conductor の報告 | `development-workflow` の `SKILL.md` の「自走で工程を通す」、`references/agent-layers.md` の「報告の形」 |
| hook の一覧 | [docs/ndf-plugin-reference.md](../ndf-plugin-reference.md) の hook の節 |

## コンテキスト

| コンテキスト | 何の語が 1 つの意味に決まるか |
| --- | --- |
| 開発の工程（`ndf-workflow`） | conductor・承認ゲート・問い合わせの区分・区分の無い問い・区間の報告の書き方 |
| Factory との受け渡し（`ndf-factory`） | 実行ジョブ・区間・人のいない実行・結果の行・区間の報告の形 |

関係は**公開された言語**である。開発の工程が報告の書き手、別のリポジトリにある Factory のランナーが読み手で、
読み手は契約のファイルの例の組を自分のテストで読む。`ndf-next` のブロック（`ndf-next-block.json`）と同じ関係である。

## 用語

用語の定義は用語集（[docs/glossary.md](../glossary.md)。正本は `docs/glossary/glossary.json`）にある。この仕様が使う
語は、人のいない実行・結果の行・区分の無い問い・区間の報告・人のいない実行の印・結果の行の差し戻しの 6 つである。

## 背景

人のいない実行では `AskUserQuestion` が出ない（許可を受け取る相手の無い `claude -p` は Tool を出さない）。conductor は
問いを本文に書き、「進めて」と返してもらえれば続けると書いて区間を終えていた。最後の応答に `- 結果:` の行が無く、
Factory はその区間を失敗として扱う。直す場所は NDF の側で、Factory のランナーのプロンプトでは補わない。

文書の決まりだけでは LLM の応答がばらつくため、報告の有無と形を hook で確かめる。

## 対象範囲

| 含む | 含まない |
| --- | --- |
| Claude Code の、人のいない実行の conductor のセッション（5 つのモードと pace のすべて） | Codex の hook |
| 区間の報告の形の契約と、その読み手 | Factory のランナーの起動と読み方の変更 |
| UserPromptSubmit・Stop・SessionEnd の hook | `NDF_FACTORY_RUN` の無い実行の振る舞い |

pace が MVV 判定で承認ゲートを通す場合は今どおり通し、区間の報告は次の承認ゲートか終わりで書く。

## 決定と理由

| 決定 | 理由 |
| --- | --- |
| 区間の報告の契約を新設し、結果の行の読み方は `ndf-next-block.json` の規則を指す | 読み方を 2 か所に持たないため。カットポイントのブロックとは書く時点と読み手の振る舞いが違い、1 つの契約に混ぜると既存の例の組の意味が変わる |
| 承認ゲートの外の承認を求める区分の問いも `判断が要る` で終え、`- 区分:` で見分ける | Factory はこの値を「人の答えを待って止まった」と読む。値を増やすとランナーの読み方も増える |
| 差し戻しの対象は UserPromptSubmit が付ける印で決める | `NDF_FACTORY_RUN` は worker やレビューの `claude -p` へも継がれる。環境変数だけで決めると、報告を書かない子の応答まで差し戻す |
| 形に合わない Stop が続く間の差し戻しは 2 回まで。回数は印に持ち、形に合った Stop で 0 に戻す | 3 回目以降は同じ応答の繰り返しになりやすい。`/goal` の続きでは 1 つのセッションに Stop が何度も起き、`stop_hook_active` は常に真になるため回数を表さない |
| hook は結果の値だけでなく、値ごとに要る項目と材料の書き方まで確かめる | 項目の欠けた報告では人が判断の材料を読めない。項目の有無は行の形で決まり、決定的に確かめられる |
| 登録の行で `NDF_FACTORY_RUN` を sh だけで確かめ、続きは hook 用の仮想環境ではなく `python3` で起動する | 人がいる会話で Python を起動しないため。仮想環境は新しいコンテナの最初のプロンプトでまだ無いことがあり、最初の区間で印が作られなくなる |
| 構造化出力のフラグ（`claude -p --json-schema` など）を使わず、応答の文の契約で受け渡す | フラグはランナーの起動の変更を要する。応答の文なら人が読む報告と機械が読む値が同じ文に載る |
| 区分ごとの扱いは `asking.md` の節に置き、`SKILL.md` と `agent-layers.md` はそこを指す | 問いの扱いの正本を 1 つに保つため |

## 仕様

### 区間の報告

conductor は区間を終える最後の応答の**末尾**に見出し「## 区間の報告」の節を置く。`ndf-next` のブロックは節の後ろに
置いてよい。判断の材料（承認資料、または問いの判断に要る事実・得失・推奨）は、同じ応答の「## 判断の材料」の節に書き、
報告の前に置く。ランナーのコンテナの中のファイルのパスは区間の後に人が開けないため、材料にしない。

| 項目 | 値 | 空のとき |
| --- | --- | --- |
| 結果 | `判断が要る` / `完了` / `できなかった` | 空にしない |
| 課題 | `#番号` を空白区切り | 空にしない |
| 区分 | `asking.md` の区分の名前（`ndf-gate:1`・`ndf-ask:redline:C4`・`ndf-ask:human-action` など） | `無し` |
| 承認ゲート | `設計 Pull Request のマージ` / `設計承認ゲートの委任の承認` / `本番の系へ届く操作` | `無し` |
| 問い | 人に決めてほしいことを 1 行 | `無し` |
| Pull Request | URL を空白区切り | `無し` |
| 材料 | `本文`（「## 判断の材料」の節がある）か、`https://` の URL を空白区切り | `無し` |
| 人に頼むこと | 人にしかできない操作を 1 行 | `無し` |
| 理由 | 1 行 | `無し` |

値ごとに要る項目（`無し` にしない項目）:

| 結果 | 要る項目 | 条件つきで要る項目 |
| --- | --- | --- |
| `判断が要る` | 区分・問い・材料 | 区分が `ndf-gate:` で始まれば承認ゲート。承認ゲートが `設計 Pull Request のマージ` なら Pull Request |
| `完了` | — | — |
| `できなかった` | 理由 | 区分が `ndf-ask:human-action` なら人に頼むこと |

読み方:

- 結果の行は、応答の文の最後の `- 結果:` の行の値（前後の空白を除く）。`ndf-next-block.json` の規則の後半と同じで、
  プラグインの中の置き場は `lib/unattended_report.py` の `RESULT_RE` だけである
- 項目は、最後の結果の行より後ろの `- <項目>: <値>` の行。同じ項目が 2 つあれば後ろを読む。行の中の空白は空白と
  タブだけで、空の値を次の行で補わない
- 材料が `本文` なら、最後の結果の行より前に「## 判断の材料」の行が要る。`本文` でも `https://` の URL でもない値
  （ファイルのパス・`http://` など）は形の誤りとする
- 値・項目・値ごとに要る項目・材料の書き方は契約のファイルの `shape` だけが持ち、読み手のコードはそこから読む

### 区分ごとの区間の終え方

| 場面 | 区間 | 結果 |
| --- | --- | --- |
| 区分の無い問い | 終えない | 決めて `ask.py decide --question` で記録し、続ける |
| 承認ゲート（pace が MVV 判定で通さないとき） | 終える | `判断が要る` |
| 承認を求める区分の問い（レッドライン・費用・取り消せない操作・規約への同意・`extra` の区分） | 終える | `判断が要る` |
| 人の操作が要る問い（`ndf-ask:human-action`） | 終える | `できなかった` |
| 工程を最後まで通した・カットポイント | 終える | `完了`（カットポイントでは `ndf-next` のブロックを添える） |
| 続けられない（スクリプトの失敗・権限の不足） | 終える | `できなかった` |

- 判定結果（`mode:`〜`次のコマンド:`）を出したところで区間を終えず、同じ区間で `次のコマンド:` を打って続ける
- `.ndf/ask.json` の `allow` と `mode` は人のいない実行に当てない。外した区分を AI が決めると、人のいない所で
  レッドラインや費用の操作を AI が承認することになるためである（共通原則の C1〜C8）

### hook

登録は `plugins/ndf/hooks/claude.json` にあり、3 つの行はどれも
`sh -c '[ -n "${NDF_FACTORY_RUN:-}" ] || exit 0; …; exec python3 "$ROOT/scripts/hook.py" unattended-<副命令>'` の形である。
プラグインの根（`CLAUDE_PLUGIN_ROOT` か `PLUGIN_ROOT`）が空か `python3` が無ければ何もせずに終える。

| 事象 | 副命令 | 関数（`hook_lib/unattended.py`） | 働き |
| --- | --- | --- | --- |
| UserPromptSubmit | `unattended-start` | `mark_session` | 期限を過ぎた印を消す。プロンプトの 1 行目が `/ndf:development-workflow`（前に `/goal ` を許す）で、その後が空白か行末なら印（回数 0）を作り、案内を `additionalContext` で返す |
| Stop | `unattended-stop` | `review_stop` | 印のあるセッションで、最後の応答（`last_assistant_message`）を判定し、差し戻すなら `{"decision":"block","reason":…}` を返す |
| SessionEnd | `unattended-end` | `clear_mark` | 印を消す |

Stop で差し戻す誤りは 2 種類である。

| 誤り | 判定する関数 |
| --- | --- |
| 区間の報告の形に合わない（結果の行が無い・3 つの外の値・要る項目の欠け・材料の書き方） | `lib/unattended_report.py` の `report_errors` |
| `次のコマンド:` にコマンド（空でも、バッククォートを外して `無し` で始まる値でもない）が載ったまま、結果が `判断が要る` でも `完了` でもない。区分が `ndf-ask:human-action` なら誤りにしない | `hook_lib/unattended.py` の `pending_command_errors` |

差し戻すかの判定（`next_count`）:

| 条件 | 差し戻すか | 回数 |
| --- | --- | --- |
| `background_tasks` か `session_crons` が空でない | 差し戻さない | 変えない |
| 誤りが無い | 差し戻さない | 0 に戻す |
| 誤りがあり、回数が上限未満 | 差し戻す | 1 足す |
| 誤りがあり、回数が上限以上 | 差し戻さない | 変えない |

差し戻しの文は、誤りの一覧・契約のファイルのパス・`asking.md` の節・残りの回数を持つ。印は Stop では消さず、
セッションの終わりまで残す。同じ Stop を別の hook（`/goal` の条件の判定など）が差し戻しても、NDF の回数は
自分が差し戻したときだけ増える。

案内の文は、人のいない実行であること・区分の無い問いは決めて `ask.py decide --question` で記録して続けること・
区間の終え方の正本（`asking.md` の節と契約のファイルのパス）を指す。形の表は写さない。

### 常に成り立つ条件

| 条件 | 破れたときの扱い |
| --- | --- |
| 同じ応答の文に対して、区間の報告の読み手と `ndf-next` の読み手は同じ結果の値を返す | テストが落ちる |
| 印が付くのは、`NDF_FACTORY_RUN` が空でなく 1 行目が `/ndf:development-workflow` のセッションだけである | — |
| `NDF_FACTORY_RUN` が空の実行では、hook は環境変数を 1 回読んで終わり、Python を起動しない | — |
| hook のどの失敗も UserPromptSubmit と Stop を止めない側へ倒す | 標準エラーへ `ndf: unattended-<副命令>: <理由>` の 1 行を残し、終了コード 0 |
| `hook.py` から人のいない実行の部品までの import の経路は標準ライブラリだけを読む | `python3 -S` で打つテストが落ちる |

## データ・設定

### 人のいない実行の印

置き場は `<状態の根>/unattended/<session_id>.json` で、状態の根は `lib/state_home.py` の `container_home` が決める。
契約の項目は `plugins/ndf/contracts/files/unattended-mark-json.json` である。

| キー | 型 | 意味 |
| --- | --- | --- |
| `session_id` | 文字列 | Claude Code のセッションの識別子（`^[A-Za-z0-9-]{1,128}$` に合わなければ印を作らない） |
| `started_at` | 文字列（UTC の ISO 8601） | 印を作った時刻 |
| `pushbacks` | 0 以上の整数 | 形に合わない Stop が続いた間に差し戻した回数 |

書き込みは `jsonio.write_atomic` で原子的に行う。読めない・形が違う印は無いものとして扱う。

### 上限の宣言（`plugins/ndf/contracts/limits.json`）

| 方針 | 値 | 意味 |
| --- | --- | --- |
| `hook.unattended-pushback` | `attempts: 2` | 形に合わない Stop が続く間に差し戻す回数の上限 |
| `hook.unattended-mark` | `seconds: 604800` | SessionEnd で消し残した印を、次の `unattended-start` が消すまでの秒（7 日） |

### 契約のファイルの検査

`scripts/contracts_lib/model.py` の `_check_form` は受け渡しの形に任意のキー `shape` を許し、`_check_shape` が
キーと型を確かめる。例の組の形 `{"text", "result", "errors"}` は `CASE_SHAPES` にある。読み手の
`form_from_dict` も実行時に同じ観点で確かめ、壊れていれば `FormError` を上げる（Stop の hook は差し戻さずに通す）。
`plugins/ndf/contracts/env/NDF_FACTORY_RUN.json` と `hook.py` のエントリポイントの項目が、環境変数と印のファイルを
宣言する。

## エラー処理

| 失敗 | 扱い |
| --- | --- |
| 印が書けない（UserPromptSubmit） | 標準エラーへ 1 行。案内は渡らない。次のプロンプトで作り直す |
| 契約のファイルか上限の宣言が読めない（Stop） | 差し戻さず、回数も変えない。標準エラーへ 1 行 |
| 印が無い・壊れている（Stop） | 何もしない |
| 回数が書けない（Stop） | 差し戻しは返し、標準エラーへ 1 行。回数が増えない間は Claude Code の上限（8 回続けて）が上限になる |
| 印が消せない（SessionEnd） | 標準エラーへ 1 行。次の `unattended-start` が期限を過ぎた印を消す |
| 標準入力が JSON でない | 3 つの副命令とも何も出さない |

## 運用

### 未確認の前提

| 項目 | 内容 |
| --- | --- |
| Factory のランナーの読み方 | 最後の結果の行の値で区間の結果を決め、行が無ければ失敗とすることは、ランナーのリポジトリにあり NDF の側では確かめていない |
| ランナーの起動の形 | `--bare` を付けるとプラグインの hook が動かず、案内も差し戻しも働かない。`stream-json` に差し戻しが現れるか、印のファイルが作られるかで確かめる |
| 背景の作業を残した Stop の後 | 背景の作業か予定が残る Stop は差し戻さない。`claude -p` が作業の完了を待たずに `result` を返す場合、結果の行の無い応答が差し戻されずにランナーへ届く。実測していない |
| conductor の振る舞い | 区分の無い問いを決めて承認ゲートまで進むかは LLM の応答に依り、リリース後テストで確かめる |

## テスト観点

| 観点 | テスト |
| --- | --- |
| 読み手が `unattended-report.json` の例の組のすべてで宣言どおりの値と誤りを返し、`ndf-next-block.json` の例の組でも同じ結果の行を読む | `plugins/ndf/scripts/tests/test_unattended_report.py` |
| 要る項目を契約のファイルから読む（`shape.required` を書き換えると判定が変わる）、材料は本文か `https://` の URL、承認ゲートでは承認ゲートの名前と Pull Request を求める | 同上 |
| 壊れた `shape` を読み手と契約の検査の両方が拒む | 同上と `scripts/tests/test_check_ndf_contracts.py` |
| 対象のプロンプトだけに印が付き、ほかのセッションには付かない | `plugins/ndf/scripts/tests/test_hook_unattended.py` |
| 形に合わない Stop を続けて 2 回差し戻し、3 回目を通す。形に合った Stop で回数が 0 に戻り、印は残る | 同上 |
| `次のコマンド:` を打たずに終える Stop を差し戻し、`判断が要る`・`完了`・人の操作待ちは通す | 同上 |
| 背景の作業がある Stop・印の無い Stop・契約か上限の宣言が読めない Stop は差し戻さない | 同上 |
| `NDF_FACTORY_RUN` が空なら登録の行が Python を起動しない。hook 用の仮想環境が無くても、`python3 -S` で 3 つの副命令が動く | 同上 |
| 標準の課題を流すと設計承認ゲートで `判断が要る` を書き、区分の無い問いが `ask.py summary` の `metrics.decision.no_ref` に数えられる。人の操作が要る課題・費用の出る課題で、それぞれ `できなかった`・`判断が要る` を書く | リリース後テスト（LLM の応答に依るためユニットテストでは縛らない） |

## 関連リンク

- [ndf-ask-guard.md](ndf-ask-guard.md) — 人がいる会話の `AskUserQuestion` の区分と問い合わせの記録
- [ndf-agent-layers-unattended-run.md](ndf-agent-layers-unattended-run.md) — conductor / supervisor / worker の 3 層の運転
- [ndf-relay-goal-skill-and-prose-wait.md](ndf-relay-goal-skill-and-prose-wait.md) — `/goal` の続きと `stop_hook_active`
- Claude Code の hook の仕様: https://code.claude.com/docs/en/hooks.md
- Claude Code の非対話の実行: https://code.claude.com/docs/en/headless.md
