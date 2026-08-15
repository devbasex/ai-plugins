# 変更するファイルと作業単位

[← 01-overview.md](01-overview.md)

## 1. 新規に追加するファイル

```text
plugins/ndf-shared/skills/cross-refactoring/
├── SKILL.md
├── docs/01-state-and-propose.md      # Step 0〜3
├── docs/02-apply-and-review.md       # Step 4〜6
├── docs/03-review-viewpoints.md      # レビュー観点
├── scripts/refactor.py               # 状態管理（uv 自己完結 / 標準ライブラリのみ）
├── scripts/prepare-worktrees.sh      # 作業ディレクトリ作成・同期・Skill 配置
├── scripts/launch-cli.sh             # claude / codex / gemini / kiro をフェーズ引数で起動
├── prompts/propose.md                # 提案プロンプト雛形
├── prompts/apply.md                  # 適用プロンプト雛形
├── prompts/review.md                 # レビュープロンプト雛形
├── prompts/fix.md                    # 指摘修正プロンプト雛形
└── tests/                            # pytest
```

## 2. 変更する既存ファイル

- `plugins/ndf-shared/skills/cross-review/scripts/lib/` — 新規。両 Skill が共有する層。
  監視・一時ディレクトリ解決・CLI 起動・作業ディレクトリ準備・担当の決定・モデルの記録・
  計測の集計を置く。範囲と置き場所の理由は
  [09-cross-review-alignment.md](09-cross-review-alignment.md)
- `plugins/ndf-shared/skills/cross-review/scripts/monitor.py` — 汎用化して `lib/` へ移し、
  既存パスからは移設先を読む（作業単位 9）
- `plugins/ndf-shared/skills/cross-review/scripts/lib/_gemini-env.sh` — 新規に切り出す。
  `launch-gemini.sh` の信頼済みディレクトリ設定と設定ファイル無害化を分離し、
  共通の起動スクリプトから読み込む
- `plugins/ndf-shared/skills/external-ai/references/cli-kiro.md` — 新規。Kiro CLI の
  非対話実行手順を `external-ai` の補助ファイル体系に載せる
- `plugins/ndf-shared/skills/external-ai/references/cli-claude.md` — 新規。`claude -p` の
  ヘッドレス実行手順
- `plugins/ndf-shared/skills/external-ai/SKILL.md` — 補助ファイル表に上記 2 つを追加し、
  `description` の対象 CLI を 4 つへ更新する。CLI の選び方の比較表も 4 者へ広げる
- `plugins/ndf-shared/manifests/{claude,codex,kiro}-skills.txt` — `cross-refactoring` を追加
- `plugins/ndf-{claude,codex,kiro}/**` — `bash scripts/build-runtime-plugins.sh` の生成物
- `plugins/ndf-claude/.claude-plugin/plugin.json` /
  `plugins/ndf-codex/.codex-plugin/plugin.json` — `8.0.0` から `8.1.0` へ
- `CLAUDE.md` / `README.md` / `docs/ndf-plugin-reference.md` /
  `docs/specifications/ndf-skill-inventory.md` / 各ランタイムの README — Skill 数と新 Skill の記述

## 3. 配布範囲

**3 ランタイムすべてに配布する。** 参加者を CLI に統一し、参加者の母集合を役割ごとに
定義したことで、Skill がランタイム中立になったためである。最終ゲートで呼ぶ
`/ndf:cross-review` も 3 ランタイムすべてに配布済みで、前提が揃っている。

ホストごとに必要な CLI は次のとおり。初期化時に不足を検出したらその時点で理由を明示して
失敗する（進行の途中での発覚を避ける）。

| ホスト | 必要な CLI |
| --- | --- |
| Claude Code | `codex` / `gemini` / `kiro-cli` |
| Codex | `claude` / `gemini` / `kiro-cli` |
| Kiro CLI | `claude` / `codex` / `gemini` |

## 4. 作業単位

### 1. 状態管理の骨格

- **対象:** `scripts/refactor.py`, `tests/test_refactor_init.py`
- **内容:** `init` / `start-round` / `advance` / `status` を実装する。`start-round` は
  ラウンド番号に加えて `IMPL` / `REVIEWERS` / `REVIEWERS_CSV` を返す。輪番はラウンド単位
  なので、項目ごとに割り当てを引くサブコマンドは不要である。
  `init` は Pull Request 番号・対象範囲・各上限値を受け取り、リポジトリ情報と
  `baseline_test` を記録して状態ファイルを生成する。
  **提案・レビューの母集合（全 − ホスト）と適用の母集合（全 − gemini）を別々に確定**し、
  実装担当がホストと同じときにレビュー候補が 3 者になる場合の絞り込みもここに置く。
  **`--model <ランタイム>=<モデル>` を繰り返し受け取り記録する。** 未指定は `null`
  （CLI の既定）とし、以後のラウンドで変更しない。
  一時ディレクトリの解決は `cross-review` と同じ優先順（環境変数 >
  `<work worktree>/.cross_refactoring/`）にし、呼び出し規約も揃える。

### 2. 作業ディレクトリ準備と Skill 配置

- **対象:** `scripts/prepare-worktrees.sh`, `tests/test_prepare_worktrees.py`
- **内容:** `work/`（head ブランチ）と読み取り用 3 つ（`--detach`）を冪等に作成し、続けて
  [Skill の配置](04-skill-provisioning.md)を行う。各作業ディレクトリのランタイム標準の
  配置先に `refactoring` / `tdd-cycle` / `quality-gates` があるかを検出し、無ければホストの
  `$PLUGIN_ROOT/skills/` からコピーする。既存があれば上書きしない。
  `work/` には**3 ランタイム分すべての配置先**を作る（実装担当がラウンドごとに変わるため）。
  生成物が差分に混入しないよう `.git/worktrees/<name>/info/exclude` へ追加し、結果を
  状態ファイルへ記録する。ホスト側にも無い Skill があれば失敗させる。
  既存パスが現リポジトリの登録済み作業ディレクトリでなければ退避して作り直す
  （`cross-review` の既存ガードを踏襲）。`sync <sha>` サブコマンドで読み取り用を指定 SHA へ
  同期する。**Kiro 専用のエージェント定義は生成しない**（承認はフラグで与えるため）。

### 3. Kiro / Claude CLI の非対話実行手順

- **対象:** `plugins/ndf-shared/skills/external-ai/references/cli-kiro.md`,
  `plugins/ndf-shared/skills/external-ai/references/cli-claude.md`,
  `issues/ndf-development-skills/03-runtime-conformance.md`
- **状態:** 調査は完了している。claude 2.1.233 と kiro-cli 2.18.0 の実機で起動形式・完了検知・
  権限まわりを確定した。codex と gemini は `cross-review` に実績があるため対象外。
  **残作業は 2 つの参照ファイルの執筆のみ**で、他の作業単位を止める要因は無い。
- **内容:** 実行ログと出典は[検証記録](../issue-113-task3-cli-verification.md)にある。
  `cli-kiro.md` に必ず書く注意点は 4 つ。

  1. **終了コードで成否を判定しない**（ツール拒否でもシェルの失敗でも 0 を返す）
  2. **標準エラー出力の照合前に ANSI エスケープを除去する**（`NO_COLOR` では消えない）
  3. **ツールの絞り込みは使わない**（シェル経由で迂回でき防御にならない一方、綴り違いが
     警告のみで素通りする。隔離は作業ディレクトリで担保する）
  4. **`agent set-default` と `agent create` を呼ばない**（前者はマシン全体の設定を奪い、
     後者はエディタを開いて非対話実行が止まる）

### 4. 提案フェーズ

- **対象:** `scripts/launch-cli.sh`, `prompts/propose.md`, `refactor.py merge-proposals`,
  `tests/test_merge_proposals.py`
- **内容:** 3 CLI に同一プロンプトで提案させ、結果ファイルへ提出させる。`merge-proposals` が
  語彙検証・重複排除・優先度付け・しきい値による採否・上限件数での切り出しを行い、
  改善項目を生成する。採用 0 件なら終了コード 2 を返して提案ラウンドの繰り返しを終える。
  `launch-cli.sh` はランタイム名で 4 分岐する（起動形式は
  [03-runtime-notes.md](03-runtime-notes.md)）。
  **提案フェーズにホストは現れない**（母集合にいないため）。ただし `launch-cli.sh` は
  **ホストと同じランタイムを起動しうる**（適用担当のとき）ので、「ホストなら起動しない」
  といった分岐を入れてはならない。対象範囲を渡し、提案が無制限に広がらないようにする。
- **モデル指定:** 状態ファイルの指定値が非 `null` なら各 CLI のモデルフラグを付ける。
  `null` なら付けずに CLI の既定へ委ねる。分岐はフラグ名の違いだけで、値の検証は CLI に任せる。

### 5. 適用フェーズ

- **対象:** `prompts/apply.md`, `refactor.py merge-apply`, `tests/test_merge_apply.py`
- **内容:** 実装担当を**1 ラウンド 1 回**起動し、採用した改善項目を優先度順に直列適用させる。
  結果ファイルは**項目ごとの結果配列**（項目 ID / コミット列 / 各コミットのテスト結果 /
  実差分行数 / 状態）を持つ。`merge-apply` は項目ごとに差分予算の超過・テスト失敗・
  コミット 0 件を検証し、**失敗した項目だけを見送りにして残りは採用する**
  （1 件の失敗でラウンドを止めない）。全件失敗のときだけ終了コード 2 で次ラウンドへ進む。
  作業ディレクトリは `work/` に固定し、`--force` と `--no-verify` を禁止する。
- **要点:** プロンプトに**項目ごとに 1 手 1 コミットへ分けること**を必須要件として書く。
  取り消し範囲が項目単位で決まらなくなるため、複数の項目を 1 コミットにまとめた場合は
  失敗として扱う。
- **トレーラーの検証:** 各コミットに `Item-Id` / `Round` / `Impl-Runtime` / `Impl-Model` が
  揃っていることを `git log --format=%(trailers)` で検証し、欠けていれば当該項目を失敗として
  扱う。`Impl-Model` には CLI が報告する実際のモデル名を書かせ、取得できないランタイムでは
  `default` を許容する。

### 6. レビューフェーズ

- **対象:** `prompts/review.md`, `docs/03-review-viewpoints.md`, `refactor.py judge-review`,
  `tests/test_judge_review.py`
- **内容:** レビュー担当 2 者を並列起動し、**ラウンドの差分**に対してレビューさせる。
  指摘は AI 自身が `gh api` でインラインコメントとして直接投稿する（`cross-review` と同じ
  方針でホストの作業文脈を汚さない）。`judge-review` は 2 者の承認で完了（終了コード 0）、
  1 つでも変更要求があれば修正へ遷移（終了コード 2）する。
- **要点:** レビュー結果の各指摘に**改善項目 ID を必須**とする。取り消しを項目単位で行う
  ために必要であり、そのラウンドに無い ID や欠落は**差し戻して再レビューさせる**。
  ラウンド全体に対する指摘は ID を `null` と明示させ、取り消し時はラウンド全件の対象とする。
- **実行主体の明記:** レビューコメントの先頭にランタイムとモデルを読める形で書かせ、
  集計用の HTML コメントを併記させる。レビュー結果にもランタイムとモデルを持たせ、
  `judge-review` が状態ファイルへ記録する。

### 7. レビュー収束と見送り

- **対象:** `prompts/fix.md`, `refactor.py merge-fix` / `should-abandon` / `abandon-items`,
  `tests/test_abandon_items.py`
- **内容:** 指摘修正は実装担当に投げ、**ラウンドの未解決指摘をまとめて**修正させ、返信と
  解決まで実行させる。修正ラウンド数が上限に達したら見送りへ移る。
- **見送りは項目単位:** `abandon-items` は未解決の指摘に紐づく項目 ID を集計し、
  **該当項目のコミット群だけを取り消して** push する。指摘の無い項目と解決済みの項目は
  Pull Request に残す。ID が `null` の未解決指摘が 1 件でもあれば、そのラウンドで適用した
  項目を全件取り消す。いずれの場合も開いているレビュースレッドに理由を返信して解決し、
  見送り項目として記録する。**Pull Request に中途半端な状態を残さない**ことを保証する。

### 8. 提案ラウンドの収束判定と最終ゲート

- **対象:** `refactor.py advance` / `report`, `SKILL.md`
- **内容:** 採用 0 件 / 上限到達 / 前ラウンドとの提案重複率 70% 以上のいずれかで提案ラウンドの
  繰り返しを終了し、終了理由を記録する。重複率は `path` + `symbol` + `smell` の集合比較で
  求める。終了後は `/ndf:cross-review <PR>` で Pull Request 全体を承認収束にかけ
  （レビューはラウンド単位なので、ラウンドを跨いだ整合はここで見る）、Draft を解除して、
  ラウンド表・項目表・見送り項目・残った提案を報告する。
- **計測:** 毎ラウンド Pull Request 本文のラウンド表を更新する。`report --metrics` は
  [05-measurement.md](05-measurement.md) の指標をランタイムとモデルの組で集計し、
  **既定モデルで走ったラウンドを区別**して出す。指定値と実測値が食い違うラウンドは警告を
  併記する。集計結果には比較として読むときの限界を必ず添える。

### 9. 監視スクリプトの汎用化

- **対象:** `plugins/ndf-shared/skills/cross-review/scripts/monitor.py`,
  `plugins/ndf-shared/skills/cross-review/tests/test_monitor_generic_stem.py`
- **内容:** 多軸監視は運用で作り込まれた資産なので**複製しない**。次のオプションを後方互換で
  追加する。
  - `--tmp-dir DIR` — 一時ディレクトリの明示指定
  - `--agents <csv>` — 監視対象の一般化。`claude` と `kiro` を含む任意の組み合わせを受け付ける
  - `--stem-template "{agent}-propose-rf{id}"` — 既定は現行のまま
  - `--state-file PATH` — 状態ファイルのパス指定（番号からの導出も維持）

  あわせて早期エラーの検出語を追加する。

  - **claude**: `permission_denials` が非空 / `is_error` が真 /
    `--dangerously-skip-permissions cannot be used with root` を致命とする
  - **kiro**: `is rejected because it matches one or more rules on the denied list` を致命と
    する。`--trust-all-tools` を渡していれば本来出ないが、フラグが効かない環境を検知する
    ために残す。**プロセスは終了コード 0 で正常終了してしまう**ため終了コードでは検知
    できない。照合の前に ANSI エスケープを除去する。無反応の打ち切りは、外部サーバの
    起動待ちなど別要因への保険として有効にしておく

  **既存テストを 1 つも変更せずに通す**ことを完了条件とする。

  この汎用化は、両 Skill が使う共通層を切り出す作業（[09-cross-review-alignment.md](09-cross-review-alignment.md)
  の作業単位 13）と同時に行う。監視スクリプトは共通層の最初の住人になる。

### 10. SKILL.md と docs

- **対象:** `SKILL.md`, `docs/01-state-and-propose.md`, `docs/02-apply-and-review.md`
- **内容:** `cross-review` と同じ構成（設計方針表 / 引数表 / 全体フロー / ステップの骨組み /
  アンチパターン / 完了報告）で執筆する。frontmatter は
  `plugins/ndf-shared/skills/README.md` の規約に従い、`description` の 1 文目にトリガ語を置く。
  `python3 scripts/check-skill-frontmatter.py` を通す。予算に収まらない場合は、予算値の
  見直しか既存 `description` の圧縮を同じ Pull Request で行う。
- 引数表に `--model <ランタイム>=<モデル>`（繰り返し可）を載せる。`argument-hint` は予算が
  厳しいので `<pr> [--scope <path>...] [--model <rt>=<name>]` 程度に短く保つ。
- `docs/02-apply-and-review.md` にコミットトレーラーの形式、レビューコメントの署名形式、
  集計の読み方と比較の限界を書く。

### 11. テスト

- **対象:** `plugins/ndf-shared/skills/cross-refactoring/tests/`
- **内容:** `cross-review` と同じ方式（一時ディレクトリに状態ファイルを組み立てて
  サブコマンドを実行）で全サブコマンドを単体テストする。外部プロセス（gh / 各 CLI /
  git push）は呼ばない。最低限の観点は次のとおり。
  - 提案マージの重複排除・語彙外の降格・しきい値・上限件数
  - **ホスト別の母集合確定**: ホストが claude / codex / kiro の 3 ケースで提案・レビューの
    母集合が「全 4 ランタイム − ホスト」になり、ホストが含まれない
  - **適用の母集合がホストによらず `["claude", "codex", "kiro"]` になり、gemini を含まない**
  - 輪番が実装担当とレビュー担当を必ず分離する（全ラウンドで重複しない）
  - **実装担当がホストと同じラウンドでレビュー担当が 3 者にならず 2 者に絞られ**、
    除外される 1 者がラウンドを跨いで入れ替わる
  - 適用の母集合が縮んだときもレビュー担当は常に 2 者になる
  - 適用結果のマージが**失敗した項目だけを見送りにして残りを採用**し、全件失敗のときだけ
    終了コード 2 を返す
  - レビュー判定の遷移（2 者承認 / 1 者変更要求 / 結果欠損）と、指摘の項目 ID 欠落・
    未知 ID の差し戻し
  - 見送り処理が**未解決の指摘に紐づく項目だけ**を取り消し対象に選び、ID が `null` の
    未解決指摘があればラウンド全件を対象にする
  - 見送り判定が修正ラウンドの上限到達時のみ真を返す
  - 提案ラウンドの収束条件 3 つ
  - **Skill 配置**: 既存を上書きせず記録する / 無ければコピーして記録する / ホスト側にも
    無ければ失敗する / `work/` に 3 ランタイム分の配置先ができる / 除外設定へ追記される
  - **モデル指定の解析**（繰り返し指定 / 未指定は `null` / 未知のランタイム名はエラー）と、
    指定値が全ラウンドで不変であること
  - **コミットトレーラーの検証**: 4 つが揃うコミットは通り、1 つでも欠ければ当該項目が失敗になる
  - **集計**: ランタイムとモデルの組で指標が正しく出る。既定モデルのラウンドが区別され、
    指定値と実測値の食い違いが警告になる
  - ラウンド開始の再開冪等性（同一ラウンドの再実行で担当が変わらない）

### 12. 配布物の同期とドキュメント更新

- **対象:** 3 つの manifest, `plugins/ndf-{claude,codex,kiro}/**`, `CLAUDE.md`, `README.md`,
  `docs/ndf-plugin-reference.md`, `docs/specifications/ndf-skill-inventory.md`,
  各ランタイムの README, `plugin.json` 2 つ, `plugins/ndf-kiro/VERSION`
- **内容:** manifest 追加後に `bash scripts/build-runtime-plugins.sh` で同期し、
  `--check` / `scripts/validate-runtime-plugins.sh` / `python3 scripts/check-markdown-links.py` /
  `claude plugin validate` を通す。Skill 数の記述（30 → 31、Claude Code 26 → 27 /
  Codex 24 → 25 / Kiro 25 → 26）を更新し、版数を `8.1.0` に上げる。

## 5. 続く作業単位

13 以降は `cross-review` への展開である。内容と進め方は
[09-cross-review-alignment.md](09-cross-review-alignment.md) を参照。

| 番号 | 内容 | 着手時期 |
| --- | --- | --- |
| 13 | 共通層の切り出し | 作業単位 9 と同時 |
| 14 | `cross-review` の担当制 | 本 Skill が一周してから |
| 15 | `cross-review` の修正を CLI 駆動にする | 14 の後 |
| 16 | `cross-review` の計測 | 15 の後 |
