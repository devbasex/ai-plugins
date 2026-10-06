# 必須のチェックの突き合わせ: ワークフローのジョブと ruleset の必須のチェックの差を Pull Request ごとに落とす

## 概要

- **Pull Request で走るジョブは、すべて必須のチェックに載るか、理由つきで宣言される。** ジョブを足したのに
  ruleset の `required_status_checks` へ入れ忘れると、そのチェックは落ちてもマージを塞がない。
  突き合わせのチェック（ジョブ `required-checks-check`、`scripts/check-required-checks.py`）が、ワークフローの
  ジョブから決まるチェックの名前・宛先のブランチの必須のチェック・宣言（`scripts/required-checks-allow.json`）の
  3 つを読み、差があれば落ちる
- **突き合わせのチェックは読むだけである。** ruleset へ書き込まない。ruleset を変えるのは承認ゲート 2 で
  利用者が打つ 1 回の `PUT` だけである
- **必須のチェックの一覧の解釈は 1 か所に置く。** `rules/branches/<branch>` の応答から名前を取り出すのは
  `plugins/ndf/scripts/lib/ci_workflows.py` の `required_contexts` で、突き合わせのスクリプトと解析
  （`plugins/ndf/scripts/project_lib/measure_ci.py` の `_required_checks`）が共有する
- **文書は必須のチェックの数を持たない。** 一覧の正は ruleset で、
  `gh api repos/<owner>/<repo>/rules/branches/develop` で読む（[版と配布の正本](../versioning-and-distribution.md)）

例: 手元で `develop` に向けて走らせる（ruleset の必須のチェックが 12 個で、7 つが追加待ちの時点）。

```console
$ python3 scripts/check-required-checks.py --repo devbasex/ai-plugins --branch develop; echo "exit=$?"
追加待ち: doc-line-limit-check（scripts/required-checks-allow.json の awaiting）→ 承認ゲート 2 で ruleset へ足す
追加待ち: glossary-check（scripts/required-checks-allow.json の awaiting）→ 承認ゲート 2 で ruleset へ足す
…（lint・pr-body-decisions-check・required-checks-check・script-structure-check・skill-shell-vars-check）
失敗 0 件・知らせ 7 件
exit=0
```

## 用語

定義は [用語集](../glossary.md) が正である（`.ndf/glossary.json` の `document`）。この文書で使う語は
「必須のチェック」「チェックの名前」「必須にしないジョブ」「突き合わせのチェック」「追加待ちのチェック」
「承認ゲート」である。

## 背景

ruleset の必須のチェックはジョブを足した側から自動では増えない。`doc-line-limit-check`・`skill-shell-vars-check`・
`lint` と、ジョブ名が `check` の 3 つ（PR body decisions・Glossary・Script structure）は、Pull Request で走るのに
必須のチェックに無く、落ちてもマージできた。赤い印を人が目で拾う運用になり、見落とすと行数の基準を超えた文書や
決定の節を欠いた設計 Pull Request が入る。ジョブ名 `check` は 3 つのワークフローで重なり、必須に入れても指す先が
名前から決まらなかった。2026-10-06 に利用者が 6 つとも必須にすると決め、あわせて入れ忘れを機械で見つける
チェックを足した。

## 決定と理由

- **名前の重なりは改名で消す。** `check` の 3 つは job id を `pr-body-decisions-check`・`glossary-check`・
  `script-structure-check` へ変える（`needs` で参照されず、名前と id が揃う）。2 つの `ci-scope` は
  `needs.ci-scope.outputs` と文書が job id で指すため、id を残して `name:` を `pytest-scope`・
  `runtime-smoke-scope` に分ける。重なりを必須にしないジョブどうしなら許すと、後で必須にしたときに同じ問題が起きる
- **突き合わせのチェックはこのリポジトリのチェックに留め、引数で受ける。** `scripts/check-*.py` の前例に揃える。
  他のプロジェクトへ配るには配る経路と他のリポジトリでの実測が要る。リポジトリ名・ブランチ・置き場を既定に
  埋め込まないため、後で移す費用は小さい
- **宣言はスクリプトの隣（`scripts/required-checks-allow.json`）に置く。** 宣言はジョブを変える差分と一緒に変わり、
  読むのは突き合わせのスクリプトだけである。`.ndf/` に置くと書き換えのたびに承認（C7）が要る
- **ruleset へ足す前のチェックは追加待ちとして宣言し、差に数えない。** 実装の Pull Request の時点で ruleset は
  古いままで、何もしなければ突き合わせのチェック自身が落ち、`merge-when-green`（必須でないチェックの失敗でも止まる）
  が止まる。追加待ちは名前ごとに理由を持ち、承認ゲート 2 で足す名前の一覧になる。足した後は「追加済み」を
  知らせて成功のまま残す（失敗にすると、宣言から外す Pull Request が入るまですべての Pull Request が落ちる）
- **読めないときと宛先のブランチが無いときは 2、規則を持たない実在のブランチは 0。** 存在しないブランチの
  `rules/branches` も `[]` を終了コード 0 で返すため、空の一覧として扱うとブランチ名の誤りがすべてのジョブの
  未登録に化ける。一方、規則を持たない実在のブランチ（`sprint/*` など）を 2 にすると、そこへの Pull Request が
  すべて落ちる。規則が無いときだけ `GET branches/<branch>` で見分ける。ジョブに宛先のブランチの条件を置くと
  ruleset の対象を変えたときに追従を忘れるため置かない
- **必須の一覧の解釈だけを共有し、取得と失敗の扱いは呼ぶ側に残す。** 解析は止まらないことを優先して空で続け、
  突き合わせのスクリプトは止める。失敗の扱いが逆なので、取得まで共有すると分岐の引数が要る
- **ワークフローは `yamlio`（ruamel.yaml）で構造として読む。** `ci_workflows.job_ids` の字下げの読み取りでは
  `strategy.matrix` の `include` / `exclude` や `name:` の式を読めない
- **名前を静的に決められないジョブと `paths` で絞ったジョブは失敗に倒す。** 推測した名前で突き合わせると未登録を
  見落とす。`paths` で絞ったジョブを必須にすると、対象外の変更で結果が来ずマージが塞がる
- **文書は必須のチェックの数を持たず、一覧を読むコマンドを指す。** 数の写しはジョブを足すたびに古くなる
- **ruleset の変更は承認ゲート 2 で利用者がコマンドを打つ。** スクリプトに書き換えの副コマンドを持たせると、
  管理者の権限を持つトークンが要り、承認の外で ruleset が変わる経路ができる
- **突き合わせのジョブは `runtime-plugin-validate.yml` に置く。** 新しいワークフローを足すと、cross-refactoring の
  `init` が宣言に無いジョブとして数え、`.ndf/project.json` の `test.ci_exempt` への追記（C7）が要る

## 仕様

### 常に成り立つ条件

| # | 条件 | 破れたときの扱い |
| --- | --- | --- |
| I1 | 宛先のブランチへの Pull Request で走るジョブのチェックの名前は、すべてのワークフローを通して一意である | 「名前の重なり」で 1 |
| I2 | すべてのジョブのチェックの名前を、Pull Request に載る名前と同じ形で静的に決められる | 「名前を決められない」で 1（そのジョブの `name:`、無ければ job id を `not_required` に宣言すれば数えない） |
| I3 | チェックの名前は、必須のチェック・`not_required`・`awaiting` のどれかに当たる | 「未登録」で 1 |
| I4 | 必須のチェックは、どれかのジョブのチェックの名前に当たる | 「消えた必須」で 1 |
| I5 | 宣言の各行は空でない `reason` を持つ | 「理由の無い宣言」で 1 |
| I6 | 宣言の各行の名前は、どれかのジョブのチェックの名前（名前を決められないジョブの `name:` / job id を含む）に当たる | 「古い宣言」で 1 |
| I7 | `not_required` の名前は必須のチェックに載らない | 「宣言と ruleset の食い違い」で 1 |
| I8 | `awaiting` の名前が必須のチェックに載ったら、宣言から外す | 「追加済み」を知らせて 0 のまま |
| I9 | `pull_request` の起動を `paths` / `paths-ignore` で絞ったワークフローのジョブは、必須のチェックにも `awaiting` にも置かない | 「絞り込みのあるジョブ」で 1 |
| I10 | 必須の一覧を読めないとき（宛先のブランチが存在しないときを含む）、成功で終わらない | 理由を標準エラーへ出して 2。規則を持たない実在のブランチは「対象外」を知らせて 0 |
| I11 | 突き合わせのスクリプトが打つ `gh api` は `--method GET` だけである。ジョブの権限は `contents: read` だけである | テストが落ちる |

### コマンド: `python3 scripts/check-required-checks.py`

| 項目 | 内容 |
| --- | --- |
| 入力 | `--root`（既定 `.`）・`--repo <owner>/<name>`（既定は環境変数 `GITHUB_REPOSITORY`）・`--branch <名前>`（既定は環境変数 `GITHUB_BASE_REF`）・`--workflows`（既定 `ci_workflows.WORKFLOW_DIR` = `.github/workflows`）・`--allow`（既定 `scripts/required-checks-allow.json`）・`--rules-file <path>`（`rules/branches` の応答の JSON を API の代わりに読む。テストと手元の確かめ用）。相対パスは `--root` から解く |
| 出力（標準出力） | 1 件 1 行で `<種類>: <チェックの名前>（<どこ>）→ <直し方>`。最後に `失敗 <n> 件・知らせ <m> 件`。並びは失敗の種類が先で、種類の表の順、同じ種類の中は名前の順 |
| 出力（標準エラー） | 読めないときだけ `読めない: <理由>` の 1 行。差の行は出さない |
| 終了コード | 0: 失敗の種類が無い（知らせはあってよい）/ 1: 失敗の種類が 1 件以上 / 2: 読めない |
| 外部の呼び出し | `gh api --method GET repos/<repo>/rules/branches/<branch>`（`--rules-file` があれば打たない）と、応答に `required_status_checks` の規則が無いときだけ `gh api --method GET repos/<repo>/branches/<branch>`（`--rules-file` があっても打つ）。1 回の上限は 30 秒（`CALL_LIMIT`） |
| 書き込み | しない |

「どこ」は、ジョブなら `<ワークフローのパス>#<job id>`（重なりは両方を `・` でつなぐ）、宣言なら
`<宣言のパス> の not_required|awaiting`、消えた必須なら `ruleset の必須のチェック` である。

種類:

| 種類 | 失敗か | 条件 | 直し方として出す文の要旨 |
| --- | --- | --- | --- |
| 未登録 | 失敗 | I3 | ruleset へ足す（追加待ちに置く）か、必須にしないジョブとして理由つきで宣言する |
| 消えた必須 | 失敗 | I4 | ruleset から外すか、ジョブの名前を戻す |
| 名前の重なり | 失敗 | I1 | どちらかの `name:` か job id を変える |
| 名前を決められない | 失敗 | I2 | `name:` を matrix の式だけにするか、必須にしないジョブとして宣言する |
| 理由の無い宣言 | 失敗 | I5 | `reason` を書く |
| 古い宣言 | 失敗 | I6 | 宣言から外す |
| 宣言と ruleset の食い違い | 失敗 | I7 | `not_required` から外すか、ruleset から外す |
| 絞り込みのあるジョブ | 失敗 | I9 | `paths` の絞り込みを外すか、必須にしないジョブとして宣言する |
| 追加待ち | 知らせ | — | 承認ゲート 2 で ruleset へ足す |
| 追加済み | 知らせ | I8 | 追加待ちの宣言から外す |
| 対象外 | 知らせ | I10 | 何もしない（この行と件数の行だけを出して 0 で終える） |

読めない（2）の理由: `--branch` が空（`GITHUB_BASE_REF` も空）・`--repo` が空（`GITHUB_REPOSITORY` も空）・
`gh` が無い・`gh api` が 0 以外で終わる（終了コードと標準エラーの先頭行を添える）・30 秒の時間切れ・応答が
規則の配列でない・規則が無く `GET branches/<branch>` が HTTP 404（宛先のブランチが無い）か失敗・ワークフローの
置き場が無い・ワークフローか宣言が YAML / JSON として読めない・宣言がオブジェクトでない・宣言に未知のキーがある・
`not_required` / `awaiting` が `name` を持つオブジェクトの並びでない。

### 対象のワークフローとジョブ

- `.github/workflows/` の直下の `.yml` / `.yaml` を名前の順に読む
- `on` に `pull_request` か `pull_request_target` を持つワークフローだけを対象にする。`branches` /
  `branches-ignore` があれば宛先のブランチに当てる（`*` は `/` を除く、`**` はすべて、`?` は 1 文字、
  `!` の否定は後に書いたものが勝つ）
- `uses:` で再利用するジョブは名前を決められない

### チェックの名前の決め方

| 場合 | 名前 |
| --- | --- |
| matrix が無い | `name:`、無ければ job id。`name:` に式があれば決められない |
| matrix があり、`name:` に `${{ matrix.<キー> }}` がある | 組ごとに式を値で置き換えた `name:`（`pytest (${{ matrix.shard }}/2)` → `pytest (0/2)`）。matrix 以外の式があるか、組にそのキーが無ければ決められない |
| matrix があり、`name:` に式が無い（`name:` が無いときを含む） | 組のキー（`include` で足したキーを含む）が 1 つなら `<name: か job id> (<値>)`（`runtime-smoke` → `runtime-smoke (claude)`）。キーが 2 つ以上なら決められない |

matrix の組は、一覧の値を持つキーの直積から `exclude` に当たる組を除き、`include` の各行を、元のキーの値が
一致する組へ足す（どの組にも足せなければ新しい組にする）。値は文字列・数・真偽（`true` / `false`）だけを受け、
式で書いた matrix・空の一覧・スカラーでない値は決められない。

### 宣言のファイル: `scripts/required-checks-allow.json`

```json
{
  "version": 1,
  "not_required": [
    {"name": "pytest (0/2)", "reason": "分けたジョブの結果は pytest が 1 つにまとめる。必須は pytest が持つ"}
  ],
  "awaiting": [
    {"name": "doc-line-limit-check", "reason": "#653 で必須にすると決めた。承認ゲート 2 で ruleset へ足す"}
  ]
}
```

- 根のキーは `version`・`not_required`・`awaiting` だけ、各行のキーは `name`・`reason` だけを受ける
- `name` は空でない文字列でなければ読めない（2）。`reason` の欠けや空は「理由の無い宣言」（1）
- `not_required` には、集約される前の分割ジョブ（`pytest (0/2)`・`pytest (1/2)`）と、重いジョブを省くかの判定だけの
  ジョブ（`pytest-scope`・`runtime-smoke-scope`）を置く。`awaiting` には ruleset へ足す承認を待つ名前を置く

### 共有の関数: `ci_workflows.required_contexts(rules) -> list[str] | None`

`rules/branches/<branch>` の応答（規則の配列）から、`type` が `required_status_checks` の規則の `context` を
重複なく整列して返す。規則が 1 つも無ければ `None`（規則を持たないブランチと存在しないブランチの見分けは呼ぶ側が
持つ）、配列でなければ `ValueError`。解析の `_required_checks` は `None` と `ValueError` を空の一覧として扱い、
時間切れ以外の取得の失敗でも空で続ける。

### 承認ゲート 2 で ruleset へ足す手順

ruleset（`protect main and develop`、`main` と `develop` が対象）は管理者が 1 回の `PUT` で書き換える。
承認ゲートの止まり方と承認資料の形は `development-workflow` の `references/approval-request.md` が正である。

1. 改名したジョブと突き合わせのチェックが `develop` に入っていることを確かめる。改名の前に ruleset へ足すと、
   改名の前のブランチから出た Pull Request が結果の来ない必須のチェックで塞がる
2. `--branch develop` で走らせた「追加待ち」の行を、足す名前の一覧として承認資料に載せる。
3. 管理者のトークンで ruleset を取得し、`bypass_actors` が空でない配列で `conditions` がオブジェクトであることを
   `jq -e` で確かめる。書き込み権限の無いトークンの応答は `bypass_actors` を含まず、本文と比較の両側がどちらも
   `null` で揃って食い違いを見逃すためである。満たさなければ本文を作らずに止める
4. 取得した ruleset の書き込める欄（`name`・`target`・`enforcement`・`conditions`・`bypass_actors`・`rules`）を
   すべて持ち、`required_status_checks` だけに名前を足した本文を作る。足した名前を除くと取得した欄と同じである
   こと（`diff` の出力が空）を承認資料に添える。`bypass_actors` を落とすと、リリースの Pull Request を管理者の
   bypass でマージする運用が壊れる

   ```bash
   ADD='["doc-line-limit-check", "..."]'
   gh api repos/devbasex/ai-plugins/rulesets/22332172 > /tmp/ruleset-before.json
   jq -e '(.bypass_actors|type=="array" and length>0) and (.conditions|type=="object")' \
     /tmp/ruleset-before.json > /dev/null \
     || { echo "bypass_actors か conditions が取得できない。管理者のトークンで取り直す" >&2; exit 1; }
   jq --argjson add "$ADD" \
     '{name, target, enforcement, conditions, bypass_actors,
       rules: [.rules[] | if .type == "required_status_checks"
         then .parameters.required_status_checks += [$add[] | {context: .}] else . end]}' \
     /tmp/ruleset-before.json > /tmp/ruleset-put.json
   diff <(jq -S '{name, target, enforcement, conditions, bypass_actors, rules}' /tmp/ruleset-before.json) \
        <(jq -S --argjson add "$ADD" \
            '.rules |= map(if .type == "required_status_checks"
              then .parameters.required_status_checks |= map(select(.context as $c | $add | index($c) | not)) else . end)' \
            /tmp/ruleset-put.json)
   ```

5. 承認を得て、4 の `diff` が空のときだけ書き換える

   ```bash
   gh api --method PUT repos/devbasex/ai-plugins/rulesets/22332172 --input /tmp/ruleset-put.json
   ```

   書き換えの後、`gh api repos/devbasex/ai-plugins/rules/branches/develop` と `.../main` の必須のチェックに
   足した名前が載り、もとの 12 個も残ること（7 つを足せば 19 個）と、`bypass_actors`・`conditions`・
   `enforcement` が `ruleset-before.json` と同じであることを確かめる
6. 突き合わせのチェックが「追加済み」を知らせる。その名前を `awaiting` から外す Pull Request（`light`）を出す

## 運用

- **ジョブを足す・改名するときは、同じ Pull Request で宣言を直す。** 必須にするなら `awaiting` へ、必須にしない
  なら `not_required` へ理由つきで置く。ruleset に載った名前を改名・削除すると「消えた必須」で落ちるため、
  ruleset からも外す（承認ゲート 2）
- **ジョブは `runtime-plugin-validate.yml` の `required-checks-check`。** `if: github.event_name == 'pull_request'`
  で `push`（`main`・`develop`）では起動しない（`GITHUB_BASE_REF` が空で宛先が決まらないため）。権限は
  `contents: read`、`GH_TOKEN` に `github.token` を渡し、`--repo "${{ github.repository }}" --branch "${{ github.base_ref }}"`
  で起動する
- **`.ndf/project.json` の `ci.required_checks` は解析が書いた写しである。** ruleset を変えた後は古くなり、次の解析で
  作り直す

## テスト観点

テストは `scripts/tests/test_check_required_checks.py` と `plugins/ndf/scripts/tests/test_lib_ci_workflows.py` にある。

- 12 個の必須の応答（`--rules-file`）とこのリポジトリのワークフロー、`not_required` だけの宣言で、未登録が
  `doc-line-limit-check`・`skill-shell-vars-check`・`lint`・`pr-body-decisions-check`・`glossary-check`・
  `script-structure-check`・`required-checks-check` の 7 つだけになり、`runtime-smoke (claude)` などの matrix の名前と
  `pytest` は出ないこと
- `name:` の matrix の式が組ごとの値に置き換わり、式の無い `name:` には `(<値>)` が付くこと。`exclude` / `include` が
  GitHub の規則どおりに組を作ること
- `name:` に matrix 以外の式・`uses:`・式の matrix が「名前を決められない」で 1 になり、その名前を `not_required` に
  宣言すれば数えないこと
- どこにも当たらない名前が、名前と `<パス>#<job id>` つきの「未登録」で 1 になること
- どのジョブにも当たらない必須のチェックが「消えた必須」で 1 になること
- 同じチェックの名前を持つ 2 つのワークフローが「名前の重なり」で 1 になり、このリポジトリのワークフローでは重なりが
  出ないこと
- Pull Request で起動しないワークフローを対象にしないこと
- 理由の無い宣言・どのジョブにも当たらない宣言・`not_required` と必須の両方に載った名前が、それぞれ 1 になること
- `awaiting` が必須に無ければ「追加待ち」、載れば「追加済み」の知らせで 0 になること
- `paths` で絞ったジョブを必須か `awaiting` に置くと「絞り込みのあるジョブ」で 1、`not_required` なら出ないこと
- 宣言・ワークフローが読めないとき、`--branch` が空のとき、応答が配列でないとき、`gh` が無い・失敗・時間切れのとき、
  規則が無く宛先のブランチが 404 か失敗のときに、理由を出して 2 で終わり、差の行を出さないこと
- 規則が無く宛先のブランチが実在するとき、「対象外」を知らせて 0 で終わること
- 打つ `gh api` に `--method GET` が付き、書き込みのメソッドが無いこと
- `required-checks-check` のジョブが `if: github.event_name == 'pull_request'` と `permissions: contents: read` だけを持つこと
- `required_contexts` が規則をまたいで整列・重複除去し、規則が無ければ `None`、配列でなければ `ValueError` になること
- 文書の記述（数を持たないこと・「必須にする」と決めた記述）は `.md` の文言を照合するテストで縛らない。レビューで読む

## 関連リンク

- [`scripts/check-required-checks.py`](../../scripts/check-required-checks.py)
- [`scripts/required-checks-allow.json`](../../scripts/required-checks-allow.json)
- [`plugins/ndf/scripts/lib/ci_workflows.py`](../../plugins/ndf/scripts/lib/ci_workflows.py)
- [版と配布の正本](../versioning-and-distribution.md)（`main` / `develop` の保護と、必須のチェックの一覧の読み方）
- [ndf-workflow-unit-and-gates.md](ndf-workflow-unit-and-gates.md)（`pr-body-decisions-check` を必須にする決定）
- [project-decl-ci-representative-run.md](project-decl-ci-representative-run.md)（解析が CI の所要を測るときの必須のチェックの扱い）
- 課題: #653
