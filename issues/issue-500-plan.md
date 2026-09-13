# #500: README.md 22 本を規約の役割へ合わせる（実装計画）

## 関連リンク

- issue: https://github.com/devbasex/ai-plugins/issues/500
- 要求と受け入れ条件: [issue-500-requirements.md](issue-500-requirements.md)
- 設計: [issue-500-design.md](issue-500-design.md)（設計 PR #608、`develop` へマージ済み）

## モード

standard（複数の文書と検査スクリプトの案内文・テストの雛形にまたがり、配布物の名前が変わる）

## 目的と非目的

達成したい状態:

- 根の `README.md` が入口の役割だけを持ち、変える人向けの規約と手順を正本へのリンクで示す
- Skill の書き方の規約が `plugins/ndf/skills/AUTHORING.md` の名前で見つかる

やらないこと:

- `docs/versioning-and-distribution.md` の書き換え（設計の決定 5。検査 J が章 2 の語を位置の決め手に読む）
- 記録（`issues/`・`docs/development-history/`・`docs/ndf-version-decisions.md`）の旧いパスの書き換え（決定 7）
- 範囲外で起票済みの #605 / #606 / #607、Skill を足すときに触る箇所の一覧（#525）

## 受け入れ条件

要求仕様の AC1〜AC14 をそのまま使う。検証手段は設計の「テスト設計」の表にある。

## 修正対象

| 区分 | ファイル |
| --- | --- |
| 改名 | `plugins/ndf/skills/README.md` → `plugins/ndf/skills/AUTHORING.md` |
| 変更 | `README.md` / `docs/plugin-development-guide.md` / `plugins/ndf/README.md` / `CONTRIBUTING.md` / `docs/project-overview.md` |
| 改名の追随 | `CLAUDE.md` / `.github/ISSUE_TEMPLATE/skill_proposal.yml` / `docs/specifications/ndf-design-phase.md` / `docs/specifications/ndf-skill-inventory/` の 3 本 / `plugins/ndf/skills/release/references/form-package-plugin.md` / `plugins/ndf/skills/skill-stats/scripts/skill-stats.py` / `scripts/check-skill-frontmatter.py` / `scripts/check-skill-repo-assumptions.py` / `scripts/tests/doc_staleness_helpers.py` |

## タスク分解

設計の「処理の流れ」の順に進める。**移す先の見出しを先に作り、消す節を後に消す。**

### Task 1: 規約を改名し、参照を追随する

- **対象ファイル:** 改名 1 本と改名の追随 11 本、`CONTRIBUTING.md` L113、`plugins/ndf/README.md` のレイアウト図
- **変更内容:** `git mv` と、パスの文字列の置き換え。本文は変えない
- **満たす受け入れ条件:** AC2、AC7
- **進め方:** 受け入れ条件の検査（AC2・AC7 の `git grep`）が落ちることを先に確かめ、改名して通す

### Task 2: 開発ガイドへ「既存プラグインの削除」を足す

- **対象ファイル:** `docs/plugin-development-guide.md`
- **変更内容:** 根の手順 1・2 を移し、コミットは作業ブランチから `develop` 宛の Pull Request へ書き換える。「Runtime plugin 検証」に `tests/runtime-smoke/README.md` へのリンクを足す
- **満たす受け入れ条件:** AC5
- **進め方:** `grep -n '^## 既存プラグインの削除'` が 0 件であることを確かめてから足す

### Task 3: 根の README を組み直す

- **対象ファイル:** `README.md`
- **変更内容:** 設計の「根の README から外す節と正本」の 12 行のとおりに外し、「手入れ後の根の README」の構成にする
- **満たす受け入れ条件:** AC3、AC4、AC6、AC9
- **進め方:** AC3 の `grep` が 4 件出ることを確かめてから外す。AC6 を 6 件のまま保つ

### Task 4: NDF の入口とリンク元を張り替える

- **対象ファイル:** `plugins/ndf/README.md` / `CONTRIBUTING.md` L115 / `docs/project-overview.md` L42
- **変更内容:** 開発版へのリンクを版と配布の正本の「開発版を試す」へ。「検証」「開発者向け」を 1 節にまとめる。新しいプラグインの手順を開発ガイドへ
- **満たす受け入れ条件:** AC8、AC10
- **進め方:** AC8 の `git grep` が落ちることを確かめてから張り替える

### Task 5: 検査を回す

- **満たす受け入れ条件:** AC9〜AC14。設計の未確認 2 件（検査 E とレイアウト図、配布の走査）をここで確かめる

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 根の README の概要と一覧表を検査 A・B・C・G・H が位置の語で読む | 2 節は変えない。Task 3 の直後に `check-doc-staleness.py` を回す |
| `plugins/ndf/README.md` のレイアウト図の行を変えると検査 E が読み違える | 検査 E は「唯一の実体（N 個）」の形だけを読む（`LAYOUT_SKILLS`）。Task 1 の直後に回す |
| 同じページのアンカーが切れる | Task 3・4 の後に `check-markdown-links.py` を回す |
| 触る対象の構造 | 文書の節の移動で、構造の危うさは無い。タスクごとに検査を通す |

## 切り戻し手順

文書と改名だけの変更で、データを持たない。Pull Request を revert すれば元へ戻る。

## 完了の定義

- [ ] AC1〜AC14 をすべて満たし、条件ごとに検証の結果を Pull Request に残す
- [ ] 設計の「未確認のまま残ること」のうち実装で決める 2 件の結果を Pull Request に書く
