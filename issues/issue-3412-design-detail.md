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
  claude-p-usage.py                U2 の突き合わせ（launches）と U1 の計画起動分（plan-cost）を足す
scripts/token-usage.py             --since を足し、PR を一意に寄せた分母を出す
scripts/check-markdown-links.py    --removing を足す（消すファイルへのリンクと可変 ref のリンクを挙げる）
scripts/tests/
  test_ai_branching.py *
  test_claude_p_usage.py
  test_token_usage.py
  test_check_markdown_links.py
plugins/ndf/…                      作り直しの決定を指すコメントの行だけ
```

依存の向き: `ai-branching.py` は同じディレクトリの 2 つの JSON と、`plugins/ndf/contracts/schemas/route-chosen.schema.json`（ファイルとして読む）と、`jsonschema` だけに依存する。`plugins/ndf/src` を import しない。`claude-p-usage.py` の `launches`・`plan-cost` はファイルを読むだけで、`ndf` も `gh` も呼ばない。確定仕様は互いにリンクで指し、`ndf-rebuild.md` が索引になる。循環は無い。

| パス | 種類 | 役割 1 文 | 依存する先 | クラスまたはモジュール | メソッドと最上位の関数 |
| --- | --- | --- | --- | --- | --- |
| `scripts/measure/ai-branching.py` | 変更 | 前後の形の記録から規則の番号ごとの回数と G1〜G3 を出す | 規則の表・分類の表・選択の記録のスキーマ・`jsonschema` | モジュール | `load_rules`（新設）・`load_measures`（新設）・`count_choices`（新設）・`summarize`（新設）・`count_progress`（変更）・`count_ledgers`（変更）・`scan_transcript`（変更）・`count_transcripts`（変更）・`main`（引数 `--until` を足し、両端を時差つきの時刻にする） |
| `scripts/measure/ai-branching-rules.json` | 変更 | 数える規則（正規表現・Tool・文）に `rule` と `form` を持たせる | — | データ | — |
| `scripts/measure/ai-branching-measures.json` | 新設 | 規則の番号を物差しへ寄せ、後の形の出所を決める | — | データ | — |
| `scripts/measure/claude-p-usage.py` | 変更 | 副命令 `launches` で U2 を起動の単位で突き合わせ、`plan-cost` で U1 の計画起動分を出す。どちらも旧い帳簿・会話の再走査・PR の一覧・`ndf records usage report` を読まない（副命令を付けない今の集計は変えない） | プランの進捗の記録・使用量ログ・`token-usage.py` の会話の ID の一覧 | モジュール | `count_launches`（新設）・`plan_cost`（新設）・`main`（副命令を足す） |
| `scripts/token-usage.py` | 変更 | 期間の始まり `--since` を受け、PR を一意に寄せた分母を出す | — | モジュール | `main`（`--since` を足す）・`unique_prs`（新設）・`aggregate`（変更） |
| `scripts/check-markdown-links.py` | 変更 | `--removing <パス>` を渡すと、残る文書のリンクのうち、消すファイルへ解決する相対リンクと、消すファイルを可変の ref で指す GitHub のリンクを挙げる | `scripts/lib/md.py` | モジュール | `removing_failures`（新設）・`main`（引数を足す） |
| `scripts/tests/test_ai_branching.py` | 新設 | 前後の見本で同じ回数に数えることを縛る | `ai-branching.py` | テスト | — |
| `scripts/tests/test_claude_p_usage.py` | 変更 | `launches`・`plan-cost` の読み方を縛る | `claude-p-usage.py` | テスト | — |
| `scripts/tests/test_token_usage.py` | 変更 | `--since` と PR の一意化を縛る | `token-usage.py` | テスト | — |
| `scripts/tests/test_check_markdown_links.py` | 変更 | `--removing` の当たりを縛る | `check-markdown-links.py` | テスト | — |

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
| 6. 文書の参照を書き換える | 下の「参照の検査」の 2 つのコマンドの当たり | `plugins`・`scripts`・`docs` などの参照は確定仕様へのリンクに、残る文書（36 本を除く。`issues/old/` も含む）のリンクのうち 2 が挙げたもの（36 本へ解決する相対リンクと、36 本を `main`・`develop` などの可変の ref で指す GitHub のリンク）は手順 7 の SHA に固定した `https://github.com/devbasex/ai-plugins/blob/<SHA>/issues/<同じファイル><同じフラグメント>` に替える。既に 40 桁の SHA で固定したリンクは替えない。用語集の語の定義の本文（「要求の行」「振り分け表」など）に書かれた旧いファイル名は正本の `docs/glossary/glossary.json` の定義を直し、`ndf records glossary render` で生成し直す | 「参照の検査」の 1 が `docs/glossary/glossary.json` の `"pending_source"` の行と、生成した `docs/glossary/*.md` のうち `pending_source` から写した欄だけを返す。2 が 0 行 |
| 7. 開いている Issue のリンクを固定する | 対象リポジトリ `devbasex/ai-plugins` の開いている Issue の全件（下の「開いている Issue の列挙」） | 本文の 36 本へのリンクのうち、参照先が 40 桁の SHA でないもの（`main`・`develop` などのブランチ）を `blob/<消す前の main の SHA>/issues/<同じファイル><同じフラグメント>` に替えた置換案と、その適用（下の「本文の書き戻しと同時の編集」） | 全件の本文から抜いた 36 本へのリンクのそれぞれが、自分の参照先の SHA で検証を通る: 元から 40 桁の SHA だったリンクはその元の SHA、今回替えたリンクは手順 7 の SHA で、`git cat-file -e <SHA>:issues/<ファイル>` が通り、フラグメントがその版のファイルの見出しに当たる。40 桁の SHA でないリンクが 0 本 |
| 8. 消す | 6 本と 36 本 | 下の「消すコマンド」（`--spec` と `--remove` は 1 つのパスに 1 つずつ繰り返す）。打つ直前に 36 本の未コミットの変更が無いことを確かめる | 36 本が消え、36 本を `pending_source` に持っていた語が `source` に確定仕様のファイルを持ち、`pending_source` を持たない。`ndf records glossary render --check` が `ok`。「参照の検査」の 1 と 2 が 0 行 |
| 9. 扱いを 1 か所にする | `issues/README.md`・`ndf-knowledge-and-kiro.md` | `issues/README.md` に「確定仕様へ移した後に `ndf records spec finalize` で消す。`old/` は 11.0.x までに移したものの保管で、新しく足さない」。`ndf-knowledge-and-kiro.md` の「仕様化の扱い」は `issues/README.md` を指す | 2 か所が同じ扱いを指す |

- 手順 7 は手順 8 の前に行う（設計文書の処理の流れ）。SHA は手順 8 のコミットの親の `origin/main` で、PR の作業の中で 1 回だけ取る（手順 6 もこの SHA を使うので、手順 6 の前に取る）
- 手順 7 の置換案の適用は編集者が打つ。`main` を指すリンクが壊れるのは手順 8 を含む PR が `main` へ入った時なので、適用はその PR のマージ（本番配布の承認）より前に終える。終わりの条件の確かめ（読み直し）は、適用の後に AI が打つ
- 手順 5 で系統を読み取れない行（2026-11-21 の棚卸しで 58 行）は、前後 10 行のコメントと同じファイルの他の決定の指しから決め、決められなければ書き換えず PR の本文に行を挙げる

#### 参照の検査

選択肢は `|` でなく `-e` を並べる（表や Markdown の中で `\|` に書き換わらない）。手順 5 を始める前に 1 が 1 行以上を返すことを確かめる（2026-11-21 の `main` で 66 行。0 行なら式か範囲の誤りで、先へ進まない）。

```bash
# 1. 要求仕様の受け入れ条件 5 の範囲
git grep -n -e issue-1142- -e issue-3347- -e issue-3395- -e issue-3388- -- \
  plugins scripts docs .github AGENTS.md CLAUDE.md \
  ':!docs/archive' ':!docs/ndf-12-survey' ':!scripts/ndf-next-parity' ':!**/CHANGELOG.md'
# 2. 残る文書のリンクのうち、消した後に壊れるもの・指す先が変わるもの
env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV uv run --frozen --project . --all-extras \
  python scripts/check-markdown-links.py \
  $(git ls-tree -r --name-only <手順 7 の SHA> -- issues/ | grep -E '^issues/issue-(1142|3347|3395|3388)-' | sed 's/^/--removing /')
```

消すパスの一覧は手順 7 の SHA の木から取る（作業ツリーの `git ls-files` では、手順 8 の後に一覧が空になって 2 が何も検査しなくなる）。一覧が 36 行であることを打つ前に確かめる。

2 は文字の並びでなくリンクを解決して判定する（`](issue-…)` の形だけを拾う `grep` は、`../issues/…`・`issues/…` の相対リンクや、`blob/main/issues/…` のような GitHub のリンクを見逃す）。`--removing` を渡したときの `check-markdown-links.py` は次のとおりに動く。

- 読む文書: `git ls-files '*.md'` の全件から `--removing` のパスを除いたもの（`issues/` と `issues/old/` を含む）。既定の走査の範囲には縛られない
- 読むリンク: `scripts/lib/md.py`（CommonMark）が返すインラインのリンク・参照のリンク・自動リンクと、HTML の `href`。引用（`>`）の中のリンクも読む（GitHub は引用の中のリンクも描くので、原文の写しの中のリンクも壊れる）。コードの書式とコードのブロックの中は読まない
- 当たり（a）: 相対リンクの宛先を、文書のディレクトリから解決して `--removing` のどれかになるもの
- 当たり（b）: `https://github.com/devbasex/ai-plugins/(blob|tree|raw)/<ref>/<パス>` で、`<パス>` が `--removing` のどれかで、`<ref>` が 40 桁の 16 進でないもの（`main`・`develop`・タグ・短い SHA）
- 出力: 当たりごとに `文書:行: (a|b) 宛先`。当たりがあれば終了コード 1。40 桁の SHA で固定したリンクは当たりにしない（その版のファイルは消した後も残る）

2026-11-21 の作業ツリーで 2 に当たるものには、`issues/issue-3412-requirements.md` の `blob/main/issues/issue-3388-design-children.md`・`blob/main/issues/issue-3395-design.md#目的の物差し`・`blob/develop/issues/issue-1142-design-next.md` などと、`issues/issue-3341-requirements.md` の `blob/main/issues/issue-1142-design-next-design-detail.md` がある（どれも（b））。手順 6 はこれらを手順 7 の SHA に固定する。

許す当たりは手順ごとに次だけである。

| 時点 | 1 で許す当たり | 2 で許す当たり |
| --- | --- | --- |
| 手順 6 の後 | `docs/glossary/glossary.json` の `"pending_source"` の行と、生成した用語集の文書のうち `pending_source` から写した欄 | 無し |
| 手順 8 の後 | 無し（除外はコマンドの pathspec の 4 つだけ。`issues/old/` は範囲の外） | 無し |

`issues/issue-3412-*` が 36 本をコードの書式（バッククォート）で名指すのは作業の記録で、リンクでないので 2 に当たらない。手順 6 の後に 2 が 0 行なのに 1 が予想外の当たりを返したときは、リンクでない参照（コードの書式・コメント）の漏れで、手順 5・6 へ戻る。

#### 開いている Issue の列挙

`gh search issues` は検索の索引と既定の上限 30 件に縛られるので使わない。REST の一覧をページの末尾まで読み、本文からリンクを直接抜く。

```bash
gh api --paginate 'repos/devbasex/ai-plugins/issues?state=open&per_page=100' \
  --jq '.[] | select(.pull_request | not) | {number, updated_at, body}'
```

抜く形は `https://github.com/devbasex/ai-plugins/(blob|tree|raw)/<参照>/issues/<36 本のどれか>(#<フラグメント>)?` である。`<参照>` が 40 桁の 16 進でなければ書き換え、40 桁の 16 進なら変えない（過去の版に固定したリンクを今回の SHA へ付け替えると、本文や見出しが変わった版を指す）。書き換えた後に同じコマンドで全件を読み直し、終わりの条件を全リンクで確かめる。検証に使う SHA はリンクごとに決める: 元から 40 桁だったリンクはリンクに書かれた SHA、今回替えたリンクは手順 7 の SHA である。今回替えたリンクかは、置換案（下）にある（Issue の番号・替えた後のリンク）の組かで決める。フラグメントは、その SHA のファイルの見出しから GitHub の規則で作ったアンカーと照らす（`scripts/check-markdown-links.py` の見出しの扱いと同じ）。元から固定したリンクが検証を通らない（その SHA に無いファイルや見出しを指す）ときは書き換えず、Issue の番号とリンクを PR の本文に挙げる（今回の削除で壊れたものでないため）。

#### 本文の書き戻しと同時の編集

GitHub の Issue の更新（REST・GraphQL の `updateIssue`）は版の一致を条件にできない。直前に取り直して照合しても、照合と書き込みの間に入った他者の編集は上書きされる。よって AI は、ほかの書き手が同じ時間に本文を書かないと確かめられる（排他的に更新できる）ときだけ一括で書き、それ以外は置換案を Issue の編集者に渡す。置き換えは SHA のリンクに当たらないので、同じ本文に何度当てても結果は変わらない。

排他的に更新できるかは次で決める。

- 本文の書き手は、NDF のセッション（レーン・プラン）と人である。NDF のセッションは、`ndf plan pending` とレーンの一覧に、手順 7 を流す実行のほかに対象リポジトリの実行が無いことで止まっていると確かめられる
- 人の編集は AI から止められない（2026-11-21 の列挙で、書き換えの対象の 23 件はどれも利用者のアカウントが書いた Issue である）。よって AI が書く場合は排他にならない。排他になるのは、Issue の編集者（利用者）が下の適用のコマンドを自分で打ち、その間 NDF のセッションが止まっている場合だけである

手順は次のとおりである。

1. 置換案を作る: 列挙の全件から、Issue ごとに（番号・取得の `updated_at`・取得した本文の sha256・替える前のリンク・替えた後のリンク）を JSON の 1 行にし、作業ファイル（コミットしない）に書く。PR の本文には Issue の番号と替えるリンクの数の表と、適用のコマンドを載せ、本番配布の承認ゲートの資料にする（承認を足さない。ゲートの資料に載せるだけ）
2. 適用のコマンド（置換案のファイルを読み、編集者が打つ）は、Issue ごとに次を行う。前の Issue の結果に関わらず、各 Issue は必ず a から始める（途中から打ち直すときも、直前の取り直しを飛ばさない）
   - a. `gh api repos/devbasex/ai-plugins/issues/<番号>` で完全な本文 B0 と `updated_at` を取り直す
   - b. B0 に置き換えを当てて B1 を作る。B0 の sha256 が置換案と違っても、B0 は REST が返した完全な本文なので B1 はその時点の他者の編集を含む。B1 が B0 と同じ（替えるリンクが無くなった）なら書かずに次へ
   - c. `gh issue edit <番号> --body-file <B1>` で書く
   - d. 取り直して、本文が B1 と同じかを確かめる。違えば c の後に誰かが書いたので、上書きしていない。書き直さずに次の Issue へ進み、番号を「書いた後に変わった」として出す（打ち直せば a から当て直す）
3. 編集の履歴の照合: 適用の後に GraphQL の `repository.issue(number).userContentEdits(last: 10)` を読み、a の取り直しから c の書き込みまでの間に `editedAt` がある、自分の c 以外の編集があれば、上書きの疑いとして番号・編集者・時刻を出す。`UserContentEdit.diff` は変更の差分で本文ではないので、当て直しの入力にも本文の復元にも使わない
4. 上書きの疑いからの復旧: AI は書き直さない（再上書きを避ける）。編集者が GitHub の画面の編集の履歴で、上書きされた版の完全な本文を開いて復元する。復元した本文を取り直した最新の本文と照らし、最新の本文が復元より後の編集を含むなら、最新の本文を基に失われた部分だけを足す。その後に適用のコマンドをその Issue だけに打ち直す（a から始まるので、復元した本文に置き換えを当て直す）

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

`ndf records spec finalize` は対象のファイルを作業ツリーから直に消す（`unlink`）ので、未コミットの編集も一緒に消える。`git ls-files` の件数は追跡しているかだけを見るので、この状態を区別できない。打つ直前に次の 2 つが 0 行であることを確かめ、1 行でもあれば消すコマンドを打たずに止める。当たったファイルは、残す編集ならコミットし、捨てる編集なら利用者に確かめてから戻す（AI が `git checkout` などで捨てない）。退避は一時のコミットで行い、共有の `git stash` は使わない。

```bash
git diff --name-only -- 'issues/issue-1142-*' 'issues/issue-3347-*' 'issues/issue-3395-*' 'issues/issue-3388-*'
git diff --cached --name-only -- 'issues/issue-1142-*' 'issues/issue-3347-*' 'issues/issue-3395-*' 'issues/issue-3388-*'
```

### 異常系の処理

| 手順 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| 3 | 番号が重なる | 振り直しの漏れ | 手順 3 の `grep` | 止める | 重なる番号が出る | 直して打ち直せる |
| 7 | 書き込みの後に本文が B1 と違う | 書き込みの後の他者の編集 | 適用の d の取り直し | 書き直さずに次の Issue へ | 番号を「書いた後に変わった」として出す | 適用のコマンドを打ち直せば a の取り直しから当て直す |
| 7 | 上書きの疑い | 取り直しと書き込みの間の他者の編集 | 適用の後の `userContentEdits` の `editedAt` | AI は書き直さない | 番号・編集者・時刻を出す | 編集者が編集の履歴から完全な本文を復元し、最新の本文と照らしてから、その Issue だけ打ち直す |
| 8 | 36 本に未コミットの変更がある | 作業中の編集の残り | 消すコマンドの直前の `git diff`・`git diff --cached` | 止める | 当たったパス | コミットか利用者の確認の後に打ち直せる |
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
| `count_progress` | 同上 | 変更 | `count_progress(files: list[pathlib.Path], since: datetime, until: datetime) -> dict` | 行ごとに自分の `at` が期間の内かで数える（前は `since` だけ）。実行の区切りと前の実行の終わり方はファイルの全行から組み、数えるのは期間の内の行だけ（期間の頭をまたぐ再開も、前の実行の終わり方で分けられる）。`kind` ごとの行の数 `kinds` を足す（R14 の `slow`）。id が `judge` の判断を直前のステップの行で分けた `judge_tests_by_decision`・`judge_design_diff_by_decision`・`judge_unclassified_by_decision` を足す（R20。下の「R20 の判断の行」） |
| `count_ledgers` | 同上 | 変更 | `count_ledgers(ndf: pathlib.Path, since: datetime, until: datetime) -> dict` | `until` ちょうどとそれより後の行を数えない |
| `summarize` | 同上 | 新設 | `summarize(counts: dict, measures: dict) -> dict` | 形ごとに、規則の番号の出所 1 つの回数だけから G1〜G3 を足す。出所が `none` の規則は 0 でなく `unmeasured` に理由とともに挙げ、その物差しの値に `partial: true` を付ける |
| `scan_transcript` | 同上 | 変更 | `scan_transcript(args: tuple[str, datetime, datetime, list[dict]]) -> dict` | 渡された規則で当たりを数え、当たりを規則の番号・形・役割（`conductor` / `other` / `unattributed`。役割の欄の無い `tool_result` は `tool_use_id` の呼び出しから継ぐ）・`follows_next`・寄せ先の根拠（後の形の R03・R10・R13）で返す（前は規則の名前で返した）。期間の外の行を数えない |
| `count_transcripts` | 同上 | 変更 | `count_transcripts(roots: list[str], since: datetime, until: datetime, jobs: int, rules: list[dict]) -> dict` | 期間と規則を `scan_transcript` へ渡し、規則の番号・形・役割ごとに合計する |
| `count_launches` | `scripts/measure/claude-p-usage.py` | 新設 | `count_launches(state_roots: list[pathlib.Path], usage_log: pathlib.Path, since: datetime, until: datetime) -> dict` | U2 の分母と分子を同じ起動の鍵で突き合わせる（下の「U1〜U3 の出し方」）。副命令 `launches` の本体 |
| `plan_cost` | 同上 | 新設 | `plan_cost(usage_log: pathlib.Path, counted_sessions: set[str], since: datetime, until: datetime) -> dict` | 使用量ログの `caller: claude-p` の行のうち、`session_id` が `counted_sessions`（`token-usage.py` が 3 層に既に帰属した会話）に無い行の換算の費用を足す。副命令 `plan-cost` の本体 |
| `unique_prs` | `scripts/token-usage.py` | 新設 | `unique_prs(sessions: list[Session], repo: str) -> dict[str, list[Session]]` | 対象リポジトリの PR の URL ごとに、その PR を出力に持つセッションを寄せる（同じ PR を複数のセッション・複数の版で続けても 1 本） |
| `aggregate` | 同上 | 変更 | 今のまま | `per_pr` の行に `unique_prs`（版の軸ごとの一意な PR の数）と、一意な PR に寄せた費用と所要の合計 `pr_cost_total`・`pr_minutes_total` を足し、`meta` に期間全体の同じ 3 つ（版をまたぐ同じ PR を 1 本に数える。U1・U3 はこちらを読む）を足す |

- `count_choices`
  - 事前条件: `schema` は `route-chosen.schema.json` を読んだ値
  - 事後条件: 返りは `{"by_rule": {<R>: {<chooser>: 回数}}, "rejected": 件数, "files": 件数}`
  - 副作用: ファイルを読むだけ
- `count_launches`・`plan_cost`
  - 副作用: ファイルを読むだけ。旧い帳簿・会話の置き場・PR の一覧（`gh`）・`ndf` を読まない

`main` の出力は今のキー（`since`・`progress`・`ledger`・`transcripts`）に `until` と `forms` を足す。`progress`・`ledger`・`transcripts`・`forms` のどれも同じ `[since, until)` で絞った値で、固定した期間を後日打ち直しても同じ値になる。`forms` は `{"before": {"by_rule": …, "measures": {"G1": {"value": n, "all_roles": n, "partial": bool}, …}, "unmeasured": {<R>: <理由>}, "unresolved": {<R>: 件数}}, "after": {…, "rejected": n}}` である。`unresolved` は寄せ先か判断の種類を記録から決められなかった回（R20 の `judge_unclassified_by_decision`、後の形の R03・R10 の `cause_unknown`）で、G に足さず、その規則を含む物差しに `partial: true` を付ける。`value` は conductor の回、`all_roles` は役割で絞らない回（`unattributed` を含む。入れ替えの前の手の表は役割で絞らずに数えたので、それと照らす値）。

#### 規則ごとの出所

`ai-branching-measures.json` は規則の番号ごとに、形ごとの出所を 1 つだけ持つ。G に足すのはその出所の回数だけで、ほかの出所の当たりは `by_rule` に参考として出すが足さない（会話と記録の両方に跡がある規則を 2 回数えない）。

| 出所 | 数えるもの | 選び方 |
| --- | --- | --- |
| `transcripts` | 規則の表のうち、その番号と形の行の当たり。役割が `conductor` で `follows_next` が偽の回だけを `value` に入れる | 前の形は、scripted-flow の洗い出しの表の「記録上の回数」の先頭の数が会話の数の規則（R01〜R13 の多く・R15・R16・R18・R19・R22・R24） |
| `progress` | 旧い `progress.jsonl` の数え（`count_progress` の出力）のうち、`counter` の欄と `keys`（一致）か `prefix`（前方一致）で選んだ値の合計 | 先頭の数が記録の数の規則。R14 は `kinds` の `slow`、R17 は `judge_by_decision` の `gate → ` で始まるもの、R20 は `judge_tests_by_decision` の `judge → ` で始まるもの（下の「R20 の判断の行」）、R21 は `stopped_by_step_exit` の `impl exit=1` と `design exit=1`、R23 は `attention_by_reason` の該当の理由 |
| `ledger` | 旧い台帳の数え（`count_ledgers` の出力）のうち、同じ選び方の値 | R25 は `resume` の `settle: rejected` |
| `records` | 選択の記録のうち、その番号で `chooser` が `conductor` の行 | 後の形で、`chooser: conductor` を書く書き手がある規則だけ。11.0.13 の書き手（`transition_decide.py`）は R03・R10 を `script`、R13 を `human` で書き、`conductor` を書く書き手は無いので、11.0.13 ではどの規則にも使わない |
| `none` | 数えない。`unmeasured` に理由を書く | 書き手の無い規則（後の形の R14・R17・R20・R21・R23 など）と、数えられない R26・R27。0 として G に足さない（目標に届いたように見せない） |

##### R20 の判断の行

`judge_by_decision` のキーは `<ステップの id> → <decision か next>` で、型が `judge` のステップの判断をすべて持つ。R20 に足すのは、ステップの id が `judge` のキーだけである。入れ替えの前の `judge` は、実装のプランのテストの判断（`verify_steps.judge_step`）・リリースのプランの判断（`release_verify.judge_fix_steps`）・修正と後片付けのテンプレートの全体テストの判断（`templates.py`）で、どれも「落ちたテストを直すか先へ進むか」である。元の 44 回は次の組である。

| キー | 回数（2026-10-14〜11-13） |
| --- | ---: |
| `judge → fix` | 28 |
| `judge → doc-lint` | 6 |
| `judge → end` | 6 |
| `judge → design-tests` | 2 |
| `judge → release` | 2 |

期間に `judge → ready`・`judge → finish`・`judge → pr`・`judge → stop` やリリースの枝の id（`judge → <枝>`）があれば、同じ判断なので R20 に足す。足さないのは次の判断である。

| キー | 足す先 | 理由 |
| --- | --- | --- |
| `gate → …` | R17 | 設計承認ゲートへ渡すかの判断 |
| `judge-record → …` | 足さない（`by_rule` に出さない） | リリース記録を書き直すかの判断で、洗い出しの表のどの規則にも無い |
| `judge-test → …` | 足さない（同上） | 手で書く単独プランの雛形の判断で、洗い出しの表の数に入っていない |
| 遅れの見張りの判断 | R14 | `judge_by_decision` でなく `kinds` の `slow` で数える |

id が `judge` のステップには、テストの判断でないものが 1 つある。旧版の `verify_steps.py`（`976513356^` の `plugins/ndf/scripts/supervise_lib/verify_steps.py` の 445〜449 行）は、CI テストの宣言の実装のプランで、PR の本文が「設計と違う点」の節を欠いたときに突き合わせをやり直すか止めるかを選ぶ `{"id": "judge", "inputs": ["pr"], "choices": ["design-tests", "stop"]}` を置く。`judge_step` も突き合わせのあるプランでは同じ問い（節が欠けたら `design-tests`）を選択肢に含む。どちらも `judge → design-tests` か `judge → stop` を書くので、キーの前方一致ではテストの判断と分けられない。旧い進捗の行はプランの定義のパスも型の版も持たず（`kind: step` の欄は at・step・type・exit・seconds・cost・next・summary・out と judge の decision）、プランの定義のファイルは conductor が置いた任意の場所にあって残らないので、プランの定義は判別に使わない。判別には、同じ実行の中で judge の行の直前にある `kind: step` の行を使う。

| 直前のステップの行 | 分け先 | 根拠 |
| --- | --- | --- |
| `step: pr`（`next: judge`） | `judge_design_diff_by_decision`（R20 に足さない） | `pr` から `judge` へ移るのは、`design_diff_on_pr` が置く `on_section_missing` の経路だけ（`pr` の `on_fail` は `fix-push`） |
| ほかのステップ（`test-limited`・`test-all`・`merge` など） | `judge_tests_by_decision`（R20 に足す） | テストかマージの結果を受けた判断 |
| 同じ実行に無い（`--from judge` で再開した実行の先頭） | 前の実行の最後の `kind: step` の行で上の 2 行のどちらかに分ける | 再開の前に止まったステップが判断の入力 |
| 前の実行にも無い（期間の外の行も含めてファイルに無い） | `judge_unclassified_by_decision`（R20 に足さない） | 判別の材料が無い |

`count_progress` は `judge_by_decision`（今のまま。参考）に加えて、この 3 つのキーの組を返す。`ai-branching-measures.json` の R20 は `{"counter": "judge_tests_by_decision", "prefix": "judge → "}` と書き、`prefix` は `→` の前後の空白まで含めて照らす（`judge-record → ` と `judge-test → ` は `judge → ` で始まらないので当たらない）。`judge_unclassified_by_decision` が 1 件でもあれば、R20 の件数を `unmeasured` でなく `unresolved: {"R20": <件数>}` に出し、R20 を含む物差しに `partial: true` を付ける（判別できない回を足すと目標に遠く、捨てると近く見えるので、どちらにも寄せない）。

従来の 44 回は `judge_by_decision` の `judge → ` の全部で、新しい R20 は 44 から `judge_design_diff_by_decision` と `judge_unclassified_by_decision` の合計を引いた値になる。差に入りうるのは主に `judge → design-tests`（2）と `judge → stop`（0）で、`fix`・`doc-lint`・`end`・`release` は節が欠けたときの問いの答えでないので、判別が効けば新しい値は 42〜44 に収まる。打ち直しでは 3 つのキーの組をそれぞれ測り直しの結果に書き、44 との差をキーごとに示す。

##### 後の形の R03・R10・R13

後の形の R03・R10・R13 の出所は `transcripts` である。プランの自動の遷移が書く `script`・`human` の行は conductor の選択でなく、conductor が手で打つ再開のコマンドは選択の記録を書かないので、conductor の手での再開は会話の記録にしか残らない。11.0.13 の再開のコマンドは次の 2 つである（`ndf plan --help` の副命令は `next`・`queue`・`wait`・`pending`・`check` で、`run` は無い）。

- `ndf plan next <プラン> --from <ステップ>`（単独のプランの開始点を選ぶ。`--answer`・`--set`・`--fresh` を伴うこともある）
- `ndf plan queue … --from <プラン>=<ステップ>`・`ndf plan queue … --resume`（並べたプランの再開）

規則の表の後の形の行は、この 2 つを 1 つの正規表現の組（`kind: bash`、番号 `R03`）で拾い、R10 と R13 には別の正規表現を置かない。拾った回は、同じ会話で同じプランを対象にした直前の `ndf plan` の結果 JSON（tool_result）で、対象ごとに 1 つの規則へ寄せる。

11.0.13 の結果の形は次のとおりで、寄せ方はこの形をそのまま読む（文字列の `cause` を仮定しない）。

| 結果 | `status`・`cause` | `items` | `routes` |
| --- | --- | --- | --- |
| `ndf plan next`（単独） | そのプランの値。止まると `cause` は `{"kind": <停止理由>, "detail": …}` の辞書（`contracts/causes.py` の `cause`） | 先頭が `{"kind": "plan", …, "step": <止まったステップ>}` | 承認ゲートで `{"approved": <ndf plan next … --from <次のステップ> のコマンド>, "rejected": null}`（`runner.py` の 418 行） |
| `ndf plan queue` | キュー全体の代表値。`cause` は止まったプランのうち先頭の 1 本の `kind`（`queue_run.py` の `_stopped_result`）、`summary` は `<先頭のプラン>: <詳細>` | プランごとに `{"kind": "plan", "name", "status", "detail"}` と承認ゲートなら `routes`。`cause` を持たない | 承認ゲートで routes を持つ先頭の 1 本の分 |
| `ndf plan wait` | 代表値（止まった先頭の `cause`） | 止まったプランは `cause`（`kind` を持つ）、承認ゲートは `gate: true` と `routes` を持つ（`wait_decide.py` の `_item`） | 先頭の 1 本の分 |

再開の対象と、照らす結果の行は次のとおりである。

- `ndf plan next <プラン> --from <ステップ>`: 対象はそのプラン 1 本で、照らすのはその結果のトップレベル
- `ndf plan queue … --from <プラン>=<ステップ>`: 対象は `--from` に書いたプランのそれぞれで、照らすのは直前の `queue` か `wait` の結果の `items` のうち `kind: plan` で `name` が一致する行
- `ndf plan queue … --resume`（`--from` を持たない分）: 対象は直前の結果の `items` で `status` が `ok` でないプラン（`--resume` が流し直すもの。`queue_run.py` の `_finished`）

対象ごとの原因は次の順に読む。

1. 対象の行（単独ならトップレベル）の `status` が承認ゲート（`gate`）か、直前に conductor が打った同じプランの `ndf plan next` が `--answer` を持つ → R13（回答の後の行き先を選んだ）
2. 対象の行の `cause.kind` が `base_conflict` → R10（衝突の後の取り込みを選んだ。`transition_decide.py` が R10 とする条件と同じ）。`queue` の結果のように行が `cause` を持たないときは、トップレベルの `cause.kind` を、トップレベルの `summary` が `<対象のプラン>: ` で始まるとき（対象が先頭の止まったプラン）か、`status: stopped` の行が対象の 1 本だけのときに限って対象の原因とする
3. 対象の行の `status` が `stopped` で、2 の条件で原因が読めない（別のプランが先頭で止まった `queue` の 2 本目など）→ 寄せ先を決めず、根拠 `cause_unknown` で `unresolved` に数える（R03 にも R10 にも足さない。R03・R10 を含む物差しに `partial: true`）
4. ほか（`cause.kind` が `base_conflict` 以外の `stopped`、または同じ会話に直前の結果が無い）→ R03

1 つのコマンドが複数の対象を持つとき、対象ごとに寄せ先を決め、対象ごとに 1 回として数える（`--from a=impl --from b=merge` は 2 回）。止まった `queue` の結果は打ち直しの `--resume` を `next` に持つので、それをそのまま打った回は下の `follows_next` で数えない。

`--answer` だけを持ち `--from` を持たない `ndf plan next` は、承認の回答そのもので規則に数えない（R13 は回答の後の行き先の選択で、回答は人の判断）。寄せ先を決めた根拠（`gate`・`answer`・`base_conflict`・`stopped`・`cause_unknown`・`no_prior_result`）は `by_rule` の各規則の内訳に出す。

`follows_next` は、どの規則に寄せても同じ判定で、直前の結果の `next` に加えて、承認ゲートの結果の `routes` の各コマンド（単独なら `routes.approved`、`queue`・`wait` なら対象の行の `routes` の各回答）とも照らす。打ったコマンドがそのどれかのとおりなら（照らし方は下の `follows_next` と同じ）真で、スクリプトが提示した承認後の行き先をそのまま実行しただけなので `value` に入れない。`routes` のどれとも `next` とも違う開始点を選んだときだけ R13 の `value` に入る。

#### 会話の記録の役割

数える会話の記録は Claude Code の JSONL だけである（conductor は claude で動く。codex・kiro は cross 系の相手役で conductor にならない）。行の役割は次の順に決める。

| 役割 | 決め手 |
| --- | --- |
| `other`（supervisor・worker） | ファイルが `<会話>/subagents/` の下にある、行の `isSidechain` が真、または行の `entrypoint` が `sdk-cli`（`claude -p` の起動） |
| `conductor` | 上に当たらず、`isSidechain` が偽で `entrypoint` が `cli`（対話の会話の本筋） |
| 呼び出しから継ぐ | 行が `isSidechain` も `entrypoint` も持たない `tool_result` のとき、その `tool_use_id` と同じ `id` の `tool_use` を持つ assistant の行（同じファイルの中）の役割を継ぐ。実際の Claude Code の対話のログには両方の欄を持たない `tool_result` があり、R04・R09・R19 の文はこの行に現れる |
| `unattributed` | 上のどれとも決められない（継ぐ先の呼び出しが同じファイルに無い・呼び出しの行も役割を決められない）。件数を出す |

`value` は `conductor` の回だけを数える。`all_roles` は、入れ替えの前の手の表と比べるための「役割で絞らない」回で、`conductor`・`other`・`unattributed` のすべてを足す（手の表は役割を見ずに当たりを数えたので、帰属できない回もその数に入っている）。`unattributed` の件数は `all_roles` の内訳として別にも出し、`value` には足さない。

`follows_next` は、conductor の Bash のコマンドが、同じ会話で直前の tool_result にある結果 JSON の `next` か、承認ゲートの結果の `routes` の各コマンド（上の「後の形の R03・R10・R13」）のどれかのとおりか（`shlex` で分けた引数がそのコマンドの `argv` で始まり、残りがその `ask` のキーの `--<キー> <値>` の組だけ）である。真なら行き先を決めたのはスクリプトなので `value` に入れない。判定は `ndf.contracts.chosen.chooser` と同じ規則を計測の側に写す（import しない。決定 9）。テストは両者に同じ見本を通して一致を縛る。

### 異常系の処理

| 関数 | 失敗 | 原因 | 検出する場所 | 扱い | 利用者に見える出力と終了コード | 後始末と再実行の可否 |
| --- | --- | --- | --- | --- | --- | --- |
| `load_rules` | 規則の行に `rule` か `form` が無い・値が語彙の外 | 表の書き誤り | 読むとき | 止める | 標準エラーに行の番号と欠けた項目、終了コード 2 | 表を直して打ち直せる |
| `load_measures` | 規則の表にある番号が分類の表に無い | 片方だけ足した | `main` で 2 つを照らす | 止める | 欠けた番号、終了コード 2 | 同上 |
| `count_choices` | 行が JSON でない・スキーマに合わない | 書き手の誤り・形の変更 | 行ごとに確かめる | 既定値で続ける（数えずに `rejected` に足す） | `forms.after.rejected` に件数 | 不要 |
| `count_choices` | スキーマのファイルが無い | 配置の変更 | `main` で読むとき | 止める | パス、終了コード 3 | 配置を直して打ち直せる |
| `plan_cost` | 使用量ログが無い・`caller: claude-p` の行が 0・行が `session_id` を持たない | 書き手が無い（11.0.13 では `record_cli` を呼ぶ箇所が無い） | 読んだ後 | 既定値で続ける | `plan_cost: {"value": <足せた分>, "complete": false, "reason": <欠けた入力>}`。終了コード 0 | 書き手を足した版で打ち直せる |
| `token-usage.py` の `main` | 期間に対象リポジトリの PR が 0 本 | 期間に PR を出した作業が無い・出力に PR の URL が残らない | 集計の後 | 既定値で続ける | `per_pr` の行と `meta` に `unique_prs: 0` と `reason`、U1・U3 は「計測不能」 | 期間か記録を直して打ち直せる |
| `count_launches` | 起動の母数か使用量の行が無い・突き合わせの鍵を持たない | 書き手が無い（11.0.13 では `record_cli` を呼ぶ箇所が無い） | 読んだ後 | 既定値で続ける | `u2: {"value": null, "reason": <欠けた入力>}`。終了コード 0 | 書き手を足した版で打ち直せる |
| `main` | `--since`・`--until` に時差が無い | 打ち誤り | 引数を読むとき | 止める | 標準エラーに値、終了コード 2 | 時差を付けて打ち直せる |

### 内部のデータと定数

| 名前 | 種類 | 保存先 | 既定値 |
| --- | --- | --- | --- |
| 規則の行の `rule`・`form`・`kind` | 設定値 | `scripts/measure/ai-branching-rules.json` | 前の 25 行は `form: before`。`PHRASES`・`TOOLS` もこの表の `kind: phrase`・`kind: tool` の行へ移す |
| 規則の番号の `measure`・`before`・`after` | 設定値 | `scripts/measure/ai-branching-measures.json` | `measure` は scripted-flow の洗い出しの判定（できる → G1、一部できる → G2、できない（数えられる 2 規則）→ G3、ほか → none）。`before`・`after` は上の「規則ごとの出所」 |
| `--since`・`--until` | 引数 | `main` | 半開区間 `[--since, --until)` の ISO 8601 の時刻で、時差を必ず付ける（`2026-11-18T00:00:00+09:00` の形。`--until` の時刻ちょうどの行は数えない）。日付だけは受けない（機械の時間帯と UTC・JST の取り違えで期間が変わるため）。`--until` は省けない |
| `SCHEMA` | 定数 | `ai-branching.py` | `plugins/ndf/contracts/schemas/route-chosen.schema.json`（リポジトリの根からの相対） |

### 既存の部品の再利用

| 新設する関数 | 確かめた既存の部品 | 確かめた手段 | 結果 |
| --- | --- | --- | --- |
| `count_choices` | `ndf.core.plan.records.recorded_choices` | `grep -rn "def recorded_choices" plugins/ndf/src` | 使えない理由: import すると計測が配るコードのバージョンに縛られる（決定 9）。行を読んだ後の確かめは `jsonschema` を使う |
| `plan_cost` | `ndf records usage report` | `ndf records usage report --help` | 使わない: 期間の合計だけを返し、会話の ID ごとの行を持たないので、`token-usage.py` が既に帰属した分を除けない。指標に使う用途が無いので通常の経路では打たない |
| `unique_prs` | `token-usage.py` の `_per_pr_row` の `n_pr` | `scripts/token-usage.py` の 680〜702 行 | 直す: `sum(len(s.prs) for s in with_pr)` はセッションごとの PR の数の和で、同じ PR を複数のセッションで数える |
| `load_rules` | 今の `RULES` の読み込み（モジュールの頭） | `ai-branching.py` の 35〜38 行 | 関数へ移して確かめを足す |

### 単体テストの観点

| 関数 | 正常 | 境界値 | 異常 |
| --- | --- | --- | --- |
| `load_rules` | 前と後の行が形ごとに分かれる | 規則が 0 行の形 | `rule` の無い行で終了コード 2 |
| `count_choices` | `conductor`・`script`・`human` の行を規則の番号ごとに数える | `since` ちょうどの行を含み、`until` ちょうどの行を含まない。`until` の 1 ミリ秒前の行を含む | スキーマに合わない行は `rejected` に入り、数えない |
| `count_progress`・`count_ledgers` | 旧い形の見本（`slow`・`judge`・`impl exit=1`・`settle` の `rejected` を含む）から、洗い出しの表の記録の数の規則（R14・R17・R20・R21・R23・R25）の値が出る | `until` ちょうどの行を数えず、その 1 秒前の行を数える。期間の頭より前に止まった実行の後の再開を、前の実行の終わり方で分ける。直前が `test-limited` の `judge → design-tests` は `judge_tests_by_decision`、直前が `step: pr`（`next: judge`）の `judge → design-tests` と `judge → stop` は `judge_design_diff_by_decision` に入る。`--from judge` で始まる実行の judge は前の実行の最後のステップの行で分ける | ファイルの頭が judge の行で前のステップの行が無いと `judge_unclassified_by_decision` に入り、R20 に足さず `unresolved` と `partial: true` が出る |
| `summarize` | 前の形と後の形で同じ規則の番号が同じ回数になる（テスト設計の 8）。旧い形の見本で G1〜G3 が規則ごとの出所の合計になる | 会話と記録の両方に跡がある規則は出所の 1 つだけを足す。出所が `none` の規則は `unmeasured` に入り `partial` が真 | `chooser` を読み捨てると `script` の回が G に入って落ちる |
| `count_transcripts` | 前の形と後の形のコマンドを規則の番号で返す。同じ `ndf ci watch` を conductor・`subagents/` の worker・`sdk-cli` の supervisor が 1 回ずつ打つと `value` は 1、`all_roles` は 3。`isSidechain` も `entrypoint` も無い `tool_result` の「基盤待ち」（R09）は、`tool_use_id` の呼び出しが conductor なら `value` に入る | `until` ちょうど（`<終わり>` の翌日 `00:00:00`）の行と、その直前の `23:59:59.999` の行で、前者を数えず後者を数える。`next` のとおりに打った `ndf plan next <プラン> --from <ステップ>` は `value` に入らず、`next` と違う開始点なら入る。同じ `--from` のコマンドが、直前の結果が `stopped` なら R03、`cause` が `{"kind": "base_conflict", "detail": …}`（11.0.13 の `ndf plan next` が返す形をそのまま写した見本）なら R10、`gate` か直前の `--answer` なら R13 に入る。`cause` を文字列で比べる実装では R10 の見本が R03 に入って落ちる。`queue` の結果で 1 本目が `base_conflict` 以外で止まり 2 本目が衝突した見本（`items` は `cause` を持たず、トップレベルの `summary` は 1 本目）では、`--from <2 本目>=<ステップ>` は `cause_unknown` で `unresolved` に入り、`--from <1 本目>=<ステップ>` は R03 に入る。`status: stopped` の行が 2 本目だけの見本では R10 に入る。`--from a=impl --from b=merge` は対象ごとに 2 回になる。承認ゲートの結果（`routes.approved.argv` が `ndf plan next <プラン> --from <次のステップ>`）の後で、その argv のとおりに打つと `value` に入らず、別の開始点を打つと R13 の `value` に入る | 継ぐ先の呼び出しが同じファイルに無い `tool_result` は `unattributed` に入り、`value` に入らず `all_roles` に入る |
| `plan_cost` | `caller: claude-p` の行 3 件のうち、1 件の `session_id` が `counted_sessions` にあると 2 件分だけを足す | 行が 0 件で `complete: false` | `session_id` の無い行は足さず `complete: false` と理由 |
| `unique_prs` | 同じ PR の URL を 2 つのセッション（別の版）が持つと PR は 1 本で、費用は両方のセッションの和 | 対象リポジトリの外の PR の URL は数えない | PR が 0 本で U1・U3 は「計測不能」と理由 |
| `token-usage.py` の `--since` | `--since` より前の行を数えない | `--since` ちょうどの行を数え、`--until` ちょうどの行を数えない | 時差の無い値で終了コード 2 |
| `check-markdown-links.py` の `--removing` | 消すファイルへの `../issues/…` の相対リンクと `blob/main/issues/…` のリンクを挙げる | 40 桁の SHA のリンクと、コードの書式の中のパスを挙げない。引用の中のリンクは挙げる | — |
| `count_launches` | 起動 3 回・使用量の行 2 件（鍵が一致）で `2/3` | 起動 0 回 | 使用量の行が鍵を持たないと `value: null` と理由 |

停止の記録と手での再開を一組にした見本を `test_ai_branching.py` に置く: プランの進捗に R03 の `chooser: script` の選択の記録（停止の後の自動の遷移）と、conductor の会話に止まった結果（`status: stopped`）の `next` と違う `ndf plan next <プラン> --from impl` を 1 回ずつ置くと、後の形の G2 の R03 は 1 になる（記録の行は足さない）。

## 測り直し

満たす決定: 決定 10、受け入れ条件 9・10

### 期間

期間は始まりを含み終わりを含まない半開区間 `[始まり, 終わり)` で固定し、表の「期間」の列に時差つきでそのまま書く。終わりは最終日の翌日の `00:00:00` である（`23:59:59` までの閉区間にすると、Claude Code の JSONL の `2026-11-13T23:59:59.123Z` のような秒未満を持つ行が最終日から欠ける）。後の値の期間は JST で決める（11.0.0 を出した日の日付が JST のため）。

| 回 | 始まり | 終わり（含まない） |
| --- | --- | --- |
| 前の値（G1〜G3 の打ち直し） | `2026-10-14T00:00:00Z` | `2026-11-14T00:00:00Z`（手の表の期間 10-14〜11-13 が UTC の日付のため） |
| 1 回目の後の値 | `2026-11-18T00:00:00+09:00` | 測る日の `T00:00:00+09:00`（その日を打つ前に決めて表に書く。測る日の当日は記録が揃わないので入れない） |
| 30 日の後の値 | `2026-11-18T00:00:00+09:00` | `2026-12-18T00:00:00+09:00`（11-18〜12-17 の 30 日。2026-12-18 以降のいつ打っても同じ値になる） |

すべての入力で同じ半開区間を使う。時刻は文字の並びでなく、時差つきの時刻に直して比べる（`Z` と `+09:00`、秒未満の桁数の違う行が混ざるため）。入力ごとの時刻の形は次のとおりで、各形の見本の行を境界のテストに置く（`<終わり>` の 1 ミリ秒前の行を数え、`<終わり>` ちょうどの行を数えない）。

| 入力 | 時刻の欄 | 実データの形（2026-11-21 に確かめる） |
| --- | --- | --- |
| Claude Code の会話の JSONL | 行の `timestamp` | `2026-11-20T14:59:59.123Z`（UTC・ミリ秒） |
| プランの進捗の記録（`progress.jsonl`）・選択の記録 | 行の `at` | 書き手の出力の形（選択の記録の見本は `2026-12-01T01:00:00Z`。秒未満の有無は実装の前に実データで確かめ、テストの見本に写す） |
| 使用量ログ（`ndf records usage`） | 行の `at` | `2026-11-21T01:42:57+00:00`（`ndf.domains.records.jsonl.now()`。UTC・秒まで・`+00:00`） |
| 旧い進捗の記録・旧い台帳 | 行の `at`・`ts` | 入れ替えの前の書き手の形（秒まで・時差つき。見本は 2026-11-13 の実データから写す） |
| `token-usage.py` | 会話の行の `timestamp` | 会話の JSONL と同じ |

`ndf records usage report` は JST の日付で集計するので、この期間の値と比べる用途が出たときは `--since <始まりの日> --until <終わりの前日>` で同じ範囲になる（今回の物差しは usage report を使わない。下の「U1〜U3 の出し方」）。

`<始まり>`・`<終わり>` は上の表の時刻、`<始まりの日>`・`<終わりの日>` はその JST の日付である。

### 打つコマンド

計測は `jsonschema` の extra を選んだリポジトリの venv で打つ（素の `python3` はシステムの Python に `jsonschema` が入っているかで動きが変わる）。

```bash
env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV uv run --frozen --project . --extra jsonschema \
  python scripts/measure/ai-branching.py --since <始まり> --until <終わり>
env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV uv run --frozen --project . --all-extras \
  python scripts/token-usage.py --since <始まり> --until <終わり> --repo devbasex/ai-plugins \
  --by version --format json --no-ledger --sessions-out <会話の ID の一覧>
env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV uv run --frozen --project . --all-extras \
  python scripts/measure/claude-p-usage.py launches --since <始まり> --until <終わり>
env -u UV_PROJECT_ENVIRONMENT -u VIRTUAL_ENV uv run --frozen --project . --all-extras \
  python scripts/measure/claude-p-usage.py plan-cost --since <始まり> --until <終わり> --counted <会話の ID の一覧>
```

- `token-usage.py` に `--since` を足す（今は `--min-version` で版を絞るだけで、期間の始まりを指定できない。期間の内に旧い版で続けた作業が落ち、対象の版で期間の前に取った記録が入る）。`--since` と `--until` は `ai-branching.py` と同じ半開区間で、`--until` の比べ方を今の `t > until` から `t >= until` で切る形へ直す。`--min-version` は期間とは別の条件で、物差しの値には付けない（付けたときは表の「期間」の列に、期間と別に「版で絞った条件」として書く）
- `--since` より前の行からは帰属の情報（読み込んだ Skill の版・作業ディレクトリ・Agent の定義・Bash の呼び出し）と、Codex の席の識別（`session_meta`）と直近の累計だけを引き継ぎ、使用量・所要・PR は期間の内の行だけから数える（PR は `gh pr create` の結果の時刻で決める。Codex の席は期間の内の累計の増分を数える）
- 同じ `message.id` の応答は、最初に現れた行の時刻で期間へ帰属させ、値は最後に現れた行の usage（確定値）を取る。期間の内に最初に現れた応答の再掲が `--until` 以後にあれば、その確定値で数える（境の前の途中値で止めない）。期間の前に最初に現れた応答は、期間の内に再掲されても数えない。隣り合う 2 期間の和は通算と一致する
- 会話の所要（`pr_minutes_total` の分子）は、親とサブエージェントの期間の内の行の時刻を 1 本の並びに合わせて測る。子だけが動いた区間も入り、親と子が重なる区間は一度だけ数える
- `--sessions-out` は、U1 の分子（`pr_cost_total`）に費用を入れた会話（対象リポジトリの PR を持つ会話）の `session_id` の一覧を書く（`plan-cost` が二重に数えず、PR を持たない会話の分は `plan-cost` が足すため）。`--repo` と `--no-ledger` が要り、既存のファイルには書かず、所有者だけが読み書きできる権限で作る
- U1 の測り直しでは `token-usage.py` に `--no-ledger` を付け、旧い使用量の帳簿（`scripts/lib/usage_ledger.py` の置き場）を読まない。帳簿の行は `link_ledger` がリポジトリを照合せずに時刻と版で会話へ寄せるので、別のプロジェクトの計画の費用が `pr_cost_total` に入る。また同じ起動が帳簿と使用量ログの両方に残ると、会話の ID の一覧では帳簿から足した起動を `plan-cost` から外せない。計画起動の分は `plan-cost` が使用量ログの対象リポジトリの行からだけ足す（`--sessions-out` は `--no-ledger` が無ければ終了コード 2）
- `claude-p-usage.py` の `launches`・`plan-cost` は、U2 と U1 の計画起動分に要る入力（プランの進捗の記録・使用量ログ・会話の ID の一覧）だけを読む。旧い帳簿・会話の置き場の再走査・PR の一覧（`gh`）・`ndf records usage report` は読まない。副命令を付けない今の集計（旧い帳簿と会話を数える版ごとの表）は 10 系の値を見る用途のために残し、物差しの測り直しでは打たない

### U1〜U3 の出し方

| 物差し | 式 | 読む欄 |
| --- | --- | --- |
| U1 | (`meta.pr_cost_total` + `plan_cost.value`) ÷ `meta.unique_prs` | `token-usage.py` の JSON の `meta`（期間全体で、版をまたぐ同じ PR を 1 本に数えた分母と、その PR を持つ会話の費用を 1 回ずつ足した分子）と、`plan-cost` の結果。`per_pr` の行の値は足さない（同じ PR が 2 つの版に現れると各行の `unique_prs` がそれぞれ 1 になり、和では分母が 2 になる） |
| U3 | `meta.pr_minutes_total` ÷ `meta.unique_prs` | `token-usage.py` の JSON の `meta`（U1 と同じ理由で `per_pr` の行を足さない） |
| U2 | 使用量の行が残った起動の数 ÷ 計画が起動した `claude -p` の数 | `count_launches` |

**U1 の費用の範囲は基準値と同じにする。** 基準値 1.42M（#1142 のコメント「移行の前の基準」）は、会話に残る 3 層（conductor 0.68M を含む）と、計画が起動した `claude -p` の 0.74M を足した値である。`token-usage.py` の 3 層には、会話の記録が残る `claude -p` だけが入り、記録の残らない計画起動の分は入らない。そこで:

- 3 層の費用は `token-usage.py` の値をそのまま使う
- 計画起動の分は、使用量ログの `caller: claude-p` の行のうち、`session_id` が `--sessions-out` の一覧に無い行だけを `plan-cost` で足す（会話が残って 3 層に既に入った起動を二重に数えない）。換算は `token-usage.py` の係数（`WEIGHTS`・`READ_RATES`）を使う
- `plan-cost` が `complete: false`（使用量ログが無い・`caller: claude-p` の書き手が無い・行が `session_id` を持たない。11.0.13 では `record_cli` を呼ぶ箇所が無いのでこれに当たる）のときは、U1 を「観測下限」として出す: 値は (3 層 + 足せた分) ÷ `unique_prs` で、欠けた計画起動の分を含まない下限である。このとき目標 1.14M 以下への到達は「判定不能」とし、下限が 1.14M を超えたときだけ「未達」と書ける

**分母は対象リポジトリの一意な PR である。** 今の `prs` はセッションごとの PR の URL の数の和で、同じ PR を複数のセッションや複数の版で続けると同じ PR を何度も数え、費用と所要が過小になる。`unique_prs` は、期間の内のセッションの出力に現れた `https://github.com/devbasex/ai-plugins/pull/<番号>` を番号で一意にした数で、その PR を持つセッションの費用と所要を 1 回ずつ足したものを `pr_cost_total`・`pr_minutes_total` とする（1 つのセッションが 2 本の PR を持てば、そのセッションの費用は合計に 1 回だけ入る）。`unique_prs` が 0 なら U1・U3 は「計測不能」とし、理由（期間に PR を出した作業が無い・出力に PR の URL が残らない）を書く。

U2 の分母は、プランの進捗の記録（`--state` の下の `progress.jsonl`）のうち `claude -p` を起動したステップの行で、鍵は（`run_id`・ステップ・試行）である。分子は使用量ログ（`ndf records usage` の置き場）の `caller` が `claude-p` の行のうち、同じ鍵を持ち `usage` が `unknown` でない行である。両方を同じ `[始まり, 終わり)` で絞って鍵で突き合わせる。分母の行が無いか起動を判別できる欄を持たない、使用量ログの行が鍵を持たない、または `caller: claude-p` の書き手が無い（11.0.13 では `records/usage.py` の `record_cli` を呼ぶ箇所が無い）ときは、U2 を「計測不能」とし、欠けた入力を理由に書く。`ndf records usage report` の `items` は期間の合計で起動の数を持たないので、U2 には使わない。

結果は `ndf-rebuild-measure.md` の「前と後の値」の表（物差し・前の値・後の値・期間・コマンド・測れなかった理由）と、#1142 のコメントに同じ表で残す。G1〜G3 は `value` を比べ、前の値の行に手の表の値と打ち直しの `all_roles` を並べ、規則ごとの差があれば理由を書く。U1 の行に目標 1.14M との差を書く（観測下限のときは「判定不能」と下限の値と欠けた入力を書く）。30 日の値は 2026-12-18 以降に、上の 30 日の期間で同じコマンドを打って足す（リリース後の確認の計画）。
