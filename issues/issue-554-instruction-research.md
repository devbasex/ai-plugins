> **#554 の設計のために行った外部調査の記録である。** 設計は [issue-554-design.md](issue-554-design.md)、
> 観点の採否は [issue-554-design-criteria.md](issue-554-design-criteria.md) にある。
> **2026-09-17 時点の記録で、以後の変更には追随しない**（調べ直しの手がかりは本文の「5. 調べ直す手がかり」にある）。

# エージェント向け指示書はどうあるのが適切か（外部一次情報の調査）

**調査日: 2026-09-17。** 以下の URL はすべてこの日に取得した。値・引用はすべてこの日時点のもので、
更新され続けるページから取ったものは「5. 調べ直す手がかり」に再取得先を書いた。

**この文書の範囲。** 対象は「AI コーディングエージェントが毎回読む指示ファイル」である
（`AGENTS.md` / `CLAUDE.md` / `GEMINI.md` / `.cursor/rules/` / `.github/copilot-instructions.md` /
`.kiro/steering/`）。Skill・hook・permission は、指示書から何を追い出すかの受け皿としてだけ扱う。

**書いていないことは書かない。** 出典で確かめられた主張だけを本文に置き、確かめられなかったものは
「推測」「未確認」と明記した。

---

## 0. 先に結論（実例から）

このリポジトリを実例にすると、観点がどう効くかがすぐ分かる。2026-09-17 時点で
`wc -l AGENTS.md CLAUDE.md` は **215 行 / 79 行**を返す。

- **`AGENTS.md` の 215 行は、Anthropic の目安（200 行）を超えている**（観点 1）
- **`CLAUDE.md` は冒頭で `**@AGENTS.md**` と書いている。** バッククォートで囲まれていないため
  これは Claude Code の import として展開され、起動時に**両方が文脈へ載る**（観点 12、S4）。
  合計 294 行が毎セッション読まれる
- **同じ `CLAUDE.md` は別の箇所で「この参照に `@` を付けない」と書いている**（`docs/ndf-version-decisions.md`
  について）。付けている参照と付けない参照が同じファイルに同居しており、判断の基準が読み手に
  伝わるかは検査の対象になる（観点 7）

**この 3 つは、どれも思いつきではなく外部の一次情報に対応づく。** 以下はその対応づけである。

---

## 1. 観点の一覧

各観点は「何を言っているか」「出典」「機械で判定できるか」の 3 つを持つ。出典の記号（S1〜S23）は
「2. 出典の表」に対応する。

### 1-1. 分量に上限がある

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | 指示書は長いほど**遵守率が落ちる**。各ランタイムが異なる形で上限を示す。Claude Code は「target under 200 lines per CLAUDE.md file. Longer files consume more context and reduce adherence.」（S4）。Cursor は「Keep rules under 500 lines」（S11）。GitHub Copilot は「Instructions must be no longer than 2 pages」（S12）。OpenAI Codex は**実装として**打ち切る:「Codex skips empty files and stops adding files once the combined size reaches the limit defined by `project_doc_max_bytes` (32 KiB by default).」（S10）。Claude Code は 4 MiB を超える `CLAUDE.md` を**読み飛ばす**（S4） |
| 出典 | S4, S10, S11, S12, S20 |
| 機械判定 | **できる。** 行数・バイト数の閾値比較。ただし閾値はランタイムごとに違う（200 行 / 500 行 / 2 ページ / 32 KiB / 4 MiB） |

**注意: 200 行はランタイム側の助言であり、研究が導いた値ではない。** 研究（S20）はこの Anthropic の
助言を借りて Context Bloat の検出閾値に使っている。値の出所は 1 つである。

### 1-2. 指示の数そのものが遵守率を下げる

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | 指示の**密度**が上がると遵守率が下がる。IFScale（500 個のキーワード包含指示で測る評価）では「even the best frontier models only achieve 68% accuracy at the max density of 500 instructions」（S19、2025-07-15、20 モデル）。劣化の形は 3 種類（threshold / linear / exponential）に分かれ、閾値型では N ≈ 150–200 まで高精度を保つ |
| 出典 | S19 |
| 機械判定 | **できる**（箇条書き・命令文の個数を数える）。ただし「多すぎる」の線をどこに引くかは判定できない |

### 1-3. 指示の位置が効く（先に書いたものが守られやすい）

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | IFScale は「bias towards earlier instructions」を報告する（S19）。長文脈では中央の情報が最も使われない:「performance is often highest when relevant information occurs at the beginning or end of the input context, and significantly degrades when models must access relevant information in the middle of long contexts」（S18、Lost in the Middle、2023-07） |
| 出典 | S18, S19 |
| 機械判定 | **できない。** どの指示が重要かを機械が決められないため、順序の妥当性は判定できない。**部分的にできるのは「重要と明示された指示が末尾にある」ことの検出だけ**（推測: 有効性は未検証） |

### 1-4. 長い文脈そのものが精度を下げる（分量制限の根拠）

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | 入力が長くなるほど精度が落ちる。Chroma の評価は 18 モデル（Claude / GPT / Gemini / Qwen）で「model performance varies significantly as input length changes, even on simple tasks」を示す（S17、2025-07-14）。同じ質問でも、関係ない文脈を足した約 113k トークンの入力は、約 300 トークンの絞った入力より精度が落ちた。Claude Code のドキュメントも「LLM performance degrades as context fills」と述べる（S5） |
| 出典 | S5, S17 |
| 機械判定 | **できない**（観点ではなく、他の観点の根拠） |

**この観点は指示書の分量だけを責めない。** 指示書は起動時に載る分の一部でしかない。Claude Code の
文脈の内訳（S7）は、システムプロンプト・auto memory・Skill 一覧・MCP ツール名・`CLAUDE.md` が
最初のプロンプトの前に載ることを示す。**ただし S7 のトークン数は「Token counts are illustrative」と
明記された例示値であり、実測ではない。**

### 1-5. 具体的で、検証できる形で書く

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | Claude Code は具体例で対比を示す:「"Use 2-space indentation" instead of "Format code properly"」「"Run `npm test` before committing" instead of "Test your changes"」「"API handlers live in `src/api/handlers/`" instead of "Keep files organized"」(S4)。プロンプトの一般則も同じで、「**Golden rule:** Show your prompt to a colleague with minimal context on the task and ask them to follow it. If they'd be confused, Claude will be too.」（S8） |
| 出典 | S4, S8 |
| 機械判定 | **部分的。** 「適切に」「きれいに」のような測れない語の検出はできるが、具体性そのものは判定できない |

### 1-6. 否定形より肯定形で書く

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | Anthropic のプロンプト実践は「**Tell Claude what to do instead of what not to do**」を筆頭に置き、「Instead of: "Do not use markdown in your response" / Try: "Your response should be composed of smoothly flowing prose paragraphs."」と例示する（S8） |
| 出典 | S8 |
| 機械判定 | **部分的。** 「〜しない」「Do not」「禁止」のパターンは検出できるが、肯定形に直せるかどうかは判定できない |

### 1-7. 矛盾した指示を残さない

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | 「if two rules contradict each other, Claude may pick one arbitrarily」（S4）。実測では **Conflicting Instructions が 100 リポジトリ中 28%** に存在した（S20）。研究は「small variations in the prompt can result in different agent behavior」と述べる。矛盾は単独で現れず、「Conflicting Instructions and Skill Leakage increase likelihood of Context Bloat by 83%」と共起する（S20） |
| 出典 | S4, S20 |
| 機械判定 | **部分的。** S20 は LLM に検出させて著者が手で検証しており、LLM 検出の精度は 57–93% と報告されている。**自動検出だけでは偽陽性が残る** |

### 1-8. 決定的に強制できるものは指示書に書かない

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | 指示書は**助言であって強制ではない**。Claude Code は「Claude treats them as context, not enforced configuration. To block an action regardless of what Claude decides, use a PreToolUse hook instead.」（S4）、「Unlike CLAUDE.md instructions which are advisory, hooks are deterministic and guarantee the action happens.」（S5）と述べ、設定と指示書の役割分担を表で分ける（S4: 「Settings rules are enforced by the client regardless of what Claude decides to do」）。実測では **Lint Leakage が 62%** と最も多い smell で、「linter・formatter・静的解析がすでに強制している規則を指示書が繰り返している」状態を指す。研究はこれらを「deterministic problems with deterministic solutions」と呼ぶ（S20） |
| 出典 | S4, S5, S20 |
| 機械判定 | **部分的〜できる。** lint 設定（`.editorconfig` / eslint / ruff など）に書かれた項目と指示書の語句の突き合わせは自動化できる。ただし対応づけは語彙の問題を含む |

**この観点は NDF の検査設計に最も直接効く。** 62% という値は「指示書の検査でまず見るべき 1 つ」を示す。

### 1-9. 常時要らないものは要求時読み込みへ追い出す

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | **Skill Leakage が 35%**（特定用途・稀にしか使わない指示が本体に居座る、S20）。各ランタイムが逃がし先を持つ: Claude Code は Skill と `paths:` 付きの `.claude/rules/`（S4）、「For domain knowledge or workflows that are only relevant sometimes, use skills instead. Claude loads them on demand without bloating every conversation.」（S5）。Cursor は 4 種類の適用方式（Always / Apply Intelligently / glob 一致 / 手動）（S11）。Kiro は inclusion モード（always / fileMatch / manual / auto）（S16）。GitHub Copilot は `.github/instructions/NAME.instructions.md` の `applyTo`（S12, S13） |
| 出典 | S4, S5, S11, S12, S13, S16, S20 |
| 機械判定 | **部分的。** 「1 箇所にしか効かない指示が常時読み込みの本体にある」ことは、パス名の出現などから推定できる。確実な判定はできない |

### 1-10. コードや履歴から導けることを書かない

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | Claude Code は含める／除くを表で並べる。除く側:「Anything Claude can figure out by reading code」「Standard language conventions Claude already knows」「Detailed API documentation (link to docs instead)」「Information that changes frequently」「File-by-file descriptions of the codebase」「Self-evident practices like "write clean code"」（S5）。`/doctor` は「cuts content Claude can derive from the codebase, such as directory layouts, dependency lists, and architecture overviews, and keeps pitfalls, rationale, and conventions that differ from tool defaults」と動く（S4、v2.1.206 以降）。判断の基準は 1 行ずつの問いである:「For each line, ask: "Would removing this cause Claude to make mistakes?" If not, cut it.」（S5） |
| 出典 | S4, S5 |
| 機械判定 | **部分的。** ディレクトリ一覧・依存一覧のような**形が決まった塊**は検出できる。「コードから導けるか」の一般判定はできない |

### 1-11. 参照は説明を添えて書く

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | **Blind References が 16%**（外部の文書やディレクトリを、用途の説明なしに指すだけ）。研究は、説明のないパス参照をエージェントは「will often ignore」と述べる（S20）。GitHub Copilot はさらに強く、**外部資料を参照させる指示自体を避けよ**と書く:「Requests to refer to external resources when formulating a response」を悪い例に挙げ、具体例として「Always conform to the coding styles defined in styleguide.md」を挙げる（S13） |
| 出典 | S13, S20 |
| 機械判定 | **部分的。** 「リンクまたはパスだけの行」の検出はできる。説明として十分かは判定できない |

**ランタイムで扱いが割れる点である。** Claude Code / Gemini CLI は `@path` の**即時展開**を持ち、参照が
読み込みになる（後述 4-3）。Copilot は参照そのものを避けよと言う。同じ「参照」でも意味が違う。

### 1-12. 階層に分け、重複を持たせない

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | 大きなリポジトリでは根に 1 枚だと「either grow to cover every subsystem's conventions, costing context on instructions unrelated to the current task, or stay too generic to be useful」（S6）。推奨は 2 段:「Root `CLAUDE.md`: instructions that apply everywhere」「Per-subdirectory `CLAUDE.md`: conventions specific to that area's stack」（S6）。`AGENTS.md` 仕様も同じ:「Place another AGENTS.md inside each package. Agents automatically read the nearest file in the directory tree, so the closest one takes precedence.」「The closest AGENTS.md to the edited file wins; explicit user chat prompts override everything.」（S1） |
| 出典 | S1, S6, S10 |
| 機械判定 | **部分的。** 同一文・同一規則の重複は検出できる。階層の切り方の妥当性は判定できない |

**「近いものが勝つ」は仕様の言い方であって、実装は連結である。** Claude Code は明示的に
「All discovered files are concatenated into context rather than overriding each other.」と書き、
**根から作業ディレクトリへ向かって並べる**（S4）。Codex も同じく連結し、「Files closer to your current
directory override earlier guidance because they appear later in the combined prompt.」と、
**後に来ることが上書きの実体である**と説明する（S10）。つまり**上書きは消去ではない**。矛盾した
指示は両方文脈に載る。

### 1-13. ランタイム間で食い違わせない

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | Claude Code は `AGENTS.md` を読まない:「Claude Code reads `CLAUDE.md`, not `AGENTS.md`. If your repository already uses `AGENTS.md` for other coding agents, create a `CLAUDE.md` that imports it so both tools read the same instructions without duplicating them.」（S4）。手段は `@AGENTS.md` の import、または symlink（`ln -s AGENTS.md CLAUDE.md`）。Copilot は `AGENTS.md` / `CLAUDE.md` / `GEMINI.md` を**いずれも**読む（S12, S13, S14）。Gemini CLI は既定が `GEMINI.md` で、`context.fileName` に `["AGENTS.md", "CONTEXT.md", "GEMINI.md"]` のように並べれば読む（S15、既定へ入れる要望は S23 で議論中） |
| 出典 | S4, S12, S13, S14, S15, S23 |
| 機械判定 | **できる。** 同じ内容を持つべきファイル群が、import / symlink / 実体の重複のどれで実現されているかは検査できる。実体が重複しているなら差分も検査できる |

### 1-14. 陳腐化させない（生成したまま放置しない）

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | **Init Fossilization が 24%**（初期化コマンドが生成した指示書が、その後 1 度も見直されていない）。研究はこれを**コミット数 1** で検出した（S20）。Claude Code は運用として 3 つを挙げる:「Review in pull requests」「Revisit after major model releases: instructions that worked around an older model's limitation may become overhead once a newer model handles the case on its own」「Add a Stop hook that proposes updates」（S6） |
| 出典 | S6, S20 |
| 機械判定 | **できる。** git 履歴（コミット数、最終更新日、コードの変更に対する追随）から判定できる。**この観点は最も自動化しやすい** |

### 1-15. 増え続けさせない／削除の根拠を残す

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | 指示書は**無制限に増える**。1,867 の GitHub リポジトリで 247,694 件の指示の寿命を追った研究は、寿命の間に **+226%**、**1 コミットあたり正味 +4.9 件**の増加を測り、古い指示ほど消されにくい（log-hazard −0.032/commit）ことを示す。全面書き直しの後も増加は再開する（+4.9%/commit）。原因は「appending an instruction is always cheap, but once an instruction's rationale is gone, deleting it without risking a correctness regression costs O(2^\|D\|)」——**根拠が失われると安全に消せなくなる**こと。対策として「prompt comments」（各指示に失敗・仮説・理由を添える）を提案し、IFEval 由来の評価で肥大 −99.3%、WildIFEval で遵守 +23.1% を報告する（S21） |
| 出典 | S21 |
| 機械判定 | **できる。** 行数の時系列と、指示ごとの根拠注記の有無は検査できる |

**S21 はプレプリント（2026-08-11 提出）であり、査読を経ていない。** 提案手法の効果（−99.3% / +23.1%）は
著者らの評価である。**増加の実測（+226% / +4.9）は観測データで、提案手法の効果とは別に読める。**

**根拠を書く場所は Claude Code にはある。** 「Block-level HTML comments (`<!-- maintainer notes -->`) in
CLAUDE.md files are stripped before the content is injected into Claude's context.」（S4）——
**人向けの注記はトークンを消費せずに残せる。** 他のランタイムで同じ扱いになるかは未確認。

### 1-16. 強調を安売りしない

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | 「If Claude keeps skipping one instruction, add emphasis such as "IMPORTANT" to that line alone. **If you emphasize many lines, none of them stands out.**」（S5）。モデル側の変化とも関係する:「Where you might have said "CRITICAL: You MUST use this tool when...", you can use more normal prompting like "Use this tool when...".」（S8、Opus 4.5 / 4.6 について） |
| 出典 | S5, S8 |
| 機械判定 | **部分的。** 強調語（IMPORTANT / MUST / CRITICAL / 太字）の密度は数えられる。適正な密度は出典に無い |

### 1-17. 構造を持たせる（見出しと箇条書き）

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | 「**Structure**: use markdown headers and bullets to group related instructions. Claude scans structure the same way readers do: organized sections are easier to follow than dense paragraphs.」（S4）。API 側の助言も同じ方向で、「organizing prompts into distinct sections (like `<background_information>`, `<instructions>`, `## Tool guidance`)」を挙げつつ、**モデルが良くなるほど書式の重要度は下がる**とも述べる（S9）。`AGENTS.md` 仕様は書式を一切定めない:「AGENTS.md is just standard Markdown. Use any headings you like」（S1） |
| 出典 | S1, S4, S9 |
| 機械判定 | **できる**（見出しの有無・段落の長さ）。ただし**仕様は何も要求していない**ため、検査は「助言への適合」であって「仕様違反の検出」ではない |

### 1-18. 書いたコマンドは実際に動くこと

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | GitHub Copilot は手順の網羅と検証を求める:「For each of bootstrap, build, test, run, lint, and any other scripted step, document the sequence of steps」「Each command should be validated by running it to ensure that it works correctly.」（S12） |
| 出典 | S12 |
| 機械判定 | **部分的。** コードブロック中のコマンドの抽出と実行は自動化できるが、副作用のあるコマンドは実行できない |

### 1-19. 読み込まれていることを確かめられること

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | 「書いた」と「読まれた」は別である。Claude Code は `/context` の **Memory files** で確認せよと繰り返し書き（S4, S5, S6）、`InstructionsLoaded` hook で「exactly which instruction files are loaded, when they load, and why」を記録できる（S4）。Gemini CLI は `/memory show` が「the full, concatenated content of the current hierarchical memory」を出す（S15）。Codex は結合順とサイズ打ち切りが決まっている（S10） |
| 出典 | S4, S5, S6, S10, S15 |
| 機械判定 | **部分的。** ランタイムごとに確認手段が違い、共通の検査にはならない |

### 1-20. 応答の文体・詳細度を指示書で決めない（Copilot のみ）

| 項目 | 内容 |
| --- | --- |
| 何を言っているか | GitHub Copilot は悪い指示として 3 つを挙げる:「Requests to refer to external resources when formulating a response」「Instructions to answer in a particular style」「Requests to always respond with a certain level of detail」。具体例は「Answer all questions in the style of a friendly colleague.」（S13）。良い指示は「Short, self-contained statements broadly applicable to most requests」（S13） |
| 出典 | S13 |
| 機械判定 | **部分的。** 文体指定の検出はパターンで可能。ただし**この助言は Copilot 固有で、他ランタイムの一次情報には無い**（Anthropic はむしろ書式の明示指示を推奨する、S8） |

### 1-21. 機密情報を書かない

**この観点について、一次情報を確認できなかった。** 調査した公式ドキュメント（S1, S4, S5, S10〜S16）に、
指示ファイルへの機密情報の記載を禁じる明示的な記述は見つからなかった。`AGENTS.md` 仕様は推奨セクションの
1 つに「security considerations」を挙げるが（S1）、これは**書く内容の話**であって記載禁止の話ではない。

**検査に入れるなら、根拠はこの調査の外に置く必要がある**（リポジトリの既存ポリシー、一般的な
シークレット検査の慣行）。

---

### 観点の一覧（見出しだけ）

1. 分量に上限がある
2. 指示の数そのものが遵守率を下げる
3. 指示の位置が効く（先に書いたものが守られやすい）
4. 長い文脈そのものが精度を下げる（分量制限の根拠）
5. 具体的で、検証できる形で書く
6. 否定形より肯定形で書く
7. 矛盾した指示を残さない
8. 決定的に強制できるものは指示書に書かない
9. 常時要らないものは要求時読み込みへ追い出す
10. コードや履歴から導けることを書かない
11. 参照は説明を添えて書く
12. 階層に分け、重複を持たせない
13. ランタイム間で食い違わせない
14. 陳腐化させない（生成したまま放置しない）
15. 増え続けさせない／削除の根拠を残す
16. 強調を安売りしない
17. 構造を持たせる（見出しと箇条書き）
18. 書いたコマンドは実際に動くこと
19. 読み込まれていることを確かめられること
20. 応答の文体・詳細度を指示書で決めない（Copilot のみ）
21. 機密情報を書かない（**一次情報を確認できなかった**）

### 機械判定の可否でまとめる

| 判定 | 観点 |
| --- | --- |
| **できる** | 1 分量 / 2 指示の数 / 13 ランタイム間の一致 / 14 陳腐化 / 15 増加と根拠 / 17 構造 |
| **部分的** | 5 具体性 / 6 肯定形 / 7 矛盾 / 8 lint 重複 / 9 要求時読み込み / 10 導けること / 11 参照 / 12 重複 / 16 強調 / 18 コマンド / 19 読み込み確認 / 20 文体 |
| **できない** | 3 位置（順序の妥当性） / 4 長文脈の劣化（根拠であって検査項目ではない） |

---

## 2. 出典の表

**一次情報を優先した。** 個人ブログは S22 の 1 件のみで、一次情報（S20）へ到達するための経路として使った。

| # | 発行元 | 題 | URL | 参照日 | ここから取った主張 |
| --- | --- | --- | --- | --- | --- |
| S1 | Agentic AI Foundation（Linux Foundation） | AGENTS.md | https://agents.md/ | 2026-09-17 | 「README for agents」。必須項目・frontmatter・特別な構文は無い。ネストしたファイルは近いものが優先。60k 超のプロジェクトが採用、20 以上のツールが対応。推奨セクションの例 |
| S2 | agentsmd（GitHub） | agents.md リポジトリ | https://github.com/agentsmd/agents.md | 2026-09-17 | 仕様サイトの実体。MIT。**統治・版・規範的表現（MUST/SHOULD）の記述は README から確認できなかった** |
| S3 | The Linux Foundation | Linux Foundation Announces the Formation of the Agentic AI Foundation (AAIF)… | https://www.linuxfoundation.org/press/linux-foundation-announces-the-formation-of-the-agentic-ai-foundation | 2026-09-17 | 2025-12-09 に AAIF 設立。MCP・goose・AGENTS.md が創設拠出。AGENTS.md は 2025-08 公開、60,000 超のプロジェクトが採用 |
| S4 | Anthropic | How Claude remembers your project（Claude Code docs / memory） | https://code.claude.com/docs/en/memory | 2026-09-17 | 200 行の目安。4 MiB で読み飛ばし。階層と連結順。`@path` import（最大 4 段）。`.claude/rules/` と `paths:`。`AGENTS.md` は読まない（import か symlink）。矛盾の扱い。HTML コメントは文脈に載らない。auto memory は MEMORY.md の先頭 200 行 / 25KB |
| S5 | Anthropic | Best practices for Claude Code | https://code.claude.com/docs/en/best-practices | 2026-09-17 | 「keep it short and human-readable」。含める／除く表。「Would removing this cause Claude to make mistakes?」。「Bloated CLAUDE.md files cause Claude to ignore your actual instructions!」。強調は 1 行だけ。hook は決定的、CLAUDE.md は助言 |
| S6 | Anthropic | Set up Claude Code in a monorepo or large codebase | https://code.claude.com/docs/en/large-codebases | 2026-09-17 | 根＋サブディレクトリの 2 段構成。PR でレビュー、モデル更新後に見直し、Stop hook で更新提案。`claudeMdExcludes` |
| S7 | Anthropic | Explore the context window | https://code.claude.com/docs/en/context-window | 2026-09-17 | 起動時に載るものの内訳（システムプロンプト・auto memory・Skill 一覧・MCP・CLAUDE.md）。**トークン数は「illustrative」と明記された例示値** |
| S8 | Anthropic | Prompting best practices（Claude platform docs） | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices | 2026-09-17 | Golden rule。順序が要るときは番号付き。「Tell Claude what to do instead of what not to do」。「CRITICAL: You MUST」を弱める助言（Opus 4.5 / 4.6）。Opus 5 では自己検証の指示を**削除**せよ |
| S9 | Anthropic | Effective context engineering for AI agents | https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents | 2026-09-17（記事は 2025-09-29） | 「right altitude」。「find the smallest possible set of high-signal tokens」。セクション分け（XML / Markdown 見出し）。網羅ではなく正典的な例を少数 |
| S10 | OpenAI | AGENTS.md（Codex docs） | https://learn.chatgpt.com/docs/agent-configuration/agents-md | 2026-09-17 | 探索順（`~/.codex/AGENTS.override.md` → `~/.codex/AGENTS.md` → git 根から下へ）。「Files closer to your current directory override earlier guidance because they appear later in the combined prompt.」`project_doc_max_bytes` 既定 32 KiB で打ち切り |
| S11 | Cursor | Rules | https://cursor.com/docs/context/rules | 2026-09-17 | `.cursor/rules` の `.mdc`（`.md` は無視）。4 種の適用方式。`alwaysApply` / `description` / `globs`。「Keep rules under 500 lines」。`AGENTS.md` は簡易な代替。**このページに `.cursorrules` の節と、Agent Skills への廃止告知は無かった** |
| S12 | GitHub | Adding repository custom instructions for GitHub Copilot | https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions | 2026-09-17 | `.github/copilot-instructions.md`、`.github/instructions/*.instructions.md`、`AGENTS.md`（任意の場所）/ 根の `CLAUDE.md` `GEMINI.md`。「Instructions must be no longer than 2 pages」「Instructions must not be task specific」。各コマンドを実行して検証せよ。優先順位（個人 > リポジトリ > 組織） |
| S13 | GitHub | About customizing Copilot responses | https://docs.github.com/en/copilot/concepts/response-customization | 2026-09-17 | 指示の種類と対応ツール。優先順位（個人 → パス別 → リポジトリ全体 → エージェント → 組織）。避けるべき 3 つ（外部資料の参照、文体指定、詳細度の一律指定）。良い指示は「short, self-contained statements」。非決定性の但し書き |
| S14 | GitHub | Copilot coding agent now supports AGENTS.md custom instructions（Changelog） | https://github.blog/changelog/2025-08-28-copilot-coding-agent-now-supports-agents-md-custom-instructions/ | 2026-09-17（記事は 2025-08-28） | `AGENTS.md` 対応の追加日。ネスト可。既存形式（copilot-instructions.md / instructions.md / CLAUDE.md / GEMINI.md）と併存 |
| S15 | Google | Provide context with GEMINI.md files（gemini-cli docs） | https://raw.githubusercontent.com/google-gemini/gemini-cli/main/docs/cli/gemini-md.md | 2026-09-17 | 3 段の読み込み（`~/.gemini/GEMINI.md` → ワークスペースと親 → ツールが触れた時点での just-in-time）。`context.fileName` で `["AGENTS.md", "CONTEXT.md", "GEMINI.md"]` を指定可。`@file.md` import。`/memory show` `/memory reload` |
| S16 | Kiro（AWS） | Steering | https://kiro.dev/docs/steering/ | 2026-09-17 | `.kiro/steering/`。既定 3 ファイル（product.md / tech.md / structure.md）。inclusion モード（always / fileMatch / manual / auto）。「Keep Files Focused — One domain per file」。**`AGENTS.md` は inclusion モードを持たず常に読まれる** |
| S17 | Chroma | Context Rot: How Increasing Input Tokens Impacts LLM Performance | https://www.trychroma.com/research/context-rot | 2026-09-17（記事は 2025-07-14） | 18 モデルで入力長に対する劣化。約 113k トークンの入力は約 300 トークンの絞った入力より精度が落ちる。distractor は 1 つでも効く。**論理的な流れを保った haystack より、シャッフルしたほうが精度が高い** |
| S18 | Liu ほか（TACL） | Lost in the Middle: How Language Models Use Long Contexts | https://arxiv.org/abs/2307.03172 | 2026-09-17（投稿は 2023-07-06） | 位置による U 字。先頭と末尾が最も使われ、中央が最も落ちる |
| S19 | Jaroslawicz ほか | How Many Instructions Can LLMs Follow at Once?（IFScale） | https://arxiv.org/abs/2507.11538 | 2026-09-17（投稿は 2025-07-15） | 500 指示で最高でも 68%。20 モデル・7 提供者。劣化は threshold / linear / exponential の 3 型、閾値は N ≈ 150–200。先に置いた指示への偏り |
| S20 | dos Santos ほか | Configuration Smells in AGENTS.md Files: Common Mistakes in Configuring Coding Agents | https://arxiv.org/abs/2606.15828 （本文: https://arxiv.org/html/2606.15828v5 ） | 2026-09-17（v1 2026-06-14、v5 2026-07-30） | 6 つの smell と出現率: Lint Leakage 62% / Context Bloat 42% / Skill Leakage 35% / Conflicting Instructions 28% / Init Fossilization 24% / Blind References 16%。対象は star 上位 100（AGENTS.md 39 / CLAUDE.md 61、2026-01 抽出）。検出は行数・LLM・コミット数の併用で、LLM 検出の精度は 57–93%、著者が手で検証 |
| S21 | Chakrabarti（South Park Commons） | Why Does CLAUDE.md Keep Growing? Catastrophic Remembering in Agentic Coding | https://www.alphaxiv.org/abs/2608.11095 | 2026-09-17（投稿は 2026-08-11） | 1,867 リポジトリ・247,694 件の指示の寿命。+226%、+4.9/commit。古い指示ほど消されない。全面書き直し後も増加が再開。prompt comments で肥大 −99.3%、遵守 +23.1%。**プレプリント（査読なし）** |
| S22 | Addy Osmani（個人 Substack） | Audit your Agent files | https://addyo.substack.com/p/audit-your-agent-files | 2026-09-17（記事は 2026-08-27） | S20 への到達経路。実務側の失敗の分類（陳腐化・重複・矛盾・過剰指定）と `/doctor` の使い方。**二次情報として、一次情報の補いにのみ使った** |
| S23 | google-gemini/gemini-cli（GitHub Issue） | Add AGENTS.md to the context filename list by default #12345 | https://github.com/google-gemini/gemini-cli/issues/12345 | 2026-09-17 | Gemini CLI の既定は `GEMINI.md` のままで、`AGENTS.md` を既定へ入れる要望が未決であること（検索結果からの確認。**Issue 本文は直接取得していない**） |

**出典の件数: 23 件**（うち一次情報 22 件、個人ブログ 1 件）。

---

## 3. 移り変わりの証拠

**「あるべき姿は動く」ことを、変わる前と後の対で示す。** ここに挙げたのは、**一次情報で前後が確かめられた
もの**だけである。

| # | 何が | 変わる前 | 変わった後 | 根拠 |
| --- | --- | --- | --- | --- |
| A | `AGENTS.md` の統治 | 2025-08 に OpenAI から公開された 1 社発の形式 | 2025-12-09 に Linux Foundation 傘下の Agentic AI Foundation（AAIF）へ移管。Platinum member に AWS・Anthropic・Google・Microsoft・OpenAI ほか | S3 |
| B | Copilot が読む指示ファイル | `.github/copilot-instructions.md` と `.github/instructions/**.instructions.md` | 2025-08-28 に `AGENTS.md`（ネスト可）・`CLAUDE.md`・`GEMINI.md` を追加。2026-06-18 に code review 側へも拡大 | S14（および検索で確認した 2026-06-18 の changelog） |
| C | 強調語の使い方（Anthropic） | ツールの発火漏れを防ぐため「CRITICAL: You MUST use this tool when...」のように強く書いていた | Opus 4.5 / 4.6 では**過剰発火**するため「dial back any aggressive language」「use more normal prompting like "Use this tool when..."」 | S8 |
| D | 自己検証の指示（Anthropic） | 「Before you finish, verify your answer against [test criteria]」を付けるとよい | Opus 5 は指示なしで自己検証するため、旧モデル向けに書いた検証指示は**書き換えるのではなく削除**せよ（over-verification を招く） | S8 |
| E | 分量の助言の粒度（Claude Code） | best practices は数値を持たず「keep it short and human-readable」 | memory の解説は数値を持つ:「target under 200 lines per CLAUDE.md file」。4 MiB 超は読み飛ばし | S4, S5（**両ページは現在も併存しており、粒度が違う。どちらが後に書かれたかは確認できていない**） |
| F | 指示の置き場所（Claude Code） | 単一の `CLAUDE.md`（＋ `@path` import） | `.claude/rules/` に分割し、`paths:` frontmatter で**一致するファイルを読んだときだけ**載せる。常時要らないものは Skill へ。`claudeMdExcludes` で他チームのファイルを除外。`/doctor` が削減を提案（v2.1.206 以降） | S4, S5, S6 |
| G | Cursor の指示ファイル | `.cursorrules`（リポジトリ根の単一ファイル） | `.cursor/rules/` の `.mdc`（frontmatter 必須。`.md` は無視される）＋ `AGENTS.md`。**現行ドキュメントには `.cursorrules` の節が無い** | S11（廃止の版・時期は一次情報を確認できなかった） |
| H | 仕様の変化が版番号として現れること | — | Claude Code のドキュメントは「Before v2.1.211…」「Before v2.1.207…」「Before v2.1.217…」「requires v2.1.214 or later」のような但し書きを多数持つ。**指示ファイルの扱いが数か月単位で動いている証拠そのものである** | S4 |

**確認できなかった移り変わり。**

- **IFScale の 2026 年更新。** 検索結果には「2026 年には強いモデルが数千の指示まで高精度を保つ」とする
  記述があったが、出所は二次情報（scorable.ai）で、**一次情報を確認できなかった**。S19 の 2025-07 時点の
  値（500 指示で 68%）のみを本文に置いた。
- **Cursor が rules を Agent Skills へ廃止したか。** 検索結果に言及があったが、**現行の公式ドキュメント
  （S11）に廃止告知は無かった**。本文には書いていない。
- **Claude Code の memory ドキュメントの過去版。** web.archive.org を取得できず、200 行の目安が
  いつ入ったかは確かめられなかった（「4. 取得できなかったページ」参照）。

---

## 4. ランタイムごとの違い

### 4-1. 読むファイルと置き場所

| ランタイム | 既定で読むファイル | 階層 | 出典 |
| --- | --- | --- | --- |
| Claude Code | `CLAUDE.md` / `.claude/CLAUDE.md` / `CLAUDE.local.md`、`.claude/rules/*.md`。**`AGENTS.md` は読まない** | 管理ポリシー → ユーザー（`~/.claude/`） → プロジェクト（根から作業ディレクトリへ）。サブディレクトリは**そこのファイルを読んだ時点**で載る | S4 |
| OpenAI Codex | `~/.codex/AGENTS.override.md` → `~/.codex/AGENTS.md` → git 根から下へ各階層の `AGENTS.override.md` → `AGENTS.md` → 代替名 | 連結。**後に来るほど強い**。合計 32 KiB（`project_doc_max_bytes`）で打ち切り | S10 |
| Gemini CLI | 既定 `GEMINI.md`。`context.fileName` で `AGENTS.md` 等を指定可（既定には入っていない） | グローバル → ワークスペースと親 → ツールが触れた時点の just-in-time | S15, S23 |
| Cursor | `.cursor/rules/*.mdc`（**`.md` は無視**）、`AGENTS.md`（簡易な代替、ネスト可） | ネストした `AGENTS.md` は近いほうが優先 | S11 |
| GitHub Copilot | `.github/copilot-instructions.md`、`.github/instructions/*.instructions.md`、`AGENTS.md`（**リポジトリ内のどこでも**）、根の `CLAUDE.md` / `GEMINI.md` | 個人 → パス別 → リポジトリ全体 → エージェント（AGENTS.md 等） → 組織。パス別とリポジトリ全体は**両方使われる** | S12, S13 |
| Kiro | `.kiro/steering/*.md`（既定 3 ファイル）、`AGENTS.md` | inclusion モードで制御。**`AGENTS.md` はモードを持たず常に読まれる** | S16 |

### 4-2. 「常時読む／条件付きで読む」の仕組みが違う

| ランタイム | 条件付き読み込みの手段 |
| --- | --- |
| Claude Code | `.claude/rules/` の `paths:`（glob。**読んだときに発火し、作成時ではない**）、サブディレクトリの `CLAUDE.md`、Skill（`description` で選ばれる） |
| Cursor | `alwaysApply` / `description`（エージェントが判断）/ `globs` / 手動 @ 指定の 4 種 |
| Kiro | `always` / `fileMatch` / `manual`（`#名前` で参照）/ `auto` |
| GitHub Copilot | `.instructions.md` の `applyTo` |
| Codex | **無い**（ディレクトリ階層による絞り込みのみ） |
| Gemini CLI | **明示的な条件指定は無い**が、ツールが触れたディレクトリの文脈ファイルを just-in-time で読む |

**Codex は条件付き読み込みを持たない。** 4 ランタイムへ同じ指示書を配る設計では、**最も制約が強いのは
Codex**（32 KiB の打ち切り、条件分岐なし）である。

### 4-3. 参照（import）の扱い

| ランタイム | 構文 | 挙動 |
| --- | --- | --- |
| Claude Code | `@path/to/file` | **起動時に展開して文脈へ載る**（最大 4 段）。コードスパン / フェンス内は展開しない（`` `@README` `` は文字列）。**import しても文脈量は減らない**:「Splitting into @path imports helps organization but doesn't reduce context, since imported files load at launch.」（S4）。作業ディレクトリ外を指す import はプロジェクト側では承認ダイアログが出る |
| Gemini CLI | `@file.md` | 相対・絶対パスに対応。大きな `GEMINI.md` の分割手段（S15） |
| Cursor | `.mdc` から他ファイル参照 | 具体例の参照を推奨、丸写しは非推奨（S11） |
| GitHub Copilot | — | **外部資料を参照させる指示自体を非推奨**（S13） |

**これは NDF の `CLAUDE.md` に既にある判断と一致する。** 本リポジトリの `CLAUDE.md` は
「この参照に `@` を付けない。付けると内容そのものが毎回コンテキストへ載り」と書いており、S4 の
「imported files still load and enter the context window at launch」と同じことを言っている。

### 4-4. 同じ観点でも助言が割れる点

| 観点 | 割れ方 |
| --- | --- |
| 分量 | Claude Code 200 行 / Cursor 500 行 / Copilot 2 ページ / Codex 32 KiB。**共通の数値は無い** |
| 外部参照 | Claude Code・Gemini CLI は import を用意する。Copilot は「外部資料を参照させるな」と言う（S4, S13, S15） |
| 文体指定 | Copilot は「文体を指示するな」と言う（S13）。Anthropic は出力書式の明示指示を推奨する（S8）。**正反対である** |
| 書式の要求 | `AGENTS.md` 仕様は何も要求しない（S1）。Cursor の `.mdc` は frontmatter が**必須**（無いと無視、S11） |
| 1 ファイルの粒度 | Kiro は「One domain per file」（S16）。`AGENTS.md` 仕様は 1 枚を基本に、パッケージごとに追加（S1） |

---

## 5. 調べ直す手がかり

次に調べ直す人が「いまどうなっているか」を最短で知るための、**更新され続ける URL** を用途別に並べる。

### 仕様と統治

| 見るもの | URL | 何が分かるか |
| --- | --- | --- |
| AGENTS.md 仕様 | https://agents.md/ | 形式の定義、採用ツール数、採用プロジェクト数。**必須項目が増えたかどうかはここで分かる** |
| AGENTS.md の実体 | https://github.com/agentsmd/agents.md | 変更履歴。仕様本体の差分はここでしか追えない |
| AAIF | https://www.linuxfoundation.org/projects/ | 統治の変更、参加企業 |

### ランタイム側の正本

| ランタイム | URL | 見るところ |
| --- | --- | --- |
| Claude Code | https://code.claude.com/docs/en/memory | 分量の目安、階層、`.claude/rules/`、import、`AGENTS.md` の扱い。**版番号付きの但し書き**が変更の履歴になっている |
| Claude Code | https://code.claude.com/docs/en/best-practices | 含める／除くの表、`/doctor` |
| Claude Code | https://code.claude.com/docs/en/large-codebases | 階層化と、指示書を維持する運用（PR レビュー・モデル更新後の見直し） |
| Claude Code | https://github.com/anthropics/claude-code/blob/main/CHANGELOG.md | 指示ファイル周りの挙動変更 |
| Anthropic（API） | https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/claude-prompting-best-practices | **モデル別の節**。強調語・検証指示・冗長性の助言が版ごとに変わる |
| OpenAI Codex | https://learn.chatgpt.com/docs/agent-configuration/agents-md | 探索順、`project_doc_max_bytes` |
| Cursor | https://cursor.com/docs/context/rules | 適用方式、行数の目安、`AGENTS.md` の位置づけ |
| GitHub Copilot | https://docs.github.com/en/copilot/concepts/response-customization | 対応ファイルと優先順位。**ツールごとの対応表がここにある** |
| GitHub Copilot | https://github.blog/changelog/label/copilot/ | 対応ファイルの追加は必ずここに出る |
| Gemini CLI | https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/gemini-md.md | 既定のファイル名、`context.fileName`、読み込みの段 |
| Kiro | https://kiro.dev/docs/steering/ | inclusion モード、`AGENTS.md` の扱い |

### 研究

| 見るもの | URL | 何が分かるか |
| --- | --- | --- |
| 設定の smell（**検査の観点の直接の根拠**） | https://arxiv.org/abs/2606.15828 | 版が更新される（2026-09-17 時点で v5）。**smell の定義と出現率が変われば検査項目も変える** |
| 指示の肥大 | https://www.alphaxiv.org/abs/2608.11095 | プレプリント。査読版が出たら差し替える |
| 指示密度と遵守率 | https://arxiv.org/abs/2507.11538 | IFScale。**モデル世代ごとに再測されるので、閾値 150–200 は動く** |
| 長文脈の劣化 | https://www.trychroma.com/research/context-rot | Chroma が再測したら更新される |
| arXiv の新着 | https://arxiv.org/list/cs.SE/recent | `AGENTS.md` / coding agent configuration の新しい実証 |

### 自分のリポジトリで測る

| 何を | どうやって |
| --- | --- |
| 実際に読み込まれたファイル | Claude Code: `/context` の **Memory files**、`InstructionsLoaded` hook。Gemini CLI: `/memory show` |
| 指示書の削れる箇所 | Claude Code: `/doctor`（v2.1.206 以降、チェックイン済み `CLAUDE.md` の削減提案） |
| 増え方 | `git log --follow --numstat -- AGENTS.md CLAUDE.md` で行数の時系列（S21 の測り方と同じ） |
| 陳腐化 | 指示ファイルのコミット数（S20 は「1」を Init Fossilization の指標にした） |

---

## 6. 取得できなかったページ

| # | URL | 何を取ろうとしたか | 結果 |
| --- | --- | --- | --- |
| 1 | http://web.archive.org/web/20250601000000*/docs.anthropic.com/en/docs/claude-code/memory | Claude Code の memory ドキュメントの過去版（200 行の目安がいつ入ったか） | **取得できなかった。** 「Claude Code is unable to fetch from web.archive.org」。移り変わり E をこれで裏づけられなかった |
| 2 | https://arxiv.org/pdf/2606.15828 | Configuration Smells 論文の本文 | **PDF として読めなかった**（バイナリのまま返った）。https://arxiv.org/html/2606.15828v5 で代替し、必要な内容は取得できた |

**取得できなかったページ: 2 件**（うち 1 件は代替経路で内容を取得済み）。

**取得できたが、探していた記述が無かったページ**（黙って落とさないために記録する）:

- https://cursor.com/changelog — 2026-08〜09 の項目までしか到達できず、`AGENTS.md` 対応・
  `.cursorrules` 廃止の項目は見つからなかった。**古い項目へ到達する手段がこのページに無い**
- https://github.com/agentsmd/agents.md — README に統治・版・規範的表現（MUST/SHOULD）の記述は無かった。
  リポジトリ内の `Technical_Charter.pdf` は未取得
- https://github.com/google-gemini/gemini-cli/issues/12345 — 検索結果の要約から状況を把握したのみで、
  **Issue 本文は直接取得していない**
