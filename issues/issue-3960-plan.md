# #3960: plugins/ndf のコメントが指す作り直しの決定を、確定仕様の決定へ書き換える

## 関連リンク
- 要求仕様: [issue-3960-requirements.md](./issue-3960-requirements.md)（正は https://github.com/devbasex/ai-plugins/issues/3960 の本文）。受け入れ条件は `3960:<番号>` で指す
- 設計書: [issue-3960-design.md](./issue-3960-design.md)（詳細設計は「どこを変えるか」に畳んである）。確かめのコマンドは [issue-3960-design-checks.md](./issue-3960-design-checks.md)
- モード: `standard`（触るファイル 84・確定仕様 2 本の新設・検査の入力の保存先を替えるため。設計 PR #4413 で設計承認済み）

## 修正対象

設計書の「どこを変えるか」の表から引き写す。ファイルの一覧は設計書の「設計の結果」にあり、単位ごとの割り当ては下の「レビューの単位」に書く。

| ファイル（本数） | 変える中身 | 決定 |
| --- | --- | --- |
| `plugins/ndf` の `.py`（60）・`bin/ndf-claude`（1）・`pyproject.toml`（1） | コメントと docstring の決定の参照を、確定仕様のパスと番号へ。`child_deps.py` は「入れ替えで消す」と「コピー」の語も外す。`identity.py` のコードの値の文字列は変えない | 3 |
| `contracts/hooks/`（3）・`contracts/limits/`（9）の JSON | `summary` の文の参照 | 3 |
| `checks/README.md`（1） | 「子の表の規則」の SSOT を `children.json` に、表の変え方の順を足す。決定を指す文 | 1・3 |
| `checks/children.json`（1） | `summary` と、生成し直す `review_items` の `routed_to` | 1・2 |
| `checks/rules.json`（1） | `spec.review_items_source` と、`ndf.adapters.llm.ATTEMPTS` の `reason` の文 | 2・3 |
| `docs/specifications/ndf-review-items.md`・`ndf-review-items-11x.md`（新設 2） | `issues/` の 2 つの表を移す | 2 |
| `ndf-rebuild.md`・`ndf-rebuild-review-scope.md`・`ndf-contract-fact-checks.md`・`docs/specifications/README.md`（4） | 表の保存先の文・「子の記号」の表・リンク・文書の一覧 | 1・2 |
| `.github/workflows/script-structure.yml`（1） | `REVIEW_ITEMS_PATHS` の 1 行 | 2 |

用語集の 2 本（`docs/glossary/`）は設計 PR で変えてあり、実装では変えない。

## タスク分解

変更の種類は「機能追加」に当たらない書き換え（振る舞いを変えない）なので、最初のタスクを退行テストにしない。振る舞いの不変は、設計書の「確かめのコマンド」の構文木と値の確かめが縛る。どのタスクも **テスト駆動を適用しない**（コメントと文書の書き換えで、縛る振る舞いが無い。合否は確かめのコマンドの終了コードと CI で決める）。テストは手元で走らせず、単位の PR の CI で見る。

### Task 1: 規則 `review-items` が読む 2 つの表を確定仕様へ移す（決定 2）
- **対象ファイル:** `docs/specifications/ndf-review-items.md`（新設）、`docs/specifications/ndf-review-items-11x.md`（新設）、`plugins/ndf/src/ndf/checks/rules.json`、`plugins/ndf/src/ndf/checks/children.json`（`review_items` の生成し直しだけ）、`.github/workflows/script-structure.yml`、`docs/specifications/ndf-contract-fact-checks.md`、`docs/specifications/README.md`、`docs/specifications/ndf-rebuild-review-scope.md`
- **変更内容:** 設計書の決定 2 の表のとおり、`issue-1142-design-review-routing.md` の「列の読み方」「行き先の表」と、`issue-3388-design-routing.md` の「列の読み方」「対応表」を、セルを変えずに 2 本へ移す。「列の読み方」のリンクは決定 2 の「リンクの替え先」で替える。`rules.json` の `spec.review_items_source` を `docs/specifications/ndf-review-items.md` に替え、`ndf check --write-spec` で `review_items` を生成し直す。`REVIEW_ITEMS_PATHS`・`ndf-contract-fact-checks.md` の 2 つのリンク・確定仕様の一覧を新しい 2 本へ向ける。`ndf-rebuild-review-scope.md` は決定 2 の表の行（決定 109 の「11.0.0 の後に足した検査」・構成要素の「振り分け表」・対象範囲の「含まない」・「版の基準」「子の分け方」）だけを直し、「子の表のコピー」を「子の表」に替える。`rules.json` の `ATTEMPTS` の `reason` の決定の参照も、同じファイルなのでここで書き換える（決定 3 の手順）
- **満たす受け入れ条件:** 3960:9・3960:11・3960:12・3960:13（足した 2 本が 500 行以下）。3960:10 は値の確かめで縛る
- **進め方:** 移す → `moved.py` と `values.py` と `tree.py` → `ndf check --write-spec` の後に差分が無いこと（テスト駆動を適用しない理由は上）

### Task 2: 子の表の SSOT を `children.json` にする（決定 1）
- **対象ファイル:** `docs/specifications/ndf-rebuild.md`、`plugins/ndf/src/ndf/checks/children.json`（`summary` だけ）、`plugins/ndf/src/ndf/checks/README.md`、`plugins/ndf/src/ndf/checks/child_deps.py`
- **変更内容:** `ndf-rebuild.md` の「子の記号」から先に要るもの・順番・レーンの 3 列を消し、R0・R1 を直前の段落の文へ移し、M3b+・X9 の 2 行を足す（行の文は設計書の決定 1）。「構造」「入れ替えの結果」の「子の表のコピー」と振り分け表の保存先の文を替える。`children.json` の `summary` と `checks/README.md` の「子の表の規則」に、自分が SSOT であることと表の変え方の 3 つの順を書く。`child_deps.py` の docstring から「入れ替え（S2）がこのモジュールと `children.json` を消す」を削り、規則 `child-deps` を入れ替えの後も使うと書き、1 行目と 30 行目の「子の表のコピー」を「子の表」に替える。`checks/README.md` と `child_deps.py` の決定の参照も、決定 3 の手順でここで書き換える。`ndf-rebuild.md` は 500 行のまま
- **満たす受け入れ条件:** 3960:7・3960:8。3960:10 は構文木と値の確かめで縛る
- **進め方:** 書き換え → `kids.py` → `git grep -n '子の表のコピー' -- plugins/ndf` が 0 件 → `tree.py`・`values.py`

### Task 3: hook の層のコメントの参照を書き換える（決定 3）
- **対象ファイル:** `plugins/ndf/hooks/boot.py`・`entry.py`、`plugins/ndf/src/ndf/domains/hooks/` の `__init__.py`・`boot.py`・`budget.py`・`cli.py`・`context.py`・`counter.py`・`dispatch.py`・`env.py`・`main.py`・`metrics.py`・`outbox.py`・`registration.py`・`sender.py`・`strict.py`、`domains/hooks/guards/` の `ask.py`・`read_repeat.py`・`session.py`・`stop_wait.py`・`turn.py`・`unattended.py`、`plugins/ndf/contracts/hooks/` の `guards.json`・`imports.json`・`runtimes.json`、`plugins/ndf/bin/ndf-claude`、`plugins/ndf/tests/bin/test_ndf_claude.py`
- **変更内容:** 設計書の「書き換えの手順」の図と表で 1 行ずつ分類し、`書き換え` の行だけを `docs/specifications/ndf-rebuild-<主題>.md の決定 N` の形にする。`contracts/hooks/` の `summary` を変えたら `ndf check --write-spec` を打つ
- **満たす受け入れ条件:** 3960:1・3960:2・3960:3・3960:4・3960:5・3960:6（この単位のファイルの分）、3960:10・3960:12
- **進め方:** `refs.py list` → 分類を埋める → 書き換え → `tree.py`・`values.py`

### Task 4: 中継のコメントの参照を書き換える（決定 3）
- **対象ファイル:** `plugins/ndf/src/ndf/domains/relay/` の `__init__.py`・`account_registry.py`・`cli.py`・`hub.py`・`markers.py`・`pending.py`・`signals.py`、`plugins/ndf/tests/domains/relay/` の `test_relay_cli.py`・`test_relay_hub.py`・`test_relay_parts.py`
- **変更内容:** Task 3 と同じ手順。中継の設計の決定（「決定 13」など）は `別の設計` として書き換えない。行の多い `hub.py`（9 行）と `test_relay_hub.py`（15 行）は取り違えが起きやすいので、根拠の列に決定の本文の文を書く
- **満たす受け入れ条件:** 3960:2・3960:3・3960:4・3960:5・3960:6（この単位のファイルの分）、3960:10
- **進め方:** Task 3 と同じ

### Task 5: 耐久ワークフロー・進行・limits のコメントの参照を書き換える（決定 3）
- **対象ファイル:** `plugins/ndf/src/ndf/core/durable.py`・`durable_runs.py`・`durable_sweep.py`、`plugins/ndf/src/ndf/core/plan/transition_decide.py`、`plugins/ndf/src/ndf/progress/backup.py`・`store.py`・`lane/__init__.py`、`plugins/ndf/src/ndf/identity.py`、`plugins/ndf/contracts/limits/` の 9 本（`durable.keep.json`・`durable.prepare.json`・`hook.ask-forward.json`・`hook.budget.pre-tool-use.json`・`hook.counter-lock.json`・`hook.deny-repeat.json`・`hook.metrics-keep.json`・`hook.pushback.json`・`hook.venv-keep.json`）、`plugins/ndf/tests/conftest.py`、`plugins/ndf/tests/core/` の `test_durable.py`・`test_durable_format.py`・`test_durable_store.py`・`test_durable_sweep.py`・`test_durable_url.py`、`plugins/ndf/tests/progress/` の `load_measure.py`・`test_store_pg.py`・`test_store_sqlite.py`
- **変更内容:** Task 3 と同じ手順。`durable.py` の 9 行目は `ndf-rebuild.md の決定 81・82・83・84・85・86`、`transition_decide.py` の 31 行目は `ndf-rebuild-scripted-flow.md の「洗い出しの一覧」`（設計書の 7 ファイルの表）。`identity.py` の `OldPlace` の `reason` の「決定 85」は `コードの値` として変えない。`contracts/limits/` の `summary` を変えたら `ndf check --write-spec` を打つ
- **満たす受け入れ条件:** 3960:1・3960:2・3960:3・3960:4・3960:5・3960:6（この単位のファイルの分）、3960:10・3960:12
- **進め方:** Task 3 と同じ。`test_durable_format.py` の hash は変わらない見込み（設計書の「文脈と制約」）で、CI で確かめる

### Task 6: 検査・アダプタ・そのほかのコメントの参照を書き換える（決定 3）
- **対象ファイル:** `plugins/ndf/src/ndf/adapters/github.py`・`llm.py`、`plugins/ndf/src/ndf/checks/facts.py`・`invariants.py`・`lint.py`・`smells.py`、`plugins/ndf/pyproject.toml`、`plugins/ndf/src/ndf/domains/release/__init__.py`、`plugins/ndf/src/ndf/domains/worktree/write_target.py`、`plugins/ndf/tests/checks/conftest.py`
- **変更内容:** Task 3 と同じ手順。`pyproject.toml` の 2 行目と 29 行目、`invariants.py` の 1 行目は設計書の 7 ファイルの表のとおり。`write_target.py` の「#1142 の決定 20」は、参照を 1 行目から外し「構文木は tree-sitter-bash で作る」の文の後ろへ `docs/specifications/ndf-rebuild-libraries.md の決定 14` として移す（設計書の「書き換えの手順」の表）。`github.py` は 1,000 行の上限にあるので行を増やさない
- **満たす受け入れ条件:** 3960:1・3960:2・3960:3・3960:4・3960:5・3960:6（この単位のファイルの分）、3960:10
- **進め方:** Task 3 と同じ

## レビューの単位

行数は追加と削除の和で、`git diff --shortstat <ベース>...HEAD` で数える。数えないパスは無い（生成し直す `children.json` の `review_items` も数える）。

| 単位 | 含むタスク | 満たす受け入れ条件 | 触るファイルの数 | 見込みの差分の行数 | 依存 | 実際の行数 |
| --- | --- | --- | --- | --- | --- | --- |
| U1 表の移設（決定 2） | Task 1 | 3960:9・3960:10・3960:11・3960:12・3960:13 | 8 | 約 470（新設 2 本で約 330、`routed_to` の生成し直しで約 115） | 無し（最初に流す） | （実装の後に書き足す） |
| U2 子の表の SSOT（決定 1） | Task 2 | 3960:7・3960:8・3960:10 | 4（`children.json` は U1 と共有） | 約 150（「子の記号」の 54 行の列の削除で約 110） | U1 の後（`children.json` と確定仕様の同じ段落を触る） | （実装の後に書き足す） |
| U3 hook の層 | Task 3 | 3960:1・3960:2・3960:3・3960:4・3960:5・3960:6・3960:10・3960:12 | 27 | 約 100（候補 45 行） | 無し | （実装の後に書き足す） |
| U4 中継 | Task 4 | 3960:2・3960:3・3960:4・3960:5・3960:6・3960:10 | 10 | 約 90（候補 46 行。うち別の設計が混じる） | 無し | （実装の後に書き足す） |
| U5 耐久ワークフロー・進行・limits | Task 5 | 3960:1・3960:2・3960:3・3960:4・3960:5・3960:6・3960:10・3960:12 | 26 | 約 90（候補 42 行） | 無し | （実装の後に書き足す） |
| U6 検査・アダプタ・そのほか | Task 6 | 3960:1・3960:2・3960:3・3960:4・3960:5・3960:6・3960:10 | 10 | 約 60（候補 25 行） | 無し | （実装の後に書き足す） |

触るファイルの合計は 84 本（`children.json` を 1 本と数える）で、設計書の「どこを変えるか」の本数と合う。

**並列にできるもの。** U3・U4・U5・U6 は互いに、また U1・U2 とも触るファイルが重ならないので、同時に流してよい。U1 と U2 は `children.json` と確定仕様の同じ段落を触るので、U1 → U2 の順にする。準備のリファクタリングは無いので、U3〜U6 を U1 の後に待たせる理由は無い。

**分け方の理由。** 準備（決定 1・2）は 1 つにすると約 620 行で目安の 400 行を超えるため、保存先の移設（U1）と SSOT の付け替え（U2）に分けた。U1 は 400 行を超えるが、約 330 行はセルを変えない引き写しで、目で読まずに `moved.py` が全セルを比べるので、これ以上分けない。決定 3 はディレクトリのまとまりで分け、どの単位も単独で構文木と値の確かめが通る。

**一覧の持ち回り。** 参照の一覧（`refs.tsv`）はコミットしないので、U1〜U6 は自分の単位のファイルの行の分類を埋めた一覧を `/work/ai-plugins/.ndf/handoff/lz1-3960/refs-<単位>.tsv` に置き、単位の PR の本文に `別の設計` 以外の行と分類ごとの件数を載せる。最後のまとめた確かめが 6 本をつないで `list` に渡し、埋めた分類を引き継ぐ。

## 確かめのコマンドの打ち方

コマンドの全文は [issue-3960-design-checks.md](./issue-3960-design-checks.md) にある。`<base>` は `git merge-base HEAD origin/main` で、検証用のディレクトリ `W` は worktree ごとに 1 回作る。

| コマンド | 単位ごとに打つ | 最後にまとめて打つ |
| --- | --- | --- |
| 構文木の確かめ `tree.py`（3960:10） | すべての単位 | 打つ |
| 値の確かめ `values.py`（3960:10） | すべての単位 | 打つ |
| 移した表の確かめ `moved.py`（3960:11） | U1 | 打つ |
| 子の表の確かめ `kids.py`（3960:7） | U2 | 打つ |
| 参照の確かめ `refs.py list`（一覧のひな形） | U3〜U6（と U1・U2 の決定 3 の分）。自分のファイルの行だけ分類を埋める | 6 本の一覧をつないで打ち直す |
| 参照の確かめ `refs.py check`（3960:1・2・3・5・6） | 打たない（ほかの単位の旧い形が残るので、全体の検査は途中で 1 を返す） | 打つ。終了コード 0 |
| `git grep -n -E 'issue-(1142\|3347\|3395\|3388)-' -- plugins/ndf` が 0 件（3960:1） | 打たない（同上） | 打つ |
| `git grep -n '子の表のコピー' -- plugins/ndf` が 0 件（3960:8） | U2 | 打つ |
| `plugins/ndf/bin/ndf check --write-spec` の後に差分が無い（3960:12） | U1・U3・U5（JSON の説明か `review_items` を変える単位） | 打つ |
| `ndf records glossary render --check`（3960:12） | 打たない（用語集は変えない） | 打つ |
| 使い捨ての worktree で 36 本を消し、`ndf check` と `python3 scripts/check-markdown-links.py`（3960:9） | U1（表の保存先が替わった時点で通るかを先に見る） | 打つ |
| CI の必須のジョブ（3960:13） | 単位の PR の CI | スプリントの PR の CI |

最後のまとめた確かめは、U1〜U6 をスプリントブランチへ入れた後に 1 回打ち、マージの直前に `main` を取り込んだら打ち直す（並行する変更が増やした「決定 N」の行を拾う。設計書の「影響と危険」）。

## リファクタリングの時機

| 時機 | 対象 | 理由 |
| --- | --- | --- |
| 実装の前（準備のリファクタリング） | しない | コードの構造を変えない書き換えで、積む先の構造が無い。測った値: 触るファイル 84 本（1 本あたりの候補の行は最大 15）、同じファイルを触るタスクは `children.json` の 2 つだけ（6 タスクのうち 2）、足すテストは 0 |
| 実装の中 | 各タスクで行う | `tdd-cycle` のリファクタリングのステップ。この変更ではコメントの言い換え（1 行 140 桁に収める）に当たる |
| スプリントの終わり（cross-refactoring） | しない | 単位をまたぐ同じ形の処理が無い（どの単位もコメントと文書だけを変え、コードの構文木は前後で一致する） |

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 番号が同じ別の設計の決定を書き換える（「決定 99」・中継の「決定 13」） | 決定の本文とコメントの文を突き合わせ、根拠を一覧に書く。最後の `refs.py check` と、`git diff` の行を一覧と照らす |
| `github.py` が 1,000 行を超える | 行を増やさず、説明の文を言い換えて今の行に収める |
| 並行するレーンが同じファイルのコメントに触って衝突する | 相手の変更を採り、その行の参照だけを書き換え直す。マージの直前に最後のまとめた確かめを打ち直す |
| 並列の単位の一覧の分類が食い違う・欠ける | 単位ごとに自分のファイルの行だけを埋め、最後に 6 本をつないで `list` を打ち直す。空の行が残れば `check` が 1 を返す |

## 切り戻し手順
- 単位ごとの PR を revert すれば戻る。データ移行は無い。U2 は U1 の後なので、U1 を戻すときは U2 を先に戻す

## 完了の定義
- [ ] 受け入れ条件 3960:1〜13 をすべて満たし、条件ごとに上の表のコマンドと結果が対応している
- [ ] 最後のまとめた確かめの 5 つのコマンドがどれも終了コード 0 で、出力をスプリントの PR に載せた
- [ ] スプリントの PR の本文に、`別の設計` 以外の行と分類ごとの件数を載せた（3960:4・3960:5）
- [ ] 「レビューの単位」の実際の行数を書き足した
- [ ] `standard` の検証の段階（`quality-gates`）を満たした
