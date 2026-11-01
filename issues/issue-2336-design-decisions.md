# #2336 の設計: 決定の記録

設計文書の本体は [issue-2336-design.md](issue-2336-design.md) にある。#2335 の設計はまだ無い（2026-11-01 の時点で `issues/issue-2335-design*.md` が無い）ため、前提 5 に従い、契約の一覧の書き方と置き場はこの設計が決める。

## 決定の記録

### 決定 1: 並列のプランがぶつからずに足せるよう、契約の一覧を 1 項目 1 ファイルの JSON にする

契約の一覧は `plugins/ndf/contracts/` の下に、エントリポイント・受け渡しのファイル・設定ファイル・環境変数の 4 つのディレクトリを置き、1 項目を 1 つの JSON ファイルにする。並列のプランが別々のエントリポイントを触っても同じファイルを変えないため、マージで衝突しない（`scripts/script-structure-allow/` が同じ理由で 1 項目 1 ファイルにした）。形の検査は標準ライブラリだけで書く。CI の `script-structure` のジョブは依存を入れない素の `python3` で動き、`jsonschema` は入っていない。

採らなかった案: 1 つの大きな JSON（76 本と 4 種類の表で数千行になり、並列のプランが毎回衝突する）、JSON Schema のファイル（検査に依存が要る。データの型の書き方は柱 P6・#2339 が決める）、Python の型（実行時に import され、配布物の読み込みの順に関わる）。P6 が型の書き方を決めたら、項目の形はそちらへ移す。

根拠: Value 4 / Value 6（MVV 版 3）

### 決定 2: 実行時にも同じ宣言を読めるよう、契約の一覧を配布物の `plugins/ndf/contracts/` に置く

設定の置き場（決定 9）は実行時に `lib/decl.locate` が契約の一覧から読む。柱 P8（#2341）の入力の読み込み部品も同じ一覧を読む見込みである。配布しない `scripts/` に置くと、実行時に読めない。一覧は `factory/` を参照しないため、`check-service-boundary.py` に当たらない。

採らなかった案: `scripts/` に置き、実行時に要る値（置き場）を `lib/` の定数へ写す（同じ値を 2 か所が持ち、受け入れ条件 2 に反する）。

根拠: Value 6 / Value 7（MVV 版 3）

### 決定 3: 既存のチェックの所要と行数を増やさないため、構造チェックを別のスクリプトにし、例外リストと import のたどり方を共通部品へ移す

`scripts/check-ndf-contracts.py` を新しく作り、`check-script-structure.py` には規則を足さない。後者は既に 956 行で 8.7 秒かかり、規則を足すと 1 ファイルの読み手の負担と所要の両方が増える。2 つのチェックが同じ役割の関数（例外の項目の読み込み・ファイル名の規則・`unused-allow` の判定・エントリポイントからの import のたどり）を別々に持たないよう、`scripts/lib/allowlist.py` と `scripts/lib/py_imports.py` へ移し、両方が使う。移しても `check-script-structure.py` の結果は変えない。

採らなかった案: `check-script-structure.py` へ規則を足す（前提 7 の 1 つ目の案。上の理由）、例外リストの部品を写して持つ（同じ役割の関数を 2 か所に持つ）。

根拠: Value 6 / Value 10（MVV 版 3）

### 決定 4: 例外リストが増えないことを CI で確かめるため、ベースブランチの merge-base と比べる

ラチェットは `git ls-tree` で merge-base の例外リストを読み、今の木にだけある項目を `allow-added` で落とす。起点は `GITHUB_BASE_REF` → `.ndf/worktree.json` の `base_branch` の順に決め、`origin/<名前>` と HEAD の merge-base を取る。CI のジョブは `fetch-depth: 0` で取得する。同じ差分で同じ `kind`・`name` の項目を消している追加は、ファイルの移動に伴う付け替えとして増加に数えない。CI の中で起点を決められなければ 3 で止まり、手元では `ratchet: skipped` を出して続ける（手元に origin が無い木でも他の検査を使えるようにする）。merge-base に `scripts/check-ndf-contracts.py` が無い変更（構造チェックを入れる変更。E6 で今の外れを載せ、同じ変更で CI へ足す）は初版として増加を数えず `ratchet: initial` を出す。初版を例外リストの置き場（`scripts/contract-allow/`）の有無で決めないのは、置き場を丸ごと消した次の変更で好きなだけ足せてしまうためである。

採らなかった案: 例外の項目の数を別のファイルに持ち比べる（数を 2 か所が持つ）、コミットの印で追加を許す（印を付ければ増やせ、ラチェットにならない）。既存の `script-structure-allow/` へのラチェットは、この課題の受け入れ条件に無いため入れない。

根拠: Value 4 / Value 3（MVV 版 3）

### 決定 5: 静的に拾える範囲を固定するため、抽出の規則を絞り、拾えない箇所は種類を分けて例外の項目に載せる

| 対象 | 拾う規則 | 拾えないとき |
| --- | --- | --- |
| エントリポイント | 前提 1 の規則（`.py` は最上位の `if __name__ == "__main__":`、`.sh` は 1 行目の `#!`）。`experimental/` とテストを除く | — |
| 引数 | `add_argument` の文字列の引数・`add_parser` / `add_subparsers` の名前。シェルは `case` の `--名前)` | `args-unparsed`（`sys.argv` を手で読む） |
| 環境変数 | `os.environ.get("X")`・`os.environ["X"]`・`os.getenv("X")`・`"X" in os.environ`。エントリポイントから import でたどれるモジュールを含む。シェルはファイルの中で代入されない大文字の変数 | `env-dynamic`（名前を組み立てる・走査する） |
| 設定のキー | 設定ファイルの項目の `readers` の関数の呼び出しの戻り値を代入した名前への、同じ関数の中の `.get("k")`・`["k"]`。最上位のキーだけ | 読み手の関数の外で開けば `setting-open-outside-reader` |
| ファイルの書き | 書く呼び出し（`open` の `w`/`a`/`x`・`write_text`・`write_bytes`・`lib/jsonio` の書く関数）の引数に現れる文字列か、同じモジュールの定数の文字列のファイル名。シェルは `>`・`>>` の文字列 | `file-dynamic` |

拾った事実は、エントリポイントから届きうる読み書き（呼ばれるかは追わない）として扱う。

採らなかった案: 呼び出しの流れを追う解析（`require-groups` が import に絞ったのと同じ理由で、費用に見合わない）、設定をすべて 1 つの読み込み部品へ移してから検査する（入力の読み込み部品は柱 P8 の範囲）。

根拠: Value 10 / Value 1（MVV 版 3）

### 決定 6: 読み手と書き手を 2 か所で持たないため、エントリポイントの項目を正とし、逆引きは生成する

読み手・書き手は、エントリポイントの項目の `env`・`settings`・`reads`・`writes` だけが持つ。受け渡しのファイル・設定・環境変数の項目は、それ自身の属性（置き場・種類・範囲・誰が設定するか）だけを持つ。逆引き（どのファイル・設定・変数を誰が読み書きするか）は `--render` が `contracts/index.md` へ生成し、構造チェックが作り直した結果との一致を見る（用語集の `glossary.py render` と同じ形）。Skill の本文が読み手のときだけ、ファイルの項目の `skill_readers` に書く（コードから拾えないため）。

採らなかった案: 両側に書いて一致を検査する（同じ事実を 2 か所に持つ）。

根拠: Value 7 / Value 6（MVV 版 3）

### 決定 7: 本番のブランチを 1 か所だけが持つよう、`worktree.json` の `production_branch` に寄せ、`delivery` の行は `channel` で指す

本番のブランチの名前は `.ndf/worktree.json` の `production_branch`（無ければ既定ブランチ）だけが持ち、決め方は `lib/repo.production_branch` の 1 か所にする（`supervise_lib/decl.py:257` と `lib/delivery.py:132` の独自の後退をやめる。#2097）。`worktree.json` に寄せるのは、ベースブランチ（`base_branch`）と並ぶ値であり、シェル（`lib/worktree-branch.sh`）と `project-decl.py write` も既にここを読み書きするためである。`project.json` の `delivery` の行は、本番のブランチやベースブランチへのマージで動くことを `"channel": "production"` / `"base"` で指し、名前を写さない。`branch` はほかのブランチで動く行と、移行の読みに残す（`channel` が無い行の `branch` が本番のブランチと同じなら、今までどおり本番の行として読む）。`project-decl.py write` は新しく書く行を `channel` で書く。

採らなかった案: `delivery` の `branch` へ寄せる（行ごとに値が分かれ、行の無いリポジトリでは本番のブランチを持てない）、両方を残して一致を検査する（受け入れ条件 5 に反する）。このリポジトリの `.ndf/project.json` の行を `channel` へ書き換えるのは C7 に当たり、承認ゲート 2 で人が見る。

根拠: Value 6 / Value 5 / C7（MVV 版 3）

### 決定 8: テストのコマンドを 1 か所だけが持つよう、`project.json` の `test.suites` に寄せる

`supervise.json` の `test.command`・`test.all` は、`project.json` の `test.suites` が無いときだけ読む移行の読みにする（#2011）。`test.suites` はテストの戦略（`lib/test_strategy.py`）の正で、JUnit の出力・範囲のコマンド・CI のジョブを持つ。`supervise.json` の 2 つのキーはその一部を写した値である。`test.no_reports`（pytest のプラグインを外す指定）は別の値なので残す。古い置き場だけを持つリポジトリでは、`test.command` から suite を 1 つ組んで渡し、寄せる前と同じコマンドで動く。

採らなかった案: `supervise.json` に寄せる（suite の属性を持てない）。

根拠: Value 6 / Value 5（MVV 版 3）

### 決定 9: PR が変えた宣言を使うよう、チームの設定は呼ばれた木から、個人の設定はメインディレクトリから読み、置き場を 1 つの関数で解く

設定の置き場は 2 つの規則で決め、設定ファイルの項目の `place` に書く。

| 範囲 | どう見分けるか | 置き場 | 無いとき |
| --- | --- | --- | --- |
| チーム | git が追跡する（`.ndf/.gitignore` に載らない） | `worktree`（呼ばれた木の根） | メインディレクトリを読む |
| 個人 | `.ndf/.gitignore` に載る（`worktree.local.json` など） | `main` | 呼ばれた木を読む |

チームの設定は PR の差分に入るため、PR の worktree で動くエントリポイントがその変更を使う必要がある（#2317 は外したチェックを、#1709 は足した suite を、メインディレクトリの古い宣言から読んだ）。個人の設定は追跡されず worktree に写らないため、メインディレクトリにしか無い。読み手はすべて `lib/decl.locate` を通し、`lib/project_decl.py` のメインディレクトリを先に読む順をやめる。`locate` のメインディレクトリは、共通ディレクトリの `core.worktree` を先に見る（サブモジュールでは `--git-common-dir` の親が `.git/modules/<名前>` になる。#2054、2026-11-01 に実測）。

採らなかった案: すべてメインディレクトリから読む（PR の変更が効かない）、すべて呼ばれた木から読む（個人の設定が worktree に無い）、読み手ごとに順を選ばせる（今の不一致の原因）。`project-decl.py write` がメインディレクトリへ書くことは変えない（書いた値は PR を通って worktree へ届く）。

根拠: Value 6 / Value 5（MVV 版 3）

### 決定 10: 呼び手の無いものを今後も残さないため、棚卸しは vulture と参照の検索で 1 回行い、以後は契約の `callers` で見る

最上位の関数の候補は `uvx vulture`（`--min-confidence 60`。2026-11-01 に 121 行）で集め、副命令のハンドラー（`cmd_*`）や文字列で呼ばれる関数を人の目で外してから消す。エントリポイントの呼び手は、エントリポイントの名前で Skill の本文・雛形のモジュール・`hooks/*.json`・`.github/workflows/`・ほかのスクリプトを検索して決め、項目の `callers` に書く。以後は構造チェックが `callers` の各ファイルがエントリポイントの名前を含むかを見る（I8）。

採らなかった案: vulture を CI の常設の検査にする（動的な呼び出しの誤検出が多く、例外の項目が増える）、自前で呼び出しの解析を書く（既存のライブラリで足りる）。消す変更は承認ゲート 2 で人が見る（要求の「確認してから行う」）。

根拠: Value 4 / Value 6（MVV 版 3）

### 決定 11: hook が起動するエントリポイントも契約に含め、hook の処理ごとの約束は含めない

`hooks/*.json` の command が起動する 7 本（`hook.py`・`relay.py` など）も前提 1 の規則に当たり、契約の項目を持つ。`relay.py` は利用者が手で打つ入口でもあり、外すと契約の一覧から主要な入口が抜ける。対象範囲の「hook が直に呼ぶモジュールの契約」は、`hook.py` から import される処理ごとの約束（関数の入出力）と読み、持たない。

採らなかった案: hook から起動されるものを規則で外す（`relay.py` が抜ける）。

根拠: Value 6（MVV 版 3）

### 決定 12: 91 本と規則の本数の差を残すため、数え上げの結果を `metrics` に出す

規則が拾うのは 76 本で、要求の参考値 91 本との差 15 本は import と source で読まれるライブラリである（前提 1 は規則の本数を正とする）。構造チェックは `metrics` に `entrypoints`（規則の本数）と `candidates`（直下の `.py`・`.sh` の数）を出し、差を毎回読めるようにする。

根拠: Value 3（MVV 版 3）
