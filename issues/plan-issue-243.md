# 243 / 282 / 281 / 287: 進行の記録を 1 つの Skill へ集める

## 関連リンク

- 設計: [parallel-batch-08/03-issue-243-282-281-287.md](parallel-batch-08/03-issue-243-282-281-287.md)
- 全体の境界と順序: [parallel-batch-08/00-overview.md](parallel-batch-08/00-overview.md)
- 課題: #243 / #282 / #281 / #287

## モード

`architecture`。新しい Skill（公開コマンド）を追加し、15 個の `SKILL.md` と
`projects-sync.sh` にまたがる。

## 目的と非目的

達成したい状態:

- 進行を記録する手順を持つ Skill が 1 つあり、15 本の `SKILL.md` はそれを指す 1 行だけを持つ
- 盤面の宣言が無いリポジトリでも issue へ進行が残る
- 開発中のリポジトリで実行したとき、手元の `plugins/ndf/scripts` が採られる
- 盤面にアイテムが無いとき、追加を試み、できないときは 1 行知らせる

やらないこと:

- 工程名の一覧を新しい Skill が持つ（設計の決定 8。工程表が唯一の基準のまま）
- 判定（工程の飛ばしの検知）の入力にする（決定 9）
- Codex の導入した実体を `$SCRIPTS` の候補へ足す（決定 3）
- 盤面を自動で作る（決定 6。作成は承認を得てから）

## 受け入れ条件

- [ ] 1. `grep -rl "projects-sync.sh" skills/*/SKILL.md` が 0 件になる
- [ ] 2. 宣言の無いリポジトリで `progress-record.sh` が issue 本文を更新する
- [ ] 3. 進行の節の外を書き換えない
- [ ] 4. `$SCRIPTS` の解決手順が独立した参照にあり、既存の 2 つのテストがそこを読む
- [ ] 5. 開発中のリポジトリの `plugins/ndf/scripts` が Codex の控えより先に当たる
- [ ] 6. 解決手順にシェルをまたぐ扱いの注意がある
- [ ] 7. アイテムが無いときに追加を試み、できないときは標準エラーへ 1 行出して終了コード 0
- [ ] 8. 解決した識別子を控え、2 回目の記録で盤面の全件を読まない
- [ ] 9. 問い合わせの上限に達したときに 1 行知らせる
- [ ] 10. 工程名の一覧が工程表と一致する（#231 の検査）
- [ ] 11. 4 つの manifest に載り、Skill の数を記した説明文書が 34 になる

## 互換性

| 対象 | 変更 | 互換性の扱い |
| --- | --- | --- |
| `projects-sync.sh` の呼び方 | 変えない | 既存の呼び出しはそのまま動く |
| `$SCRIPTS` の解決手順の位置 | `projects-tracking.md` から `scripts-lookup.md` へ | 参照する 15 本と 2 つのテストを同時に直す |
| 新しい Skill | 追加 | 既存の Skill は変わらない |

## タスク分解

### Task 1: `$SCRIPTS` の解決を独立した参照へ移し、候補の並びを直す

- **対象ファイル:** `development-workflow/references/scripts-lookup.md`（新規）、
  `references/projects-tracking.md`、`development-workflow/tests/test_projects_scripts_lookup.py`、
  `worktree/tests/test_scripts_reference.py`
- **変更内容:** 手順を移し、開発中のリポジトリを候補の先頭へ置く。シェルをまたぐ注意を書く
- **満たす受け入れ条件:** 4 / 5 / 6
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 2: issue 本文へ進行を記録する

- **対象ファイル:** `scripts/progress-record.sh`（新規）、テスト（新規）
- **変更内容:** `## 進行` の節だけを差し替える。節が無ければ末尾へ足す
- **満たす受け入れ条件:** 2 / 3
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 3: 盤面の宛先の解決とアイテムの追加

- **対象ファイル:** `scripts/projects-sync.sh`、`scripts/lib/projects-common.sh`、テスト
- **変更内容:** 識別子を控え、アイテムが無ければ追加を試み、上限に達したら知らせる
- **満たす受け入れ条件:** 7 / 8 / 9
- **進め方:** 失敗するテスト → 最小実装 → 整理

### Task 4: 新しい Skill を作り、15 本の定型文を置き換える

- **対象ファイル:** `skills/progress-tracking/`（新規）、15 本の `SKILL.md`、
  `manifests/*-skills.txt`、`README.md`、`plugins/ndf/README.md`
- **変更内容:** 手順を持つ Skill を作り、呼び出し側を 1 行へ置き換える
- **満たす受け入れ条件:** 1 / 10 / 11
- **進め方:** 文書と配布物。検査で確かめる

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 15 本の置き換えで参照が壊れる | `check-markdown-links.py` と `check-cross-skill-refs.py` が拾う |
| 解決手順の移設でテストが古い位置を読む | 2 つのテストの参照先を同じ変更で直す |
| issue 本文の書き換えで人の編集を消す | 節の外を触らない。テストで固定する |

## 完了の定義

- [ ] 受け入れ条件 11 件をすべて満たす
- [ ] `uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest . -q` が通る
- [ ] `bash scripts/validate-runtime-plugins.sh` が通る
