# NDF の作り直しの物差し: 行き先を選んだ主体と使用量を、入れ替えの前後で同じコマンドで数える

#1142 の作り直しの目的のうち「分岐と例外の行き先をスクリプトが決める」と「使用量を減らす」を、入れ替え（S2 #3304、11.0.0）の前後で同じコマンドで測る物差しの仕様である。物差しは G1〜G3（行き先を conductor が選んだ回）と U1〜U3（PR 1 本あたりの費用・使用量が残る割合・所要）の 6 つで、`scripts/measure/` の 3 本のスクリプトと 2 つの表が数え方を固定する。作り直しの索引は [ndf-rebuild.md](ndf-rebuild.md) にある。

## 概要

**例: 止まったプランの再開を、前の形と後の形で同じ規則に数える。** 2026-11-12、レーン lrel は CI の監視が「CI 以外の理由」で止まったプランを手で組み直し、`supervise.py run 6-impl-3058.json --from merge` を打った（#3153）。10 系のこのコマンドは規則の表の前の形の行（`form: before`）に当たり、規則 R03（止まったプランの再開点を選ぶ）の 1 回になる。11 系で同じ場面の conductor は `ndf plan next <プラン> --from merge` を打つ。このコマンドは後の形の行（`form: after`）に当たり、同じ会話の直前の `ndf plan` の結果の `cause.kind` が `base_conflict` なら R10、承認ゲートの後なら R13、ほかの停止なら R03 に寄る。どちらの形も規則の番号で数えるため、コマンドの名前とパスが変わっても前後を比べられる。

数えないものもある。プランが止まった後の自動の遷移は、プランの実行が選択の記録（`route_chosen`）を `chooser: script` で書く。conductor が結果 JSON の `next` のとおりに打ったコマンドも、行き先を選んだのはスクリプトなので数えない。数えるのは conductor が自分で行き先を選んだ回だけである。

| # | 物差し | 数えるもの | 目標（入れ替えの後の 30 日） | 前の値の出所 |
| --- | --- | --- | --- | --- |
| G1 | conductor が選んだ回（スクリプト化「できる」の 11 規則） | R06・R07・R09・R10・R13・R15・R16・R17・R19・R22・R24 | 0 | scripted-flow の洗い出しの一覧（2026-10-14〜11-13 の 30 日の R01〜R27 の回数を判定ごとに合計） |
| G2 | conductor が選んだ回（「一部できる」の 12 規則） | R01〜R05・R08・R11・R12・R18・R20・R23・R25 | 前の値の半分以下（3,296 以下） | 同上 |
| G3 | conductor が選んだ回（「できない」の 4 規則のうち数えられる 2 規則） | R14・R21 | 0（スクリプトが判断の呼び出しとして呼ぶ） | 同上 |
| U1 | PR 1 本あたりの費用（換算。input を 1 とする） | 会話に残る 3 層と、計画が起動した `claude -p` の費用の和 ÷ 一意な PR の数 | 前の値の 80% 以下（1.14M 以下） | #1142 のコメント「移行の前の基準」（2026-09-25、10.17.25） |
| U2 | 計画が起動した `claude -p` のうち使用量が残る割合 | 使用量の行が残った起動 ÷ 計画が起動した `claude -p` | 100% | 同上 |
| U3 | PR 1 本あたりの所要 | 一意な PR を持つ会話の所要の和 ÷ 一意な PR の数 | 前の値以下 | 同上 |

G2 の目標は、機械で決まる分をスクリプトへ移すと洗い出しの一覧が見込む半分である。U1 の目標は、conductor の分のうち選ぶ回の減る分（G1・G2 の目標で 64% 減）を半分も見込まない値にした。R26・R27 は数えられない（下の「規則ごとの出所」）。

## 用語

目的の物差しと選択の記録の語は[用語集: NDF の作り直し](../glossary/ndf-rebuild.md) が持つ。この文書だけで使う語は次のとおりである。

| 語 | 意味 |
| --- | --- |
| 形 | 記録とコマンドの世代。`before` は入れ替えの前（10 系のコマンド・旧い進捗の記録・並列の計画の台帳）、`after` は入れ替えの後（11 系のコマンド・選択の記録） |
| 規則 | scripted-flow の洗い出しの一覧の 1 行（R01〜R27）。AI が分岐と例外の行き先を決めている箇所で、前後の形を突き合わせる鍵 |
| 出所 | 規則と形ごとに、G に足す回数を取る記録の種類（`transcripts`・`progress`・`ledger`・`records`・`none`） |
| conductor の回 | 会話の記録で役割が conductor で、結果の `next` か `routes` のとおりでない回（出力の `value`） |
| 役割で絞らない回 | 役割を問わず当たりを足した回（出力の `all_roles`）。入れ替えの前の手の表と照らすための値 |

物差しの G1〜G3 は子の記号の G1（#3618）とは別のものである。この文書の G1〜G3・U1〜U3 は物差しだけを指す。

## 背景

コマンドの文字で数えると、作り直しでコマンドの名前とパスが変わった時点で回数が 0 に見える（#3395 の設計レビューの項目 3395-1）。行き先を選んだ主体（スクリプト・conductor・人）を数えれば、名前が変わっても前後を比べられる。そこで #3395 は選択の記録の契約（D1+ #3284）を置き、#3412 は前の形の 10 系のコマンドと後の形の 11 系のコマンドを同じ規則の番号へ寄せる表を足した。

G1〜G3 の前の値は、規則の名前を洗い出しの一覧の番号へ手で寄せて足した値である。#3412 はスクリプトに規則の番号を持たせ、同じ期間を直したスクリプトで打ち直した値も並べる。前と後の比べはスクリプトの値どうしで行う。

**要求の受け入れ条件。** #1142 の受け入れ条件のうち計測と振り返りの 3 つは、この文書が仕様として持つ（[ndf-rebuild.md](ndf-rebuild.md) の「要求の受け入れ条件のオーナー」）。

| #1142 の受け入れ条件 | この文書の持ち方 |
| --- | --- |
| マイルストーン 26 の改善効果（スプリントごとの費用・トークン・所要時間・worker の固定費・検査とレビューの回数と指摘・流出不具合）の計測と振り返りが #1142 のコメントに残る。測れなかった値は理由と一緒に残る | 移行の前の値は #1142 のコメント「移行の前の基準」が持つ。U1〜U3 はその値を前の値にする |
| 計測に使ったスクリプトがリポジトリにあり、同じコマンドで打ち直せる | 「打つコマンド」の 3 本。期間を固定すれば後日打ち直しても同じ値になる |
| 移行の後に同じ物差しで測り直し、移行の前の基準と比べた結果が残る | 「測り直しの回」の 2 回。結果はこの文書と #1142 のコメントに同じ表で残す |

## 対象範囲

- **含む**: `scripts/measure/ai-branching.py` と規則の表 `ai-branching-rules.json`・分類の表 `ai-branching-measures.json`、`scripts/token-usage.py` の期間と一意な PR、`scripts/measure/claude-p-usage.py` の副命令 `launches`・`plan-cost`・`measure`。数える入力は、会話の記録・プランの進捗の記録（`progress.jsonl`）・並列の計画の台帳・使用量ログ
- **含まない**: CI の検査（ラチェット）。会話の記録は利用者の手元にしか無く、CI で読めない。構造の物差し（`structure-baseline.py`）は [ndf-rebuild-libraries.md](ndf-rebuild-libraries.md)、AI が陥りやすい作りの数（`ai-smells.py`）は [ndf-rebuild-data-and-smells.md](ndf-rebuild-data-and-smells.md) が持つ
- **プロジェクトごとに違うもの**: 無い。計測のスクリプトは配らず、このリポジトリの根に置く（[ndf-rebuild-libraries.md](ndf-rebuild-libraries.md) の決定 12）。対象のリポジトリは引数 `--repo` で受ける

## 仕様

### 規則の表と分類の表

規則の表 `ai-branching-rules.json` は 1 行 1 つの当たりの型で、次の項目を持つ。

| 項目 | 値 |
| --- | --- |
| `rule` | 規則の番号（`R` と 2 桁以上の数字） |
| `form` | `before` か `after` |
| `kind` | `bash`（`pattern` の正規表現で Bash のコマンドを照らす）・`tool`（`tool` の名前。任意で `source_prefix` が `metadata.source` の接頭辞を絞る）・`phrase`（tool_result に `phrase` の文が現れたら数える）・`cause`（tool_result の結果 JSON の `cause.kind` が `cause` の値。任意で `result_tool` が結果の `tool` を絞る） |
| `name` | 当たりの説明 |
| `measured` | 省けば真。偽の行は、洗い出しの一覧の「記録上の回数」の先頭の数に入らない当たりで、`by_rule` の `reference` に出し、G・`value`・`all_roles` に足さない |

後の形の行は、11 系のコマンド（`ndf plan next … --from`・`ndf plan queue … --from`・`--resume`・`ndf plan wait`・`ndf lanes wait`・`ndf lanes start`・`ndf ci watch`・`ndf ci merge`・`ndf records mvv gate`・`ndf records handoff`・`ndf worktree check`・`ndf loop`）と、`gh pr checks`・`gh run rerun` などの手打ち・`AskUserQuestion`・`SendMessage` を拾う。11 系のコマンドを足すたびに、この表へ後の形の行を足す。

分類の表 `ai-branching-measures.json` は、規則の番号ごとに `measure`（`G1`・`G2`・`G3`・`none`）と、形ごとの出所 `before`・`after` を 1 つずつ持つ。`measure` は洗い出しの判定（できる → G1、一部できる → G2、できない（数えられる 2 規則）→ G3、ほか → `none`）である。規則の表にある番号が分類の表に無いと、スクリプトは終了コード 2 で止まる。

### 規則ごとの出所

G に足すのは、規則と形ごとに決めた出所 1 つの回数だけである。ほかの出所の当たりは `by_rule` に参考として出すが足さない（会話と記録の両方に跡がある規則を 2 回数えない）。

| 出所 | 数えるもの | 当てる規則 |
| --- | --- | --- |
| `transcripts` | 規則の表のその番号と形の行の当たり。conductor の回だけを `value` に入れる | 前の形は、洗い出しの一覧の「記録上の回数」の先頭の数が会話の数の規則。後の形は R02・R03・R05〜R13・R15・R16・R18・R22 |
| `progress` | 旧い `progress.jsonl` の数えのうち、`counter` の欄と `keys`（一致）か `prefix`（前方一致）で選んだ値の合計 | 前の形の R14（`kinds` の `slow`）・R17（`judge_by_decision` の `gate → `）・R20（`judge_tests_by_decision` の `judge → `）・R21（`stopped_by_step_exit` の `impl exit=1`・`design exit=1`）・R23（`attention_by_reason` の「スプリント MVV を使えない」） |
| `ledger` | 旧い台帳の数えのうち、同じ選び方の値 | 前の形の R25（`resume` の `settle: rejected`） |
| `records` | 選択の記録のうち、その番号で `chooser` が `conductor` の行 | `chooser: conductor` を書く書き手がある規則だけ。11.0.13 にその書き手は無く、どの規則にも当てない |
| `none` | 数えない。`unmeasured` に理由を書き、その規則を含む物差しに `partial: true` を付ける | 後の形の R01・R04・R14・R17・R19・R20・R21・R23・R24・R25（11.0.13 に、選択を `chooser: conductor` で書く書き手も、会話の記録で拾う 11 系のコマンドの規則も無い）と、両方の形の R26・R27 |

`none` を 0 として G に足さないのは、測れない規則が目標に届いたように見えるからである。R26（worker が返した判断の要る事柄）は、結果ファイルが worktree ごとに散って作業の後に消え、会話の中の報告の見出しは worker の手順書の引用と区別できない。R27（モードの判定）は会話の本文に残り、Tool の呼び出しにならない。

### 前の形の数え方

- **プランの記録**: `--ndf` の下と `--state` の下のすべての `progress.jsonl` を 1 回ずつ読む。実行の区切りと前の実行の終わり方はファイルの全行から組み、数えるのは期間の内の行だけである。`run` の行の `from`（途中からの再開）を直前の実行の終わり方で分け、`attention` を理由ごとに、`judge` のステップを決定ごとに、行を `kind` ごとに数える
- **並列の計画の台帳**: `<ndf>/parallel/*/ledger.jsonl` の `ask` を `source` ごとに、`resume` の `take` と `settle` の `result` を数える
- **会話の記録**: `assistant` の行の `tool_use` を id で 1 度だけ数え、規則の表の行で照らす。1 つのコマンドが 2 つの規則に当たれば、それぞれに数える

会話の数は、NDF そのものを開発した会話（同じコマンドを調べた回）を含む上限の値である。回数は「AI が出力を読んで次の手を決めた跡」の件数で、すべてが誤りではない。

**R20 の判断の行。** `judge_by_decision` のキーは `<ステップの id> → <decision か next>` である。R20（落ちたテストを直すか先へ進むか）に足すのは、ステップの id が `judge` のキーのうち、同じ実行の中で直前にある `kind: step` の行で分けたテストの判断だけである。

| 直前のステップの行 | 分け先 |
| --- | --- |
| `step: pr`（`next: judge`。PR の本文が「設計と違う点」の節を欠いたときの突き合わせの問い） | `judge_design_diff_by_decision`（R20 に足さない） |
| ほかのステップ（`test-limited`・`test-all`・`merge` など） | `judge_tests_by_decision`（R20 に足す） |
| 同じ実行に無い（`--from judge` で再開した実行の先頭） | 前の実行の最後の `kind: step` の行で上の 2 行のどちらかに分ける |
| 前の実行にも無い | `judge_unclassified_by_decision`。R20 の `unresolved` に件数を出し、R20 を含む物差しに `partial: true` を付ける |

`gate → …` は R17 に、遅れの見張りは `kinds` の `slow` で R14 に数える。`judge-record → …`（リリース記録を書き直すか）と `judge-test → …`（手で書く単独プランの雛形）は、洗い出しの一覧のどの規則にも無いので数えない。`prefix` の `judge → ` は矢印の前後の空白まで含めて照らす。

### 後の形の数え方

- **選択の記録**: 同じ `progress.jsonl` の `event: route_chosen` の行を、`plugins/ndf/contracts/schemas/route-chosen.schema.json` をファイルとして読んで確かめ、規則の番号と `chooser` ごとに数える。形の合わない行と `at` を時刻に直せない行は数えずに `rejected` へ件数を出す
- **会話の記録**: 規則の表の後の形の行で照らす。Bash は `&&`・`;`・`|`・改行で区切った 1 つずつのコマンドで照らす

**再開のコマンドの寄せ方（R03・R10・R13）。** 11 系の再開は `ndf plan next <プラン> --from <ステップ>`（`--answer`・`--set`・`--fresh` を伴うこともある）と `ndf plan queue … --from <プラン>=<ステップ>`・`--resume` の 2 つで、どちらも選択の記録を書かない。そこで後の形は 1 つの正規表現（番号 R03）で拾い、同じ会話の同じプランを対象にした直前の `ndf plan`（`next`・`queue`・`wait`）の結果 JSON で、対象ごとに 1 つの規則へ寄せる。プランの対象は、会話の行の `cwd` と同じ呼び出しの `cd` から引いた絶対のパスと、`--state-dir` の保存先の組で決める。

1. 対象の行の `status` が承認ゲート（`gate`）か、直前に conductor が打った同じプランの `ndf plan next` が `--answer` を持つ → R13
2. 対象の行の `cause.kind` が `base_conflict` → R10（プランの実行が R10 とする条件と同じ）。`queue` の結果のように行が `cause` を持たないときは、トップレベルの `summary` が `<対象のプラン>: ` で始まるか、`status: stopped` の行が対象の 1 本だけのときに限り、トップレベルの `cause.kind` を対象の原因とする
3. 対象の行が `stopped` で 2 の条件で原因を読めない → 寄せ先を決めず、根拠 `cause_unknown` で R03 の `unresolved` に数え、R03 と R10 を含む物差しに `partial: true` を付ける
4. ほか（`base_conflict` 以外の停止・止まっていない結果・同じ会話に直前の結果が無い）→ R03

`ndf plan queue … --resume` の対象は、キューの記録の置き場（`--queue-dir`。省けば先頭のプランの隣の `<名前>.queue`）の結果で `status` が `ok` でないプランである。1 つのコマンドが複数の対象を持てば対象ごとに 1 回として数える（`--from a=impl --from b=merge` は 2 回）。`--from` を持たず `--answer` だけの `ndf plan next` は承認の回答そのもので、規則に数えない。寄せ先の根拠（`gate`・`answer`・`base_conflict`・`stopped`・`cause_unknown`・`no_prior_result`・`other`）は `by_rule` の内訳に出す。保存先を宣言するプランで結果 JSON に保存先が載らない経路は、前の結果に結べず `no_prior_result` で R03 に寄る（このリポジトリに宣言するプランは無い）。

**`next` のとおりに打った回。** conductor の Bash のコマンドが、同じ会話の直前の結果 JSON の `next`、または承認ゲートの結果の `routes` の各コマンドのどれかのとおりなら（`shlex` で分けた引数がそのコマンドの `argv` で始まり、残りがその `ask` のキーの `--<キー> <値>` の組だけ）、`follows_next` を真とし `value` に入れない。判定は `ndf.contracts.chosen.chooser` と同じ規則を計測の側に写したもので、両者に同じ見本を通すテストが一致を縛る。

### 会話の記録の役割

数える会話の記録は Claude Code の JSONL だけである（conductor は claude で動き、codex と kiro は cross 系の相手役で conductor にならない）。

| 役割 | 決め手 |
| --- | --- |
| other（supervisor・worker） | ファイルが `<会話>/subagents/` の下にある、行の `isSidechain` が真、または行の `entrypoint` が `sdk-cli`（`claude -p` の起動） |
| conductor | 上に当たらず、`isSidechain` が偽で `entrypoint` が `cli` |
| 呼び出しから継ぐ | 行が `isSidechain` も `entrypoint` も持たない `tool_result` のとき、同じファイルの中で `tool_use_id` と同じ `id` の `tool_use` を持つ行の役割を継ぐ |
| unattributed | 上のどれとも決められない。件数を出し、`value` に入れず `all_roles` に入れる |

### 出力の形

`ai-branching.py` は JSON 1 つを標準出力へ書く。トップレベルは `since`・`until`・`progress`・`ledger`・`transcripts`（`--skip-transcripts` で省く）・`forms` で、どれも同じ期間で絞った値である。`forms` は形ごとに次を持つ。

| キー | 中身 |
| --- | --- |
| `by_rule` | 規則の番号ごとの `source`・`value`・`all_roles` と、参考の `reference`（ほかの出所の当たりと `measured: false` の行の当たり） |
| `measures` | `G1`〜`G3` ごとの `value`（conductor の回）・`all_roles`・`partial` |
| `unmeasured` | 出所が `none` の規則と理由 |
| `unresolved` | 寄せ先か判断の種類を決められなかった回（R03 の `cause_unknown`・R20 の判別できない判断）。G に足さない |
| `rejected`（`after` だけ） | 形の合わない選択の記録の行の件数 |

### 期間

期間は始まりを含み終わりを含まない半開区間 `[始まり, 終わり)` で、両端は時差つきの ISO 8601 の時刻で渡す（`2026-11-18T00:00:00+09:00` の形）。日付だけと時差の無い値は終了コード 2 で断る（機械の時間帯と UTC・JST の取り違えで期間が変わるため）。終わりは最終日の翌日の `00:00:00` である。`23:59:59` までの閉区間にすると、Claude Code の JSONL の `2026-11-13T23:59:59.123Z` のような秒未満を持つ行が最終日から欠ける。どの入力も、行の時刻を時差つきの時刻に直して比べる（`Z` と `+09:00`、秒未満の桁数の違う行が混ざるため）。

| 入力 | 時刻の欄 | 形 |
| --- | --- | --- |
| Claude Code の会話の JSONL | 行の `timestamp` | UTC・ミリ秒（`2026-11-20T14:59:59.123Z`） |
| プランの進捗の記録・選択の記録 | 行の `at` | 時差つき（スキーマは秒未満を任意で受ける） |
| 使用量ログ（`ndf records usage`） | 行の `at` | UTC・秒まで・`+00:00`（`2026-11-21T01:42:57+00:00`） |
| 旧い進捗の記録・旧い台帳 | 行の `at`・`ts` | 入れ替えの前の書き手の形（秒まで・時差つき） |

### 測り直しの回

30 日に届かない期間でも判定を残すため、測り直しを 2 回に分ける（#3412 が足した）。後の値の期間は JST で決める（11.0.0 を出した日付が JST のため）。

| 回 | 始まり | 終わり（含まない） |
| --- | --- | --- |
| 前の値（G1〜G3 の打ち直し） | `2026-10-14T00:00:00Z` | `2026-11-14T00:00:00Z`（手の表の期間 10-14〜11-13 が UTC の日付のため） |
| 1 回目の後の値 | `2026-11-18T00:00:00+09:00` | 測る日の `T00:00:00+09:00`（打つ前に決める。当日は記録がそろわないので入れない） |
| 30 日の後の値 | `2026-11-18T00:00:00+09:00` | `2026-12-18T00:00:00+09:00`（11-18〜12-17 の 30 日。2026-12-18 以降のいつ打っても同じ値になる） |

1 回目は日数が短く、目標との比べは参考の値になる。30 日の値は 2026-12-18 以降に、同じコマンドを打って足す。

### 打つコマンド

計測はリポジトリの根の venv で打つ（`ai-branching.py` は `jsonschema` で選択の記録を確かめる）。`<始まり>`・`<終わり>` は上の表の時刻、`<一覧>` は新しいファイルのパスである。

**打つ実行元は、u3-usage の 2 本をどちらも含むリビジョンに限る。** `token-usage.py` の `--since`・`--sessions-out`・`--no-ledger` は Task 4（PR #3972 https://github.com/devbasex/ai-plugins/pull/3972 ）、`claude-p-usage.py` の副命令 `launches`・`plan-cost`・`measure` と `--counted` は Task 5（#3972 のマージ後に出す別の PR）が足す。両方がマージされる前の `sprint/ls3-spec-r1` や `main` では、下の 2 つ目と 3 つ目のコマンドが引数の誤りで止まる。打つ前に実行元のコミット（`git rev-parse HEAD`）を決め、結果に残す。そのコミットが 2 本のマージコミットをどちらも祖先に持つことを `git merge-base --independent <実行元のコミット> <Task 4 のマージコミット> <Task 5 のマージコミット>` の 1 回で確かめる。出力が実行元のコミット 1 件だけなら両方を含む。2 件以上か、実行元と違うコミットが出れば測らず両方のマージを待つ（祖先ごとに `--is-ancestor` を打つと git の起動が 1 回増える）。マージコミットはマージの後に変わらないので、`gh pr view <番号> --json mergeCommit` で 1 本につき 1 回引けば測り直しのたびに引き直さない。スクリプトの `--help` を打って確かめない（`token-usage.py` は引数を読む前に依存を確かめ、依存が無いと `--help` だけでも uv を起動して環境をそろえるため、計測のたびに CLI の起動が 2 回増える）。

```bash
env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV uv run --frozen --project . --extra jsonschema \
  python scripts/measure/ai-branching.py --since <始まり> --until <終わり> --jobs 6
env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV uv run --frozen --project . --all-extras \
  python scripts/token-usage.py --since <始まり> --until <終わり> --repo devbasex/ai-plugins \
  --by version --format json --no-ledger --sessions-out <一覧>
env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV uv run --frozen --project . --all-extras \
  python scripts/measure/claude-p-usage.py measure --since <始まり> --until <終わり> --repo devbasex/ai-plugins \
  --counted <一覧>
```

`claude-p-usage.py` は `measure` を 1 回だけ打つ。`launches` と `plan-cost` を別々に打つと、同じ進捗の記録と使用量ログを 2 回集める。

| スクリプト | 引数の要点 |
| --- | --- |
| `ai-branching.py` | `--since`・`--until` は省けない。`--ndf`（既定 `.ndf`）・`--state`（既定 `~/.local/state/ndf`）・`--transcripts`（会話の記録の根。繰り返せる。既定 `~/.claude/ndf/accounts/*/projects/-work-ai-plugins*`）・`--jobs`（既定 6）・`--skip-transcripts`。規則の表と分類の表の形が誤っていれば終了コード 2、スキーマのファイルが無ければ 3 |
| `token-usage.py` | `--since` と `--until` は同じ半開区間（`--until` ちょうどの行を数えない）。`--repo` は `<所有者>/<リポジトリ>` かそのディレクトリで、渡したときだけ PR を一意に寄せる。`--sessions-out` は U1 の分子に費用を足した会話の ID を 1 行 1 つ・昇順で書き、`--repo` と `--no-ledger` を要し、既にあるファイルには書かない。`--no-ledger` は旧い使用量の帳簿を読まない（帳簿の行は別のリポジトリの計画の分も時刻だけで会話へ寄せ、`plan-cost` の分と重なる）。`--min-version` は期間と別の条件で、物差しの値には付けない |
| `claude-p-usage.py` | 副命令 `launches`（U2）・`plan-cost`（U1 の計画起動分）・`measure`（`plan-cost` と同じ引数で 2 つを 1 回の収集で出す）。`--repo` は必須。`--state`（`progress.jsonl` を探す根。繰り返せる。既定はカレントのリポジトリの `.ndf`）・`--usage-log`（既定は `ndf records usage` の置き場の `usage.jsonl`）。旧い帳簿・会話の置き場・`gh`・`ndf` を読まない。副命令を付けない集計は 10 系の値を見るために残し、測り直しでは打たない |

### U1〜U3 の出し方

| 物差し | 式 | 読む欄 |
| --- | --- | --- |
| U1 | (Σ `pr_cost_total` + `plan_cost.value`) ÷ `unique_prs` | `token-usage.py` の JSON の `per_pr` の全行の和（版ごとに割った値を重みで戻さず、合計と一意な PR の数から割る）と、`measure` の `plan_cost` |
| U3 | Σ `pr_minutes_total` ÷ `unique_prs` | `token-usage.py` の `per_pr` |
| U2 | 使用量の行が残った起動の数 ÷ 計画が起動した `claude -p` の数 | `measure` の `u2` |

**分母は対象リポジトリの一意な PR である。** `unique_prs` は、期間の内の会話の出力に現れた `https://github.com/devbasex/ai-plugins/pull/<番号>` を番号で一意にした数で、同じ PR を複数の会話や複数の版で続けても 1 本である。その PR を持つ会話の費用と所要を 1 回ずつ足したものが `pr_cost_total`・`pr_minutes_total` で、1 つの会話が 2 本の PR を持てば、その会話の費用は合計に 1 回だけ入る。`unique_prs` が 0 なら U1・U3 は計測不能とし、理由を `reason` に書く。

**U1 の費用の範囲は前の値と同じにする。** 前の値は、会話に残る 3 層（conductor・supervisor・worker）と、計画が起動した `claude -p` を足した範囲で測った。`token-usage.py` の 3 層には会話の記録が残る `claude -p` だけが入るので、計画起動の分は、使用量ログの `caller: claude-p` の行のうち `session_id` が `--sessions-out` の一覧に無い行だけを `plan-cost` で足す（換算は `token-usage.py` の係数。使用量ログはキャッシュの書き込みを 5 分と 1 時間に分けないので 5 分の重みで数える）。`plan-cost` が `complete: false` のときは、U1 を観測下限（欠けた計画起動の分を含まない下限）として出し、目標への到達は判定不能とする。下限が目標を超えたときだけ未達と書ける。

**U2 の分母と分子は同じ起動の鍵で突き合わせる。** 分母は、対象リポジトリに属する状態ディレクトリの `progress.jsonl` の `kind: launch` の行を、起動の時刻で期間に絞ったものである。鍵は（`run_id`・ステップ・試行）で、行が `run_id`・`attempt` を持たないときは `<状態のディレクトリ>#<前の kind: run の行の数>` と同じ実行の中でそのステップが起動した回数で補う。分子は使用量ログの `caller: claude-p` の行のうち、同じ鍵を持ち `usage` が `unknown` でない行である。`kind: launch` を書かない実行・鍵の無い使用量の行・母数に無い起動の使用量の行がある期間は、U2 を計測不能とし、欠けた入力を `reason` に書く。`ndf records usage report` は期間の合計だけで起動の数も会話ごとの行も持たないので、U1・U2 のどちらにも使わない。

### 今の書き手と測れない値

11.0.13 の時点で、物差しの入力の書き手は次のとおりである。

| 入力 | 今の書き手 | 物差しへの効き方 |
| --- | --- | --- |
| 選択の記録 | プランの実行の遷移（`ndf.core.plan.transition_decide`）だけ。停止の後の自動の遷移を R03・R10 として `chooser: script`、承認ゲートの回答を R13 として `chooser: human` で書く | `chooser: conductor` の行が無いので、後の形の出所に `records` を使う規則は無い。conductor の手での再開は会話の記録にしか残らず、後の形の R03・R10・R13 の出所は `transcripts` である（`records` にすると手での再開が消える） |
| `progress.jsonl` の `kind: launch` と使用量ログの `caller: claude-p` | 無い。`ndf.domains.records.usage.record_cli` に呼び手が無い | U2 は計測不能、U1 は観測下限になる。書き手は #3975 が持つ |
| プランの記録全般 | 工程のプランが起動しない既知の不具合がある（#3908） | 選択の記録が 0 件のままなら、後の形の G1〜G3 は会話の記録の分だけになり、結果に「プランの選択は測れなかった（#3908）」と残す |

### 常に成り立つ条件

| 対象 | 条件 | 破れたときの扱い |
| --- | --- | --- |
| 計測のスクリプト | `ai-branching.py` と `claude-p-usage.py` の `launches`・`plan-cost` は `plugins/ndf/src` を import せず、選択の記録はスキーマのファイル、使用量は使用量ログの行を直に読む（決定 114） | `plugins/ndf/src` を import の道に入れない環境で打つテストが落とす |
| 規則の番号 | 前の形と後の形の同じ選択は、同じ規則の番号に 1 回ずつ数える | 前後の見本を 1 回ずつ置くテストが落とす |
| 出所 | G に足すのは規則と形ごとの出所 1 つの回数だけで、出所が `none` の規則は 0 でなく `unmeasured` に出る | 会話と記録の両方に跡がある規則の見本で 2 回数えると落ちる |
| 期間 | どの入力も同じ `[since, until)` で絞り、`until` ちょうどの行を数えない | 境界の見本で落ちる |
| 数え方 | 物差しの数え方（規則の正規表現・文の一覧・期間）はスクリプトと表に固定し、前後を同じコマンドで打つ。表を直したら前の値も同じ表で打ち直して並べる | 測り直しの結果の表で、前の値の行に手の表の値と打ち直しの値を並べる |

## 決定と理由

### 決定 114: 入れ替えの前後を同じ物差しで比べるため、行き先を選んだ主体を、選択の記録と 11 系のコマンドの規則の両方で数え、前と後の形を同じ規則の番号へ寄せる

後の形の G1〜G3 は、選択の記録で `chooser` が `conductor` の行と、プランの外で conductor が打った 11 系のコマンド（会話の記録）の両方で数える。11 系のコマンドは規則の表に形 `after` の行として規則の番号つきで足し、前の形の 10 系のコマンドと旧い進捗・台帳の数えも同じ番号へ寄せる。物差しは規則の番号を G1〜G3 へ寄せる分類の表で決め、出所は規則と形ごとに 1 つにする。会話の記録では役割で conductor を見分け、supervisor と worker が同じコマンドを打っても conductor の回に入れない。結果の `next` のとおりに打ったコマンドは、行き先を選んだのがスクリプトなので数えない（#3412 が足した）。

計測は配るコードの版に縛られない。後の形は記録の形（`route-chosen.schema.json` と使用量ログの行の `caller`・`session_id`・`usage`・`at`）だけで読み、`plugins/ndf` のモジュールを import しない。スキーマが変われば、計測の側の確かめも同じ変更で直す。前の形の読み手は `plugins/ndf/scripts/lib` の import をやめ、`scripts/measure/` と根の `scripts/lib/` に置いた（入れ替えで旧いプラグインのディレクトリが消えても動く）。

- 採らなかった案: 選択の記録だけで数える（#3395 の初めの形）。conductor が手で打つ再開のコマンドは選択の記録を書かず、プランの外で conductor が選んだ回が 0 に見え、3395-1 の「後の値が 0 に見える」が残る
- 採らなかった案: 会話の記録のコマンドの文字だけで数える。スクリプトが選んだ回（`chooser: script`）と conductor が選んだ回を分けられない
- 採らなかった案: 後の形を `ndf.contracts.chosen` の import か `bin/ndf` の副命令の JSON で読む（#3395 の初めの形）。計測が配るコードの版に縛られ、入れ替えのたびに計測が壊れる。使用量の副命令 `ndf records usage report` は期間の合計だけで会話の ID ごとの行を持たず、3 層に既に入った分を除けない
- 採らなかった案: 30 日を待ってから測る。確定仕様化と後に続く 3 本（#3492・#3696・#3697）が 1 か月止まる。代わりに測り直しを 2 回に分けた（「測り直しの回」）
- 代償: 11 系のコマンドを足すたびに、規則の表へ後の形の行を足す手間が残る。1 回目の値は日数が短く参考の値になる

## テスト観点

テストは `scripts/tests/test_ai_branching.py`・`test_token_usage.py`・`test_claude_p_usage.py` が持つ。

- 前の形と後の形の見本の記録を置いた入力で打つと、`forms.before` と `forms.after` の両方に規則の番号ごとの回数と G1〜G3 が出る
- 同じ選択を前の形と後の形で 1 回ずつ置くと、同じ規則の番号に 1 回ずつ数えられる。R03 の `chooser: script` の選択の記録と、conductor の会話の `next` と違う `ndf plan next <プラン> --from impl` を一組にすると、R03 は 1 回になる
- 同じ `ndf ci watch` を conductor・`subagents/` の worker・`entrypoint: sdk-cli` の supervisor が 1 回ずつ打つと、`value` は 1、`all_roles` は 3。役割の欄の無い `tool_result` は呼び出しの役割を継ぎ、継ぐ先が無ければ `unattributed` に入る
- 再開のコマンドは、直前の結果が `stopped` なら R03、`cause` が `{"kind": "base_conflict", "detail": …}` なら R10、`gate` か直前の `--answer` なら R13 に入る。`cause` を文字列で比べる実装は R10 の見本を R03 に入れて落ちる。`queue` の 2 本目の衝突で原因を読めない見本は `cause_unknown` で `unresolved` に入る
- 旧い形の進捗と台帳の見本（`slow`・`impl exit=1`・`design exit=1`・`settle` の `rejected`・直前のステップで分ける `judge`）で、R14・R17・R20・R21・R23・R25 が会話なしで数えられる
- `until` ちょうどの行を、会話・選択の記録・旧い進捗・台帳・`token-usage.py` のどれでも数えず、その 1 ミリ秒前の行は数える。時差の無い値は終了コード 2
- スキーマに合わない `route_chosen` の行は数えず `rejected` に出る
- `unique_prs` は同じ PR を 2 つの会話が持っても 1 本で、費用は両方の会話の和。対象リポジトリの外の PR は数えない
- `plan_cost` は `--counted` にある会話の行を足さず、行が欠ければ `complete: false` と理由を返す。`count_launches` は鍵の一致した分だけを分子にし、使用量の行が鍵を持たなければ `value: null` と理由を返す
- 測り直しの結果の表は、物差し・前の値・後の値・期間・コマンド・測れなかった理由の列を持つ（レビュー）

## 関連リンク

- [ndf-rebuild.md](ndf-rebuild.md)（作り直しの索引・子の記号・番号の対応表）・[ndf-rebuild-scripted-flow.md](ndf-rebuild-scripted-flow.md)（洗い出しの一覧 R01〜R27・選択の記録・止まった理由の種類）・[ndf-rebuild-review-scope.md](ndf-rebuild-review-scope.md)（11.0.0 と 11.x の範囲）・[ndf-rebuild-libraries.md](ndf-rebuild-libraries.md)（決定 12: 計測のスクリプトの置き場）
- [用語集: NDF の作り直し](../glossary/ndf-rebuild.md)
- [#1142](https://github.com/devbasex/ai-plugins/issues/1142)（作り直しの傘。コメント「移行の前の基準」）・[#3395](https://github.com/devbasex/ai-plugins/issues/3395)（物差しの初めの設計）・[#3412](https://github.com/devbasex/ai-plugins/issues/3412)（確定仕様化と測り直し）・[#3908](https://github.com/devbasex/ai-plugins/issues/3908)（工程のプランが起動しない）・[#3975](https://github.com/devbasex/ai-plugins/issues/3975)（U2・U1 の書き手）
