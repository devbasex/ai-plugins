# #3412 の詳細設計: 確定仕様化の手順と、前後の形を同じコマンドで数える計測

設計文書 [issue-3412-design.md](./issue-3412-design.md) の構成要素と構造を、ファイルと関数の粒度へ下ろした文書である。確定仕様化の部分は関数を持たないので、手順とファイルの中身を書く。

## 地図

触るファイル（`*` は新設。削除する設計文書 36 本は [設計文書の「移す先の対応表」](./issue-3412-design.md#移す先の対応表) にある）:

```text
docs/specifications/
  ndf-rebuild.md *                 目的・構造・入れ替え・記号の表・番号の対応表
  ndf-rebuild-libraries.md *
  ndf-rebuild-data-and-smells.md *
  ndf-rebuild-scripted-flow.md *
  ndf-rebuild-measure.md *         物差し・前後の値・打ったコマンド
  ndf-rebuild-review-scope.md *
  ndf-knowledge-and-kiro.md        「仕様化の扱い」を issues/README.md へ向ける
  README.md                        索引に 6 本を足す
issues/README.md                   完了後の扱い
docs/glossary/glossary.json        ndf records spec finalize が書く
docs/glossary/ndf-rebuild.md       ndf records glossary render が書く
scripts/measure/
  ai-branching.py                  形ごとの数え方
  ai-branching-rules.json          規則の番号と形を足す
  ai-branching-measures.json *     規則の番号 → G1〜G3 と形ごとの出所
  claude-p-usage.py                後の形を ndf records usage report から読む
scripts/tests/
  test_ai_branching.py *
  test_claude_p_usage.py
plugins/ndf/…                      作り直しの決定を指すコメントの行だけ
```

依存の向き: `ai-branching.py` は同じディレクトリの 2 つの JSON と、`plugins/ndf/contracts/schemas/route-chosen.schema.json`（ファイルとして読む）と、`jsonschema` だけに依存する。`plugins/ndf/src` を import しない。`claude-p-usage.py` は `ndf` を外部コマンドとして呼ぶ。確定仕様は互いにリンクで指し、`ndf-rebuild.md` が索引になる。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `scripts/measure/ai-branching.py` | 変更 | 前後の形の記録から規則の番号ごとの回数と G1〜G3 を出す | 規則の表・分類の表・選択の記録のスキーマ・`jsonschema` | モジュール | `load_rules`（新設）・`load_measures`（新設）・`count_choices`（新設）・`summarize`（新設）・`count_progress`（変更）・`count_ledgers`（変更）・`scan_transcript`（変更）・`count_transcripts`（変更）・`main`（引数 `--until` を足し、両端を時差つきの時刻にする） |
| `scripts/measure/ai-branching-rules.json` | 変更 | 数える規則（正規表現・Tool・文）に `rule` と `form` を持たせる | — | データ | — |
| `scripts/measure/ai-branching-measures.json` | 新設 | 規則の番号を物差しへ寄せ、後の形の出所を決める | — | データ | — |
| `scripts/measure/claude-p-usage.py` | 変更 | 11 系の使用量を後の形として読み、U2 を起動の単位で突き合わせる | `ndf records usage report`・プランの進捗の記録・使用量ログ | モジュール | `read_usage_report`（新設）・`count_launches`（新設） |
| `scripts/tests/test_ai_branching.py` | 新設 | 前後の見本で同じ回数に数えることを縛る | `ai-branching.py` | テスト | — |
| `scripts/tests/test_claude_p_usage.py` | 変更 | 後の形の読み方を縛る | `claude-p-usage.py` | テスト | — |

## 確定仕様化

満たす決定: 決定 1〜7、受け入れ条件 1〜6・11

### 処理の手順

| 手順 | 入力 | 出力 | 終わりの条件 |
| --- | --- | --- | --- |
| 1. 決定を分ける | 36 本すべての `### 決定 N` の見出しと注記（「採らない」「置き換えた」・取り消し線）と、移さない 16 本の中でコードとテストが指す事実（手順 5 の棚卸しの行が指す先） | 決定ごとの「生きている / 置き換えた（置き換え先）/ 戻した」の表と、移さない 16 本から移す決定・事実の（元の系統・元の番号）→（移し先の確定仕様・番号）の表（作業ファイル。コミットしない） | 36 本の見出しの全件が表にある。移さない 16 本の生きている決定（`fixes` の決定 69〜75 と `issue-3388-design.md` の決定 1〜10 を含む）は、1 件ずつ「移す（移し先と番号。重なれば 131 以降）」か「移さない（作業の決定である理由）」が決まっている |
| 2. 確定仕様を書く | 移す 20 本・手順 1 の表 | 6 本。各本 500 行以下 | `plan-to-spec` の削除・変換ルールを当て、開発の履歴・子の割り当て・途中の書き直しを落としている |
| 3. 番号をそろえる | 6 本の決定と不変条件の見出し | `scripted-flow` の決定 65 を 130 にし、不変条件の重なりも最大の次へ振り直す。`ndf-rebuild.md` の「番号の対応表」に（元の系統・元の番号・確定仕様・番号）を書く。元の文書はファイル名で書かず、表の頭に消す前のコミットの `issues/` の固定リンクを 1 つ置く | 設計文書のテスト設計の 2 の `grep` が 0 行 |
| 4. 記号の表を置く | 作り直しの子の記号と Issue の番号（`gh issue list --label`・#3388 のサブ Issue） | `ndf-rebuild.md` の「子の記号」の表 | 6 本の記号がすべて表にある。前の企画の子は番号で書く |
| 5. コメントを書き換える | `git grep -nE '決定 ?[0-9]+' -- plugins/ndf/src plugins/ndf/tests scripts` の 316 行 | 作り直しの決定を指す行を `docs/specifications/ndf-rebuild-<主題>.md の決定 N` の形へ | 作り直しの決定を指す行がすべてファイル名を持つ。ほかの課題の決定を指す行は変えない |
| 6. 文書の参照を書き換える | 下の「参照の検査」の 2 つのコマンドの当たり | `plugins`・`scripts`・`docs` などの参照は確定仕様へのリンクに、`issues/` に残る文書（36 本と `old/` を除く）の相対リンクは手順 7 の SHA に固定した `https://github.com/devbasex/ai-plugins/blob/<SHA>/issues/…` に替える。用語集の語の定義の本文（「要求の行」「振り分け表」など）に書かれた旧いファイル名は正本の `docs/glossary/glossary.json` の定義を直し、`ndf records glossary render` で生成し直す | 「参照の検査」の 1 が `docs/glossary/glossary.json` の `"pending_source"` の行と、生成した `docs/glossary/*.md` のうち `pending_source` から写した欄だけを返す。2 が 0 行 |
| 7. 開いている Issue のリンクを固定する | 対象リポジトリ `devbasex/ai-plugins` の開いている Issue の全件（下の「開いている Issue の列挙」） | 本文の 36 本へのリンクのうち、参照先が 40 桁の SHA でないもの（`main`・`develop` などのブランチ）を `blob/<消す前の main の SHA>/issues/<同じファイル><同じフラグメント>` に替えた本文 | 全件の本文から抜いた 36 本へのリンクが、すべて手順 7 の SHA を指し、`git cat-file -e <SHA>:issues/<ファイル>` が通り、フラグメントがその版のファイルの見出しに当たる |
| 8. 消す | 6 本と 36 本 | 下の「消すコマンド」（`--spec` と `--remove` は 1 つのパスに 1 つずつ繰り返す） | 36 本が消え、36 本を `pending_source` に持っていた語が `source` に確定仕様のファイルを持ち、`pending_source` を持たない。`ndf records glossary render --check` が `ok`。「参照の検査」の 1 と 2 が 0 行 |
| 9. 扱いを 1 か所にする | `issues/README.md`・`ndf-knowledge-and-kiro.md` | `issues/README.md` に「確定仕様へ移した後に `ndf records spec finalize` で消す。`old/` は 11.0.x までに移したものの保管で、新しく足さない」。`ndf-knowledge-and-kiro.md` の「仕様化の扱い」は `issues/README.md` を指す | 2 か所が同じ扱いを指す |

- 手順 7 は手順 8 の前に行う（設計文書の処理の流れ）。SHA は手順 8 のコミットの親の `origin/main` で、PR の作業の中で 1 回だけ取る
- 手順 5 で系統を読み取れない行（2026-11-21 の棚卸しで 58 行）は、前後 10 行のコメントと同じファイルの他の決定の指しから決め、決められなければ書き換えず PR の本文に行を挙げる

#### 参照の検査

選択肢は `|` でなく `-e` を並べる（表や Markdown の中で `\|` に書き換わらない）。手順 5 を始める前に 1 が 1 行以上を返すことを確かめる（2026-11-21 の `main` で 66 行。0 行なら式か範囲の誤りで、先へ進まない）。

```bash
# 1. 要求仕様の受け入れ条件 5 の範囲
git grep -n -e issue-1142- -e issue-3347- -e issue-3395- -e issue-3388- -- \
  plugins scripts docs .github AGENTS.md CLAUDE.md \
  ':!docs/archive' ':!docs/ndf-12-survey' ':!scripts/ndf-next-parity' ':!**/CHANGELOG.md'
# 2. issues/ に残る文書の相対リンク（リンクの検査が既定で issues/ を走査するため）
git grep -nE '\]\((\./)?issue-(1142|3347|3395|3388)-' -- issues \
  ':!issues/old' ':!issues/issue-1142-*' ':!issues/issue-3347-*' ':!issues/issue-3395-*' ':!issues/issue-3388-*'
```

許す当たりは手順ごとに次だけである。

| 時点 | 1 で許す当たり | 2 で許す当たり |
| --- | --- | --- |
| 手順 6 の後 | `docs/glossary/glossary.json` の `"pending_source"` の行と、生成した用語集の文書のうち `pending_source` から写した欄 | 無し |
| 手順 8 の後 | 無し（除外はコマンドの pathspec の 4 つだけ。`issues/old/` は範囲の外） | 無し |

`issues/issue-3412-*` が 36 本をコードの書式（バッククォート）で名指すのは作業の記録で、リンクでないので 2 に当たらない。

#### 開いている Issue の列挙

`gh search issues` は検索の索引と既定の上限 30 件に縛られるので使わない。REST の一覧をページの末尾まで読み、本文からリンクを直接抜く。

```bash
gh api --paginate 'repos/devbasex/ai-plugins/issues?state=open&per_page=100' \
  --jq '.[] | select(.pull_request | not) | {number, updated_at, body}'
```

抜く形は `https://github.com/devbasex/ai-plugins/blob/<参照>/issues/<36 本のどれか>(#<フラグメント>)?` である。`<参照>` が 40 桁の 16 進でなければ書き換える。書き換えた後に同じコマンドで全件を読み直し、終わりの条件を全リンクで確かめる。フラグメントは、その SHA のファイルの見出しから GitHub の規則で作ったアンカーと照らす（`scripts/check-markdown-links.py` の見出しの扱いと同じ）。

#### 本文の書き戻しと同時の編集

GitHub の Issue の更新（REST・GraphQL の `updateIssue`）は版の一致を条件にできないので、取得から書き戻しの間の他者の編集を、書き戻しの前と後の 2 回の照合と編集の履歴で拾う。置き換えは SHA のリンクに当たらないので、同じ本文に何度当てても結果は変わらない。

1. 列挙で得た `body` と `updated_at` を元の値として持つ
2. 書き戻す直前に `gh api repos/devbasex/ai-plugins/issues/<番号>` で取り直し、`updated_at` か `body` が元と違えば、取り直した本文に置き換えを当て直して 2 をやり直す（3 回で揃わなければその Issue を飛ばし、PR の本文に番号を挙げる）
3. `gh issue edit <番号> --body-file <置き換えた本文>` で書く
4. GraphQL の `repository.issue(number).userContentEdits(last: 10)` を読み、2 で取り直した時刻から 3 の書き込みまでの間に、自分以外の `editor` の編集があれば上書きしたとみなす。その編集の `diff`（その時点の本文）に置き換えを当て直して 3 から打ち直す

#### 消すコマンド

```bash
ndf records spec finalize \
  --spec docs/specifications/ndf-rebuild.md \
  --spec docs/specifications/ndf-rebuild-libraries.md \
  --spec docs/specifications/ndf-rebuild-data-and-smells.md \
  --spec docs/specifications/ndf-rebuild-scripted-flow.md \
  --spec docs/specifications/ndf-rebuild-measure.md \
  --spec docs/specifications/ndf-rebuild-review-scope.md \
  $(git ls-files 'issues/issue-1142-*' 'issues/issue-3347-*' 'issues/issue-3395-*' 'issues/issue-3388-*' | sed 's/^/--remove /')
```

`git ls-files` の出力が 36 行であることを打つ前に確かめる（設計文書の「移す先の対応表」の行と同じ集合）。語の SSOT が決まらなければ `--source-of <語>=<ファイル>` を語ごとに 1 つ足す。

### 異常系の処理

| 手順 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| 3 | 番号が重なる | 振り直しの漏れ | 手順 3 の `grep` | 止める | 重なる番号が出る | 直して打ち直せる |
| 7 | 取得から書き戻しの間に他者が書いた | 同時の編集 | 書き戻しの直前の取り直し（`updated_at`・`body`）と、書き戻しの後の `userContentEdits` | 最新の本文に置き換えを当て直して打ち直す | 3 回で揃わなければ Issue の番号を PR の本文に挙げる | 置き換えは何度当てても同じなので打ち直せる |
| 7 | 列挙が途中で切れた | API の失敗・上限 | `gh api --paginate` の終了コード | 止める | `gh` の標準エラー | 打ち直せる（書き換えは何度当てても同じ） |
| 8 | 語の SSOT が決まらない | 語が 2 本以上の確定仕様に現れる | `ndf records spec finalize` | 呼び出し元へ返す | `summary` に語と候補 | `--source-of` を足して打ち直せる |
| 8 | 参照が残る | 手順 5・6 の漏れ | `scripts/check-markdown-links.py`・文書の古さの検査 | 止める | 検査の当たり | 直して打ち直せる |

## 前後の形の計測

満たす決定: 決定 8・9・10、受け入れ条件 7〜10

### 関数の仕様

| 関数 | 保存先 | 種類 | シグネチャ | 責務 |
| --- | --- | --- | --- | --- |
| `load_rules` | `scripts/measure/ai-branching.py` | 新設 | `load_rules(path: pathlib.Path) -> list[dict]` | 規則の表を読み、`rule`（`R` と数字）・`form`（`before` / `after`）・`kind`（`bash` / `tool` / `phrase`）を確かめて返す |
| `load_measures` | 同上 | 新設 | `load_measures(path: pathlib.Path) -> dict[str, dict]` | 規則の番号ごとの `measure`（`G1` / `G2` / `G3` / `none`）と、形ごとの出所 `before`・`after`（下の「規則ごとの出所」）を返す |
| `count_choices` | 同上 | 新設 | `count_choices(files: list[pathlib.Path], since: datetime, until: datetime, schema: dict) -> dict` | `progress.jsonl` の `event` が `route_chosen` の行をスキーマで確かめ、規則の番号と `chooser` ごとに数える。形の合わない行は `rejected` に数える |
| `count_progress` | 同上 | 変更 | `count_progress(files: list[pathlib.Path], since: datetime, until: datetime) -> dict` | 行ごとに自分の `at` が期間の内かで数える（前は `since` だけ）。実行の区切りと前の実行の終わり方はファイルの全行から組み、数えるのは期間の内の行だけ（期間の頭をまたぐ再開も、前の実行の終わり方で分けられる）。`kind` ごとの行の数 `kinds` を足す（R14 の `slow`） |
| `count_ledgers` | 同上 | 変更 | `count_ledgers(ndf: pathlib.Path, since: datetime, until: datetime) -> dict` | `until` より後の行を数えない |
| `summarize` | 同上 | 新設 | `summarize(counts: dict, measures: dict) -> dict` | 形ごとに、規則の番号の出所 1 つの回数だけから G1〜G3 を足す。出所が `none` の規則は 0 でなく `unmeasured` に理由とともに挙げ、その物差しの値に `partial: true` を付ける |
| `scan_transcript` | 同上 | 変更 | `scan_transcript(args: tuple[str, datetime, datetime, list[dict]]) -> dict` | 渡された規則で当たりを数え、当たりを規則の番号・形・役割（`conductor` / `other` / `unattributed`）・`follows_next` で返す（前は規則の名前で返した）。期間の外の行を数えない |
| `count_transcripts` | 同上 | 変更 | `count_transcripts(roots: list[str], since: datetime, until: datetime, jobs: int, rules: list[dict]) -> dict` | 期間と規則を `scan_transcript` へ渡し、規則の番号・形・役割ごとに合計する |
| `read_usage_report` | `scripts/measure/claude-p-usage.py` | 新設 | `read_usage_report(since: date, until: date) -> dict` | `ndf records usage report --since --until`（JST の日付）を打ち、結果 JSON の `items[0]` を後の形の使用量として返す |
| `count_launches` | 同上 | 新設 | `count_launches(state_roots: list[pathlib.Path], usage_log: pathlib.Path, since: datetime, until: datetime) -> dict` | U2 の分母と分子を同じ起動の鍵で突き合わせる（下の「U1〜U3 の出し方」） |

- `count_choices`
  - 事前条件: `schema` は `route-chosen.schema.json` を読んだ値
  - 事後条件: 返りは `{"by_rule": {<R>: {<chooser>: 回数}}, "rejected": 件数, "files": 件数}`
  - 副作用: ファイルを読むだけ
- `read_usage_report`
  - 副作用: 外部コマンド `ndf` を 1 回打つ

`main` の出力は今のキー（`since`・`progress`・`ledger`・`transcripts`）に `until` と `forms` を足す。`progress`・`ledger`・`transcripts`・`forms` のどれも同じ `[since, until]` で絞った値で、固定した期間を後日打ち直しても同じ値になる。`forms` は `{"before": {"by_rule": …, "measures": {"G1": {"value": n, "all_roles": n, "partial": bool}, …}, "unmeasured": {<R>: <理由>}}, "after": {…, "rejected": n}}` である。`value` は conductor の回、`all_roles` は役割で絞らない回（入れ替えの前の手の表は役割で絞らずに数えたので、それと照らす値）。

#### 規則ごとの出所

`ai-branching-measures.json` は規則の番号ごとに、形ごとの出所を 1 つだけ持つ。G に足すのはその出所の回数だけで、ほかの出所の当たりは `by_rule` に参考として出すが足さない（会話と記録の両方に跡がある規則を 2 回数えない）。

| 出所 | 数えるもの | 選び方 |
| --- | --- | --- |
| `transcripts` | 規則の表のうち、その番号と形の行の当たり。役割が `conductor` で `follows_next` が偽の回だけを `value` に入れる | 前の形は、scripted-flow の洗い出しの表の「記録上の回数」の先頭の数が会話の数の規則（R01〜R13 の多く・R15・R16・R18・R19・R22・R24） |
| `progress` | 旧い `progress.jsonl` の数え（`count_progress` の出力）のうち、`counter` の欄と `keys`（一致）か `prefix`（前方一致）で選んだ値の合計 | 先頭の数が記録の数の規則。R14 は `kinds` の `slow`、R17 は `judge_by_decision` の `gate → ` で始まるもの、R20 は `judge_by_decision` の判断の行、R21 は `stopped_by_step_exit` の `impl exit=1` と `design exit=1`、R23 は `attention_by_reason` の該当の理由 |
| `ledger` | 旧い台帳の数え（`count_ledgers` の出力）のうち、同じ選び方の値 | R25 は `resume` の `settle: rejected` |
| `records` | 選択の記録のうち、その番号で `chooser` が `conductor` の行 | 後の形で、`chooser: conductor` を書く書き手がある規則だけ。11.0.13 の書き手（`transition_decide.py`）は R03・R10 を `script`、R13 を `human` で書き、`conductor` を書く書き手は無いので、11.0.13 ではどの規則にも使わない |
| `none` | 数えない。`unmeasured` に理由を書く | 書き手の無い規則（後の形の R14・R17・R20・R21・R23 など）と、数えられない R26・R27。0 として G に足さない（目標に届いたように見せない） |

後の形の R03・R10・R13 の出所は `transcripts` である。プランの自動の遷移が書く `script`・`human` の行は conductor の選択でなく、`ndf plan run --from` と `ndf plan queue --from`・`--resume` は選択の記録を書かないので、conductor の手での再開は会話の記録にしか残らない。

#### 会話の記録の役割

数える会話の記録は Claude Code の JSONL だけである（conductor は claude で動く。codex・kiro は cross 系の相手役で conductor にならない）。行の役割は次の順に決める。

| 役割 | 決め手 |
| --- | --- |
| `other`（supervisor・worker） | ファイルが `<会話>/subagents/` の下にある、行の `isSidechain` が真、または行の `entrypoint` が `sdk-cli`（`claude -p` の起動） |
| `conductor` | 上に当たらず、`isSidechain` が偽で `entrypoint` が `cli`（対話の会話の本筋） |
| `unattributed` | `isSidechain` か `entrypoint` が無いなど、上のどれとも決められない。`value` にも `all_roles` にも足さず、件数だけを出す |

`follows_next` は、conductor の Bash のコマンドが、同じ会話で直前の tool_result にある結果 JSON の `next` のとおりか（`shlex` で分けた引数が `next.argv` で始まり、残りが `next.ask` のキーの `--<キー> <値>` の組だけ）である。真なら行き先を決めたのはスクリプトなので `value` に入れない。判定は `ndf.contracts.chosen.chooser` と同じ規則を計測の側に写す（import しない。決定 9）。テストは両者に同じ見本を通して一致を縛る。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rules` | 規則の行に `rule` か `form` が無い・値が語彙の外 | 表の書き誤り | 読むとき | 止める | 標準エラーに行の番号と欠けた項目、終了コード 2 | 表を直して打ち直せる |
| `load_measures` | 規則の表にある番号が分類の表に無い | 片方だけ足した | `main` で 2 つを照らす | 止める | 欠けた番号、終了コード 2 | 同上 |
| `count_choices` | 行が JSON でない・スキーマに合わない | 書き手の誤り・形の変更 | 行ごとに確かめる | 既定値で続ける（数えずに `rejected` に足す） | `forms.after.rejected` に件数 | 不要 |
| `count_choices` | スキーマのファイルが無い | 配置の変更 | `main` で読むとき | 止める | パス、終了コード 3 | 配置を直して打ち直せる |
| `read_usage_report` | `ndf` が無い・0 以外で終わる・JSON でない | 導入されていない・期間に記録が無い | 呼び出しの後 | 呼び出し元へ返す | 結果に `after: {"error": <理由>}` を載せ、ほかの値は出す。終了コード 0 | 導入してから打ち直せる |
| `count_launches` | 起動の母数か使用量の行が無い・突き合わせの鍵を持たない | 書き手が無い（11.0.13 では `record_cli` を呼ぶ箇所が無い） | 読んだ後 | 既定値で続ける | `u2: {"value": null, "reason": <欠けた入力>}`。終了コード 0 | 書き手を足した版で打ち直せる |
| `main` | `--since`・`--until` に時差が無い | 打ち誤り | 引数を読むとき | 止める | 標準エラーに値、終了コード 2 | 時差を付けて打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 規則の行の `rule`・`form`・`kind` | 設定値 | `scripts/measure/ai-branching-rules.json` | 前の 25 行は `form: before`。`PHRASES`・`TOOLS` もこの表の `kind: phrase`・`kind: tool` の行へ移す |
| 規則の番号の `measure`・`before`・`after` | 設定値 | `scripts/measure/ai-branching-measures.json` | `measure` は scripted-flow の洗い出しの判定（できる → G1、一部できる → G2、できない（数えられる 2 規則）→ G3、ほか → none）。`before`・`after` は上の「規則ごとの出所」 |
| `--since`・`--until` | 引数 | `main` | 両端を含む ISO 8601 の時刻で、時差を必ず付ける（`2026-11-18T00:00:00+09:00` の形）。日付だけは受けない（機械の時間帯と UTC・JST の取り違えで期間が変わるため）。`--until` は省けない |
| `SCHEMA` | 定数 | `ai-branching.py` | `plugins/ndf/contracts/schemas/route-chosen.schema.json`（リポジトリの根からの相対） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `count_choices` | `ndf.core.plan.records.recorded_choices` | `grep -rn "def recorded_choices" plugins/ndf/src` | 使えない理由: import すると計測が配るコードのバージョンに縛られる（決定 9）。行を読んだ後の確かめは `jsonschema` を使う |
| `read_usage_report` | `ndf records usage report` | `ndf records usage report --help` | 使う（外部コマンドとして） |
| `load_rules` | 今の `RULES` の読み込み（モジュールの頭） | `ai-branching.py` の 35〜38 行 | 関数へ移して確かめを足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `load_rules` | 前と後の行が形ごとに分かれる | 規則が 0 行の形 | `rule` の無い行で終了コード 2 |
| `count_choices` | `conductor`・`script`・`human` の行を規則の番号ごとに数える | `since` と `until` の時刻ちょうどの行を含む | スキーマに合わない行は `rejected` に入り、数えない |
| `count_progress`・`count_ledgers` | 旧い形の見本（`slow`・`judge`・`impl exit=1`・`settle` の `rejected` を含む）から、洗い出しの表の記録の数の規則（R14・R17・R20・R21・R23・R25）の値が出る | `until` の 1 秒後の行を数えない。期間の頭より前に止まった実行の後の再開を、前の実行の終わり方で分ける | — |
| `summarize` | 前の形と後の形で同じ規則の番号が同じ回数になる（テスト設計の 8）。旧い形の見本で G1〜G3 が規則ごとの出所の合計になる | 会話と記録の両方に跡がある規則は出所の 1 つだけを足す。出所が `none` の規則は `unmeasured` に入り `partial` が真 | `chooser` を読み捨てると `script` の回が G に入って落ちる |
| `count_transcripts` | 前の形と後の形のコマンドを規則の番号で返す。同じ `ndf ci watch` を conductor・`subagents/` の worker・`sdk-cli` の supervisor が 1 回ずつ打つと `value` は 1、`all_roles` は 3 | `until` の 1 秒後の行を数えない。`next` のとおりに打った `ndf plan run --from` は `value` に入らず、`next` と違う開始点なら入る | `isSidechain` の無い行は `unattributed` に入る |
| `read_usage_report` | 結果 JSON の `items[0]` を返す | 期間に記録が 0 件 | `ndf` が無いと `error` を載せて続ける |
| `count_launches` | 起動 3 回・使用量の行 2 件（鍵が一致）で `2/3` | 起動 0 回 | 使用量の行が鍵を持たないと `value: null` と理由 |

停止の記録と手での再開を一組にした見本を `test_ai_branching.py` に置く: プランの進捗に R03 の `chooser: script` の選択の記録（停止の後の自動の遷移）と、conductor の会話に止まった結果の `next` と違う `ndf plan run <プラン> --from impl` を 1 回ずつ置くと、後の形の G2 の R03 は 1 になる（記録の行は足さない）。

## 測り直し

満たす決定: 決定 10、受け入れ条件 9・10

### 期間

期間は両端を含む時刻で固定し、表の「期間」の列に時差つきでそのまま書く。後の値の期間は JST で決める（11.0.0 を出した日と `ndf records usage report` の日付が JST のため）。

| 回 | 始まり | 終わり |
| --- | --- | --- |
| 前の値（G1〜G3 の打ち直し） | `2026-10-14T00:00:00Z` | `2026-11-13T23:59:59Z`（手の表の期間が UTC の日付のため） |
| 1 回目の後の値 | `2026-11-18T00:00:00+09:00` | 測る日の前日の `T23:59:59+09:00`（その日を打つ前に決めて表に書く。測る日の当日は記録が揃わないので入れない） |
| 30 日の後の値 | `2026-11-18T00:00:00+09:00` | `2026-12-17T23:59:59+09:00`（11-18〜12-17 の 30 日。2026-12-18 以降のいつ打っても同じ値になる） |

`<始まり>`・`<終わり>` は上の表の時刻、`<始まりの日>`・`<終わりの日>` はその JST の日付である。

### 打つコマンド

計測は `jsonschema` の extra を選んだリポジトリの venv で打つ（素の `python3` はシステムの Python に `jsonschema` が入っているかで動きが変わる）。

```bash
env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV uv run --frozen --project . --extra jsonschema \
  python scripts/measure/ai-branching.py --since <始まり> --until <終わり>
env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV uv run --frozen --project . --all-extras \
  python scripts/token-usage.py --min-version 11.0.0 --until <終わり> --by version --format json
env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV uv run --frozen --project . --all-extras \
  python scripts/measure/claude-p-usage.py --since <始まり> --until <終わり>
```

`claude-p-usage.py` は `--since` を省くと旧い帳簿の先頭から数え、帳簿が空なら止まるので、必ず両方を渡す。`read_usage_report` はその中で `ndf records usage report --since <始まりの日> --until <終わりの日>` を打つ。

### U1〜U3 の出し方

| 物差し | 式 | 読む欄 |
| --- | --- | --- |
| U1 | Σ_v (`conductor_cost` + `supervisor_cost` + `worker_cost`)_v × `prs`_v ÷ Σ_v `prs`_v | `token-usage.py` の JSON の `per_pr` のうち `version` が 11.0.0 以上で `prs` が 1 以上の行。版ごとの値は PR 1 本あたりなので、PR の数で重みを付けて期間全体の 1 つの値にする |
| U3 | Σ_v `minutes`_v × `prs`_v ÷ Σ_v `prs`_v | 同上 |
| U2 | 使用量の行が残った起動の数 ÷ 計画が起動した `claude -p` の数 | `count_launches` |

`token-usage.py` は `--since` を持たず、始まりは `--min-version 11.0.0`（11.0.0 より前の版で動いた会話を落とす）で決まる。11.0.0 を出した時刻より前に 11 系の開発版で動いた会話が入りうるので、表の「期間」の列にその旨を書く。

U2 の分母は、プランの進捗の記録（`--state` の下の `progress.jsonl`）のうち `claude -p` を起動したステップの行で、鍵は（`run_id`・ステップ・試行）である。分子は使用量ログ（`ndf records usage` の置き場）の `caller` が `claude-p` の行のうち、同じ鍵を持ち `usage` が `unknown` でない行である。両方を同じ `[始まり, 終わり]` で絞って鍵で突き合わせる。分母の行が無いか起動を判別できる欄を持たない、使用量ログの行が鍵を持たない、または `caller: claude-p` の書き手が無い（11.0.13 では `records/usage.py` の `record_cli` を呼ぶ箇所が無い）ときは、U2 を「計測不能」とし、欠けた入力を理由に書く。`ndf records usage report` の `items` は期間の合計で起動の数を持たないので、U2 には使わない。

結果は `ndf-rebuild-measure.md` の「前と後の値」の表（物差し・前の値・後の値・期間・コマンド・測れなかった理由）と、#1142 のコメントに同じ表で残す。G1〜G3 は `value` を比べ、前の値の行に手の表の値と打ち直しの `all_roles` を並べ、規則ごとの差があれば理由を書く。U1 の行に目標 1.14M との差を書く。30 日の値は 2026-12-18 以降に、上の 30 日の期間で同じコマンドを打って足す（リリース後の確認の計画）。
