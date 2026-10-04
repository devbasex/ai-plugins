# #1654: cross-refactoring: 依存を入れられないときも assess が終了コード 3 を返し、リファクタリングが黙って飛ばされる

正は課題の本文（#1654）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

課題の起票時の本文（2026-10-03、#725 の点検で見つけたもの）を、要約せずに写す。

> ## 何を見つけたか
>
> `refactor.py assess` が外部パッケージ（md・mdtable）を入れられないときも終了コード 3 で終わる。3 は SKILL.md が「リファクタリングを飛ばしてよい（起動しない）」と定める値と同じで、supervise のプランも 3 を飛ばす合図に読む。このため、依存が欠けた環境ではリファクタリングが失敗として現れず、黙って飛ばされる。
>
> 実行で確かめた（origin/develop 7c9deb77）:
>
> ```
> NDF_DEPS_REEXEC=<script> /usr/bin/python3 plugins/ndf/skills/cross-refactoring/scripts/refactor.py assess --base origin/main
> ❌ [ndf deps] …
> exit=3
> ```
>
> あわせて、SKILL.md:31 は `scripts/refactor.py` を「uv 自己完結、標準ライブラリのみ」と書くが、実装は import の時点で外部パッケージを要求する。
>
> ## どこで見つけたか
>
> - `plugins/ndf/skills/cross-refactoring/SKILL.md` 130-136（「終了コード 3 なら起動しない」と assess の打ち方）、31（「標準ライブラリのみ」）
> - `plugins/ndf/skills/cross-refactoring/scripts/refactor.py:37` の `deps.require("md", "mdtable")`（import の時点で呼ぶ）
> - `plugins/ndf/scripts/lib/deps.py:61` の `EXIT_PRECONDITION = 3`、69 の `sys.exit(EXIT_PRECONDITION)`
> - `plugins/ndf/scripts/supervise_lib/steps.py:109`（`skip_code` の既定が 3）、`plugins/ndf/scripts/supervise_lib/templates.py:179-185`（assess の `skip_to: review`）
>
> ## なぜこの変更の範囲外なのか
>
> スプリント m725（#725・#1289・#842・#824）の受け入れ条件に含まれない。 直すには依存の欠けの終了コードか assess の「飛ばしてよい」の値のどちらかを変える必要があり、deps.py の全利用者・SKILL.md・supervise のプランが共有する終了コードの約束事に触れるため、即時修正の 4 条件のうち「既存の約束事を壊さない」を満たさない。根拠: Value 6（根本原因の場所で直す。終了コードの持ち手は共通の層の deps.py にあり、そこで設計してから直す）。
>
> ## 修正レイヤー
>
> - 現象レイヤー: cross-refactoring の `refactor.py assess`（と supervise の検査のプランの assess ステップ）
> - 修正レイヤー: 依存の欠けの終了コード（`plugins/ndf/scripts/lib/deps.py` の `EXIT_PRECONDITION = 3`）と、supervise の `skip_code` の既定（`plugins/ndf/scripts/supervise_lib/steps.py:109`）が同じ値を使う終了コードの約束
> - 同じ値の衝突は assess のほかに、`skip_to` を持つ finish ステップ（`supervise_lib/templates.py:381`。`check.py finish` → `record`）にも及ぶ。`deps.require` を呼ぶファイルは `plugins/ndf` の scripts と skills で 61 ある
> - 修正方針: 分離（前提の欠けと「飛ばしてよい」を別の終了コードにする）
> - 個別に残る作業: SKILL.md:31 の「標準ライブラリのみ」の訂正
>
> ## 直さないと何が起きるか
>
> 依存の入っていない環境（素の python3 で起動したとき、venv が壊れたとき）では、プロダクションコードの差分があってもリファクタリングが毎回飛ばされ、誰も気づかないまま工程が review へ進む。SKILL.md の「標準ライブラリのみ」を信じた利用者は依存を入れる理由を持たない。
>
> ## 由来
>
> issue #725（スプリント m725。cross-refactoring の SKILL.md の点検、2026-10-03 利用者の依頼）
>

依頼（2026-10-03、conductor の起動指示）: 「人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。」

## 目的

- 依存（外部パッケージ）が欠けて手順のスクリプトが動けなかったことと、手順のスクリプトが「飛ばしてよい」と判定したことを、終了コードだけで区別できるようにする
- supervise のプランが依存の欠けを「飛ばしてよい」と読まず、止まった（失敗）として工程に現れるようにする
- cross-refactoring の SKILL.md が `scripts/refactor.py` の依存を実装どおりに書く

## 前提

- 前提 1: 直す場所は、依存の欠けの終了コードを持つ共通の層（`plugins/ndf/scripts/lib/deps.py`）と、終了コードの表の正本（`plugins/ndf/scripts/lib/README.md` と `step_result.py` の定数）である。`refactor.py assess` の「飛ばしてよい」の値（3）と supervise の `skip_code` の既定（3）は変えない。理由: 3 を「飛ばしてよい」と読む側は assess のほか `check-trigger.py eval` / `changed` / `finish`、計画の `実行の条件`、`procedures.py` の `_skip_condition` の 5 か所にあり、3 を動かすと読む側すべてが変わる。依存の欠けを出すのは `deps.py` の 1 か所（`_stop`）だけである（Value 6）
- 前提 2: 依存の欠けに当てる新しい終了コードの値は設計が決める。値は、共通の契約で既に意味を持つ値（0・1・2・3・10〜19・20〜29）と、`deps.require` を呼ぶ既存のエントリポイントが自分で使っている値（例: `refactor.py` の取り込みの失敗の 4）のどちらとも重ならないものにする
- 前提 3: 依存の欠けは、共通の契約の `status` では `stopped` に当たる（`code_matches("stopped", <新しい値>)` が真）
- 前提 4: `deps.require` が終了コード 3 を返すことに頼っている呼び手は、テスト（`plugins/ndf/scripts/tests/test_deps.py`）のほかに無い。2026-10-03 に `plugins/ndf/scripts` の `relay_lib` / `hook_lib` を `\[ndf deps\]` と `== 3` で検索して 0 件だった。設計が `deps.require` を呼ぶ全ファイル（66）の呼び手の側を検索し直して確かめる
- 前提 5: 終了コード 3 が「前提が無い（宣言・認証・対象のファイル）」と「正常な否定の結果」の両方を指す共通の契約そのもの（`release-steps.py` / `runtime_policy.py check` / `launch-cli.sh` などの 3）は、この課題では分けない。いまそれらの終了コードを「飛ばしてよい」と読むステップは無い（`skip_to` / `実行の条件` が打つのは上の 5 か所だけ）

## 対象範囲

含む:
- `deps.require` が依存を用意できずに止まるすべての経路（起動し直したのに import できない・宣言が無い・uv を入れられない）の終了コードを、3 から新しい値へ変える
- 共通の終了コードの表（`plugins/ndf/scripts/lib/README.md` と `step_result.py` の定数・`code_matches`）に新しい値を足し、3 の説明から「依存が無い」に当たる読みを外す
- supervise の run のステップと `実行の条件` が、新しい値を「飛ばしてよい」と読まないことを確かめる（`skip_code` の既定は 3 のまま）
- cross-refactoring の SKILL.md の「前提」の節（assess の打ち方と終了コードの読み方）に、新しい値の意味（依存が欠けた。起動するかを判定できていない）を足す
- cross-refactoring の SKILL.md の `scripts/refactor.py` の説明から「標準ライブラリのみ」を外し、外部パッケージ（`md` / `mdtable` のグループ）を `deps.require` で用意すると書く
- `refactor.py assess` の help の終了コードの一覧に新しい値を足す

含まない:
- 終了コード 3 の「前提が無い」と「正常な否定の結果」の、`deps.py` 以外での分離（前提 5）
- `refactor.py assess` の「飛ばしてよい」の値と、supervise の `skip_code` の既定の変更（前提 1）
- 依存を入れる手順そのもの（uv の入れ方・環境の置き場所）の変更
- `deps.require` を呼ばない hook とラッパー（I13・決定 20）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | エントリポイントが依存を求めた | エントリポイントが import の前に `deps.require(<グループ>)` を呼ぶ | — | — |
| E2 | 依存が import できると分かった | E1 で全グループが import できる | — | E1 |
| E3 | uv の環境で起動し直した | E1 で import できず、uv が見つかるか入れられた | E5 へ | E1 |
| E4 | 起動し直した後も import できないと分かった | E3 の後の E1 で import できない（`NDF_DEPS_REEXEC` が自分のパス） | E5 へ | E3 |
| E5 | 依存の欠けで止まった | E4・宣言が無い・uv を入れられない | — | E1 |
| E6 | supervise がステップの終了コードを読んだ | run のステップか `実行の条件` が終わる | — | E2 か E5 |
| E7 | リファクタリングを飛ばした | E6 で終了コードが `skip_code`（3） | — | E2（assess が判定を終えている） |
| E8 | ステップが止まった（失敗した）と記録した | E6 で終了コードが 0 でも `skip_code` でも承認ゲートでもない | — | E6 |

E7 は E2 を順序の前提に持つ。今は E5 の後でも E7 が起きる（課題の現象）。受け入れ条件 1〜4 は E5 の後に E7 が起きず E8 が起きることを求める。

## 受け入れ条件

- [ ] 1: `NDF_DEPS_REEXEC` に自分のパスを入れ、外部パッケージの無い `/usr/bin/python3` で `refactor.py assess --base origin/<起点>` を打つと、終了コードは 3 でも 0 でもなく、前提 2 の新しい値になる。標準エラーに `❌ [ndf deps]` で始まる理由の行が出る
- [ ] 2: `deps.require` の 3 つの止まり方（起動し直した後も import できない・`pyproject.toml` と `uv.lock` が無い・uv を入れられない）は、どれも前提 2 の新しい値で終わる
- [ ] 3: `skip_to` を持つ run のステップ（`skip_code` を書かない）が前提 2 の新しい値で終わると、supervise は `skip_to` へ進まず、そのステップを飛ばした（`skipped`）と記録しない。`on_fail` へ進む
- [ ] 4: 計画の `実行の条件`（`skip_code: 3`）のコマンドが前提 2 の新しい値で終わると、報告は `結果: 完了` にならず `結果: 止まった` になる
- [ ] 5: 退行しない: `refactor.py assess` は、依存がそろった環境で、プロダクションコードの差分が無ければ 3、あれば 0、`<base>` を解けなければ 2 で終わる（今と同じ）
- [ ] 6: 退行しない: `skip_to` を持つ run のステップが 3 で終わると、今と同じく `skip_to` へ進む
- [ ] 7: `step_result.code_matches("stopped", <新しい値>)` が真になり、`plugins/ndf/scripts/lib/README.md` の終了コードの表に新しい値の行がある。3 の行は「依存が無い」を含まない
- [ ] 8: cross-refactoring の SKILL.md の「前提」の節は、assess の終了コードとして 0・2・3 と新しい値の 4 つの読み方を示し、新しい値のとき「起動しない」とも「飛ばしてよい」とも読ませない
- [ ] 9: cross-refactoring の SKILL.md の `scripts/refactor.py` の説明に「標準ライブラリのみ」が無く、外部パッケージを `deps.require` で用意することが書かれている
- [ ] 10: 全体テスト（`.ndf/project.json` の `test` の `command`）が通る。`python3 scripts/check-skill-frontmatter.py` と `claude plugin validate .` が終了コード 0 で終わる

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 依存の欠けの終了コードを持つのは `deps.py` の定数 1 つだけにする。値を写した定数を Skill ごとに置かない（Value 6） |
| 移行性 | 依存の欠けを 3 と読んでいた呼び手は前提 4 のとおりテストだけで、利用者の側の移行の作業は要らない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。`deps.require` を呼ぶすべてのエントリポイント（66 ファイル）の、依存の欠けのときの終了コードが 3 から新しい値になる。共通の終了コードの表に行が 1 つ増える |
| データ | 変わらない |
| 既存の振る舞い | 依存の欠けた環境で、supervise の検査のプランの assess が「リファクタリングを飛ばす」から「止まった（`on_fail` の refactor へ進む）」に変わる。依存がそろった環境の振る舞いは変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `NDF_DEPS_REEXEC=<refactor.py の絶対パス> /usr/bin/python3 plugins/ndf/skills/cross-refactoring/scripts/refactor.py assess --base origin/main; echo "exit=$?"`（受け入れ条件 1） |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4`（`plugins/ndf/scripts/tests/test_deps.py` の終了コードの期待値と、supervise の skip の判定のテスト） |
| 静的解析・型検査 | `python3 scripts/check-skill-frontmatter.py`、`claude plugin validate .` |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 終了コードの定数は `plugins/ndf/scripts/lib/`（`deps.py`・`step_result.py`）が持ち、表の正本は `plugins/ndf/scripts/lib/README.md`。Skill の側に値を写さない |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。`deps.py` は標準ライブラリだけで書く（ファイルの冒頭の docstring） |
| テスト戦略 | 終了コードは pytest で子プロセスを起こして確かめる（`test_deps.py` の既存の形）。supervise の skip の判定は単体テストで確かめる。`.md` の文言を照合するテストは書かない（`AGENTS.md` の DON'T） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体テストの実行、`deps.py` の docstring と `lib/README.md` の表の同期 |
| 確認してから行う | `skip_code` の既定や「飛ばしてよい」の値（3）を変えること（前提 1 の外へ出る） |
| 行わない | `deps.py` 以外の終了コード 3 の分離、依存の入れ方の変更、hook とラッパーからの `deps.require` の呼び出し |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 依存の欠けに当てる新しい終了コードの値（前提 2 の制約の中で） | 設計（`design`）。設計の承認で人が確かめる | 設計 PR の承認まで |
| 前提 4 の確かめ（`deps.require` を呼ぶ 66 ファイルの呼び手に、依存の欠けを 3 と読む側が無いか） | 設計（`design`） | 設計 PR の承認まで |
| 前提 5 の分離（終了コード 3 の 2 つの意味を共通の契約で分けるか）を別の課題として起こすか | 設計の過程で conductor が判断する（範囲外の課題として起票するかどうか） | 設計 PR の承認まで |
