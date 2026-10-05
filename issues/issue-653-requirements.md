# #653: 継続的統合で走る 3 つの検査が必須になっていない（落ちてもマージできる）

正は課題の本文（#653）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何が起きているか

Pull Request で走るジョブのうち **3 つが ruleset の必須の検査に入っていない**。
落ちても `mergeStateStatus` は塞がらず、マージできる。

配布（v10.12.0、PR #651 / #652）で必須の検査の一覧を引いたときに見つけた。**この配布の
範囲外**である（版数を直す作業とは別の話で、どちらの PR でも直していない）。

### 必須になっている 12 個

```console
$ gh api repos/devbasex/ai-plugins/rulesets/22332172 \
    --jq '.rules[]|select(.type=="required_status_checks")|.parameters.required_status_checks[].context'
guard
pytest
runtime-smoke (claude)
runtime-smoke (codex)
runtime-smoke (kiro)
runtime-smoke (agy)
runtime-plugin-build-check
skill-frontmatter-check
skill-repo-assumptions-check
runtime-plugin-validate
markdown-link-check
instruction-files-check
```

**`instruction-files-check` は手で足された。** 突き合わせる検査は無いままで、入れ忘れの仕組みの問題は残る
（`git grep -ln required_status_checks origin/develop -- scripts .github docs plugins` は `plugins/ndf/scripts/project_lib/measure_ci.py` ほか 3 件を返すが、`measure_ci.py` は CI の所要を測るために必須の一覧を読むだけで、ワークフローのジョブ名との突き合わせではない。2026-10-03、develop 37f8d578）。

### 走るが必須になっていない 3 個（起票の時点）

| ジョブ | ワークフロー | 足した時期 |
| --- | --- | --- |
| `doc-line-limit-check` | `.github/workflows/runtime-plugin-validate.yml:90` | 1798397（2026-09-05） |
| `skill-shell-vars-check` | `.github/workflows/runtime-plugin-validate.yml:59` | 332733d（2026-09-09） |
| `check` | `.github/workflows/pr-body-decisions.yml:20` | 362a12f（2026-09-13） |

いずれも**ジョブを足したときに ruleset の一覧へ入れていない**。ワークフローの側だけを足すと、
走って赤くなるだけで塞がらない。**表に出ない失敗**にあたる。

### 後から足されて必須になっていない 3 個（2026-09-29 時点。2026-10-03 も同じで、ruleset の一覧は 12 個のまま）

`gh api repos/devbasex/ai-plugins/rulesets/22332172` の必須の一覧は上の 12 個のままで、その後に足したワークフローの
ジョブも入っていない。

| ジョブ | ワークフロー | 足した時期 |
| --- | --- | --- |
| `check` | `.github/workflows/glossary.yml:16`（Glossary） | bc5dc941（2026-09-25） |
| `check` | `.github/workflows/script-structure.yml:24`（Script structure） | aa438e4f（2026-09-26） |
| `lint` | `.github/workflows/lint.yml:28`（Lint） | fbfa063e（2026-09-26） |

**ジョブ名 `check` が 3 つのワークフローで重なっている**（PR body decisions・Glossary・Script structure）。必須の一覧は
`context` の名前で照合するため、どれか 1 つを `check` として必須に入れると、どのワークフローのジョブを指すかが
名前から決まらない。

**走るが必須になっていないジョブは、上の 2 つの表で計 6 つである。**

## なぜ問題か

赤い印は PR ページに出るが、マージの判定には入らない。**人がその 3 つを目で見て判断する
運用になっている。** 見落とせば、行数の基準を超えた文書・`$SCRIPTS` の解決を書き忘れた
Skill・決めたことの節を欠いた PR 本文がそのまま入る。

必須の検査を足す動機がそもそもこれであり、12 個は塞いで 3 個は塞がないという区別に理由が
見当たらない（意図して外しているなら、その理由をどこかへ書く必要がある）。

## 直し方の候補

1. 3 つを ruleset の `required_status_checks` へ足す
2. 意図して外しているなら、`docs/` のどこかに「必須にしない検査とその理由」を書く
3. **ジョブを足したときに ruleset へ入れ忘れないようにする。** 走るジョブ名と必須の一覧を
   突き合わせる検査を足す（`.github/workflows/*.yml` の `jobs` の名前と、ruleset の
   `context` の差を出す）。**これが無いと、次に足したジョブでも同じことが起きる**

## 直した後に合わせる記述

**必須の検査の数を書いた文書がある。** `docs/versioning-and-distribution.md:166` は「必須の検査 12 個は
Pull Request で走るため」と書く。3 つのうち必須にしたものがあれば、この数も変わる。

## 起票時の受け入れ条件（下の「受け入れ条件」で置き換えた）

- ~~3 つのそれぞれについて、必須にするか外すかが決まっている~~ → 6 つとも必須にすると決まった（2026-10-06、利用者の決定）。下の AC1〜AC3
- ~~外すものがあるなら、その理由が説明文書に書かれている~~ → 外すものは無い。「必須にしない」とした過去の決定の記述を改める（AC8）
- ~~ジョブを足したときの入れ忘れが機械で見つかる（または、見つけない理由が書かれている）~~ → 見つける方を採った（AC4〜AC7）
- ~~必須の検査の数を書いた文書（`docs/versioning-and-distribution.md:166`）が、決めた後の一覧と一致している~~ → AC9

## 決定（2026-10-06 利用者）

**6 つとも必須にする**（直し方の候補 1 と 3）。

- ジョブ名 `check` が重なる 3 つ（PR body decisions・Glossary・Script structure）は、一意の名前へ改名してから必須に入れる
- `doc-line-limit-check`・`skill-shell-vars-check`・3 つの改名後の名前・`lint` を ruleset の `required_status_checks` へ足す（ruleset の変更は本番系の設定変更なので承認ゲート 2 で扱う）
- ワークフローのジョブ名と ruleset の必須の一覧を突き合わせる検査を足し、次にジョブを足したときの入れ忘れを機械で見つける
- `pr-body-decisions` の古い失敗で止まる #1645 は同じスプリントで直す
- `docs/versioning-and-distribution.md` の必須の検査の数を、決めた後の一覧に合わせる

---

# 要求と受け入れ条件

上の節（「何が起きているか」から「決定」まで）を依頼の原文として扱い、手を入れない。以下は原文と決定から導いた要求である。
語は用語集に合わせ、継続的統合のジョブが返す合否を「チェック」、ruleset の `required_status_checks` に載ったものを「必須のチェック」と呼ぶ
（原文の「必須の検査」と同じもの。「検査」は用語集ではフェーズの名前のため、要求の側では使わない）。

## 目的

- Pull Request で走るチェックが落ちたら、マージの判定がそれを塞ぐ状態にする。人が赤い印を目で拾う運用をやめる
- 次にワークフローへジョブを足したとき、必須のチェックへの入れ忘れを機械が見つける

## 前提

- 前提 1: 対象の ruleset は `protect main and develop`（id 22332172。`refs/heads/main` と `refs/heads/develop` が対象）の 1 つだけである。2026-10-05 に `gh api repos/devbasex/ai-plugins/rulesets` が返したのはこの 1 件だった
- 前提 2: ブランチに効く必須のチェックの一覧は `gh api repos/<owner>/<repo>/rules/branches/<branch>` で読める。このリポジトリは公開で、2026-10-05 に未認証の `curl` でも HTTP 200 が返った。CI の `GITHUB_TOKEN` で読める
- 前提 3: ruleset の変更（必須のチェックの追加）は、実装の Pull Request が `develop` へ入り、改名したジョブ名が `develop` で走るようになった後に、承認ゲート 2 で利用者の承認を得て行う。AI は承認なしに ruleset を変えない
- 前提 4: `pr-body-decisions` のチェックを必須にすると、本文の編集の前に落ちた古い実行が残った Pull Request で `merge-when-green` が止まる（#1645 の現象）。#1645 は同じスプリントで直すと決まっており、ruleset へ足すのは #1645 の修正が `develop` に入った後とする。#1645 の修正そのものはこの課題の範囲に含めない
- 前提 5: 6 つのジョブは、いずれも `pull_request` の起動を `paths` で絞っていない（2026-10-05 にワークフローの `on:` を読んで確かめた）。そのため必須にしても、対象外の変更で「実行されていない」のまま残ってマージを塞ぐことはない
- 前提 6: `pr-body-decisions` のチェックは設計の Pull Request（head が `design/` で始まる）以外では成功で終わる。必須にしても、実装とリリースの Pull Request を塞がない

## 対象範囲

含む:
- 名前が重なる 3 つのジョブ（PR body decisions・Glossary・Script structure の `check`）を一意の名前へ改名する
- ワークフローのジョブから決まるチェックの名前と、ブランチの必須のチェックの一覧を突き合わせるチェックを足し、継続的統合で走らせる
- 必須にしないジョブを、理由と組で宣言できるようにする（集約される前の分割ジョブ・判定だけのジョブなど）
- 承認ゲート 2 で、6 つ（改名後の名前）と、足した突き合わせのチェックを ruleset の必須のチェックへ足す手順を承認資料に載せる
- 必須のチェックの数を持つ文書と、「必須にしない」とした過去の決定の記述を、決めた後の一覧に合わせる

含まない:
- #1645（`merge-when-green` が古い失敗を数える）の修正。同じスプリントの別の課題で扱う
- ruleset のほかの規則（`pull_request`・`deletion`・`non_fast_forward`・`strict_required_status_checks_policy`）の変更
- `workflow_dispatch` だけで走るワークフロー（`runtime-plugin-authenticated-smoke.yml`）を必須にすること
- 6 つのジョブの中身（何を見て落とすか）の変更
- ほかのリポジトリの ruleset の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 名前が重なる 3 つのジョブを改名した | 実装の Pull Request の差分 | 改名後の名前が他と重なれば、突き合わせのチェック（E3）が落ちる | — |
| E2 | Pull Request でワークフローのジョブが走り、チェックの名前で結果を返した | Pull Request の作成・更新 | ジョブが落ちれば、そのチェックが失敗になる | E1 |
| E3 | 突き合わせのチェックが、ジョブから決まるチェックの名前と宛先のブランチの必須のチェックの差を出した | E2 と同じ | 必須の一覧を読めなければ、差を出さずに失敗で終わる（成功にしない） | E1 |
| E4 | 実装の Pull Request を `develop` へマージした | 利用者・conductor のマージ | E3 が差を出して落ちていても、ruleset へ足す前なので塞がない（未決 1） | E2・E3 |
| E5 | #1645 の修正が `develop` に入った | 別の課題 | 入っていなければ E6 で `pr-body-decisions` を足すのを待つ | — |
| E6 | 利用者が承認ゲート 2 で ruleset の変更を承認し、必須のチェックへ足した | 承認ゲート 2 | 承認されなければ足さない。差は E3 が出し続ける | E4・E5 |
| E7 | 必須のチェックが落ちた Pull Request のマージが塞がれた | E6 の後の Pull Request | — | E6 |
| E8 | 後からジョブを足した Pull Request で、突き合わせのチェックが入れ忘れを知らせた | ワークフローへのジョブの追加 | — | E6 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 必須のチェック | ブランチの ruleset の `required_status_checks` に `context` の名前で載ったチェック。落ちるか結果が無いとマージを塞ぐ |
| チェックの名前 | ジョブの結果が Pull Request に載るときの名前。ジョブの `name:`（無ければ job id）に matrix の値が付いたもの。必須のチェックはこの名前で照合する |
| 必須にしないジョブ | 突き合わせのチェックが差に数えないと、理由と組で宣言したジョブ |

## 受け入れ条件

- [ ] AC1: PR body decisions・Glossary・Script structure のジョブのチェックの名前が、互いにも他のワークフローのジョブとも重ならない（`pull_request` で走るジョブのチェックの名前の一覧に重複が無い）
- [ ] AC2: 実装の Pull Request で、改名した 3 つのジョブが新しいチェックの名前で結果を返す（`gh pr checks <番号>` に新しい名前が出て、`check` という名前が出ない）
- [ ] AC3: 承認ゲート 2 の後、`gh api repos/devbasex/ai-plugins/rules/branches/develop` と `.../main` が返す必須のチェックの一覧に、`doc-line-limit-check`・`skill-shell-vars-check`・`lint`・改名後の 3 つの名前・突き合わせのチェックの名前が載り、もとの 12 個も残る（計 19 個）
- [ ] AC4: 突き合わせのチェックは、`pull_request` で走るジョブから決まるチェックの名前のうち、宛先のブランチの必須のチェックにも必須にしないジョブにも当たらないものを 1 件ずつ名前とワークフローのパスつきで出し、1 件でもあれば失敗で終わる
- [ ] AC5: 突き合わせのチェックは、必須のチェックのうち、どのジョブのチェックの名前にも当たらないもの（改名・削除で消えた名前）も出して失敗で終わる
- [ ] AC6: 突き合わせのチェックは、ジョブの `name:` と matrix から決まる名前を Pull Request に載る名前と同じ形で作る。前提: 2026-10-05 の `develop` のワークフローと必須のチェック 12 個。操作: 必須にしないジョブの宣言だけを入れて走らせる。結果: 差に出るのは 6 つ（AC1 の改名後の名前を含む）と突き合わせのチェック自身だけで、`runtime-smoke (claude)` など matrix の 4 つと `pytest` は差に出ない
- [ ] AC7: 必須の一覧を読めないとき（API の失敗・権限不足・時間切れ）、突き合わせのチェックは成功で終わらず、読めなかった理由を出して失敗で終わる
- [ ] AC8: 「必須のチェックにしない」と書いた記述（`.github/workflows/pr-body-decisions.yml` の冒頭のコメントと `docs/specifications/ndf-workflow-unit-and-gates.md` の決定の表の「継続的統合の突き合わせは必須のチェックにしない」）が、必須にした事実と決めた日・決めた人に改まる
- [ ] AC9: 必須のチェックの数を持つ文書（`docs/versioning-and-distribution.md` の散文とコンソールの例の説明、ほかに `git grep -n "必須のチェック"` が数を伴って返す現行の文書）の数が AC3 の一覧の数と一致する。リリース済みの版の記録（`docs/ndf-version-decisions*.md`・`docs/development-history/`・`docs/presentations/`・`docs/articles/`）は書き換えない
- [ ] AC10: 既存のチェックが退行しない。もとの 12 個のチェックの名前は変わらず、実装の Pull Request でも結果を返す

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 突き合わせのチェックの出力だけで、どのジョブを ruleset へ足すか（または必須にしないジョブとして宣言するか）が分かる。必須にしないジョブの宣言は理由を必ず持ち、理由の無い宣言は失敗にする |
| 移行性 | ruleset へ改名後の名前を足すのは、改名が `develop` に入った後にする。先に足すと、改名の前のブランチから出た Pull Request が結果の来ない必須のチェックで塞がる。マージ済みの改名の前に出ていた Pull Request は、`develop` を取り込み直せば通る |
| セキュリティ | 突き合わせのチェックは必須の一覧を読むだけで、ruleset へ書き込まない。ワークフローの権限は読み取り（`contents: read`）を超えない |
| システム環境 | 突き合わせのチェックは、ワークフローの置き場・対象のブランチ・必須にしないジョブを宣言か引数で受け、`devbasex/ai-plugins` の名前とこのリポジトリのジョブ名を既定に埋め込まない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（プラグインの Skill・hook・結果の JSON の契約は変えない。変わるのはこのリポジトリの継続的統合のジョブ名だけ） |
| データ | 無し |
| 既存の振る舞い | 3 つのジョブのチェックの名前が `check` から変わる。ruleset の変更の後は、6 つと突き合わせのチェックが落ちた Pull Request をマージできなくなる。ジョブ名 `check` を名前で探す呼び出し元があれば合わせる（設計で洗い出す） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4`（突き合わせのチェックの単体テスト。matrix の展開・重複・必須の一覧の読み取りの失敗・宣言の理由の欠落） |
| 静的解析 | `bash scripts/check-lint.sh`・`python3 scripts/check-script-structure.py`・`python3 plugins/ndf/scripts/instructions-check.py --root .`・`claude plugin validate .` |
| 突き合わせ（手元） | 突き合わせのチェックを手元で `develop` に向けて走らせ、AC6 の差の一覧を確かめる |
| 手動確認（マージ前） | 実装の Pull Request の `gh pr checks` に改名後の 3 つの名前と突き合わせのチェックが出る（AC2） |
| 手動確認 | 承認ゲート 2 の後に `gh api repos/devbasex/ai-plugins/rules/branches/develop` と `.../main` で必須のチェックの一覧を読み、AC3 の 19 個と一致する。突き合わせのチェックが ruleset の変更の後の Pull Request で成功する |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | このリポジトリだけのチェックは `scripts/check-*.py` と `.github/workflows/` に置く（`scripts/check-doc-line-limit.py` などの前例）。NDF の配布物（`plugins/ndf/`）に置くかは設計が決める。必須のチェックの一覧の読み取りは `plugins/ndf/scripts/project_lib/measure_ci.py` の `_required_checks` と同じ役割であり、分けて持たない（Value 6） |
| コーディング規約 | `AGENTS.md` の「ベストプラクティス」。外部コマンド（`gh api`）の失敗時の終了コードと出力は書く前に実行して確かめる |
| テスト戦略 | 名前の展開と差の計算は入力のワークフローと必須の一覧を与える単体テストで担保する。ruleset への反映は承認ゲート 2 の後の手動確認で担保する |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テスト・lint・構造チェックの実行。改名したジョブ名を参照する箇所の追従 |
| 確認してから行う | ruleset の必須のチェックの変更（承認ゲート 2）。`.ndf/` の設定の書き換え（C7。必須にしないジョブの宣言を `.ndf/` に置くと決めた場合） |
| 行わない | ruleset のほかの規則の変更。6 つのジョブの判定の中身の変更。#1645 の修正。リリース済みの版の記録の書き換え |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 突き合わせのチェックを足す実装の Pull Request では、ruleset がまだ古いため差が出て突き合わせのチェック自身が落ちる。この Pull Request を落ちたまま通すか、ruleset を変えるまでは差を知らせるだけにする段取りを置くか | 設計（`design`） | 設計の承認（承認ゲート 1） |
| 2. 改名後の 3 つのジョブの名前と、突き合わせのチェックのジョブの名前 | 設計 | 設計の承認 |
| 3. 必須にしないジョブの宣言の置き場（`.ndf/` か、チェックのスクリプトの隣か）と、2026-10-05 時点で宣言するジョブ（`ci-scope`・`pytest (0/2)`・`pytest (1/2)` が候補） | 設計 | 設計の承認 |
| 4. 突き合わせのチェックを NDF の配布物として他のプロジェクトでも使える形にするか、このリポジトリのチェックに留めるか | 設計 | 設計の承認 |
