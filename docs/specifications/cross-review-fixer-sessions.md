# cross-review: 2 ラウンド目からの修正担当が前のラウンドの会話を続けて直す（`fixer_sessions`）

## 概要

**`.ndf/review.json` に `"fixer_sessions": true` を書いたリポジトリでは、同じ `ndf cross-review run` の 2 ラウンド目からの修正担当（`claude -p`）が、
前のラウンドの修正担当の会話を続けて直す。** 前のラウンドで読んだ設計文書とコードを探し直さないためである。NDF が会話の ID（uuid4）を
決めて `--session-id` で始め、次のラウンドで同じ ID を `--resume` で渡す。書かない・偽のリポジトリは今のとおり `--no-session-persistence` で起動し、
会話を残さない。

**例: 窓 1,000,000 トークンのモデルで、修正が要るラウンドが 3 回続く起動。**

| ラウンド | 判断 | 修正担当の引数（会話の部分） | `process` の修正担当の行の後ろ |
| ---: | --- | --- | --- |
| 1 | 前の記録が無い | `--session-id <新しい ID-A>` | `（新しい会話: その起動で初めて）` |
| 2 | 1 ラウンド目は DONE・マージできた・最後の文脈 244,412 + 増分の最大 244,412 + 余白 50,000 = 538,824 ≦ 1,000,000 | `--resume <ID-A>` | `（会話を続けた）` |
| 3 | 2 ラウンド目の出力の途中に自動の要約の行があった | `--session-id <新しい ID-B>` | `（新しい会話: 前の会話で自動の要約が起きた）` |

2 ラウンド目で会話が見つからなければ（アカウントを切り替えた・記録が消えた）、同じラウンドで `--session-id <新しい ID>` の修正担当を 1 度だけ起動し直し、
出力を `round2-fix-new.*` に書く。

この文書は、その形に**なぜしたか**（背景・決定と理由・常に成り立つ条件・境界・テスト観点）を残す。手順と表の正は次にある。

| 何を | 正本 |
| --- | --- |
| 会話の続け方・新しい会話にする条件・起動し直し・`process` の文 | `cross-review` の [docs/02-fix-and-rotation.md](../../plugins/ndf/skills/cross-review/docs/02-fix-and-rotation.md) |
| `.ndf/review.json` の `fixer_sessions` の型・既定・残るもの | [docs/contracts/ndf-settings.md](../../plugins/ndf/docs/contracts/ndf-settings.md) と [review.schema.json](../../plugins/ndf/contracts/schemas/ndf-settings/review.schema.json) |
| 窓の手前の余白 `cross-review.fixer.resume-margin`（`count` 50000） | [`contracts/limits/`](../../plugins/ndf/contracts/limits/) |
| 続けるかの判断 | [resume_decide.py](../../plugins/ndf/src/ndf/domains/crossreview/resume_decide.py) の `choose`・`grown` |
| 会話の引数と結果の行の読み取り | [adapters/claude.py](../../plugins/ndf/src/ndf/adapters/claude.py) の `Session`・`session_args`・`result_line`・`context_tokens`・`context_window`・`compacted`・`conversation_missing` |

## 用語

「修正担当」「相手役」「レビューの状態ファイル」などの定義は用語集（[docs/glossary.md](../glossary.md) の `ndf-cross-review`）が正である。
この文書では次の語を使う。

| 語 | 意味 |
| --- | --- |
| 同じ起動 | `ndf cross-review run` の 1 回（駆動 `drive` の 1 回の呼び出し）。中断からの再開・打ち直しは別の起動 |
| 最後の文脈 | 修正担当の出力の最後の行（`type: result`）の `usage.iterations` の最後の要素の `input_tokens`・`cache_read_input_tokens`・`cache_creation_input_tokens` の和 |
| 窓 | 同じ結果の行の `modelUsage` の主モデルの `contextWindow`。主モデルは 4 つのトークン数（`inputTokens`・`outputTokens`・`cacheReadInputTokens`・`cacheCreationInputTokens`）の和が最も大きいモデルで、和が同じなら窓の小さい方 |
| 増分の最大 | その起動で修正担当の 1 ラウンドに増えた文脈の最大（`_Run.fixer_growth`） |

## 背景

2026-11-22 にあるマシンの cross-review の記録（55 回の実行・225 ラウンド）を集計すると、レビュー 243 分に対して修正が 802 分（77%）で、
修正 1 回の tool 呼び出しは平均 14.4 回だった。Bash 1,930 回のうち `python3 -` 363・`grep -n` 282・`sed -n` 245・`grep -rn` 90 で、修正担当は
ラウンドごとに会話を残さない新しい `claude -p` として起動され、同じファイルを探し直してから直していた。

会話を残すと、プロンプトの未公開の指摘と修正担当が読んだファイルの中身が、claude の設定の保存先（`CLAUDE_CONFIG_DIR`、無ければ `~/.claude`）の
`projects/<作業ディレクトリのキー>/<会話の ID>.jsonl` に平文で既定 30 日残る。そのため保存は利用者がリポジトリごとに選ぶ。ai-plugins は
自分の `.ndf/review.json` に真を書いている。

## 対象範囲

- **働く範囲**: 実行者のメインディレクトリの `.ndf/review.json`（version 1）に `"fixer_sessions": true` を書いたリポジトリの `ndf cross-review run`。
  書かないリポジトリの修正担当の引数は変わらない
- **対象外**:
  - 相手役（codex・kiro）の起動。相手役は毎ラウンド独立に読むことで、修正担当の思い込みを拾う
  - 別の起動（打ち直し・中断からの再開）の間で会話を引き継ぐこと
  - 修正担当の並列化（#4015）。この仕様は 1 本の修正担当を前提にする
  - 修正担当への指示の文と Tool の範囲
  - 設定のファイルをたどる途中で worktree の中のリンクを通る経路（#4191。下の「既知の限界」）

## 仕様

### 常に成り立つ条件

| # | 対象 | 条件 | 破れたときの扱い |
| --- | --- | --- | --- |
| I1 | 修正担当の引数 | `--no-session-persistence` と `--session-id`・`--resume` が同時に入らない。会話の指定（`Session`）が無ければ今と同じ引数になる | テストが落ちる |
| I2 | `fixer_sessions` が無い・偽 | どのラウンドの修正担当も `--no-session-persistence` で起動し、`choose` を呼ばず、会話が見つからない印を探さず、`run.fixer_last` を置かない | テストが落ちる |
| I3 | 状態ファイル・要約・修正の結果ファイル | 形を変えず、会話の ID も会話の状態も書かない。次のラウンドで続けるために駆動が持つ会話の ID と状態は、駆動のメモリ（`_Run`）だけに置く。ID そのものは、保存を有効にすると Claude の会話の記録のファイル名（`projects/<作業ディレクトリのキー>/<会話の ID>.jsonl`）に入り、値によらず修正担当の逐次の出力（`round<n>-fix.out`・`round<n>-fix-new.out` の stream-json の `session_id`）にも残り得る | テストが落ちる |
| I4 | 相手役の起動 | 引数に会話の指定を持たない | テストが落ちる |
| I5 | 起動し直し | 1 ラウンドに 1 回までで、続けた起動の結果ファイルが無く会話が見つからない印があるときだけ行う | テストが落ちる |
| I6 | `fixer_sessions` が真の設定 | 読んだファイルの実体（`os.path.realpath`）がレビューする worktree の実体の外にある。中にあれば `ReviewDeclInvalid` で止め、修正担当を起動しない | テストが落ちる |

### 続けるかの判断

`resume_decide.choose(previous, growth, margin)` は、上の行から順に見て最初に当たった行で決める純粋な関数である。

| 条件 | 結果（理由） |
| --- | --- |
| 前の記録が無い | 新しい会話（その起動で初めて） |
| 前の修正担当が DONE で終わらなかった | 新しい会話（前の修正担当が正常に終わらなかった） |
| 前の修正の結果をマージできなかった | 新しい会話（前の修正の結果をマージできなかった） |
| 前の修正担当の出力に自動の要約の行（`type: system`・`subtype: compact_boundary`）がある | 新しい会話（前の会話で自動の要約が起きた） |
| 最後の文脈か窓を読めない | 新しい会話（前の会話の文脈を読めない） |
| 最後の文脈 + 増分の最大 + 余白 > 窓 | 新しい会話（前の会話の文脈が窓に収まらない） |
| それ以外（和が窓ちょうどを含む） | 続ける |

増分は `resume_decide.grown` が決める。新しい会話のラウンドは最後の文脈、続けた会話のラウンドは最後の文脈から続ける前の文脈を引いた値
（負なら 0）、自動の要約が起きたラウンドはそのラウンドの窓（読めなければ 0）、文脈を読めなければ 0 である。増分の最大はこれらの最大を
保つだけなので、自動の要約を一度見ても、その起動で以後ずっと続けないわけではない。要約の直後のラウンドは表の 4 行目で新しい会話になり、
その後のラウンドは「最後の文脈 + 増分の最大 + 余白 ≦ 窓」なら続ける（要約の起きたラウンドの窓が読めなかったとき、または後の会話の窓が
それより大きいとき）。
自動の要約の行は出力の途中に出るので、`claude.compacted` は出力のファイルを全体まで読む（末尾だけを読む `agents.tail` では見落とす）。

取り込めなかったラウンドは `errored` で終わり駆動のループも終わるため、「マージできなかった」で新しい会話になるのは実際には次の起動である。
判断の関数はループの終わり方が変わっても続けないよう、この条件も持つ。

### 会話が見つからないときの起動し直し

続けた起動が結果ファイルを書かず、出力の最後の結果の行の `errors` か標準エラーに `No conversation found with session ID`（`claude.NO_CONVERSATION`）が
あれば、同じラウンドで新しい ID の `--session-id` の修正担当を 1 度だけ起動し、出力を `round<n>-fix-new.out`・`.err.log`・`-launch.json` に書く。
1 回目の出力は `round<n>-fix.*` に残る。起動し直した起動も結果を書かなければ、今のとおり注意「修正担当が結果を書かなかった」に載せてラウンドを
`errored` で終える。タイムアウト・誤りの終了など印の無い失敗では起動し直さない。

### 会話を保存するかの宣言

`fixer_sessions` は `focus` と同じく実行者のメインディレクトリの `.ndf/review.json` から読み、PR の head の同じファイルの値は使わない。

- 真偽値でない値（`"true"`・`0`・null）は `ReviewDeclInvalid` で止める（どちらの側にも黙って倒さない）
- 書けるのは `version: 1` の宣言だけである。スキーマは version 1 の外で `fixer_sessions` を持つ宣言を通さない
- `read_settings(main, worktree)` は設定のファイルの実体を先に決めて中身をそこから読み、真で実体がレビューする worktree と同じかその中なら
  `ReviewDeclInvalid` で止める。メインディレクトリの `.ndf` か `review.json` が worktree の中を指すリンクなら、同期で入った PR の値が保存を
  有効にできてしまうためである。`--worktree` にメインディレクトリそのものを渡したときも、真の宣言は止まる
- 偽・キー無しの宣言は、実体の場所によらず今のとおり読む

`fixer_sessions` が止めるのは Claude の会話の記録だけである。値によらず、修正担当の逐次の出力（`--verbose` 付きの stream-json。修正担当が読んだ
ファイルの中身と未公開の指摘を含む）は状態の保存先の `round<n>-fix.out`（起動し直したときは `round<n>-fix-new.out` も）に残る。

### 決定と理由

| # | 決定 | 理由 | 採らなかった案 |
| ---: | --- | --- | --- |
| 1 | NDF が uuid4 の ID を決めて `--session-id` で始め、`--resume` で続ける。次のラウンドで使う ID は `_Run` のメモリだけで持つ（Claude の会話の記録と修正担当の出力には残り得る。I3） | 始める前に ID が決まるので、出力を読めない・書きかけで止まったときも ID が分かる。Claude Code の CLI の機能で足り、NDF が会話の保持を作らない | 出力の `session_id` を読む・`--continue`（同じ worktree の別の claude の会話を拾う恐れ）・`--fork-session`（ID が毎回変わり得るものが無い） |
| 2 | `claude.py` の `ClaudeCall` に会話の指定 `Session`（`id`・`resume`）を足し、すべての権限で受ける。指定があれば `--no-session-persistence` を付けない | claude の引数を組む場所を `claude.py` の 1 か所に保つ。`resume: Optional[str]` を置き換えたので、意味の無い組み合わせが生まれない | `drive.py` が組んだ列を書き換える・`resume` の横に `session_id` を足す |
| 3 | 「最後の文脈 + 増分の最大 + 余白」が窓に収まるときだけ続ける。窓は結果の行の主モデルから読み、余白は `contracts/limits/` の 1 つの値（50,000。relay の再起動の方針の余白と同じ値）に置く | 続けたラウンドの中でも文脈は増え、窓を超えると自動の要約に入る。窓はモデルと環境で 5 倍違い（1,000,000 と 200,000）、固定の上限はどちらにも合わない。窓を全モデルの最小値にすると、補助の呼び出しで小さい窓のモデルが載るだけで続ける機能が黙って止まる | 固定の上限（150,000）・`usage` の合計で測る（全ターンの読みの合計で、持ち越す文脈を表さない）・上限を置かない |
| 4 | 会話が見つからない印を見たときだけ、同じラウンドで新しい会話で 1 度だけ起動し直し、出力の名前を `-fix-new` で分ける | アカウントの切り替えで保存先が変わっても、そのラウンドの修正を失わない。印の無い失敗（タイムアウトは 20 分走った後）を起動し直すと、1 ラウンドの時間が 2 倍になる。記録の置き場は claude の内部の形なので、事前にファイルを探すより claude が答える印を読む | DONE でなければいつも起動し直す・起動の前に会話の記録のファイルを探す・出力を上書きする |
| 5 | 続けた会話にも、毎回同じ形のプロンプト（その回の指摘・結果ファイルのパス・指摘の基準）と system を渡す | 前の指示が自動の要約で落ちた会話や、新しい会話へ落ちた起動でも同じに動く。プロンプトの組み立てが会話の状態で 2 通りにならない | 続けた会話には指摘の一覧だけを渡す |
| 6 | NDF は会話の記録を消さず、claude の `cleanupPeriodDays`（既定 30 日）に任せる | 記録の置き場は claude の内部の形で、消し損ねと消し過ぎを NDF が持つことになる | 駆動の終わりに、その起動で作った記録を消す |
| 7 | 保存は `.ndf/review.json` の `fixer_sessions: true`（既定 偽）を書いたリポジトリだけにし、実行者のメインディレクトリの値だけを読む | 未公開の指摘と読んだファイルを、利用者が選ばないまま平文で残さない。リポジトリの運用の値は宣言に置き、PR が保存を有効にできないようにする | 既定を真にする・環境変数や引数で選ぶ（リポジトリの決まりとして固定できない）・新しい設定のファイルを足す |

決定 3 の代償として、窓の小さいモデルでは 2 ラウンド目からも探し直しから始まることが多い。決定 4 の代償として、会話が見つからないラウンドは
修正担当を 2 回起動する。決定 7 の代償として、宣言を書かないリポジトリでは探し直しが残る。既定を真へ替えるかは、下の対照の計測で費用が
増えないと確かめてから別の Issue で決める。

## データ・設定

| 置き場 | 項目 | 値 |
| --- | --- | --- |
| `.ndf/review.json`（version 1） | `fixer_sessions` | 真偽値、既定 偽。例: `{"version": 1, "focus": [], "fixer_sessions": true}` |
| `contracts/limits/` | `cross-review.fixer.resume-margin` | `count` 50000（トークン） |
| `contracts/domains/ndf-files.json` | 行 `review` の `keys` | `fixer_sessions` を含む |
| `_Run`（駆動のメモリだけ） | `fixer_last` | `resume_decide.Previous`（会話の ID・DONE で終わったか・マージできたか・自動の要約の行があったか・最後の文脈・窓）。起動の始めは None |
| `_Run`（駆動のメモリだけ） | `fixer_growth` | 増分の最大。起動の始めは 0 |

`ndf cross-review run` の引数・結果 JSON の形・状態ファイル・要約・修正の結果ファイルは変わらない。変わるのは子の `claude -p` の引数と、結果 JSON の
`process` の修正担当の行の後ろに付く `（会話を続けた）` か `（新しい会話: <理由>）` だけである。理由は上の判断の表の 6 つに、
「前の会話が見つからない」「会話を保存しない設定」を加えた 8 つである。`process` は結果 JSON にだけ出る。

## 運用

**既知の限界。**

| 項目 | 内容 |
| --- | --- |
| 費用 | 続けた会話では、毎ターンのキャッシュ読みが持ち越した文脈の大きさだけ増える（キャッシュ読みは入力の 0.1 倍、Opus 5.5・Sonnet 5.5 は 0.05 倍）。ターン数が減らなければ費用は増える。判断は窓を超えないことだけを守り、持ち越しの量そのものは抑えない |
| キャッシュの保持 | 修正担当のキャッシュの書き込みは 1 時間の保持だった。相手役のレビューを挟んでも、続けた最初のターンはキャッシュを読める見込みである。外れれば持ち越した文脈を書き直す |
| リンクの途中の経路（#4191） | `read_settings` が比べるのは、たどり着いた実体のパスだけである。メインディレクトリの `review.json` が worktree の中のリンクを経由して worktree の外の真の設定に行き着くと、止まらない。成り立つには実行者が自分でそのリンクを張る必要があり、脅威の前提（うっかりと権限の無い人）の外なので閉じていない。途中の検査は PR #4186 の cross-review で一度足し、`//` で始まるパスと Windows のパスの先頭の扱いに指摘が続いたため取り消して #4191 へ回した |
| claude の出力の形 | 会話が見つからない印の文（claude 2.1.330 で確かめた）・`compact_boundary` の行の形・主モデルの判定は claude の出力の形に依る。文が変わると起動し直しが働かず、そのラウンドは今のとおり `errored` で終わり、次の起動は新しい会話で始まる |

**リリースの後の確かめ。** 5 回以上の実行の `round<n>-fix.out` の `tool_use` の数と更新時刻、結果の行の `total_cost_usd` を集計し、背景の実測と比べた
結果を #4013 に書く。費用は ai-plugins の `fixer_sessions` を偽にした期間（対照群）と真にした期間（処理群）で比べる。2 ラウンド目からの修正担当
1 回を単位にし、主モデルとそのラウンドの指摘の数の帯（1〜2・3〜5・6 以上）が同じものだけを組にし、両群 5 回以上ずつ集まってから判定する。
処理群の費用の中央値が高く `tool_use` の中央値も減っていなければ宣言を偽に戻し、費用が増えず `tool_use` か時間が減れば既定を真へ替えるかを
別の Issue に起こす。続けたラウンドと窓不足で新しい会話になったラウンドは比べない（窓と文脈の大きさで群が分かれる）。

## テスト観点

| 観点 | 縛るテスト |
| --- | --- |
| 会話の指定から `--session-id`・`--resume` を組み、指定が無い起動の引数が今と同じこと（I1） | `tests/adapters/test_claude_args.py` の `test_issue_4013_session_args_start_or_continue_a_conversation`・`test_issue_4013_minimal_launch_with_a_session_keeps_the_conversation`・`test_issue_4013_minimal_args_without_a_session_are_unchanged` |
| 結果の行・最後の文脈・主モデルの窓・自動の要約の行（出力の途中を含む）・会話が見つからない印を読み、読めなければ None か偽になること | `tests/adapters/test_claude_reply.py` の `test_issue_4013_*` |
| `choose` が条件ごとの理由で新しい会話にし、和が窓ちょうどで続け、1 超えると新しい会話にすること。`grown` の 4 通り | `tests/domains/crossreview/test_crossreview_resume.py` |
| `fixer_sessions` が真偽値だけを受け、既定が偽で、スキーマが version 1 だけで通すこと | `test_crossreview_criteria.py` の `test_issue_4013_fixer_sessions_*`・`tests/checks/test_spec_docs.py` の `test_issue_4013_fixer_sessions_is_accepted_only_in_version_1` |
| 2 ラウンド目が 1 ラウンド目の ID で続き、窓に収まらない・増分が大きい・自動の要約・DONE でない、で新しい会話になること | `test_crossreview_fixer_sessions.py` の `test_issue_4013_second_round_fixer_continues_the_first_conversation`・`test_issue_4013_context_that_would_not_fit_the_window_starts_a_new_conversation`・`test_issue_4013_a_larger_growth_decides_the_next_round`・`test_issue_4013_an_automatic_summary_stops_continuing`・`test_issue_4013_a_fixer_that_did_not_finish_is_not_continued` |
| 会話が見つからなければ `-fix-new` で 1 度だけ起動し直し、ほかの失敗と保存しない設定では起動し直さないこと（I5） | `test_crossreview_fixer_sessions.py` の `test_issue_4013_a_missing_conversation_is_relaunched_once_as_a_new_one`・`test_issue_4013_other_failures_are_not_relaunched`・`test_issue_4013_without_saved_sessions_a_missing_mark_is_not_relaunched` |
| 保存しない設定では会話を持たず、状態ファイルに会話の ID が入らず、相手役の引数が変わらないこと（I2・I3・I4） | `test_crossreview_fixer_sessions.py` の `test_issue_4013_fixer_without_saved_sessions_never_keeps_a_conversation`・`test_issue_4013_review_settings_default_to_not_saving` |
| worktree の中の実体の真の宣言で止まり、外のリンクと通常のファイルの真は受けること（I6） | `test_crossreview_fixer_sessions.py` の `test_issue_4013_saved_sessions_*` |
| 起動し直しと続けた会話の増分の測り方 | `test_crossreview_fixer_sessions.py` の `test_launch_fixer_*` |

駆動のテストは `test_crossreview_run.py` の行数の上限のため `test_crossreview_fixer_sessions.py` に分けてある。手順書の文は文言を突き合わせるテストを
書かず、ドキュメントレビューで見る。

## 関連リンク

- [issue #4013](https://github.com/devbasex/ai-plugins/issues/4013) — 要求と受け入れ条件
- [Pull Request #4186](https://github.com/devbasex/ai-plugins/pull/4186) — 実装
- [issue #4191](https://github.com/devbasex/ai-plugins/issues/4191) — リンクの途中の経路の扱い
- [cross-review-review-profile.md](cross-review-review-profile.md) — `.ndf/review.json` のプロファイル
- [cross-review-impact-severity.md](cross-review-impact-severity.md) — 同じ実測から起こした、重要度の偏りの仕様（#4014）
- [ndf-context-restart-policy.md](ndf-context-restart-policy.md) — relay の文脈の余白（同じ 50,000 の前例）
- Claude Code の CLI リファレンス: https://code.claude.com/docs/en/cli-reference
- Claude Code の Data usage: https://code.claude.com/docs/en/data-usage
