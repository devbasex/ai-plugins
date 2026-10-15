# #1337: NDF が作る worktree で、プロジェクトの依存物とコンテナのテスト環境がそのまま使えるようにする

正は課題の本文（#1337）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> NDF が作る作業ツリー（`/ndf:worktree` の `.worktrees/` と、cross-review・cross-refactoring の `/tmp/ndf-worktrees/`）で、そのプロジェクトの依存物とテストの環境がそのまま使えるようにする。（2026-09-27 利用者の指示。根拠は `issues/genericity-survey-2026-09-27.md` の G4）

（以下は原文の呼び方を用語集に合わせて **worktree** と書く。`/ndf:worktree` が作るものは**開発 worktree**、cross-review・cross-refactoring が作るものは**レビュー worktree** である）

## 例: carmo-system-console の開発 worktree で PHP のファイルをコミットすると

今の振る舞いは次のとおりである（`.githooks/pre-commit`・`docker-compose.dev.yml` を 2026-09-27 に読んだ）。

1. `/ndf:worktree` が `.worktrees/feat/x` を作る。中身は追跡されているファイルだけで、`vendor/` が無い
2. `git commit` で pre-commit が走る。devcontainer の中（`REMOTE_CONTAINERS=true`）では `./vendor/bin/pint` を直接呼び、
   `xargs: ./vendor/bin/pint: No such file` で落ちる
3. コンテナの外では `docker compose exec -T app ./vendor/bin/pint <ファイル>` を呼ぶ。`app` は `.:/src` で
   **メインディレクトリ**をマウントしているため、Pint が整形するのはメインディレクトリの同じ名前のファイルで、
   worktree の変更は整形されない（ファイルを読んでの推測。実測はしていない）
4. 記録では、利用者は `--no-verify` でコミットするか（6 件）、`rm vendor && cp -a …/vendor` を手で繰り返していた

この課題が済むと、2 は worktree の依存物で Pint が走り、NDF が走らせるコンテナ越しのテストは worktree がコンテナへ届いた状態で走る。
届けられないときは、メインディレクトリで走らせずに止まる。**3（コンテナの外から打つ pre-commit）はこの課題の範囲外で、#1388 が扱う**（2026-09-28 の承認ゲート 1 で利用者が決定）。

## 目的

- NDF が作る worktree（開発 worktree とレビュー worktree）で、利用者のプロジェクトの pre-commit・範囲テスト・
  全体テストが、手作業なしに **worktree の変更に対して**走る
- 依存物が無いことによる失敗と、コンテナがメインディレクトリを見ていることによる偽の green を無くす

## 今の状態（2026-09-27 に調べた）

- worktree を作る処理は 4 箇所あり、どれも `git worktree add` だけで依存物を用意しない
  - `worktree-setup.sh create`（`/ndf:worktree`。`plugins/ndf/scripts/worktree-setup.sh:340`）
  - `supervise_lib/paths.py` の `ensure_worktree`（3 層の queue と run）
  - cross-review の `review_lib/workspace.py` の `_create_worktree`（`/tmp/ndf-worktrees/`）
  - cross-refactoring の `prepare-worktrees.sh:99`（`git worktree add --detach`）
- 依存物を持ち込む仕組みは既にある: `worktree-localenv.sh setup` が共有設定（`.ndf/worktree.json`）の
  `localenv.copy_from_main` / `copy_as_real` に従ってメインディレクトリから複製する。ただし
  **`localenv.kind` が `compose` のときだけ動き、上の 4 箇所のどれからも呼ばれない**（手で打つ手順）
- `worktree-testenv.sh` はコンテナのテスト環境を共有設定の `compose_files` で worktree ごとに組める。
  これもレビュー worktree からは使われない
- carmo-system-console の会話の記録で 23 件（直近 30 日で 19 件）: pre-commit が `./vendor/bin/pint: No such file` で落ち、
  `--no-verify` のコミットが 6 件、`rm vendor && cp -a …/vendor` の手作業を繰り返した
- project-trygroup-prd では `/tmp/ndf-worktrees` で `ModuleNotFoundError` が多数出た。ただし
  `scripts/run-python-tests.sh` はサービスごとの venv を `$TMPDIR` に自分で作るため、このスクリプトが worktree で
  落ちるかは確かめていない（記録の失敗は別のコマンドから出た可能性がある）
- cross-refactoring の着手前のテストは、依存物が無いと red になり、既存の失敗として扱われて止まる
- Claude Code の `EnterWorktree` で隔離したセッションが、NDF のスクリプト（`closing-issues.sh`、`gh` の複合コマンド）を
  `Refusing to run it` で拒む（記録 3 件、2026-09-08）。拒む判定が Claude Code の側のどこにあるかは調べていない

## 前提

- 前提 1: 依存の用意は**プロジェクトの宣言で有効にしたプロジェクトだけ**で働く。宣言が無いプロジェクトでは、
  worktree を作る 4 箇所は今と同じコマンドだけを実行する
- 前提 2: 依存の用意の手段（宣言したコマンドを走らせる / メインディレクトリの依存物を参照する / 複製する）と、
  宣言をどのファイル（`.ndf/project.json` か共有設定の `localenv`）のどのキーに持つかは `design` が決める。
  既存の `localenv.copy_from_main` / `copy_as_real` を使い回せるならそれを優先する
- 前提 3: 依存の用意は worktree を**作ったときに 1 回**行う。既にある worktree を使い回すとき（cross-review の
  2 ラウンド目以降、`prepare-worktrees.sh` の再利用）は、宣言が求めない限りやり直さない
- 前提 4: 依存の用意に使うコマンドは、プロジェクトが宣言に書いたテストのコマンドと同じ信頼の度合いで扱う
  （プロジェクト自身が書いたものだけを走らせ、NDF が推測で足さない）
- 前提 5: `project-decl.py` の解析（#1333）が依存の用意の宣言の案を出すかは `design` が決める。
  出さない場合も、利用者が手で書いた宣言で動く
- 前提 6: サンプルでの確認（受け入れ条件 9・10）は、手元の `/work/carmo-system-console` と `/work/project-trygroup-prd` で
  行う。リポジトリの CI では、形を真似た仮のリポジトリで同じ振る舞いを確かめる

## 対象範囲

含む:
- worktree を作る 4 箇所（`worktree-setup.sh create`・`ensure_worktree`・cross-review・cross-refactoring）での依存の用意
- 依存の用意を有効にする宣言の形と、宣言が無いときに今と同じ振る舞いを保つこと
- コンテナ越しのテスト・検査を、worktree の変更に対して走らせる手段（届けられないときに止まることを含む）
- worktree を消すときに、メインディレクトリの依存物を壊さないこと
- `EnterWorktree` の隔離と NDF のスクリプトの付き合い方を決め、`worktree` Skill に書くこと

含まない:
- 利用者のプロジェクトの pre-commit・テストのスクリプトそのものを書き換えること（carmo の `.githooks/pre-commit` は変えない）
- コンテナの外から打つ pre-commit（利用者の git の hook が `docker compose exec` でコンテナへ入る経路）を worktree へ届けること（#1388）
- 依存物のキャッシュの共有や、依存の解決の高速化
- Claude Code 本体の `EnterWorktree` の判定を変えること
- テストの走らせ方の宣言（#1334 の `test` の戦略）を変えること

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | worktree を作った | 4 箇所のいずれかの作成の処理 | 今と同じ（作成の失敗を返す） | — |
| E2 | 依存の用意の宣言を読んだ | E1 の直後 | 宣言が読めない（壊れている）: worktree は残し、宣言の場所と誤りを示して用意をしない。宣言が無い: 何もしない | E1 |
| E3 | 依存物を用意した | E2 で宣言があった | 用意のコマンドが 0 以外で終わった・複製元が無い: worktree は残し、失敗した手順と出力の末尾を示す。作成の処理は失敗として返す（受け入れ条件 4） | E2 |
| E4 | テスト環境を worktree へ向けた | コンテナ越しのテスト・検査を走らせる直前 | worktree をコンテナへ届けられない: テストを走らせずに止まり、理由を示す（メインディレクトリで走らせない） | E3（依存物がコンテナの中で要る場合） |
| E5 | pre-commit・範囲テスト・全体テストを走らせた | コミット・cross-refactoring の着手前のテスト・項目の検証・最終ゲート | 今と同じ（テストの失敗として扱う） | E3、コンテナ越しなら E4 |
| E6 | worktree を消した | `/ndf:merged`・cross 系の後片付け・`git worktree remove` | メインディレクトリの依存物は消えない・変わらない（受け入れ条件 6） | E1 |
| E7 | `EnterWorktree` の隔離の中から NDF のスクリプトを呼んだ | 利用者か Claude Code が `EnterWorktree` を使った | 拒まれたときの手が `worktree` Skill に書いてある（受け入れ条件 8） | — |

E4 の「届けられない」の判定の仕方（compose の上書き・マウントの確認）と、E6 の守り方（参照か複製か）は `design` が決める。

## 用語

| 用語 | 意味 |
| --- | --- |
| 依存物 | パッケージマネージャが入れる、追跡されないディレクトリ（`vendor/`・`node_modules/`・`.venv` など）。worktree には最初から無い |
| 依存の用意 | worktree を作った直後に、宣言に従って依存物を worktree で使える状態にすること。手段（コマンド・参照・複製）は問わない |

## 受け入れ条件

- [ ] 1. 依存の用意を宣言したプロジェクトで `worktree-setup.sh create <ブランチ>` を実行すると、終了コード 0 で終わった時点で、
  宣言した依存物（例: `vendor/bin/pint`）が worktree のパスから実行できる
- [ ] 2. 同じ宣言で、`supervise_lib/paths.py` の `ensure_worktree`・cross-review の worktree の作成・
  cross-refactoring の `prepare-worktrees.sh` が作った worktree でも、1 と同じく依存物が worktree のパスから実行できる
- [ ] 3. 依存の用意の宣言が無いプロジェクトで 4 箇所が worktree を作ると、実行される外部コマンドと作られるファイルが今と同じである
  （宣言の無い仮のリポジトリで、変更の前後の 4 箇所の出力と worktree の中身を比べて一致する）
- [ ] 4. 依存の用意が失敗した（宣言のコマンドが 0 以外で終わった・複製元が無い）とき、作成の処理は 0 以外で終わり、
  どの手順が失敗したかと出力の末尾を標準エラーに出す。worktree そのものは消さない
- [ ] 5. 依存の用意の宣言が壊れている（JSON として読めない・型が違う）とき、依存の用意をせず、宣言のファイルと誤りの箇所を示して 0 以外で終わる
- [ ] 6. 依存の用意をした worktree を `git worktree remove --force` と `/ndf:merged` の後片付けで消した後、メインディレクトリの依存物の
  ファイルの一覧と内容のハッシュが消す前と一致する
- [ ] 7. コンテナ越しのテストを宣言したプロジェクトで、worktree だけに入れた失敗するテストの変更を cross-refactoring の範囲テストと
  `worktree-testenv.sh test` で走らせると red になる（メインディレクトリのコードで走って green にならない）。worktree をコンテナへ
  届けられない構成では、テストを走らせずに 0 以外で終わり、届けられない理由を示す
- [ ] 8. `EnterWorktree` の扱いが `worktree` Skill に書かれている: NDF の手順が `EnterWorktree` を使うか使わないか、使わない場合に
  隔離の中で NDF のスクリプトが拒まれたときの代わりの手（どのディレクトリで何を打つか）。**代わりの手を 1 度実際に打って通ることを
  確かめてから書く**
- [ ] 9. carmo-system-console の開発 worktree で、PHP のファイルを変えてコミットすると pre-commit の Pint が worktree のファイルに対して走り、
  `--no-verify` なしでコミットが通る。同じ worktree で `php artisan test` の範囲テストが worktree の変更に対して走る
  （worktree だけで落ちるように変えたテストが red になる）
- [ ] 10. project-trygroup-prd のレビュー worktree で `./scripts/run-python-tests.sh <サービス>` が通る
- [ ] 11. 既存のテスト（`uv run --frozen --project . --all-extras pytest . -q -n 4`）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | 使い回す worktree では依存の用意をやり直さない（前提 3）。cross-review の 2 ラウンド目以降の開始までの時間が、宣言の無いときと比べて依存の用意の分だけ延びない |
| 運用・保守性 | 依存の用意の成否と所要秒数が、作成の処理の出力に 1 行で出る |
| セキュリティ | 依存の用意で走らせるのは宣言に書かれたコマンドだけ（前提 4）。複製・参照の書き込み先は worktree の中に限る（既存の `destination_is_safe` と同じ規則。symlink をたどって外へ書かない） |
| 移行性 | 既存の共有設定（`localenv.copy_from_main` などを書いたもの）は書き換えなしで今と同じに動く |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 共有設定かプロジェクトの宣言に、依存の用意のキーが増える（任意。無ければ今と同じ）。`worktree-setup.sh create` などの引数は変えない |
| データ | 宣言のスキーマ（`worktree.schema.json` か `project.schema.json`）にキーが増える。移行は要らない |
| 既存の振る舞い | 宣言したプロジェクトだけ、worktree の作成に依存の用意が加わる。コンテナ越しのテストは worktree を見るか、止まる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4`（仮のリポジトリで受け入れ条件 1〜7 を確かめる） |
| 静的解析 | `ruff check`（`pyproject.toml [tool.ruff]`）、シェルは既存の `script-structure` の検査 |
| 検証 | `claude plugin validate .`、`python3 scripts/check-skill-frontmatter.py` |
| 手動確認 | 受け入れ条件 9・10 は手元のサンプルで打つ（前提 6）。コマンドと出力を Pull Request に貼る。受け入れ条件 8 の代わりの手も同じ |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 共通の処理は `plugins/ndf/scripts/`（`lib/worktree-*.sh` か `project_lib/`）に置き、4 箇所から呼ぶ。cross-review と cross-refactoring で同じ役割の処理を分けない |
| コーディング規約 | `AGENTS.md`（最小限の実装、外部コマンドは書く前に実行して確かめる） |
| テスト戦略 | 仮のリポジトリを使う結合のテストで 4 箇所の振る舞いを見る。`.md` の文言を照合するテストは書かない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、宣言の無いプロジェクトで振る舞いが変わらないことの確認 |
| 確認してから行う | 既存の共有設定のキーの意味を変えること、`localenv.kind` が `compose` でないときにも `localenv.setup` を働かせること |
| 行わない | 利用者のプロジェクトのスクリプトの書き換え、依存物の推測での自動導入（宣言の無いプロジェクトで `composer install` などを走らせる） |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 依存の用意の手段（コマンド / 参照 / 複製）と宣言の置き場所（`project.json` か共有設定の `localenv`） | `design` | 設計 PR |
| コンテナへ worktree を届ける方法（`worktree-testenv.sh` の compose の上書きをレビュー worktree にも使うか）と、届けられないことの判定 | `design` | 設計 PR |
| `EnterWorktree` を使わないと決めるか、NDF のスクリプトを隔離の中でも動く形にするか。`Refusing to run it` を出す判定の調査を含む | `design` | 設計 PR |
| project-trygroup-prd の `ModuleNotFoundError` がどのコマンドから出たか（受け入れ条件 10 が今の時点で既に通るか） | `design` の実測 | 設計 PR |
| `project-decl.py` の解析が依存の用意の案を出すか（前提 5） | `design` | 設計 PR |

関連: #1333（宣言）、#1334（テストの戦略）
