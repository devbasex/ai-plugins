# プロジェクトの宣言を解析する

**NDF は、使い始めたリポジトリを手順 0 で解析し、そのプロジェクトの形を `.ndf/project.json` に書き出す。**
各 Skill とスクリプトは、ai-plugins の形（pytest を uv で・`develop` 宛て・版数を持つプラグインの配布）を既定にせず、
この宣言を読む。宣言に無い項目は、リポジトリから決まる値（origin の HEAD など）か「不明」として扱う。

## 例: carmo-system-console で手順 0 を通す

1. `project-decl.py check` が 2 で終わる（`.ndf/project.json` が無い）
2. conductor が `project-decl.py measure --out "$TMP/measure.json"` を打つ。言語（PHP 8.3・Laravel 10）・CI の JUnit の合計・
   ワークフロー・必須のチェック・compose の `mysql` を「測った値」として、テストの走らせ方・起点と本番・配布・課題の正本・
   NDF の方針の検査を「問い」として、候補と根拠を付けて返す
3. conductor が問いに答える。`AGENTS.md` の `docker compose exec app ./vendor/bin/phpunit` の行を根拠にテストを
   「PHPUnit・`app` のコンテナ越し」と決め、マージ先が `main` に揃っていることから起点と本番を `main` と決め、答えのファイルを書く
4. `project-decl.py write` が `.ndf/project.json` を作り、`.ndf/worktree.json` に欠けていた `base_branch`・`production_branch` だけを足す。
   書いた差分と、項目ごとの出所（測った / 判断した / 不明 / 手の値を保った）が出る
5. 手順 0 の出力が `宣言: あり。解析: 作成した（.ndf/project.json・.ndf/worktree.json。不明 無し）` になる

## 手順 0 での扱い

手順 0 は `worktree-setup.sh check` に続けて `project-decl.py check` を打つ（`decl=` の行）。`worktree-setup.sh check` が 3 のときは見ない。

| `decl=` | 次に行うこと | `宣言:` の行に足す `解析:` の部分 |
| --- | --- | --- |
| 0 | 手順 1 へ進む | `解析: 不要（新しい）` |
| 2 | 下の 3 手（measure → 答え → write）を通し、手順 1 へ進む | `解析: 作成した（<書いたファイル>。不明 <P 番号>）` / `解析: 更新した（変わった入力 <パス>）` / `解析: 更新した（手動）` / `解析: できなかった（<理由>）` |
| 3 | **先へ進まない。** `check` の出力を示して利用者に直してもらう | 出さない |
| 1、または `$SCRIPTS` を決められない | 手順 1 へ進む | `解析: 判定できない（<理由>）` |

`宣言:` の行は `宣言: <worktree の結果>。解析: <解析の結果>` の形にする。解析が失敗した・時間を超えたときも工程を止めない。
書き出した宣言はコミットしない限り取り消せるので、承認を求めない。

## 3 手

```bash
TMP="$(mktemp -d)"
python3 "$SCRIPTS/project-decl.py" measure --out "$TMP/measure.json"; echo "exit=$?"
# 測定の結果の status: question の項目に答え、"$TMP/answers.json" を書く（下の「答え方」）
python3 "$SCRIPTS/project-decl.py" write --measure "$TMP/measure.json" --answers "$TMP/answers.json"; echo "exit=$?"
```

| 手 | 終了コード | 次に行うこと |
| --- | --- | --- |
| measure | 0 | 答えを書く。項目が不明でも 0 で終わる（`gh` が使えない・時間切れの項目は `status: unknown`） |
| measure | 1 | `解析: できなかった（<理由>）` を出して手順 1 へ進む |
| write | 0 | 出力の差分・出所・食い違い・案内を、手順 0 の `宣言:` の行と利用者への報告に載せる |
| write | 1 | `解析: できなかった（書けない: <理由>）` を出して手順 1 へ進む |
| write | 3 | 既存の宣言が壊れている。作り直さずに止まり、出力の箇所を利用者に直してもらう |

`measure` の締め切りは 120 秒（`--budget`）で、`gh` の 1 回の呼び出しは残りと 30 秒の小さい方で打ち切る。
`measure` が書くのは `--out` だけで、リポジトリの中へは置けない。`gh` へは読む要求（`gh api --method GET`）だけを渡す。

CI の壁時計（`ci.workflows[].wall_seconds`）とテストの所要の `ci-steps`・`ci-junit` は、ワークフローごとの代表の run から測る。
代表の run は、直近 50 件の成功の run のうち、そのワークフローの新しい順に最大 5 件を見て、ジョブを飛ばしていない最新の 1 件である。
ジョブを飛ばした run は、conclusion が `skipped` のジョブを 1 つ以上含む run で、ワークフローやジョブの名前では判定しない。
5 件ともジョブを飛ばしていたワークフローは、壁時計を持たない行になり、注記に `飛ばしていない run が無い: <パス>（候補 <n> 件）` が出る。
候補のジョブの取得が時間切れになったら、そのワークフローから後は壁時計を持たない行になり、注記に `候補のジョブを取れない（時間切れ）: <パス>` が出る。

## 答え方

**答えるのは、測定の結果で `status: question` の項目だけである。** 測った値（`status: measured`）は答えで変えられない。
ほかのキーを書くと `write` は無視し、`無視した答え` の行を出す。

- 値は候補（`candidates`）から選ぶ。候補に無い値を書くときは、根拠（`evidence`）の行を 1 つ以上挙げる
- 根拠が無い・決められない項目は `{"unknown": "<理由>"}` と書く。ai-plugins の値（`develop`・`uv run ... pytest` など）で埋めない
- 秘密の値（`.env` の中身・トークン・接続文字列）を書かない。書いても `write` が捨てて `{"unknown": "秘密の形"}` にする
- `reason` は出力にだけ載り、宣言には書かれない

| 項目 | 答えの `value` の形 | 決め方 |
| --- | --- | --- |
| `test`（P2） | `{"strategy"?, "ci"?: {"check"?, "junit_artifacts"?}, "suites": [{"name", "runner", "command", "scope_command"?, "junit"?, "container"?: {"service", "compose_files"}, "needs", "paths", "kind"?}]}` | 指示書・README の走らせ方の行を正とする。コンテナ越しなら `container` を書き、`scope_command` の `{paths}`（引用の外の、空白で区切った 1 語）にシェルの引用で守った範囲のパスが入る。`command` と `scope_command` はシェルで走らせる。`kind` は suite の種別で、`test`（既定）か `lint`（静的解析。整形の検査を含む）。静的解析の suite は変更したファイルのうち `paths` に当たるものにかかり、`paths` の要素には接頭辞のほかに glob（`*.sh` など）を書ける。`junit` はコマンドが JUnit XML を書く相対パス（pytest は `-o junit_family=xunit1 --junitxml=<パス>` をコマンドに書く。無ければ落ちたテストの見分けが全体の走らせ直しへ落ちる）。`strategy` は `local-full`（全体テストを手元で走らせる）/ `local-scoped-ci-whole`（範囲は手元、全体は CI に任せる。`ci.check` に待つチェック、`ci.junit_artifacts` に JUnit の成果物の名前の glob）/ `round-only`（パスでテストを選べない。suite の `command` をそのまま走らせる）。空なら所要と suite から導く（600 秒を超え CI が読めれば `local-scoped-ci-whole`。`scope_command` が無ければ `round-only`。ほかは `local-full`）ので、その既定を答えとして示し、手元で全体テストを回さない方針があるときだけ書き換える |
| `branches`（P6） | `{"base": "<起点>", "production": "<本番>"}` | origin の HEAD・直近 100 件のマージ先・最後のコミットの日時を見る。長くコミットの無いブランチ（`develop` など）は採らない |
| `delivery`（P7） | `[{"target", "kind": "auto" / "manual", "trigger", "branch"?, "versioned", "production"?}]` | 配布の設定ファイル・ワークフローの `deploy` の行・版数の置き場とタグから決める。`production`（本番系へ届くか。[pace.md](pace.md) の「手動反映の本番系の形」）は解析では書かず、利用者が書く |
| `issues`（P8） | `{"primary", "others"}`。名前は `github`・`markdown`・`redmine`・`external` など | 名前だけを書き、URL や識別子を書かない |
| `ndf_policies`（P9） | `{"doc_lint": bool, "reject_md_wording_tests": bool}` | 指示書に NDF の文体の規則・`.md` の文言テストを退ける方針があるときだけ `true`。無ければ両方 `false` |

答えのファイルの例:

```json
{"test": {"value": {"strategy": "local-scoped-ci-whole",
                    "ci": {"check": "test-results", "junit_artifacts": "junit-*"},
                    "suites": [{"name": "phpunit", "runner": "phpunit",
                                "command": "docker compose exec -T app ./vendor/bin/phpunit --log-junit build/ndf/junit.xml",
                                "scope_command": "docker compose exec -T app ./vendor/bin/phpunit --log-junit build/ndf/junit.xml {paths}",
                                "junit": "build/ndf/junit.xml",
                                "container": {"service": "app", "compose_files": ["docker-compose.yml"]},
                                "needs": ["mysql"], "paths": ["tests"]}]},
          "reason": "AGENTS.md:34 の手順。全体は約 64 分で CI が 24 本に分けて回すため CI に任せる"},
 "branches": {"value": {"base": "main", "production": "main"}, "reason": "マージ先の多くが main。develop が無い"},
 "delivery": {"unknown": "デプロイの設定がリポジトリに無い"}}
```

`value` が項目の形に合わなければ、`write` はその項目を `{"unknown": "答えが形に合わない: <箇所>"}` にして続ける。

## 書き出しの規則

- 手で書いた値は変えない。変えるのは、キーが無い項目と、前の `write` が書いた値のまま（`analysis.written` の指紋が一致する）の項目だけ。
  解析の値と食い違えば、出力に `宣言 <値> / 解析 <値>` の行が出る
- 新しい解析で不明になった項目は、前の値を残す
- `ci` のワークフローの行に新しい解析の壁時計が無ければ、前の解析の同じパスの行の壁時計を残し、出力に `ci#<パス>: 不明（新しい解析に壁時計が無い）（前の値を残した）` の行が出る
- 秘密の形の値は、手で書いた値でも捨てて `{"unknown": "秘密の形"}` にする。値は宣言・差分・出力のどれにも出ない
- `.ndf/worktree.json` には、`base_branch`・`production_branch` のうち無いキーだけを足す。ほかのキーと順序は変えない
- 解析の結果が前と同じなら、ファイルを書かない
- 宣言はメインディレクトリの `.ndf/` に書き、コミットは利用者に任せる。要らなければコミットしない。`.gitignore` の対象なら、そのことが出力に出る

宣言の形は [../schemas/project.schema.json](../schemas/project.schema.json) にある（`project-decl.py schema` の生成物）。

## 指示書の案内

`write` の出力の `案内:` の行に、指示書の状態が出る。解析は指示書を書き換えない。

| 状態 | 案内 |
| --- | --- |
| 指示書（`AGENTS.md`・`CLAUDE.md`・`.claude/CLAUDE.md`・`KIRO.md`・`GEMINI.md`）が 1 つも無い | 指示書が無いこと |
| 先頭に `NDF_PLUGIN_GUIDE_START` の印を持つファイルを、指示書が `@` で読み込んでいる | 古い NDF の案内の読み込みを外すか消すこと |

## 再解析

**構成の変化を宣言へ映す経路は、自動と手動の 2 つである。** どちらも同じ 3 手と同じ書き出しの規則を通る。

### 自動の再解析

`check` は、解析が読んだ入力の指紋（下の表のファイルの HEAD の blob・ブランチの構成・解析器の版）が記録と違えば 2 を返し、
`変わった入力: <パス>` を出す。手順 0 はそのまま 3 手を通し、`解析: 更新した（変わった入力 <パス>）` を出す。
自動の再解析は手順 0 でだけ走る。ほかの工程は、始めに宣言をそのまま読む。

| 分類 | 入力のパス（根と 1 階層下） |
| --- | --- |
| CI | `.github/workflows/*` |
| 依存の定義 | `composer.json`・`package.json`・`pyproject.toml`・`requirements*.txt`・`Gemfile`・`go.mod`・`Cargo.toml` |
| テストの設定 | `phpunit.xml*`・`pytest.ini`・`tox.ini`・`vitest.config.*`・`jest.config.*` |
| コンテナ | `compose*.y*ml`・`docker-compose*.y*ml` |
| 配布 | `amplify.yml`・`samconfig.toml`・`buildspec.yml`・`appspec.yml` |
| 指示書 | `AGENTS.md`・`CLAUDE.md`・`.claude/CLAUDE.md`・`KIRO.md`・`GEMINI.md` |

コミットしていない編集・ロックファイルの変更では古くならない。

### 手動の再解析

自動では拾わない変化（CI の結果だけの変化・CI を分けた・JUnit を出し始めた・表の外のファイルでテストの走らせ方や配布を変えた）の後は、
次の 1 つのコマンドで始める。

```bash
python3 "$SCRIPTS/project-decl.py" check --force
```

`--force` は宣言が新しくても 2 を返し、`変わった入力: 強制（--force）` を出す。壊れた宣言（3）と判定できない（1）は変わらず、
ファイルも書かない。2 を受けた後は、上の 3 手を通す。答えを書くのは conductor で、利用者が `check --force` だけを打っても宣言は変わらない。
手順 0 の `解析:` の部分は `更新した（手動）` にする。
