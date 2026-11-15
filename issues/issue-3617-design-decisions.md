# #3617 の設計の決定

設計文書は [issue-3617-design.md](issue-3617-design.md) にある。外部の調査の番号（`STD1` など）はその文書の「外部の調査」の表を指す。

## 決定の記録

### 決定 1: 例外リストの件数と受け持ちの Issue の番号を持てるよう、ハードコードの検査は今の検査を広げる

今の検査（`scripts/check-skill-repo-assumptions.py`）は、（ファイル・語）ごとの件数・理由・種類（`not-a-violation` と、Issue の番号を必須にする `pending`）を 1 項目 1 ファイルで持ち、件数より増えれば 1、減れば 2 で落とす。要求の S3（件数と理由）と S12（他レーンの受け持ちを例外で扱う）をそのまま満たすため、走査の対象を広げるだけにする。ndf-next の検査のエントリポイント（`ndf check`）へ新しい規則として足すことは採らない。`ndf check` は兆候の基準のファイルを持たない方針で、例外の件数の仕組みを別に作り直すことになるためである。

既存の検出のツールは採らない。detect-secrets（LIB1）は既知の検出を記録する形は同じだが、検出が秘密の値に限られる。betterer（LIB2）は JavaScript の実行環境を足し、latest が先行版である。semgrep（LIB3）は語の一致だけの検査に対して依存が大きく、例外の件数と Issue の番号は自前で足すことになる。

- 作り方: 自前で作る
- 比べた候補: LIB1 / LIB2 / LIB3

根拠: Value 11 / Value 12（MVV 版 7）

### 決定 2: 今の NDF の件数を変えないため、追加の語は ndf-next の形のプラグインにだけ当てる

追加の語は `repo-name`（`ai-plugins`）・`org-name`（`devbasex`）・`marketplace-entry`（`ndf@`）・`remote-origin`（コードとコントラクトの中のリモート名の文字列。`"origin"`・`origin/`・`remotes/origin`・`remote.origin`・`remote "origin"`）・`parity-table`（機能の対応表への参照）の 5 つである。要求の前提 6 の名前のうち、`ndf--v`・`marketplace.json`・`plugin.json`・`develop` は今の語（`tag-form`・`marketplace-json`・`plugin-manifest`・`develop-branch` ほか）が受ける。

ndf-next の形のプラグインは、Skill の一覧を `manifests/skills.json` に持つプラグインと決める。今の NDF は `manifests/*-skills.txt` を持つため、走査の対象と語は今と同じになり、要求の前提 2 が成り立つ。入れ替え（S2）で今の NDF が ndf-next の形になれば、追加の語も当たる。

走査は、Skill の本文とエージェントに今の文書の語、`src/` の Python・`contracts/` の JSON・`hooks/*.py` に今のスクリプトの語を当て、どちらにも追加の語を足す。`remote-origin` はコードとコントラクトにだけ当てる（Skill の本文の手順の `origin` は範囲の外。決定 13）。

`remote-origin` は、`contracts/` の JSON Schema の `default` の値を走査しない。既定の値を持つのはスキーマの `default` であり（決定 5）、既定を書く正しい置き場を違反として数えると、例外リストに常に 1 件残るためである。外すのは `default` という鍵の値だけで、ほかの鍵（`description`・`enum`・`examples` など）と Python のコードは走査する。代わりに、`remote-origin` に当たる `default` の値が `worktree.schema.json` の `remote` の 1 か所だけであることを検査が数え、2 か所以上か 0 か所なら終了コード 2 で落とす（設計文書の I7a）。

根拠: Value 4（MVV 版 7）

### 決定 3: 例外リストの意味を 1 つにするため、当たりは直すか 2 つの種類のどちらかに分ける

ndf-next の今の当たりは、次の規則で 1 件ずつ分ける。

| 当たり | 扱い |
| --- | --- |
| 点検の一覧 7・10・11・13〜20 の場所 | 直す。例外リストへ載せない（受け入れ条件 22） |
| 他レーンが受け持つ場所（点検の一覧 1〜3・8・9・12 の場所と、#3613 が置き換える語） | 種類 `pending`、Issue の番号は受け持ち（#3613・#3615・#3616） |
| NDF 自身の公開の置き場（スキーマの `$schema`・`$id` の URL、初期値の出所の URL）と、言語の表の正当な行（`contracts/domains/test-files.json` の `pytest` など）・プラグイン自身の定義の読み手（`identity.py` の `plugin.json`） | 種類 `not-a-violation`、理由を書く |
| どれにも当たらない | 直すか、範囲外の課題として起票して `pending` にする |

`pending` で残すのは他レーンの直しを待つ場所だけである。この課題の直しを例外リストへ逃がすと、受け入れ条件 22 を検査が確かめられなくなる。

根拠: Value 7 / Value 13（MVV 版 7）

### 決定 4: 選べる値の減少を 1 か所で止めるため、基準のファイル `scripts/ndf-choices.json` と根の検査を置く

要求の未決 5 の置き場は、NDF 自身の開発の検査とその例外を置く根の `scripts/` にする（ハードコードの検査と例外リストと同じ置き場）。基準は集合ごとに出所（事実の表の行・スキーマの列挙・ファイル名の glob の 3 種）と値の一覧を持ち、値を減らすときは `removed` に Issue の番号と理由を持つ項目を足す。

**基準に無い値がコントラクトに増えても落とす**（I6）。増えた値を黙って通すと、基準がその値を知らないまま、後で消えても検査が気づかない。I6 は要求の S4（減ったら落とす）より範囲が広く、値を足す PR に基準のファイルの更新を求めるため、入れるかを設計承認ゲートで人が決める（設計文書の「人へ残す判断」）。外す場合は I6 と、そのテストの行を消す。リリースの形の値はまだ機械が読む表を持たないため、形ごとの文書（`skills/release/references/form-*.md`）のファイル名から読む。#3615 が形の表を置いたら、その PR が出所を表へ替える。

検査は stdlib の JSON の読み取りだけで書き、ndf-next の依存の環境を要らないようにする。CI では今のジョブ `ndf-next-checks` に 1 ステップを足す。新しいジョブを作ると、必須のチェックの宣言（`scripts/required-checks-allow.json`）か ruleset の変更が要る。

ラチェットの形（記録と比べて後退したら落とす）は LIB1・LIB2 と同じで、値の集合の後退を見るツールは見つからなかったため自前で書く。

- 作り方: 自前で作る
- 比べた候補: LIB1 / LIB2

根拠: Value 11 / Value 13（MVV 版 7）

### 決定 5: リモート名の事実を 1 か所に持つため、宣言 `.ndf/worktree.json` の `remote` で受け、既定 `origin` はスキーマの `default` だけが持つ

リモート名は、ベースブランチ・本番ブランチと同じく git の運用の宣言なので `.ndf/worktree.json` に置く。git 自身が既定を `origin` とし、設定で変えられる（STD2）。既定の値はスキーマの `default` に 1 回だけ書き、リモート名を決める部品（`adapters/git.py`）がそれを読む。コードに定数を持つとスキーマと 2 か所になり、ハードコードの検査（`remote-origin`）の例外を 1 件残すことになる。スキーマの `default` はハードコードの検査の走査の外とし、1 か所だけであることを検査が数える（決定 2）。

リモート名を決める部品は最も下の層の 1 つ（アダプタ）に置き、テンプレート（`{{remote}}`）・ルート・収束・工程・各領域がそこから引く。今の `git.ORIGIN` を使う部品のうち、この課題が直すもの（`domains/release/verify.py` ほか、設計の結果の触るファイルにあるもの）は、リモート名を引数か部品から受ける。

要求の前提 5 に従い、#3615 が受け持つ `domains/release/gather.py`・`assemble.py` は直さない。この 2 ファイルが読む `git.ORIGIN` は名前を残し、値を文字列 `origin` ではなくスキーマの `default` から読む形にする。`adapters/git.py` にリモート名の文字列は残らず（受け入れ条件 8）、2 ファイルは変えずに今と同じ値を受ける。#3615 がこの 2 ファイルを直すときに、`git.ORIGIN` をリモート名を決める部品の呼び出しへ替えて `git.ORIGIN` を消す。それまで、宣言に別の名前を書いたリポジトリでもリリースの部品は既定のリモート名を使う。これを受け入れ条件 8 の範囲としてよいかは、設計文書の「人へ残す判断」で人が確かめる。

個人設定（`.ndf/worktree.local.json`）には置かない。フォークの運用のように手元ごとに名前が違う使い方は、今の要求に無い。

- 作り方: 標準に倣う
- 比べた候補: STD1 / STD2

根拠: Value 4 / Value 11（MVV 版 7）

### 決定 6: 文書だけの判定を 1 つにするため、テンプレート `check` の `assess` を宣言 `docs` で分ける判定へ寄せ、振る舞いの文書の既定を空にする

文書だけとみなすパスの受け口は、既に `.ndf/project.json` の `docs`（`files`・`behavior`）がある。工程の条件（`code_in_diff` など）はこれを読むが、テンプレート `check` の `assess` は `.md` をシェルに直に書いていた。`assess` は副命令 `progress` の下の命令を呼び、工程と同じ判定を使う。照合は git の pathspec の glob で行い、GitHub Actions の `paths-ignore`（PRD1）と同じく「変えたファイルがすべて当たれば飛ばす」とする。

振る舞いの文書の既定（`**/skills/**/*.md`・`**/agents/*.md`）は空にする。Skill の本文が振る舞いであるのは ai-plugins の事情で（要求の前提 3）、ai-plugins は `.ndf/project.json` の `docs.behavior` にその 2 つを書く。今の NDF も同じ宣言を読むが、書く値は今の NDF の既定と同じなので、今の NDF の振る舞いは変わらない。

今の NDF の既定と読み方は、次のとおりである（2026-11-15 に確かめた）。

```console
$ grep -n 'DEFAULT_BEHAVIOR =' plugins/ndf/scripts/lib/diff_kinds.py
38:DEFAULT_BEHAVIOR = ("**/skills/**/*.md", "**/agents/*.md")
$ grep -n 'behavior = ' plugins/ndf/scripts/lib/diff_kinds.py
102:    behavior = _patterns(docs["behavior"], "docs.behavior") if docs.get("behavior") is not None else DEFAULT_BEHAVIOR
$ python3 -c "import json; print('behavior' in json.load(open('.ndf/project.json'))['docs'])"
False
```

今の NDF は `docs.behavior` があればその値を、無ければ `DEFAULT_BEHAVIOR` を使う。ai-plugins の宣言には今 `docs.behavior` が無いので、今の NDF は既定の 2 つのパターンで判定している。同じ 2 つのパターンを宣言に書けば、今の NDF が判定に使うパターンは変わらない。振る舞いを決める文書は文書のファイルの部分集合であるという今の NDF の決まり（`diff_kinds.py` の I3）も、ai-plugins の `docs.files` が `**/*.md` を持つため満たす。

- 作り方: 自前で作る・標準に倣う
- 比べた候補: PRD1 / LIB4

根拠: Value 3 / Value 4（MVV 版 7）

### 決定 7: 初期値を利用先で上書きできるよう、初期値のファイルに印を付け、宣言 `.ndf/estimate.json` の `allocation_minutes` で受ける

配分の値は 1 件あたりの所要（分）の見積りで、見積りの単位を持つ宣言 `.ndf/estimate.json` に置く。初期値のファイルは `"initial": true` と、上書きの置き場を書いた `summary` を持つ。出所（#917 の実測の URL）は残す。

ndf-next には配分の表を作る部品がまだ無い（cross-refactoring を移す子 M4 の範囲）。この課題は、初期値と宣言を重ねる判断の部品（`domains/crossrefactor/allocation_decide.py`）を置き、M4 が配分の表をこの部品から作る。初期値を上限の宣言（`contracts/limits.json`）へ移すことは採らない。上限の方針の形（秒・倍率・回数）が種類ごとの分の表に合わない。

根拠: Value 4 / Value 11（MVV 版 7）

### 決定 8: 旧い NDF をどの登録名でも外せるよう、`claude plugin list --json` の一覧から外す登録を決め、読めなければ外さずに起動する

外す登録は、一覧の要素のうち `enabled` が真で、`id` の `@` の前が旧い NDF の名前（`identity` が持つ入れ替えの後の名前）であり、プラグインの根の名前と違うものとする。根の名前と同じものを外さない規則は今のコードと同じである。一覧は文書化された CLI の出力（STD3）を読み、文書に無い導入先のファイル（`installed_plugins.json`）は読まない。

要求の未決 4（一覧が読めないとき）は、外す登録を空にして起動し、結果の `items` に知らせを 1 件載せる。止めると、`claude` の CLI の版の違いだけで Skill を回す起動が全部止まる。外さずに起動したときに起きるのは、旧い NDF が並んで読まれることで、今のハードコードの登録名が合わない環境と同じである。

- 作り方: ライブラリやサービスを使う
- 比べた候補: STD3

根拠: Value 4 / Value 3（MVV 版 7）

### 決定 9: スキーマと食い違わないよう、リリースの workflow は宣言に無いブランチを GitHub の既定ブランチで補う

スキーマは、`base_branch`・`production_branch` が無いとき origin の HEAD が指すブランチを使うと定める。GitHub の上でそれに当たるのはリポジトリの既定ブランチである。workflow はこの値を GitHub から読み、`jq` の既定値に渡す。ai-plugins は両方を宣言に書いているため、結果は変わらない。

根拠: Value 4（MVV 版 7）

### 決定 10: 機能の対応表をリリース対象の外で使い続けるため、`scripts/ndf-next-parity/` へ移し、テストは ndf-next の依存の環境で流す

要求の未決 1 の移す先は、根の `scripts/` の下にする。対応表は表の読み手と試走の判定（Python）を持つため、知識を置く `docs/` と計画を置く `issues/` には合わない。`scripts/` は NDF 自身の開発の検査とその材料の置き場である。

対応表の読み手は `jsonschema` を使い、根の環境はこれを持たない（`uv.lock` に無い）。テストは ndf-next の依存の環境（`plugins/ndf-next/uv.lock`）で流し、`ndf-next.yml` のテストのジョブに移した先を足す。根の依存へ `jsonschema` を足すことは採らない。同じ版の固定が 2 か所になる。

対応表を読むのは、対応表の読み手・試走の判定・対応表と Skill の一覧の一致を見るテストの 3 つだけで、`ndf check` の規則は対応表を読まない（設計文書の「機能の対応表の読み手」に検索の結果がある）。移した 3 つはプラグインの根を引数で受け、既定はリポジトリの根の `plugins/ndf-next/` とする。名前を入れ替えた一時コピーの中では流さない。

対応表と Skill の一覧の一致を見るテスト（`tests/domains/manifests/test_skill_table.py`）も、対応表の側へ移す。子の表（`children.json`）の `X3` の `packages` から `ndf.parity` を外し、対応表の `places` の `X3` を移した先にする。試走の判定は、プラグインの根を引数で受ける（既定は今の ndf-next の置き場）。

Skill とエージェントの冒頭の注記（104 ファイル）は消す。注記が伝えていた「旧い名前のまま残した副命令と行き先の子」は、対応表の行が持つ。

根拠: Value 6 / Value 11（MVV 版 7）

### 決定 11: hook の登録を壊さずに利用者向けの一覧を絞るため、`check`・`manifests` は一覧から隠し、`hooks` は残す

要求の未決 2 は副命令ごとに決める。

| 副命令 | 扱い | 理由 |
| --- | --- | --- |
| `check` | 一覧から隠す | 見る範囲は ndf-next 自身だけで、利用者のリポジトリで使わない |
| `manifests` | 一覧から隠す | `render` はプラグインの根のファイルを作り、利用者のリポジトリで使わない |
| `hooks` | 一覧に残す | `stats` は利用者の環境の hook の所要を出す。`render`・`capture`・`check-budget` は開発のものだが、同じ副命令の下にある |

隠すのは使い方の表示だけで、名前で呼べば動く。CI は `bin/ndf check` を打ち、名前を入れ替えた一時コピーでも同じ検査を打つため、根の `scripts/` へ移すとその仕組みを作り直すことになる。受け入れ条件 25 も `src/ndf/checks/children.json` の置き場を前提にする。開発の検査の実体がリリース対象に残ることは、範囲外の課題として起票する。

根拠: Value 1 / Value 6（MVV 版 7）

### 決定 12: 入れ替えの前にこの直しが入るよう、子の表に `X4`（順番 9・レーン `lgeneric`）を足し、`S2` の `needs` に `X4` を足す

要求の未決 3 を決める。記号は、子の表の横断の土台の子（`X0`〜`X3`。`X3` は機能の対応表を作った子）の続きの `X4` にする。この課題は特定の領域の部品を作り直すのではなく、ndf-next 全体の既定・同梱物・検査を直すためである。

| 列 | 値 |
| --- | --- |
| 記号 | `X4` |
| 番号 | #3617 |
| `packages` | 無し（新しいサブパッケージを作らない。足すモジュールは既存のパッケージの持ち主に入る） |
| `needs` | 無し |
| 順番 | 9（試走 `T2` と同じ。入れ替え `S2` の 10 より前） |
| レーン | `lgeneric`（並列の計画 p6 でこの課題を流すレーン） |

`S2` の `needs` に `X4` を足し、入れ替えがこの直しを待つ。#1142 の本文の子の表にも同じ行を足す。`children.json` と本文の表は、この設計 PR をマージした後の実装で直す（`children.json` の決まり）。この文書が、この子の行の正本である。

根拠: Value 5（MVV 版 7）

### 決定 13: 範囲を点検の一覧の場所に保つため、Skill の本文の手順のリモート名は直さずに範囲外として起票する

Skill の本文の git の手順は、`origin` を約 190 行書く（2026-11-15、`grep -rnE '"origin|origin/|\borigin\b' plugins/ndf-next/skills`）。点検の一覧 10 はコードとコントラクトの 5 か所で、受け入れ条件 8 は ndf の取得・照合・差分の基準を対象にする。本文の手順は #3613 が全体の語を置き換えている最中で、同じ 190 行を並べて直すと衝突が増える。範囲外の課題として起票し、ハードコードの検査の `remote-origin` は Skill の本文に当てない。

根拠: Value 5 / Value 7（MVV 版 7）
