# #2231: 配布を Claude Code と codex だけにし（すべてのプラグイン）、agy・kiro へは配らず cross 系の相手役だけにする

正は課題の本文（#2231）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 方針の変更（2026-10-31 夜、承認ゲート 1 で利用者が指示）

> claude とcodexのみ、すべてのpluginを配布することとします。agy、kiroは配布せず、cross系で呼ばれるだけ。

（書き手: 利用者、日付: 2026-10-31。会話の原文）

- **Claude Code と codex**: マーケットプレイスのすべてのプラグイン（ndf・playwright-kit・mcp-*）を、すべての Skill で配る。codex の配布は縮めない
- **agy と kiro**: 何も配らない（`plugins/ndf/manifests/agy-skills.txt`・`kiro-skills.txt`、`dev.agy/`・各プラグインの `dev.kiro/` の導入物、`release.runtimes` の kiro を外す）。cross-review / cross-refactoring から相手役の CLI として呼ばれる経路だけを保つ
- 下の「依頼（原文）」と受け入れ条件のうち、codex に配布を絞る部分はこの変更で置き換わる。プロジェクト MVV の Mission（4 ランタイム）の改訂は別の課題で扱う

## 受け入れ条件（方針の変更の後）

- [ ] 1. `agy-skills.txt`・`kiro-skills.txt` と agy・kiro 向けの導入物（`dev.agy/`、ndf・playwright-kit・mcp-* の `dev.kiro/`）が配布物から無くなり、`release` の対象ランタイムが claude と codex だけになる（マージ前にテストとビルドで確かめる）
- [ ] 2. codex へ配る Skill とプラグインは変更前と同じ（すべて）である（マージ前に manifests とビルドで確かめる）
- [ ] 3. agy・kiro を相手役にした cross-review と cross-refactoring の起動経路（`launch-cli.sh`・`launch-reviewer.sh`）が変わらず通る（マージ前にテスト、リリース後に実物で確かめる）
- [ ] 4. AGENTS.md・CLAUDE.md・KIRO.md・README・`docs/ndf-plugin-reference.md`・`docs/versioning-and-distribution.md`・`docs/plugin-development-guide.md` が 2 ランタイム配布＋相手役 2 つの構成を書く（マージ前にリンク検査と指示書のチェックで確かめる）
- [ ] 5. ランタイムのスモークテストと CI から agy・kiro の導入の検査が外れ、codex の検査は残る（マージ前に CI で確かめる）
- [ ] 6. 配布をやめると直す必要が無くなる agy・kiro だけの open の課題の一覧を設計に載せる（閉じるのは棚卸し）

---

## 依頼（原文）

> **codex・agy・kiro へは、cross-review / cross-refactoring に要る Skill 以外を配布しない。**

（書き手: 利用者（起票者が「決めたこと（2026-10-31）」として本文へ引用した文。会話の原文は確かめていない）、日付: 2026-10-31）

> ## 何が困るか
>
> NDF は Claude Code・Codex・Kiro CLI・agy の 4 つのランタイムへ、ほぼ同じ Skill を配っている（`plugins/ndf/manifests/*-skills.txt`）。同じ機能を 4 通りに保つ費用がかかり、ランタイムごとの差分を吸収する層・スモークテスト・不具合（agy の本文の切り詰め #2163 など）が増え続ける。
>
> Codex・agy・kiro が NDF の工程で担っているのは、cross-review と cross-refactoring の相手役である。工程の本体（supervise・relay・承認ゲート・hook）は Claude Code で動かす。
>
> ## 直すもの
>
> - `plugins/ndf/manifests/codex-skills.txt`・`agy-skills.txt`・`kiro-skills.txt` を、cross-review / cross-refactoring に要る Skill だけにする（要る Skill の一覧は設計で決める）
> - `dev.agy/`・`dev.kiro/` の agents・hook・steering のうち、その 2 つに要らないものを外す
> - AGENTS.md の「4 ランタイム共通」の前提、各ランタイムの README、`docs/ndf-plugin-reference.md`、`docs/versioning-and-distribution.md` を新しい配布に合わせる
> - ランタイムのスモークテストを、配る Skill に合わせて絞る
>
> ## 受け入れ条件
>
> - 3 つのマニフェストに、cross-review / cross-refactoring に要らない Skill が無い
> - 絞った後も、codex・agy・kiro を相手役にした cross-review と cross-refactoring が通る
> - 配布を絞ると直す必要が無くなる open の課題（agy・kiro・codex だけで起きる不具合）を棚卸しで閉じる

（書き手: 起票者、日付: 2026-10-31。上の引用以外は利用者の指示の原文として扱わない）

## 起票者の推定

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | codex・agy・kiro が NDF の工程で担うのは cross-review / cross-refactoring の**相手役**だけで、工程の本体（supervise・relay・承認ゲート・hook）は Claude Code で動かす | 本文「何が困るか」の 2 段落目 | 前提 1。受け入れ条件 1・2 の「要る」の判定基準にする |
| S2 | 要る Skill の一覧は設計で決める | 本文「直すもの」の 1 行目 | 未決 1（設計で決め、承認ゲート 1 で利用者が確かめる） |
| S3 | `dev.agy/`・`dev.kiro/` の agents・hook・steering も、2 つに要らないものを外す（利用者の文は「Skill」だけを言う） | 本文「直すもの」の 2 行目 | 受け入れ条件 3・4 |
| S4 | AGENTS.md・各ランタイムの README・`docs/ndf-plugin-reference.md`・`docs/versioning-and-distribution.md` を新しい配布に合わせる | 本文「直すもの」の 3 行目、方針メモ「4 ランタイム共通の前提（AGENTS.md）を変える」 | 受け入れ条件 6 |
| S5 | ランタイムのスモークテストを配る Skill に合わせて絞る | 本文「直すもの」の 4 行目 | 受け入れ条件 7 |
| S6 | 配布を絞ると直す必要が無くなる open の課題を棚卸しで閉じる | 本文「受け入れ条件」の 3 行目 | 受け入れ条件 9。閉じるのは棚卸しの工程で行い、実装の PR では閉じない |
| S7 | codex・agy・kiro を**ホスト**にして cross-review / cross-refactoring を起動する使い方（`--host codex` など）は残さない | 起票者（この要求を書いた AI）が S1 から推した。本文には書かれていない | 前提 2・未決 2。承認ゲート 1 で利用者が確かめる |

## 目的

- codex・agy・kiro へ配る NDF を、cross-review / cross-refactoring の相手役に要るものだけにし、同じ機能を 4 通りに保つ費用（ランタイムごとの差分を吸収する層・スモークテスト・ランタイムだけで起きる不具合）を減らす
- 絞った後も、Claude Code をホストにした cross-review / cross-refactoring で codex・agy・kiro が相手役として今と同じ結果を返す

## 前提

- 前提 1: Claude Code 以外のランタイムが NDF の工程で担う役割は、cross-review のレビュー担当と cross-refactoring の提案・実装の担当（相手役）だけである（S1）
- 前提 2: cross-review / cross-refactoring のホスト（収束ループを起動する CLI）は Claude Code に限る。codex・agy・kiro をホストにする使い方は、この変更の後は保証しない（S7。誤っても、配る Skill の一覧へ行を足せば戻せる）
- 前提 3: 相手役が読む手順は、ホストの側が担当のworktreeへ置くか、起動の依頼文に直接書く。今の実装では、cross-review のレビュー担当の依頼文は Skill を読まずに完結し（`plugins/ndf/skills/cross-review/scripts/launch-reviewer.sh` の `render_review_prompt`）、cross-refactoring は `refactoring`・`tdd-cycle`・`quality-gates` をホストの導入先から担当のworktreeへ写す（`plugins/ndf/skills/cross-refactoring/scripts/prepare-worktrees.sh`・`refactor_lib/vocabulary.py` の `REQUIRED_SKILLS`）。相手役のランタイムに配った Skill に頼る経路が他に無いことは、設計で確かめる
- 前提 4: Claude Code へ配る Skill（`plugins/ndf/manifests/claude-skills.txt`）は変えない
- 前提 5: 利用者の手元に既に入っている codex・agy・kiro 向けの Skill を、こちらから消す操作はしない。配布物から消えた Skill が手元に残るかは各ランタイムの導入の振る舞いに従い、その扱いを利用者向けの文書に書く

## 対象範囲

含む:
- `plugins/ndf/manifests/codex-skills.txt`・`agy-skills.txt`・`kiro-skills.txt` の中身
- `plugins/ndf/dev.agy/`・`plugins/ndf/dev.kiro/` のエージェント定義・hook・steering・プロンプトのうち、相手役に要らないもの
- 配布物を組み立てる・確かめるスクリプトとテスト（マニフェストを読むもの、ランタイムのスモークテスト）を、新しい配布に合わせる
- `AGENTS.md` の「4 ランタイム共通」の記述、各ランタイムの README、`docs/ndf-plugin-reference.md`、`docs/versioning-and-distribution.md`
- 配布を絞ると直す必要が無くなる open の課題の一覧（棚卸しへ渡す材料）

含まない:
- Claude Code へ配る Skill・エージェント・hook の変更
- cross-review / cross-refactoring 自体の振る舞いの変更（参加者プール・輪番・収束の規則）
- playwright-kit・mcp-* プラグインの配布
- 一覧にした open の課題を実装の PR で閉じること（閉じるのは棚卸し）
- 利用者の手元の導入済みの Skill を消す操作

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | 相手役に要る Skill の一覧を決めた | 設計 | 一覧に漏れがあると E5・E6 で相手役が手順を読めない。受け入れ条件 2 のテストが落とす | — |
| E2 | 3 つのマニフェストを一覧に合わせて絞った | 実装 | 要らない Skill が残る・要る Skill が欠ける。受け入れ条件 1・2 のテストが落とす | E1 |
| E3 | `dev.agy/`・`dev.kiro/` から要らないエージェント・hook・steering を外した | 実装 | 外したものを導入の手順・hook の設定が参照したまま残り、導入が失敗する。受け入れ条件 3・4・5 が落とす | E1 |
| E4 | 利用者が新しい版を codex・agy・kiro へ導入した | リリースと利用者の更新 | 導入の検証（`claude plugin validate .`・ランタイムのスモークテスト）が落ちる | E2・E3 |
| E5 | Claude Code をホストにした cross-review で、codex・agy・kiro がレビュー担当として結果ファイルを返した | `/ndf:cross-review` | 結果ファイルが無い（NO_RESULT）。受け入れ条件 8 の確認で見る | E4 |
| E6 | Claude Code をホストにした cross-refactoring で、codex・agy・kiro が提案・実装の担当として結果を返した | `/ndf:cross-refactoring` | 手順書（`refactoring` など）がworktreeに無く、提案が語彙外で全件降格する。受け入れ条件 8 の確認で見る | E4 |
| E7 | 直す必要が無くなった open の課題を棚卸しで閉じた | `/ndf:backlog-refinement` | 一覧の漏れ。受け入れ条件 9 | E4 |

## 用語

| 用語 | 意味 |
| --- | --- |
| 相手役 | cross-review のレビュー担当、cross-refactoring の提案・実装の担当として、ホストから CLI プロセスで起動されるランタイム |
| ホスト | cross-review / cross-refactoring の収束ループを起動し、修正（`/ndf:fix`）と記録を行う CLI |
| マニフェスト | `plugins/ndf/manifests/<ランタイム>-skills.txt`。ランタイムごとに配る Skill の一覧 |

## 受け入れ条件

- [ ] 1. `plugins/ndf/manifests/codex-skills.txt`・`agy-skills.txt`・`kiro-skills.txt` のどの行も、設計で決めた「相手役に要る Skill の一覧」に含まれる。一覧に無い Skill がマニフェストにあると落ちる検査が CI で動く
- [ ] 2. 相手役が起動時に読む Skill（cross-refactoring の `REQUIRED_SKILLS` など、設計で洗い出したもの）が、ホストの導入先からworktreeへ写せる。欠けると落ちる検査が CI で動く
- [ ] 3. `plugins/ndf/dev.agy/` の配布物（エージェント定義・`hooks.json`・`skills/`・`scripts/`）に、相手役に要らないものが無い。何を残したかと理由は設計文書に表で残る
- [ ] 4. `plugins/ndf/dev.kiro/install.sh` が生成するもの（`.kiro/skills/`・`.kiro/steering/`・`.kiro/agents/ndf.json`）と `dev.kiro/` のプロンプトに、相手役に要らないものが無い
- [ ] 5. `claude plugin validate .` が終了コード 0 で終わり、`python3 scripts/check-skill-frontmatter.py` と `python3 plugins/ndf/scripts/instructions-check.py --root .` が通る
- [ ] 6. `AGENTS.md`・各ランタイムの README・`docs/ndf-plugin-reference.md`・`docs/versioning-and-distribution.md` に「4 ランタイムへほぼ同じ Skill を配る」前提の記述が残らず、codex・agy・kiro が相手役だけを担うことと、配る Skill の一覧の正本（マニフェスト）を指す記述がある
- [ ] 7. ランタイムのスモークテスト（`scripts/runtime-smoke-test.sh` と `.github/workflows/runtime-plugin-smoke*.yml`）が、codex・agy・kiro について配る Skill だけを確かめ、配らない Skill の存在を求めない。CI のスモークのジョブが通る
- [ ] 8. 新しい配布物を導入した環境で、Claude Code をホストに codex・agy・kiro を相手役にした cross-review 1 回と cross-refactoring 1 回が、各相手役の結果ファイルを返して終わる（NO_RESULT が出ない）
- [ ] 9. 配布を絞ると直す必要が無くなる open の課題の一覧（番号と理由）が、実装の PR か本課題に残り、棚卸しで閉じるか残すかが決まる
- [ ] 10. 退行しない: Claude Code 向けのマニフェスト（`claude-skills.txt`）と Claude Code の配布物は変わらず、既存の pytest（`uv run --frozen --project . --all-extras pytest . -q -n 4`）が通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 運用・保守性 | codex・agy・kiro へ配る Skill の数が、変更前（codex 45・agy 45・kiro 46）から減り、減った数を実装の PR に実測で書く（Value 3）。ランタイムのスモークテストの所要（秒）を変更の前後で実装の PR に書く |
| 移行性 | 既に codex・agy・kiro へ NDF を入れた利用者が新しい版へ更新したとき、配布から外れた Skill が手元でどうなるか（消える / 残る）をランタイムごとに確かめ、残る場合の消し方を README に書く。こちらから利用者の手元を消さない（C6） |
| システム環境 | 対象のランタイムは今と同じ（Claude Code・Codex・Kiro CLI・agy）。相手役の起動に使う CLI の版の下限は変えない |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わる。codex・agy・kiro で `$ndf:<Skill>` などとして呼べた Skill の大半が配布から外れる。互換の経路は持たない（`互換なし`）。Claude Code の公開インタフェースは変わらない |
| データ | 変わらない |
| 既存の振る舞い | codex・agy・kiro をホストにした NDF の工程（`--host codex` など）は保証の外になる（前提 2）。Claude Code をホストにした cross-review / cross-refactoring の振る舞いは変わらない |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| 起動 | `claude plugin validate .`（終了コード 0）。`bash plugins/ndf/dev.kiro/install.sh` を一時ディレクトリで実行し、生成物を見る |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析・型検査 | `python3 scripts/check-skill-frontmatter.py`・`python3 plugins/ndf/scripts/instructions-check.py --root .`・CI の `lint` / `runtime-plugin-validate` / `runtime-plugin-smoke` |
| 手動確認 | リリース後テストで、新しい版を入れた環境から `/ndf:cross-review <PR>` と `/ndf:cross-refactoring <PR>` を 1 回ずつ回し、codex・agy（`--include agy`）・kiro の結果ファイルが揃うことを見る（受け入れ条件 8） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 配る Skill の正本は `plugins/ndf/manifests/`（`CLAUDE.md` の「NDF の Skill 構成」）。Skill の実体は `plugins/ndf/skills/` の 1 か所のまま |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。`.md` の文言を照合するテストは書かない（`AGENTS.md` の DON'T） |
| テスト戦略 | マニフェストと一覧の突き合わせ・worktreeへ写す Skill の有無は単体のテストで、導入できることはスモークテストで、相手役として動くことはリリース後テストで確かめる |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テスト・検査の実行、外したファイルを参照する箇所の掃除 |
| 確認してから行う（承認ゲート 1・2 で人が見る） | 配る Skill の一覧（未決 1）、ホストの役割を残すか（未決 2）、`AGENTS.md` の運用の節の書き換え（C7）、公開インタフェースの縮小 |
| 行わない | Claude Code の配布物の変更、cross 系の振る舞いの変更、open の課題を実装の PR で閉じること、利用者の手元の Skill を消すこと |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. 相手役に要る Skill の一覧（前提 3 のとおりなら、相手役のランタイムへ配る Skill は 0 件にもなりうる。その場合にマニフェストを空にするか、cross-review・cross-refactoring だけを残すか） | 設計で案を作り、利用者が承認ゲート 1 で決める | 承認ゲート 1 |
| 2. codex・agy・kiro をホストにする使い方を残すか（残すなら `cross-review`・`cross-refactoring`・`fix`・`pr-review` と cross-refactoring の `REQUIRED_SKILLS` が要る） | 利用者（承認ゲート 1） | 承認ゲート 1 |
| 3. 配布から外れた Skill が利用者の手元に残るか（ランタイムごと） | 設計で実測して決める | 設計 PR |
| 4. プロジェクト MVV の Mission（「Claude Code / Codex / Kiro / agy のどれを使うチームにも、要求から本番の配布までの開発工程を…届け」）と Vision が、この変更の後の配布（codex・agy・kiro は相手役だけ）と食い違う。利用者の決定（2026-10-31）は MVV より上位なので要求は進めるが、MVV の改訂（`development-workflow` の `references/project-mvv.md` の「改訂」）を起こすか | 利用者（承認ゲート 1。改訂は利用者の承認で成立する） | 承認ゲート 1 |
