# 初期化・再開・提案

[← SKILL.md](../SKILL.md)

## 駆動の準備

スクリプトの位置は SKILL.md の「前提」のエントリポイントのブロック（解決のエントリポイント `resolve.sh` が返す絶対パス）で求め、
対象のリポジトリの中から打つ。Skill のディレクトリへは移らない。副コマンドを呼ぶ `rf` / `rf_eval` の定義は SKILL.md の
「実行」の先頭にある。**出力を `eval` する呼び出し**（`init` / `start-phase` / `merge-plan` /
`merge-implement` / `verify` / `final-gate`）は `rf_eval` を使う。`eval "$(rf ...)"` と
書くと `rf` はコマンド置換のサブシェルで動くため、`exit 4` はサブシェルしか終わらせず、
**中断したはずの進行がそのまま続く**。

## 初期化（`init`）

`init` が返す KEY=VALUE は次のとおり。

| 変数 | 内容 |
| --- | --- |
| `ID` | 状態ファイルの鍵（最初に初期化した Pull Request 番号） |
| `RUNTIMES` / `RUNTIMES_CSV` | 提案の参加者（既定は cross-review と同じ claude / codex / kiro とホスト） |
| `IMPL` / `IMPL_MODEL` | 実装担当（リファクタリング計画・テスト追加・実装・修正・最終ゲート修正）とそのモデル |
| `PHASE` | 再開の地点。駆動は終わった手順を飛ばす |
| `BUDGET_MINUTES` | 想定最大時間 |
| `WORKTREE_ROOT` / `WORK` / `TMP_DIR` | 作業ディレクトリと一時ディレクトリ |
| `REPO` / `HEAD_BRANCH` / `BASE_BRANCH` / `SCOPE` | 対象の情報 |
| `STRATEGY` / `STRATEGY_SOURCE` | 解いたテストの戦略とその根拠（`test.strategy` / `derived:test_duration` / `derived:test.suites` / `args`） |

`init` が内部で行うこと。

1. **引数のチェック** — `--budget-minutes` は 1 以上の整数でなければ止める（終了コード 4。
   argparse の 2 にしない）。廃止した 5 引数（`--max-test-rounds` / `--max-outer-rounds` / `--max-items-per-round` /
   `--max-fix-rounds` / `--test-timeout`）は `⚠ <引数> は廃止しました（#933）` の 1 行を
   出して値を使わない
2. **ホストの確定** — `--host` の明示指定を第一とし、未指定時のみ環境変数から推定する。
   推定できなければ**既定値を置かずに失敗する**
3. **参加者の確定** — 既定（claude / codex / kiro とホスト）に `--include` を足し、`--exclude` を
   除く。参加者プールに無い者を外す指定は中断せず、`ℹ` の 1 行を出して無視する
4. **実装担当の確定** — `--implementer` → ホスト（参加者にいれば）→ 参加者の先頭。
   名指しが参加者に無ければ止める。状態の `implementer` と `implementer_reason`
   （`named` / `host` / `first`）に残し、**再開しても変えない**。替わるのは同じ実行の中の振り替え（`reassigned`。#919）だけで、
   claude のアカウントへ振り替えたときはその名前を `implementer_account` に残す（空はアカウントを選んでいない）
5. **モデルの確定** — `--model <ランタイム>=<モデル>` を受け取り、実行の間は固定する
6. **書き込み用の作業ディレクトリ作成** — 作る前に Pull Request の状態を確かめ、閉じている・マージ済み・状態を判定できないときは作らずに止める（終了コード 4。判定は `pr_gate.init_refusal`。Draft かどうかは問わない）。`<root>/work/` に `origin/<head>` を detach で展開する。
   head ブランチは開発用の worktree で checkout 済みのことが多く、git は同じブランチを 2 つの
   作業ツリーへ checkout できないためである。push は `HEAD:<head>` で行う。既にある場合は `origin/<head>` へ**早送りで同期**
   してから使い、早送りできない・取得に失敗したときは中断する
7. **認証状態の確認** — 参加者の CLI を 1 つずつ確認し、通らない者を**外して続ける**。
   `--require-all` なら 1 者でも通らなければ中断する。着手前のテストより先に確認する
8. **Jev を使うかの判定** — 鍵（`AI_GATEWAY_API_KEY`）→ `NDF_JEV=0` でない → 対象が
   公開リポジトリ（`gh repo view --json visibility`）→ 疎通（固定の問い 1 回・10 秒）の
   順に確かめ、状態の `judge` に `{kind: "jev" | "runtime", reason, failures}` で残す。
   **1 度だけ判定し、再開で問い直さない**
9. **語彙と観点の受け渡し** — 兆候・手法・重要度・観点の集合を状態ファイルの `vocabulary`
   へ書く。テンプレートはここから**許容値をそのまま列挙する**
10. **リファクタリング計画の置き場所と生成物の同期コマンドの記録** — 既定は**対象の Pull Request の
    コメント 1 件**。書き出しと同期は push の直前にオーケストレーターが行う
11. **`--scope` のゲート**（下の節）
12. **テストの戦略の解決**（下の節）— 宣言（`.ndf/project.json` の `test`）と引数から戦略を解き、状態の `strategy` に写す。
    以後は変えない。解けなければ欠けたキーと直し方を出して止める（終了コード 4）
13. **着手前のテスト** — 戦略ごとに走らせる（`local-full` は全体テスト、`local-scoped-ci-whole` は `--scope` のテストの
    置き場所の範囲テスト、`round-only` は全体テストとラウンドテスト）。**落ちても止めない。** 落ちたテストは JUnit から読んで
    `baseline_test.existing_failures`（既存失敗）に書き、最終ゲートは既存失敗の外で新しく落ちたテストが無ければ通る。
    JUnit を読めなければ `null` と理由を書く。上限（`limits.init_test_timeout`）を超えたときだけ止める。
    実測の秒は `baseline_test.seconds`、宣言の所要は `whole_seconds`・`ci_seconds` に残し、時間の上限とバッファに使う
14. **測定の設定の読み込み** — 書き込み用の作業ディレクトリの `.ndf/code-metrics.json` を読み、
    既定の対応と重ねて状態の `code_metrics.config` に書く。`status` は `pending` から始まる。
    `limits.measure_timeout` もここで書く（下の「指標の測定」）

### 再開

同じ `init` を打ち直すと、前回の状態から再開する。

| 前回の状態 | 扱い |
| --- | --- |
| 無い | 新しく始める |
| 版 2 で `phase` が `done` | 新しく始める |
| 版 2 で終わっていない | **再開する。** `PHASE` を返し、駆動は終わった手順を飛ばす。各マージ処理はマージ処理済みの目印で冪等 |
| 旧い形（`schema` なし・`rounds` あり）で `final` が空 | **止める**（終了コード 4）。旧い版（v10.17.5 以前）で終えるか、状態ファイルを消して始め直す |
| 旧い形で `final` が入っている | 新しく始め、版 2 の形で作り直す |

| 再開で渡した引数 | 扱い |
| --- | --- |
| `--max-fix-rounds` / `--test-timeout` | 廃止を知らせて無視する（決定 24。修正は締め切りまで、テストの上限は予算から導く） |
| `--baseline-test` / `--round-test` | 知らせるだけ（戦略は `init` で解いたまま） |
| `--budget-minutes` | **リファクタリング計画の前**（`phase` が `propose` か `plan` でリファクタリング計画が無い）だけ置き換え、上限の表（`limits`）を組み直す。後は知らせるだけ。採用の件数・実装の終わり・バッファはリファクタリング計画の時点の予算で固定されている |
| `--implementer` | 知らせるだけ（置き換えない） |
| `--exclude` / `--include` / `--require-all` | 参加者を作り直す。**実装担当が外れたら**、リファクタリング計画の前は決め方を当て直し、リファクタリング計画の後は止める |
| そのほか状態に載る引数 | 状態と違えば「反映しない」と知らせる |

**想定最大時間は cross-refactoring が動いていた時間で数える**。リファクタリング計画を取り込んだ後の再開では、
状態を書く前に止まっていた時間を測り、まだ来ていない締め切りをその分だけ後ろへずらす。計画し直さない。`init` の打ち直しと、
駆動の打ち直し（耐久ワークフローを続ける前に `refactor.py catch-up <ID>` を 1 度打つ）が同じ処理を通る。

| 項目 | 決まり |
| --- | --- |
| 止まっていた時間 | 再開の時刻 − 最後の動きの時刻。最後の動きの時刻は作業ディレクトリ（`.cross_refactoring/`）の直下のファイルのうち最も新しい更新時刻 |
| 心拍のファイル | `cross-refactoring-rf<ID>-alive`。CLI の監視（`monitor.py --alive-file`。plan・add-tests・implement・fix）とテストの実行が、待つ間 15 秒ごとに更新時刻を今にする。出力の無い CLI やテストの稼働を止まっていた時間に数えないためである |
| ずらす締め切り | 上限の表のテストの追加の終わり・実装の終わり・直しの試行の打ち切り・最終ゲート修正の打ち切り・打ち切りの後の取り消しの締め切りのうち、最後の動きの時刻より後に来るものだけ。中断の前に過ぎていた締め切りは開き直さない |
| ずらさないとき | 止まっていた時間が余裕（`0.05·B`）以下（`within_margin`）、計画の前、最終ゲートに入った後 |
| 旧い状態ファイル | 止まっていた時間を記録しない版で計画した状態（`pause` が無い）はずらさず、そのことを 1 度だけ記録する（`legacy`） |
| 記録 | `pause.events[]`（`at` / `last_activity_at` / `seconds` / `shifted` / `reason`）と、和の `pause.seconds` / `pause.shifted_seconds`。報告に「止まっていた時間」の 1 行、実行の行に `paused_seconds` が出る |

2 回中断すれば、止まっていた時間を足した分だけずれる。続けて 2 度打っても、1 度目の保存で最後の動きの時刻が今になるため、
ずれるのは 1 度だけである。

### `--scope` のゲート

`--scope` にテストの置き場所が含まれているかを見る。含まれていないと、テストの追加で足すテストが範囲外になり、
その項目は必ず取り消される。コマンドの実行集合は見ない（範囲テストはテンプレートの `{paths}` に置き場所が直に入る）。

- テストの置き場所は、名前（`tests` / `spec` / `__tests__` などのディレクトリ名と、`test_*` / `*_test.*` などのファイル名）→
  配下の 1 階層にテストの名前のディレクトリがある → 配下の追跡ファイルにテストの名前の形がある（テストを本体と同じ場所に置く
  `src/**/*.spec.ts` の構成）の順に判定する。`--scope` は範囲の設定なので、**まだ無いディレクトリを指すことがある**

### テストの戦略

宣言（`.ndf/project.json` の `test`。[project-analysis.md](../../development-workflow/references/project-analysis.md) の P2）と
引数から、共通層 `scripts/lib/test_strategy.py` の `resolve` が決める。supervise の `new` も同じ関数を使う。
**コマンドの語・ランナーの名前・前置きは見ない。** コマンドへの加工は `{paths}`（引用の外の、空白で区切った 1 語）を
シェルの引用で守った対象の並びへ置き換えることだけで、コマンドはどれもシェルで 1 本ずつ走らせる。suite の種別
（`kind`。`test` か `lint`）は宣言か `--test-kind` だけで決まり、テンプレートの語から推測しない。

| 入力（上から順に当てる） | 戦略 | 根拠 |
| --- | --- | --- |
| `--round-test` が `{paths}` を含まない | `round-only`（ラウンドテスト = その値。全体テストは `--baseline-test` か宣言の suite の `command`） | `args` |
| `--round-test` か `--baseline-test` が `{paths}` を含む | 宣言の `test.strategy` があればそれ、無ければ `local-full`（テンプレート = その値。宣言の同じ種別の suite の `scope_command` を置き換える） | `args` |
| `--baseline-test` だけが `{paths}` を含まない | `round-only`（ラウンドテスト = 全体テスト = その値。項目ごとに全体を走らせると知らせる） | `args` |
| 宣言の `test.strategy` | その値 | `test.strategy` |
| 宣言の suite に `scope_command` が 1 つも無い（テストの suite があればテストの suite だけで見る） | `round-only`（ラウンドテスト = suite ごとの `command`） | `derived:test.suites` |
| 所要 w（`test_duration`。`ndf-record` → `ci-junit` → `ci-steps` の順）> 600 秒で、宣言の `ci` が読める | `local-scoped-ci-whole` | `derived:test_duration` |
| それ以外 | `local-full` | `derived:test_duration` |
| 宣言の `test` が無い・不明 | 止める（`.ndf/project.json の test が無い（か不明: <理由>）。/ndf:development-workflow の手順 0 で解析するか、--round-test を渡す`） | — |

| 戦略 | 範囲テスト | 着手前 | 危険フラグの全体テスト | 最終ゲート |
| --- | --- | --- | --- | --- |
| `local-full` | `scope_command` の `{paths}` へ `test_targets` | 全体テストを手元で | 手元で 1 度 | 手元で全体テスト（使い回しあり） |
| `local-scoped-ci-whole` | 同上（コンテナ越しでよい） | `--scope` の置き場所の範囲テストだけ | 走らせず最終ゲートへ寄せる（`whole_test.deferred`） | push 済みの HEAD のチェック（`test.ci.check` か必須のチェック）を `limits.ci_wait_timeout` まで待つ |
| `round-only` | ラウンドテストをそのまま | 全体テストとラウンドテスト | 手元で 1 度 | 手元で全体テスト |

テンプレートの不備（`{paths}` が無い・`--filter={paths}` や `'{paths}'` のように引用の外の 1 語として立っていない）と、
`local-scoped-ci-whole` なのに `scope_command` を持つ suite が無いときは、理由を出して止める。

**全体テストは種別ごとに、宣言の `command` が引数のテンプレートより先に効く。** 宣言に `command` が無ければ、テストのテンプレートは
`{paths}` を `.` にしたもの、静的解析のテンプレートは `{paths}` を `--scope` のパスで埋めたものを全体テストにする。静的解析の
テンプレートの `{paths}` は `.` にしない（`shellcheck .` のようにディレクトリを渡すと、変更と無関係に落ちる）。

| 種別 | 範囲テストの範囲 | 着手前・最終ゲート | 危険フラグの全体テスト |
| --- | --- | --- | --- |
| テスト（`test`） | 項目の `test_targets` | 上の戦略の表のとおり | 走らせる |
| 静的解析（`lint`） | 項目のコミットが変えたファイルのうち suite の `paths`（接頭辞か glob）に当たるもの。検証のたびに組む | どの戦略でも手元で全体テストを走らせ、suite ごとの成否を `baseline_test.suites` に残す | 走らせない |

テストの種別の suite が無い戦略では、`--scope` のテストの置き場所の検査とテスト整備ラウンドを行わず、そのことを
計画と報告の「行わなかったこと」に書く。

**起動の失敗で止まる。** 着手前・項目の範囲テスト・テスト整備ラウンド・最終ゲートのどこかでテストのコマンドを起動できない
（終了コード 126 / 127）と、落ちたとは扱わず、項目の状態を変えず取り消しもせずに中断（終了コード 4）する。記録は状態ファイルの
`launch_failure` に、報告には `⛔ 起動の失敗（<手順>）: <コマンド> — <理由>` の行が出る。コマンドを直して再開すると、
次の未検証の項目から続く。

### 作業ディレクトリの構成

```text
<worktree-base>/<owner>--<repo>/rf<PR>/
├── work/              # 書き込み用。origin/<head> を detach で展開する
├── <参加1>/           # 読み取り用。--detach
├── <参加2>/           # 読み取り用。--detach
├── <参加3>/           # 読み取り用。--detach
└── work/.cross_refactoring/   # 状態ファイル / プロンプト / 結果 / ログ
```

- `<worktree-base>` の解決順は `cross-review` と同じ（`NDF_WORKTREE_BASE` >
  `<システム tmpdir>/ndf-worktrees`）
- **同一ブランチを 2 つの作業ディレクトリへ checkout できない**という git の制約が
  あるため、提案用は必ず `--detach` にする
- 読み取り専用でも分ける理由は 3 つ。提案の担当がテストを実行できること、
  テスト実行が生む生成物が競合しないこと、各担当へ渡す作業領域を各自の
  ディレクトリ 1 つに収められること

## 手順書となる Skill の配置

```bash
"$SCRIPTS/prepare-worktrees.sh" "$ID"
```

実装担当は `refactoring` Skill の手順どおりに直すことが前提だが、**参加ランタイムに
NDF が入っている保証はない**。そこで作業ディレクトリの準備に続けて、手順書が使える
状態かを確認し、無ければ配置する。

### 配置先

| ランタイム | 作業ディレクトリ内の配置先 |
| --- | --- |
| claude | `<worktree>/.claude/skills/<name>/` |
| codex | `<worktree>/.agents/skills/<name>/` |
| kiro | `<worktree>/.kiro/skills/<name>/` |
| agy | `<worktree>/.agents/skills/<name>/` |

**4 者とも担当ごとの worktree の中へ置く。** 利用者のホームと対象リポジトリ本体へは
一切書き込まない。配置先の名前はランタイムごとに違うが、扱いは同じである
（agy は codex と同じ `.agents/skills` を読む）。どのランタイムでも**兆候と手法の
語彙を読ませないと提案が語彙外になって全件降格する**ため、プロンプトの明示パスで
読ませる。

配置するのはテストの追加・実装・修正で実際に使う 3 つに絞る。

| Skill | 用途 |
| --- | --- |
| `refactoring` | 手順の本体（兆候の語彙 / 手法カタログ / 現状固定テスト / 表現の判断） |
| `tdd-cycle` | テストが乏しい経路で現状固定テストを先に書く手順 |
| `quality-gates` | 「直し終わった」と言える条件の判定 |

`ndf-policies` は**配置しない**。git 運用やコミット規約といったリポジトリ運用の方針で
あり、対象リポジトリの運用と食い違う可能性が高い。必要な規約（項目とコミットを `Item-Id` で結ぶこと、
コミットトレーラー、`--force` 禁止など）はプロンプト側で明示する。

### 守ること

- **配置は作業ディレクトリの中だけで完結させる。** 利用者のホーム（`~/.claude/` /
  `~/.kiro/`）と対象リポジトリ本体には一切書き込まない
- 既に `<name>/SKILL.md` があれば**それを使い、上書きしない**（利用者の設定を壊さない）
- `SKILL.md` は無いが中身のあるディレクトリがあれば、**触らずに中断する**。
  利用者が作りかけている Skill や補助ファイルを消さないため。配置してよいのは
  空ディレクトリと、存在しないパスだけである
- シンボリックリンクにしない（作業ディレクトリを消せば残らない状態にする）
- ホスト側にも見つからない Skill があれば**初期化を失敗させる**。黙って劣化した状態で
  走らせない
- `kiro-cli agent set-default` と `agent create` を呼ばない。前者は
  `~/.local/share/kiro-cli/data.sqlite3` に保存される**マシン全体の設定**を奪い、
  後者はエディタを開いて非対話実行が止まる

### 差分に混入させない方法

配置した Skill と `.cross_refactoring/` が Pull Request の差分に現れないよう、
**配置したディレクトリ自身へ全件無視の `.gitignore` を置く**。

`.git/worktrees/<name>/info/exclude` は使えない。現行の git は作業ディレクトリごとの
`info/exclude` を読まず、共通の `.git/info/exclude` を見る（実測で確認）。そちらへ書くと
**対象リポジトリ本体を書き換える**ことになり、「配置は作業ディレクトリの中だけで完結
させる」という前提を破る。

#### 除外の中に置いた手順書は読める

全件無視を効かせたまま、**agy が配置した手順書を開けることを実測で確かめた**。
読み取り側の除外を無効にする設定は置かない。

### 読ませ方（明示パスを必ず書く）

Skill を配置しても、**本文を読むかどうかはランタイムによって違う**。

| ランタイム | 配置しただけで本文を読むか | 明示パス指定で読むか |
| --- | --- | --- |
| claude | 読む | — |
| codex | 読む | — |
| kiro | **読まない** | **読む** |

**Kiro は配置しただけでは SKILL.md 本文を読まない。** `description` に書かれた語彙と
モデルの一般知識だけで「それらしい」応答を返すため、**一見すると手順に沿っているように
見えて中身が違う**。実測では「テストが乏しければ現状固定テストを先に書く」という中核の
手順を飛ばした。

そのため `launch-cli.sh` は**プロンプトへ Skill の明示パスを必ず書く**。claude と codex
には冗長だが、3 ランタイムで同じプロンプトを使うため常に書く。

読ませても**従うとは限らない**。同じ課題を与えた実測では、現状固定テストの追加数が
claude 17 本 / codex 1 メソッド / kiro 0 本と揃わなかった。最後の砦は
[マージ処理の機械検証](02-plan-and-implement.md#マージ処理のチェック)である。

## 提案

```bash
"$SCRIPTS/prepare-worktrees.sh" "$ID" sync "$(git -C "$WORK" rev-parse HEAD)"
rf measure "$ID"                  # 指標を 1 回だけ測る。測れなくても 0（4 だけが中断）
rf_eval start-phase "$ID" propose            # PHASE_TIMEOUT = 提案の枠の終わりまでの残り + 余裕
for a in $RUNTIMES; do "$SCRIPTS/launch-cli.sh" "$a" propose "$ID"; done
"$LIB/monitor.py" "$ID" --agents "$RUNTIMES_CSV" --tmp-dir "$TMP_DIR" \
    --stem-template "{agent}-propose-rf$ID" --phase propose \
    --timeout "$PHASE_TIMEOUT" --stall-timeout "$PHASE_TIMEOUT"
rf merge-proposals "$ID"          # 2 = 改善候補 0 件（最終ゲートへ）
```

**提案は参加者の全員が 1 度だけ、並行して行う。** 1 者でも提案を終えれば残りの提案で続ける。全員が結果を残さなかったときだけ
`refactor.py reassign "$ID" propose --seats <起動した者>` を打ち、返った `PROPOSERS` で提案を集め直す（3 なら止まる。
規則は [02 の「結果を残さなかった担当の振り替え」](02-plan-and-implement.md#結果を残さなかった担当の振り替え)）。
claude の別のアカウントで集め直したときは、その名前を `proposer_accounts` に残し、提案の起動は席が実装担当と同じでもここからアカウントを引く。振り替えで実装担当のランタイムが外れたら、外した後の参加者から実装担当を選び直して `IMPL` で返す。 提案とリファクタリング計画は想定最大時間の中の枠で
行い、枠の終わり（提案 `開始 + 0.20·B`、リファクタリング計画 `開始 + 0.30·B`）を監視の上限にする
（決定 24。式は [02 の「締め切り」](02-plan-and-implement.md#締め切り)）。

### 指標の測定（`measure`）

**提案の前に 1 回だけ、対象範囲のコードの指標を測り、参加者の全員が同じファイルを読む。**
測るのはオーケストレーターで、参加者の CLI には測らせない（値の出所に LLM の申告を使わない）。

1. `git ls-files -- <--scope>` で追跡されたファイルを集め、拡張子で言語に分ける。表に無い拡張子は
   「言語を判定しなかったファイル」に数えるだけにする
2. 言語ごとにツールを起動する（言語の名前の順・1 回に 500 本まで）。Python は Ruff（循環的複雑度・
   分岐・文・引数・return の数）と complexipy（認知的複雑度）の 2 つを 1 つの測定ツール
   `ruff-complexipy` として測り、関数の範囲は `ast` で数える。JavaScript・TypeScript・PHP・Go ほかは
   lizard（循環的複雑度と関数の行数）で測る。shell は測らない（`unsupported_language`）
3. 言語ごとの測定の後に、重複を 1 回ずつ探す。Python は pylint 同梱の `symilar`、ほかの言語
   （shell を含む）は jscpd で、最小 8 行である
4. 指標のファイル `$TMP_DIR/code-metrics-rf<ID>.md` を書く。言語ごとの節（見出しにツールの名前と版）に、
   本体とテストを分けて、複雑な関数・ファイルの行数・重複の箇所の上位を載せる。要約の件数は全件から数える
5. 提案のプロンプトに「指標」の節が入り、このファイルの絶対パスが載る。ファイルが無ければ節ごと入らない

**起動は `uvx --from <パッケージ>==<版>`（jscpd は `npx -y jscpd@<版>`）で版を固定する。** ランナーが
無いときだけ PATH のコマンドを使い、その版（`--version`）とランナー `path` を記録する。対象リポジトリの
依存へは何も足さず、作業ディレクトリへも書かない（Ruff は `--no-cache`、complexipy と jscpd の出力は
一時ディレクトリ）。

**測れなくても止めない。** 理由は識別子で残し、指標のファイル・リファクタリング計画・完了報告で同じ値を使う。

| 識別子 | いつ |
| --- | --- |
| `tool_missing` | ランナーもツールのコマンドも無い |
| `unsupported_language` | 言語の表にあるが既定のツールが無い（shell） |
| `disabled` | 宣言がその言語を `null` にした |
| `tool_failed` | 起動できない・正常でない終了コード（uvx / npx の取得の失敗を含む） |
| `unreadable_output` | 正常に終わったが出力を読めない |
| `timeout` | 測定に使える時間を過ぎた・残りが無かった |
| `too_many_files` | 重複検出に渡すパスの合計が 256 KiB を超えた |

実行の単位の `code_metrics.status` は `written`（ファイルを書いた。測れた言語が 0 でも）・`disabled`
（`--no-code-metrics`）・`no_language`（判定できた言語が無い）・`write_failed`（書けない）のどれかになる。
`pending` 以外なら 2 回目の `measure` は測らない。提案を始めた後（`phases.propose.started_at` がある）も測らない。
旧い状態ファイル（`code_metrics` が無い）の再開は、提案を始める前なら `init` が記録を作って測る。

**時間は提案の枠から割く。** 使える時間は `min(0.05·B, 0.5·max(0, 提案の枠の終わり − 今))` で、言語ごとの
測定と重複検出が分け合う。提案の枠の終わりは動かない（[02 の「締め切り」](02-plan-and-implement.md#締め切り)）。

**言語ごとのツールは `.ndf/code-metrics.json` で置き換える。** 書いた言語だけが置き換わり、ほかは既定のまま測る。

```json
{"version": 1, "tools": {"python": "lizard", "shell": null}}
```

値は `"ruff-complexipy"`（`python` だけ）・`"lizard"`・`null`（その言語を測らず、重複も探さない）。形が違えば
（JSON でない・知らない鍵・知らない言語かツール）宣言の全体を使わずに既定で測り、`declaration_invalid` と
理由を指標のファイルと計画に残す。重複の最小の行数と版は宣言で変えない。

**参加者は `evidence` に根拠の値を書ける。** 鍵は `cc` / `cognitive` / `lines` / `functions` /
`max_function_lines` / `duplicate_lines`、値は数である。書かなくても、形が悪くても見送られない（採否と
順位に使わない）。

### 観点を並べて観点ごとに探させる

テンプレートは観点（重複・責務の混在・分岐の表し方・名前・依存の向き・テストの書きにくさ・
データの形・大きさ）を列挙し、**観点ごとに見直して**探すよう指示する（AC5）。一覧が
無いと、参加者は目についた兆候に偏る（#917 では `long_method` が 10 件）。**合計の件数の
上限は示さない。** 示すと、出す前に絞られる。観点の呼び名と代表の兆候は
`refactoring/references/vocabulary.md` の「観点」の節が持ち、`vocabulary.VIEWPOINTS` が読んで状態ファイル経由でテンプレートへ列挙する。
観点の識別子を `smell` に書いた提案は、語彙外として見送らずに代表の兆候へ写す（重複排除の鍵を兆候に揃える）。
兆候・手法・観点の識別子は重ならず、重なれば読み込みで止まる。

### 語彙は列挙して渡す

`smell` / `technique` / `severity` は**識別子を列挙して渡す**。手順書の見出しは日本語なので、
「語彙に限定する」とだけ書くと、読んだ側が日本語を語彙と解釈する（実測では提案 4 件が
全て日本語で返った）。語彙外の値を含む提案は `vocabulary` の理由で見送る。

### 提案の直前に読み取り用を同期する

読み取り用の作業ディレクトリは HEAD へ同期してから提案させる。同期しないと、
**消えたコードに対する提案**が返る（実測）。HEAD が変わっていなければ何も起きない。

## 改善候補の切り出し（`merge-proposals`）

1. 各参加者の `<ランタイム>-propose-rf<ID>-result.json` を読む。1 者が欠けた・壊れた JSON を
   返したときは、その者の提案を無かったものとして続ける（`proposed` に `null` / 件数が残る）
2. 鍵（`path` + `symbol` + `smell`）が同じ提案を 1 件へまとめる。賛同した者を足し、
   `rationale` / `plan` は長い方、重要度は高い方、見積りの行数は大きい方を採る。`evidence` は
   知らない鍵と数でない値を落とし、既にある鍵を残して無い鍵だけ足す
3. 語彙外を含む提案は `vocabulary`、しきい値未満は `threshold` で見送る
4. 残りを（賛同した者の数、重要度）の降順に並べ、`path` + `symbol` の組の単位で
   **上位 30 組**を取る。取った組の中は**上位 3 件まで**リファクタリング計画へ渡す（最大 90 件）。
   外れた組の提案と、組の中の 4 件目以降は `rank` で見送る

**組で切る**（決定 17）。件数で切ると、意味の上で同じ提案が枠を占め、リファクタリング計画の
中で統合された後に空いた枠を埋められない。「同じ変更か」は同じ組の中でしか問わない
ため、組で切れば統合で組の数は減らない。

**意味の上で同じ提案かはここで決めない。** リファクタリング計画の中で Jev か実装担当が問う
（[02-plan-and-implement.md](02-plan-and-implement.md)）。
