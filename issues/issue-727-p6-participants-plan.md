# cross-review / cross-refactoring: 参加する CLI が 1 者でも使えないと収束ループを開始できず、再開で渡した引数が黙って無視される → 使える者だけで開始し、cross-review は毎ラウンド 2 席を確保し、再開で渡した引数は反映されるか反映しないと知らされる（実装計画 P6: 共通層と cross-review / #727 #687 #478 #648）

## 関連リンク

- 親 issue #727、子 issue #687 #478 #648（#664 は 2 本目の Pull Request が扱う）
- 設計: [issue-727-687-478-664-648-design.md](issue-727-687-478-664-648-design.md)（決定 20 件。用語の対応表はこの文書の識別子の引き先）
- 要求: [issue-727-687-478-664-648-requirements.md](issue-727-687-478-664-648-requirements.md)（受け入れ条件 AC1〜AC50）
- 契約: [issue-727-687-478-664-648-contracts.md](issue-727-687-478-664-648-contracts.md)（状態ファイル・引数・関数の形）
- 設計 Pull Request: #782（2026-09-19 マージ）

## モード

`standard`。収束ループの初期化の振る舞いを変え、複数モジュール（共通層と cross-review）にまたがる。

## 目的と非目的

達成したい状態:

- 参加する CLI のどれか 1 者が導入・認証されていなくても、cross-review の初期化が使える者で始まり、使えない者と理由が状態ファイルに残る
- cross-review の各ラウンドに 2 席が確保される（使える者 → ホスト → 同じランタイムの 2 つ目）
- 中断した収束ループを引数を変えて再開したとき、渡した引数が反映されるか、反映しないことが知らされる
- 使える者の決定・席の埋め方・再開の反映の 3 つの規則が共通層に 1 か所ずつ入り、2 本目の Pull Request（cross-refactoring 側）がそのまま呼べる

やらないこと（2 本目の Pull Request が行う）:

- cross-refactoring の初期化・担当・表示・引数・文書の変更
- 従来の確認（`check_auth`）・適用専用の母集合（`impl_pool`）・従来の席と適用の割り当て（`review_assign` / `assign`）の削除
- リポジトリの根の `CLAUDE.md` の書き換え
- 起動した後に分かる使えなさで担当を自動的に外す仕組み（設計の決定 18）

## 前提

- 前提 1: 設計文書の決定 20 件は変えない。実装で決めると設計が残した 3 件（監視の比較の寄せ方・出力の文言・テストの置き場所）は、この計画の「実装で決めたこと」に書き、設計文書の「未確認のまま残ること」の表を同じ Pull Request で更新する
- 前提 2: 並行する束が同じ状態の部品（`state.py`）を触る。G3（PR #791）は副コマンドの登録関数の分割と再開の経路の分割、G2（PR #790）は指摘の分類を触る。この Pull Request は既存の行を書き換える量を最小にし、足す形で書く。競合は後からマージする側が解く
- 前提 3: ホストが席に入ったときの自分の Pull Request への投稿の扱い（`is_own_pr`）は、この Pull Request では実機で回さず、検査の持ち場か運用で確かめる。確かめていないことを Pull Request 本文の残リスクに書く

## 受け入れ条件

要求文書の AC1〜AC6、AC8〜AC30、AC44〜AC46、AC48〜AC50 をそのまま使う。検証手段は要求文書の「検証手段」と設計文書の「テスト設計」の表にある。AC45・AC46・AC48（子 issue の再現手順）は手元で実行して結果を issue のコメントに残す。

## ドメイン用語

設計文書の「用語の対応表」を使う。この文書で追加する語は無い。

## 不変条件

- 使える者の並びは、ランタイムの固定の順（`ALL_RUNTIMES`）を保つ
- 使える者が 3 者のときの席は、変更前の輪番と同じ値になる
- この変更の前に始めた実行の状態ファイルは書き換えずに読める
- 再開で渡さなかった引数は、状態ファイルの値のまま残る

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `state.py init` の引数 | `--exclude` / `--include` / `--require-all` / `--no-require-all` を足す。`--only` が `none` を取る。`--max-rounds` / `--rotate-after` の既定を未指定へ | 追加のみ。骨組みは値があるときだけ渡す形へ変える |
| `state.py read-result` の担当の引数 | 4 つの名前の選択肢から、席の名前の形の検査へ | 従来の 4 つの名前はそのまま通る |
| 起動スクリプトの第 1 引数 | ランタイム名から席の名前へ | 従来の名前はそのまま通る |
| 監視の位置引数の選択肢 | 4 つの名前と `both` から、席の名前の形と `both` へ | 同上 |
| 状態ファイル | 最上位に `participants` と `resume_changes` が増える。`rounds[].reviewers` と `rounds[].<席>` の鍵に席の名前が入りうる | 項目が無いときの読み方を契約文書の「移行」が決める。既存のファイルは書き換えない |
| 共通層の関数 | 6 つを新設。旧関数は残す | 追加のみ |

## 修正対象

共通層:

- `plugins/ndf/scripts/lib/auth.py`
- `plugins/ndf/scripts/lib/assignment.py`
- `plugins/ndf/scripts/lib/statefile.py`
- `plugins/ndf/scripts/lib/monitor.py`
- `plugins/ndf/scripts/lib/README.md`（関数の一覧の行）
- `plugins/ndf/scripts/tests/test_auth_probe.py`（書き直し）、`test_lib_participants.py`（新設）、`test_lib_resume_args.py`（新設）、`test_lib_assignment.py`（追記）

cross-review:

- `plugins/ndf/skills/cross-review/scripts/state.py`
- `plugins/ndf/skills/cross-review/scripts/launch-reviewer.sh` / `critique.sh` / `critique-round.sh` / `wait-review.sh` / `measure.py`
- `plugins/ndf/skills/cross-review/SKILL.md` / `docs/01-state-and-review.md` / `docs/04-contracts.md` / `docs/05-pool-and-convergence.md`
- `plugins/ndf/skills/cross-review/tests/test_state_review_pool.py`（追記）、`test_state_round_guard.py`（追記）、`test_state_resume_args.py`（新設）、`test_seat_names.py`（新設）、`test_skill_layout.py`（追記）

cross-refactoring（テストだけ）:

- `plugins/ndf/skills/cross-refactoring/tests/test_assignment.py`（席の埋め方のテストを追記。従来の席の割り当てのテストは 2 本目の Pull Request が消す）

文書:

- `issues/issue-727-687-478-664-648-design.md`（「未確認のまま残ること」の 3 行）

## タスク分解

受け入れ条件の番号は要求文書のものである。

### Task 1: 止めない確認を共通層に足す

- **対象ファイル:** `lib/auth.py`、`scripts/tests/test_auth_probe.py`
- **変更内容:** 確認コマンドを走らせて結果だけを返す関数（`probe_auth(runtimes, *, info, env=None)` → `(結果, 飛ばしたか)`）を足す。確認コマンド・未認証の文言・時間切れの秒数・飛ばす環境変数は変えない。従来の確認は残す。既存テストは従来の確認を使っているため、止めない確認のテストへ書き直す（従来の確認のテストは 2 本目の Pull Request が消すまで残してよい）
- **満たす受け入れ条件:** AC5（確認コマンドを呼ばない部分）、AC6
- **進め方:** 失敗するテスト（コマンドが見つからない / 時間切れ / 終了コード非 0 / 未認証の文言 / 成功 / 飛ばし）→ 最小実装 → 従来の確認と重なる走らせ方を 1 つの内部関数へ寄せる

### Task 2: 使える者の解決・席の埋め方・適用の輪番・席の名前を共通層に足す

- **対象ファイル:** `lib/assignment.py`、`scripts/tests/test_lib_participants.py`（新設）、`scripts/tests/test_lib_assignment.py`、`skills/cross-refactoring/tests/test_assignment.py`
- **変更内容:** 既定の参加者の表（`DEFAULT_REFACTOR_RUNTIMES = ("codex", "kiro")`）、母集合の既定（`refactor_pool(host)`）、参加者の記録（`Participants` データクラス。`pool` / `included` / `excluded` / `available` / `unavailable` / `probe_skipped` / `require_all` と `to_state()`）、使える者の解決（`resolve_participants`。順序は設計文書の表の 6 段）、席の形（`SEAT_PATTERN`）と席の名前の解釈（`seat_runtime`）、席の埋め方（`review_seats(round_no, available, fallback)`。規則の表は docstring に置く。設計の決定 20）、適用の輪番（`impl_assign(round_no, participants)`）を足す。モジュールの docstring の「役割ごとに母集合が違う」の表は、2 本目の Pull Request で母集合が 1 つになるまで残す
- **満たす受け入れ条件:** AC1〜AC5、AC8〜AC13、AC34 のうち適用の輪番の値
- **進め方:** 失敗するテスト → 最小実装 → 整理。AC8 は変更前の席の割り当て（`review_assign`）を期待値に使う

### Task 3: 再開の反映を共通層に足す

- **対象ファイル:** `lib/statefile.py`、`scripts/tests/test_lib_resume_args.py`（新設）
- **変更内容:** 反映の表の 1 行（`ResumeField(arg, key, mode)`。`mode` は `replace` / `notify`）と、再開の反映（`apply_resume_args(state, args, spec)` → 標準エラーへ出す行の一覧）を足す。「反映する」は未指定でない値を状態へ書き `resume_changes` に `{at, field, from, to}` を積む。「知らせる」は状態と違うときだけ行を返す。値が同じなら行も記録も出さない。予約語 `none` の扱い（1 者指定は `null`、一覧は空）は呼び出し側が引数を正規化してから渡す形にし、この関数は値をそのまま比べる
- **満たす受け入れ条件:** AC25〜AC29 の共通層の部分
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 4: cross-review の新規の初期化を共通層へ載せ替える

- **対象ファイル:** `cross-review/scripts/state.py`、`cross-review/tests/test_state_review_pool.py`
- **変更内容:**
  - 初期化の引数: `--only` の型を 4 つの名前か `none` へ、`--exclude` / `--include`（カンマ区切り・繰り返し可・`none`）、`--require-all` / `--no-require-all`（既定は未指定）を足す。`--max-rounds` / `--rotate-after` の既定を未指定へ変え、新規の経路で 12 / 8 を置く。既存の引数の行はそのまま残し、足す行だけを加える（G3 が副コマンドの登録関数を分けるため）
  - 使える者の解決（`_resolve_reviewers(host, args)`）: 母集合の既定と使える者の解決を呼び、使える者が 2 者に満たなければホストを止めない確認で確かめて埋め合わせ（`fallback`）を決める。1 者指定があればホストを確かめず埋め合わせは空。割り当ての失敗は終了コード 1 へ写す。従来の確認の相手を決める関数と 1 者指定の検査（`_auth_targets` / `_validate_only`）はこの関数で置き換える
  - 初期状態: `participants`（埋め合わせを含む 8 項目）と `resume_changes: []` を書く。`max_rounds` / `rotate_after` は既定を埋めた値
  - 標準エラーの行: 母集合と使える者の 1 行、通らなかった者は 1 者 1 行、埋め合わせは 1 行（文言は「実装で決めたこと」）
- **満たす受け入れ条件:** AC14〜AC20
- **進め方:** 失敗するテスト（止めない確認を差し替えて新規の初期化を呼び、状態ファイルの有無・終了コード・標準エラーを見る）→ 最小実装 → 整理。既存テスト `test_auth_check_covers_only_the_reviewers_that_run` と `test_init_rejects_an_only_outside_the_pool` は新しい形へ書き直す

### Task 5: 担当の読み出しを記録から先に見る順へ変え、前ラウンドの検査に記録の担当を渡す

- **対象ファイル:** `cross-review/scripts/state.py`、`cross-review/tests/test_state_review_pool.py`、`cross-review/tests/test_state_round_guard.py`
- **変更内容:** 担当の読み出し（`_round_reviewers`）を「ラウンドの記録 → 1 者指定 → 参加者の記録から席の埋め方 → ホストの輪番（変更前と同じ値）→ `codex` / `agy`」の順にする。前ラウンドの検査（`_guard_previous_round`）は `prev["reviewers"]`（無ければ担当の読み出し）を結果なしの判定と通過の判定へ渡す
- **満たす受け入れ条件:** AC17（席が 2 つ返る部分）、AC18・AC19（`start-round` の返り値）、AC22、AC23
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 6: 席の名前を結果の受け口と起動・監視・計測に通す

- **対象ファイル:** `cross-review/scripts/state.py`（結果の受け口の引数）、`launch-reviewer.sh` / `critique.sh` / `critique-round.sh` / `wait-review.sh`、`lib/monitor.py`、`cross-review/scripts/measure.py`、`cross-review/tests/test_seat_names.py`（新設）、既存の監視のテスト
- **変更内容:**
  - 結果の受け口（`read-result`）の担当の引数を、席の名前の形の検査（`seat_runtime` を型に使う）にする。通らなければ argparse の終了コード 2
  - 起動スクリプト 2 本（`launch-reviewer.sh` / `critique.sh`）の先頭の検査を席の形（`^(claude|codex|agy|kiro)(-[2-9])?$`）にし、CLI は `${SEAT%%-*}` で選んで共通の起動スクリプト（`launch-cli.sh`）へ渡す。結果ファイルの stem は席の名前で組む（変更なし）。`critique-round.sh` は席の名前をそのまま `critique.sh` へ渡すだけで変更は無い（確かめて記録する）。`wait-review.sh` は使い方の説明の担当名を席の名前に直す
  - 監視（`lib/monitor.py`）: 席の名前からランタイムを引く内部関数を 1 つ置き、CLI 固有の分岐 3 か所（`codex` の sentinel 2 か所、`claude` の標準出力の検査 1 か所）をその関数で包む。位置引数の選択肢を席の形と `both` を受ける型へ替える
  - 計測（`measure.py`）: 担当の名前の一覧で数える箇所を、記録の鍵のうち席の形に一致するものを数える形にする
- **満たす受け入れ条件:** AC21
- **進め方:** 失敗するテスト（結果の受け口を `claude-2` で呼ぶ / 起動スクリプトを共通の起動スクリプトを差し替えて呼び、渡った CLI 名と stem を見る / 監視の位置引数に `kiro-2` を渡す）→ 最小実装 → 整理

### Task 7: cross-review の再開で引数を反映し、担当に関わる引数で参加者を作り直す

- **対象ファイル:** `cross-review/scripts/state.py`、`cross-review/tests/test_state_resume_args.py`（新設）
- **変更内容:** 反映の表（`max_rounds` / `rotate_after` / `only` / `verify_commands` / `verify_exit_codes` は反映する。`host` は知らせる）を置き、再開の経路（`_resume_from_state`）に引数を渡して再開の反映を呼ぶ。1 者指定・外す者・足す者・全員を要する指定のどれかを渡した再開では、渡さなかった引数を状態ファイルの値（`participants.included` / `excluded` / `require_all`、`only`）で補って使える者の解決をやり直し、`participants` を書き換えて `resume_changes` に 1 件積む。失敗したら状態ファイルを書き換えずに終了コード 1。既存の関数は引数を 1 つ足し、本体の既存の行は動かさず、反映の呼び出しを 1 ブロック足す形にする（G3 の分割と競合する行を減らす）
- **満たす受け入れ条件:** AC25〜AC29
- **進め方:** 失敗するテスト（状態ファイルを置いた作業ツリーで初期化を呼び、状態ファイルと標準エラーを見る。一部の引数だけを渡す組み合わせを含む）→ 最小実装 → 整理

### Task 8: 完了報告に「参加した者」の節を足す

- **対象ファイル:** `cross-review/scripts/state.py`、`cross-review/tests/test_state_review_pool.py`
- **変更内容:** 「PR 履歴」の後に「参加した者」の節を出す関数を 1 つ足し、完了報告（`cmd_report`）から 1 行で呼ぶ。行は 7 つ（母集合 / 使える者 / 外した者 / 足した者 / 確認を通らなかった者（理由つき）/ 席の埋め合わせ / 再開で変えた値）。参加者の記録が無ければ「使える者: 記録なし」、確認を飛ばした印が真なら「確認を通らなかった者: 確認を飛ばした（`NDF_SKIP_AUTH_CHECK`）」。既存の行は書き換えない（G3 が完了報告の結末の節を触る）
- **満たす受け入れ条件:** AC24
- **進め方:** 失敗するテスト → 最小実装

### Task 9: 骨組みと文書を席の規則と新しい引数に合わせる

- **対象ファイル:** `cross-review/SKILL.md`、`docs/01-state-and-review.md`、`docs/04-contracts.md`、`docs/05-pool-and-convergence.md`、`cross-review/tests/test_skill_layout.py`
- **変更内容:** 骨組み（Step 0 / 2 / 2.5）を契約文書の「手順書の骨組み」の形にする。初期化へは値のある引数だけを渡し、起動・監視・取り込み・反証の担当は `$REVIEWERS` / `$REVIEWERS_CSV` を使う。引数の表と `argument-hint` に 3 つの引数を足し、`--only` から「デバッグ用」を消す。`docs/05` に使える者の解決・席の埋め方（3 者以上 / 2 者 / 1 者 / 0 者）・足す者と外す者・確認が把握になったことを書く。`docs/04` に `participants` と `resume_changes` と席の名前の形を書く。`docs/01` に再開で反映する引数と反映しない引数の表を書く。`test_skill_layout.py` に「`ONLY` を含む行は初期化へ渡す行と引数の説明の行だけ」の検査を足す
- **満たす受け入れ条件:** AC30、AC44
- **進め方:** テスト（`grep` の行数）→ 文書の書き換え。文書は `markdown-writing` の規約で書く

### Task 10: 設計文書の「未確認のまま残ること」を更新し、配布物と検査を通す

- **対象ファイル:** `issues/issue-727-687-478-664-648-design.md`、`lib/README.md`、生成物
- **変更内容:** 実装で決めた 3 件（監視の比較の寄せ方・出力の文言・テストの置き場所）を「未確認のまま残ること」の表から「決めた」へ書き換える。共通層の README の関数の行を足す。`bash scripts/build-runtime-plugins.sh` で生成物を揃え、AC49・AC50 のコマンドを通す。AC45・AC46・AC48 を手元で実行し、結果を issue のコメントに残す
- **満たす受け入れ条件:** AC45、AC46、AC48、AC49、AC50
- **進め方:** コマンドの実行と結果の記録（テスト駆動の対象ではない）

## 実装で決めたこと

設計文書が実装に委ねた 3 件を決める。

| 項目 | 決めたこと | 理由 |
| --- | --- | --- |
| 監視の CLI 固有の検査を席の名前に通す形 | 監視（`lib/monitor.py`）に席の名前からランタイムを引く内部関数を 1 つ置き、比較 3 か所をその関数で包む。席の形に合わない名前はそのまま返す | 監視は cross-refactoring も使い、担当名を任意の骨格で受ける経路がある（`test_monitor_generic_stem.py`）。形に合わない名前で失敗させると、その経路が壊れる |
| 出力の文言 | 反映した行は `↻ <項目>: <旧> → <新>`、知らせる行は `ℹ --<引数> は再開では反映しません（状態: <値> / 指定: <値>）`、通らなかった者は `⚠ <名前> を担当から外しました（<理由>）`、埋め合わせは `⚠ 使える者が <数> 者のため、席を<相手>で埋めます（観点が減ります）` | 既存の初期化の出力が `↻` / `ℹ` / `⚠` の印で始まる形に揃える。項目名と値を含めることは設計が決めている |
| テストの置き場所 | 設計文書の「テスト設計」の表のとおり。席の埋め方のテストは cross-refactoring の割り当てのテスト（`test_assignment.py`）に置く | 変更前の席の割り当てのテストが同じファイルにあり、AC8 の期待値をその場で引ける |

## 影響範囲

- cross-review の初期化の既定の振る舞い: 確認を通らない者が 1 者でもあれば止める形から、使える者で回す形へ（従来の形は `--require-all`）
- cross-review の席: 使える者が 2 者に満たないとき、ホスト → 同じランタイムの 2 つ目で埋める。従来は 1 者で回すか失敗していた
- 状態ファイル・結果ファイルの名前に、席の名前（`claude-2` など）が現れうる。読む側（監視・計測・完了報告）はこの Pull Request で追随する
- cross-refactoring: 共通層の関数が増えるだけで振る舞いは変わらない。旧関数を残すため既存のテストは通る

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 状態の部品（`state.py`、4470 行）を G2 / G3 と並行して触る | 既存の行の書き換えを最小にし、足す形で書く。着手の前に G3 の差分を読んだ（副コマンドの登録関数の分割・再開の経路の 3 分割・完了報告の結末の節）。触る関数を初期化・担当の読み出し・前ラウンドの検査・結果の受け口の引数・完了報告の呼び出し 1 行に限る |
| 監視の比較を包む変更が、任意の骨格で担当名を受ける経路を壊す | 席の形に合わない名前はそのまま返す。既存の監視のテスト（`test_monitor_*`）を毎タスクで通す |
| 席の名前が読み手に届かない箇所が残る | 設計文書の「構成要素」の受け口 10 か所を 1 つずつ検査に対応づけ、`grep -n 'claude|codex|agy|kiro' scripts/` で分岐と選択肢を洗い直す |
| 従来の確認のテストが止めない確認の導入で意味を失う | 2 本目の Pull Request で消すまで残す。この Pull Request では止めない確認のテストを足す |

## 切り戻し手順

- Pull Request を revert すれば戻る。状態ファイルの新しい項目（`participants` / `resume_changes`）は、旧版の読み手が読まない鍵のため、途中の実行を旧版で再開しても壊れない
- 席の名前を持つ状態ファイル（`claude-2` の鍵）を旧版で読むと、その席の結果は数えられない。旧版へ戻すときは実行を新しく始める

## 完了の定義

- [ ] AC1〜AC6、AC8〜AC30、AC44〜AC46、AC48〜AC50 を満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る（監視の環境変数を export していないシェルで実行する）
- [ ] AC50 の 6 つのコマンドが終了コード 0 で終わる
- [ ] 設計文書の「未確認のまま残ること」が更新されている
- [ ] Draft の Pull Request を `develop` 宛に出した
