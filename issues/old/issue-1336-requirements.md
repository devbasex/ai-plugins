# #1336: 配布の工程をプラグインの版数でないプロジェクトでも回せるようにし、マージが本番のデプロイになるリポジトリでゲート 2 をそのマージに掛ける

正は課題の本文（#1336）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

**この節は「何を満たすか」だけを扱う。** どう作るか（配布の形の選び方・止める位置・固定値を移す範囲）は設計が決める。
マイルストーンは「23 汎用性の回復」。版は配布の工程が決める（`release`）。

## 例: carmo-system-console で実装 PR をマージしたとき

carmo-system-console は `main` へのマージで CodePipeline が本番（ECS）へ出す。起点も本番も `main` で、版数もタグも無い。

今（ndf 10.17.40）:

1. #1333 の解析で、宣言は `worktree.json` の `base_branch: main`・`production_branch: main` と、`project.json` の
   `delivery: [{"kind": "auto", "branch": "main", "versioned": false, ...}]` を持つ
2. 実装 PR（`main` 宛て）のコードレビューが収束すると、conductor は `merged-steps.py merge-when-green` で CI を待ってマージする。
   **このマージがそのまま本番のデプロイになるのに、承認ゲート 2 で止まらない**（`development-workflow` の「実装 Pull Request の
   マージは、それ自体では承認ゲートにならない」）
3. ミッションを閉じる `supervise.py new close` は、`.ndf/supervise.json` の `release.form` が無いと止まる。版数の無いミッションは
   `new mission` の `--version` を渡せない

変えた後:

1. `main` 宛ての PR のマージの前に、ゲート 2（本番系へ届く操作）で止まる。承認の依頼に「このマージで本番へ出る」ことと、出る差分が出る
2. 版数を渡さずに `new mission` と `new close` が通り、最後の配布のステージは「マージでデプロイ」の形として組まれる
3. ai-plugins では、実装 PR の `develop` へのマージは今のまま止まらず、配布は今の `package-plugin` のまま動く

## 依頼（原文）

> 配布の工程を、ai-plugins の形（プラグインの版数・開発版と正式版の 2 チャネル）でないプロジェクトでも回せるようにする。特に、**マージがそのまま本番のデプロイになるリポジトリで、承認ゲート 2（本番系へ届く操作）がそのマージに掛かる**ようにする。（2026-09-27 利用者の指示。根拠は `issues/genericity-survey-2026-09-27.md` の G3）

課題の元の「設計で決めること」:

> - 配布の形の一覧（例: プラグインの版数 / マージでデプロイ / 手動のデプロイのコマンド / 配布なし）と、宣言（#1333）での選び方
> - マージでデプロイする形で、ゲート 2 をどのマージに掛けるか（`production_branch` 宛ての PR のマージ）。`merged-steps.py merge-when-green` と hook で止める位置
> - `package-plugin` の固定値（ブランチ・タグ・リポジトリ名・CHANGELOG・配置）を宣言か `worktree.json` から読む形へ移す範囲
> - 版数を持たないときの `new mission` / `new close` のステージ

課題の元の受け入れ条件:

> - carmo-system-console と project-trygroup-prd で、本番チャネル宛ての PR のマージの前にゲート 2 で止まる
> - 版数を持たないプロジェクトで `new mission` と `new close` が止まらない
> - ai-plugins の配布（`package-plugin`）の振る舞いは変わらない
>
> 関連: #1333（宣言）

## 今の状態（2026-09-27 に調べ、2026-09-28 に develop の bbefe135 で確かめ直した）

- 配布の雛形 `package-plugin`（`supervise_lib/release_templates.py:303` の `RELEASE_FORMS` にある唯一の形）とそのスクリプトが ai-plugins を固定している
  - `release-steps.py:690-695,714,736-744,785-787`: 開発版の PR の宛先が `develop`、本番が `develop`→`main`、差分が `origin/main...origin/develop`。`.ndf/worktree.json` の `base_branch` / `production_branch` を読まない（雛形の側の `release_templates.py:55,115` は `production_branch` を読む）
  - `release-steps.py:714,732,744,781,1040`: タグ `ndf--v<版>`、PR の題 `Release: ndf v…`、`--plugins` の既定 `ndf`
  - `release-steps.py:519-607,897-907`: 根の `CHANGELOG.md` と見出し `## [<plugin> <版>]`
  - `release-verification-steps.py:42,175,194,297-298`: `REPO_SLUG="devbasex/ai-plugins"`・`--ref` は `develop`/`main` だけ・タグ `ndf--v*`
  - `lib/step_result.py:232-236`: `plugins/<名>/.claude-plugin/plugin.json` の配置。根に `.claude-plugin/` を持つ単体プラグインでも止まる
  - `lib/step_result.py:221-224`・`lib/versions.py`: 版の形 `X.Y.Z[-dev.N]`
- 版数の無いプロジェクトで止まる: `new mission` / `new close` は `--version` を必須にする（`new_args.py:287-288`）。`new close` は `release.form` を必須にする（`decl.py:148`）。雛形の無い形のための `mission.py:241` の `manual_release_wave`（手で行うリリースのステージ）はあるが、`release.form` が無いと `decl.py` が先に止める
- 宣言（#1333、クローズ済み）の `.ndf/project.json` の `delivery`（P7）は `[{"target", "kind": "auto"|"manual", "trigger", "branch"?, "versioned": bool}]` で、`[]` は配布しない。`release.form`（`.ndf/supervise.json`）とは別に置かれ、今は雛形の選択に使われていない
- サンプルの配布
  - carmo-system-console: `main` へのマージで CodePipeline が本番（ECS）へ出す。版数・タグ無し
  - project-trygroup-prd: `develop` へのマージで stg、`main` へのマージで prd へ自動デプロイ
  - carmo-contractors-app: フロントは Amplify が push で自動、API は `sam deploy --config-env prod` を手で打つ
  - with-ai-dev: 手動のデプロイだけ
- 今のゲート 2 は「リリースの工程」と `operation` の実装に掛かる。`development-workflow` の SKILL.md は「リリース（マージと公開）はマージ先が本番チャネルか」で判定すると書く一方、「実装 Pull Request のマージは、それ自体では承認ゲートにならない」とも書く。`merged-steps.py merge-when-green`（`:313-330`）は宛先を見ずに `gh pr merge --admin` を打ち、hook にもマージを止める規則は無い。本番チャネルへのマージが本番のデプロイになるリポジトリでは、承認なしで本番へ届く

## 目的

- **マージが本番のデプロイになるリポジトリで、本番へ届く前に人が止められる。** 承認ゲート 2 の定義（本番系へ届く操作）をそのまま当てはめ、新しい承認ゲートは作らない
- **版数を持たないプロジェクトで、ミッションを始めてから閉じるまでの工程が止まらない**
- **ai-plugins の形（プラグインの版数・2 チャネル）を、配布の形の 1 つとして残す。** ai-plugins の配布の振る舞いは変えない

## 解釈

| 依頼文の語 | 具体化 |
| --- | --- |
| 配布の形（この課題で使う語） | プロジェクトが変更を利用者へ届ける方法の種類。下の F1〜F4 |
| 本番系へ自動で出るブランチ（この課題で使う語） | そのブランチへのマージ（push）で本番系へのデプロイが自動で始まるブランチ。宣言の `delivery` と `production_branch` から決まる |
| ai-plugins の形でないプロジェクト | 宣言の `delivery` が「版数を持つプラグインの配布」以外（マージでデプロイ・手動のデプロイ・配布しない）を表すプロジェクト。4 つのサンプルが当たる |
| 回せる | `new mission` → 実装 → `new close` の配布のステージまでが、版数を渡さず、ai-plugins の値（`develop`・`ndf--v`・`devbasex/ai-plugins`・`CHANGELOG.md`・`plugins/<名>/`）を使わずに通る |
| マージがそのまま本番のデプロイになる | 宣言の `delivery` に `kind: auto` で `branch` を持つ行があり、そのブランチへのマージ（push）で本番系へ出る。`branch` が `production_branch` と同じか、`delivery` の行が本番と読める |
| ゲート 2 がそのマージに掛かる | その宛先の PR を NDF がマージする前に、利用者の承認を求めて止まる。承認の依頼には、マージで本番へ出ることと差分を示す（`decision-request`） |

**配布の形**（課題の元の例）:

| # | 形 | サンプル |
| --- | --- | --- |
| F1 | プラグインの版数（今の `package-plugin`） | ai-plugins |
| F2 | マージでデプロイ | carmo-system-console（`main`）、project-trygroup-prd（`develop` で stg・`main` で prd）、carmo-contractors-app の web（Amplify） |
| F3 | 手動のデプロイのコマンド | carmo-contractors-app の api（`sam deploy`）、with-ai-dev |
| F4 | 配布なし | `delivery: []` のプロジェクト |

## 前提

- 前提 1: 配布の形は、#1333 の宣言（`project.json` の `delivery` と `worktree.json` の `production_branch`）から選べる。宣言の形を変える必要が出たら、#1333 の schema を拡げる（互換を保つ）
- 前提 2: ゲート 2 は新しい承認ゲートではない。今のゲート 2（本番系へ届く操作）の判定「マージ先が本番チャネルか」を、実装 PR のマージにも当てはめる。承認ゲートの数は 2 のままである（`development-workflow` の「承認ゲートは 2 つで、増やさない」）
- 前提 3: 本番系へ届くマージかどうかは、PR の宛先のブランチが「本番系へ自動で出るブランチ」かで決める。本番系へ自動で出るブランチは、宣言の `delivery` の `kind: auto` の行の `branch` のうち本番を指すものと `production_branch` から決まる。stg など検証用の系へ出るブランチ（trygroup の `develop`）へのマージは、取り消せるため止めない（「検証環境や開発版のチャネルへ入れるマージは取り消せるため、承認を求めない」）
- 前提 4: `pace: fast` / `auto` で MVV の承認をゲート 2 の許可として扱う規則（`pace.md`）は、このマージのゲート 2 にもそのまま効く。レッドラインは今のとおり除く
- 前提 5: 人の手による `gh pr merge` や GitHub の画面からのマージは止めない。止める対象は NDF の工程（conductor・supervisor・worker とスクリプト）が行うマージである
- 前提 6: ai-plugins の宣言（`delivery` の `branch: main`・`production_branch: main`・`base_branch: develop`）では、実装 PR は `develop` 宛てなので止まらない。`develop`→`main` のマージは今のとおり `release` の本番の工程のゲート 2 で止まる（二重に止めない）
- 前提 7: 検証はサンプルのリポジトリへマージも push もしない。仮のリポジトリ（サンプルと同じ宣言を持つもの）か、マージの直前で止まることの確認（dry-run か、止まった時点の出力）で行う（サンプルは別の組織のリポジトリである）
- 前提 8: 版数を持たないプロジェクトのミッションは、版数の代わりにミッションの名前（とマイルストーン）で識別する。タグ・CHANGELOG・版数の更新は行わない
- 前提 9: F3（手動のデプロイのコマンド）でコマンドを NDF が実行するかどうかは、実行が本番系への操作なので、実行する場合はゲート 2 の承認の後に限る。コマンドを宣言に持つか、利用者が打つ案内だけにするかは設計が決める

## 対象範囲

含む:

- 配布の形の一覧（F1〜F4）と、宣言からの選び方
- NDF が行うマージのうち、本番系へ自動で出るブランチ宛てのものをゲート 2 で止めること（`merged-steps.py merge-when-green` と、NDF の工程がマージを行う経路）
- `development-workflow` の承認ゲートの記述（「実装 Pull Request のマージは、それ自体では承認ゲートにならない」）を、前提 2・3 と食い違わない形へ直すこと
- 版数を持たないときの `new mission` / `new close` のステージ（`--version` と `release.form` を要さない経路）
- `package-plugin` の固定値（ブランチ・タグ・リポジトリ名・CHANGELOG・配置・版の形）を宣言か `worktree.json` から読む形へ移す範囲の決定と、決めた範囲の移し替え

含まない:

- マージの方法（`--admin --merge` の固定・squash）、環境ブランチ（`qa/*`・`alpha`）宛ての PR の扱い、cross-review の CI の見方（G5）
- デプロイそのものの成否の監視（CodePipeline・Amplify の結果を待つこと）
- 宣言の解析（#1333）の変更。解析が `delivery` を正しく測れないときの直しは #1333 の後続として起票する
- 課題の正本が GitHub Issues でないときの工程（#479 / G7）
- サンプルへのマージ・push（前提 7）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | ミッションを始めた | `supervise.py new mission` | 版数を持たない宣言で `--version` を求めて止まる → 止めない（AC3） | 宣言（#1333）がある。無ければ今の手順 0 が作る |
| E2 | 配布の形を選んだ | E1・`new close` | `delivery` が不明・無い → 形を決められないことを出力に出し、手で行う配布のステージ（`manual_release_wave`）へ落とす（AC5） | E1 |
| E3 | 実装 PR のレビューが収束した | cross-review の収束 | — | E1 |
| E4 | PR の宛先が本番系へ自動で出るブランチかを判定した | E3 の後のマージの前 | 宣言が読めない → 本番系として扱い止める（AC7） | E3 |
| E5 | ゲート 2 の承認を求めた | E4 が「本番系」 | — | E4。`pace` の規則は前提 4 |
| E6 | PR をマージした（本番へデプロイが始まった） | E5 の承認、または E4 が「本番系でない」 | 承認が無いまま NDF がマージを試みた → スクリプトが拒む（AC1・AC2） | E5 か E4 |
| E7 | ミッションを閉じた | `supervise.py new close` | 版数・`release.form` を求めて止まる → 止めない（AC4） | E6 |

E4 の宣言が読めないときの扱いは受け入れ条件 AC7 へ、E2 の形が決まらないときは AC5 へ移した。E6 の「人の手によるマージ」は前提 5 で対象外にした。

## 受け入れ条件

- [ ] AC1: carmo-system-console の宣言（`base_branch: main`・`production_branch: main`・`delivery` に `main` へのマージで本番）を持つリポジトリで、`main` 宛ての PR を `merged-steps.py merge-when-green` でマージしようとすると、承認が無ければマージせずに終わり、ゲート 2 の承認が要ることを結果に出す（終了コードと結果の JSON で判定できる）
- [ ] AC2: project-trygroup-prd の宣言（`base_branch: develop`・`production_branch: main`・`develop` で stg・`main` で prd）を持つリポジトリで、`main` 宛ての PR は AC1 と同じく止まり、`develop` 宛ての PR は止まらずにマージへ進む
- [ ] AC3: 版数を持たない宣言（`delivery` のすべての行が `versioned: false`、または `delivery: []`）のリポジトリで、`supervise.py new mission` が `--version` を渡さずに通り、プランができる
- [ ] AC4: AC3 のリポジトリで、`supervise.py new close` が `--version` と `release.form` を渡さずに通り、配布の形（F2 / F3 / F4）に合ったステージがプランに入る。F4 では配布のステージを置かない
- [ ] AC5: `delivery` が不明（`{"unknown": ...}`）か無いリポジトリで `new close` を通すと、止まらずに手で行う配布のステージが入り、形を決められなかった理由がプランの `note` に出る
- [ ] AC6: ai-plugins で、`package-plugin` の配布（`release-steps.py` の `bump`・`changelog`・`release --channel dev|prod`・`notes`、`release-verification-steps.py verify-install`）が今と同じ結果を出す（既存のテストが通り、`develop` 宛ての実装 PR の `merge-when-green` は止まらない）
- [ ] AC7: 宣言が壊れている・読めないリポジトリで NDF がマージしようとすると、本番系へ出るかを決められないとして止まる（止めない側へ倒さない）
- [ ] AC8: ゲート 2 で止まったマージは、利用者の承認（`fast` / `auto` では前提 4 の MVV の承認）を得た後に、同じコマンドに承認を示す引数を渡すとマージへ進む。承認を示す引数の無い呼び出しでは進まない
- [ ] AC9: `development-workflow` の承認ゲートの節を読んだ人が、実装 PR のマージがどの条件でゲート 2 に当たるか（宛先が本番系へ自動で出るブランチか）を答えられる。「実装 Pull Request のマージは、それ自体では承認ゲートにならない」との食い違いが無い
- [ ] AC10: `package-plugin` の固定値のうち、設計が「宣言から読む」と決めたもの（少なくとも本番と開発版のブランチ）を、宣言を変えた仮のリポジトリで変えると、`release-steps.py` がその値を使う（`develop`・`main` の直書きで動かない）
- [ ] AC11: 4 つのサンプルと同じ宣言を持つ仮のリポジトリで、`new mission` と `new close` が止まらず、選ばれた配布の形が F2（carmo-system-console・project-trygroup-prd）/ F2 と F3（carmo-contractors-app）/ F3（with-ai-dev）になる

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | 本番系かの判定に `gh` の呼び出しを足さない（宣言とPR の宛先だけで決める）。宣言が読めないときは止める側へ倒す（AC7） |
| 性能・拡張性 | マージの前の判定は決定論だけで、1 秒以内に終わる |
| 運用・保守性 | 止まったときの結果に、判定に使った宣言の値（ファイルとキー）と PR の宛先が出る。人が理由を後から読める |
| 移行性 | ai-plugins の宣言と配布は変えずに動く（AC6）。既存の `release.form: package-plugin` の宣言をそのまま読む |
| セキュリティ | 承認を省く経路を足さない。NDF が承認の引数を自分で付けてマージへ進むのは、利用者の承認か前提 4 の MVV の承認があるときだけである |
| システム環境 | 4 ランタイムの工程から同じに働く（止める位置はスクリプトに置き、hook だけに頼らない） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`merge-when-green` が本番系宛ての PR で止まる（新しい終了コードか結果の状態）。承認を示す引数が加わる。`new mission` / `new close` の `--version` が条件つきで省ける |
| データ | 無し（宣言の形を拡げる場合は #1333 の schema に互換のまま足す） |
| 既存の振る舞い | 起点と本番が同じブランチのリポジトリ（carmo-system-console）では、実装 PR のマージのたびにゲート 2 で止まる。ai-plugins では変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4`（仮のリポジトリにサンプルと同じ宣言を置き、PR の宛先を変えて判定を確かめる。`gh` は差し替える） |
| 静的解析・構造 | `python3 scripts/check-script-structure.py`、`bash scripts/build-runtime-plugins.sh --check`、`python3 scripts/check-skill-frontmatter.py`、`claude plugin validate .` |
| 実測 | 4 つのサンプルの宣言（#1333 の解析で作ったもの）を仮のリポジトリへ写し、`new mission` / `new close` と `merge-when-green` の判定を走らせる。結果を課題か Pull Request に残す |
| 手動確認 | 無し（すべてコマンドの出力とファイルで判定する。AC9 は文書の記述を読み、条件が書かれていることをレビューで確かめる） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 安定版の置き場（`plugins/ndf/scripts/`・Skill の本文）に置く。既定で働く承認ゲートとリリースの手順なので、実験版には置かない（`AGENTS.md` の「安定版と実験版」） |
| コーディング規約 | `AGENTS.md`、`plugins/ndf/skills/AUTHORING.md`。宣言の読み取りは `supervise_lib/decl.py`・`project_lib` と `lib/worktree-branch.sh` の `wt_production_branch` の既存の部品に寄せる |
| テスト戦略 | 判定は単体テストで、仮のリポジトリと差し替えた `gh` で確かめる。ai-plugins の配布は既存のテストで守る（AC6）。`.md` の文言テストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 本番系へ出るマージの前に止める。止めた理由と判定に使った宣言を示す。ai-plugins の配布の既存テストを走らせる |
| 確認してから行う | 宣言の形（#1333 の schema）を拡げること。承認ゲートの記述を変えること（設計 PR のゲート 1 で承認を得る） |
| 行わない | 承認ゲートを増やす。承認を省く経路を足す。サンプルへのマージ・push。人の手によるマージを止める |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 配布の形を `delivery`（`project.json`）から導くか、`release.form`（`supervise.json`）に F2〜F4 の値を足すか。両方あるときの優先 | 設計（`design`） | 設計 PR |
| 本番系へ自動で出るブランチの決め方の細部（`delivery` の行が本番か検証かを何で見分けるか。今の `delivery` には系の区別のキーが無い） | 設計（要れば #1333 の schema を拡げる） | 設計 PR |
| マージを止める位置（`merge-when-green` だけか、`merged` の手順・hook も併せるか）と、承認を示す引数の形 | 設計 | 設計 PR |
| 起点と本番が同じブランチのリポジトリで、実装 PR ごとに止めるか、ミッションの PR をまとめて 1 回で止めるか | 設計（`development-workflow` の「工程が動く単位」と突き合わせる） | 設計 PR |
| `package-plugin` の固定値のうち、宣言から読む形へ移すものの範囲（タグの接頭辞・リポジトリ名・CHANGELOG・配置・版の形） | 設計 | 設計 PR |
| F3 で NDF がデプロイのコマンドを実行するか、案内だけにするか（前提 9） | 設計 | 設計 PR |
| 「配布の形」「本番系へ自動で出るブランチ」を用語集（`.ndf/glossary.json`）へ足すか。`.ndf/` の書き換えは共通原則の C7 に当たるため、要求の工程では足さずに解釈の表で定めた | 設計（設計 PR のゲート 1 で利用者が承認する） | 設計 PR |

関連: #1333（宣言）
