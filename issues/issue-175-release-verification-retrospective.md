# issue #175: リリース後テストと振り返りの工程、範囲外の課題の起票

## 関連リンク

- [issue #175](https://github.com/devbasex/ai-plugins/issues/175)
- 実際に困った例: [issue #173](https://github.com/devbasex/ai-plugins/issues/173) / [PR #174](https://github.com/devbasex/ai-plugins/pull/174)

## 依頼（原文）

> /ndf:development-workflow https://github.com/devbasex/ai-plugins/issues/175
> リリース後の検証、振り返りまで実施して完了

issue #175 の本文は上のリンクを参照する。本書はその提案を実施の単位へ落としたものである。

## モード

`architecture`。利用者へ公開されるコマンド（Skill）を 3 個追加し、工程の振り分け方針そのものを
変える。変更は `development-workflow` / `quality-gates` / `cross-review` / `pr-review` / `fix` /
`tdd-cycle` / `refactoring` / `problem-solving` と配布定義にまたがる。

## 目的と非目的

達成したい状態:

- 配布した成果物が利用者の環境で受け入れ条件を満たすことを確かめる工程が、名前と手順を持つ
- 進め方のうち次に変えることを決めて残す工程が、名前と記録先を持つ
- 今回の範囲に含まれない課題を、見つけたその場で issue にする手順がある

やらないこと:

- 既存の工程（実装・構造改善・レビュー・完了判定）の手順そのものの変更。3 工程の追加と、
  そこへつなぐ 1 行の追記にとどめる
- モード判定の基準の変更。判定基準を持つのは `development-workflow` だけという構成を保つ
- 起票を自動化する仕組みの実装。判断と本文の型を定めるところまでとする

## 前提

- 前提 1: リリース後テストの起点は版の配布後に置く。配布の手続き（版の更新・タグ）が
  終わっていることを、この工程の開始条件とする
- 前提 2: 振り返りの記録は `docs/development-history/` へ連番のファイルとして置く
- 前提 3: 横断的な手順の名前は `out-of-scope` とする
- 前提 4: 横断的な手順は、呼び出し元の各 `SKILL.md` へ 1 行ずつ書き、あわせて
  `development-workflow` の工程表にも横断の行を持たせる
- 前提 5: `quality-gates` の完了報告のうち「未検証の項目」はリリース後テストの入力に、
  「範囲外と判断したもの」は起票の入力になる。どちらも引き継ぎ先を報告の中に書く
- 前提 6: 起票の重複確認は `gh issue list --search` の 1 回の検索で足りるものとする
- 前提 7: 由来は本文の参照だけで辿る。label は増やさない
- 前提 8: 3 個の Skill は 3 ランタイムすべてへ配布する

## 用語

| 用語 | 意味 |
| --- | --- |
| リリース後テスト | 配布された成果物が、利用者の環境で受け入れ条件を満たすことの確認 |
| 振り返り | この変更の進め方のうち、次に変えることを決めて残す工程 |
| 範囲外の課題 | 今回の受け入れ条件にも、直す対象にも含まれない課題 |
| 工程 | モードごとに要否が決まり、順序を持つもの |
| 横断的な手順 | 順序を持たず、複数の工程から呼ばれるもの |

## 受け入れ条件

- [ ] 1. `plugins/ndf/skills/release-verification/SKILL.md` があり、開始条件（版の配布が
      終わっていること）・対象・手順・合否の判定・出力物を定めている
- [ ] 2. `plugins/ndf/skills/retrospective/SKILL.md` があり、記録先を
      `docs/development-history/` と定め、起票の取りこぼしを拾う手順を持つ
- [ ] 3. `plugins/ndf/skills/out-of-scope/SKILL.md` があり、3 択（起票する / 範囲内へ入れる /
      起票しない）の判断・重複の確認・起票する本文の型・由来の記録を定めている
- [ ] 4. 3 個の Skill がモードの要否を自分では決めず、`development-workflow` の判定結果を
      受け取る側に徹している（各 `SKILL.md` にモードの条件表が無い）
- [ ] 5. `development-workflow` の工程表に「リリース後テスト」「振り返り」の行があり、
      モードごとの要否が `light` 不要 / `standard` 条件付きと必須 / `architecture` 必須 /
      `legacy-refactor` 必須になっている
- [ ] 6. `development-workflow` の標準フローの図に、2 つの工程と起票への破線が入っている
- [ ] 7. `development-workflow/references/workflow-modes.md` のモード別の詳細に、
      2 工程の要否が入っている
- [ ] 8. `quality-gates/SKILL.md` に、リリース後テストとの境界と、完了報告の
      「未検証の項目」「範囲外と判断したもの」の引き継ぎ先が書かれている
- [ ] 9. `tdd-cycle` / `refactoring` / `problem-solving` / `cross-review` / `pr-review` /
      `fix` の各 `SKILL.md` に、範囲外の課題を見つけたときへの案内が 1 行ずつ入っている
- [ ] 10. 3 個の Skill が `plugins/ndf/manifests/` の 3 ファイルすべてに載っている
- [ ] 11. `python3 scripts/check-skill-frontmatter.py` が終了コード 0 で終わる
- [ ] 12. `python3 scripts/check-markdown-links.py` が終了コード 0 で終わる
- [ ] 13. `bash scripts/validate-runtime-plugins.sh` が終了コード 0 で終わる
- [ ] 14. `uv run --with pytest pytest plugins/ndf -q` が変更前と同じ 923 件で通る
- [ ] 15. `CLAUDE.md` / `AGENTS.md` / `README.md` と `plugins/ndf/.claude-plugin/plugin.json` の
      Skill 数と版が、追加後の数と一致している

## 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| 起票の時期 | 見つけたその場で起票し、振り返りで取りこぼしを拾う | 採用 | 文脈が最も濃いのは発見の瞬間である。レビュー中なら、指摘へ返信するときに番号を書いて閉じられる |
| 起票の時期 | 振り返りでまとめて起票する | 不採用 | 発見からマージ後まで時間が空き、どのファイルのどの行で、なぜ範囲外と判断したのかが記憶に頼る |
| 起票の時期 | 計画ファイルの「やらないこと」へ書き足す | 不採用 | 計画ファイルはマージ後に読まれず、次の作業で拾われる場所にならない |
| 検証の起点 | 版の配布後 | 採用 | プラグインは利用者の再インストールを経て届く。配布の過程で壊れるものを拾える |
| 検証の起点 | マージ直後 | 不採用 | 配布の過程が対象から外れる |
| 記録先 | `docs/development-history/` | 採用 | マージ後も読まれる場所で、issue と PR の番号を見出しに持つ既存の書式と合う |
| 記録先 | 計画ファイルの末尾 | 不採用 | 計画ファイルはマージ後に読まれない |
| 呼び出し方 | 呼び出し元の `SKILL.md` と工程表の両方に書く | 採用 | 工程の実行中は手元の `SKILL.md` しか読まないため 1 行が要る。全体像は工程表で見える |
| 呼び出し方 | 工程表だけに書く | 不採用 | 工程の途中で `development-workflow` を読み直さないため、実際には呼ばれない |

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| 公開インタフェース（Skill） | 3 個の追加 | 追加のみ。既存の Skill 名・引数は変えない |
| 工程の並び | 末尾に 2 工程を追加 | 既存の工程の順序と責務は変えない |
| 完了報告の形式 | `quality-gates` の 3 項目に引き継ぎ先を添える | 項目自体は変えない |

## 修正対象

```text
plugins/ndf/skills/release-verification/SKILL.md          （新規）
plugins/ndf/skills/retrospective/SKILL.md                 （新規）
plugins/ndf/skills/out-of-scope/SKILL.md                  （新規）
plugins/ndf/skills/development-workflow/SKILL.md
plugins/ndf/skills/development-workflow/references/workflow-modes.md
plugins/ndf/skills/quality-gates/SKILL.md
plugins/ndf/skills/tdd-cycle/SKILL.md
plugins/ndf/skills/refactoring/SKILL.md
plugins/ndf/skills/problem-solving/SKILL.md
plugins/ndf/skills/cross-review/SKILL.md
plugins/ndf/skills/pr-review/SKILL.md
plugins/ndf/skills/fix/SKILL.md
plugins/ndf/manifests/claude-skills.txt
plugins/ndf/manifests/codex-skills.txt
plugins/ndf/manifests/kiro-skills.txt
plugins/ndf/.claude-plugin/plugin.json
CLAUDE.md / AGENTS.md / README.md
docs/specifications/ndf-skill-inventory.md
```

## タスク分解

### Task 1: 範囲外の課題を起票する横断的な手順を作る

- **対象ファイル:** `plugins/ndf/skills/out-of-scope/SKILL.md`、3 つの manifest
- **変更内容:** 3 択の判断・重複の確認・起票する本文の型・由来の記録を定める
- **満たす受け入れ条件:** 3、4、10
- **進め方:** 文書のため、検査スクリプトを先に失敗させてから書く（`check-skill-frontmatter.py`
  は manifest に載った Skill の実体が無いと失敗する）

### Task 2: リリース後テストの工程を作る

- **対象ファイル:** `plugins/ndf/skills/release-verification/SKILL.md`、3 つの manifest
- **変更内容:** 開始条件・対象・手順・合否の判定・出力物を定める。入力は `quality-gates` の
  完了報告の「未検証の項目」とする
- **満たす受け入れ条件:** 1、4、10
- **進め方:** Task 1 と同じ

### Task 3: 振り返りの工程を作る

- **対象ファイル:** `plugins/ndf/skills/retrospective/SKILL.md`、3 つの manifest
- **変更内容:** 観点・記録先・起票の取りこぼしの拾い方を定める
- **満たす受け入れ条件:** 2、4、10
- **進め方:** Task 1 と同じ

### Task 4: 工程の振り分けへ 2 工程と横断の行を足す

- **対象ファイル:** `development-workflow/SKILL.md`、同 `references/workflow-modes.md`
- **変更内容:** 工程表に 3 行、標準フローの図に 2 工程と起票の破線、モード別の詳細に要否
- **満たす受け入れ条件:** 5、6、7
- **進め方:** 図はガイドの上限に収まる形で書く

### Task 5: 既存の工程からつなぐ

- **対象ファイル:** `quality-gates` / `tdd-cycle` / `refactoring` / `problem-solving` /
  `cross-review` / `pr-review` / `fix` の各 `SKILL.md`
- **変更内容:** 範囲外の課題を見つけたときの案内を 1 行ずつ。`quality-gates` には
  リリース後テストとの境界と引き継ぎ先を加える
- **満たす受け入れ条件:** 8、9

### Task 6: 配布と版の記載を合わせる

- **対象ファイル:** `plugins/ndf/.claude-plugin/plugin.json`、`CLAUDE.md` / `AGENTS.md` /
  `README.md`、`docs/specifications/ndf-skill-inventory.md`、生成物
- **変更内容:** Skill 数と版を追加後の数へ更新し、`scripts/build-runtime-plugins.sh` で
  生成物を同期する
- **満たす受け入れ条件:** 11、12、13、14、15

## 影響範囲

Skill を 3 個増やすと、各ランタイムの初期一覧に載る `description` の総量が増える。
`check-skill-frontmatter.py` は配布先ごとの初期一覧予算を検査するため、この検査の結果で
超過の有無を確かめる。

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 初期一覧の予算を超える | `check-skill-frontmatter.py` で確かめ、超えたら `description` を短くする |
| 横断的な手順が工程と誤読され、モードで要否を決められる | 各 `SKILL.md` に「工程ではない」ことと、モードの要否を持たないことを書く |
| 起票が増えすぎて扱えなくなる | 3 択の 3 つ目（起票しない）を残し、理由を 1 行書く形にする |

## 切り戻し手順

文書と配布定義の変更のみで、データの移行を伴わない。PR 単位で `git revert` すれば戻る。

## 完了の定義

- [x] 受け入れ条件 1〜15 をすべて満たし、条件ごとに検証手段と結果が対応している
- [x] `architecture` モードの検証の段階 1〜4 を通す（詳細は `quality-gates`）
- [x] 版の配布後にリリース後テストを実施し、結果を記録する
- [x] 振り返りを `docs/development-history/` へ残す（[04-2026-08-31.md](../docs/development-history/04-2026-08-31.md)）

## リリース後テスト

対象の版: NDF v9.4.0（2026-08-31 配布。3 Skill は v9.3.0 で追加し、v9.4.0 に含まれる）
導入経路: 利用者が行うのと同じ再取得を 3 ランタイムで実施した

| ランタイム | 実行したこと | 実行時刻 | 結果 |
| --- | --- | --- | --- |
| Claude Code | `claude plugin marketplace update ai-plugins` → `claude plugin update ndf@ai-plugins` | 2026-08-31 08:24 | 合格 / 9.3.0 → 9.4.0 |
| Codex | `codex plugin marketplace upgrade ai-plugins` → `codex plugin list` | 2026-08-31 08:24 | 合格 / 9.4.0 |
| Kiro | `bash plugins/ndf/dev.kiro/install.sh --project <検証用> --yes` | 2026-08-31 08:23 | 合格 / 9.4.0・Skill 29 + steering 1 |

受け入れ条件は、開発の作業ツリーではなく導入済みの
`~/.claude/plugins/cache/ai-plugins/ndf/9.4.0` を対象に確かめた。

| 受け入れ条件 | 実行したこと | 実行時刻 | 結果 |
| --- | --- | --- | --- |
| 1〜3. 3 Skill の `SKILL.md` と必須の節 | 導入側の `skills/<名前>/SKILL.md` に開始条件・出力物・記録先・3 択があることを確認 | 2026-08-31 08:24 | 合格 |
| 4. モードの条件表を持たない | 3 Skill いずれもモードの条件表の行数 0 | 2026-08-31 08:24 | 合格 |
| 5〜7. `development-workflow` の工程表・図・詳細 | 工程表に 2 行、図に 2 工程と起票への破線 4 本、`workflow-modes.md` に 7 箇所 | 2026-08-31 08:24 | 合格 |
| 8. `quality-gates` の境界と引き継ぎ先 | `release-verification` への言及 3 箇所、「未検証の項目」4 箇所 | 2026-08-31 08:24 | 合格 |
| 9. 呼び出し元 6 Skill の案内 | 6 Skill すべてに `out-of-scope` への案内がある | 2026-08-31 08:24 | 合格 |
| 10. `manifests/` の 3 ファイルすべてに載る | 3 Skill × 3 ファイルの 9 組すべてで掲載を確認 | 2026-08-31 08:24 | 合格 |
| 11〜14. 検査スクリプトとテスト | `check-skill-frontmatter.py` / `check-markdown-links.py` / `validate-runtime-plugins.sh` が exit 0、`pytest scripts/tests plugins/ndf -q` が 1000 passed | 2026-08-31 08:10 | 合格 |
| 15. 版と Skill 数の一致 | 導入側の `plugin.json` が 9.4.0。`check-doc-staleness.py` が exit 0 | 2026-08-31 08:24 | 合格 |

合否: **合格**（15 件すべてを実施、保留なし）

### 検証で見つかった事実

| # | 事実 | 3 択の判断 |
| --- | --- | --- |
| 1 | 検証の開始時点で、Claude Code の導入済みの版は 9.3.0、Codex のマーケットプレイスは 2026-08-22 の revision だった。版を配布しても、利用者が再取得を実行するまで届かない | **範囲内へ入れる**（担当 B / #188）。#188 は版を上げる担い手と時期を扱う。「利用者が取得する」段はその延長にあり、別に起票すると同じ工程の話が 2 件に分かれる |
| 2 | `codex plugin marketplace upgrade` の後も `~/.codex/config.toml` の `last_updated` / `last_revision` が古いまま残る。プラグインの実体は 9.4.0 に入れ替わっている | **起票しない**。Codex CLI 側のメタデータの扱いであり、NDF の配布物では直せない |

起票したもの: なし（事実 1 は #188 の範囲へ入れた）
