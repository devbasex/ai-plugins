# プロジェクトの設定を解析する

**NDF は、使い始めたリポジトリを手順 0 で解析し、そのプロジェクトの形を `.ndf/project.json` に書き出す。**
各 Skill とスクリプトは、NDF のリポジトリの形（pytest を uv で・本番チャネルと別のベースブランチ宛て・版数を持つプラグインのデプロイ）を既定にせず、
この設定を読む。設定に無い項目は、リポジトリから決まる値（origin の HEAD など）か「不明」として扱う。

## 例: carmo-system-console で手順 0 を通す

1. `ndf project check` が 3 で終わる（`.ndf/project.json` が無い）
2. conductor が `ndf project measure --out "$TMP/measure.json"` を打つ。言語（PHP 8.3・Laravel 10）・必須のチェック・
   compose の `mysql` を「測った値」として、テストの走らせ方・課題の SSOT・
   NDF の方針の検査を「質問」として、候補と根拠を付けて返す
3. conductor が質問に回答する。`AGENTS.md` の `docker compose exec app ./vendor/bin/phpunit` の行を根拠にテストを
   「PHPUnit・`app` のコンテナ越し」と決め、回答のファイルを書く
4. `ndf project write` が `.ndf/project.json` を作る。書いた差分と、項目ごとの出所（測った / 判断した / 不明 / 手の値を保った）が出る
5. 手順 0 の出力が `設定: あり。解析: 作成した（.ndf/project.json。不明 無し）` になる
6. ブランチ戦略の設定（セクション `branch_strategy`）は `ndf project branch-strategy measure` で測り、質問に回答して書く（下の「ブランチ戦略の設定」）

## 手順 0 での扱い

手順 0 は `ndf worktree check` に続けて `ndf project check` を打つ（`decl=` の行）。`ndf worktree check` が 0 以外のときは見ない。

| `decl=` | 次に行うこと | `設定:` の行に足す `解析:` の部分 |
| --- | --- | --- |
| 0 | 手順 1 へ進む | `解析: 不要（新しい）` |
| 3 | 下の 3 手（measure → 回答 → write）を通し、手順 1 へ進む | `解析: 作成した（<書いたファイル>。不明 <P 番号>）` / `解析: 更新した（変わった入力 <パス>）` / `解析: 更新した（手動）` / `解析: できなかった（<理由>）` |
| 2 | **先へ進まない。** `check` の出力を示して利用者に直してもらう | 出さない |
| 1 | 手順 1 へ進む | `解析: 判定できない（<理由>）` |

`設定:` の行は `設定: <worktree の結果>。解析: <解析の結果>` の形にする。解析が失敗した・時間を超えたときも工程を止めない。
書き出した設定はコミットしない限り取り消せるので、承認を求めない。

## 3 手

```bash
TMP="$(mktemp -d)"
ndf project measure --out "$TMP/measure.json"; echo "exit=$?"
# 測定の結果の status: question の項目に回答、"$TMP/answers.json" を書く（下の「回答方」）
ndf project write --measure "$TMP/measure.json" --answers "$TMP/answers.json"; echo "exit=$?"
```

| 手 | 終了コード | 次に行うこと |
| --- | --- | --- |
| measure | 0 | 回答を書く。項目が不明でも 0 で終わる（`gh` が使えない・時間切れの項目は `status: unknown`） |
| measure | 1 | `解析: できなかった（<理由>）` を出して手順 1 へ進む |
| write | 0 | 出力の差分・出所・食い違い・案内を、手順 0 の `設定:` の行と利用者への報告に載せる |
| write | 1 | `解析: できなかった（書けない: <理由>）` を出して手順 1 へ進む |
| write | 2 | 既存の設定が壊れている。作り直さずに止まり、出力の箇所を利用者に直してもらう |

`measure` の締め切りは 120 秒（`--budget`）で、`gh` の 1 回の呼び出しは残りと 30 秒の小さい方で打ち切る。
`measure` が書くのは `--out` だけで、リポジトリの中へは置けない。`gh` へは読む要求（`gh api --method GET`）だけを渡す。

**CI について測るのは、既定ブランチの必須のチェック（`ci.required_checks`）だけである。**
`gh` で呼ぶ API は、必須のチェック（`rules/branches`）・閉じた PR（`pulls`）・課題（`issues`）の 3 つだけである。
CI の実行時間・JUnit の合計・ワークフローの一覧・言語ごとのファイル数は、読む仕組みが無いので測らない（ https://github.com/devbasex/ai-plugins/issues/4516 で測るのをやめた）。
テストの走らせ方（`test`）は、手がかりがあれば質問（`status: question`）になり、無ければ回答の要らない値になる。
質問のときは、下の「回答方」の表のとおり、指示書・README のテストの走らせ方の行から suite を決め、`ci_jobs` はワークフローのファイルで確かめて回答する。
テストの実行器・テストを走らせるコマンド・指示書や README のテストの記述がどれも無いリポジトリでは、`test` は測った値（`status: measured`）の空の suite の一覧になるので、回答しない。

## 回答方

**回答するのは、測定の結果で `status: question` の項目だけである。** 測った値（`status: measured`）は回答で変えられない。
ほかのキーを書くと `write` は無視し、`無視した回答` の行を出す。

- 値は候補（`candidates`）から選ぶ。候補に無い値を書くときは、根拠（`evidence`）の行を 1 つ以上挙げる
- 根拠が無い・決められない項目は `{"unknown": "<理由>"}` と書く。NDF のリポジトリの値（`uv run ... pytest` など）や特定のブランチ名で埋めない
- 秘密の値（`.env` の中身・トークン・接続文字列）を書かない。書いても `write` が捨てて `{"unknown": "秘密の形"}` にする
- `reason` は出力にだけ載り、設定には書かれない

| 項目 | 回答の `value` の形 | 決め方 |
| --- | --- | --- |
| `test`（P2） | `{"strategy"?, "ci"?: {"check"?, "junit_artifacts"?}, "suites": [{"name", "runner", "command", "scope_command"?, "junit"?, "container"?: {"service", "compose_files"}, "needs", "paths", "kind"?, "ci_jobs"?}]}` | 指示書・README の走らせ方の行を正とする。コンテナ越しなら `container` を書き、`scope_command` の `{paths}`（引用の外の、空白で区切った 1 語）にシェルの引用で守った範囲のパスの並びが入る。入るパスの種類は場所で違い、cross-refactoring の改修の範囲テストではテストのファイルのパス（計画の `test_targets` の `::` より前）、全体テストを CI に任せる戦略の着手前のテストでは `--scope` のテストの置く場所（ディレクトリかファイル）、静的解析の suite では変更したファイルのパスが入る。テストの `scope_command` はテストのファイルのパスを受けるコマンドで書く。ディレクトリしか受けないランナーは、範囲テストをファイルで受ける形と全体テストをディレクトリで走らせる形に分ける（unittest なら `scope_command` を `python3 -m unittest {paths}`、`command` を `python3 -m unittest discover -s <テストのディレクトリ>` にする。`python3 -m unittest tests/test_x.py tests/test_y.py` と `discover -s tests` はどちらも終了コード 0、`discover -s tests/test_x.py` は 1、テストの無いディレクトリを渡した `discover -s scripts/storylib` は 5 で終わる）。ファイルを受けない `scope_command` は、cross-refactoring の `init` がテストのファイルを入れた試走で見分けて改修の前に止める。`command` と `scope_command` はシェルで走らせる。`kind` は suite の種別で、`test`（既定）か `lint`（静的解析。整形の検査を含む）。静的解析の suite は変更したファイルのうち `paths` に当たるものにかかり、`paths` の要素には接頭辞のほかに glob（`*.sh` など）を書ける。`junit` はコマンドが JUnit XML を書く相対パス（pytest は `-o junit_family=xunit1 --junitxml=<パス>` をコマンドに書く。無ければ落ちたテストの見分けが全体の走らせ直しへ落ちる）。`strategy` は `local-full`（全体テストを手元で走らせる）/ `local-scoped-ci-whole`（範囲は手元、全体は CI に任せる。`ci.check` に待つチェック、`ci.junit_artifacts` に JUnit の成果物の名前の glob）/ `round-only`（パスでテストを選べない。suite の `command` をそのまま走らせる）。解析はテストの所要時間を測らないので、`strategy` は指示書・README に書かれたテストの方針から選ぶ。既定は、`scope_command` が無ければ `round-only`、あれば `local-full` である。手元で全体テストを回さない方針が指示書・README にあるときだけ、`local-scoped-ci-whole` に変え、その行を根拠（`evidence`）に挙げる。所要時間を理由に `local-scoped-ci-whole` を選ぶときは、所要時間を確かめた出所（指示書の記述や CI の実行の画面）を根拠に挙げる。方針も所要時間も確かめられないときは、既定のままにする。`ci_jobs` は suite と同じ検査を走らせる CI のジョブ（`<ワークフローのファイル>#<job id>` か `<ワークフローのファイル>`）で、ワークフローのファイルから回答する。CI テストの設定（`ci.only`）は回答しない。書き出しが、テストの suite の `ci_jobs` のファイルがリポジトリにあれば `true` を書き、条件を満たさなくなれば解析が書いた値を消す（人が書いた値は変えない）。設定があれば手元でテストを走らせず、合否を PR の CI の結果で決める |
| `issues`（P8） | `{"primary", "others"}`。名前は `github`・`markdown`・`redmine`・`external` など | 名前だけを書き、URL や識別子を書かない |
| `ndf_policies`（P9） | `{"doc_lint": bool, "reject_md_wording_tests": bool}` | 指示書に NDF の文体の規則・`.md` の文言テストを退ける方針があるときだけ `true`。無ければ両方 `false` |
| `materials` | `{"product"?: {"kind", "users"?}, "needs"?: {"usage", "market"}, "seeds"?: {"libraries", "environment"}, "deferred_milestones", "cause_types"?, "code_measures"?, "failures"?}` | 棚卸しの見直しと大整理が読む材料の設定（`backlog-refinement` の `references/research.md`）。候補は README の冒頭と直接の依存から出る。調べる依存（`seeds.libraries`）は候補から人が残すものだけにする。利用の記録（`needs.usage`）の読むコマンドと範囲（`scope.aggregate_only`・`days`・`mask`）・市場と競合・周辺環境・後回しのマイルストーンは人が書く。決められなければ `{"unknown": "<理由>"}` と書き、見直しはすべての側面を飛ばす |

回答のファイルの例:

```json
{"test": {"value": {"strategy": "local-scoped-ci-whole",
                    "ci": {"check": "test-results", "junit_artifacts": "junit-*"},
                    "suites": [{"name": "phpunit", "runner": "phpunit",
                                "command": "docker compose exec -T app ./vendor/bin/phpunit --log-junit build/ndf/junit.xml",
                                "scope_command": "docker compose exec -T app ./vendor/bin/phpunit --log-junit build/ndf/junit.xml {paths}",
                                "junit": "build/ndf/junit.xml",
                                "container": {"service": "app", "compose_files": ["docker-compose.yml"]},
                                "needs": ["mysql"], "paths": ["tests"]}]},
          "reason": "AGENTS.md:34 の手順。全体は約 64 分で CI が 24 本に分けて回すため CI に任せる"}}
```

`value` が項目の形に合わなければ、`write` はその項目を `{"unknown": "回答が形に合わない: <箇所>"}` にして続ける。

## 書き出しの規則

- 手で書いた値は変えない。変えるのは、キーが無い項目と、前の `write` が書いた値のまま（`analysis.written` の指紋が一致する）の項目だけ。
  解析の値と食い違えば、出力に `設定 <値> / 解析 <値>` の行が出る
- 新しい解析で不明になった項目は、前の値を残す
- 秘密の形の値は、手で書いた値でも捨てて `{"unknown": "秘密の形"}` にする。値は設定・差分・出力のどれにも出ない
- ブランチ名とブランチ戦略（`branch_strategy`）は書かない。書くのは `ndf project branch-strategy` だけである
- 解析の結果が前と同じなら、ファイルを書かない
- 設定はメインディレクトリの `.ndf/` に書く。コミットするかは下の「`.ndf/` の追跡」に従う。`.gitignore` の対象なら、そのことが出力に出る

設定の形は [../schemas/project.schema.json](../schemas/project.schema.json) にある（`ndf project schema` の生成物）。

## `.ndf/` の追跡

**`.ndf/` は追跡する（コミットしてチームで共有する）のが標準である。** 設定（`project.json`・`worktree.json`・`mvv.md`・`mvv.json`・`glossary.json` など）は
clone した全員と CI で同じ値のまま効き、変える Pull Request を承認ゲートで人が見る（共通原則の C7）。追跡から外すのは次の 2 種類だけである。

| 外すもの | 外し方 |
| --- | --- |
| 個人の値（`.ndf/worktree.local.json`） | `.ndf/.gitignore` に書く。`worktree` Skill の `ndf worktree init` が作る |
| 実行時の出力（`.ndf/handoff/` など） | コミットしない。`handoff/` は `.gitignore` にも書かない（[handoff.md](handoff.md)） |

ルートの `.gitignore` が `.ndf/` を丸ごと外しているときは、利用者が `.gitignore` を直す（NDF は `.gitignore` を書き換えない）。
設定を書くコマンド（`ndf project write`）は、設定が `.gitignore` の対象なら
`status: ok` のまま、除外している行（`<ファイル>:<行>` とパターン）と対象のファイルを `items`（`kind: note`・`key: gitignore`・`result: ignored`）と
要約に出す。

## 指示書の案内

`write` の出力の `案内:` の行に、指示書の状態が出る。解析は指示書を書き換えない。

| 状態 | 案内 |
| --- | --- |
| 指示書（`AGENTS.md`・`CLAUDE.md`・`.claude/CLAUDE.md`・`KIRO.md`・`GEMINI.md`）が 1 つも無い | 指示書が無いこと |
| 先頭に `NDF_PLUGIN_GUIDE_START` のマーカーを持つファイルを、指示書が `@` で読み込んでいる | 古い NDF の案内の読み込みを外すか消すこと |

## ブランチ戦略の設定

ブランチ戦略・リリース・マージの方法論は、`.ndf/project.json` のセクション `branch_strategy` に 1 か所で書く。
**このセクションを作る・更新する・移す・消すのは `ndf project branch-strategy` だけで、人もエージェントも手で書かない。**
`measure`・`write` の 3 手はこのセクションを測らず、書かない。
結果は 1 行の JSON（`status`・`kind`・`values`・`evidence`・`questions`・`diff`・`digest`・`failed`。形は
`contracts/schemas/io/branch-strategy.result.schema.json`）で、終了コードは `ok` が 0・`ask`（質問があり書いていない）が 3・
`stopped`（検査に落ちた・書けない）が 1・読めないが 2 である。

```bash
# 新しいリポジトリ（既定ブランチのコミットが 1 本以下・マージ済みの PR・タグ・Release・ruleset・ワークフローが無い）
ndf project branch-strategy init --kind plugin
# 既存のリポジトリ: 事例と文書を測り、値ごとの根拠と質問を出す（書かない）
ndf project branch-strategy measure > "$TMP/strategy.json"
# 質問に回答して差分とハッシュを見る → ハッシュを渡して書く
ndf project branch-strategy write --result "$TMP/strategy.json" --answers "$TMP/strategy-answers.json"
ndf project branch-strategy write --result "$TMP/strategy.json" --answers "$TMP/strategy-answers.json" --digest <ハッシュ>
# 旧い 4 か所（worktree.json・project.json・supervise.json・release.json）のキーを移す。差分を見てからハッシュを渡す
ndf project branch-strategy migrate
ndf project branch-strategy migrate --digest <ハッシュ>
# 移行が質問を返したら、回答（候補の 1 つ）を渡して打ち直す（ハッシュを渡すときも同じ回答を渡す）
ndf project branch-strategy migrate --answers "$TMP/migrate-answers.json" --digest <ハッシュ>
# NDF を外すとき
ndf project branch-strategy remove --digest <ハッシュ>
```

- `--kind` は代表のリポジトリの種類（既定の表 `contracts/data/branch-strategy-presets.json` の `kinds` の名前）。`init` で省くと、種類を問う結果
  （`why: no_kind`）を返して書かない。既存のリポジトリで `init` を打つと、書かずに `measure` を案内する
- `measure` は、履歴から推すキー（リリース方式・ベースブランチ・起点・タグの形・Release の有無・マージコミットの作り方）を
  直近の事例が 3 件以上・9 割以上で合うときだけ値にし、既定ブランチ・版数のファイル・`plugin.json`・ワークフロー・
  本番ブランチの ruleset はその 1 件で決める。足りない・食い違う（README・CONTRIBUTING・AGENTS.md の `--base` の行と
  事例が違うときも）ときは書かずに質問を返す
- 質問（`questions`）は `decision-request` の手順で人へ回す。回答のファイルは `{"<キーの位置>": <値>}` で、回答で
  種類の既定から値を変えると `kind` は `custom` になる。`--result` を省いた `write` は今のセクションを基盤にし、回答したキーだけを変える
- 届け先（`/release/deliveries`。本番ブランチ・ベースブランチへのマージで動く行は `channel` で指し、ブランチ名を写さない）は
  リリースの設定ファイル・ワークフローの `deploy` の行・版数の保存先とタグを見て、回答のファイルで渡す
- 書く前に組み合わせの規則（R1〜R10。スキーマ `contracts/schemas/ndf-settings/project.schema.json` の `$defs/BranchStrategy`）を通し、
  落ちれば 1 バイトも書かない

設定と実際の使い方のずれは、`measure` の `values` と今のセクションを比べて見る。設定を変える・外すのは利用者の設定の書き換え
（共通原則 C7）で、実装の中でコミットし、承認ゲートで人が見る。変えた設定はそれから始めるスプリントにだけ効く。

## 再解析

**構成の変化を設定へ映す経路は、自動と手動の 2 つである。** どちらも同じ 3 手と同じ書き出しの規則を通る。

### 自動の再解析

`check` は、解析が読んだ入力の指紋（下の表のファイルの HEAD の blob・ブランチの構成・解析器のバージョン）が記録と違えば 3 を返し、
`変わった入力: <パス>` を出す。手順 0 はそのまま 3 手を通し、`解析: 更新した（変わった入力 <パス>）` を出す。
自動の再解析は手順 0 でだけ走る。ほかの工程は、始めに設定をそのまま読む。

| 分類 | 入力のパス（根と 1 階層下） |
| --- | --- |
| CI | `.github/workflows/*` |
| 依存の定義 | `composer.json`・`package.json`・`pyproject.toml`・`requirements*.txt`・`Gemfile`・`go.mod`・`Cargo.toml` |
| テストの設定 | `phpunit.xml*`・`pytest.ini`・`tox.ini`・`vitest.config.*`・`jest.config.*` |
| コンテナ | `compose*.y*ml`・`docker-compose*.y*ml` |
| デプロイ | `amplify.yml`・`samconfig.toml`・`buildspec.yml`・`appspec.yml` |
| 指示書 | `AGENTS.md`・`CLAUDE.md`・`.claude/CLAUDE.md`・`KIRO.md`・`GEMINI.md` |

コミットしていない編集・ロックファイルの変更では古くならない。

### 手動の再解析

自動では拾わない変化（CI の結果だけの変化・CI を分けた・JUnit を出し始めた・表の外のファイルでテストの走らせ方やデプロイを変えた）の後は、
次の 1 つのコマンドで始める。

```bash
ndf project check --force
```

`--force` は設定が新しくても 3 を返し、`変わった入力: 強制（--force）` を出す。壊れた設定（2）と判定できない（1）は変わらず、
ファイルも書かない。3 を受けた後は、上の 3 手を通す。回答を書くのは conductor で、利用者が `check --force` だけを打っても設定は変わらない。
手順 0 の `解析:` の部分は `更新した（手動）` にする。
