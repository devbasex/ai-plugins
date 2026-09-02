# 並行開発 バッチ 04 — 全体指示

12 件の課題を同時に進めるための指示書である。各担当は自分の担当ファイルだけを読めば着手できる。
この文書は、担当どうしが同じ行を書き換えないための境界と、着手の順序を定める。

## 着手する前に知っておくこと

| 項目 | 現在の状態 |
| --- | --- |
| 現行版 | **9.7.0**（正式版。`main` と `develop` は同じ位置） |
| 開発の起点 | **`develop`**。`.ndf/worktree.json` の `base_branch` が宣言している |
| Pull Request の宛先 | **`develop`**。`--base develop` を必ず付ける |
| 配布の時期 | **このバッチのマージがすべて終わった後**。個別には版を上げない |
| `/goal` で工程を通すとき | 設計レビューのマージ前で 1 度止まり、承認を待つ（`development-workflow` の該当節） |

**このバッチの課題は 12 件のうち 11 件が、直前の開発（バッチ 03 と v9.7.0 の配布）で見つかった
ものである。** 残る 1 件（#231）も同じ期間に見つかっている。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 担当 | 1 件以上の課題を最後まで進める実行主体。1 担当 = 1 作業ツリー = 1 Pull Request |
| 検査 | `scripts/` 配下のスクリプトが行う機械的な突き合わせ |
| 起点 | 開発の本流のブランチ。このリポジトリでは `develop` |
| 収束 | `cross-review` で codex と gemini の両方が承認した状態 |

## 担当と課題

| 担当 | 課題 | ブランチ | 指示書 |
| --- | --- | --- | --- |
| A | 開発版チャネルの取得手順の記載を実機に合わせる（#263） | `docs/issue-263-install-steps` | [01-issue-263.md](01-issue-263.md) |
| B | 版数の書式を 1 か所へ集め、配線テストの掴み方を直す（#258 / #256） | `fix/issue-258-version-pattern` | [02-issue-258-256.md](02-issue-258-256.md) |
| C | `cross-review` の判定と巻き直しと振動検知（#261 / #244 / #246 / #245） | `fix/issue-261-cross-review` | [03-issue-261-244-246-245.md](03-issue-261-244-246-245.md) |
| D | テストの収集と実行環境への依存（#232 / #233 / #235） | `fix/issue-232-test-collection` | [04-issue-232-233-235.md](04-issue-232-233-235.md) |
| E | 工程の記録と、閉じ忘れる issue（#231 / #259） | `fix/issue-231-workflow-records` | [05-issue-231-259.md](05-issue-231-259.md) |

各担当の指示書は受け入れ条件・設計・決定の記録・テスト設計を持つ。実装へ入る前にこの 4 つを
書き終える（バッチ 03 と同じ進め方）。

## モードの判定

判定するのは `development-workflow` である。着手時に改めて通すが、見込みは次のとおり。

| 担当 | 見込み | 根拠 |
| --- | --- | --- |
| A | `light` | 説明文書の記載を実機に合わせる。本番の振る舞いも構造も変えない |
| B | `standard` | 検査の振る舞いを変える。テストがある領域 |
| C | `standard` | 収束判定と同期の振る舞いを変える。テストがある領域 |
| D | `standard` | テストの収集の仕方と継続的統合の設定を変える |
| E | `standard` | 盤面の値と後片付けの手順を変える |

`standard` は設計 Pull Request が必須である。**4 件をまとめて 1 本の設計 Pull Request にしてよい**
（バッチ 03 で採った形。設計文書が別々のファイルにあり、同じ行を書き換えないため）。

## 担当どうしの境界

| 担当 | 書き換えてよいパス | 触らないパス |
| --- | --- | --- |
| A | `AGENTS.md` / `plugins/ndf/README.md` | `scripts/` / `plugins/ndf/skills/` / `.github/` |
| B | `scripts/check-doc-staleness.py` / `scripts/validate-runtime-plugins.sh` / `scripts/lib/` / `scripts/tests/` | `AGENTS.md` / `plugins/` / `.github/` |
| C | `plugins/ndf/skills/cross-review/` | `scripts/` / `AGENTS.md` / `.github/` / 他の Skill |
| D | `.github/workflows/` / リポジトリの根の `conftest.py` / `.gitignore` / `plugins/playwright-kit/skills/playwright-kit-ops/` / `plugins/ndf/skills/cross-refactoring/tests/` / `plugins/ndf/skills/worktree/tests/conftest.py` / `plugins/ndf/skills/development-workflow/tests/conftest.py` | `scripts/` / `AGENTS.md` / `cross-review/` |
| E | `plugins/ndf/scripts/lib/projects-common.sh` / `plugins/ndf/skills/merged/` / `plugins/ndf/skills/development-workflow/references/` / `development-workflow/tests/` の新しいファイル | `scripts/` / `AGENTS.md` / `cross-review/` / `.github/` / `development-workflow/tests/conftest.py` |

### 担当 A と B は `AGENTS.md` で接する

**`AGENTS.md` は担当 A が持つ。** 担当 B の #258（版数の書式の一元化）で `AGENTS.md` の
「検査が突き合わせる 13 箇所」の記載が古くなる場合は、**担当 A のマージ後に取り込んでから直す**。

**マージの順序は A → B → D とする。** 担当 D は `scripts/tests/` にある読み飛ばしの指定を
外すため、担当 B のマージ後に起点を取り込んでから手を付ける。担当 C と E は他の担当と
接しないため、順序を持たない。

### 担当 C は `SKILL.md` の行数に注意する

`cross-review` の `SKILL.md` は 500 行の上限に張り付いている（#245）。**#261 / #244 / #246 の
どれを入れても行数が増えるため、#245 の構成の見直しを先に済ませる。** 担当 C の中での順序である。

### 担当 D と E は他の担当のテストを動かす

どちらも `uv run --with pytest pytest` を回す。**他の担当が同時にテストを足しているため、
自分のブランチで通ることだけを見る。** `develop` の最新を取り込むのはマージの直前でよい。

## このバッチに入れなかった課題と、その理由

| 課題 | 理由 |
| --- | --- |
| #266 設計 Pull Request のマージを hook で縛る | #265 でマージした「`AskUserQuestion` で止まる」手順の運用を先に見る。飛ばされる事例が出てから着手する |
| #221 工程を飛ばしても気づく手立てが無い | #266 と同じ性質。#266 の判断が先 |
| #237〜#243 OSS 運用の整備 | 別のまとまり。#236 を親とする一連の課題で、このバッチとは独立している |
| #228 / #229 | 別のセッションが起票したもの。担当の割り当てが未定 |
| #214 / #215 / #216 agy CLI への置き換え | 3 件が連動し、単独でバッチ 1 本にあたる規模 |
| #113 / #116 / #144 / #156 / #158 / #159 | いずれも規模が大きい。個別に判断する |
| #181 / #193 / #197 / #201 / #224 | このバッチの 12 件と領域が重ならないが、規模を抑えるため次のまとまりへ回す |

## 完了条件（バッチ全体）

- [ ] 設計 Pull Request（担当 B / C / D / E 分）がマージされている
- [ ] 5 件の実装 Pull Request がいずれもマージされている
- [ ] `bash scripts/validate-runtime-plugins.sh` が終了コード 0 で終わる
- [ ] `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] `python3 scripts/check-doc-staleness.py --root .` が終了コード 0 で終わる
- [ ] `claude plugin validate` が通る
- [ ] 範囲外と判断した課題が `out-of-scope` で issue になっている
- [ ] 版を上げて配布し（`release`）、振り返りを残している

## 共通の進め方

### 作業ツリーを用意する

```bash
cd /work/ai-plugins
git fetch origin
git worktree add -b "<ブランチ名>" ".worktrees/<ブランチ名>" origin/develop
cd ".worktrees/<ブランチ名>"
```

**起点は `develop` である。** `main` から切らない。

### テストを動かす

システムの `python3` には `pytest` が入っていない。`uv` の仮想環境で動かす。

```bash
uv run --with pytest pytest <テストのパス> -q
```

### 生成物を同期する

`plugins/ndf/skills/` を触った担当は、Pull Request の中で実行する。

```bash
bash scripts/build-runtime-plugins.sh
```

### Pull Request の宛先

**`--base develop` を必ず付ける。** 既定ブランチが `main` のため、指定しないと `main` 宛になり
`pr-base-guard` で落ちる。

### issue を閉じる

**`develop` 宛の Pull Request では `Closes` が働かない**（#259 がこの課題そのものである）。
マージした後に `gh issue close` で閉じる。

### 範囲外の課題を見つけたとき

`out-of-scope` を使い、**見つけたその場で** issue にする。判断は 3 択（起票する / 範囲内へ入れる /
起票しない）に限り、3 つ目も理由を 1 行残す。

## 参照

- [issues/old/parallel-batch-03/00-overview.md](../old/parallel-batch-03/00-overview.md) — 前のバッチの指示書
- [docs/development-history/09-2026-09-02.md](../../docs/development-history/09-2026-09-02.md) — バッチ 03 の振り返り
- `plugins/ndf/skills/development-workflow/SKILL.md` — 工程の振り分けの基準
