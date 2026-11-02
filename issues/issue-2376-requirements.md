# #2376: worker: ステップの工程と作業に応じて、要る Skill だけを渡す

正は課題の本文（#2376）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 今どうなっているか

プランの worker（`supervise.py` が `claude -p` で起動するもの）と Agent の `ndf:worker` は、ステップに要る Skill を受け取る仕組みを持たない。

| worker | Skill の扱い |
| --- | --- |
| プランの通常の worker（`WORK_SYSTEM`） | Skill のツールが無く、Skill の規則を受け取らない |
| プランの full の worker（`FULL_SYSTEM`） | Skill を呼べるが、どれを読むかはプロンプト次第 |
| PR 本文を書く worker（`PR_SYSTEM`） | Tool が無く、規則を読めない |
| Agent の `ndf:worker` | 定義で Skill を外し、「`SKILL.md` を読まない」と定める |

その結果、たとえば `document-style` は 2026-10 以降、AI からほぼ起動されていない（#2372 で実測）。#2372 では `document-style` だけを 3 つの system プロンプトへ個別に足して止血したが、別の Skill で同じことが起きればまた同じ書き足しが要る。文書を書かないステップにも指示が常に載る。

## 誰に効くか

- NDF でスプリントを回す利用者（プランの worker が書く文書・コードの質）
- NDF の保守者（Skill を足したときに worker へ届ける経路が 1 か所になる）

## 変えると何が成り立つか

worker が動くとき、そのステップの工程と作業に要る Skill だけが、worker の種類に合った形で届く。

## スケルトン（設計で決める）

| 部品 | 中身 |
| --- | --- |
| 対応表（1 か所） | 工程（工程表の行名）→ 要る Skill の一覧。例: 設計・ドキュメント再構成・Pull Request・振り返り → `document-style`、実装 → `logging-guidelines` |
| プランのステップ | 任意の `skills` 欄で上書き・追加できる。無ければ対応表から引く |
| 渡し方 | Skill を呼べる worker には「読み込む」指示、Tool のある worker には SKILL.md のパス、Tool の無い worker には規則の要約。どれもそのステップの分だけ |
| Agent の `ndf:worker` | supervisor が起動指示の「読む Skill」の欄にパスを書く。worker の定義は「欄に挙がった SKILL.md だけ読む」へ一般化する |

## Claude Code が持つ手段（2026-11-02、v2.1.316 で調べた）

「特定の Skill だけを有効にする」オプションは無い。使えそうなものは次のとおり。

| 機能 | 何ができるか | 限られること |
| --- | --- | --- |
| subagent の frontmatter の `skills` 欄 | 挙げた Skill の本文全体を起動の時点で文脈へ読み込む | 読み込むだけで、他の Skill の起動は止めない（止めるには `disallowedTools: Skill`） |
| `claude -p --agents '<JSON>' --agent <名前>` | subagent の定義を起動のたびに JSON で渡す | JSON の `skills` が効くかは未確認 |
| 権限の規則 `Skill(name)` | 特定の Skill の起動を許す・拒む | allow は事前の承認で、他を隠さない |
| 設定の `skillOverrides` | Skill ごとに一覧から隠す | プラグインの Skill には効かない |
| `--disable-slash-commands` | Skill とコマンドをすべて止める | 全部か無しか |

出典: https://code.claude.com/docs/en/sub-agents 、https://code.claude.com/docs/en/skills

**第一候補:** ステップごとに `--agents` で worker の定義を組み、`skills` に対応表から引いた Skill を挙げ、`disallowedTools: Skill` を残す。設計の前に次の 2 つを実測で確かめる。

- 通常の worker は `--setting-sources ""` でプラグインを読まない。この起動でプラグインの Skill（`ndf:document-style`）を `skills` で読み込めるか
- `--agents` の JSON で `skills` が効くか

効かなければ、SKILL.md のパスを渡して読ませる形（#2372 の止血）を対応表で一般化する。

## 実測: `skills:` と `disallowedTools: Skill` の併用（2026-11-02、v2.1.316）

`skills: [ndf:document-style]` と `disallowedTools: Skill` を持つ定義で、Tool を使わずに規則 1 の見出しを答えられるかを見た。

| 起動のしかた | 定義の置き場 | 本文の読み込み | Skill のツール |
| --- | --- | --- | --- |
| `claude -p --agent <名前>`（本体のセッションに当てる） | `--agents` の JSON・ファイルとも | されない | 無い |
| 本体から Agent ツールで subagent として起動 | `--agents` の JSON・ファイルとも（`ndf:` の有無を問わない） | される | 無い |
| 同上、`--setting-sources ""` | `--agents` の JSON | されない（プラグインが読み込まれない） | 無い |
| 同上、`--setting-sources ""` に `--plugin-dir <NDF>` を足す | `--agents` の JSON | される | 無い |

- 併用すると「指定した Skill の本文を最初から持ち、他の Skill は呼べない worker」になる
- `skills:` は subagent として起動したときだけ効く。`--agent` で本体に当てても効かない
- プランの worker は、本体の `claude -p`（`--setting-sources "" --plugin-dir <NDF>`）から `--agents` の JSON で組んだ worker を subagent として 1 つ起動する形にすれば、ステップごとに要る Skill だけを入れられる。起動が 1 段増える費用と時間は設計で測る
- Agent の `ndf:worker` はそのまま効く。目的ごとの定義を `skills:` つきで選ぶ

## 受け入れ条件

- [ ] 工程 → Skill の対応表が 1 か所にあり、プランの生成と Agent の起動指示の両方がそこを読む
- [ ] プランのステップの `skills` 欄で、対応表の値を上書き・追加できる（無ければ対応表の値）
- [ ] 3 種類のプランの worker と Agent の `ndf:worker` に、そのステップの Skill だけが種類に合った形で届く。要らないステップには届かない
- [ ] #2372 で個別に足した `document-style` の指示が、この仕組みに置き換わる
- [ ] 届いたことを、プランの記録（`progress.jsonl` など）から確かめられる

## 関連

- #2372（`document-style` だけの止血）
- 設計のプランに組み直しのステップを足した PR #2375

## 依頼（原文）

> worker: ステップの工程と作業に応じて、要る Skill だけを渡す

> worker が動くとき、そのステップの工程と作業に要る Skill だけが、worker の種類に合った形で届く。

（書き手: 起票者、日付: 2026-11-02。題と「変えると何が成り立つか」の節。上の「今どうなっているか」から「関連」までの節もすべて起票者が #2372 の対応の中で書いた。利用者の指示の原文は課題に無い）

> 人へ問わずに進め、決められない点は前提か未決として課題の本文へ書く。

（書き手: AI の起動指示、日付: 2026-11-02。要求の工程の進め方の指示で、要求の中身ではない）

## 起票者の推定

題・本文の条件はすべて起票者が #2372 の実測（`document-style` が 2026-10 以降 AI からほぼ起動されていない）から推して書いた。承認ゲート 1 で利用者が確かめるまで、利用者の決定として扱わない。

| # | 条件 | 推した出所 | この要求での扱い |
| --- | --- | --- | --- |
| S1 | 工程 → Skill の対応表を 1 か所に置き、プランの生成と Agent の起動指示の両方がそこを読む | 本文の部品の表と「受け入れ条件」1 行目 | 受け入れ条件 1・2・10 |
| S2 | 対応表の初期値は、設計・ドキュメント再構成・Pull Request・振り返り → `document-style`、実装 → `logging-guidelines` | 本文の部品の表の例 | 前提 2 |
| S3 | プランのステップの `skills` 欄で対応表の値を上書き・追加でき、無ければ対応表の値 | 本文の部品の表と「受け入れ条件」2 行目 | 受け入れ条件 3・4・5 |
| S4 | 渡し方は worker の種類で変える（Skill を呼べる worker には読み込む指示、Tool のある worker には SKILL.md のパス、Tool の無い worker には規則の要約） | 本文の部品の表の「渡し方」の行 | 受け入れ条件 7・8・9（形は設計で決める。未決 1） |
| S5 | 第一候補は、ステップごとに `--agents` の JSON で worker の定義を組み、`skills` に Skill を挙げ `disallowedTools: Skill` を残して subagent として起動する形 | 本文「Claude Code が持つ手段」「実測」 | 含まない（作り方は設計で決める。未決 1） |
| S6 | Agent の `ndf:worker` は、起動指示の「読む Skill」の欄にパスを書く形か、目的ごとの定義を `skills:` つきで選ぶ形にする | 本文の部品の表と「実測」の節（2 つの案が並んでいる） | 受け入れ条件 10・11（どちらの形かは未決 2） |
| S7 | #2372 で個別に足した `document-style` の指示をこの仕組みへ置き換える | 本文「受け入れ条件」4 行目 | 受け入れ条件 12・13 |
| S8 | 届いたことを、プランの記録（`progress.jsonl` など）から確かめられる | 本文「受け入れ条件」5 行目 | 受け入れ条件 14 |
| S9 | 要らないステップには届かない | 本文「受け入れ条件」3 行目 | 受け入れ条件 6 |

## 目的

- worker が動くとき、そのステップに要る Skill の規則が worker に届き、要らない Skill の規則は届かない状態にする
- Skill を worker へ届ける経路を 1 か所にし、NDF の保守者が新しい Skill を届けたいときに対応表の 1 行を足せば済むようにする（#2372 のように worker の種類ごとの文を個別に書き足さない）

## 前提

- 前提 1: 対応表のキーは工程（ステップの `stage`。`development-workflow` の工程表の行名と同じ値）とする。同じ工程の中で作業（`kind`）ごとに要る Skill が違うときは、そのステップの `skills` 欄で表す。作業をキーに足すかは設計で、届け先の数を数えてから決めてよい
- 前提 2: 対応表の初期値は S2 のとおりとする（設計・ドキュメント再構成・Pull Request・振り返り → `document-style`、実装 → `logging-guidelines`）。行を足す・外すのは対応表の 1 行の変更で済み、この要求の受け入れ条件を変えない
- 前提 3: 対象の worker は 4 種類とする。プランの通常の worker（`WORK_SYSTEM`）・full の worker（`FULL_SYSTEM`）・PR 本文を書く worker（`PR_SYSTEM`）・Agent の `ndf:worker`。判断だけを行う `JUDGE_SYSTEM` は Skill を受け取らない
- 前提 4: 届ける Skill は NDF のプラグインに同梱された Skill に限る（`plugins/ndf/skills/<名前>/SKILL.md` が実在するもの）。利用者のプロジェクトや他のプラグインの Skill は対応表に書けない
- 前提 5: 起動の形の実測は課題の本文の「実測」の節（2026-11-02、Claude Code v2.1.316）を出発点にする。版が上がって振る舞いが変わったときは設計の時点で測り直す

## 対象範囲

含む:
- 工程 → Skill の対応表（1 か所）と、それを引く処理
- プランのステップの `skills` 欄（置き換えと追加）と、プランの検査での名前の照合
- 4 種類の worker への渡し方（そのステップの分だけ）
- Agent の `ndf:worker` を起動するときに supervisor が対応表を引く手順と、`ndf:worker` の定義の一般化
- #2372 で足した `document-style` 専用の指示（`prompts.py` の `DOCUMENT_STYLE`・`DOCUMENT_RULE`・`_style_rules`、`FULL_SYSTEM` の 1 行、`worker.md` と `agent-layers.md` の例外の文）の置き換え
- 届けた Skill を `progress.jsonl` に残すこと
- 起動 1 回あたりの費用と秒の変更前後の実測

含まない:
- 対応表の初期値より先の Skill の選び直し（どの工程にどの Skill が要るかの棚卸し）
- 利用者のプロジェクト・他のプラグインの Skill を届けること（前提 4）
- 判断だけを行う worker（`JUDGE_SYSTEM`）への配布
- cross-review・cross-refactoring・external-ai が起動する相手役の CLI（codex・kiro・agy）への配布
- conductor（本体のセッション）が読む Skill の決め方

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | プランを作った（ステップに `stage` と、要れば `skills` 欄を書いた） | 雛形の生成か、手で書いたプラン | — | — |
| E2 | プランを検査した（`skills` 欄の Skill 名を照合した） | `supervise.py` の起動・キューへの投入 | 実在しない Skill 名があれば、起動せずに名前を示して落ちる（受け入れ条件 5） | E1 |
| E3 | ステップに届ける Skill を決めた（対応表と `skills` 欄から） | worker のステップの開始 | 対応表を読めない・Skill の `SKILL.md` が無いときは、worker を起動せずにステップを失敗として記録する（受け入れ条件 15） | E2 |
| E4 | worker を起動した（種類に合った形で Skill を渡した） | E3 | 起動の失敗は今の扱いのまま | E3 |
| E5 | 届けた Skill を記録した | E4 | 記録の書き込みに失敗しても worker の結果は変えない（今の `progress.jsonl` の扱いと同じ） | E3。起動の前後どちらで書くかは設計で決める |
| E6 | supervisor が `ndf:worker` へ起動指示を書いた（対応表を引いて「読む Skill」を決めた） | supervisor が作業を 1 つ渡す | 対応表を引くコマンドが失敗したら、Skill を空にせず supervisor が `結果: 判断が要る` で返す | — |
| E7 | worker が届いた Skill の規則で作業し、報告を返した | E4・E6 | — | E4 か E6 |

## 用語

| 用語 | 意味 |
| --- | --- |
| Skill の対応表 | 工程（ステップの `stage`）から、そのステップの worker へ届ける Skill の一覧を引く表。1 か所に置く |
| Skill の渡し方 | Skill を worker へ届ける形。worker の種類で決まる（読み込ませる・`SKILL.md` のパスを渡す・規則の要約を渡す、など） |

## 受け入れ条件

対応表:

- [ ] 1. Skill の対応表はリポジトリの中の 1 か所にだけあり、工程の名前を渡すと Skill の一覧を返す。プランの worker の起動と、Agent の `ndf:worker` 向けの引くコマンド（受け入れ条件 10）の両方がその 1 か所を読む（ほかに工程と Skill の組を持つ定数・文が無いことを `grep` で確かめる）
- [ ] 2. 対応表に無い工程のステップと、`stage` の無いステップには、Skill が 1 つも届かない

プランのステップの `skills` 欄:

- [ ] 3. 置き換えの指定をしたステップには、指定した Skill だけが届く。0 件で置き換えたステップには何も届かない
- [ ] 4. 追加の指定をしたステップには、対応表の Skill と追加した Skill が重ならずに届く
- [ ] 5. 実在しない Skill 名を書いたプランは、worker を 1 つも起動する前の検査で落ち、その名前とステップの `id` を出力に示す
- [ ] 6. `skills` 欄の無い既存のプランは、書き換えずに今と同じく検査を通り、対応表の値で動く（退行しない）

worker への渡し方:

- [ ] 7. プランの通常の worker（`WORK_SYSTEM`）の起動に、そのステップの Skill の規則が届く。対応表と `skills` 欄で決まった Skill の名前のほかの Skill（対応表のほかの行の Skill）は、起動に渡すもの（引数・プロンプト・定義）に含まれない
- [ ] 8. full の worker（`FULL_SYSTEM`）の起動に、そのステップの Skill を読み込む指示か本文が届き、ほかの行の Skill の名前は含まれない
- [ ] 9. PR 本文を書く worker（`PR_SYSTEM`）に、`Pull Request` の工程で対応表が挙げる Skill の規則が Tool を使わずに読める形で届く
- [ ] 10. 工程の名前を渡すと、対応表の Skill の名前と `SKILL.md` の絶対パスを返すコマンドがある。supervisor が `ndf:worker` を起動するときの手順（`agent-layers.md` の「supervisor → worker」）がこのコマンドで「読む Skill」を決める
- [ ] 11. `ndf:worker` の定義は特定の Skill の名前を持たず、起動指示か定義で渡された Skill だけを読む。`Skill` と `Agent` のツールは今と同じく使えない（退行しない）
      前提の確認: `plugins/ndf/agents/worker.md` の frontmatter が `disallowedTools: Skill, Agent`（2026-11-02、83cfd6ff2）

#2372 の置き換え:

- [ ] 12. `prompts.py` の `DOCUMENT_STYLE`・`DOCUMENT_RULE`・`_style_rules` と、`FULL_SYSTEM` の `document-style` の 1 行が無くなり、同じ規則が対応表の `document-style` の行から届く
- [ ] 13. `worker.md` と `agent-layers.md` から `document-style` だけを名指す例外の文が無くなる。設計・ドキュメント再構成・Pull Request・振り返りの工程の worker には、今と同じく `document-style` の規則が届く（退行しない）
      前提の確認: `git show 0d305775d -- plugins/ndf/scripts/supervise_lib/prompts.py` で、今は `WORK_SYSTEM`・`FULL_SYSTEM`・`PR_SYSTEM` のすべての起動に工程を問わず届いている（2026-11-02）

記録と異常系:

- [ ] 14. worker のステップごとに、届けた Skill の名前の一覧（0 件なら空の一覧）と渡し方が `progress.jsonl` の 1 行に残り、記録だけでどのステップに何が届いたかを答えられる
- [ ] 15. ステップの開始のときに対応表を読めない、または届ける Skill の `SKILL.md` が無いときは、worker を起動せずにそのステップを失敗とし、読めなかった Skill の名前かファイルを記録に残す

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | Skill を届けるステップと届けないステップのそれぞれで、worker の起動 1 回あたりの費用（USD）と秒を変更前後で同じステップについて実測し、設計文書と実装の PR に載せる。起動が 1 段増える形を採るなら、その増分も同じ表に載せる。増分の許容（上限）は実測の後に設計で決める（未決 3） |
| 運用・保守性 | 新しい Skill をある工程の worker へ届けるのに要る変更は、対応表の 1 行の追加だけになる（`prompts.py`・worker の定義・`agent-layers.md` を書き換えない） |
| 移行性 | `skills` 欄の無い既存のプラン・キューに入ったプランは、移行の手順なしでそのまま動く（受け入れ条件 6） |
| システム環境 | Claude Code v2.1.316 以降で動く。通常の worker の起動（`--setting-sources ""`・`--disable-slash-commands`）でプラグインが読み込まれない条件の下でも届く |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | プランの JSON にステップの任意の欄 `skills` が加わる（互換あり。無ければ今の振る舞い）。対応表を引くコマンドが加わる |
| データ | `progress.jsonl` の worker のステップの行に、届けた Skill と渡し方の項目が加わる（項目の追加だけで、既存の項目は変えない） |
| 既存の振る舞い | worker の起動の形（プロンプト・引数・定義）が変わる。`document-style` 以外の Skill が届くステップ（初期値では実装の工程の `logging-guidelines`）が生まれる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/scripts/tests -q -n 4`。受け入れ条件 1〜9・12・14・15 は、起動の引数とプロンプトを組み立てる関数・プランの検査・記録の書き込みの単体テストで確かめる（`claude` を起動しない） |
| 静的解析・型検査 | `python3 scripts/check-skill-frontmatter.py`・`python3 plugins/ndf/scripts/instructions-check.py --root .`・`claude plugin validate .` |
| 受け入れ条件 1・12・13 | `grep -rn "document-style" plugins/ndf/scripts/supervise_lib plugins/ndf/agents/worker.md plugins/ndf/skills/development-workflow/references/agent-layers.md` が対応表の行のほかに当たらない |
| 受け入れ条件 10 | 対応表を引くコマンドへ `設計` と存在しない工程名を渡し、前者が `document-style` の名前と実在するパスを、後者が空の一覧を返す |
| 手動確認（マージ前） | 実在の `claude` で、設計の工程の work ステップ 1 つと、Skill の無い工程の work ステップ 1 つを起動し、前者だけが `document-style` の規則 1 の見出しを Tool を使わずに答えられること、`progress.jsonl` に両方の行が残ることを見る。同じ起動で費用と秒を測る（非機能の性能の行） |
| 手動確認 | Agent の `ndf:worker` を設計の工程の作業で 1 回起動し、報告の文書が `document-style` の「書き終えたらセルフチェック」を通っていることを見る（supervisor は LLM のため、リリース後テストで見る） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 対応表と引く処理は `plugins/ndf/scripts/` の下に置き、実験版の置き場（`plugins/ndf/scripts/experimental/`）には置かない（既定で働くため安定版。`AGENTS.md` の「安定版と実験版」） |
| コーディング規約 | `AGENTS.md` の「最小限のコード実装」。Skill の frontmatter は `plugins/ndf/skills/AUTHORING.md`。同じ役割の定数を Skill ごと・worker の種類ごとに分けない（MVV Value 6） |
| テスト戦略 | 振る舞いは単体テストで担保する。`.md` の文言を照合するテストは書かない（`AGENTS.md` の DON'T）。#2372 の `test_supervise_document_style.py` は新しい仕組みのテストへ置き換える |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、`skills` 欄の無いプランが変わらず動くことの確認、起動の費用と秒の実測 |
| 確認してから行う | プランの JSON の欄と `progress.jsonl` の項目の追加（承認ゲート 1 の設計で人が見る）。`worker.md`・`agent-layers.md` の規約の書き換え（承認ゲート 2 で人が見る） |
| 行わない | 対応表の初期値より先の Skill の選び直し、相手役の CLI への配布、`JUDGE_SYSTEM` の変更 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| 1. プランの worker への渡し方の形（`--agents` の JSON で組んだ worker を subagent として起動する形〔S5〕か、`SKILL.md` のパスか本文をプロンプトへ入れる形か） | 設計（実測の結果で選び、承認ゲート 1 で人が確かめる） | 設計 PR |
| 2. Agent の `ndf:worker` の形（起動指示の「読む Skill」の欄にパスを書く形か、目的ごとの定義を `skills:` つきで選ぶ形か〔S6〕） | 設計 | 設計 PR |
| 3. 起動 1 回あたりの費用と秒の増分の許容（上限） | 設計（実測の後に決め、承認ゲート 1 で人が確かめる） | 設計 PR |
| 4. `skills` 欄で置き換えと追加をどう書き分けるか（欄の形） | 設計 | 設計 PR |
