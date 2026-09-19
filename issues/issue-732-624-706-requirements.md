# cross-review: 誤りを示されていない重大な指摘が数えられずに承認で終わる → 数えない指摘を棄却と軽微な指摘に限る（#732 #624 #706 の要求）

## 目的

**この変更の後、cross-review の収束の判定が数えないのは、棄却された指摘（実行して再現しなかった・否定を受けた）と軽微な指摘だけになる。** 誰にも誤りを示されていない重大な指摘は、担当の数・反証の有無・根拠の項目の有無によらず新しい指摘として残り、修正の工程へ渡る。

いまは、誰にも誤りを示されていない重大な指摘が「数えない」区分へ落ち、新しい指摘 0 件として承認で終わる。修正の要る指摘が修正の工程へ渡らない。この形は 3 つの場面で出る。3 つの場面は同じ 1 つの直しで数えられる。立証不足の区分が 2 つの意味を兼ねる状態が解け、区分を読めば未解決の理由が分かる。

| 場面 | 課題 |
| --- | --- |
| 反証する担当がいない 1 者のループ | #624 |
| 実行検証も支持も無い指摘 | #706 |
| 起動し直した担当の指摘 | #583 の収束の部分 |

## 用語と識別子の対応

本文は左の業務用語で書く。識別子は、コードブロック・表・受け入れ条件（検査の入力と期待値をそのまま定める）でそのまま使う。

| 用語 | 識別子 | 意味 |
| --- | --- | --- |
| 数える | `COUNTED_CLASSIFICATIONS`（数える区分の集合） | 収束の判定（新規性の層）が、そのラウンドの新しい指摘として件数に入れること |
| 重大な指摘 / 軽微な指摘 | `severity` の `major` / `minor`（`major` 以上 / `minor` 以下） | 指摘の重大度。軽微な指摘は承認（`APPROVE`）を妨げない |
| 区分 | `classification` | 印のあるラウンドで指摘ごとに付く値 |
| 棄却 | `rejected`（理由は `rejection_reason`） | 指摘が誤りだと示されたこと。実行して再現しなかった（`not_reproduced`）か、別の担当が否定（`refute`）を返した |
| 反証の機会 | `critiques` が 1 件以上 | 提案者以外の担当が、その指摘へ賛否を返したこと |
| 反証の値: 支持 / 否定 / 立証できない / 範囲外 | `support` / `refute` / `insufficient_evidence` / `out_of_scope` | 反証の担当が返す値のうち、この文書が扱う 4 つ |
| 立証の機会が無かった | — | 反証の機会が無い、または反証はあるが支持も否定も無い。誤りは示されていないが、独立に確かめた担当もいない |
| 未反証 | `unrefuted`（理由は `unrefuted_reason`。値は反証なし `no_critique` / 支持なし `not_supported`） | 新しい区分。重大な指摘で、再現も棄却もされておらず、独立に確かめた担当もいない |
| 人の判断待ち | `needs_human_judgment` | 独立に確かめた担当がいる重大な指摘の区分 |
| 立証不足 | `insufficient_evidence`（区分の値） | 変更後は、再現も棄却もされていない軽微な指摘だけの区分 |
| 独立に確かめた | `support` が 1 件以上、または `origin_runtimes` が 2 者以上 | 提案者以外の担当が支持を返した、または 2 者以上が同じ指摘を独立に出した |
| 根拠の 2 項目 | `evidence` と `falsification`（両方が空でないとき `has_evidence` が真） | 別の担当が確かめるための入力 |
| 印 | `evidence_rounds` | そのラウンドが統合・実行検証・反証を通ったこと。印のあるラウンドだけが区分で絞られ、無いラウンドは全件を数える |
| 代表 | `merged_into` を持たない側 | 統合した組で判定が読む 1 件。束ねられた側は `merged_into` を持つ |
| 却下の記録 | `rejected_findings` | 修正の工程が却下した指摘と理由。次のラウンドのレビュープロンプトへ渡る |
| 状態の管理スクリプト / 測定スクリプト / 反証のプロンプト | `state.py` / `measure.py` / `critique.sh` | いずれも `plugins/ndf/skills/cross-review/scripts/` の下 |
| 区分の判定 / 区分の書き込み / 新しい指摘の数え上げ / 反証の不足の扱い | `_classify_finding` / `_apply_classification` / `_new_finding_count` / `_handle_incomplete_critiques` | 状態の管理スクリプトの内部関数 |
| 収束の判定 / 反証の取り込み | `judge`（本体は `cmd_judge`）/ `collect-critiques` | 状態の管理スクリプトの副コマンド |
| 1 者に絞る引数 | `--only` | 使える担当を 1 者へ絞る起動の引数。意味づけは #727 が持つ |
| 承認で終わる | `approved` | 収束ループの結末 |

## 対象範囲

変えるのは、区分の判定と数える集合、印の外し方、それらの説明と確定仕様、テストである。1 者に絞る引数の扱い・投稿の経路・終了コードは並行する設計が持つ。

含む:

- `state.py` の区分の判定（`_classify_finding` / `_apply_classification`）と、数える区分の集合（`COUNTED_CLASSIFICATIONS`。`measure.py` の同名の定数も）
- `state.py` の反証が揃わないときの印の外し方（`_handle_incomplete_critiques`）
- 反証のプロンプト（`critique.sh`）の `insufficient_evidence` の説明
- `cross-review` の `docs/04` / `docs/05` / `docs/06` の区分の表
- 確定仕様 `docs/specifications/cross-review-evidence-based.md` の区分の表
- テスト 4 ファイル（`tests/test_classify_findings.py` / `tests/test_critiques.py` / `tests/test_measure.py` / `tests/test_skill_layout.py`）

含まない:

| 扱わないもの | 理由 |
| --- | --- |
| `--only` の意味づけ・使える担当の決め方・再開の引数 | #727（G1）の設計が持つ。1 者のときに何を数えるかだけをこの文書が決める |
| 起動し直した担当の投稿の重なり、投稿を進行側へ移すこと | #730（G5）の設計が持つ。#583 のうちこの文書が扱うのは、起動し直した担当の指摘が数えられずに収束する部分だけである |
| `read-result` / `judge` の終了コードと出力の変数、結末の語彙 | #729（G3）の設計が持つ。`judge` の 0 / 2 / 7 / 8 とその出力の変数は変えない |
| `cross-refactoring` の収束 | 区分を持たない（`refactor_lib` は `classification` を読まない） |
| 新規性の一致の判定（位置・近傍・本文）と振動の閾値 | 変えるのは母集合だけである（#156 の設計のまま） |
| `minor` 以下の指摘を数えること | `minor` は `APPROVE` を妨げない（`docs/03` の判定の基準）。数えると、`minor` だけの `REQUEST_CHANGES` でラウンドが続く |
| 反証の値（5 つ）の追加・削除 | 反証の担当が返す語彙は変えない。変えるのは、返った値を区分がどう読むかである |
| クラス図・型の追加 | 型を追加・変更しない（関数と辞書で組んだ状態ファイルを扱う）。状態ファイルの形は設計文書の「データ構造」が持つ |
| システムの文脈・配置の図 | 動くのは `state.py` の 1 プロセスで、外部との出入り（`gh`）は変わらない |
| `CHANGELOG.md` と版数 | 配布の工程が書く |

## 前提

数える区分を増やしても成り立つことを 3 つ前提にする。修正の工程が全件を読むこと、却下の記録が同じ論点を止めること、新規性の一致が前のラウンドの指摘を新規に数えないことである。

| # | 前提 | 崩れたときに起きること |
| --- | --- | --- |
| 1 | 修正の工程（`fix`）は Pull Request の未解決のスレッドを区分によらず全件読む。区分は収束の判定と測定だけが読む（`plugins/ndf/skills/fix/` と `docs/02-fix-and-rotation.md` に区分を読む箇所が無い。`grep -rn classification` が 0 件） | 数えるだけで修正へ渡らない指摘が生まれ、同じ指摘が毎ラウンド新規に見える |
| 2 | 却下した指摘は `rejected_findings` に位置と理由つきで残り、次のラウンドのレビュープロンプトへ渡る（#156 の 1 本目） | 数える区分が増えたとき、同じ論点が戻る。この記録がそれを止める |
| 3 | 新規性の一致（位置・近傍・本文）は変えない。前のラウンドと一致する指摘は、区分によらず新規に数えない | — |

## 受け入れ条件

22 件である。区分 12 件（AC1〜AC12）、印 1 件（AC13）、退行しない 4 件（AC14〜AC17）、文書 3 件（AC18〜AC20）、全体 2 件（AC21〜AC22）。#624 の再現は AC1、#706 の再現は AC5 と AC7、#583 の収束の部分は AC4 が確かめる。各条件は検査の入力と期待値をそのまま定めるため識別子で書く。業務用語との対応は「用語と識別子の対応」にある。

### 区分（#624 / #706 / #583 の収束の部分）

- [ ] AC1: 前提: 担当が 1 者（`only: "codex"`）で、印の付いたラウンドが 1 つある。そのラウンドに根拠の 2 項目を持つ `major` の指摘が 1 件、反証は 0 件、前のラウンドは無い
      操作: `_new_finding_count` と `judge` を呼ぶ
      結果: `_new_finding_count` が `(1, True)` を返し、`judge` が終了コード 2 で終わる。指摘の `classification` は `unrefuted`（変更前は `(0, True)` と終了コード 0。#624 の再現）
- [ ] AC2: 前提: AC1 の指摘が根拠の 2 項目のどちらかを欠く（`has_evidence` が偽）
      結果: `_new_finding_count` は `(1, True)` を返す。`classification` は `unrefuted` で、`has_evidence` は偽のまま残る
- [ ] AC3: 前提: AC1 の指摘が `minor` である
      結果: `_new_finding_count` は `(0, True)` を返し、`classification` は `insufficient_evidence` である
- [ ] AC4: 前提: 担当が `agy` + `kiro` で、印の付いたラウンドがある。そのラウンドで `kiro` の指摘は反証を持ち、`agy` の根拠を持つ `major` は反証 0 件のまま入っている（起動し直した担当の指摘が、反証を取り込んだ後に取り込まれた形）
      結果: `_new_finding_count` が `agy` の 1 件を数える。`classification` は `unrefuted`（#583 の収束の部分）
- [ ] AC5: 前提: 担当 2 者で、相手が根拠を持つ `major` へ `insufficient_evidence` を返し、`support` も `refute` も無い
      結果: `classification` は `unrefuted` で、数える（変更前は `insufficient_evidence` で数えない。#706 の「反証の担当が `support` を返さなかった」）
- [ ] AC6: AC5 で相手が `out_of_scope` を返したときも、`classification` は `unrefuted` で、数える
- [ ] AC7: 前提: `major` の指摘に `support` が 1 件以上ある
      結果: `has_evidence` の真偽によらず `classification` は `needs_human_judgment` である。変更前は `has_evidence` が偽なら `insufficient_evidence` だった（#706 の「`support` が付いていても根拠の 2 項目の欠落で落ちる」）
- [ ] AC8: `origin_runtimes` が 2 者以上の `major` は、`has_evidence` の真偽によらず `needs_human_judgment` である
- [ ] AC9: `refute` が 1 件以上、または実行検証が `not_reproduced` の指摘は `rejected` になり、`unrefuted` より先に当たる。`reproduced` の指摘は `verified_blocking` / `verified_non_blocking` になる（変更前と同じ）
- [ ] AC10: `unrefuted` の指摘は `unrefuted_reason` を持つ。値は `no_critique`（反証を返した担当が 0 者）か `not_supported`（反証はあるが `support` も `refute` も無い）のどちらかである。他の区分の指摘は `unrefuted_reason` を持たない（区分が変わったときは消える）
- [ ] AC11: `COUNTED_CLASSIFICATIONS` は `verified_blocking` / `needs_human_judgment` / `unrefuted` の 3 つである。`state.py` と `measure.py` の値が一致する
- [ ] AC12: `measure.py` の方式 `proposed` は、印のあるラウンドの `unrefuted` の指摘を `found` に数える

### 反証の取り直しで揃わないときは印を外す

- [ ] AC13: 前提: 印が付いたラウンドがある
      操作: `collect-critiques` を実行し、反証が揃わない（終了コード 7）
      結果: `evidence_rounds` からそのラウンドの番号が消え、`_new_finding_count` は payload の全件を数える。取り直した後も揃わないとき（2 度目）も印は付かない

### 退行しない

- [ ] AC14: `minor` 以下の指摘の区分は変わらない。`support` が付いた `minor` は `insufficient_evidence`、再現した `minor` は `verified_non_blocking` で、どちらも数えない
- [ ] AC15: 印を持たないラウンドの数え方（payload の全件）は変わらない
- [ ] AC16: `tests/test_classify_findings.py` の既存のテストのうち、期待値を変えるのは旧い区分を固定した 4 件だけである。それ以外は期待値を変えずに通る。4 件は次のとおり
      `test_support_without_evidence_is_insufficient` / `test_nothing_matched_is_insufficient` /
      `test_a_finding_without_verification_is_readable` / `test_only_two_classifications_are_counted`
- [ ] AC17: `judge` の終了コード（0 / 2 / 7 / 8 / 1）は変わらない。標準出力の変数（`REVIEWER_INTENTS` / `NEW_FINDINGS` / `CARRIED_OVER_THREADS` / `PENDING_POSTS` / `RELAUNCH_AGENTS`）も変わらない。`cmd_judge` の本体の行を書き換えない

### 文書

- [ ] AC18: 規約の 3 文書が新しい区分を書く。`docs/06-evidence.md` の区分の表が 6 行になり、`unrefuted` の行と「数えるのは 3 つ」を持つ。`docs/04-contracts.md` の `classification` の項が 6 区分と数える 3 つを書く。`docs/05-pool-and-convergence.md` の終了基準が、担当 1 者と起動し直した担当の指摘の数え方を書く。次の 4 つがそれぞれ 1 行以上を出す

  ```bash
  grep -n "unrefuted" plugins/ndf/skills/cross-review/docs/06-evidence.md
  grep -n "unrefuted" plugins/ndf/skills/cross-review/docs/04-contracts.md
  grep -n "unrefuted" plugins/ndf/skills/cross-review/docs/05-pool-and-convergence.md
  grep -n "印を外す" plugins/ndf/skills/cross-review/docs/06-evidence.md
  ```

- [ ] AC19: `critique.sh` のプロンプトが「`insufficient_evidence` を返しても指摘は数から落ちない。誤りを示せるなら `refute` を返す」ことを書く。`grep -n "数から落ち" plugins/ndf/skills/cross-review/scripts/critique.sh` が 1 行以上を出す
- [ ] AC20: 確定仕様 `docs/specifications/cross-review-evidence-based.md` が `docs/06-evidence.md` と同じ 6 区分を持つ。持つのは区分の表と区分ごとの行き先の表である。`grep -c "unrefuted" docs/specifications/cross-review-evidence-based.md` が 2 以上を出す

### 全体

- [ ] AC21: `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] AC22: 次の 4 つが終了コード 0 で終わる

  ```bash
  python3 scripts/check-skill-frontmatter.py
  python3 scripts/check-doc-staleness.py
  python3 scripts/check-markdown-links.py --root .
  python3 scripts/check-doc-line-limit.py
  ```

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | 数える区分の集合は `state.py` と `measure.py` の 2 か所にあり、一致をテストが固定する（AC11）。区分の理由（`rejection_reason` / `unrefuted_reason`）は状態ファイルに残り、収束の後に「なぜ数えたか・数えなかったか」を読める |
| 移行性 | 状態ファイルの `version` は上げない。この変更より前の状態ファイルは、次に `judge` を呼んだ時点で区分が付け直される（区分は毎回計算し直す）。旧い記録の `classification` を `measure.py` が読むときは、旧い値のまま読む |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `state.py` の引数・終了コード・出力の変数は変わらない。状態ファイルの `classification` に値 `unrefuted` が増え、`unrefuted_reason` が増える（読む側は `measure.py` だけ） |
| データ | 状態ファイルの形は変わらない（項目が 1 つ増える。`version` は上げない） |
| 既存の振る舞い | 印のあるラウンドで、反証を受けていない・支持されていない・根拠の項目を欠く重大な指摘が数えられるようになる。2 者で相手が「立証できない」を返した重大な指摘も数える（#156 の判断を改める。理由は設計文書の決定 4）。収束までのラウンド数が増えることがある |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --with pytest pytest scripts/tests plugins/ndf -q` |
| 静的解析・文書の検査 | AC22 の 4 コマンド |
| 再現手順 | #624: 状態ファイルを最小の形で組み `_new_finding_count` と `cmd_judge` を呼ぶ（AC1）。#706: `_classify_finding` に反証 `insufficient_evidence` 付きの `major` と、`support` 付きで根拠を欠く `major` を渡す（AC5 / AC7） |
| 手動確認 | 無し（すべてテストで確かめる） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | Skill の実体は `plugins/ndf/skills/cross-review/`。テストは同じ Skill の `tests/`。`dev.kiro` / `dev.agy` は symlink で参照するため書き写す配布物は無い |
| コーディング規約 | `state.py` は stdlib だけの uv 自己完結スクリプト（`AGENTS.md` / `cross-review/SKILL.md`）。区分の判定は純粋な関数で、出力も終了コードも持たない |
| テスト戦略 | 区分は `_classify_finding` の単体で、数え方は状態ファイルを組んだ `_new_finding_count` と `cmd_judge` の終了コードで確かめる（既存の `test_classify_findings.py` の形） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 区分の判定と数える集合の変更、印の外し方、文書と確定仕様の更新、テスト |
| 確認してから行う | 無し |
| 行わない | `--only` の扱い（#727）、投稿の移動（#730）、終了コードと結末の語彙（#729）、`minor` を数えること |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 未反証を数えることで収束までのラウンド数がどれだけ増えるか | 実装の後の運用で測定スクリプトの出力を見る | 配布後 |

## 置き換える既存の受け入れ条件との対応

**この文書は、既存の設計 [issue-624-478-648-requirements.md](issue-624-478-648-requirements.md) の P4（AC1〜AC9。PR #667、2026-09-15）を置き換える。** P5（#478 #648）は #727 の設計が持ち、この文書は触らない。#583 の投稿の重なりは #730 の設計が持つ。

| 既存 | この文書 | 扱い |
| --- | --- | --- |
| AC1（1 者、根拠付き `major`、反証 0 件 → 数える） | AC1 | 引き継ぐ。区分の名前が `needs_human_judgment` から `unrefuted` へ変わる |
| AC2（1 者、`minor` → 数えない） | AC3 | 引き継ぐ |
| AC3（1 者、根拠なし → 数えない） | AC2 | **改める。** 根拠の項目の欠けは立証の機会が無かった側に入る（親 #732） |
| AC4（起動し直した担当の `major` → 数える） | AC4 | 引き継ぐ。区分は `unrefuted` |
| AC5（揃わないときに印を外す） | AC13 | 引き継ぐ |
| AC6（2 者で相手が `insufficient_evidence` / `out_of_scope` → 数えない） | AC5 / AC6 | **改める。** 数える（設計文書の決定 4） |
| AC7（`origin_runtimes` 2 者の区分は変わらない） | AC8 | 引き継ぐ。根拠の条件は外す |
| AC8（印なしのラウンドと実行検証の区分は変わらない） | AC9 / AC15 / AC16 | 引き継ぐ。期待値を変える既存のテスト 4 件を名指しする |
| AC9（文書の 3 つの grep） | AC18 | 引き継ぐ。語を `unrefuted` に変える |

## 依頼（原文）

3 件の依頼はいずれも、指摘が数えない区分へ落ちたまま収束する現象を報告している。#732 が根本原因と採る手を定め、#624 と #706 がそれぞれの再現を持つ。引用のため、識別子と文の長さは元のまま残す。

### #732（根本原因の親）

> **cross-review の収束で「数えない」とする区分。** 場所は `plugins/ndf/skills/cross-review/scripts/state.py` の `_classify_finding`（3443 行目）と `COUNTED_CLASSIFICATIONS`（3431 行目）、規約 `docs/06-evidence.md` の区分の表である。
>
> - `insufficient_evidence` が 2 つの場合を兼ねている。反証の機会があって支持されなかった場合と、立証の手段が無かった場合（反証する担当がいない・実行検証が無い・根拠の項目が欠ける）である
> - どちらも新規性の数から落ちるため、立証の手段が無かっただけの未解決の `major` が残ったまま収束する
>
> #583 の収束の部分も、同じ区分を通る。
>
> ## 採る手
>
> 分離（`extract_strategy`）。「棄却された（`refute` / `not_reproduced`）」と「立証の機会が無かった」を別の区分にし、数えない判断を前者だけに掛ける。
>
> ## 完了条件
>
> - 数えない判断が棄却された指摘に限られ、反証の機会が無かった `major` が新規性に残ることを検査が確かめる
> - 各子 issue の再現手順を実行し、現象が出ないことを確かめる（子 issue はその時点の棚卸が「閉じてよい」で閉じる）

### #624

> `cross-review` を `--only codex` で 1 者だけにして回すと、codex が `REQUEST_CHANGES` で新しい指摘を投稿したラウンドでも、`judge` が収束と判定する（ndf 10.10.1、2026-09-13）。
>
> `--only` で担当が 1 者だと、反証する他の担当がいないため数える区分に入る指摘が 0 件になり、`_evaluate_convergence`（`:2848`）の `findings_measurable and new_findings == 0` が真になる。
>
> 状態ファイルを最小の形で組み、関数を直接呼んだ再現（`--only codex`、証拠付きの major 1 件、印の付いたラウンド 1）:
>
> ```text
> new_finding_count (0, True) classification insufficient_evidence
> round_passes False converged True
> ```

### #706

> `cross-review` で、**未解決の `major` が残っているのに、指摘が数えない区分 `insufficient_evidence` へ落ち、新規性の母集合から外れて「新しい指摘 0 件」と判定される。** 結果は `approved` になる。
>
> ideabase の PR #45 のラウンド 2 で、3 件の指摘がいずれも `suggested_check` を実行できず `insufficient_evidence` になり、judge が `NEW_FINDINGS=0` を返した。指摘のうち 2 件は別の担当が `support` を付けており、こちらでもコードを読んで再現を確かめられた（文書が実装と食い違っていた）。
>
> 数えない区分へ落ちるのは、次のいずれかに当たる指摘である。
>
> - 反証の担当が `support` を返さなかった（`insufficient_evidence` を返した、または反証が無い）
> - 根拠の 2 項目（`evidence` / `falsification`）のどちらかが欠けている
> - 重要度が `minor` 以下である
>
> 対処の候補: 実行検証ができない指摘は `insufficient_evidence` ではなく別の区分（未検証）として新規性に数える、または他の担当の `support` が付いた指摘は区分によらず数える。

## 関連する文書

この文書は「何を満たすか」だけを扱う。

| 文書 | 何を持つか |
| --- | --- |
| [issue-732-624-706-design.md](issue-732-624-706-design.md) | どう作るか（決定の記録・実測・データ構造・契約・処理の流れ・テスト設計） |
| [issue-624-478-648-requirements.md](issue-624-478-648-requirements.md) | 置き換える前の受け入れ条件（P4）。P5 は #727 の設計が持つ |
