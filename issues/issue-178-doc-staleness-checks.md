# issue #178: 版を上げるときに古くなる記載を検査の対象へ広げる

## 関連リンク

- issue: https://github.com/devbasex/ai-plugins/issues/178
- 指示書: `issues/parallel-batch-01/01-issue-178.md`
- 既存の検査: `scripts/validate-runtime-plugins.sh`

## モード

`standard`。テストの実績がある領域（`plugins/ndf/skills/*/tests/`）へ振る舞いを追加する変更で、
公開インタフェースの破壊や DB の移行を伴わない。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 検査 | `scripts/validate-runtime-plugins.sh` が行う機械的な突き合わせ。値が食い違えば 0 以外の終了コードで終わる |
| 説明文書 | 利用者が読む `README.md` と `plugins/ndf/README.md` の 2 本 |
| 配布 Skill | ランタイムごとの一覧ファイル（`plugins/ndf/manifests/*-skills.txt`）に載っている Skill |
| 実体 | `plugins/ndf/skills/` に置かれた Skill のディレクトリ。配布先はここから選ばれる |
| 任意 Skill | `plugins/ndf/optional-skills/` に置かれた、どの配布先にも載せない Skill |
| 更新案内 | `plugins/ndf/README.md` の「v&lt;版&gt; へ更新するとき」の節 |
| 突き合わせ元 | 説明文書に書かれた数や版数。人が書く側 |
| 突き合わせ先 | マニフェストの行数・ディレクトリの実体数・`plugin.json` の版数。機械が数える側 |

## 依頼（原文）

> 配布する Skill の数は 3 つのランタイムごとに違い、その数が 2 つの説明文書に書かれている。
> 数を機械的に突き合わせる検査は、プラグインの定義ファイルにしか届いていない。説明文書の
> 側は人が読んで直す必要があり、版を上げるたびに古い数が残る。

## 目的と非目的

達成したい状態:

- 説明文書に書かれた Skill の数が突き合わせ先と食い違ったとき、検査が 0 以外の終了コードで
  終わり、どのファイルのどの記載がどの値と食い違ったかを出力する
- 数の記載を消しても検査を通せない
- 更新案内の見出しの版数が `plugins/ndf/.claude-plugin/plugin.json` の版から遅れたとき、
  検査が失敗する

やらないこと:

- 更新案内の**本文**の正しさを機械で判定すること。本文がその版の変更内容を説明しているかは
  人が読んで判断する。代わりに版を上げる手順へ、本文を読み直す項目を加える
- 説明文書を生成物にすること。本文の書き方を人が決められる状態を保つ
- 配布 Skill の数そのものを動かすこと（`manifests/*-skills.txt` と `skills/` の増減）
- `plugins/ndf/skills/` 配下と `plugins/playwright-kit/` 配下の変更。並行して進む他の 2 件が
  同じ場所を書き換えている
- `pytest` のテストを CI で実行すること。`.github/` は書き換えてよいパスに含まれない。
  範囲外として #182 に起票した

## 前提

- 前提 1: 現時点の説明文書の数はすべて突き合わせ先と一致している。したがって、この変更を
  入れた直後の `bash scripts/validate-runtime-plugins.sh` は終了コード 0 で終わる
- 前提 2: `README.md` の元 Skill 数（35）は、実体の数（31）と任意 Skill の数（4）の和である。
  カテゴリ内訳に並ぶ 35 個の名前は、この 2 つのディレクトリの名前と一致している（実測）
- 前提 3: `README.md` はランタイムを Claude Code / Kiro / Codex の順で、
  `plugins/ndf/README.md` は Claude Code / Codex / Kiro の順で書いている。対応づけは位置では
  なくランタイム名で行う

## 受け入れ条件

記号は指示書の表に合わせる。検証手段は `scripts/tests/` のテスト名で示す
（`uv run pytest scripts/tests -q` / 31 passed / exit=0）。

- [x] A: `README.md` の「Claude Code 向け core N 個 / Kiro 向け core N 個 / Codex 向け core N 個」
      が、対応するマニフェストの行数と食い違うと検査が失敗する
      → `test_runtime_skill_count_mismatch_fails`（3 ランタイム分）
- [x] A': `README.md` から上記 3 つのランタイムのいずれかの記載が消えると検査が失敗する
      → `test_runtime_skill_count_removed_fails`（3 ランタイム分）
- [x] B: `README.md` の「元 Skills（N 個）」が、実体の数と任意 Skill の数の和と食い違うと
      検査が失敗する → `test_source_skill_count_mismatch_fails`
- [x] B': `README.md` から元 Skills の記載が消えると検査が失敗する
      → `test_source_skill_count_removed_fails`
- [x] C: `README.md` のカテゴリ内訳の合計が、実体の数と任意 Skill の数の和と食い違うと
      検査が失敗する → `test_category_total_mismatch_fails`
- [x] C': カテゴリ内訳の 1 行について、宣言された数とその行に並ぶ Skill 名の数が食い違うと
      検査が失敗する → `test_category_line_count_mismatch_fails`
- [x] C'': カテゴリ内訳が 1 行も無いと検査が失敗する
      → `test_category_breakdown_removed_fails`
- [x] D: `plugins/ndf/README.md` の配布先の表の 3 行が、対応するマニフェストの行数と食い違うと
      検査が失敗する → `test_distribution_table_mismatch_fails`（3 行分）
- [x] D': 配布先の表から 3 つのランタイムのいずれかの行が消えると検査が失敗する
      → `test_distribution_table_row_removed_fails`（3 行分）
- [x] E: `plugins/ndf/README.md` のレイアウト図に書かれた実体の数（`skills/`）と任意 Skill の
      数（`optional-skills/`）が、実際のディレクトリの数と食い違うと検査が失敗する
      → `test_layout_skill_count_mismatch_fails` / `test_layout_optional_count_mismatch_fails`
      / `test_layout_counts_removed_fails`
- [x] F: 更新案内の見出しの版数が `plugins/ndf/.claude-plugin/plugin.json` の版と食い違うと
      検査が失敗する → `test_upgrade_heading_version_stale_fails`
- [x] F': 更新案内の見出しが無いと検査が失敗する → `test_upgrade_heading_removed_fails`。
      見出しが 2 つある場合も失敗する（`test_upgrade_heading_duplicated_fails`）
- [x] 検査の失敗時の出力に、ファイルのパス・記載の識別（どの行の何か）・突き合わせた 2 つの値が
      含まれる → `test_failure_output_names_file_label_and_both_values`。実物を崩した出力は
      Pull Request 本文に載せた
- [x] 現在のリポジトリの内容では `bash scripts/validate-runtime-plugins.sh` が終了コード 0 で
      終わる → 実行して exit=0
- [x] `claude plugin validate` が通る → `claude plugin validate plugins/ndf` が exit=0
- [x] 上記のすべてに対応する自動テストが `scripts/tests/` にあり、`uv run pytest scripts/tests -q`
      が通る → 31 passed / exit=0
- [x] `docs/plugin-development-guide.md` のバージョン管理の節に、更新案内の本文を読み直す項目が
      加わっている → 手順 3 として追加
- [x] 起きてはいけないこと: 実物の `README.md` と `plugins/ndf/README.md` を書き換えるテストを
      置かない → テストは `tmp_path` に作った木だけを崩す（`doc_staleness_helpers.build_tree`）。
      実物に対しては読み取りのみ（`test_real_repository_passes`）
- [x] 起きてはいけないこと: `plugins/ndf/manifests/*-skills.txt` の行と `plugins/ndf/skills/` の
      ディレクトリが増減していない
      → `git diff --stat origin/main -- plugins/ndf/manifests plugins/ndf/skills` が差分なし

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | `validate-runtime-plugins.sh` の Python ブロックへ直接追記する | 不採用 | ヒアドキュメントの中は単体で呼び出せない。テストが検査を実行するにはリポジトリ全体（`claude plugin validate` と 3 本の installer を含む）を一時ディレクトリへ複製する必要があり、テストの実行時間と前提が重くなる |
| B | `scripts/check-doc-staleness.py` として独立させ、`--root <ディレクトリ>` を受け取る。`validate-runtime-plugins.sh` はこれを呼ぶ | 採用 | 既存の `scripts/check-markdown-links.py --root` と同じ形。テストは一時ディレクトリに最小の木を作って直接呼べる |
| C | 説明文書の数を生成物にし、`build-runtime-plugins.sh` が書き込む | 不採用 | 本文の書き方を人が決められなくなる。数の食い違いは検査で足り、生成にする必要がない |
| D | 更新案内の本文の正しさも機械で判定する | 不採用 | 本文がその版の変更内容を説明しているかは自然言語の判断で、機械では決められない |

### 更新案内について、検査と手順で役割を分ける理由

更新案内は数ではなく本文であり、内容の正しさは機械では判定できない。見出しの版数だけを
置き換えて本文を前の版のまま残しても、検査は通る。そこで役割を 2 つに分ける。

| 手段 | 担うこと |
| --- | --- |
| 見出しの版数の検査 | 版を上げたときに**必ずこの節へ触る**状態を作る。触らなければ検査が落ちる |
| 版を上げる手順の項目 | 触った人に**本文を読み直す機会**を作る。判断は人が行う |

検査だけでは本文が残る。手順だけでは節の存在を忘れる。2 つを組み合わせると、版を上げる
たびに人がこの節を開き、本文を読み直すところまでは必ず到達する。

## 不変条件

- 説明文書から数や版数を読み取れないことは、値の食い違いと同じく失敗として扱う。素通りさせると
  記載を消すだけで検査を無効化できる
- 突き合わせ先は 1 箇所から数える。マニフェストの行数の数え方（コメントと空行を除く）は既存の
  `manifest_skill_count` と同じ規則にする

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 公開インタフェース（Skill・コマンド） | 変わらない | 配布 Skill の数と名前は動かない |
| `scripts/validate-runtime-plugins.sh` の呼び出し方 | 変わらない | 引数は増えない |
| `scripts/check-doc-staleness.py` | 新設 | 単独でも `--root` を渡して実行できる |

## 修正対象

- `scripts/check-doc-staleness.py`（新設）
- `scripts/validate-runtime-plugins.sh`（新設した検査の呼び出しを 1 行追加）
- `scripts/tests/conftest.py`（新設）
- `scripts/tests/doc_staleness_helpers.py`（新設。一時ディレクトリへ最小の木を作る）
- `scripts/tests/test_doc_staleness.py`（新設）
- `scripts/tests/test_validate_wiring.py`（新設）
- `docs/plugin-development-guide.md`（バージョン管理の節へ項目を追加）
- `issues/issue-178-doc-staleness-checks.md`（この文書）

## タスク分解

### Task 1: 突き合わせ先を数える層と、一時ディレクトリを作るテストの土台

- **対象ファイル:** `scripts/check-doc-staleness.py`, `scripts/tests/conftest.py`,
  `scripts/tests/doc_staleness_helpers.py`, `scripts/tests/test_doc_staleness.py`
- **変更内容:** `--root` を受け取り、マニフェストの行数・実体の数・任意 Skill の数・
  `plugin.json` の版数を読む層を作る。テストの補助は、値を差し替えられる最小の木を
  一時ディレクトリへ書き出す
- **満たす受け入れ条件:** 土台。単独では条件を満たさない
- **進め方:** 最小の木を作って検査が終了コード 0 で終わることを確かめる失敗テスト → 実装

### Task 2: `README.md` の 3 箇所（A / B / C）を突き合わせる

- **対象ファイル:** `scripts/check-doc-staleness.py`, `scripts/tests/test_doc_staleness.py`
- **変更内容:** ランタイム別の公開 Skill 数・元 Skill 数・カテゴリ内訳を読み取り、
  突き合わせ先と比べる。読み取れないことも失敗にする
- **満たす受け入れ条件:** A / A' / B / B' / C / C' / C''
- **進め方:** 崩した木で失敗することを確かめる失敗テスト → 通す最小実装

### Task 3: `plugins/ndf/README.md` の 3 箇所（D / E / F）を突き合わせる

- **対象ファイル:** `scripts/check-doc-staleness.py`, `scripts/tests/test_doc_staleness.py`
- **変更内容:** 配布先の表・レイアウト図の 2 つの数・更新案内の見出しの版数を読み取り、
  突き合わせ先と比べる
- **満たす受け入れ条件:** D / D' / E / F / F'
- **進め方:** 崩した木で失敗することを確かめる失敗テスト → 通す最小実装

### Task 4: 既存の検査から呼ぶ

- **対象ファイル:** `scripts/validate-runtime-plugins.sh`, `scripts/tests/test_validate_wiring.py`
- **変更内容:** `check-markdown-links.py` と同じ位置に 1 行足す。配線されていることを
  テストで固定する
- **満たす受け入れ条件:** `bash scripts/validate-runtime-plugins.sh` が終了コード 0 で終わる
- **進め方:** 配線を読むテスト → 1 行追加

### Task 5: 版を上げる手順へ項目を加える

- **対象ファイル:** `docs/plugin-development-guide.md`
- **変更内容:** バージョン管理の節の手順へ、更新案内の本文を読み直す項目と、この検査が
  見出しの版数だけを見ることを添える
- **満たす受け入れ条件:** 版を上げる手順に更新案内の本文を読み直す項目が加わっている
- **進め方:** 文書の変更のためテスト駆動を適用しない

## 影響範囲

- `scripts/validate-runtime-plugins.sh` を実行する経路すべて。CI の `runtime-plugin-validate`
  ジョブと、`.githooks` 経由のローカル実行が含まれる
- 説明文書を編集する作業。数を書き換えるときは突き合わせ先と揃える必要が生じる

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 正規表現が説明文書の別の箇所を誤って拾い、正しい記載で検査が落ちる | 拾う位置を見出し・表の行・箇条書きの形で固定し、現在のリポジトリで終了コード 0 になることを確かめる |
| 説明文書の書き方を変えたとき、検査が読み取れずに落ちる | 失敗の出力へ、期待する書き方を添える |
| 並行して進む 2 件が同じファイルを書き換える | 触る範囲を `scripts/` `docs/` `issues/` と 2 本の README へ限る |
| `scripts/tests/` が CI で実行されない | 既存の `plugins/ndf/skills/*/tests/` も CI に無い。この変更の範囲外として `out-of-scope` で起票する |

## 切り戻し手順

`scripts/validate-runtime-plugins.sh` から追加した 1 行を消せば、検査は変更前の対象へ戻る。
データの移行は無い。

## 完了の定義

- [ ] 受け入れ条件をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `uv run pytest scripts/tests -q` が通る
- [ ] `bash scripts/validate-runtime-plugins.sh` が終了コード 0 で終わる
- [ ] `claude plugin validate` が通る
- [ ] 指示書の検証手順（実物を崩して戻す）を実行し、出力を Pull Request 本文へ残した
