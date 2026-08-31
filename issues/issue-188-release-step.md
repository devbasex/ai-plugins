# issue #188: 配布の工程と、版を上げる担い手・時期を決める

## 関連リンク

- [issue #188](https://github.com/devbasex/ai-plugins/issues/188)
- [issues/parallel-batch-02/00-overview.md](parallel-batch-02/00-overview.md) — このバッチの全体指示
- 由来: [PR #187](https://github.com/devbasex/ai-plugins/pull/187)（版上げを進行側が拾った実例）
- 工程の定義: `plugins/ndf/skills/development-workflow/SKILL.md`

## モード

`architecture`。工程表へ工程を 1 つ足し、公開される Skill が 1 個増える。変更は
`development-workflow` / `merged` / `release-verification` / 配布定義 / 説明文書にまたがる。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| 配布 | プラグインの版を上げ、利用者が取得できる状態にすること |
| まとまり | 1 度にマージする Pull Request の集合。単独の変更では 1 件 |
| 進行側 | 複数の Pull Request の進行を束ねている側。単独の変更では実装した本人と同じ |
| 工程表 | `development-workflow/SKILL.md` の「モードごとに起動する Skill」の表 |

## 目的と非目的

達成したい状態:

- 版を上げる担い手と時期が、その場の判断ではなく工程として決まっている
- `release-verification` の開始条件（版の配布が終わっていること）を満たす手順が、
  直前の工程として存在する
- 配布物が変わったのに版が据え置きのまま次の変更へ進む経路が塞がれている

やらないこと:

- **版の決め方（semver の判断）を新しく作ること。** `docs/plugin-development-guide.md` に
  すでにある。この変更は担い手と時期を決めるものであり、手順の中身は既存を指す
- **配布の自動化。** 誰がいつ行うかを決めることと、それを機械にやらせることは別である
- **`merged` の後片付けの手順そのものの変更。** 削除の同意取得は現状のままとする

## 前提

- 前提 1: `release-verification` の開始条件は「版の配布が終わっていること」であり、
  この工程が無いと開始できない
- 前提 2: 版上げの手順は `docs/plugin-development-guide.md` の「バージョン管理」にある
- 前提 3: `scripts/check-doc-staleness.py` が、説明文書の Skill 数と更新案内の見出しの
  版数を `manifests/` と `plugin.json` に突き合わせる（#178）
- 前提 4: バッチ 01 では 3 件をマージした後、進行側が別の Pull Request（#187）で
  1 度だけ版を上げた。この形が実績として存在する

## 決めること への回答

issue #188 が挙げた 2 つに答える。

| 決めること | 採る案 | 理由 |
| --- | --- | --- |
| 担い手 | **まとまりの最後のマージを行った側**（単独の変更では実装した本人、複数では進行側） | マージの完了を知っているのはその側だけである。実装担当に持たせると、自分の Pull Request が最後かどうかを判断できない |
| 時期 | **まとまり単位でマージが終わった後** | Pull Request ごとに上げると版数が飛ぶ。単独の変更ではまとまりが 1 件になり、マージ直後と一致する |

### 代替案と採否

| 案 | 内容 | 採否 | 理由 |
| --- | --- | --- | --- |
| A | 各 Pull Request のマージ直後に、マージした担当が上げる | 不採用 | 3 件をまとめてマージすると版が 3 つ進む。利用者から見て、意味のない版が 2 つ増える |
| B | まとまり単位で、最後のマージを行った側が上げる | **採用** | 単独の変更では A と同じ挙動になる。まとまりのときだけ 1 回に集約される |
| C | 配布を工程にせず、`merged` の手順の一部にする | 不採用 | `merged` は後片付けで、削除の同意取得が主題である。配布は取り消しの効かない公開であり、同意の性質が違う。工程表に行が無いままだと要否をモードで決められない |
| D | 配布を自動化し、マージを検知して版を上げる | 不採用 | 版の大小（MAJOR / MINOR / PATCH）は変更の意味から決まる。機械が決めると、破壊的変更が PATCH で出る |

## 受け入れ条件

- [ ] 1. `plugins/ndf/skills/release/SKILL.md` があり、担い手・時期・手順・完了の判定・
      出力物を定めている
- [ ] 2. `release` がモードの要否を自分では決めず、`development-workflow` の判定結果を
      受け取る側に徹している（`SKILL.md` にモードの条件表が無い）
- [ ] 3. `release` が版の決め方を新しく作らず、`docs/plugin-development-guide.md` を指している
- [ ] 4. `development-workflow` の工程表に「配布」の行があり、後片付けとリリース後テストの
      間に位置している
- [ ] 5. 工程表の「配布」の要否が、4 モードすべてで定まっている
- [ ] 6. `development-workflow` の標準フローの図に配布の工程が入り、後片付けから
      リリース後テストへの経路がそこを通る
- [ ] 7. `references/workflow-modes.md` のモード別の詳細に配布の要否が入っている
- [ ] 8. `merged/SKILL.md` に、まとまりの最後のマージだったときの次の工程への案内がある
- [ ] 9. `release-verification/SKILL.md` の開始条件が、`release` を直前の工程として指している
- [ ] 10. `release` が `plugins/ndf/manifests/` の 3 ファイルすべてに載っている
- [ ] 11. `python3 scripts/check-skill-frontmatter.py` が終了コード 0 で終わる
- [ ] 12. `python3 scripts/check-markdown-links.py` が終了コード 0 で終わる
- [ ] 13. `python3 scripts/check-doc-staleness.py` が終了コード 0 で終わる
- [ ] 14. `bash scripts/validate-runtime-plugins.sh` が終了コード 0 で終わる
- [ ] 15. `uv run --with pytest pytest scripts/tests plugins/ndf -q` が通る
- [ ] 16. `README.md` / `plugins/ndf/README.md` / `CLAUDE.md` の Skill 数が
      追加後の数と一致している

## 版を上げるかどうか

**この Pull Request では版を上げない。** 採用した案 B が「まとまり単位でマージが終わった後」と
定めるためである。自分の変更で版を上げると、決めたばかりの規則を最初の適用で破ることになる。
バッチ 02 のマージが終わった後、進行側が新しい `release` の工程で 1 度だけ上げる。

説明文書の Skill 数は、ランタイム別の 3 つと元 Skill の総数をそれぞれ 1 ずつ増やす。
書き換える箇所は次のとおりで、1 か所でも残ると `check-doc-staleness.py` か
`validate-runtime-plugins.sh` が落ちる。

| 記載 | 変更 |
| --- | --- |
| `README.md` の公開Skills（Claude Code / Kiro / Codex） | 31 → 32 / 30 → 31 / 29 → 30 |
| `README.md` の元Skills と、カテゴリ内訳の「開発方法論」 | 35 → 36 / 8 → 9 |
| `README.md` のプラグイン一覧の説明（公開Skills と同じ 3 つを再掲している） | 31 → 32 / 30 → 31 / 29 → 30 |
| `plugins/ndf/README.md` の配布先の表（Claude Code / Codex / Kiro CLI） | 31 → 32 / 29 → 30 / 30 → 31 |
| `plugins/ndf/README.md` のレイアウト図 | 31 → 32 |
| `plugins/ndf/README.md` の更新案内の本文 | 数の再掲をやめ、冒頭の表を指す |
| `CLAUDE.md` の Skill 構成（総数と 3 ランタイム） | 35 → 36 / 31 → 32 / 29 → 30 / 30 → 31 |
| `.claude-plugin/marketplace.json` と `plugins/ndf/.claude-plugin/plugin.json` の description | 31 → 32 |
| `plugins/ndf/.codex-plugin/plugin.json` の description | 29 → 30 |

数を機械で突き合わせるのは `README.md` と `plugins/ndf/README.md`（`check-doc-staleness.py`）、
`marketplace.json` と 2 つの `plugin.json`（`validate-runtime-plugins.sh`）である。`CLAUDE.md`
はどちらの検査の対象でもないため、手で確かめる。`AGENTS.md` には Skill 数の記載が無く、
新しく書き足すと検査の外で古くなるため、対象へ入れない。

`check-doc-staleness.py` は説明文書の数を `manifests/` の行数と突き合わせるほか、更新案内の
見出しの版数を `plugin.json` の版とも突き合わせる。この Pull Request では版を上げないため、
数だけを直しても版数の組は崩れない。

## 修正対象

| ファイル | 変更 |
| --- | --- |
| `plugins/ndf/skills/release/SKILL.md` | 新設 |
| `plugins/ndf/skills/development-workflow/SKILL.md` | 工程表に 1 行、標準フローの図、`architecture` の現状の表 |
| `plugins/ndf/skills/development-workflow/references/workflow-modes.md` | モード別の詳細に配布 |
| `plugins/ndf/skills/merged/SKILL.md` | 次の工程への案内 |
| `plugins/ndf/skills/release-verification/SKILL.md` | 開始条件から `release` を指す |
| `plugins/ndf/manifests/*-skills.txt` | 3 ファイルへ `release` を追加 |
| `README.md` / `plugins/ndf/README.md` / `CLAUDE.md` | Skill 数（上の表） |
| `.claude-plugin/marketplace.json` | description の Skill 数 |
| `plugins/ndf/.claude-plugin/plugin.json` / `.codex-plugin/plugin.json` | skills の一覧へ `release` を追加、description の Skill 数。**版は上げない**（上の節） |

## タスク分解

### Task 1: `release` Skill を新設する

- **対象:** `plugins/ndf/skills/release/SKILL.md`
- **内容:** 担い手・時期・手順・完了の判定・出力物。版の決め方は
  `docs/plugin-development-guide.md` を指す。利用者が取得する手順（再インストール）まで
  出力物へ含める
- **進め方:** frontmatter の規約は `plugins/ndf/skills/README.md` に従い、
  `check-skill-frontmatter.py` で確かめる

### Task 2: 工程表と標準フローへ配布を入れる

- **対象:** `development-workflow/SKILL.md`、`references/workflow-modes.md`
- **内容:** 後片付けとリリース後テストの間に「配布」の行を置く。要否は
  `light` が条件付き（配布物が変わったときのみ）、他の 3 つが必須
- **進め方:** モードの要否は工程表だけが持つ。`release/SKILL.md` に条件表を置かない

### Task 3: 前後の工程からつなぐ

- **対象:** `merged/SKILL.md`、`release-verification/SKILL.md`
- **内容:** `merged` の完了報告に、まとまりの最後のマージだったときの案内を 1 項目足す。
  `release-verification` の開始条件が `release` を直前の工程として指す

### Task 4: 配布定義と説明文書を合わせる

- **対象:** `manifests/*-skills.txt` 3 ファイル、`README.md`、`plugins/ndf/README.md`、
  `CLAUDE.md`、`.claude-plugin/marketplace.json`、`plugins/ndf/.claude-plugin/plugin.json`、
  `plugins/ndf/.codex-plugin/plugin.json`
- **内容:** `release` を `manifests/` の 3 ファイルと 2 つの `plugin.json` の skills へ追加し、
  Skill 数を「版を上げるかどうか」の表のとおりに直す
- **進め方:** `bash scripts/build-runtime-plugins.sh` で生成物を同期し、
  `check-doc-staleness.py` と `validate-runtime-plugins.sh` で確かめる

## 影響範囲

利用者から見える変化は、`/ndf:release` が増えることと、工程表に行が 1 つ増えることである。
既存の Skill の手順は変えない。`merged` と `release-verification` への追記は案内であり、
既存の手順の削除・改名を伴わない。

## リスクと対処

| リスク | 対処 |
| --- | --- |
| 「まとまり」の範囲が曖昧で、いつ配布するか決まらない | まとまりは Pull Request の集合として定義し、単独の変更ではまとまり = 1 件と明記する |
| 工程を増やしたことで、単独の小さな変更が重くなる | `light` は配布物が変わったときだけ通る条件付きにする |
| 配布したが利用者が取得しない状態が残る | 出力物へ取得の手順（再インストールのコマンド）を含め、`release-verification` が導入済みの版を照合する |
| `release` と `release-verification` の名前が紛らわしい | 工程表で隣接させ、両方の `SKILL.md` の冒頭で相手との境界を書く |

## 切り戻し手順

文書と配布定義の変更のみで、データの移行を伴わない。`git revert` で戻る。
`release` を消す場合は `manifests/` の 3 ファイルと説明文書の数も同じコミットで戻す。

## 完了の定義

- [ ] 受け入れ条件 1〜16 をすべて満たし、条件ごとに検証手段と結果が対応している
- [ ] `architecture` モードの検証の段階 1〜4 を通す（詳細は `quality-gates`）
- [ ] バッチ 02 のマージ後、新しい `release` の工程で実際に版を上げる
- [ ] リリース後テストと振り返りをバッチ 02 の単位で行う
