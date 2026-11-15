# #3641 の切り替えの手順: ai-plugins を develop と main の運用から GitHub flow へ移す

設計文書は [issue-3641-design.md](issue-3641-design.md) にある。決定の記録は [issue-3641-design-decisions.md](issue-3641-design-decisions.md) にある。
この文書は、ai-plugins を一度だけ切り替える手順（受け入れ条件 17・18）を、順序と戻す方法つきで書く。利用者が新しい版へ移るときの移行の手順とは別である。

## 実例

起票の時点（2026-11-16）の ai-plugins は次の状態にある。

| 項目 | 値 | 出典 |
| --- | --- | --- |
| develop にあって main に無いコミット | 167 | `git rev-list --count origin/main..origin/develop`（`origin/develop` 4301b5015） |
| main にあって develop に無いコミット | 155 | `git rev-list --count origin/develop..origin/main` |
| develop 宛ての開いた PR | #3644（`release/v10.27.73-dev.1`）・#3587（`sprint/lrel-lock`）・#3327（`spec/aifac-1`） | `gh pr list --base develop --state open` |
| main 宛ての開いた PR | #3634（`release/v10.27.72`） | `gh pr list --state open` |
| main の ruleset（`protect main`、id 22332172） | 承認 1・CODEOWNERS のレビュー必須・`strict_required_status_checks_policy: false`・merge queue の規則なし・迂回は管理者（`RepositoryRole` 5、`pull_request`） | `gh api repos/devbasex/ai-plugins/rulesets/22332172` |
| リポジトリと Organization の秘密 | どちらも 0 件 | `gh api repos/devbasex/ai-plugins/actions/secrets --jq .total_count`・`.../actions/organization-secrets --jq .total_count` |

この状態から、手順 1〜7 を順に進める。どの手順の間も、正式版のマーケットプレイス（main）の利用者は main の先端の正式版を取得できる。

## 手順

| # | 手順 | 誰が行うか | 終わったと言える状態 | 戻す方法 |
| --- | --- | --- | --- | --- |
| 1 | 準備: この課題の実装の PR を develop へマージする。上げ幅のラベル `release:major`・`release:minor`・`release:patch` を作る | スプリントの経路（実装の PR は今の流れ）・conductor（ラベル） | `plugins/ndf/scripts/trunk-steps.py` が develop にある。`gh label list` に 3 つのラベルがある | 実装の PR を revert する PR を develop へ出す。ラベルは `gh label delete` |
| 2 | 秘密を置く: `NDF_RELEASE_TOKEN`（fine-grained PAT。対象はこのリポジトリだけ、`contents`・`pull_requests` の書き込み）をリポジトリの秘密に置く | 人（C1。実行の前に止めて人へ戻す） | `gh api repos/devbasex/ai-plugins/actions/secrets --jq '.secrets[].name'` に `NDF_RELEASE_TOKEN` が出る | 秘密を消す（`gh secret delete NDF_RELEASE_TOKEN`）。トークンを失効させる |
| 3 | 最後の develop→main のリリース: develop 宛てのマージを止め、動いている各レーンを検査のステージの後で止め（下の「レーンの付け替え」の止める箇所）、今の `whole` の流れでリリースを出し、同期の PR をマージする（決定 12）。CHANGELOG の節と GitHub Release に、develop の更新を止めることと登録し直しの手順への参照を載せる（決定 13） | オーガナイザー（リリースの依頼の受け手）・リリース承認ゲートで人 | `git diff --quiet origin/main origin/develop` が 0（2 つのブランチの木が同じ）。CD がそのバージョンのタグを作った。動いている各レーンの次のステージが流れていない | 前のバージョンのタグへ戻す手順は `docs/versioning-and-distribution.md` の「過去の版へ戻る」。develop は残っている |
| 4 | 設定の PR: `origin/main` から切ったブランチで、下の「設定の PR の中身」を main 宛てに出し、人が GitHub でマージする | conductor が PR を作る。マージは人（C7・P1） | main の `.ndf/worktree.json` の `branch_flow.promotion` が `trunk`。CD の実行が「リリース対象の変化が無い」で成功した | 設定の PR を revert する PR を main へ出し、人が GitHub でマージする（手順 5 の前なら今の ruleset のまま通る） |
| 5 | ruleset を変える: 下の「ruleset の変更」の値に書き換える | 人（C2。実行の前に止めて人へ戻す） | `gh api repos/devbasex/ai-plugins/rules/branches/main` に `merge_queue` の規則があり、`pull_request` の `required_approving_review_count` が 0 | 保存した今の ruleset の JSON で `PUT` し直す（「ruleset の変更」の戻す値） |
| 6 | 動いているレーンを付け替える: 下の「レーンの付け替え」のとおり、develop 宛ての開いた PR の宛先を main へ変え、スプリントブランチへ main を取り込み、残りのステージを `trunk` の行で組み直してから止めたステージを流す | オーガナイザーと各レーン | `gh pr list --base develop --state open` が 0 件。各レーンの目録の開始時のブランチ運用が `trunk` の行の値で、残りのステージのプランに開発版のステージ・リリースブランチの組み立て・同期の PR が無い | 宛先を develop へ戻す（`gh pr edit <番号> --base develop`）。取り込みのマージコミットは revert する。目録は書き換える前に保存した値へ戻す |
| 7 | 確かめる: 最初のスプリントの PR を `trunk` の本番のプランで出し、リリース後テストで受け入れ条件 2・3・8・10・11 を見る | スプリントの経路・リリース後テスト | 下の「確かめること」の 5 行がそろう | 手順 4・5 を戻す（逆の順で 5 → 4） |

**手順 4 と 5 の間は、`trunk` の本番のプランが止まる。** 設定の方式が `merge-queue` で ruleset に merge queue が無いため、`land` は `precondition` で止まる（I6、受け入れ条件 9）。この間に main へコードが入ることは無い。手順 5 を手順 4 の直後に行う。

**手順 4 で `guard` が落ちる。** 今の main の設定（`whole`）の宛先の検査は、develop か develop の祖先の `release/v` 以外の head を断る。設定の PR は人が GitHub で、今の ruleset の管理者の迂回を使ってマージする（決定 12）。

## 設定の PR の中身

| ファイル | 書き換え |
| --- | --- |
| `.ndf/worktree.json` | `base_branch` を `main` に。`branch_flow` を `{"start": "production", "integrate": "release", "doc_prs": "base", "promotion": "trunk", "reviews": <今のまま>}` に |
| `.ndf/project.json` | `production_merge` を `{"method": "merge-queue"}` に（`admin` を外す。方式があるとき迂回を使わない。I6）。`delivery` の行の `trigger` の説明を「main へのマージで版上げの PR・タグ・GitHub Release を作る」に |
| `.ndf/supervise.json` | `release.dev_workflow` を外す |
| `.github/workflows/release-dev.yml` | 消す（開発版の PR のマージを起動する workflow で、ai-plugins では使わない） |
| `.github/workflows/` の `push` の対象 | `lint.yml`・`pytest.yml`・`runtime-plugin-smoke.yml`・`runtime-plugin-validate.yml`・`script-structure.yml` の `push` から `develop` を外す。`runtime-plugin-smoke-latest.yml` の ref の既定を `main` に |
| `AGENTS.md` | 「Git運用ルール」の宛先を `main` に。「版とリリースの方針」のチャネルの表を正式版の 1 行にし、開発版の接尾辞の段落を外す。develop を更新しないことと登録し直しの手順への参照を 1 段落で書く（決定 13） |
| `CONTRIBUTING.md` | ブランチの起点と PR の宛先を `main` に |
| `docs/versioning-and-distribution.md` | 開発版チャネルの節を、ai-plugins では使わないことと登録し直しの手順（`marketplace add devbasex/ai-plugins`、ref なし）に書き換える。バージョンとタグは CD がマージの後に作ること、上げ幅のラベルを書く |

**`CLAUDE.md` は書き換えない。** develop と開発版を前提にした記述が無い（`git grep -c -w develop -- CLAUDE.md` が 0 件）。

## ruleset の変更

対象は main の ruleset（`protect main`、id 22332172）である。変える前に今の値を `gh api repos/devbasex/ai-plugins/rulesets/22332172` で保存し、戻す値にする。書き換えは同じ JSON の値を変えて `PUT /repos/{owner}/{repo}/rulesets/{ruleset_id}` で送る。

| 規則 | 項目 | 今の値 | 変えた後の値 | 理由 |
| --- | --- | --- | --- | --- |
| `merge_queue`（足す） | `merge_method` | — | `MERGE` | `pull_request` の `allowed_merge_methods` が `merge` だけである |
| `merge_queue` | `grouping_strategy` | — | `ALLGREEN` | 落ちた PR を組み合わせに含めない |
| `merge_queue` | `max_entries_to_build` | — | 5 | 同時に走らせるレーンの上限（6 本）の内に収める |
| `merge_queue` | `min_entries_to_merge`・`max_entries_to_merge`・`min_entries_to_merge_wait_minutes` | — | 1・5・5 | 1 本でも待たずに出し、同時の承認をまとめて確かめる |
| `merge_queue` | `check_response_timeout_minutes` | — | 60 | 必須のチェック 17 個の 1 回が収まる値。切り替えの後の実測で見直す |
| `pull_request` | `required_approving_review_count` | 1 | 0 | NDF の承認ゲートの承認は GitHub のレビューの承認ではなく、1 のままでは `land` がキューへ入れられない（STD3）。人の承認はリリース承認ゲートと人の GitHub でのマージが持つ |
| `pull_request` | `require_code_owner_review` | `true` | `false` | CODEOWNERS（`.github/CODEOWNERS`）のレビューは承認の数とは別に承認を求め、`land` がキューへ入れられない |
| `required_status_checks` | `strict_required_status_checks_policy` | `false` | `false`（変えない） | 組み合わせは merge queue が確かめる |
| `bypass_actors` | — | 管理者（`pull_request`） | 変えない | 人が GitHub で迂回してマージする経路を残す。NDF は方式があるとき迂回しない（I6） |

`pull_request` の `require_extra_approval_for_unattributed_changes`（今の値 `true`）は変えない。承認の数を 0 にしたときにこの項目がキューへの投入を止めるかは確かめていない（設計文書の「未確認のまま残ること」）。分かるのは手順 7 で、ruleset を変えた後である。

手順 7 で投入が止まったときの扱い:

| 状態 | main への経路 | 戻し方 |
| --- | --- | --- |
| `land` が `not_enqueued` か `precondition` で止まり、PR の画面に未帰属の変更の承認を求める表示がある | NDF の経路は止まる。main へは何も入らない。人の GitHub での迂回のマージ（管理者）は使える | 人（C2）がこの項目を `false` にして手順 7 をやり直す。直らなければ、保存した今の ruleset の JSON で `PUT` し直し（手順 5 を戻す）、続けて手順 4 を戻す |
| ほかの理由で止まる | 同上 | 保存した今の ruleset の JSON で `PUT` し直し、手順 4 を戻す。理由を設計へ戻す |

## レーンの付け替え

| PR | 今の宛先 | 扱い |
| --- | --- | --- |
| 開発版の PR（`release/v*-dev.*`。起票の時点で #3644） | develop | 閉じる（`gh pr close`）。ai-plugins では開発版を出さない |
| スプリントの PR（起票の時点で #3587） | develop | `gh pr edit <番号> --base main`。スプリントブランチへ `origin/main` を通常のマージで取り込み、push する（強制 push はしない）。衝突すればその PR をレーンへ返して止まる |
| 確定仕様化の PR（起票の時点で #3327） | develop | 同上 |

手順 3 の後、develop と main の木は同じである。develop から切ったブランチへ main を取り込むと、同期の PR のマージコミットが main 宛ての PR の差分に入るが、木の差は無い。
各レーンのスプリントの残りのステージ（開発版のステージ・本番のステージ・まとめのステージ）は、組んだ時点の取り込み方（`whole`）で組まれている。スプリントの目録（`sprint.json`）が開始時のブランチ運用（キー `ブランチ運用`）を持ち、同じスプリントを組み直しても目録の値で組むためである（`sprint_waves.py` の `pin_flow`）。

組み直しは次の順で行う。

| # | すること | 止まったときの扱い |
| --- | --- | --- |
| 1 | 止める（手順 3 の始め）: 各レーンの queue を、検査のステージが終わった所で止め、次のステージ（開発版のステージ・本番のステージ）を流さない | 止められないレーンがあれば手順 3 を始めない |
| 2 | 目録の値を書き換える（手順 6）: 目録の開始時のブランチ運用を保存し、`trunk` の行の値（`start: production`・`integrate: release`・`doc_prs: base`・`promotion: trunk`、ベースブランチと本番ブランチは `main`）へ書き換える | — |
| 3 | 残りのステージのプランを作り直す: 書き換えた目録の値で、止めた所から後のステージを組み直す。済んだステージ（実装・検査）は作り直さない | 作り直せなければ、レーンを止めたまま人へ戻す。組み直す前のプラン（`whole` の本番のプラン）は流さない |
| 4 | 作り直したプランに、開発版のステージ・リリースブランチの組み立て・同期の PR・`--channel dev` が無いことを確かめてから、止めたステージを流す | 1 つでも残れば流さずに人へ戻す |

手段は目録の値の書き換えとプランの作り直しの両方である。プランだけを作り直すと `pin_flow` が目録の `whole` の値を読み直し、同じプランが組まれる。値だけを書き換えると、書き出し済みのステージのファイルが `whole` のまま流れる。作り直しを打つコマンドの形（同じスプリントの `new sprint` の打ち直しで足りるか）は、実装計画で実行して確かめる。足りなければ、止めたまま 1〜4 の後に本番のプラン（`trunk` の種類、PR の番号を受ける）をレーンの PR へ組む（決定 3 のスプリントの外のプランと同じ経路）。

## 確かめること

| # | 何を見るか | 受け入れ条件 |
| --- | --- | --- |
| 1 | 最初のスプリントの PR が main 宛てで、承認の後に merge queue を通ってマージされた | 2・8 |
| 2 | そのマージの push で CD が版上げの PR を作り、そのマージの push でタグ `ndf--v<バージョン>`・GitHub Release・CHANGELOG の節がそろった | 3 |
| 3 | 同期の PR が作られていない（`gh pr list --state all --head release/v<バージョン>-sync` が 0 件） | 3・10 |
| 4 | 本番のプランに開発版のデプロイのステップ（`--channel dev`・`verify-install --ref develop`）が無い | 11 |
| 5 | 2 本の PR をほぼ同時にキューへ入れ、キューの一時のコミットで必須のチェックが走った | 8 |
