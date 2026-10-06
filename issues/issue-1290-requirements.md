# #1290: 担当の選び方と振り替えの規則を `lib/assignment.py` に持たせる

正は課題の本文（#1290）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 何をまとめるか

担当（ランタイム × モデル）の選び方と振り替えの規則を `plugins/ndf/scripts/lib/assignment.py`（担当の選択の層）の 1 か所に持たせる。担当の選択は「どの CLI を担当に入れるか（利用可能な参加者の決め方）」と「結果を残さなかったときどう振り替えるか」の 2 つの問いからなる。

| 課題 | 状態（2026-10-06） | この課題での扱い |
| --- | --- | --- |
| https://github.com/devbasex/ai-plugins/issues/919 | 閉じた（完了。ndf 10.17.60） | 振り替えの規則は `assignment.after_no_result` に入った。呼ぶ側（`cross-review` の `judge.py`、`cross-refactoring` の `reassign.py` と `assignee_launch.py`）はその答えに従い、`monitor_outcome.py` は理由の語彙と起動結果の読み書きだけを持つ。**残りは無い** |
| https://github.com/devbasex/ai-plugins/issues/760 | 閉じた（やらない。2026-10-01、N7 構想・効果が未測定） | worker の輪番と統計による選択は扱わない。`supervise_lib/worker_steps.py` の `call_worker` は計画の `runtime` を読むだけで、選ぶ判断を持たないため移すものも無い |
| https://github.com/devbasex/ai-plugins/issues/461 | 開いている。**この課題の要求へ取り込んだ** | 利用可能な参加者の決め方（`lib/auth.py` の `probe_auth`）が認証だけを見るため、モデルを引けない・更新トークンが失効した CLI が担当に入り、ラウンドを潰す。この課題に残る統合の範囲の中心 |

**残る統合の範囲は #461 の 1 つである。** 確認を通った CLI が実行で落ちる穴は、担当に入れる側（確認）と、落ちた後の側（理由の語彙と振り替え）の両方にある。#919 が後者の規則の置き場を作ったので、この課題は前者を同じ層へ寄せ、後者の語彙に #461 の 2 つの形を足す。

# 要求と受け入れ条件

## 依頼（原文）

> #1290。この要求へ次を取り込む: #461（cross-review / cross-refactoring の init が共有する CLI の確認 lib/auth.py の probe_auth が認証だけを見るため、モデルを引けない・更新トークンが失効した CLI が担当に入りラウンドを潰す）。どちらも「どの CLI を担当に入れ、結果を残さなかったときどう振り替えるか」という担当の選択の層（lib/assignment.py）の問題として 1 本の設計で扱う。#461 の受け入れ条件も同じ写し（issues/issue-1290-requirements.md）へ含め、#461 の本文には「## 受け入れ条件」の節を書き、#1290 の要求へ取り込んだことを書く。#1290 の子 #760・#919 は閉じ済みなので、残る統合の範囲を本文で確かめて書き直す。人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

> #461 のコメント（2026-09-07、利用者）: codexがupdateしたこと、新モデル gpt-6-astraがリリースされて従来バージョン(gpt-5.5)が対象外になったための模様。こうした場合はdefaultモデルを選択するようにすること。

> #1290 の元の修正方針: 散った選び方と振り替えの規則を `lib/assignment.py` へ統合し、呼ぶ側（`worker_steps.py`・`monitor_outcome.py`・`cross-review` / `cross-refactoring`）からは判断を移す。

## 目的

- `cross-review` / `cross-refactoring` の `init` と `external-ai.py check` が「使える」と判定した CLI は、実際にモデルを 1 回引ける。モデルを引けない CLI と更新トークンが失効した CLI は、起動の前に担当から外れ、外れた理由が `init` の出力に出る
- 確認の後で同じ形の失敗が起きても（実行の途中で更新トークンが失効するなど）、起動し直しで 1 回分を失わずに、`after_no_result` の規則で振り替えて先へ進む
- 「どの CLI を担当に入れるか」と「結果なしの後どうするか」の判断が、`lib/assignment.py`（と、確認のコマンドの定数を持つ `lib/auth.py`）の外に残らない

## 現状（2026-10-06、`develop` の ed7ebf4f で確かめた）

| 場所 | 今の振る舞い |
| --- | --- |
| `plugins/ndf/scripts/lib/auth.py:20-27` の `AUTH_PROBES` | claude `claude auth status` / codex `codex login status` / agy `agy models` / kiro `kiro-cli whoami`。どれもモデルを引かない |
| `plugins/ndf/scripts/lib/assignment.py:159` の `resolve_participants` | `probe` の `ok` だけで `available` と `unavailable`（名前 → 理由）に分ける |
| 呼び手 | `cross-review/scripts/review_lib/participants.py:295`、`cross-refactoring/scripts/refactor_lib/commands/setup.py:155`、`external-ai/scripts/external-ai.py:103` の 3 か所が同じ `auth.probe_auth` を使う |
| `plugins/ndf/scripts/lib/monitor_patterns.py:46` の `EARLY_ERROR_FATAL` | 行頭の `HTTP/1.1 401` などだけ。codex の `unexpected status 404 Not Found: The model ... does not exist` と `refresh token was revoked` は当たらない |
| `plugins/ndf/scripts/lib/monitor_outcome.py:44` の `REASONS` | 2 つの形は `missing` に畳まれる |
| `plugins/ndf/scripts/lib/assignment.py:347` の `NO_RELAUNCH_REASONS` | `usage_limit` だけ。`missing` は 1 度同じ担当で起動し直してから振り替える |

## 前提

決められない点を、後から成否を判定できる文にした。誤っても局所的に直せるため、人へ問わずに前提として置く。

- 前提 1: 「使えるか」の確認は 1 つだけにする。`init`（2 つの Skill）と `external-ai.py check` は同じ確認を通る（#852 の決定。2 つ目の確認を作らない）
- 前提 2: 確認の「最小の呼び出し」は、担当を起動するときと同じモデルの指定（`cross-refactoring` の `--model`、無ければ CLI の設定のまま）で、短い固定の問いを 1 回投げ、応答が返るかだけを見る。応答の中身は判定に使わない
- 前提 3: 確認は参加者ごとに並行に走らせ、1 者の時間切れは今の `AUTH_PROBE_TIMEOUT`（120 秒）のままにする
- 前提 4: モデルを引けないときに CLI の既定のモデルへ切り替えるのは、**モデルを利用者が引数で指定していない**（CLI の設定ファイルから来ている）ときだけである。`--model <ランタイム>=<モデル>` で明示したモデルが引けないときは切り替えず、その CLI を理由つきで外す（明示の指示を黙って上書きしない）
- 前提 5: 既定のモデルへの切り替えは、その実行の起動の引数で行い、利用者の CLI の設定ファイル（`~/.codex/config.toml` など）を書き換えない（共通原則 C6）
- 前提 6: 更新トークンの失効とモデルを引けないことは、同じ実行の中で同じ担当を起動し直しても解けないものとして扱う（`NO_RELAUNCH_REASONS` に入る）。#461 のコメント（2026-09-07）のとおりモデルの 404 は恒常とは限らないが、数十秒後の起動し直しで解ける形は観測されていない
- 前提 7: 更新トークンの失効で claude を別のアカウントへ振り替えることはしない。アカウントへの振り替えは今のとおり `usage_limit` のときだけである（#919 の規則の行 3）
- 前提 8: `NDF_SKIP_AUTH_CHECK` を立てたときは、今と同じく確認を 1 回も呼ばない（最小の呼び出しも飛ばす）

## 対象範囲

含む:

- 利用可能な参加者の確認を、認証に加えてモデルを 1 回引くところまで広げる（`lib/auth.py` の確認と、それを読む `lib/assignment.py` の `resolve_participants`）
- 確認を通らなかった理由の分類（未認証・更新トークンの失効・モデルを引けない・時間切れ・コマンドが無い）と、`init` / `check` の出力への表示
- 設定から来たモデルを引けないときの、CLI の既定のモデルでの確認のし直しと、その実行の担当の起動に既定のモデルを使うこと
- 監視の早期の致命検知の語彙（`monitor_patterns.py`）と理由の語彙（`monitor_outcome.py` の `REASONS`）に、モデルを引けない形と更新トークンの失効の形を足すこと
- 新しい 2 つの理由を `assignment.NO_RELAUNCH_REASONS` に入れ、`after_no_result` が起動し直さずに振り替えること

含まない:

- worker の輪番と統計による選択（#760。やらないと判断した）
- 計画の worker（`supervise_lib/worker_steps.py`）の結果なしの振り替え。`runtime` を計画が固定で書く形のままにする
- #919 で入った振り替えの規則の行の順と、振り替え先の選び方の変更
- 母集合の既定（`DEFAULT_RUNTIMES`）の変更
- 利用者の CLI の設定ファイルの書き換え、ログインのし直しの自動化

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 参加者を解決した（母集合 ∪ 足す者 − 外す者） | `init` / `check` の開始 | 名前の矛盾で終了コード 1（今のまま） | — |
| E2 | 参加者ごとに認証を確かめた | E1 | 通らなければ理由 `unauthenticated` などで外す（E5） | E1 |
| E3 | 参加者ごとにモデルを 1 回引いた | E2 が通った | 理由 `model_unavailable` / `auth_expired` / `timeout` を付けて E4 か E5 へ | E2 |
| E4 | CLI の既定のモデルで引き直した | E3 が `model_unavailable` で、モデルが引数の明示でない（前提 4） | 通らなければ E5 | E3 |
| E5 | 利用可能な参加者と使えない者（理由つき）を記録した | E2〜E4 の結果 | `--require-all` で欠けがあれば止める（今のまま） | E2〜E4 |
| E6 | 席・実装担当を選んだ | E5 | 利用可能な参加者が 0 なら止める（今のまま） | E5 |
| E7 | 担当を起動した（E4 を通った者は既定のモデルで） | E6 | 起動の失敗は今の監視の経路 | E6 |
| E8 | 担当の `err.log` に致命の文言が出て、監視が理由を付けて止めた | 実行の途中のモデルの 404・更新トークンの失効 | 文言が語彙に無ければ今の `missing` | E7 |
| E9 | 結果なしの規則が振り替え先を決めた | E8 の理由 | 振り替え先が無ければ今の中断 | E8 |
| E10 | 振り替えの事実と理由を状態ファイルと出力に残した | E9 | — | E9 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 利用可能な参加者 | 母集合から外す者を引き、確認を通った者。状態ファイルの `available` |
| 最小の呼び出し | 担当を起動するときと同じモデルの指定で、短い固定の問いを 1 回投げて応答が返るかを見る確認 |
| 既定のモデル | 利用者の設定も引数の指定も無いときに CLI が使うモデル |

## 受け入れ条件

### 担当に入れる前の確認（#461）

- [ ] AC1: 認証は通るがモデルを引けない CLI（`codex login status` は終了コード 0 で、`codex exec` が `404 Not Found: The model ... does not exist` を出す偽の CLI）は、`cross-review init` と `cross-refactoring init` で `available` に入らず、`unavailable` にその CLI と理由 `model_unavailable` が入る
- [ ] AC2: 認証の状態確認は通るが更新トークンが失効した CLI（`refresh token was revoked` または `refresh_token_invalidated` を出す偽の CLI）は、AC1 と同じく外れ、理由 `auth_expired` が入る
- [ ] AC3: AC1・AC2 で外れた CLI について、`init` の出力に `❌ <ランタイム>: <理由>` の 1 行が出て、`err.log` を開かなくても理由が読める。通った CLI は `✅ <ランタイム>` の行で、認証とモデルの両方を確かめたことが分かる
- [ ] AC4: `external-ai.py check <ランタイム>` は AC1・AC2 の CLI で失敗を返し（終了コードが成功と異なる）、理由を出力する。`init` と `check` は同じ関数を通る
- [ ] AC5: モデルを CLI の設定から引いていて（`--model` の明示が無い）そのモデルを引けないとき、CLI の既定のモデルで引き直し、通れば `available` に入る。状態ファイルの参加者の記録に、そのランタイムで既定のモデルを使うことと元のモデル名が残り、`init` の出力にも 1 行出る
- [ ] AC6: AC5 で既定のモデルへ切り替えた CLI を担当として起動するとき、起動の引数が既定のモデルを指し、実測のモデル（`models.observed_model`）が設定のモデルと違う値で記録される
- [ ] AC7: `--model <ランタイム>=<モデル>` で明示したモデルを引けないときは、既定のモデルへ切り替えずにその CLI を理由 `model_unavailable` で外す（前提 4）
- [ ] AC8: 確認は利用者の CLI の設定ファイルを書き換えない（確認の前後で `~/.codex/config.toml` などの内容が同じ）

### 担当に入れた後の失敗（#461 と #919 の規則の接点）

- [ ] AC9: 担当の `err.log` に AC1 の 404 の文言が出たとき、監視は結果ファイルを待たずに止め、理由 `model_unavailable` を結果に残す。AC2 の文言なら理由 `auth_expired` を残す（#461 の本文に載る 3 つの実物の行をそのまま入力にしたテストで確かめる）
- [ ] AC10: 理由 `model_unavailable` と `auth_expired` の結果なしは、`after_no_result` が同じ担当で起動し直さず、振り替え先があれば振り替え、無ければ中断する（`relaunch` の記録が残らない）
- [ ] AC11: `cross-review` で、ラウンドの席の 1 つが AC9 の形で止まったとき、利用可能な参加者が 3 者以上なら同じラウンドの中で別のランタイムへ振り替わり、judge の出力に `REASSIGNED` の行と理由が出て、`final = error` にならない（駆動 `drive.py` を通す結合テスト）
- [ ] AC12: `cross-refactoring` で、提案担当か実装担当が AC9 の形で止まったとき、#919 の振り替えと同じ経路で続く（提案担当・実装担当それぞれ 1 本の結合テスト）

### 規則の置き場所

- [ ] AC13: 「その CLI を担当に入れるか」（確認の結果から `available` / `unavailable` を決める）と「既定のモデルへ切り替えるか」の判断は `lib/assignment.py` が持ち、`participants.py`・`setup.py`・`external-ai.py` に同じ判断の分岐が無い。確認のコマンドと文言の定数は `lib/auth.py` に、致命の文言は `lib/monitor_patterns.py` に、理由の語彙は `lib/monitor_outcome.py` にある
- [ ] AC14: 子 #919 の受け入れ条件のテスト（`plugins/ndf/scripts/tests/test_lib_assignment.py` と 2 つの Skill の振り替えの結合テスト）がこの変更の後も通る

### 退行しないこと

- [ ] AC15: 認証もモデルも通る CLI だけの実行では、席・実装担当・状態ファイルの既存の欄・終了コードが今と同じである（既存のテストがそのまま通る）
- [ ] AC16: `NDF_SKIP_AUTH_CHECK` を立てたとき、確認のコマンドも最小の呼び出しも 1 回も走らない
- [ ] AC17: 理由が新しい 2 つと `usage_limit` 以外の 1 度目の結果なしは、今と同じく同じ担当で 1 度だけ起動し直す
- [ ] AC18: 未認証の CLI（今の `UNAUTHENTICATED_MARKERS` に当たるもの）は今と同じく外れ、最小の呼び出しを走らせない

### 課題

- [ ] AC19: #461 が、この課題の実装の Pull Request のマージで閉じる条件（AC1〜AC12）を満たす

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | 確認を通った CLI がモデルを引けずに落ちて 1 ラウンドを失う事象（#461 の 4 件の観測の形）が、`init` で外れるか、起動し直し無しの振り替えで続くかのどちらかになり、状態ファイルの手編集が要らない |
| 性能・拡張性 | `init` の確認にかかる時間を、参加者ごとの秒数として出力か状態ファイルに残す。参加者ごとに並行に走らせ（前提 3）、全体の時間は最も遅い 1 者の秒数 + 数秒に収まる。実装の Pull Request で 3 者（claude / codex / kiro）の実測の秒数を示す |
| 運用・保守性 | 担当に入れる判断と振り替えの判断の正本は `lib/assignment.py` の 1 か所で、2 つの Skill と `external-ai.py check` が同じ関数を通る。確認で外れた理由と振り替えの理由が、後から状態ファイルで数えられる |
| セキュリティ | 最小の呼び出しは、担当の起動と同じ環境（`claude_accounts.account_env` の作る `CLAUDE_CONFIG_DIR` など）だけを子へ渡し、トークンを NDF が読まない。確認の出力の理由（stderr の先頭）にトークンが載らないよう、今の秘密の伏せ字（`secret_redact.py`）を通す |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `init` と `external-ai.py check` の出力の行が、理由を含む形になる（`✅` / `❌` の先頭は変えない）。監視の理由の語彙に `model_unavailable` と `auth_expired` が増える（足すだけ）。終了コードは変わらない |
| データ | 状態ファイルの参加者の記録に、既定のモデルへ切り替えた事実と元のモデル名、確認の秒数の欄が増える。既存の状態ファイルは欄が無いものとして読める（移行は不要） |
| 既存の振る舞い | `init` が CLI ごとに 1 回モデルを呼ぶため、確認の時間と少量の利用枠を使う。モデルを引けない・更新トークンが失効した CLI が担当から外れる。実行の途中の同じ形は起動し直しを経ずに振り替わる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4`（`.ndf/project.json` の `test`）。確認と担当に入れる判断は `plugins/ndf/scripts/tests/` の単体テスト（偽の CLI を PATH に置く）、致命の語彙は #461 の実物の行を入力にした単体テスト、2 つの Skill は監視の結果を偽装した結合テスト |
| 静的解析・型検査 | `python3 scripts/check-skill-frontmatter.py`、`claude plugin validate .`（終了コード 0） |
| 手動確認 | リリース後テストで、実際の claude / codex / kiro に対する `cross-review init` の出力に、認証とモデルの両方の確認と参加者ごとの秒数が出ることを見る。モデルを引けない形は #478 のリリース後テストと同じ shim で作り、`init` で外れることを見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 判断は `plugins/ndf/scripts/lib/assignment.py`、確認のコマンドは `lib/auth.py`、致命の文言は `lib/monitor_patterns.py`、理由の語彙は `lib/monitor_outcome.py`。Skill ごとに同じ役割の関数を置かない（プロジェクト MVV の Value 6・#1142） |
| コーディング規約 | `AGENTS.md` の最小限のコード実装。`.md` の文言を照合するテストを書かない。呼ぶ CLI のコマンド（各 CLI の最小の呼び出しの形・既定のモデルの指定の形）は、書く前に実行して終了コードと出力を確かめる（`AGENTS.md` のベストプラクティス） |
| テスト戦略 | 判断は純粋な関数の単体テストで全分岐を見る。外部 CLI は PATH に置いた偽の CLI で置き換え、実物の文言（#461 の観測）を出させる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 全体テストの実行、既存の出力の行の先頭と終了コードの維持、外れた理由と切り替えの記録 |
| 確認してから行う | アカウントの選び方（`claude_accounts.py`）を変えること（C1 に近いため設計の承認で人が見る）。確認が呼ぶ CLI の引数で認証の情報を扱う形にすること |
| 行わない | 利用者の CLI の設定ファイルの書き換え（C6）、ログインのし直しの自動化、トークンを子へ渡すこと、#760 の輪番・統計、母集合の既定の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 各 CLI の「最小の呼び出し」の形（codex は `codex exec`、claude は `claude -p`、kiro は `kiro-cli chat --no-interactive`、agy は `agy -p` のどれで、どの引数か）と、1 回の所要・消費の実測 | 設計（`design`）。書く前に実行して確かめる | 設計 PR |
| 設定ファイルがモデルを持つとき、CLI の既定のモデルを起動の引数でどう指すか（codex の設定を上書きする引数があるか、既定のモデル名をどこから得るか）。CLI ごとに手段が無ければ、その CLI は AC5 の対象から外して理由つきで外す | 設計（`design`）。手段が無い CLI が出たら設計の承認で人が確かめる | 設計 PR |
| 確認の結果を同じ実行の再開で使い回すか、再開のたびに確かめ直すか | 設計（`design`） | 設計 PR |
| `auth_expired` と `model_unavailable` の文言の照合を、行頭に限るか（`EARLY_ERROR_BENIGN` の誤検知の除外との兼ね合い） | 設計（`design`） | 設計 PR |
