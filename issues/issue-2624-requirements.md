# #2624: Value 6「どのプロジェクトでも使える形を保つ」の違反の棚卸し: 27 件（課題の無いもの 14 件）と、見張りの検査の穴

正は課題の本文（#2624）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> そもそも、Vision 6. **どのプロジェクトでも使える形を保つ** に違反している状態です。ほかにも違反がないかどうかチェックしてください

> carmo-cdkはインフラに関わるため、あえてCDを置いていないプロジェクトですね。参考としてはあまり適切ではないです（そういった例の対処も必要ですが）

（書き手: 利用者、日付: 2026-11-06。#2616・#2623 を受けて）

> プロジェクト MVV の Value 6（`.ndf/mvv.md`）: 「どのプロジェクトでも使える形を保つ: スクリプトにするのは手順の骨組みだけにする。プロジェクトごとに違うもの（テストのコマンド・起点のブランチ・配布の手段・言語・ランタイム・課題の置き場）は、設定か引数で受ける。ai-plugins の形を既定に埋め込まない。」

## 起票者の推定

題・本文のうち、利用者の指示の原文に無く起票者が推して足した条件。設計承認ゲートで利用者が確かめるまで、利用者の決定として扱わない。利用者の原文は「ほかにも違反がないかどうかチェックしてください」と、CD をあえて置かないプロジェクトへの対処が要るという補足の 2 文である。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 27 件のそれぞれが、直す課題（既存か新規）につながっているか、違反でないとする理由が書かれている | 受け入れ条件の案 1 | 受け入れ条件 1 |
| S2 | 課題が無い 14 件が起票されているか、既存の課題の範囲に足されている | 受け入れ条件の案 2 | 受け入れ条件 3・4 へ縮めて採る。行ごとには起票しない。新しい課題は 2 件までとする（Value 2・Value 3）。件数は前提 2 |
| S3 | リポジトリ前提の検査の範囲の穴が塞がり、ai-plugins の値を既定に入れる変更を CI が落とす | 受け入れ条件の案 3 | 受け入れ条件 10〜15。検知は語の照合に限る（前提 3） |
| S4 | 「新しい経路は宣言を読む」ことを、設計の検査かレビューの観点に入れる | 受け入れ条件の案 4 | 受け入れ条件 16。レビューの重点を選ぶ（理由は前提 4） |
| S5 | 27 件を区分と重さで数える分け方（止まる・誤る・無駄に動く） | 調べ方を決めた worker の報告 | 行き先の順位の材料として残す。受け入れ条件にしない |
| S6 | 例外で直すことが繰り返されている、という根にある形 1 | 事例 50 件の集計 | 含まない（対象範囲に理由） |

## 目的

ai-plugins の形を前提にした箇所（Value 6 の違反）について、次の 2 つの状態を作る。

- 見つかった 27 件のすべてに、誰がどこで直すかの行き先がある
- 同じ形の値をリリース対象のスクリプトへ足す変更を、CI が止める

## 前提

- 前提 1: 27 件は develop 790da7a7（2026-11-06）の調べである（節「調べた結果」に起票時の本文を引用した）。HEAD 7db74dfa6 で確かめたのは行 1・9・10・11・12・15・18 で、いずれも残っている（行 10 は `lib/parallel_external.py:270`・`:289` へ動いた。行 1 は並列の計画の 3 本に `delivery` の語が 0 件）。残りの行は未確認で、受け入れ条件 2 で確かめる
- 前提 2: 「課題が無い 14 件」は、表の「既存の課題」の列と一致しない。列が「無し」で始まる行は 12 行（行 7・9・10・11・12・15・17・19・21・22・23・27）で、一部だけ無い行が 1 行（行 8）ある。要求は件数ではなく表の行で扱う。行 12 の重さも、表（誤る）と調べた記録（無駄に動く）で異なる。表の値を使う
- 前提 3: 検査が拾うのは語だけである。語を含まないまま ai-plugins の形を前提にした論理（#2616 の一部）は拾えない。その部分はレビューの観点（受け入れ条件 16）で受ける
- 前提 4: 「新しい経路は宣言を読む」の観点は NDF を作るこのリポジトリの事情なので、NDF の共通の手順でなくこのリポジトリの宣言（`.ndf/review.json` の重点）に置く。他のプロジェクトへ決まりを足さない（Value 5・Value 6）
- 前提 5: 依頼は違反の確認であり、27 件を直すことまでは求めていない。この課題で直すのは、検査を強める変更と同時に解消でき、課題が無く、1 か所を宣言の読み出しへ換えるだけの行 9・10・11・15 に限る。残りは行き先の課題で直す（Value 2）
- 前提 6: モードは `standard` である。行き先の課題の本文への追記は、同じリポジトリの課題の操作で、必ず人の承認が要る操作に当たらない。Redmine など外部の課題管理へは書かない

## 対象範囲

含む:

- 27 行の場所を現在の develop で確かめ直し、行き先を決めること
- 既存の課題の本文への追記と、新しい課題 2 件以内の起票
- 行 9・10・11・15 の修正
- リポジトリ前提の検査（CI のジョブ `skill-repo-assumptions-check`）の走査範囲と語の追加、既知の当たりの宣言（行 27）
- このリポジトリのレビューの重点への観点の追加

含まない:

- 行 9・10・11・15 以外の修正。それぞれの行き先の課題で直す
- 語 `issues/`（47 ファイル 84 行）と `gh`（79 ファイル 247 行）の検知。どちらも置き場が固定である状態（行 16・20）が直るまで、当たりのほとんどが同じ行き先の既知の違反になり、検査の差分を読めなくなる
- CD をあえて置かないプロジェクトの扱い。#2623 の未決 6 が持つ。この課題は表に行き先として書く
- 複数のリポジトリに分かれたプロジェクトの扱い。#2653 が持つ
- 「例外で直す」の再発の抑止（根にある形 1）。語で拾える形は受け入れ条件 11 で止まり、語で拾えない形は棚卸しの見直し（#2315）で扱う
- 言語・ランタイムの事例（#1339・#1777・#2449・#2509・#2447 など）。どれも既存の課題がある

## 27 件の行き先

受け入れ条件 1 の表の案である。行の番号は節「調べた結果」の「違反の一覧」の番号で、設計と実装で確定してこの表を更新する。

| 行 | 中身 | 行き先 | 扱い |
| --- | --- | --- | --- |
| 1 | 並列の計画のまとめたリリースが `delivery` を読まず、版を決められないと計画全体を止める | #2616・#2554・#2623 | 既存の課題のまま |
| 2 | リリースのプランの形がプラグインの形 1 つだけ | #2623 | 既存の課題のまま |
| 3 | プラグインの形の部品に ai-plugins の配置が入る | #1756 | README・タグ・CHANGELOG の部分を本文へ足す |
| 4 | `loop sprint` が版数の無いサービスにも開発版の版数を求める | #2354・#2623 | 既存の課題のまま |
| 5 | CD を持つリポジトリを例外として扱う | #2623 | 既存の課題のまま |
| 6 | `release` の手順 3 がどの形にも版上げの Pull Request と CHANGELOG の節を求める | #2623 | 既存の課題のまま |
| 7 | Pull Request の本文に CHANGELOG の材料の節を常に求める | #2623 | 本文へ足す（関連 #2601） |
| 8 | 承認の記録と承認資料をプラグインの形で一般の規則にする。承認の外で本番へ通すパスを固定する | #2623・#2619 | 承認の外で通すパスの部分を #2623 の本文へ足す |
| 9 | コードレビューのエージェントが差分を `main` と比べる | この課題 | 受け入れ条件 5 |
| 10 | 並列の計画の本番のマージの照会が `--base main` の決め打ち | この課題 | 受け入れ条件 6・7 |
| 11 | ベースブランチが決まらないときの最後の既定が `main` | この課題 | 受け入れ条件 8 |
| 12 | 本番の宣言が無いと既定ブランチを本番とみなす | #1338 | 本文へ足す |
| 13 | 手順書がベースブランチと本番を `develop`・`main` の文字で書く | #1338 | 本文へ足す |
| 14 | `main` 宛ての Pull Request の closing keywords で課題が先に閉じる | #2082・#2228 | 既存の課題のまま |
| 15 | `supervise.py example` の見本が `pytest -q` を埋め込む | この課題 | 受け入れ条件 9 |
| 16 | 課題の追跡が GitHub Issues に固定で、`issues.primary` を読まない | 新しい課題 1 件（未決 1） | 起票するか #479 を開き直す |
| 17 | マイルストーンの名前の規則をどのリポジトリにも当てる | 新しい課題 1 件（行 19・21・22・23 と同じ課題） | 他のリポジトリでの観測が無い行を 1 件にまとめる（Value 3） |
| 18 | マージを `--admin`・merge commit に固定する | #1338 | 既存の課題のまま |
| 19 | 確定仕様の置き場を `docs/specifications` に固定する | 行 17 と同じ課題 | 関連 #2013 |
| 20 | 設計文書と承認資料の置き場を `issues/` に固定する | #2600 | 既存の課題のまま |
| 21 | 日本語の強制とコミットの接頭辞に宣言の項目が無い | 行 17 と同じ課題 | 同上 |
| 22 | 手順書が ai-plugins にしか無いファイルを根拠として指す | 行 17 と同じ課題 | 同上 |
| 23 | メインディレクトリの編集の案内を出さないパスの既定に ai-plugins の運用が入る | 行 17 と同じ課題 | 同上 |
| 24 | 検査の既定値に ai-plugins のファイルが入る | #1968 | 既存の課題のまま |
| 25 | 単独起動の場所と Tool が書くファイルを Skill ごとの例外で扱う | #1707・#2448 | 既存の課題のまま |
| 26 | `--repo` を見ずに作業ディレクトリのリポジトリで Pull Request を引く | #2446 | 既存の課題のまま |
| 27 | リポジトリ前提の検査の範囲に穴がある | この課題 | 受け入れ条件 10〜15 |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 27 行の場所を確かめ直した | 実装の着手 | 場所が動いた行は語で探し直す。消えた行は検索のコマンドと結果を表に添える | — |
| E2 | 行き先を確定した | E1 の結果 | 決められない行は未決へ移して止める | E1 |
| E3 | 既存の課題の本文へ追記した | E2 | 追記に失敗した行は表の扱いを未完了のまま残し、完了判定で止める | E2 |
| E4 | 新しい課題を起票した | E2 | E3 と同じ | E2 |
| E5 | 行 9・10・11・15 を直した | 実装 | 失敗するテストが通らなければ、その行を行き先の課題へ移す | E1 |
| E6 | 検査の走査範囲と語を広げた | 実装 | 既知の当たりが検査を落とす。E7 まで同じ変更で行う | E5 |
| E7 | 既知の当たりを行き先つきで宣言した | E6 の当たり | 行き先の無い当たりは E2 へ戻す | E3・E4・E6 |
| E8 | レビューの重点へ観点を足した | 実装 | 宣言の検査で落ちたら形を直す | — |
| E9 | CI が新しい違反を落とした | 語を含む変更の Pull Request | 落とせなかった形は語を足す | E6・E7 |

## 用語

| 用語 | 意味 |
| --- | --- |
| リポジトリ前提の検査 | リリース対象の Skill とスクリプトに ai-plugins にしか無い語が無いかを見る CI のジョブ `skill-repo-assumptions-check`（本体は `scripts/check-skill-repo-assumptions.py`）。節「調べた結果」の引用の中の「見張りの検査」と同じものを指す |
| 既知の当たり | リポジトリ前提の検査に当たるが、NDF 自身の置き場を読むなど違反でない箇所か、行き先の課題が直すまで残す箇所 |

## 受け入れ条件

行き先:

- [ ] 1. 「27 件の行き先」の表の 27 行すべてに、課題の番号・「この課題」・「違反でない」と理由のどれかが入っている
- [ ] 2. 着手時の develop で 27 行の場所を確かめ、消えていた行には確かめたコマンドと結果が添えられている
- [ ] 3. 表で「本文へ足す」とした行は、行き先の課題の本文に、番号でなく中身と場所で載っている。行き先の課題の既存の文は変わっていない
- [ ] 4. 新しい課題は 2 件以内で、それぞれの本文が #2624 を指し、対象の行の中身と場所を持つ

修正:

- [ ] 5. `plugins/ndf/agents/code-reviewer.md` の差分の取得の手順に、ブランチ名 `main` の決め打ちが無い（`grep -n 'main' plugins/ndf/agents/code-reviewer.md` の結果で確かめる）
- [ ] 6. `production_branch` が `prod` と宣言されたリポジトリで、並列の計画の届いた段と本番の Pull Request の URL の照会が `--base prod` を渡す
- [ ] 7. `production_branch` の宣言が無く既定ブランチが `main` のリポジトリでは、同じ照会が `--base main` を渡す（退行しない）。前提の確認: `lib/parallel_external.py:270`・`:289` は `main` を固定で渡す
- [ ] 8. 宣言が無く、`origin/HEAD`・`main`・`master` のどれも無いリポジトリで、`pr-steps.py` は Pull Request を作るコマンドを打たずに止まり、`base_branch` の設定を求める
- [ ] 9. `supervise.py example` の出力と `supervise_lib/plan.py` の例に `pytest` が無く、出力はプランの読み込みの検査を通る

検査:

- [ ] 10. リポジトリ前提の検査が、`plugins/ndf/skills/*/scripts/` のリリース対象のスクリプト 143 本と `plugins/ndf/hooks/*.json` 2 本を走査する
- [ ] 11. 次の語のそれぞれについて、リリース対象のスクリプトの新しいファイルへ語を書くと、検査が終了コード 1 で落ちる: `"main"`・`"develop"`・`--base main`・`--admin`・タグの形 `--v`・`release/v`・`plugin.json`・`docs/specifications`・`pytest`
- [ ] 12. `release-steps.py`・`lib/step_result.py`・`release-verification-steps.py` のファイル全体の除外が無くなり、この 3 本の新しい箇所へ語を足すと検査が終了コード 1 で落ちる
- [ ] 13. 既知の当たりは、語とファイルの組で、違反でない理由か行き先の課題の番号とともに宣言されている
- [ ] 14. 宣言した既知の当たりが走査の範囲から消えると、検査が終了コード 2 で落ちる
- [ ] 15. 変更後の develop の状態で `python3 scripts/check-skill-repo-assumptions.py` が終了コード 0 で終わる（退行しない）。前提の確認: HEAD 7db74dfa6 で終了コード 0

レビュー:

- [ ] 16. `.ndf/review.json` の重点に「リリース対象のスクリプトへ足す経路が、プロジェクトごとに違う値を宣言から読むこと」の観点があり、cross-review の担当へ渡る文に載る

退行:

- [ ] 17. `.ndf/` の宣言の項目が増減していない

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 検査 1 回の所要は 10 秒以内である。走査の対象が約 480 本へ増えても CI の待ちを増やさない |
| 運用・保守性 | 検査が落ちたとき、ファイルと行番号と語が出力に載る |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`supervise.py example` の出力の値と、起点が決まらないときの `pr-steps.py` の振る舞い。引数と出力の形は変わらない |
| データ | 変わらない |
| 既存の振る舞い | 語を含む Pull Request が、除外の宣言を足すまで CI で落ちる。実装中の #2623 などの変更にも効く |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `python3 scripts/check-skill-repo-assumptions.py` |
| テスト | `uv run --frozen --project . --all-extras pytest scripts/tests/test_check_skill_repo_assumptions.py -q`（HEAD 7db74dfa6 で 22 件通過）と、全体テスト `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析・型検査 | CI の既存のジョブ |
| 手動確認（マージ前） | 受け入れ条件 1・3・4 を、表と行き先の課題の本文で確かめる |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 検査の本体は `scripts/`、リリース対象は `plugins/ndf/`。`AGENTS.md` の「マーケットプレイスの構造」 |
| コーディング規約 | `plugins/ndf/skills/AUTHORING.md` の「対象リポジトリを仮定しない」。除外はファイルと理由の対で宣言する今の形を、語とファイルの組へ広げる |
| テスト戦略 | 検査は語ごとの単体テスト、修正 3 行（10・11・15）は一時リポジトリか差し替えた `gh` での単体テスト。`.md` の文言を照合するテストは書かない（行 9 は手動の検索で確かめる） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 検査の当たりをファイルごとでなく語とファイルの組で除外する。Skill 本文の語の表はそのまま残す |
| 確認してから行う | 設計承認ゲートで、行き先の表と新しい課題の数を確かめる。`.ndf/review.json` の変更はリリース承認ゲートで人が見る |
| 行わない | 行 9・10・11・15 以外の修正。新しい課題の工程を既定で始めること（着手は棚卸しの順位で決める。Value 3）。課題を閉じること |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 行 16（課題の置き場）を新しい課題にするか、見送りで閉じた #479 を開き直すか。見送りの理由「止まった観測が無い」に対し、carmo-system-console が Redmine を正本にする記録がある | 利用者 | 設計承認ゲート |
| 2. 既知の当たりの宣言の形（既存の `EXCLUSIONS` を広げるか、別の表にするか） | 設計 | 設計 |
| 3. 語 `"main"` のうち、ブランチ以外の意味の当たり（関数の名前など）を語の形で避けるか、既知の当たりにするか | 設計 | 設計 |

## 関連

#2616・#2623（デプロイとリリースの手段の根）・#2620・#2619（語）・#1338（ブランチと `--admin`）・#2239（課題の置き場）・#2600（`issues/` の置き場）・#1756・#2354・#2446・#1707・#2448・#1968・#2653

## 調べた結果

起票時の本文の引用である（書き手: 起票者、日付: 2026-11-06。worker の報告を含む）。語は調べた時点のまま残す。

> ## 調べ方（2026-11-06、develop 790da7a7）
>
> 3 つに分けて調べた。詳しい表は末尾の「調べた記録」にある。
>
> 1. スクリプトと hook（`plugins/ndf/scripts/`・`skills/*/scripts/`・`hooks/`。tests・experimental を除く 477 本）
> 2. 手順書（`skills/**/*.md`・`agents/*.md`・`README.md`）
> 3. 他のリポジトリで実際に起きた事例（課題 1,048 件の本文と、/work の下の 11 リポジトリの `.ndf/`）。インフラのためにあえて CD を置かないプロジェクト（carmo-cdk）の事例は、CD を前提にした直し方の根拠に数えず別に分けた
>
> ## 結果の要約
>
> | 区分 | まとまり | うち課題が無い | 重さ |
> | --- | ---: | ---: | --- |
> | テストのコマンド | 1 | 1 | 無駄に動く |
> | 起点のブランチ | 6 | 5 | 止まる 1・誤る 5 |
> | 配布の手段 | 8 | 1 | 止まる 3・誤る 3・無駄に動く 2 |
> | 言語 | 0（スクリプト・手順書） | — | — |
> | ランタイム | 0（スクリプト・手順書） | — | — |
> | 課題の置き場 | 2 | 1 | 止まる 1・誤る 1 |
> | その他（ai-plugins 固有の運用・置き場） | 10 | 6 | 止まる 1・誤る 5・無駄に動く 4 |
> | 計 | 27 | 14 | |
>
> 言語・ランタイムは静的には 0 件だったが、他のリポジトリの事例では言語 5 件・ランタイム 5 件が起きている（#1339・#1777・#2449・#1568・#2509・#2447 など。判定の論理が言語ごとに分かれていない形）。
>
> ## 違反の一覧（重なりを除いた 27 件）
>
> | # | 区分 | まとまり | 代表の場所 | 他のプロジェクトで起きること | 重さ | 既存の課題 |
> | --- | --- | --- | --- | --- | --- | --- |
> | 1 | 配布 | 並列の計画のまとめた配布が、版を `release.plugin` と `plugin.json` だけから決め、決められないと計画全体を止める。`delivery` の宣言を読まない | `scripts/lib/parallel_run.py:86-100`・`:219`・`lib/parallel_external.py:194-196`・`development-workflow/references/parallel.md:84-88` | CD のアプリのリポジトリで計画全体が止まる（carul で実際に起きた） | 止まる | #2616・#2623・#2554 |
> | 2 | 配布 | 雛形 `new release` の形が package-plugin の 1 つだけ | `supervise_lib/release_templates.py:81-83`・`:488`・`new_args.py:17` | プラグインでないリポジトリで、どう設定しても使えない | 止まる | #2623 |
> | 3 | 配布 | 形 package-plugin の部品に ai-plugins の配置（プラグインの置き場・README とマーケットプレイスの書き換え・`docs/versioning-and-distribution.md`・タグ `<plugin>--v`・根の CHANGELOG）が入っている | `lib/step_result.py:334`・`release-steps.py:184-193`・`release_lib/bump.py:170` | 別の配置のプラグインのリポジトリで止まる | 止まる | #1756（置き場の部分だけ） |
> | 4 | 配布 | 開発ループの `loop sprint` が、配布の形を見ずに開発版の版数（`--dev`）を必須にする | `lib/sprint_loop_cli.py:109`・`development-workflow/references/loop.md:44-45` | 版数を持たないサービスで意味のない版を作る | 誤る | #2354・#2623 |
> | 5 | 配布 | デプロイを NDF の工程が担う前提で、CD を持つリポジトリを例外扱いにする | `release/SKILL.md:45-47`・`:88-89`・`release/references/form-service.md:37-59`・`production-merge.md` | 工程表に配布が残り、版上げ・リリース PR に入りかける | 誤る | #2623 |
> | 6 | 配布 | `release` の手順 3 が、どの形にも `release/v<版>` の PR・CHANGELOG の版の節・スプリント単位の版上げを課す（同じ文書の `:186` と食い違う） | `release/SKILL.md:191-194`・`:200-201`・`:76-86` | 版数の無いサービスで要らない PR と節を作る | 無駄に動く | #2623 |
> | 7 | 配布 | PR 本文に CHANGELOG の材料の節（利用者向けの変化・移行の手順）をどのリポジトリでも求める | `pr-steps.py:57`・`pr/SKILL.md:89-91`・`:100-107` | CHANGELOG の無いリポジトリの全 PR に意味のない節が入る | 無駄に動く | 無し（関連 #2601） |
> | 8 | 配布 | 承認の記録と承認資料（`issues/approval-<プラグイン>-v<版>.md`）を一般の規則として書く。承認の外で本番へ通すパスを `docs/`・`issues/` に固定 | `release/SKILL.md:142-145`・`pace.md:399`・`release_lib/approval.py:22`・`:106` | サービスの経路で書けない記録を求める。`docs/` を配るリポジトリで承認の後の変更が素通りする | 誤る | #2623・#2619（素通りは無し） |
> | 9 | ブランチ | コードレビューのエージェントが差分を `main...HEAD` で取る | `agents/code-reviewer.md:47`・`:90`・`:91` | develop を起点にするリポジトリで関係ない変更までレビューする。`main` が無いと差分を取れない | 誤る | 無し |
> | 10 | ブランチ | 並列の計画の本番のマージの照会が `--base main` の決め打ち | `lib/parallel_external.py:268`・`:287` | 本番が main でないリポジトリで届いた段と URL が空になる | 誤る | 無し |
> | 11 | ブランチ | 起点が決まらないときの最後の既定が `"main"`（止まって設定を求めない） | `pr-steps.py:116`・`pr_steps_lib/gh_pr.py:122` | `master`・`trunk` のリポジトリで無いブランチへ PR を作ろうとして失敗する | 止まる（軽） | 無し |
> | 12 | ブランチ | `production_branch` が無いと本番を既定ブランチとみなす（「既定ブランチ = main = 本番」の前提） | `lib/repo.py:144-151`・`supervise_lib/decl.py:219`・`new_args.py:117` | 既定ブランチが develop のリポジトリで、起点へのマージがすべて本番扱いになる | 誤る | 無し |
> | 13 | ブランチ | 手順書が起点と本番を `develop`/`main` の文字で書く。`pr` の手順書は「既定は main」と書き、スクリプトの実際の既定と食い違う | `development-workflow/SKILL.md:31`・`:302`・`parallel.md:86`・`pace.md:170`・`pr/SKILL.md:21`・`:41` | main 1 本のリポジトリで conductor が develop を探す。`--base main` を明示して宛先を誤る | 誤る | #1338（一部） |
> | 14 | ブランチ | main 宛ての PR の閉じる語で課題が先に閉じる | （事例）carmo-screening | 本番の前に課題が閉じる | 誤る | #2082・#2228 |
> | 15 | テスト | `supervise.py example` のプランの見本が `pytest -q` を埋め込む | `supervise_lib/plan.py:22`・`:335`・`supervise.py:265` | 見本を写すと Python でないリポジトリで落ちる | 無駄に動く | 無し |
> | 16 | 課題 | 課題の追跡が GitHub Issues と `gh` に固定。`project.json` の `issues.primary` を宣言させているのに読むのは使用量の集計だけ。`gh` を呼ぶスクリプトは 78 本 | `progress-record.sh`・`supervise_lib/worker_steps.py`・`sprint-close.py`・`progress-tracking/SKILL.md:148`・`out-of-scope/SKILL.md:62` | Redmine・Markdown を正本にするリポジトリで止まる（carmo-system-console は Redmine が正本） | 止まる | #2239（起票の部分だけ）・#479〜#484（見送り） |
> | 17 | 課題 | マイルストーンの名前の規則（`<2 桁の連番> <主題>`・閉じたら `v<版数>`）を、マイルストーンを使うどのリポジトリにも当てる（ai-plugins の AGENTS.md の運用） | `backlog-refinement/references/milestones.md:29-31`・`:95`・`glossary.md:61` | 別の命名のリポジトリで直近のマイルストーンを取り違える | 誤る | 無し |
> | 18 | その他 | マージを `gh pr merge --admin`・merge commit に固定 | `merged_lib/merge.py:221`・`:23`・`merged/SKILL.md:154` | 管理者でない利用者で止まる。管理者なら必須レビューを黙って迂回する | 止まる | #1338 |
> | 19 | その他 | 確定仕様の置き場を `docs/specifications` に固定 | `lib/repo.py:20`・`design-match.py:264`・`plan-to-spec-steps.py:139` | 別の場所に仕様を置くリポジトリで、突き合わせが 1 本も拾わず「食い違い無し」と報告する | 誤る | 無し（関連 #2013） |
> | 20 | その他 | 設計文書・要求の写し・承認資料の置き場を `issues/` に固定 | `supervise_lib/procedures.py:16`・`sprint_waves.py:132`・`design_stage.py:99-104` | `issues/` を別に使うリポジトリでファイルが混ざる | 無駄に動く | #2600 |
> | 21 | その他 | 日本語の強制（コミット・PR・`<!-- I want to review in Japanese. -->`）とコミットの接頭辞（`Docs:`・`Update:`・`Release:`）。宣言に言語と規約の項目が無い | `lib/pr_mode.py:18`・`pr-steps.py:54`・`plan-to-spec-steps.py:154`・`lib/formatting.py:130`・`pr/SKILL.md:69` | 英語で運用するリポジトリ・Conventional Commits のリポジトリで規約外のコミットと本文ができる | 誤る | 無し |
> | 22 | その他 | 利用者向けの手順書が ai-plugins にしか無いファイル（`AGENTS.md`・`docs/versioning-and-distribution.md`）を根拠・正本として指す | `release/SKILL.md:132`・`development-workflow/SKILL.md:244`・`pace.md:420`・`release/references/release-steps.md:178` | 導入先でリンク切れ。条件の正本を読めない | 誤る | 無し |
> | 23 | その他 | 主ディレクトリの編集の案内を出さないパスの既定に ai-plugins の運用（`docs/`・`issues/`・`.kiro/`）が入る | `hook_lib/worktree.py:29`・`:106-111` | `docs/` が成果物のリポジトリで案内が出ない | 誤る（軽） | 無し |
> | 24 | その他 | 検査の既定値に ai-plugins のファイル（`docs/presentations/`・`KIRO.md`） | `doc-lint.py:49`・`instructions_lib/declaration.py:18` | 同じ名前の置き場が黙って検査から外れる | 無駄に動く | #1968 |
> | 25 | その他 | 単独起動の場所・ランタイムが書くファイル（`.serena/`・`.amazonq/`）を Skill ごとの例外で扱う | （事例）#1655→#1707、#1436→#2448 | 別の Skill・別の Tool で同じ形が再発する | 誤る | #1707・#2448 |
> | 26 | その他 | `--repo` を見ずに作業ディレクトリのリポジトリで PR を引く | （事例）#2446 | 別のリポジトリの PR のマージで判定を誤る | 誤る | #2446 |
> | 27 | 検査 | 違反を見張る CI の検査 `skill-repo-assumptions-check` に穴がある。`skills/*/scripts/` の 143 本と `hooks/*.json` を走査しない。リテラルの `"main"`・`--base main`・`--admin`・タグの `--v`・`release/v`・`plugin.json`・`docs/specifications`・`issues/`・`gh`・`pytest` を検知しない。`release-steps.py`・`step_result.py`・`release-verification-steps.py` をファイルごと除外している | `.github/workflows/` の該当ジョブ | 上の多くが検査を通って入った | 無駄に動く | 無し |
>
> ## 根にある形
>
> 1. **「例外で直す」が繰り返されている。** 他のリポジトリの事例 50 件のうち、その場の例外で直したものが 7 件あり、そのうち 4 件は同じ区分で別の形として再発した（#359→#1088、#1435→#2157、#1655→#1707、#1436→#2448）
> 2. **宣言はあるのに読まない経路がある。** #1336 で `delivery` を作ったが、後から足した並列の計画（1）が読まない。`issues.primary`（16）も宣言だけで読まない。新しい経路を足すときに宣言を読むことを強制する仕組みが無い
> 3. **`supervise.json` を持つのは ai-plugins だけ**（/work の下の 11 リポジトリで確かめた）。`release.plugin` を読む経路は、ほかのどのリポジトリでも値が無い
> 4. **見張りの検査（27）が語の照合だけで、範囲にも穴がある。** #2616 のように語を含まないまま ai-plugins の形を前提にした論理は、語の照合では原理上拾えない
>
> ## 調べた記録
>
> <details><summary>スクリプトと hook（worker の報告）</summary>
>
> # Value 6 の違反の調べ（スクリプト・hook）
>
> - 調べた日: 2026-11-05（develop の b285499f7）
> - 範囲: `plugins/ndf/scripts/`・`plugins/ndf/skills/*/scripts/`・`plugins/ndf/hooks/` の `.py`・`.sh`・hook の JSON。`tests/` と `experimental/` は除いた（477 本。うち `skills/*/scripts` が 143 本）
> - 判定: 設定か引数で受けていても、無いときの既定が ai-plugins の値なら違反とした。無いときに止まって設定を求めるものは違反にしていない
> - 重さ: **止まる**（工程が進まない）・**誤る**（間違った結果・記録を出す）・**無駄に動く**（要らない案内・処理・規約外の出力）
>
> ## 違反でないと確かめたもの（先に示す）
>
> - **テストのコマンド**: `supervise_lib/decl.py:_require_declared`（271 行目付近）は、`.ndf/project.json` の `test`・`--test-cmd`・`supervise.json` の `test.command` のどれも無いと `DeclError` で止まる。`test-run.py`・`test_strategy.py`・`worktree-testenv.sh`・`worktree_deps.py` も宣言からだけ読む
> - **起点のブランチ**: `decl.py:_apply_simple_decls`・`paths.py:_resolve_base`・`check-trigger.py:range_base` は `--base` → `worktree.json` の `base_branch` → `origin/HEAD` の順に読み、決まらなければ止まる
> - **単独のスプリントの配布**: `sprint_routes.py`・`lib/delivery.py` は `release.form` が無ければ雛形を使わず、`delivery` の宣言から昇格・手で届ける・届けないを組む（carul の 1 スプリントが通ったのはこの経路）
> - **言語**: `cross-refactoring` の計測（`codemetrics.py:92-102`）は Python に ruff/complexipy/symilar、ほかに lizard/jscpd を使い分ける。`.md` の文言テストの拒否（`implement.py:383`）と `doc-lint.py` は `ndf_policies` で宣言したときだけ働く
> - **ランタイム**: worker の起動（`claude -p`・`external-ai.py`）は NDF 自身の実行で、対象プロジェクトのコードの実行に掛からない
> - **NDF 自身の置き場**: `.claude-plugin/plugin.json` を読む `deps.py:119`・`usage_ledger.py:73`・`run_metrics.py:106`・`relay_lib/version_dir.py:86`・`parallel_external.py:79` は、NDF プラグイン自身の版を読んでいる。スキーマの URL（`worktree-setup.sh:64`・`project_lib/model.py:17`）も NDF 自身の宣言の形を指している
>
> ## 1. テストのコマンド（1 件）
>
> | 違反のまとまり | 代表の ファイル:行 | 今の既定 | 他のプロジェクトで何が起きるか | 重さ | 既存の課題 |
> | --- | --- | --- | --- | --- | --- |
> | `supervise.py example` が出すプランの見本と、docstring の計画の例が `pytest -q` を埋め込んでいる | `scripts/supervise_lib/plan.py:22`・`plan.py:335`（`EXAMPLE`）・`supervise.py:265` | `"cmd": "pytest -q"` | 見本を写して計画を書くと、Python でないリポジトリで pytest を走らせ、「テストが無い」で落ちる | 無駄に動く | 無し |
>
> ## 2. 起点のブランチ（3 件）
>
> | 違反のまとまり | 代表の ファイル:行 | 今の既定 | 他のプロジェクトで何が起きるか | 重さ | 既存の課題 |
> | --- | --- | --- | --- | --- | --- |
> | 並列の計画で、本番のマージを探す PR の照会が宛先を `main` に決め打ちしている（`production_branch` を読まない） | `scripts/lib/parallel_external.py:268`・`parallel_external.py:287` | `gh pr list --base main` と head `release/v<版>` | 本番のブランチが `main` でないリポジトリでは、本番まで届いても届いた段（`progress/<連番>.json`）と `prod_pr_url` が空になり、記録が欠ける | 誤る | 無し（関連 #2616・#1338） |
> | 起点が決まらないときの最後の既定が `"main"` のリテラル（宣言 → 既定ブランチの次） | `scripts/pr-steps.py:116`・`scripts/pr_steps_lib/gh_pr.py:122` | `... or "main"` | `origin/HEAD` を持たず `main` も無い clone（`master`・`trunk`）で、無いブランチへ PR を作ろうとして失敗する。止まって設定を求めない | 止まる（軽） | 無し |
> | `production_branch` が無いと、本番を既定ブランチ（`origin/HEAD`）とみなす。ai-plugins の「既定ブランチ = main = 本番」の形を前提にしている | `scripts/lib/repo.py:144-151`・`scripts/supervise_lib/decl.py:219`・`scripts/supervise_lib/new_args.py:117` | 既定ブランチ（`origin/HEAD` → `main` → `master`） | 既定ブランチが `develop` のリポジトリ（git-flow でよくある形）では起点と本番が同じ名前になる。起点へのマージがすべて本番扱いになり、余計に承認ゲートで止まる | 無駄に動く | 無し |
>
> ## 3. 配布の手段（3 件）
>
> | 違反のまとまり | 代表の ファイル:行 | 今の既定 | 他のプロジェクトで何が起きるか | 重さ | 既存の課題 |
> | --- | --- | --- | --- | --- | --- |
> | 並列の計画のまとめた配布が、出す版を `release.plugin` とベースブランチの `plugin.json` だけから決める。決められないと計画全体を止める | `scripts/lib/parallel_run.py:86-100`（`dev_version`）・`parallel_run.py:219`・`scripts/lib/parallel_external.py:194-196` | プラグインの版（plugin.json）と `release/v<版>`・`issues/approval-<plugin>-v<版>-parallel-*.md`（`parallel_release.py:320`） | CD でデプロイするアプリ（carul）で、レーンの配布の依頼 1 つが原因で計画全体が止まる | 止まる | #2616・#2554・#2623 |
> | 形 package-plugin の部品に ai-plugins の配置が入っている。プラグインの置き場（`ndf`・`playwright-kit`・`plugins/mcp/<名前>`）、README とマーケットプレイスの書き換え（`/plugins/cache/ai-plugins/ndf/`・`ndf@ai-plugins`）、`docs/versioning-and-distribution.md`、タグ `<plugin>--v<版>`、根の `CHANGELOG.md` | `scripts/lib/step_result.py:334`・`scripts/release-steps.py:184-193`（加えて 286-287・660）・`scripts/release_lib/bump.py:170` | ai-plugins の配置とタグの形 | ai-plugins と違う配置のプラグインのリポジトリ（根に `.claude-plugin/` がある、タグが `v1.2.3` など）で、版上げが「プラグインが無い」で止まるか、前のタグを見つけられない。README の書き換えは「手で直す」の項目として残る | 止まる | #1756（置き場の部分だけ） |
> | 雛形 `new release` の形が package-plugin の 1 つしかない（`RELEASE_FORMS`） | `scripts/supervise_lib/release_templates.py:488`・`release_templates.py:81-83`・`scripts/supervise_lib/new_args.py:17` | `{"package-plugin": ...}` | プラグインでないリポジトリでは `new release` が使えない（`release.form` を書いても合う形が無い）。止まりはするが、どんな設定を書いても満たせない | 止まる | #2623 |
>
> ## 4. 言語（0 件）
>
> 違反は見つからなかった。
>
> - 探した語: `pytest`・`uv run`・`npm (test|run)`・`pnpm`・`yarn`・`go test`・`cargo test`・`ruff`・`vulture`・`pyproject`・`mypy`・`black`・`isort`・`pip install`・`uv sync`・`symilar`・`endswith(".py")`
> - 範囲: 上の 477 本
> - 当たったものは 3 通りだった。NDF 自身の環境（`deps.py`・`hook-env.py`・`relay_lib/runtime.py`）、計測と判定の材料（`measure_repo.py`・`measure_ci.py`・`junit.py` の多言語の一覧）、言語ごとに使い分けている所（`codemetrics.py`）。`verify.py:398` の Python だけの判定は、宣言で有効にしたときだけ働く（`implement.py:383`）
>
> ## 5. ランタイム（0 件）
>
> 違反は見つからなかった。
>
> - 探した語: `RUNTIMES =`・`("claude", "codex"`・`claude,codex`・`"runtimes"`・`claude -p`
> - 範囲: 上の 477 本
> - 当たったのは NDF 自身の実行・使用量の帳簿・cross 系の相手役の一覧だった。`release-verification-steps.py:380` の `--runtimes claude,codex` は、形 package-plugin の導入を確かめるときだけ使う（3 の 2 行目と同じ形の中）
>
> ## 6. 課題の置き場（1 件）
>
> | 違反のまとまり | 代表の ファイル:行 | 今の既定 | 他のプロジェクトで何が起きるか | 重さ | 既存の課題 |
> | --- | --- | --- | --- | --- | --- |
> | 課題の追跡が GitHub Issues と `gh` に固定されている。`.ndf/project.json` の `issues.primary` は計測で書かれる（`project-decl.py:190`）が、それを読んで置き場を替えるスクリプトが無い。`gh` / `gh issue` を呼ぶ配布のスクリプトは 78 本 | `scripts/progress-record.sh`（課題の本文の `## 進行`）・`scripts/supervise_lib/worker_steps.py`（`gh issue view` で課題の本文をプロンプトへ）・`scripts/sprint-close.py`（課題を閉じる） | GitHub Issues（`gh issue view/edit/close`・`Closes #N`） | GitLab・Jira・Backlog で課題を持つリポジトリでは、進行の記録・課題の本文の読み込み・閉じる処理のすべてが `stopped` になる | 止まる | #2239（out-of-scope の起票先だけ） |
>
> ## 7. その他（7 件）
>
> | 違反のまとまり | 代表の ファイル:行 | 今の既定 | 他のプロジェクトで何が起きるか | 重さ | 既存の課題 |
> | --- | --- | --- | --- | --- | --- |
> | マージを `gh pr merge --admin` で打ち、方法の既定が merge commit（ai-plugins の ruleset を前提にしている） | `scripts/merged_lib/merge.py:221`・`merged_lib/merge.py:23`・`scripts/merged-steps.py:459` | `--admin`・`--method merge` | 管理者権限の無い利用者や、merge commit を禁じたリポジトリでマージが失敗する。権限がある利用者では、必須のチェックを素通りする | 止まる | #1338 |
> | 確定仕様の置き場を `docs/specifications` に固定している | `scripts/lib/repo.py:20`・`scripts/design-match.py:264`・`scripts/plan-to-spec-steps.py:139` | `docs/specifications/` | 仕様を別の場所に置くリポジトリでは、design-match の突き合わせが既存の仕様を 1 本も拾わない（食い違いが無いと報告する）。索引への登録も行われない | 誤る | 無し（関連 #2013） |
> | 設計文書・要求の写し・承認資料の置き場を `issues/` に固定している | `scripts/supervise_lib/procedures.py:16`・`scripts/supervise_lib/sprint_waves.py:132`・`scripts/supervise_lib/design_stage.py:99-104` | `issues/issue-<n>-*.md`・`issues/approval-*.md` | `issues/` を別の用途に使うリポジトリで、NDF のファイルが混ざる。名前を設定で替えられない | 無駄に動く | #2600 |
> | PR 本文の目印とコミットメッセージの接頭辞が ai-plugins の規約（日本語のレビューの目印・`Docs:`・`Update:`・`Release:`）に固定されている | `scripts/lib/pr_mode.py:18`・`scripts/plan-to-spec-steps.py:154`・`scripts/lib/formatting.py:130` | `<!-- I want to review in Japanese. -->`・`Docs: …`・`Update: …` | Conventional Commits などの別の規約を持つリポジトリで、規約外のコミットと本文ができる（commitlint があれば CI で落ちる） | 無駄に動く | 無し |
> | 主ディレクトリの編集の案内を出さないパスの既定に、ai-plugins の運用（`docs/`・`issues/`・`.kiro/` など）が入っている | `scripts/hook_lib/worktree.py:29`・`hook_lib/worktree.py:106-111` | `DEFAULT_ALLOW_PATHS`（`guard.allow_paths` で差し替えられる） | `docs/` が成果物（公開するサイトなど）のリポジトリで、主ディレクトリの編集に案内が出ない。働くのは `.ndf/worktree.json` を持つリポジトリだけ | 誤る（軽） | 無し |
> | 検査の既定値に ai-plugins のファイルが入っている | `scripts/doc-lint.py:49`（`docs/presentations/` を外す）・`scripts/instructions_lib/declaration.py:18`（指示書の既定に `KIRO.md`） | ai-plugins の置き場とファイル名 | 害は小さい（無いファイルは読まない）。ただし同じ名前の置き場を持つリポジトリでは、黙って検査から外れる | 無駄に動く | #1968（KIRO.md の既定の食い違い） |
> | 承認の外でも本番へ通すパスに `docs/`・`issues/` を固定している（形 package-plugin の中だけ） | `scripts/release_lib/approval.py:22`・`approval.py:106` | `UNGATED = ("docs/", "issues/")` | `docs/` も配布物に含むリポジトリで、承認の後に入った `docs/` の変更が承認を取り直さずに本番へ入る | 誤る | 無し |
>
> ## CI のジョブ `skill-repo-assumptions-check` が見ているもの・見ていないもの
>
> ジョブの定義は `.github/workflows/runtime-plugin-validate.yml:51-58`、本体は `scripts/check-skill-repo-assumptions.py`。今の結果は「ヒット 31 行・すべて除外・終了コード 0」。
>
> **見ているもの**
>
> - manifest に載る Skill の Markdown に掛ける `PATTERNS`（14 語。`claude plugin `・`.claude-plugin`・`marketplace.json`・`manifests/`・`check-pr-base` など）
> - `plugins/ndf/scripts/` の下の `.py`・`.sh`・`.js` 334 本に掛ける `SCRIPT_PATTERNS`（8 語。`plugins/ndf/`・`playwright-kit`・`build-runtime-plugins`・`check-skill-frontmatter`・`check-markdown-links`・`check-doc-line-limit`・`claude,codex,kiro`・`origin/develop`）
> - 除外の宣言が陳腐化していないか（宣言した対象が走査の範囲に実在するか）
>
> **見ていないもの**
>
> - **走査していない範囲**: `skills/*/scripts/` の 143 本（`collect_scripts` は family の `scripts/` だけを見る。`collect_documents` は `*.md` だけを見る）。`hooks/*.json` も見ない
> - **検知しない語**: リテラルの `"main"`・`"develop"`（`origin/develop` しか見ない。上の 2 の 1 行目と 2 行目を通す）、`--base main`、`--admin`、タグの形 `--v`／`ndf--v`、`release/v`、スクリプトの中の `plugin.json`（`.claude-plugin` は Markdown の語にしか無い）、`docs/specifications`、`issues/`、`gh`、`pytest`
> - **ファイル単位の除外が中身を隠している**: `release-steps.py`・`lib/step_result.py`・`release-verification-steps.py` は理由つきでファイルごと除外している。このため、形 package-plugin の中にある ai-plugins の配置（上の 3 の 2 行目、#1756）を検知しない
> - 書き方が正しいか（設定を読んでいるか・既定が何か）は判定しない（docstring に明記されている）。このため、今回の 3 の 1 行目（#2616）のように、語を含まないまま ai-plugins の形を前提にした論理は原理上拾えない
>
>
> </details>
>
> <details><summary>手順書（worker の報告）</summary>
>
> # Value 6 違反の調査（手順書: skills/**/*.md・agents/*.md・plugins/ndf/README.md）
>
> 調べた日: 2026-11-05。範囲: `plugins/ndf/skills/**/*.md`（`release/assets/slides/node_modules/` は除く）・`plugins/ndf/agents/*.md`・`plugins/ndf/README.md`。
> 手順書が名指しするスクリプトは、引数の既定を確かめるためだけに読んだ（`pr-steps.py`・`lib/sprint_loop_cli.py`・`merged_lib/merge.py`・`lib/usage_aggregate.py`）。
>
> 重さ: **止まる**（工程が進まない）/ **誤る**（違う値・違う対象で進む）/ **無駄に動く**（要らない作業・記載を求める）。
>
> ## 1. テストのコマンド — 0 件
>
> 探した語: `pytest`・`uv run`・`ruff`・`vulture`・`pyproject`・`npm test`・`make test`（`lang-*` を除き 47 か所）。
>
> 全部が「例」（`quality-gates`・`pr-tests`・`tdd-cycle`・`cross-review`・`agents/debugger.md`）か、宣言（`.ndf/project.json` の `test`）を読む手順（`cross-refactoring/SKILL.md:49,102`・`project-analysis.md:71`）だった。`tdd-cycle/SKILL.md:39-56` は宣言を読まずにリポジトリから探すが、探し方は言語を問わない（違反ではない。宣言を読めば手間が減る、という程度）。
>
> ## 2. 起点のブランチ — 3 件
>
> | 違反のまとまり | 代表の ファイル:行 | 書いてある前提 | 他のプロジェクトで何が起きるか | 重さ | 既存の課題 |
> | --- | --- | --- | --- | --- | --- |
> | コードレビューのエージェントが差分を `main` と比べる | `agents/code-reviewer.md:47`・`:90`・`:91` | 起点は `main` | `base_branch: develop` のリポジトリでは、`main...HEAD` に develop の未リリース分が入り、関係ない変更までレビューする。`main` が無い（`master` など）リポジトリでは差分を取れない | 誤る | 無し |
> | `pr` の手順書が「既定は `main` ベース」と書く（スクリプトは `.ndf/worktree.json` の `base_branch`、無ければ既定ブランチを使う。`pr-steps.py:116-118`） | `pr/SKILL.md:21`・`:41`・`:62` | 宛先は `main`/`master` | 手順書を読んだエージェントが `--base main` を明示で渡すと、設定より前に効いて develop 運用のリポジトリで宛先を誤る | 誤る（低） | #1338（develop と main の 2 本に縛らない） |
> | 起点と本番を `develop`/`main` という文字で書く（判定はスクリプトが設定から行う） | `development-workflow/SKILL.md:31`（「develop から切り」）・`:302`（「このリポジトリの値は `develop`」）・`development-workflow/references/parallel.md:86`・`:125`・`:143`（「`origin/develop`」「すでに `main` にあれば」）。ほか `pace.md:170,189,197`・`glossary.md:127` | 起点が develop、本番が main の 2 本 | main 1 本のリポジトリ（carmo-system-console など）では手順の文が実物と合わず、conductor が `develop` を探しに行くか、無いブランチを宛先に書く | 誤る（低） | #1338 |
>
> ## 3. 配布の手段 — 6 件
>
> | 違反のまとまり | 代表の ファイル:行 | 書いてある前提 | 他のプロジェクトで何が起きるか | 重さ | 既存の課題 |
> | --- | --- | --- | --- | --- | --- |
> | 並列の計画の「まとめた配布」と一時レーンが、版を上げて出す配布だけを持つ。CD のリポジトリの経路が無い | `development-workflow/references/parallel.md:84-88`・`:100-113`・`:121-133` | どのレーンの配布の依頼も、版を決めて提示のプラン → 本番のプランで出す（版は `plugin.json` から取る） | CD を持つアプリのリポジトリ（carul）で、配布の依頼 1 つで計画全体が止まる（実際に起きた） | 止まる | #2616・#2554・#2623 |
> | 開発ループの `loop sprint` が、配布の形を見ずに開発版の版数を必須にする（`lib/sprint_loop_cli.py:109` の `--dev` が `required=True`） | `development-workflow/references/loop.md:44`（「版を決める（`release` の規則）」）・`:45`・`:13` | どのリポジトリも版数で出す | 版数を持たないサービスで、conductor が意味のない版数をでっち上げて渡す。渡さなければ止まる | 誤る | #2354（版を流す時点に決める）・#2623 |
> | 配布を NDF の工程が担う前提で、CD を持つリポジトリを例外として書く | `release/SKILL.md:45-47`・`:88-89`・`release/references/form-service.md:37-59`（ほか `development-workflow/references/production-merge.md` 全体） | 配布は `release` の工程（版上げ → PR → 公開）。マージで反映する形は「この決まりは当てはまらない」 | CD のリポジトリでも工程表に「配布」が残り、版上げ・リリース PR の準備に入りかける。例外を読み落とすと二重に出す | 誤る | #2623（根の課題） |
> | `release` の手順 3 が、どの形にも版上げの PR（`release/v<版>`）と CHANGELOG の版の節、「PR ごとに版を上げない・スプリント単位で上げる」を課す | `release/SKILL.md:191-194`・`:200-201`・`:76-86` | 版数と変更履歴を持つ配布物（プラグイン） | 版数を持たないサービス（`:186` 自身が認めている）で、要らない版上げの PR と CHANGELOG の節を作る。`:186` と `:191` が食い違う | 無駄に動く | #2623 |
> | PR の本文に、CHANGELOG の材料の節（`## 利用者向けの変化`・`## 移行の手順`。プラグインの名前を添える・MAJOR の候補になる）をどのリポジトリでも求める（`pr-steps.py:57`） | `pr/SKILL.md:89-91`・`:100-107` | 版の節を `release-steps.py notes` が組むプラグインの配布 | CHANGELOG もプラグインも無いリポジトリでも、`create`/`update` の `next` が節を足すよう求め、意味のない節が全部の PR に入る | 無駄に動く | 無し（関連 #2601） |
> | 承認の記録と承認資料の置き場が、プラグインの形のスクリプトを前提に一般の規則として書かれている | `release/SKILL.md:142-145`（「承認の記録の無い承認資料では本番の配布が通らない」。`approval-facts`/`approve` はプラグインの形だけ）・`development-workflow/references/pace.md:399`（`issues/approval-<プラグイン>-v<版>.md`）・`release-verification/SKILL.md:31`（「版の更新・タグ・公開」） | 本番の承認 = 版の承認資料 | サービスの手で届ける経路（`deploy-facts`）では書けない記録を求めているように読め、承認資料の名前にプラグインと版が入らない | 誤る（低） | #2623・#2619 |
>
> ## 4. 言語 — 0 件
>
> 探した語: `ruff`・`vulture`・`pyproject`・`mypy`・`complexipy`（`refactoring/references/lang-*` を除く）。
> `cross-refactoring/docs/01-state-and-propose.md:309,348` の複雑さの測り方は言語ごとに分けてある（Python は `ruff-complexipy`、ほかは `lizard`）。`merged/SKILL.md:125` の生成物の一覧は複数言語を並べている。違反は無かった。
>
> ## 5. ランタイム — 0 件
>
> 探した語: `docker compose`・`devcontainer`・`.venv`・`uv sync`・`npm install`・`node_modules`。
> 対象プロジェクトの実行環境は宣言（`project.json` の `test.suites[].container`、`worktree.json` の `deps`）で受けていた。`uv`・`python3`・`jq`・`gh` を求める箇所（`cross-refactoring/SKILL.md:171`・`install-wrapper/SKILL.md:60`）は NDF 自身の実行環境で、対象プロジェクトのものではない（`gh` は区分 6 で扱う）。
>
> ## 6. 課題の置き場 — 2 件
>
> | 違反のまとまり | 代表の ファイル:行 | 書いてある前提 | 他のプロジェクトで何が起きるか | 重さ | 既存の課題 |
> | --- | --- | --- | --- | --- | --- |
> | 課題の正本を GitHub Issues に固定している。`.ndf/project.json` の `issues.primary`（`github`・`markdown`・`redmine` など。`project-analysis.md:74`）を宣言させているのに、読むのは使用量の集計（`lib/usage_aggregate.py:248`）だけ | `progress-tracking/SKILL.md:100`・`:148`・`:206`（進行の記録を issue の本文へ、閉じる語で課題を集める）・`out-of-scope/SKILL.md:62-114`（`issue-file.py --repo`）・`requirements-design/SKILL.md:189-190`（`gh issue edit`）。ほか `retrospective/SKILL.md:317-334`・`backlog-refinement/SKILL.md:153,369,409` | 課題は GitHub Issues、閉じるのは closing keywords | Redmine や Markdown で課題を持つリポジトリでは、進行の記録・範囲外の起票・スプリントを閉じる工程・棚卸しが書けずに止まる（スプリントを閉じられない） | 止まる | #2239（起票だけ。全体は無し） |
> | マイルストーンの名前の規則（open は `<2 桁の連番> <主題>`、閉じたら `v<版数>` へ改名）を、マイルストーンを使うどのリポジトリにも当てる。これは ai-plugins の `AGENTS.md` の運用 | `backlog-refinement/references/milestones.md:29-31`・`:95`・`development-workflow/references/glossary.md:61`（ほか `issue-plan-strategy/references/execution-plan.md:22`） | マイルストーンの名前が着手の順序を表す | 「Sprint 12」「2026 Q1」「v2.1」のような名前のリポジトリで、直近のマイルストーンを取り違え、新しく作るマイルストーンに別の規則の名前を付ける | 誤る | 無し |
>
> GitHub Projects のボードは `.ndf/projects.json` が無ければ何もしない（`development-workflow/references/projects-tracking.md:6-7`）ので違反ではない。
>
> ## 7. その他（ai-plugins 固有の運用を利用者の手順として書いている箇所）— 4 件
>
> | 違反のまとまり | 代表の ファイル:行 | 書いてある前提 | 他のプロジェクトで何が起きるか | 重さ | 既存の課題 |
> | --- | --- | --- | --- | --- | --- |
> | マージを `gh pr merge --admin` に固定する（`merged_lib/merge.py:221`）。ai-plugins の ruleset を越える運用 | `merged/SKILL.md:154` | マージする人は管理者で、必須レビューを越えてよい | 管理者でない利用者では止まる。管理者なら他のリポジトリの必須レビューを黙って迂回する。`form-package-plugin.md:195` の「`--admin` を既定にしない」と食い違う | 止まる（迂回は安全の問題） | #1338 |
> | コミットと PR を日本語で書く・本文末尾に `<!-- I want to review in Japanese. -->` を付ける（`pr-steps.py:54`）。ai-plugins の `AGENTS.md` の言語の方針 | `pr/SKILL.md:69`・`:89`・`:187` | 開発者が日本語で読み書きする | 英語で運用するリポジトリ・公開 OSS で、日本語のコミットと PR、日本語のレビューを求める印が入る。宣言（`project.json` の `ndf_policies`）に言語の項目が無い | 誤る | 無し |
> | 利用者向けの手順書が、ai-plugins のリポジトリにしか無いファイルを根拠・正本として指す | `release/SKILL.md:132`・`development-workflow/SKILL.md:244`・`development-workflow/references/pace.md:420`（「条件は `AGENTS.md`」。pace の条件は ai-plugins の AGENTS.md にしか無い）・`release/references/release-steps.md:178`（`../../../../../docs/versioning-and-distribution.md`。導入先ではリンク切れ）・`loop.md:4`・`relay.md:324`・`estimate/references/rules.md:132` | 読み手は ai-plugins の clone の中にいる | 他のリポジトリで手順書が指す `AGENTS.md` には条件が無く、リンクは開けない。条件を探して手が止まるか、そのリポジトリの AGENTS.md を誤って根拠にする | 誤る（低） | 無し |
> | 要求と受け入れ条件・計画ファイルの置き場を `issues/` に固定する | `development-workflow/SKILL.md:176`・`development-workflow/references/projects-tracking.md:46`（`issues/<ファイル名>`）・`pace.md:399` | リポジトリの根に `issues/` を置ける | 既に `issues/` を別の用途で使うリポジトリ・根に置き場を足せないリポジトリで、ファイルが混ざる | 無駄に動く（低） | #2600 |
>
> ai-plugins 自身の開発の話として違反に数えなかったもの: `skills/AUTHORING.md`・`skills/EXCERPTS.md`（Skill の書き手向け）、`plugins/ndf/README.md` の導入手順（NDF の配り方そのもの）、`release/references/form-package-plugin.md` と `release-steps.md` の「Claude Code のプラグインの形」の節（形で分けてある）、`pace.md:11`・`glossary.md:75`・`project-mvv.md:8`・`waiting.md:13`・`context-window.md:46`・`work-vessels.md:82`（実測と例として ai-plugins の値を挙げている）、`projects-tracking.md:22,141-152`（`owner: devbasex` は設定の例）、`worktree/references/declaration.md:34`（スキーマの URL）。
>
> ## 件数のまとめ
>
> | 区分 | 件数 |
> | --- | --- |
> | 1. テストのコマンド | 0 |
> | 2. 起点のブランチ | 3 |
> | 3. 配布の手段 | 6 |
> | 4. 言語 | 0 |
> | 5. ランタイム | 0 |
> | 6. 課題の置き場 | 2 |
> | 7. その他 | 4 |
> | 計 | 15 |
>
> ## 重いもの上位 5 件
>
> 1. 並列の計画の「まとめた配布」が版を上げる配布だけを持つ（区分 3、止まる、#2616・#2554・#2623）
> 2. 課題の正本が GitHub Issues に固定で、`issues.primary` を読まない（区分 6、止まる、#2239 は一部だけ）
> 3. マージが `gh pr merge --admin` に固定（区分 7、止まる・必須レビューの迂回、#1338）
> 4. 開発ループの `loop sprint` が、版数を持たないリポジトリにも `--dev` の版を必須にする（区分 3、誤る、#2354・#2623）
> 5. コードレビューのエージェントが `git diff main...HEAD` で差分を取る（区分 2、誤る、課題無し）
>
>
> </details>
>
> <details><summary>他のリポジトリで起きた事例（worker の報告）</summary>
>
> # Value 6 の違反事例の収集（ai-plugins 以外のリポジトリで起きたもの）
>
> 調べた日: 2026-11-05〜06。調べたもの: devbasex/ai-plugins の課題 1048 件（open・closed の全件の本文。`gh issue list --state all --limit 3000`）と、各課題のコメントの配布の記録、`issues/old/genericity-survey-2026-09-27.md`（マイルストーン「23 汎用性の回復」の起点になった調査）、/work の下の clone の `.ndf/`。
>
> ## 読み方
>
> - **版**: コメントに「ndf--vX で配布」とあれば確定の値。`(推定)` は閉じた時刻の直後のタグ
> - **直し方**: `設定` = 宣言・引数で受ける形にした / `例外` = 起きた 1 か所だけを直した（同じ区分の別の箇所に同じ形が残りうる） / `見送り` = NOT_PLANNED で閉じた / `—` = 未着手
> - **⚠** = 例外で直したか見送ったもので、同じ区分に別の形の違反が残っている兆し
> - **★CD無し** = carmo-cdk のように、インフラのため**あえて CD を置かない**プロジェクトの事例（利用者の補足 2026-11-06）。CD を前提にした直し方の根拠には数えない。別の対処が要る事例として残す
>
> ## 1. 配布の手段（7 件）
>
> | 課題 | 起きたリポジトリ | 状態 | 直し方 | 残っている兆し |
> | --- | --- | --- | --- | --- |
> | #2616 並列の計画: plugin.json の無いリポジトリでは配布の版を決められず、計画全体が止まる | volareinc/carul（develop→検証、main→本番を CodePipeline が反映。`supervise.json` 無し） | OPEN（10.27.21 で観測） | — | ⚠ **#1336 の宣言（`project.json` の `delivery`）が並列の経路で読まれていない。** carul は `delivery` を 2 行（`kind: auto`・`versioned: false`）宣言済みなのに、`lib/parallel_external.py:196` が plugin.json を読む。1 スプリント・開発ループの経路では通る（本文）。#1336 の直しが経路ごとで、後から足した経路（並列の計画）に届かなかった |
> | #2623 デプロイとリリースは CD で解く（根の課題） | carul（#2616 の起点）・ai-plugins | OPEN | — | 「NDF が版を上げてまとめて出す工程」そのものが ai-plugins の形という指摘 |
> | #2620 「配布」を使わず「デプロイ」と「リリース」に分ける | ai-plugins（語の整理） | OPEN | — | 語の側の前提 |
> | #1336 配布の工程をプラグインの版数でないプロジェクトでも回し、マージが本番になるリポジトリでゲート 2 を掛ける | volareinc/carmo-system-console（main マージ＝本番の CodePipeline）・carmo-contractors-app | CLOSED（ndf--v10.17.42） | 設定（`project.json` の `delivery`、`new mission`/`new close` を版数なしで通す） | 上の #2616 で経路の取りこぼしが出た |
> | #1454 本番チャネルを「手動のデプロイの操作」として宣言できない | **★CD無し** volareinc/carmo-cdk（main マージは本番に届かず、`cdk deploy` を手で打つ） | CLOSED（ndf--v10.17.44） | 設定（`delivery` の `kind: manual`） | CD を置かない形の受け口は宣言にある。CD 前提へ改める #2623 で、この形を壊さないことが要る |
> | #1756 release: plugin_dir と changed-plugins が置き場を ndf・playwright-kit の名前で決め打ち | （静的に発見。package-plugin を使う他のリポジトリを想定） | OPEN | — | package-plugin の形の中にも ai-plugins の名前が残る |
> | #228 release に配備の完了を確かめる手立てが無く、ログの文字列を推測して待つ | car-pricing（コンテナイメージを ECR へ push） | CLOSED（v10.0.0 推定） | 例外寄り（release の手順に「外部の状態で確かめる」指針を書く） | ⚠ 配備の完了確認は手順の指針だけで、宣言の項目（どこを見れば完了か）にはなっていない |
>
> 参考（手元の宣言、`.ndf/project.json` の `delivery`）:
>
> | リポジトリ | delivery | supervise.json | plugin.json |
> | --- | --- | --- | --- |
> | carul | auto 2 行（develop→検証・main→本番、CodePipeline） | 無し | 無し |
> | carmo-screening | auto（main push で CodeBuild）＋ manual（`cdk deploy`） | 無し | 無し |
> | bi-tools | auto（main マージで GitHub Actions が配置） | 無し | 無し |
> | devbase | auto（main マージ、`versioned: true`）＋ Pages | 無し | 無し |
> | **★CD無し** carmo-cdk | manual（`cdk deploy`） | 無し | 無し |
> | patio-style | manual（ECR へ push し carmo-cdk で `cdk deploy`） | 無し | 無し |
> | novel | manual（投稿サイトへ手で） | 無し | 無し |
> | ai-plugins | （package-plugin） | **有り**（`release.plugin` など） | 有り |
>
> **`supervise.json` を持つのは ai-plugins だけである。** NDF のうち `supervise.json` の `release.plugin` を読む箇所（`release_lib/names.py`・`release-verification-steps.py`・`parallel_external.py`）は、他のどのリポジトリでも値が無い。
>
> ## 2. 起点のブランチ（7 件）
>
> | 課題 | 起きたリポジトリ | 状態 | 直し方 | 残っている兆し |
> | --- | --- | --- | --- | --- |
> | #202 起点ブランチが既定ブランチと異なるとき、worktree の起点と origin/main の取り込みが誤る | ai-plugins（develop へ移る時点。git-flow 一般の問題として） | CLOSED（v9.6.0 推定） | 設定（`worktree.json` の `base_branch`） | — |
> | #359 retrospective が `BASE=develop` と決め打ち | （#292 の棚卸しで発見。宣言の無いリポジトリ・main 起点で壊れる） | CLOSED（v10.2.0 推定） | 例外（その 1 Skill に解決の数行を写した。検査は「値の決め打ち」を見ない） | ⚠ 同じ決め打ちが #1088 で supervise.py に 5 か所見つかった |
> | #1088 supervise.py の雛形から ai-plugins の形（テスト・同期・起点・配布・置き場）を外す | （静的に発見。Value 6 の追加の契機） | CLOSED（ndf--v10.17.21） | 設定（宣言と引数） | 区分をまたぐ（テストのコマンド・起点・配布） |
> | #527 development-workflow が worktree.json の有無を見ず、宣言が無いまま進む | （宣言の無いリポジトリ一般） | CLOSED（v10.12.0 推定） | 設定（宣言の有無の確認） | car-pricing・ideabase の宣言は今も `version` だけ（`base_branch` 無し） |
> | #1338 マージと PR の運用を `--admin`・merge commit・develop/main の 2 本に縛らない | carmo-system-console（ruleset で必須レビュー、`qa/*` 宛ての PR が 38%）・carmo-contractors-app（`enforce_admins`、環境ブランチ宛て 55%） | OPEN | — | `--admin` 固定・PR を merge commit の件名で数える・環境ブランチを扱えない、が残る（commit status の CI を見ない #349 もここへ引き継がれた） |
> | #2082 既定ブランチ宛の実装 PR の閉じる語で、マージ時点に課題が閉じる | volareinc/carmo-screening（起点＝本番＝main） | OPEN | — | develop を挟む ai-plugins では起きない形 |
> | #2228 課題を閉じる時点を 1 つにする（#2082 の親） | carmo-screening ほか | OPEN | — | 同上 |
>
> ## 3. テストのコマンド（10 件）
>
> | 課題 | 起きたリポジトリ | 状態 | 直し方 | 残っている兆し |
> | --- | --- | --- | --- | --- |
> | #1334 テストの走らせ方を戦略の宣言で決め、長い全体テストでも cross-refactoring を回す | carmo-system-console（PHPUnit をコンテナ越し、直列 64 分） | CLOSED（ndf--v10.17.35） | 設定（`project.json` の `test.strategy`・`scope_command`） | — |
> | #1437 `--test-cmd` が静的解析のとき、全体テストが `{paths}` 空で常に落ちる | **★CD無し** carmo-cdk（shellcheck） | CLOSED（ndf--v10.17.48） | 設定（全体テストは宣言の `suites[].command`） | — |
> | #1434 範囲テストの雛形が shellcheck だと 30 分で 1 件しか改善できない | **★CD無し** carmo-cdk | CLOSED（見送り。「大半は #1483 で直った」） | 見送り | ⚠ 「テストの土台が無い範囲」の扱いは未着手のまま |
> | #1459 範囲テストが起動できないまま適用と取り消しを約 380 回、770 コミット | **★CD無し** carmo-cdk（サブシェルの command） | CLOSED（ndf--v10.17.46） | 設定（宣言の解釈を test-run.py と揃える、起動失敗の区別） | — |
> | #1630 範囲テストが unittest の `discover -s {paths}` にファイルを渡して落ち続ける | takemi-ohama/novel（unittest） | CLOSED（v10.23.0 推定） | 設定（宣言の契約の明記と検査） | — |
> | #1337 NDF の worktree でプロジェクトの依存物とコンテナのテスト環境を使えるようにする | carmo-system-console（`vendor/` 無しで Pint が落ちる、`--no-verify` 6 件） | CLOSED（ndf--v10.17.38） | 設定（`worktree.json` の `deps`・`testenv`。carul も使う） | — |
> | #1388 コンテナの外から打つ pre-commit を worktree へ届ける | carmo-system-console | CLOSED（見送り。「実走の観測が無い」） | 見送り | ⚠ 調査（genericity-survey）は `--no-verify` の記録を挙げていた |
> | #2449 cross-refactoring の試走が `test.suites[].paths` 配下の JS を phpunit へ渡す | carmo-system-console（PHPUnit＋tests/e2e の JS。宣言の command も DooD で動かず差し替え） | OPEN（10.27.14 で観測） | — | runner が扱うファイルの種類で絞らない |
> | #2567 テストのステップが作業場所のテスト用コンテナが止まっているのを確かめずに走る | volareinc/carul（DooD、compose の api） | OPEN | — | `worktree-testenv.sh up` を前提にしない経路がある。detached HEAD で up が断る |
> | #2516 名前が `test_` で始まる本番のモジュールをテストとみなす | ai-plugins 自身（参考。判定規則は #1339 と同根） | OPEN | — | 下の「言語」の判定規則と同じ |
>
> ## 4. 言語（5 件）
>
> | 課題 | 起きたリポジトリ | 状態 | 直し方 | 残っている兆し |
> | --- | --- | --- | --- | --- |
> | #1339 テストのパスと変更の分類を PHP・TypeScript・Vue の慣習でも正しく判定する | carmo-system-console ほか（survey の G6、`/tmp/ndfprobe` で実測） | OPEN | — | `latest_price.py`・`contest_rules.ts` をテスト扱い、`UserServiceTest.php` を取りこぼす |
> | #1777 cross-refactoring の assess が dbt の `.sql` を本番コードに数えない | bi-tools（dbt） | OPEN | — | `CODE_EXTENSIONS` が言語の一覧を固定で持つ |
> | #2449（再掲）phpunit に JS を渡す | carmo-system-console | OPEN | — | 同上 |
> | #818 Python / PHP / TypeScript の言語サーバを使えるようにする | carmo-predict-contract・carmo-system-serverside | CLOSED（10.17.18 までに配布） | 機能の追加 | — |
> | #1568 ML・データの課題を development-workflow で通す | volareinc/carmo-screening | OPEN | — | 言語ではなく領域の違い（参考） |
>
> ## 5. ランタイム（実行環境）（5 件）
>
> | 課題 | 起きたリポジトリ | 状態 | 直し方 | 残っている兆し |
> | --- | --- | --- | --- | --- |
> | #1455 project-mvv.py propose / vet が psutil の無い python3 で止まる | **★CD無し** carmo-cdk（`/usr/bin/python3` 3.14、psutil 無し） | CLOSED（ndf--v10.27.1） | 設定寄り（`deps.require` と、経路をたどる構造チェック） | — |
> | #1297 待ちの雛形が `timeout` を使い macOS で exit 127 | devbasex/devbase（macOS） | CLOSED（ndf--v10.17.48） | 例外（雛形 2 つから外部コマンドを外す） | ⚠ 他の雛形・スクリプトの GNU 前提は未棚卸し |
> | #2509 `UV_PROJECT_ENVIRONMENT` が子プロセスへ漏れ、利用者の `uv run` が NDF の共有の環境を作り直す | volareinc/carul（利用者も uv を使う） | OPEN | — | NDF の環境変数が利用者のコマンドへ漏れる |
> | #2447 ndf の依存の仮想環境が壊れていると cross-review が違う理由で止まる | carmo-screening | OPEN | — | 参考（NDF 自身の環境） |
> | #1543 従量の接続へ切り替えた claude が settings.json の env に負ける | 利用者の環境 | CLOSED（ndf--v10.17.51） | — | 参考（リポジトリではなく利用者の設定） |
>
> ## 6. 課題の置き場（7 件）
>
> | 課題 | 起きたリポジトリ | 状態 | 直し方 | 残っている兆し |
> | --- | --- | --- | --- | --- |
> | #479 課題追跡を GitHub Issues 以外（Redmine）へ向けられるようにする（子 #480〜#484） | carmo-system-console（Redmine が正本）・carmo-kun・carmo-cdk・carmo-contractors-app | CLOSED（見送り 2026-10-01。「止まった観測が本文に無い」） | 見送り | ⚠ 宣言の側では carmo-cdk・carmo-screening が `issues.others` に redmine、carmo-screening・nyle-dx は `primary: markdown` を持つ。宣言はあるが NDF の操作は GitHub 前提のまま |
> | #2239 out-of-scope: 起票先の流儀（課題追跡の宣言・テンプレート）に従って起票する | （#2216 の設計で利用者が指示） | OPEN | — | 相手先の `issues.primary` を読まず常に GitHub へ起票 |
> | #2327 別のリポジトリの課題が振り返りの記録を満たせず開いたまま残る | （スプリント m26-2 の検査で発見） | OPEN | — | — |
> | #1847 issue-file note が別のリポジトリの既存課題へ同意なしにコメントを公開する | （上流＝ai-plugins と開発対象が別のとき） | CLOSED（v10.17.69 推定） | 例外（そこで人へ 1 回止める） | ⚠ #2239・#2327 と同じ「別のリポジトリ」の扱いの一部だけ |
> | #229 out-of-scope と retrospective に起票先の判断が無い | car-pricing | CLOSED（v10.0.0 推定） | 手順の追加（起票先の判断） | — |
> | #242 振り返りの記録が専用の PR を要し、開発対象には置き場所も無い | car-pricing ほか | CLOSED（v10.0.0 推定） | 置き場の変更（起点の課題へのコメント） | 起点の課題が GitHub にある前提（Redmine・markdown では置けない） |
> | #2082（再掲）閉じる語で課題が先に閉じる | carmo-screening | OPEN | — | GitHub の自動クローズが前提 |
>
> ## 7. その他（Value 6 の 6 区分の外。ai-plugins の形を前提にしていたもの）（9 件）
>
> | 課題 | 区分の中身 | 起きたリポジトリ | 状態 | 直し方 | 残っている兆し |
> | --- | --- | --- | --- | --- | --- |
> | #1435 設計プランの用語集のステップが `.gitignore` 対象の `.ndf/` へコミットしようとして止まる | `.ndf/` の追跡 | **★CD無し** carmo-cdk（`.ndf/` を追跡しない） | CLOSED（ndf--v10.17.55） | 例外（そのステップだけ飛ばす） | ⚠ 同じ形が #2157 で再発 |
> | #2157 `.ndf/` を追跡するかが Skill の間で食い違う | `.ndf/` の追跡 | volareinc 系・carmo-system-console・novel | CLOSED（v10.27.3 推定） | 共通層（`lib/ndf_tracking.py`） | carmo-cdk は今も `.ndf/` を ignore |
> | #1655 cross-refactoring の単独起動を直す（ai-plugins 以外で打つ場所） | Skill の置き場＝対象リポジトリの前提 | （ai-plugins 以外一般） | CLOSED（ndf--v10.17.59） | 例外（cross-refactoring だけ） | ⚠ **#1707 で cross-review に同じ形が残っている（OPEN）** |
> | #1707 cross-review を SKILL.md のとおり単独起動すると Skill のディレクトリが git でなく止まる | 同上 | （ai-plugins 以外一般。ai-plugins では偶然動く） | OPEN | — | 同上 |
> | #2446 マージを判定するフックが `--repo` を見ず作業ディレクトリのリポジトリで PR を引く | 複数リポジトリの作業 | carul のセッションから carmo-cdk の PR をマージ | OPEN | — | 作業ディレクトリ＝対象リポジトリの前提 |
> | #1593 CI の無いリポジトリで検査のプランが毎回止まる | CI の有無 | takemi-ohama/novel | CLOSED（v10.23.0 推定） | 設定（`ci.provider: none` を読む） | — |
> | #349 commit status にしか CI を書かないリポジトリで失敗を拾えない | CI の種類 | （ideabase ほか想定） | CLOSED（見送り） | 見送り | ⚠ #1338 の本文に「cross-review が commit status を見ない」として残る |
> | #1436 Serena MCP が書き換える `.serena/*.yml` を未 push の変更とみなす | 利用者の Tool のファイル | carmo-cdk・volareinc 系 | CLOSED（ndf--v10.17.46） | 例外（その種類のファイル） | ⚠ #2448 で別の Tool（kiro の `.amazonq/`）に同じ形 |
> | #2448 cross 系で kiro を起動すると作業ツリーの `.amazonq/cli-agents/*.json` を書き換える | 利用者のリポジトリの設定ファイル | carmo-screening・carmo-system-console | OPEN | — | 同上 |
>
> ## V6 と関係の無い事例（carul などで起きたが、ai-plugins の形の前提ではない）
>
> 数に入れていない。carul: #2568・#2569・#2583・#2584・#2585・#2617（並列の計画とループの不具合）、#2519（設計の手順）。volareinc 系ほか: #13・#21・#584・#585・#822（すでに直っていた）・#1447・#1496・#2084・#2229。
>
> ## まとめ
>
> | 区分 | 件数 | OPEN | ⚠ |
> | --- | --- | --- | --- |
> | 配布の手段 | 7 | 4（#2616・#2623・#2620・#1756） | 2（#2616・#228） |
> | 起点のブランチ | 7 | 3（#1338・#2082・#2228） | 1（#359） |
> | テストのコマンド | 10 | 3（#2449・#2567・#2516） | 2（#1434・#1388） |
> | 言語 | 5（再掲 1） | 4（#1339・#1777・#2449・#1568） | 0 |
> | ランタイム | 5 | 2（#2509・#2447） | 1（#1297） |
> | 課題の置き場 | 7（再掲 1） | 3（#2239・#2327・#2082） | 2（#479・#1847） |
> | その他 | 9 | 3（#1707・#2446・#2448） | 4（#1435・#1655・#349・#1436） |
>
> **読み取れること**
>
> 1. **宣言の受け口は作ってあるが、後から足した経路が読まない。** #1336 で `delivery` の宣言を作り、carul は正しく宣言しているのに、並列の計画（後から足した経路）は `supervise.json` の `release.plugin` → plugin.json を読む（#2616）。`supervise.json` を持つのは ai-plugins だけである
> 2. **例外で直したものは、同じ区分の別の箇所で再発している。** #359 → #1088（develop の決め打ち）、#1435 → #2157（`.ndf/` の追跡）、#1655 → #1707（単独起動の打つ場所）、#1436 → #2448（利用者の Tool のファイル）。どれも 1 か所を直した後、別の Skill・スクリプトに同じ形が見つかった
> 3. **「観測が無い」で見送ったもののうち 2 件は、宣言や調査に反証がある。** #479（Redmine）は carmo-system-console が正本に使い、carmo-cdk・carmo-screening も宣言に書く。#1388（pre-commit）は調査が `--no-verify` 6 件を記録していた
> 4. **★CD無し（carmo-cdk）の事例は 6 件**（#1454・#1437・#1434・#1459・#1455・#1435）。配布に関わるのは #1454 だけで、`delivery.kind: manual` で受けている。CD を前提に改める #2623 では、この形（と patio-style・novel の manual）を壊さない受け口が別に要る
>
>
> </details>
>
>
> ## carul から届いた課題の束 D（2026-11-06）
>
> carul の課題を根本原因で束ねた結果、束 D（Value 6）はこの課題の範囲と重なる。束 A〜C は #2647・#2648・#2649。
>
> ### 束 D（軸の外、Value 6）: NDF の経路がプロジェクトの宣言・環境でなく ai-plugins の前提を使う
>
> **根本原因。** いくつかの経路が `.ndf/project.json` の宣言（`delivery`・`test.suites[].container`）を読まず、ai-plugins の前提
> （`plugin.json` の版・作業ディレクトリのリポジトリ・ブランチ名・NDF 自身の venv）で動く。NDF が自分のために設定した環境変数も、
> 利用者のコマンドへそのまま漏れる。
>
> **場所。**
> - #2616・#2554: `plugins/ndf/scripts/lib/parallel_run.py:86-100`（`dev_version` は `release.plugin` と `plugin.json` だけ）・`:219-221`・`:418-424`（`open_release` が版を決められないと `self.stop = True` で計画全体を止める）。`parallel_run.py`・`parallel_release.py`・`parallel_external.py` は `delivery` を 1 回も読まない（grep で 0 件）。1 スプリントの経路は `supervise_lib/sprint_routes.py:7-14` で `delivery` を読むので、carul（`delivery` が `kind: auto`）は昇格できる
> - #2509（受け入れ条件 5〜7）: `plugins/ndf/scripts/lib/deps.py:177`（起動し直すときに `UV_PROJECT_ENVIRONMENT` を共有の環境へ向け、外さないまま子へ渡る）
> - #2567: `plugins/ndf/scripts/lib/container_reach.py:92-143`・`lib/test_triage.py:292-301`（宣言のコンテナを確かめるだけで起こさず、人に `worktree-testenv.sh up` を打たせる）・`plugins/ndf/scripts/worktree-testenv.sh:166-168`（テスト環境をブランチ名で引くため detached HEAD で断る）
> - #2446: `plugins/ndf/skills/development-workflow/scripts/lib/workflow-merge.sh` の `wf_check_merge`（`--repo` を見ず作業ディレクトリの origin で PR を引く。本文の記述による。コードは読んでいない）
>
> **束に入る課題。** carul: #2616・#2567・#2509・#2446。同じ根: #2554。棚卸し: #2624（27 件の表の 1 行目が #2616・#2554）・#2623。
>
> **直せば消える理由。** 「配布の方式・テスト環境・対象のリポジトリは宣言から、NDF の環境は NDF の中だけで」という 1 つの規則を、
> 上の 4 か所に当てる。#2616 は `delivery` が `kind: auto` なら配布を組まず `released` と答え、#2567 はテストのステップの前に宣言のコンテナを起こす。
> #2509 は `deps.require` が起動し直した後に、NDF が付けた値だけを外す。
>
> **直しても残るもの。** #2616 の「配布を組めないと計画全体を止める」（`parallel_run.py:422-424`）は宣言の読み忘れとは別の欠けで、
> 1 行の直しで依頼への `failed` の答えにできる。#2624 の残り 20 件余りは別に要る。
>
> **既存の柱との重なり。** #2624・#2623（Value 6 の棚卸しと CD の前提。どちらもマイルストーンに入っていない）。#2509・#2616・#2567 はマイルストーン 30 にある。
>
> ---
