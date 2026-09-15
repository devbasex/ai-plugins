# #624 / #478 / #648: 反証する担当がいない指摘を数え、使える担当だけで回し、再開で引数を反映する

設計は [issue-624-478-648-design.md](issue-624-478-648-design.md) にある。この文書は「何を満たすか」だけを扱う。

**3 つの課題は 2 本の Pull Request で直す**（設計文書の決定 1）。P4 は #624 と、#583 のうち収束の誤りの部分を
直す。P5 は #478 と #648 を直す。マージは P4 → P5 の順である。受け入れ条件も Pull Request ごとに分ける。

## 目的

- 反証する担当がいない指摘が、数える区分から黙って落ちなくなる。1 者で回しても、起動し直した担当の指摘でも、
  修正を要する指摘が残ったまま収束しない
- 認証できない担当や、打ち切りが分かっている担当を外し、使える担当だけでレビューを回せる
- 中断した収束ループを、引数で進め方を変えて再開できる。反映しなかった引数は出力で分かる

## 対象範囲

含む:

- `state.py` の収束の数え方（区分の判定、証拠集約の印の外し方）（P4）
- `lib/assignment.py` の `review_assign` と、除外・使える者の一覧の算出（P5）
- `lib/auth.py` に止めない認証の確認を足す（P5）
- `state.py` の `init` の新規と再開の経路、`start-round` の担当の決め方、前ラウンドの検査、`report`（P5）
- `cross-review` の `SKILL.md` と `docs/`（01 / 04 / 05 / 06）、テスト（P4 / P5）

含まない:

| 扱わないもの | 理由 |
| --- | --- |
| `cross-refactoring` の除外の引数 | 実装担当を外す規則と組むと、使える者が 2 者のときにレビュー担当が 1 者になる。著者を含めて回す規則（#478 のコメント）は判定とプロンプトに及び、D-C の範囲と重なる（設計文書の決定 10）。#664 へ切り出した |
| 起動した後に分かる使えなさ（利用上限・モデルの 404）で担当を自動で外すこと | 分類は D-A（#619）が作る。この変更は利用者が `--exclude` で外す入口だけを作る（決定 19） |
| 監視の上限・結果のファイル化・`NO_RESULT` の理由・`JUDGE_RC -eq 7` の分岐 | D-A（#662 #598 #537 #619 #584 #583）の範囲 |
| #583 の投稿の重なり | D-A の範囲。この変更が扱うのは #583 のうち収束の誤りの部分だけである |
| 担当 2 者で、相手が反証で `support` を返さない単独の指摘を数えないこと | #156 の設計どおりであり、#624 の対象外と issue に書かれている |
| 再開で `--verify-command` を空へ戻す手段 | 置き換えはできる。空へ戻す要求は出ていない |
| クラス図 | 型を追加・変更しない（関数と辞書で組んだ状態ファイルを扱う）。状態ファイルの形は契約文書のデータ構造の節が持つ |
| システムの文脈・配置の図 | 動くのは `state.py` の 1 プロセスで、外部との出入り（`gh` と認証の確認コマンド）は変わらない |
| `CHANGELOG.md` と版数 | 配布の工程が書く |

## 受け入れ条件（P4: #624 と #583 の収束の部分）

反証する担当がいない指摘を数える:

- [ ] AC1: 担当が 1 者（`only: "codex"`）で、証拠集約の印が付いたラウンドがある。そのラウンドに根拠を持つ `major` の
  指摘が 1 件あり、反証は 0 件で、前のラウンドは無い。このとき `_new_finding_count` が `(1, True)` を返し、`judge` が終了コード 2 で
  終わる（変更前は `(0, True)` と終了コード 0。issue の再現）
- [ ] AC2: AC1 の指摘が `minor` のとき、`_new_finding_count` は `(0, True)` を返す。担当 2 者のときと同じく、
  `minor` 以下は数えない
- [ ] AC3: AC1 の指摘が根拠を持たないとき、`_new_finding_count` は `(0, True)` を返す。根拠を持たないとは、
  `evidence` か `falsification` が空であることをいう
- [ ] AC4: 担当が `agy` + `kiro` で印が付いたラウンドに、`agy` の根拠を持つ `major` が反証 0 件のまま入っている。
  起動し直した担当の指摘が、反証を取り込んだ後に取り込まれた形である。このとき `_new_finding_count` が `(1, True)` を
  返す（#583 の収束の部分）

反証の取り直しで揃わないときは印を外す:

- [ ] AC5: 印が付いたラウンドで `collect-critiques` を実行し、反証が揃わない（終了コード 7）。このとき
  `evidence_rounds` からそのラウンドの番号が消え、`_new_finding_count` は payload の全件を数える。取り直した後も
  揃わないとき（2 度目）も印は付かない

退行しない:

- [ ] AC6: 担当 2 者で、相手が単独の根拠を持つ `major` へ `insufficient_evidence` か `out_of_scope` を返した指摘は
  数えない。`support` なら数え、`refute` なら `rejected` になる
- [ ] AC7: `origin_runtimes` が 2 者の指摘の区分は変わらない（根拠を持つ `major` は数え、`minor` は数えない）
- [ ] AC8: 印を持たないラウンドの数え方（payload の全件）と、実行検証の区分（`reproduced` / `not_reproduced`）は
  変わらない。`tests/test_classify_findings.py` の既存のテストが、期待値を変えずに通る

文書:

- [ ] AC9: `docs/06-evidence.md` の区分の表の順 4 が、「反証を受けた単独の指摘だけが `support` を求められる」条件を
  書く。同じ文書の「走らせる順序」の節が、取り直しで揃わないときに印を外すことを書く。`docs/05-pool-and-convergence.md` の
  終了基準が、担当 1 者と起動し直した担当の指摘の数え方を書く。次の 2 つがそれぞれ 1 行以上を出す

  ```bash
  grep -n "反証を受けた" plugins/ndf/skills/cross-review/docs/06-evidence.md
  grep -n "印を外す" plugins/ndf/skills/cross-review/docs/06-evidence.md
  ```

## 受け入れ条件（P5: #478 使える担当だけで回す）

認証の確認を把握にする:

- [ ] AC10: ホスト `claude` で `kiro` の認証の確認が失敗する。`init` は終了コード 0 で状態ファイルを作る。
  `available_reviewers` は `["codex", "agy"]` で、`unavailable_reviewers` は `kiro` と失敗の理由を持つ。
  標準エラーに `kiro` を外したことが 1 行出る
- [ ] AC11: `--require-all` を付けると、AC10 と同じ状態で `init` が終了コード 1 で終わり、状態ファイルを作らない
- [ ] AC12: `NDF_SKIP_AUTH_CHECK=1` のとき、`available_reviewers` は母集合から `--exclude` で外した者を除いた全員に
  なる。確認を飛ばしたことが出力に残る

除外の引数:

- [ ] AC13: ホスト `claude` で `--exclude agy` を渡す。`agy` の認証を確かめず、`excluded_reviewers` が `["agy"]`、
  `available_reviewers` が `["codex", "kiro"]` になる。`--exclude agy --exclude kiro` と `--exclude agy,kiro` は同じ
  状態ファイルを作る
- [ ] AC14: 母集合の外（ホスト自身）を `--exclude` に渡す。または `--only codex --exclude codex` を渡す。
  どちらも `init` が終了コード 1 で終わり、状態ファイルを作らない

使える者の数で分ける:

- [ ] AC15: 使える者が 3 者のとき、`start-round` が返す担当は変更前と同じ輪番になる。4 つのホストとラウンド 1〜12
  の全組で、`review_assign(r, review_pool(host))` が変更前の `review_assign(r, host)` と一致する
- [ ] AC16: 使える者が 2 者（`codex` / `kiro`）のとき、ラウンド 1〜4 の `start-round` がすべて `codex kiro` を返す
- [ ] AC17: 使える者が 1 者のとき、`init` は終了コード 0 で終わり、観点が 1 つになることを 1 行出す。
  `start-round` はその 1 者を返す
- [ ] AC18: 使える者が 0 者のとき、`init` が終了コード 1 で終わり、状態ファイルを作らない

退行しない:

- [ ] AC19: `available_reviewers` を持たない状態ファイルは、この変更の前に始めた実行である。このとき `start-round` が
  返す担当は変更前と同じになる。`host` も持たない状態ファイルは `codex` / `agy` のままである
- [ ] AC20: `cross-refactoring` の担当と認証の関門が変わらない。`cross-refactoring/tests/test_assignment.py` の `assign` の期待値
  （`EXPECTED_FOR_CLAUDE`）が変えずに通る。`check_auth` は失敗が 1 件でもあれば中断させる

報告:

- [ ] AC21: `state.py report` が、使える者・`--exclude` で外した者・認証を通らなかった者・再開で変えた値を、
  1 行ずつ出す。外した者と認証を通らなかった者には理由を添える。4 つとも空でない状態ファイル（`resume_changes` が
  1 件以上）で 4 行とも出る

## 受け入れ条件（P5: #648 再開で引数を反映する）

明示的に渡した引数だけを反映する:

- [ ] AC22: `max_rounds: 12` の状態ファイルで、再開の `init` に `--max-rounds 20` を渡す。`max_rounds` が 20 になり、
  反映したことが `12 → 20` の形で 1 行出る。`resume_changes` には `field: "max_rounds"` / `from: 12` / `to: 20` の
  要素が 1 件積まれる。`--rotate-after` / `--verify-command` / `--verify-exit-code` も同じく反映される。
  `--verify-command` と `--verify-exit-code` は置き換え、継ぎ足さない
- [ ] AC23: 再開の `init` に `--max-rounds` などの引数を渡さない。このとき状態ファイルの次の 10 項目が変わらず、
  `max_rounds: 20` の状態ファイルが 12 へ戻らない

  | 区分 | 項目 |
  | --- | --- |
  | 進め方 | `max_rounds` / `rotate_after` / `verify_commands` / `verify_exit_codes` |
  | 担当 | `only` / `excluded_reviewers` / `available_reviewers` / `unavailable_reviewers` / `require_all` / `auth_skipped` |
- [ ] AC24: `only: null` の状態ファイルで、再開の `init` に `--only codex` を渡す。`only` が `codex` になり、次の
  `start-round` が `codex` だけを返す。記録を持つ過去のラウンドの `_round_reviewers` は記録のまま変わらない
- [ ] AC25: `only: "codex"` の状態ファイルで、再開の `init` に `--only none` を渡す。`only` が `null` になり、使える者が
  作り直される。`--exclude none` は `excluded_reviewers` を空にする
- [ ] AC26: 再開の `init` に `--exclude agy` を渡す。母集合から `agy` を除いた全員の認証を確かめ直し、
  `available_reviewers` から `agy` が消え、次の `start-round` が `agy` を返さない。担当に関わる引数（`--only` /
  `--exclude` / `--require-all`）を渡さない再開では、認証を確かめ直さない
- [ ] AC27: `host: "claude"` の状態ファイルで、再開の `init` に `--host codex` を渡す。`host` は `claude` のまま変わらず、
  反映しないことが 1 行出る。同じ値の `--host claude` では何も出ない

手順書の骨組み:

- [ ] AC28: `SKILL.md` と `docs/01-state-and-review.md` の骨組みが、`start-round` が返した担当をそのまま使う。
  起動・監視・取り込み・反証の担当を `$ONLY` で絞らない。2 ファイルに対する `grep -n 'ONLY' ` の出力が、`init` へ
  引数を渡す行と引数の説明の行だけになる

前のラウンドの検査:

- [ ] AC29: 前のラウンドが `verdict` を持たず、担当 `agy` + `kiro` の両者が `REQUEST_CHANGES` で、修正の記録が無い。
  このとき `start-round` は終了コード 5 で止まる。変更前は旧来の 2 者 `codex` / `agy` で数え、`codex` を結果なしと
  読んで通していた

文書:

- [ ] AC30: 次の 4 ファイルが、それぞれの内容を書く

  | ファイル | 書く内容 |
  | --- | --- |
  | `SKILL.md` | 引数の表と `argument-hint` に `--exclude` と `--require-all` がある。`--only` の説明から「デバッグ用」が消える |
  | `docs/05-pool-and-convergence.md` | 使える者の数による分岐と、除外 |
  | `docs/04-contracts.md` | 状態ファイルの 6 項目（`available_reviewers` / `excluded_reviewers` / `unavailable_reviewers` / `require_all` / `auth_skipped` / `resume_changes`） |
  | `docs/01-state-and-review.md` | 再開で反映する引数と、反映しない引数 |

## 受け入れ条件（両方）

- [ ] AC31: `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] AC32: 次の 3 つが終了コード 0 で終わる

  ```bash
  bash scripts/build-runtime-plugins.sh --check
  claude plugin validate .
  python3 scripts/check-skill-frontmatter.py
  ```

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | 母集合の 1 者が使えないことで、収束ループを開始できない状態にならない（AC10） |
| 性能・拡張性 | 認証の確認の回数は、新規の `init` で変更前を上回らない（除外した者は確かめない）。再開で増えるのは担当に関わる引数を渡したときだけで、母集合の最大 3 者である |
| 運用・保守性 | 担当が欠けたまま収束したことが、`report` の出力だけで分かる（AC21）。再開で反映しなかった引数が出力に出る（AC27） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 認証に失敗する CLI がある利用者 | `init` が止まらず、使える者で回る。従来の関門は `--require-all` で選べる |
| `--only` で回していた利用者 | 反証する担当がいない根拠付きの `major` が数えられ、最初のラウンドで収束しなくなる |
| 状態ファイルの形 | 最上位に 6 項目が増える。無い状態ファイルは従来の担当の決め方で読む |
| `init` の引数 | `--exclude` / `--require-all` が増え、`--only` が `none` を取る。既定値は変わらない（`--max-rounds 12` / `--rotate-after 8`） |
| 再開で修正の記録の無い前ラウンドがある実行 | 担当が `codex` / `agy` 以外のラウンドでも、前ラウンドの検査が止める（AC29） |
| `cross-refactoring` | 変わらない |

## 前提

| # | 前提 |
| --- | --- |
| 1 | P1〜P3（D-A）が先に `develop` へ入る。P4 / P5 の `state.py` の変更はその上に載せる。P4 の規則は、起動し直しの経路が証拠集約を通る形（P3）でも通らない形でも成り立つように決める |
| 2 | 担当は最大 2 者である（`review_assign` が 3 者以上から 1 者を外すため）。反証を返せる担当は、提案者を除いて最大 1 者になる |
| 3 | 再開の `init` の後、骨組みは必ず `start-round` で新しいラウンドを開く。開いたまま中断したラウンドの担当を書き換える必要は無い |
| 4 | 認証の確認コマンド（`AUTH_PROBES`）とその判定は変えない |

前提 3 の根拠: `SKILL.md` の骨組みは `init` の直後に `start-round` を呼ぶ。`start-round` は常に
`len(rounds) + 1` のラウンドを開く（`state.py` の `cmd_start_round`）。

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --with pytest pytest scripts/tests plugins/ndf -q` |
| 配布物の同期 | `bash scripts/build-runtime-plugins.sh --check` |
| 定義の検査 | `claude plugin validate .` と `python3 scripts/check-skill-frontmatter.py` |
| 手動確認 | P5 の後、`--exclude agy` を付けた `cross-review` を 1 本の Pull Request で回し、`agy` が 1 度も起動しないことを `report` で見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 担当の決め方は `plugins/ndf/scripts/lib/assignment.py`、認証は `lib/auth.py` に置く（`cross-refactoring` と共有する共通層）。判定は `state.py` に置き、骨組みは結果を使うだけにする |
| コーディング規約 | 状態ファイルを最小の形で組み、関数を直接呼んで確かめる（`AGENTS.md` の DO） |
| テスト戦略 | 既存の形（`tests/conftest.py` の `state_mod` で `state.py` を読み込み、GitHub を呼ぶ関数を差し替える） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、配布物の同期の検査 |
| 確認してから行う | `init` の既定を「認証の失敗で止める」から「使える者で回す」へ変えること（利用者の判断を仰ぐ） |
| 行わない | `cross-refactoring` の担当の決め方と `refactor_lib` の変更、監視と起動の変更 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 母集合 | 全ランタイム − ホストの 3 者（`review_pool(host)`） |
| 使える者 | 母集合から、`--exclude` で外した者と認証を通らなかった者を除いた一覧（`available_reviewers`） |
| 担当 | そのラウンドにレビューする者。`start-round` がラウンドへ記録する |
| 証拠集約の印 | `evidence_rounds` に載るラウンド番号。統合・実行検証・反証を通り切ったラウンドに付く |
| 反証を受けた指摘 | 提案者でない担当の有効な反証（`CRITIQUE_VERDICTS` のいずれか）が 1 件以上結ばれた指摘 |
| 単独の指摘 | `origin_runtimes` が 1 者の指摘 |
| 再開 | 状態ファイルが残り `final` が `null` のときの `init`（`_resume_from_state`） |

## 依頼（原文）

### #624

> `cross-review` を `--only codex` で 1 者だけにして回すと、codex が `REQUEST_CHANGES` で新しい指摘を投稿したラウンドでも、
> `judge` が収束と判定する（ndf 10.10.1、2026-09-13）。
>
> | 候補 | 内容 |
> | --- | --- |
> | 反証の担当がいないラウンドは絞り込まない | 反証が 0 件のときは全件を数える（`_evidence_completed` を偽に扱う） |
> | `--only` では intent に従う | 担当が 1 者なら、全員 pass（`round_passes`）だけを収束の条件にする |
> | 印を指摘単位で持つ | 証拠集約の印をラウンド単位ではなく指摘単位（反証の対象になったか）で持ち、反証の対象にならなかった指摘は絞り込まない。#583 の起動し直しの経路もまとめて塞がる |

### #583（収束の部分）

> **起動し直した担当の指摘も、反証を受けないまま数えられない。** 証拠集約の印（`_mark_evidence_round`）は、
> 1 回目の経路で反証を取り込んだ時点でラウンドに付く。起動し直した担当の指摘はその後に取り込まれるため、
> 反証の対象にならないまま `_new_finding_count` の絞り込みにかかり、単独の major は `insufficient_evidence` へ落ちる。

### #478

> **認証確認を「関門」から「導入状況の把握」へ変える。** 使える者でレビューし、使えない者は最初から数えない。
>
> 1. `init` で母集合の各ランタイムの認証を確かめ、**通った者の一覧を `state.json` へ記録する**（`available_reviewers`）
> 2. **明示的に外す手段を用意する**（例: `--exclude agy`）。**認証は通るが実行で落ちる担当を外す用途でも使う。**
> 3. 使える者の数で分岐する（3 者: 現行どおり / 2 者: 毎ラウンドその 2 者 / 1 者: 警告して 1 者 / 0 者: 失敗）
> 4. `review_assign()` は「**使える者が 3 者以上のときだけ 1 者を外す**」に変える
> 5. 全員揃っていることを要求したい運用のために `--require-all` を用意する
> 6. 完了報告に「このループに参加したのは誰か」を出す

### #648

> `/ndf:cross-review` を中断・再開すると、`state.py init` に渡した `--only` / `--max-rounds` / `--rotate-after` /
> `--verify-command` / `--verify-exit-code` / `--host` が**黙って無視される**。
>
> 再開経路でも、**明示的に渡された引数だけ**を状態ファイルへ反映する。反映できないと決めた引数（例えば `--host`）は、
> **渡されたら 1 行知らせる**。黙って捨てない。

（各 issue の本文から抜粋。全文は `gh issue view 624` / `583` / `478` / `648`）
