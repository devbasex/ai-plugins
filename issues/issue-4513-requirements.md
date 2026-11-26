## 何が起きたか

バージョン番号を持たないサービスのリポジトリで、並列の計画のレーンがスプリントの工程「デプロイ」を終えられなくなった（NDF 11.0.36）。

1. オーガナイザーがまとめたリリースを出し、`ndf lanes release --result released --version "main <短いハッシュ>" --release <PR番号>` で記録した（`<PR番号>` は本番のリリース記録を置いた Pull Request）
2. レーンのリリース承認ゲートは回答 `approved` で通り、工程の文脈に `product_version: "main <短いハッシュ>"` が書かれた
3. 続くステップで、デプロイを担当する worker が「semver の形の値を返せない。作り物の番号は書かない」と判断を求めて止まった
4. conductor が `approved` と回答すると、工程は `bad_report`（結果の形が違う）で失敗した
5. retry すると 3 に戻り、同じことが繰り返された

同じまとめたリリースの 3 レーンのうち、再現したのは 1 レーンである。ほかの 2 レーンが通った理由は未確認。急ぎの対応が指示されている（他のプロジェクトの進行を妨げている）。

## 種類

不具合（出したものを指す値でデプロイの工程を終えられるという、今の手順書と工程の文脈の定義が認めている振る舞いが成り立たない）

## 今の姿

デプロイの工程は、出したバージョンを `product_version` として後の工程（リリース後テストなど）へ渡す。期待する結果の根拠は次の 2 つである。

- [release の手順書](https://github.com/devbasex/ai-plugins/blob/main/plugins/ndf/skills/release/SKILL.md) は「版数を持たないリリースもある」とし、出ているものを一意に指せる値（リビジョン・ビルド番号）を記録する
- [工程の文脈の定義](https://github.com/devbasex/ai-plugins/blob/main/plugins/ndf/contracts/workflow/context.schema.json) の `product_version` は、空でない文字列なら受け付ける

一方、今のコードは次のように動く（コミット `ea401b56a` で確認）。

| 箇所 | 今の動き |
| --- | --- |
| [結果 JSON の定義](https://github.com/devbasex/ai-plugins/blob/main/plugins/ndf/contracts/schemas/result.schema.json) | `outputs.product_version` は `1.2.3` の形（semver）しか受け付けない。工程の文脈の定義と食い違う |
| デプロイを担当する worker への入力 | 承認の対象の Pull Request だけを受け取り、工程の文脈に書かれた `product_version` は受け取らない（[作業の定義](https://github.com/devbasex/ai-plugins/blob/main/plugins/ndf/contracts/worker/kinds.json)） |
| worker が判断を求めた後の `approved` | 配布を担当する AI（worker）は、デプロイの工程の最後のステップ `release` で動く。worker が `status: gate` の結果を返して判断を求め、それに `approved` と回答すると、続くステップが無いので、その時点でステップの出力を検査する。worker の結果の `outputs` はこの出力に引き継がれないので、`product_version` が無く `bad_report` で失敗する |

## 事象

conductor が worker の判断の求めに `approved` と回答すると、次のエラーで工程が失敗する。

```text
ステップ release の出力が規約に合わない: (最上位): 'outputs' is a required property
```

このエラーには、何の値が無いのかも、worker が何を判断してほしかったのかも書かれていない。retry すると同じ判断の求めに戻る。

バージョンの形でない値を返そうとしても、結果を書く段階で断られる。

```console
$ ndf contracts emit --tool worker --status ok --summary x --outputs '{"product_version":"main abc1234","delivery":{}}' --out <ファイル>
結果の形の誤り: properties/outputs/properties/product_version/pattern（pattern）
（終了コード 2）
```

## 期待する結果

- バージョン番号を持たないリポジトリでは、出したものを指す値（例: `main abc1234`）を `product_version` にして、デプロイの工程が終わる
- 出力がそろわないまま判断の求めに `approved` と回答した場合は、何が足りず、なぜ止まったかが示される

## 再現手順

前の状態: `.ndf/project.json` のブランチ戦略が次の値のリポジトリ。`release_strategy: trunk`、`approval.target: production_pr`、`approval.via: gate`、`release.version_store.kind: none`、`release.deliveries[0].kind: manual`、`release.deliveries[0].versioned: false`（キーは `branch_strategy` の下の位置）。

1. 並列の計画のレーンでスプリントを進め、リリース承認ゲートで待たせる
2. オーガナイザーが、本番のリリース記録を置いた Pull Request の番号を `--release <PR番号>` に指定して、`ndf lanes release <計画> <リクエスト> --result released --version "main <短いハッシュ>" --release <PR番号> --reason <理由>` で、まとめたリリースの結果を記録する
3. レーンでデプロイの工程を進める。worker が判断を求めて止まる
4. 判断の求めに `approved` と回答する。工程が `bad_report` で失敗する
5. retry する。3 に戻る

3 で worker がどう判断するかは LLM の回答なので、毎回同じとは限らない。バージョンの形でない値が断られることと、`outputs` の無い結果で上のエラーになることは、コードの定義だけで毎回再現する。

## 環境

| 項目 | 値 |
| --- | --- |
| NDF | 11.0.36 で発生。`main` の `ea401b56a` でも定義が同じ |
| 対象のリポジトリ | バージョン番号を持たないサービス（上の再現手順の設定） |
| ランタイム・OS | 未確認 |

## なぜ困るか

- バージョン番号を持たないサービス（Web サービスなど）は、デプロイの工程を自動で終えられず、後の工程へ進めない
- 抜け出すには、`ndf progress finish <ID> デプロイ --output <出力の JSON>` で工程を手で終えるしかない
- 停止の理由が示されないため、retry を繰り返して時間と LLM の費用を使う
- NDF はどのリリースの形のリポジトリでも同じ工程で使えることを目指しており、代表的な種類の Web サービスで工程が止まる

## 目的

- バージョン番号を持たないリポジトリでも、手作業の回避なしにデプロイの工程が終わり、後の工程へ進める
- デプロイの出力がそろわないときに、利用者が原因と次の操作を判断できる

## 対象範囲

含む:

- バージョン番号を持たないリポジトリ（`release.version_store.kind: none`）で、デプロイの工程が出力をそろえて終わること
- 並列の計画のまとめたリリースで記録した値と、スプリントを 1 つだけ進めて出す場合の値の両方
- その値を受け取る後の工程が止まらないこと
- worker の判断の求めに `approved` と回答しても出力がそろわないときの、止まり方と理由の表示

含まない:

- バージョン番号を持つリポジトリの番号の付け方と、番号を上げる手順の変更
- 再現しなかった 2 レーンが通った理由の調査
- 手で工程を終える手順（`ndf progress finish`）の変更
- 承認したレーンの Pull Request の変更が、オーガナイザーが出した配布物に含まれることを本番のリリース記録で確かめること、手動の配布で記録を置く手段、その確かめで止まったときの復旧（#4586）

## 前提

- 前提 1: 「バージョン番号を持たないリポジトリ」は、ブランチ戦略の設定が `release.version_store.kind: none` のリポジトリを指す
- 前提 2: 前提 1 のリポジトリでは、出したものを指す値は空でない文字列で、形は問わない。空白だけの文字列は空と同じ扱いにする。まとめたリリースでは、オーガナイザーが `--version` で渡した値がそれに当たる

## 受け入れ条件

前提 1 のリポジトリ:

- [ ] 1. 前提: まとめたリリースを `--version "main abc1234"` で記録し、レーンのリリース承認ゲートが `approved` で通っている
      操作: デプロイの工程を進める
      結果: 判断の求めで止まらずに工程が終わり、工程の文脈の `product_version` が `main abc1234` のままである
- [ ] 2. 条件 1 の流れで、デプロイの工程はオーガナイザーが出したものをもう一度出さない
- [ ] 3. 前提: まとめたリリースを使わず、スプリントを 1 つだけ進めて出す
      操作: デプロイの工程で、出したものを指す値（例: コミットの短いハッシュ）を返す
      結果: 結果 JSON が形の誤りにならず、工程が終わり、その値が `product_version` になる
- [ ] 4. `product_version` がバージョン番号の形でないスプリントで、デプロイより後の工程（リリース後テスト・体裁レビュー・振り返り・課題を閉じる処理）が、値の形を理由に止まらない
- [ ] 5. `product_version` が空文字列か空白だけの結果は、受け付けられない

出力がそろわないとき（リポジトリの種類を問わない）:

- [ ] 6. 前提: デプロイの worker が `product_version` を返さずに判断を求めている
      操作: `approved` と回答する
      結果: 工程は失敗で止まる。示される理由に、足りない値の名前（`product_version`）と、worker が判断を求めた理由の要約が含まれる
- [ ] 7. 条件 6 の後、conductor の操作なしに同じ判断の求めが出し直されない（conductor が自分で打つ retry は禁じない。起票者の推定 S4）

退行しないこと:

- [ ] 8. バージョン番号を持つリポジトリでは、デプロイの worker がバージョン番号の形でない値（例: `ndf v11.0.0`）を返しても、今と同じく工程は完了にならない
- [ ] 9. 前提: リリース承認ゲートより前のステップが MVV 判定の結果（`mvv_gate`）を記録している。そのうえで、配布を担当する AI が配布先（`delivery`）と配布物の識別子（`product_version`）をそろえた結果を返して判断を求めている
      操作: 続行を承認する（`approved` と回答する）
      結果: デプロイの工程が完了する

承認したレーンの Pull Request の変更が配布物に含まれることの確かめ（本番リリース記録の照会）・手動の配布で記録を置く手段・確かめで止まったときの復旧は、この課題から外し、#4586（ https://github.com/devbasex/ai-plugins/issues/4586 ）で扱う。

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。結果 JSON の `outputs.product_version` が、バージョン番号を持たないリポジトリではバージョン番号の形でない値を受け付ける。今受け付けている値はそのまま通る |
| 重さ（直すまでの被害） | 直すまでは、前提 1 のリポジトリの利用者は、デプロイの工程が自動で終わらない。回避の手段は、`ndf progress finish` で工程を手で終えること |
| データ | スキーマの移行なし |
| 既存の振る舞い | デプロイの工程の出力の確かめと、失敗の理由の表示 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 本番のリリース記録 | 本番へ出した値と、含めた Pull Request の一覧を、NDF が読める形で書いた Pull Request のコメント |
| まとめたリリース | オーガナイザーが複数のレーンの Pull Request を 1 つのバージョンとして出すリリース |
| レーン | 並列の計画の下で進める開発の流れの 1 本 |
| conductor | 開発の流れを進める AI の会話。承認や判断の求めに回答する |
| オーガナイザー | 並列の計画の全体をまとめる conductor の役割 |
| 承認ゲート | 人手の承認を求める点 |
| 工程・ステップ | デプロイの工程は、`release-mvv`・`release-reading`・`release-gate`・`release` のステップを順に流す。エラーに出る「ステップ release」は、この最後のステップで、worker が配布をする。オーガナイザーの「リリース」とは別のものである |
| 判断の求め | worker が結果に `status: gate` を書き、conductor の判断を待って止まること |
| MVV 判定（`mvv_gate`） | 承認ゲートの前に、プロジェクトの判断基準（MVV）に照らして人の承認を省けるかを AI が判定した結果。ステップ `release-mvv` が記録する |
| `<計画>`・`<リクエスト>` | `ndf lanes release` の引数。`<計画>` は並列の計画の名前（例: `p9`）、`<リクエスト>` はまとめたリリースに含めるレーンのリリースの依頼（1 つ以上） |

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 出したものを指す値が工程の文脈に書かれた | リリース承認ゲートへのまとめたリリースの回答 | 値の無い回答では書かれない。デプロイの工程が値を返す（条件 3） | — |
| E2 | デプロイの出力がそろった | まとめたリリースの記録済みの値を使う（worker を起動しない）か、起動した worker が出力を返す | worker が判断を求めて止まるか、結果の形の誤りになる | E1（まとめたリリースのとき） |
| E3 | 判断の求めに回答した | conductor の回答 | 出力がそろわなければ理由を示して失敗する（条件 6） | worker が起動され、判断を求めている |
| E4 | デプロイの工程が終わった | 出力の確かめの合格 | `bad_report` で失敗する | E2 |
| E5 | 後の工程が `product_version` を読んだ | 後の工程の開始 | 値の形で止まらない（条件 4） | E4 |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | 前提 1 の設定を使う作り物のリポジトリで、条件 1〜9 の範囲テストを書く（`uv run --frozen --project . --all-extras pytest <対象> -q`） |
| 静的解析・型検査 | `ndf check` と CI |
| 手動確認 | 問題が起きたリポジトリと同じ設定で、デプロイの工程が手作業なしに終わることを利用者が確かめる。リリース後テストで行う |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| コーディング規約・テスト戦略 | [AGENTS.md](https://github.com/devbasex/ai-plugins/blob/main/AGENTS.md)。安定版の経路で直す（結果 JSON の形と工程の振る舞いの変更に当たる） |
| 公開の制約 | テストと例には作り物の値だけを使う |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| バージョン番号の有無による値の確かめを、どこで行うか。結果 JSON の定義はリポジトリの設定を読めない | 設計の担当 | 設計承認ゲート |
| 後の工程のほかに、バージョン番号の形を求める読み手があるか（プランが前のリリースの結果を参照する仕組みは、今は番号の形を求める） | 設計の担当 | 設計承認ゲート |
| 条件 6・7 をデプロイ以外の工程にも当てはまる共通の層で直すか | 設計の担当 | 設計承認ゲート |

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | まとめたリリースで記録した値を、デプロイの工程がそのまま出力にして終わる | 本文の「期待」 | 受け入れ条件 1・2 |
| S2 | バージョン番号を持つリポジトリの値の確かめは緩めない | 本文には無い。今の振る舞いを守るために足した | 受け入れ条件 8 |
| S3 | まとめたリリースを使わない場合も、出したものを指す値で終われる | 本文には無い。release の手順書の「版数を持たないリリース」から足した | 受け入れ条件 3 |
| S4 | 「同じゲートを繰り返さずに止まる」は、理由を示して失敗で止まり、自動で出し直さないことを指す。conductor が自分で打つ retry は禁じない | 本文の「期待」の 2 つ目を解釈した | 受け入れ条件 6・7 |
| S5 | 値の形は問わず、空（空白だけを含む）でなければよい | 本文の「`minLength: 1` の文字列で、semver を求めていない」 | 前提 2、受け入れ条件 5 |

## リクエスト（原文）

<details>
<summary>起票時の本文（書き手: 起票者、日付: 2026-11-25 起票。末尾の「着手の指示」のセクションは本文の記載で 2026-11-27）</summary>

````text
## 何が起きたか

NDF 11.0.36。並列の計画のレーンで、まとめたリリース（オーガナイザーが `ndf lanes release --result released --version "main <短いハッシュ>"` で記録）の後に、スプリントの工程「デプロイ」が終わらなかった。

1. ステップ `release-gate` はオーガナイザーの回答 `approved` で通り、文脈に `product_version: "main <ハッシュ>"`・`release_pr` が書かれた
2. ステップ `release`（プラン `release`）の worker が `status: gate` を返して止まった。worker の summary は「版数を持たないサービスのため product_version を semver の形で返せず、作り物の版数を書かずに判断を求める」。`outputs` には `delivery` だけを載せ、`product_version` を空けた
3. conductor がこのゲートに `approved` を答えると、`ステップ release の出力が規約に合わない: (最上位): 'outputs' is a required property`（`bad_report`）で工程が失敗した
4. retry すると、また 2 のゲートで止まる。これを繰り返した

## 期待

- `contracts/workflow/context.schema.json` の `product_version` は `minLength: 1` の文字列で、semver を求めていない。`branch_strategy.release.version_store.kind: none` のリポジトリでは、まとめたリリースの `--version` の値（文脈の `product_version`）を、release のステップがそのまま outputs に返して工程を終えられること
- ゲートへ `approved` を答えた後の出力が `release-result` の規約（`outputs.product_version` が要る）を満たさないなら、少なくとも同じゲートを繰り返さずに止まって理由を示すこと

## 回避

`ndf progress finish <ID> デプロイ --output <steps: [] / summary / outputs.product_version・release_pr の JSON>` で工程を手で終えた。

## 環境

- `.ndf/project.json`: `release_strategy: trunk`、`approval.target: production_pr`、`via: gate`、`version_store.kind: none`、`deliveries[0].kind: manual`、`versioned: false`
- 同じまとめたリリースの 3 レーンのうち、1 レーンで再現した（ほかの 2 レーンは通った）

## 着手の指示（2026-11-27）

利用者が急ぎの対応を指示した（他のプロジェクトの進行を妨げている）。並列の計画 p9 のレーン lrelfix で直す。
````

</details>

