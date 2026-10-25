# #2104: pace: normal の昇格で、承認ゲート 2 で見た先端と promote-approved がマージする先端を照合しない

正は課題の本文（#2104）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何を見つけたか

`pace: normal` の昇格の経路では、利用者が承認ゲート 2 で見た先端と、承認の後に `supervise.py run <昇格のプラン> --from promote-approved` がマージする先端を照合しない。承認の後にベースブランチが進むと（別のスプリントのマージなど）、利用者が見ていないコミットが本番チャネルへ入る。#1529 は同じ食い違いを `fast` / `auto`（MVV 判定の経路）について、導入の確認の承認資料の SHA と `--expect-head` で照合して直すが、`normal` の経路には照合の材料（承認したときの先端を残す記録）が無い。

## どこで見つけたか

#1529 の設計 PR #2094（`issues/issue-1529-design.md` の「未確認のまま残ること」の 1 行目、`issues/issue-1529-requirements.md` の未決 1）。承認ゲート 1 で利用者が「別の課題で起票する」と決めた。

## なぜこの変更の範囲外なのか

#1529 の受け入れ条件は `fast` / `auto` の昇格のプランの照合だけを求める。`normal` には照合の材料を残す仕組みから足す必要があり、設計の対象が違う。根拠: Value 1 / Value 3（MVV 版 2）

## 直さないと何が起きるか

`delivery` で経路 `promote` を組むリポジトリの `pace: normal` で、承認ゲート 2 で利用者が承認した中身と、本番チャネルへ入る中身が食い違いうる。本番の配布（`release --channel prod`）は #815 で承認したコミットと照合しており、昇格の経路だけが照合を持たない。

## 起票者の推定

- 照合の材料は「承認ゲート 2 の時点の先端の SHA を承認の記録に残す」形になる（推した出所: #1529 の設計の決定 2、#815 の `lib/approved_commit.py`）
- 食い違えば承認ゲート 2 からやり直す（推した出所: #1454 の手動反映の本番系の手順、#1529 の決定 5）

## 由来

PR #2094

---

# 要求と受け入れ条件

## 依頼（原文）

> `pace: normal` の昇格の経路では、利用者が承認ゲート 2 で見た先端と、承認の後に `supervise.py run <昇格のプラン> --from promote-approved` がマージする先端を照合しない。承認の後にベースブランチが進むと（別のスプリントのマージなど）、利用者が見ていないコミットが本番チャネルへ入る。

（書き手: 起票者（#1529 の設計 PR #2094 から起票した AI）、日付: 2026-10-25。利用者の指示の原文として扱わない）

> 承認ゲート 1 で利用者が「別の課題で起票する」と決めた。

（書き手: 起票者が利用者の決定を写した文、日付: 2026-10-25。利用者が決めたのは「#1529 の範囲外とし、別の課題にする」ことだけで、直し方の形は決めていない）

> 人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-10-25）

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 照合の材料は「承認ゲート 2 の時点の先端の SHA を承認の記録に残す」形になる | #1529 の設計の決定 2、#815 の `lib/approved_commit.py` | 一部を採る。先端の SHA を承認ゲート 2 の承認資料へ載せることは受け入れ条件 1。#815 の「承認の記録」の行（`approve` が書く行）まで求めるかは未決 1 |
| S2 | 食い違えば承認ゲート 2 からやり直す | #1454 の手動反映の本番系の手順、#1529 の決定 5 | 受け入れ条件 3・4 |

## 目的

- 昇格の経路で本番チャネルへ入るのは、利用者が承認ゲート 2 で承認資料に見た昇格の Pull Request の先端（承認したコミット）だけにする。承認の後にベースブランチが進んでいれば、マージせずに承認ゲート 2 へ戻る
- 本番の配布（`release --channel prod`。#815）と `fast` / `auto` の `promote`（#1529）が既に持つ「承認した先端とマージする先端の照合」を、昇格の承認の後のマージ（`promote-approved`）にも持たせ、照合の無い本番チャネルへの経路を無くす

## 前提

- 前提 1: 照合の基準にする SHA は、`normal` の `promote` ステップが承認ゲート 2 で止まるときに読んだ昇格の Pull Request の先端（`headRefOid`）である。止まる時点は今どおり CI を待つ前で、CI の結果は `promote-approved` が今どおり待つ
- 前提 2: `promote-approved` のコマンドは今 `normal` と `fast` / `auto` で同じ（`test_supervise_pace_verify.py` が等しいことを確かめている）。照合は両方の `promote-approved` に効かせる。`fast` / `auto` で `handoff` の後に `--from promote-approved` で続けるときは、`prepare` が書いた昇格の承認資料（`{state_dir}/work/approval-promote.md`。承認ゲート 2 で利用者が見るもの）の先端と照合する
- 前提 3: `promote-approved` が読む承認資料のパスはプランが決めて渡す。利用者も conductor もパスを手で渡さない
- 前提 4: `fast` / `auto` の `promote` ステップ（`--expect-head-from approval-verify.md --gate-approved mvv`。#1529 の P1〜P4）は変えない

## 対象範囲

含む:
- `normal` の `promote` ステップが承認ゲート 2 で止まるときに、昇格の Pull Request の先端の SHA を承認資料へ載せること
- `fast` / `auto` の `prepare` が書く昇格の承認資料にも同じ SHA の行を載せること（前提 2）
- `promote-approved` が承認資料の SHA と昇格の Pull Request の先端を照合し、違えばマージせずに止まること、資料を読めなければ照合を飛ばさずに止まること
- 止まった後の続け方（承認ゲート 2 からやり直す）を、結果の `next` と `development-workflow` の `references/pace.md` の承認ゲート 2 の行に書くこと
- 確定仕様 `docs/specifications/ndf-release-approved-commit.md` の P5（`normal` の `promote` と `promote-approved` は `--expect-head-from` を持たない）と「含まない」の `pace: normal` の行を、この変更に合わせて書き直すこと

含まない:
- `release --channel prod`（#815）と `fast` / `auto` の `promote` ステップ（#1529）の照合の変更
- 昇格でない本番チャネルへのマージ（経路 `merge` で宛先が自動反映の本番チャネルのとき、`merge-when-green` が承認ゲート 2 で止まり `--from merge-approved` で続ける経路）の照合。同じ形の欠けがありうるが、この課題の対象（昇格の経路）と違う。別の課題の候補として conductor へ返す
- 手動反映の本番系（`gate-2` のプランと手で行う「本番」のステージ。#1454）
- 承認ゲートの数と置き場所の変更（承認の工程を足さない）

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | `normal` の `promote` が昇格の Pull Request を見つけた（無ければ作った） | 昇格のプランの `promote` ステップ | `gh` が失敗すれば stopped。昇格する変更が無ければ ok で終える（今どおり） | — |
| E2 | `promote` が昇格の Pull Request の先端 X を読み、承認資料へ X を載せて承認ゲート 2 で止まった | E1 の後の最初の読み | 資料を書けなければマージせずに止まる（受け入れ条件 1 の退行が無いこと） | E1 |
| E3 | 利用者が承認資料を見て承認ゲート 2 を承認した | 人 | 差し戻しなら続きは流さない（今どおり） | E2 |
| E4 | ベースブランチが X から Y へ進んだ | プランの外のマージ | — | E2 の後のどの時点でも起きうる（E3 の前でも後でも同じ扱い） |
| E5 | `promote-approved` が承認資料から X を読んだ | conductor の `run <プラン> --from promote-approved` | 資料が無い・行が無い・形が違えば、`gh` を呼ばずに止まる（受け入れ条件 4） | E3 |
| E6 | `promote-approved` が昇格の Pull Request の先端を読み、X と比べた | E5 の後、CI を待つ間の読みのたび | 先端が X でなければマージせずに止まる（受け入れ条件 3） | E5 |
| E7 | X で CI が通り、`--match-head-commit X` で本番チャネルへマージした | E6 で一致し CI が緑 | マージが拒まれれば stopped（今どおり） | E6 |
| E8 | 照合で止まった後、`--from promote` から流し直し、新しい先端 Y で承認ゲート 2 に止まった | conductor（結果の `next` に従う） | E2 と同じ | E5 か E6 の失敗 |

## 受け入れ条件

- [ ] 1. `pace: normal` の昇格のプランの `promote` ステップが承認ゲート 2 で止まる（終了コード 10・status `gate`）とき、書いた承認資料に、止まる時点で読んだ昇格の Pull Request の先端の 40 桁の SHA が 1 行で載り、結果の `metrics` か `items` にも同じ SHA が載る
- [ ] 2. 前提: 承認資料の SHA が X で、昇格の Pull Request の先端が X のまま CI が通る
      操作: `run <昇格のプラン> --from promote-approved`
      結果: `gh pr merge` が `--match-head-commit X` つきで 1 度呼ばれ、本番チャネルへマージされ、続く `record` へ進む（今の成功の経路が変わらない）
- [ ] 3. 前提: 承認資料の SHA が X で、昇格の Pull Request の先端が Y（X と違う）。承認の前に進んだときも、`promote-approved` が CI を待つ間に進んだときも同じ
      操作: `run <昇格のプラン> --from promote-approved`
      結果: `gh pr merge` を呼ばず、`items[].result: head_moved`（`head` と `expected` つき）で非 0 で止まり、結果の `next` が承認ゲート 2 からやり直す打ち方（`--from promote` から流し直す）を示す
- [ ] 4. 前提: `promote-approved` に渡す承認資料が無い、SHA の行が無い、または行が 40 桁の 16 進でない
      操作: `run <昇格のプラン> --from promote-approved`
      結果: `gh` を 1 度も呼ばずに終了コード 2（`EXIT_UNREADABLE`）で止まる（照合を飛ばしてマージしない）
- [ ] 5. `promote-approved` のコマンドに、照合に使う承認資料のパスがプランの組み立てで入る（`normal` と `fast` / `auto` の両方。前提 2・3）。プランの組み立てのテストで確かめる
- [ ] 6. `fast` / `auto` の `prepare` が書く昇格の承認資料に、受け入れ条件 1 と同じ形の SHA の行が載る
- [ ] 7. `fast` / `auto` の `promote` ステップのコマンドと振る舞い（`--expect-head-from approval-verify.md --gate-approved mvv`・失敗で `handoff`）が変わらない。#1529 の既存テストがすべて通る
- [ ] 8. SHA の行を書く形と読む形は 1 か所（`lib/approved_commit.py` の関数）が決め、書き手と読み手がそれを共有する
- [ ] 9. `development-workflow` の `references/pace.md` の承認ゲート 2 の行と、確定仕様 `docs/specifications/ndf-release-approved-commit.md` から「`promote-approved` は照合を持たず、今の先端をマージする」旨の記述が無くなり、照合で止まったときの続け方が書かれる
- [ ] 10. 全体テスト（下の検証手段）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| セキュリティ | 本番チャネルへ入るのは、承認資料に載った SHA と等しい先端だけである（受け入れ条件 3・4）。資料を読めないときに照合を飛ばす経路を持たない |
| 運用・保守性 | 照合で止まった結果は、どの SHA を期待し今どの SHA かを `items` に残し、続け方を `next` に書く。conductor がプランの外で手当てをしなくても承認ゲート 2 へ戻れる |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `merged-steps.py promote` の承認ゲート 2 の結果に SHA が増える（追加のみ）。`promote-approved` のコマンドに照合の引数が増える（プランが組み立てるため、利用者の打ち方は変わらない）。既に書き出したプラン（引数の無い `promote-approved`）は今どおり照合なしで動くか、止まるかは未決 2 |
| データ | 承認資料（Markdown）に SHA の行が 1 行増える。スキーマの移行は無い |
| 既存の振る舞い | 承認の後にベースブランチが進んだとき、`promote-approved` がマージせずに止まるようになる（今はその先端をマージする） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析・型検査 | `uv run --frozen --project . --group lint ruff check .` |
| 文書の検査 | `python3 plugins/ndf/scripts/instructions-check.py --root .`、`python3 scripts/check-skill-frontmatter.py` |
| 手動確認 | 昇格の経路を組むリポジトリで `pace: normal` の承認ゲート 2 を 1 度通し、承認資料に先端の SHA が載ることと、承認の後にマージされた先端がその SHA であることを見る（リリース後テストで確かめる） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | `plugins/ndf/scripts/` の既存のモジュール（`merged_lib/`・`supervise_lib/`・`lib/approved_commit.py`）に置く。実験版の置き場（`scripts/experimental/`）には置かない（既定で働く承認ゲートの振る舞いのため安定版。`AGENTS.md` の「安定版と実験版」） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。同じ役割の関数を分けない（MVV Value 6） |
| テスト戦略 | `merged-steps.py promote` の単体テスト（`gh` を偽物にして呼んだ回数と引数を数える）とプランの組み立てのテストで受け入れ条件 1〜8 を担保する。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、#1529 と #815 のテストを通したままにすること |
| 確認してから行う | `merged-steps.py` の引数の追加と承認資料の行の追加（承認ゲート 1・2 で人が見る） |
| 行わない | 承認ゲートの追加、`release --channel prod` と `fast` / `auto` の `promote` の変更、経路 `merge` の `merge-approved` の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 照合の基準を承認資料の SHA の行だけにするか、#815 の「承認の記録」の行（`approve --by user` が書く）がそろうことまで求めるか（S1）。後者は承認の後に conductor の操作が 1 つ増える | `design`（承認ゲート 1 で利用者が確かめる） | 設計 PR |
| 2. この変更の前に書き出したプランの `promote-approved`（照合の引数を持たない）を、今どおり照合なしで通すか、照合の材料が無いとして止めるか | `design` | 設計 PR |
| 3. 経路 `merge` の `merge-approved`（昇格でない本番チャネルへのマージ）にも同じ欠けがあるかの確認と起票 | conductor | この課題の設計 PR まで |
