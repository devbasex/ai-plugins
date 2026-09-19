# #727 / #687 / #478 / #664 / #648: 使える者を共通層が決め、担当が揃わなくても 2 席で回す

設計は [issue-727-687-478-664-648-design.md](issue-727-687-478-664-648-design.md) にある。この文書は
「何を満たすか」だけを扱う。

**この文書は、既存の設計 [issue-624-478-648-requirements.md](issue-624-478-648-requirements.md) の
P5（AC10〜AC30）を置き換える。** 対応は末尾の「既存の受け入れ条件との対応」にある。P4（#624）は
#732 の設計が持つ。既存の設計文書の本体は触らない。

## 目的

- 参加する CLI のどれか 1 者が使えなくても、収束ループ（cross-review / cross-refactoring）を開始でき、
  使える者だけで回る。使えない者と理由は出力と状態ファイルに残る
- cross-review は、使える者が 2 者に満たなくても、各ラウンドに 2 席を確保する。席の埋め方の規則は
  1 つで、両 Skill が共有する共通層が持つ
- cross-refactoring の既定の参加者は codex / kiro / ホストの 3 者になり、agy は既定から外れる。
  外す・戻す手段は引数で持つ
- 中断した収束ループを、引数で進め方を変えて再開できる。反映しなかった引数は出力で分かる。
  この規則も共通層が 1 か所で持つ

## 前提

| # | 前提 |
| --- | --- |
| 1 | cross-refactoring のレビュー工程は #436 で消えており（Step 7 の `cross-review` が担う）、`assign()` が返すレビュー担当は状態ファイルへの記録と表示にしか使われない |
| 2 | 使える者の確認は、認証の確認コマンド（`AUTH_PROBES`）のままである。モデルを引く最小の呼び出しへ替える判断は #461 が持つ。この変更が作るのは、確認の結果で使える者を決める入口である |
| 3 | 再開の `init` の後、骨組みは必ず `start-round` で新しいラウンドを開く。開いたまま中断したラウンドの担当を書き換える必要は無い |
| 4 | G2（#732）が同じ `state.py` の `_classify_finding` を、G3（#729）が `_read_review_result_file` / `_record_no_result` / `report` を、G5（#730）が投稿の経路を触る。この変更が触る節は `init` / 再開 / `_round_reviewers` / `start-round` / `read-result` の担当名の受け口 / `report` の参加者の節である |
| 5 | 同じランタイムの 2 つの CLI プロセスは、別の作業文脈を持てば独立した意見として扱う（#687 の利用者の指示） |

## 対象範囲

含む:

- 共通層 `lib/assignment.py`: 既定の母集合（Skill ごと）、使える者の解決、席の埋め方、適用の輪番、席の名前
- 共通層 `lib/auth.py`: 止めない確認（`probe_auth`）。1 件の失敗で `die` する `check_auth` を消す
- 共通層 `lib/statefile.py`: 再開で明示的に渡した引数だけを状態へ重ね、反映しない引数を知らせる
- cross-review の `init`（新規と再開）、`start-round` の担当、`read-result` と起動スクリプトの席の受け口、`report`、
  `SKILL.md` と `docs/`（01 / 04 / 05）
- cross-refactoring の `init`（新規と再開）、`start-round` と適用の輪番、`report` / 改修計画の表示、`SKILL.md` と `docs/01`
- `CLAUDE.md` の cross-refactoring と cross-review の節
- テスト（共通層・両 Skill）

含まない:

| 扱わないもの | 理由 |
| --- | --- |
| 反証する担当がいない指摘の数え方（#624、既存の P4） | #732（G2）の設計が持つ |
| 確認を「モデルを引く最小の呼び出し」へ替えること | #461。確認コマンドの所要と形の実測が要る。この変更の共通層は確認の手段を差し替えられる形にする |
| 起動した後に分かる使えなさ（利用上限・モデルの 404）で担当を自動で外すこと | #729（G3）が理由の語彙を持つ。この変更は利用者が `--exclude` で外し、再開で反映できる入口までを作る |
| 監視の上限・結末の語彙・投稿の重なり | G3 / G5 の範囲 |
| cross-refactoring の適用ラウンドの取り込みと担当の交代 | #728（G4）の範囲 |
| 提案者と適用者が同じランタイムになることを避ける割り当て | 採らないと決めた（設計文書の決定 8） |
| 再開で `--verify-command` を空へ戻す手段 | 置き換えはできる。空へ戻す要求は出ていない |
| クラス図 | 型を追加するのは `Participants` 1 つで、関係を持つ型が無い。形は契約文書のデータ構造が持つ |
| `CHANGELOG.md` と版数 | 配布の工程が書く |

## 用語

| 用語 | 意味 |
| --- | --- |
| ランタイム | `claude` / `codex` / `agy` / `kiro` の 4 つ（`ALL_RUNTIMES`） |
| ホスト | 収束ループを起動しているランタイム（`detect_host`） |
| 母集合の既定 | Skill ごとに決まる参加者の出発点。cross-review は全ランタイム − ホスト、cross-refactoring は codex / kiro / ホスト |
| 参加者 | 母集合の既定に `--include` を足し、`--exclude` を除いた一覧。確認の対象 |
| 使える者 | 参加者のうち確認を通った者（`participants.available`）。`--only` があれば `[only]` |
| 席 | ラウンドで 1 つの CLI プロセスが占める場所。名前はランタイム名か `<ランタイム>-<2〜9>`（同じランタイムの 2 つ目以降） |
| 担当 | そのラウンドの席を占める者。cross-review は `rounds[].reviewers`、cross-refactoring は `rounds[].impl` |
| 埋め合わせ | 使える者が 2 席に足りないとき、ホスト、次に同じランタイムの 2 つ目で席を埋めること |
| 再開 | 状態ファイルが残り `final` が `null` のときの `init` |

## 受け入れ条件

### 共通層: 使える者の解決（`lib/assignment.py` / `lib/auth.py`）

- [ ] AC1: 母集合 3 者のうち 1 者の確認が失敗する `probe` を `resolve_participants` に渡す。返る値の `available` は
      残り 2 者（母集合の順）、`unavailable` はその 1 者と理由を持ち、例外は上がらない
- [ ] AC2: AC1 と同じ入力で `require_all=True` を渡すと `AssignmentError` が上がり、メッセージに欠けた者の名前と
      理由が含まれる
- [ ] AC3: `exclude` に含めた者に対して `probe` が呼ばれない。`include` で足した者は呼ばれる（呼び出しの回数と
      引数で確かめる）
- [ ] AC4: 次の 4 つはいずれも `AssignmentError` になる。`include` と `exclude` に同じ名前 / `ALL_RUNTIMES` に無い
      名前 / `only` が `exclude` に含まれる / `only` が参加者に無い
- [ ] AC5: `NDF_SKIP_AUTH_CHECK` が立つと、`available` は参加者の全員、`probe_skipped` は真で、確認コマンドは
      1 回も呼ばれない
- [ ] AC6: `probe_auth` は失敗で例外を上げず、`ok: false` と理由（`コマンドが見つかりません` / 時間切れ / 終了コード
      非 0 / 未認証の文言）を返す。成功は `ok: true`
- [ ] AC7: P7 の後、`check_auth` / `impl_pool` / `review_assign` / `assign` の 4 つを `git grep -n` で探す。
      `plugins/ndf/scripts/lib/` と両 Skill の `scripts/` で 0 件になる

### 共通層: 席の埋め方（cross-review の規則）

- [ ] AC8: 使える者が 3 者のとき、`review_seats(r, available, [])` は変更前の `review_assign(r, host)` と一致する。
      4 つのホスト × ラウンド 1〜12 の全組で確かめる
- [ ] AC9: 使える者が 4 者（`--include` でホストを足した）のとき、毎ラウンド 2 席で、ラウンド 1〜4 で各者が
      ちょうど 2 回担当になる
- [ ] AC10: 使える者が 2 者のとき、ラウンド 1〜4 の全部でその 2 者が返る
- [ ] AC11: 使える者が 1 者（`codex`）のとき、埋め合わせに `["claude"]` を渡すと `["codex", "claude"]`、空を渡すと
      `["codex", "codex-2"]` が返る
- [ ] AC12: 使える者が 0 者のとき、埋め合わせに `["claude"]` を渡すと `["claude", "claude-2"]`、空を渡すと
      `AssignmentError` になる
- [ ] AC13: `seat_runtime("kiro-2")` と `seat_runtime("kiro")` は `kiro` を返す。`gemini` / `kiro-1` / `kiro-10` /
      `kiro-2-3` は `AssignmentError` になる

### cross-review: 新規の `init`

- [ ] AC14: ホスト `claude` で `kiro` の確認が失敗する。`init` は終了コード 0 で状態ファイルを作る。
      `participants.available` は `["codex", "agy"]` で、`participants.unavailable.kiro` に理由が入る。標準エラーに
      `kiro` を外したことが 1 行出る
- [ ] AC15: AC14 と同じ状態で `--require-all` を付けると、`init` は終了コード 1 で終わり、状態ファイルを作らない
- [ ] AC16: `--exclude agy` を渡すと `agy` の確認を行わない。`participants.excluded` が `["agy"]`、`available` が
      `["codex", "kiro"]` になる。`--exclude agy --exclude kiro` と `--exclude agy,kiro` は同じ状態ファイルを作る
- [ ] AC17: ホスト `claude` で `--include claude` を渡すと、`available` が 4 者になり、`start-round` が 2 席を返す
- [ ] AC18: 使える者が `codex` の 1 者で、ホストの確認が通る。`init` は終了コード 0 で終わり、観点が減ることを 1 行出す。
      `participants.fallback` は `["claude"]`、`start-round` は `codex claude` を返す
- [ ] AC19: 使える者が 0 者でホストの確認が通ると、`init` は終了コード 0 で終わり、`start-round` は `claude claude-2`
      を返す。ホストの確認も通らないと `init` は終了コード 1 で終わり、状態ファイルを作らない
- [ ] AC20: 次の 3 つはいずれも終了コード 1 で終わり、状態ファイルを作らない。`--exclude claude`（ホスト）/
      `--only codex --exclude codex` / `--include agy --exclude agy`
- [ ] AC21: `read-result <pr> claude-2` が受け付けられ、`rounds[-1]["claude-2"]` に結果を書く。
      `launch-reviewer.sh claude-2 <pr> <round>` は `claude` の CLI を起動し、stem は `claude-2-review-pr<N>` になる
      （起動は差し替えて確かめる）
- [ ] AC22: `participants` を持たない状態ファイルで、`host` があれば `start-round` は変更前の輪番を返す。
      `host` も無ければ `codex` / `agy` を返す
- [ ] AC23: 前のラウンドが `verdict` を持たず、担当 `agy` + `kiro` の両者が `REQUEST_CHANGES` で修正の記録が無い。
      このとき `start-round` は終了コード 5 で止まる
- [ ] AC24: `report` が「参加した者」の節を出す。行は 6 つで、使える者 / `--exclude` で外した者 / `--include` で
      足した者 / 確認を通らなかった者（理由つき）/ 埋め合わせ / 再開で変えた値である。`participants` を持たない
      状態ファイルでは「記録なし」と出す

### cross-review: 再開の `init`

- [ ] AC25: `max_rounds: 12` の状態ファイルへ `--max-rounds 20` を渡す。`max_rounds` が 20 になり、`12 → 20` の
      形で 1 行出る。`resume_changes` に `{field: "max_rounds", from: 12, to: 20}` が 1 件積まれる。
      `--rotate-after` / `--verify-command` / `--verify-exit-code` も同じく反映され、後の 2 つは置き換える
- [ ] AC26: 引数を渡さない再開では、次の 6 項目が変わらず、確認コマンドは 1 回も呼ばれない。`max_rounds` /
      `rotate_after` / `verify_commands` / `verify_exit_codes` / `only` / `participants`
- [ ] AC27: `only: null` の状態ファイルへ `--only codex` を渡すと `only` が `codex` になり、次の `start-round` が
      `codex` だけを返す。記録を持つ過去のラウンドの `reviewers` は変わらない。`--only none` は `only` を `null` へ戻す
- [ ] AC28: `--exclude agy` を渡した再開では、参加者の確認をやり直し、`available` から `agy` が消え、次の
      `start-round` が `agy` を返さない。`--exclude none` は除外を空へ戻す
- [ ] AC29: `host: "claude"` の状態ファイルへ `--host codex` を渡すと `host` は変わらず、反映しないことが 1 行出る。
      `--host claude` では何も出ない
- [ ] AC30: `SKILL.md` と `docs/01-state-and-review.md` で `grep -n 'ONLY'` が当たる行は、`init` へ引数を渡す行と
      引数の説明の行だけになる。起動・監視・取り込み・反証の担当は `$REVIEWERS` / `$REVIEWERS_CSV` を使う

### cross-refactoring: 母集合と担当

- [ ] AC31: ホスト `claude` の新規の `init` で、状態ファイルの `runtimes` は `["claude", "codex", "kiro"]` になり、
      `agy` の確認は行われない。状態ファイルに `impl_capable` は無く、標準出力に `IMPL_POOL=` の行は無い
- [ ] AC32: ホスト `codex` では `runtimes` が `["codex", "kiro"]`、ホスト `agy` では `["codex", "agy", "kiro"]` になる
- [ ] AC33: `--include agy` で `runtimes` が 4 者に、`--exclude kiro` で 2 者になる
- [ ] AC34: `impl_assign(r, ["claude", "codex", "kiro"])` をラウンド 1〜6 で呼ぶ。返る値は `codex` / `kiro` / `claude` /
      `codex` / `kiro` / `claude` である。`start-round` は `REVIEWERS` / `REVIEWERS_CSV` を出さない。ラウンドの記録に
      `reviewers` / `reviewer_models` が無い
- [ ] AC35: `kiro` の確認が失敗しても `init` は終了コード 0 で終わる。`participants.unavailable.kiro` に理由が入り、
      `runtimes` は 2 者になる。`--require-all` を付けると終了コード 4 で終わり、状態ファイルを作らない
- [ ] AC36: 使える者が 0 者のとき `init` は終了コード 4 で終わり、状態ファイルを作らない
- [ ] AC37: `report` と改修計画の表示にレビュー担当の列が無く、母集合を 1 行で出す（「提案・レビュー」と「適用の母集合」の
      2 行に分けない）

### cross-refactoring: 再開の `init`

- [ ] AC38: `max_outer_rounds: 3` の状態ファイルへ `--max-outer-rounds 5` を渡す。5 になり、`3 → 5` の形で 1 行出て、
      `resume_changes` に 1 件積まれる。`--max-test-rounds` / `--max-fix-rounds` / `--max-items-per-round` も同じ
- [ ] AC39: 再開で `--model codex=x` / `--host codex` / `--scope other` を渡すと、状態は変わらず、反映しないことが
      引数ごとに 1 行出る。状態に載る他の引数（`--baseline-test` など。契約文書の表）も同じ扱いである。引数を渡さない
      再開では、上限 4 項目と `models` と `runtimes` が変わらない
- [ ] AC40: 再開で `--exclude kiro` を渡すと参加者の確認をやり直す。`runtimes` から `kiro` が消え、次の `start-round` の
      `RUNTIMES` に `kiro` が無い
- [ ] AC41: `impl_capable` を持ち `participants` を持たない状態ファイル（この変更の前に始めた実行）を、`start-round` /
      `report` が読める。適用の輪番は `runtimes` から決まる

### 文書

- [ ] AC42: `CLAUDE.md` の cross-refactoring の節が「codex / kiro / ホストの 3 者」と「適用担当は 3 ラウンドで 1 周」を
      書く。「ホストを除く 3 者」「参加する 4 者」を含まない。cross-review の節が「codex / agy の両方」を含まない。
      次の 3 つがいずれも 0 行を出す

  ```bash
  grep -n "ホストを除く 3 者" CLAUDE.md
  grep -n "参加する 4 者" CLAUDE.md
  grep -n "codex / agy の両方" CLAUDE.md
  ```

- [ ] AC43: cross-refactoring の `SKILL.md` の「担当の決め方」が母集合を 1 つの表で書く。引数の表と `argument-hint` に
      `--exclude` / `--include` / `--require-all` がある。「前提」から「すべてログイン済み」が消える。ホストごとに要る
      CLI の表が `codex` / `kiro-cli`（ホストが codex / kiro ならもう 1 つ）になる。`docs/01-state-and-propose.md` の
      `init` が返す変数の表に `IMPL_POOL` が無い
- [ ] AC44: cross-review の次の 4 ファイルが、それぞれの内容を書く

  | ファイル | 書く内容 |
  | --- | --- |
  | `SKILL.md` | 引数の表と `argument-hint` に `--exclude` / `--include` / `--require-all`。`--only` の説明から「デバッグ用」が消える。母集合の行が席の規則を指す |
  | `docs/05-pool-and-convergence.md` | 使える者の解決と席の埋め方（3 者以上 / 2 者 / 1 者 / 0 者）、`--exclude` / `--include`、確認が把握になったこと |
  | `docs/04-contracts.md` | 状態ファイルの `participants` と `resume_changes`、席の名前の形 |
  | `docs/01-state-and-review.md` | 再開で反映する引数と、反映しない引数 |

### 子 issue の再現手順

- [ ] AC45: #478 の再現（`kiro-cli` が無い環境で `init`）で、`init` が終了コード 0 で終わる。#687 の場面 3
      （`codex` が使えない）で、`--exclude codex` を付けずに `init` が開始できる
- [ ] AC46: #648 の再現（再開の `init` に `--only codex`）で、次の `start-round` が `codex` だけを返す
- [ ] AC47: #664 の再現（`git grep -n '"--exclude"' -- plugins/ndf/skills/cross-refactoring`）が 1 行以上を出す
- [ ] AC48: #461 の再現（モデルを引けない CLI が確認を通る）は、この変更の後も現象が残ることを確かめて記録する
      （直す判断は #461 が持つ）

### 全体

- [ ] AC49: `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] AC50: 次の 6 つが終了コード 0 で終わる

  ```bash
  bash scripts/build-runtime-plugins.sh --check
  claude plugin validate .
  python3 scripts/check-skill-frontmatter.py
  python3 scripts/check-doc-staleness.py --root .
  python3 scripts/check-markdown-links.py --root .
  python3 scripts/check-skill-shell-vars.py
  ```

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 可用性 | 母集合の 1 者が使えないことで、どちらの収束ループも開始できない状態にならない（AC14、AC35） |
| 性能・拡張性 | 確認の回数は、新規の `init` で「参加者の数 + 埋め合わせが要るときのホスト 1 回」を超えない。除外した者は確かめない。再開で確かめ直すのは担当に関わる引数を渡したときだけである |
| 運用・保守性 | 担当が欠けたまま収束したこと、席を埋め合わせたこと、再開で反映しなかった引数が、`report` と `init` の出力だけで分かる（AC24、AC29、AC39） |
| 移行性 | この変更の前に始めた実行の状態ファイルを、書き換えずに読める（AC22、AC41） |

## 影響

| 対象 | 影響 |
| --- | --- |
| 認証に失敗する CLI がある利用者 | どちらの `init` も止まらず、使える者で回る。従来の関門は `--require-all` で選べる |
| cross-refactoring の既定の参加者 | agy が既定から外れ、ホストが提案と適用に入る。`--include agy` で戻せる。提案者と適用者が同じランタイムになりうる |
| cross-review で使える者が 2 者に満たない利用者 | ホスト、次に同じランタイムの 2 つ目が席を埋める。1 者で回るのは `--only` を渡したときだけになる |
| 状態ファイルの形 | 両 Skill の最上位に `participants` と `resume_changes` が増える。cross-refactoring の `impl_capable` とラウンドの `reviewers` / `reviewer_models` が新規の状態から消える。無い項目は従来の読み方で読む |
| `init` の引数 | 両 Skill に `--exclude` / `--include` / `--require-all` が増える。cross-review の `--only` が `none` を取る。既定値は変わらない |
| 共通層の関数 | `check_auth` / `impl_pool` / `review_assign` / `assign` が消え、`probe_auth` / `resolve_participants` / `review_seats` / `impl_assign` / `seat_runtime` / `refactor_pool` が入る。呼び出し側は両 Skill だけである |
| 担当名の形 | ランタイム名に `-2`〜`-9` の接尾辞を持つ席の名前が、結果ファイルの stem と状態ファイルの鍵に現れうる |
| 再開で修正の記録の無い前ラウンドがある実行 | 担当が `codex` / `agy` 以外のラウンドでも、前ラウンドの検査が止める（AC23） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --with pytest pytest scripts/tests plugins/ndf -q` |
| 配布物の同期 | `bash scripts/build-runtime-plugins.sh --check` |
| 定義と文書の検査 | AC50 の 6 つ |
| 手動確認 | P7 の後、ホスト claude で `--exclude codex` を付けた cross-review と、既定の cross-refactoring を 1 本ずつ回し、`report` で参加者と席を見る |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 担当の決め方は `plugins/ndf/scripts/lib/assignment.py`、確認は `lib/auth.py`、再開の反映は `lib/statefile.py` に置く。判定と状態の鍵は各 Skill の `state.py` / `refactor_lib` が持ち、骨組みは結果を使うだけにする |
| コーディング規約 | 状態ファイルを最小の形で組み、関数を直接呼んで確かめる（`AGENTS.md` の DO）。分岐は表（データ）で持つ（`refactoring` の「分岐をデータ化」） |
| テスト戦略 | 共通層は `plugins/ndf/scripts/tests/` の関数テスト、Skill は既存の形（`conftest.py` の `state_mod` / `crossref_helpers`）で GitHub と CLI の起動を差し替える |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、配布物の同期の検査、文書の検査 |
| 確認してから行う | `init` の既定を「確認の失敗で止める」から「使える者で回す」へ変えること。cross-refactoring の既定から agy を外すこと。どちらも設計 Pull Request の承認で確認する |
| 行わない | 監視・起動・投稿の経路の変更、`refactor_lib` の適用の取り込みの変更、確認コマンドの差し替え |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 確認を「モデルを引く最小の呼び出し」へ替えるか、替えるならランタイムごとのコマンドと所要 | #461 | マイルストーン 06 の着手時 |
| 同じランタイムの 2 席が、別のランタイムの 2 席より指摘を見落とすか | この変更の後の運用（`measure.py`） | 実測が 3 本たまった時点 |

## 既存の受け入れ条件との対応

既存の設計（PR #667）の P5 の受け入れ条件を、この文書のどこが引き継ぐかを示す。

| 既存 | この文書 | 変わったこと |
| --- | --- | --- |
| AC10 | AC14 | 項目が `available_reviewers` から `participants.available` へ。値は同じ |
| AC11 | AC15 | 同じ |
| AC12 | AC5 | 共通層の条件として書き直した |
| AC13 | AC16 | 同じ |
| AC14 | AC20 | `--include` と `--exclude` の矛盾を足した |
| AC15 | AC8 | 関数が `review_assign` から `review_seats` へ。値は同じ |
| AC16 | AC10 | 同じ |
| AC17 | AC18 | 1 者で回すのではなく、ホストで 2 席目を埋める。1 者のまま回るのは `--only` だけ |
| AC18 | AC19 | 0 者で失敗するのではなく、ホストが通れば 2 席を埋める。ホストも通らないときだけ失敗する |
| AC19 | AC22 | 同じ |
| AC20 | AC7、AC34、AC35 | 「cross-refactoring は変わらない」から「cross-refactoring も同じ層を通る」へ。`check_auth` は消える |
| AC21 | AC24 | `--include` で足した者と埋め合わせの行を足した |
| AC22 | AC25 | 同じ |
| AC23 | AC26 | 項目が `participants` に畳まれた |
| AC24 | AC27 | 同じ |
| AC25 | AC27、AC28 | 同じ |
| AC26 | AC28 | 同じ |
| AC27 | AC29 | 同じ |
| AC28 | AC30 | 同じ |
| AC29 | AC23 | 同じ |
| AC30 | AC44 | `--include` と席の規則を足した |
| AC31 | AC49 | 同じ |
| AC32 | AC50 | 文書の検査 3 つを足した |
| — | AC1〜AC4、AC6、AC9、AC11〜AC13、AC17、AC21 | 新設（共通層の解決と席） |
| — | AC31〜AC43 | 新設（cross-refactoring と `CLAUDE.md`） |
| — | AC45〜AC48 | 新設（子 issue の再現手順） |

## 依頼（原文）

### #727（根本原因の親）

> **参加する CLI が実行できるかを確かめ、使える者から担当を割り当てる共通層。** 場所は `plugins/ndf/scripts/lib/auth.py` の `check_auth`（36 行目）と `assignment.py` の `review_pool` / `review_assign` / `assign`（78 / 95 / 114 行目）である。
>
> - `check_auth` は認証の成否だけを見て、1 件の失敗で `die` する。モデルを引けるか・更新トークンが生きているかは見ない
> - `assignment.py` は母集合を「全ランタイム − ホスト」の定数から作り、使える者を入力に取らない
> - cross-review（`state.py` の `init`）と cross-refactoring（`refactor_lib/commands/setup.py` の `cmd_init`）の両方が、この層を使う
>
> ## 採る手
>
> 移動（`move_responsibility`）。使える者の決定を、各 Skill の初期化の関門から共通層の割り当てへ移す。
>
> **cross-refactoring の既定の母集合は codex / kiro / ホストとし、agy を外す**（#664 / #687 の 2026-09-18 の決定。CLI の起動 199 回のうち失敗は agy の 7 回だけで、提案の所要も agy が最も長かった）。提案も適用もこの 3 者で回す。cross-review と共有する `assignment.py` で規則を 1 つにし、ホストが codex / kiro のとき（母集合が 2 者）の扱いは #687 の規則（各ラウンド 2 者を確保する。同一ランタイム 2 つ・ホストの参加を許す）に従う。
>
> ## 完了条件
>
> - 共通層が「最小の呼び出しが通るか」で使える者を決めて返し、割り当てがその一覧から担当を選ぶ
> - cross-review と cross-refactoring の両方がこの層だけを通り、片方にだけ古い形が残らない
> - `CLAUDE.md` の cross-refactoring の節（「ホストを除く 3 者」「参加する 4 者から輪番」）と `cross-refactoring/SKILL.md` の「担当の決め方」を、実装後の母集合に合わせる
> - 各子 issue の再現手順を実行し、現象が出ないことを確かめる（子 issue はその時点の棚卸が「閉じてよい」で閉じる）

### #687

> - **各ラウンドで 2 者がレビューできることを最優先にする。** 誰が担当かより、2 つの目で見ることを優先する
> - **文脈が分かれていれば、同じランタイムを 2 つ立ててよい。** 別プロセス・別文脈なら独立した意見になる
> - **コードを書いたランタイム（ホスト）が担当に入ってよい。** 輪番の形にはこだわらない
> - **他にランタイムが 1 つも無ければ、ホストを 2 つ走らせる形でよい**
>
> **置き場所も決める。** 規則が決まっても、置き場所が決まらなければ次に使う人へ届かない。
>
> **cross-refactoring では、既定の担当から agy を外し、ホストのランタイムを輪番へ入れる**と決めた（利用者の指示）。提案も適用も codex / kiro / ホストの 3 者になる。

### #478

> **認証確認を「関門」から「導入状況の把握」へ変える。** 使える者でレビューし、使えない者は最初から数えない。
>
> 1. `init` で母集合の各ランタイムの認証を確かめ、**通った者の一覧を `state.json` へ記録する**（`available_reviewers`）
> 2. **明示的に外す手段を用意する**（例: `--exclude agy`）。**認証は通るが実行で落ちる担当を外す用途でも使う。**
> 3. 使える者の数で分岐する（3 者: 現行どおり / 2 者: 毎ラウンドその 2 者 / 1 者: 警告して 1 者 / 0 者: 失敗）
> 4. `review_assign()` は「**使える者が 3 者以上のときだけ 1 者を外す**」に変える
> 5. 全員揃っていることを要求したい運用のために `--require-all` を用意する
> 6. 完了報告に「このループに参加したのは誰か」を出す

### #664

> `cross-refactoring` には、担当から特定のランタイムを外す引数が無い。
>
> **外す手段を足すだけでは足りない。** `assign()` は実装担当を先に決めてから、提案・レビューの母集合から実装担当を除いた者をレビュー担当にする。使える者が 2 者だと、実装担当が codex か kiro のラウンドではレビュー担当が 1 者になる。
>
> | 母集合 | いま | 変えた後 |
> |---|---|---|
> | 提案（`review_pool(host)` ） | 全ランタイム − ホスト（codex / agy / kiro） | codex / kiro / ホスト |
> | 適用（`impl_pool()` ） | 4 者すべて | codex / kiro / ホスト |
>
> - 外す手段（`--exclude` ）を足すだけでなく、**既定の母集合から agy を外す**。agy を戻す手段を残すかは設計で決める
> - 同じ項目の提案者と適用者が重ならないよう、割り当てで避けるかは設計で決める
> - ホストが codex / kiro のときは、母集合が 2 者になる。そのときの扱いは #687 の規則に従う
> - `CLAUDE.md` の cross-refactoring の節（「codex / agy / kiro / claude のうちホストを除く 3 者」「参加する 4 者から輪番」）もあわせて直す

### #648

> `/ndf:cross-review` を中断・再開すると、`state.py init` に渡した `--only` / `--max-rounds` / `--rotate-after` / `--verify-command` / `--verify-exit-code` / `--host` が**黙って無視される**。
>
> **`cross-refactoring` にも同じ形がある。** `refactor.py` の `init` が受ける `--max-outer-rounds` / `--max-test-rounds` / `--max-fix-rounds` / `--max-items-per-round` / `--model` は再開時に反映されない。
>
> ## 修正レイヤー
>
> `plugins/ndf/scripts/lib/statefile.py` に置く、再開時の引数の反映の契約。「明示的に渡された引数だけを状態へ重ね、反映しない引数は渡されたら知らせる」を 1 か所で持つ。

（各 issue の本文から抜粋。全文は `gh issue view 727` / `687` / `478` / `664` / `648`）
