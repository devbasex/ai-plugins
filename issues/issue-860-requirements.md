# #860: pr-review: 差分の収集・既存コメントの取得・投稿・外部 AI への委譲をスクリプトにする

正は課題の本文（#860）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

親: #845　使う基盤: #848 #849 #852

## 依頼（原文）

> ## 何をするか
>
> `pr-review/SKILL.md`（21,804B）のうち次をスクリプトへ移す。
>
> | 今の手順 | 移す先 |
> | --- | --- |
> | `--branch` の起点の解決（約 2.0KB）と diff / stat / log | #848 の入口（未作成）+ `scripts/lib/gh_parts.py pr-info` |
> | 既存コメントの取得（重複防止） | `gh_parts.py pr-info --with threads`（未解決のスレッド）。`--with` が受けるのは `checks` / `threads` / `diff` / `logs` で、解決済みのスレッドと一般のコメントは持たない |
> | payload の組み立てと POST、自分の PR の格下げ | `gh_parts.py review-post` |
> | 外部 AI への委譲のプロンプト組み立て（約 5.8KB） | `skills/external-ai/scripts/external-ai.py run` |
> | event（APPROVE / REQUEST_CHANGES / COMMENT）の決定 | 指摘の重要度の集合から機械的に決める |
>
> ## LLM に残る判断
>
> - レビューそのもの（第 1 段・第 2 段。段 3）
>
> ## development-workflow との接点
>
> - `pr-review` は工程 Skill の一覧（切れ目 2）に載っている。Skill 名は変えない
>
> ## 受け入れ条件
>
> - [ ] 上の 5 つがスクリプトの呼び出しになり、SKILL.md が約 10KB 以下になる
> - [ ] 外部 AI の待ちに上限がある（#345）
>
> ## 関連
>
> #602、#357、#345、#656

conductor からの指示（2026-10-07）: 人へ問わずに進め、決められない点は前提か未決として本文へ書く。

## 例: `/ndf:pr-review 1836 codex` を、変えた後の形で通すと

今（ndf 10.17.66、`pr-review/SKILL.md` は 21,907 B）:

1. LLM が SKILL.md の「プロンプト組み立て」を読み、`gh pr view` と `gh pr diff` を打ち、観点の節を写してプロンプトのファイルを書く
2. LLM が `external-ai` の手順を読んで codex を起動し、完了を待つ
3. codex が payload を組み、自分の PR かを確かめて event を格下げし、`gh api -X POST .../reviews` で投稿し、結果サマリを書く
4. LLM が結果サマリを `jq` で読み、失敗なら payload から投稿し直す

変えた後:

1. LLM がスクリプトを 1 回呼ぶ。スクリプトが PR のメタ・差分・未解決のスレッドを集め、観点の正本からプロンプトを組み、`external-ai.py run codex --phase review` で起動する（上限つきの待ち）
2. codex はレビューして**指摘ファイルだけ**を書く。投稿しない
3. スクリプトが指摘の重要度から event を決め、`gh_parts.py review-post` で投稿する（自分の PR なら `COMMENT` へ下げ、本来の判定は結果に残す）
4. LLM は結果（review URL・event・件数）を読んで報告する

LLM 自身がレビューするとき（第二引数なし）は、手順 2 を LLM が行い、1 と 3 は同じスクリプトが行う。

## 目的

- **pr-review を通すときに LLM が読む量と打つ手順を減らす。** 判断の要らない手順（ベースブランチの解決・差分の収集・既存の指摘の取得・プロンプトの組み立て・外部 CLI の起動と待ち・event の決定・投稿）をスクリプトへ移し、LLM はレビューそのもの（第 1 段の仕様適合・第 2 段のコード品質）と結果の報告だけを持つ
- **同じ規則の写しを無くす。** ベースブランチの解決は `lib/repo.py`、投稿と格下げは `gh_parts.py review-post`、外部 CLI の起動と待ちは `external-ai.py run` が持ち、SKILL.md に写さない
- **外部 AI の待ちに上限を持たせる**（#345）

## 前提

- 前提 1: #848（ベースブランチの解決の入口 `ndf-branch.sh`）は NOT_PLANNED で閉じており、入口は無い。ベースブランチの解決は既存の `plugins/ndf/scripts/lib/repo.py` の関数を呼ぶ形でこの課題の中で作る。`lib/repo.py` の `base_branch` は宣言のベースブランチが実在するかを確かめないため、実在の確認（取得済みの参照 → `git ls-remote` の参照名の照合）は今の SKILL.md の振る舞いを保って足す
- 前提 2: 重複防止に使う既存の指摘は**未解決のスレッドだけ**（`gh_parts.py pr-info --with threads`）とする。解決済みのスレッドと一般のコメントは見ない。解決済みの指摘をもう一度挙げることは許す（解決済みは直したか見送ったかで、同じ指摘が再び当たるなら再び示す価値がある）
- 前提 3: 外部 AI は投稿しない。指摘ファイル（Reviews API の `comments[]` の形に重要度を持たせたもの）を書き、投稿はスクリプトが `review-post` で行う。外部 AI に `gh api` で投稿させる今の流れは無くなる
- 前提 4: 委譲先として受ける外部 AI は今と同じ `codex` / `agy` の 2 つとする。`external-ai.py run` が受ける `kiro` / `claude` は足さない
- 前提 5: `APPROVE` の投稿は今と同じく、利用者が `/ndf:pr-review` を呼んだことを依頼として扱う。自分の PR では `review-post` が `COMMENT` へ下げる（今の `result_posts.review_posts` の振る舞い）
- 前提 6: 「約 10KB 以下」は `wc -c plugins/ndf/skills/pr-review/SKILL.md` が 10,240 以下であることとする

## 対象範囲

含む:
- `--branch` モードのベースブランチの解決と、差分のファイル一覧・統計・コミット履歴の収集をスクリプトにする
- PR モードの PR のメタ・差分・未解決のスレッドの取得をスクリプトにする（`gh_parts.py pr-info`）
- 指摘ファイルから event を決め、`gh_parts.py review-post` で投稿する手順をスクリプトにする
- 外部 AI への委譲で、プロンプトの組み立て・`external-ai.py run --phase review` での起動と待ち・結果の回収をスクリプトにする
- `pr-review/SKILL.md` から、上の手順の bash・プロンプトの写し・結果サマリの形を除き、スクリプトの呼び出しと LLM の判断の節だけを残す
- `pr-review` の委譲の流れを説明している他の Skill の記述（`external-ai/SKILL.md` の pr-review への言及）を、変えた後の流れに合わせる

含まない:
- 第 1 段・第 2 段の観点そのものの見直し（中身は変えない。置き場を移すだけ）
- `cross-review` のレビューの流れの変更（`cross-review` は `pr-review` を呼ばない）
- 他の 4 Skill（`merged` / `cherry-pick-pr` / `deploy` / `retrospective`）にあるベースブランチの解決の写しの置き換え（#845 の各 Skill の課題で扱う）
- 委譲先の CLI を増やすこと（前提 4）
- Skill 名・引数の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | レビューの対象を決めた（PR 番号 / 直前の PR / `--branch`） | 利用者が `/ndf:pr-review` を呼んだ | 直前の PR が無い・PR を取得できない → 非 0 で止まり、理由を出す | — |
| E2 | ベースブランチを解決した（`--branch` のとき） | E1 で `--branch` が選ばれた | 宣言のベースブランチが origin にもローカルにも無い → 非 0 で止まり、既定ブランチへ落とさない | E1 |
| E3 | 差分と統計と履歴（`--branch`）、または PR のメタと差分（PR モード）を集めた | E1・E2 | 取得できない → 非 0 で止まる | E2（`--branch`） |
| E4 | 未解決のスレッドを集めた（PR モード） | E3 | 取得できない → 非 0 で止まる（重複防止なしで投稿しない） | E3 |
| E5 | 外部 AI へのプロンプトを組んだ（委譲のとき） | E3・E4 | 組めない → 非 0 で止まる | E4 |
| E6 | 外部 AI が指摘ファイルを書いた（委譲のとき） | E5 の後に `external-ai.py run` が起動した | 上限を越えた・ファイルが無い・形が読めない → 非 0 で終わり、投稿しない | E5 |
| E7 | LLM がレビューして指摘ファイルを書いた（委譲しないとき） | E3・E4 | — | E4 |
| E8 | event を決めた | E6 または E7 | 重要度が読めない指摘がある → 非 0 で止まり、投稿しない | E6 / E7 |
| E9 | PR へ投稿した（自分の PR なら `COMMENT` で） | E8 | 位置の拒否 → 指摘を総評へ移して送り直す（`review-post` の今の振る舞い）。それ以外の失敗 → 非 0 で終わり、指摘ファイルを残す | E8、PR モードだけ |
| E10 | 結果を報告した | E9（PR モード）または E8（`--branch`） | — | E9 / E8 |

`--branch` モードは E9 を通らない（投稿しない。今と同じ）。

## 用語

| 用語 | 意味 |
| --- | --- |
| 未解決のスレッド | PR のレビュースレッドのうち、解決済みの印が付いていないもの |
| 本来の判定 | 指摘から決めた event。自分の PR で `COMMENT` へ下げても結果に残す |

## 受け入れ条件

ベースブランチと差分（`--branch`）:

- [ ] `--branch` のとき、スクリプトの 1 回の呼び出しがベースブランチ・変更ファイルの一覧・差分の統計・コミット履歴を出す
- [ ] ベースブランチの解決が次の 5 通りでテストされている: 宣言のベースブランチが取得済みの参照にある / 取得済みの参照に無く `git ls-remote` で見つかる / 宣言のベースブランチがどこにも無い（非 0 で止まり、既定ブランチの差分を出さない）/ 宣言が無く origin の HEAD がある / 宣言も origin の HEAD も無く `main` か `master` がある
- [ ] `git ls-remote` が別のブランチ（`refs/heads/x/refs/heads/<ベースブランチ>`）だけを返すとき、ベースブランチは「無い」と判定される
- [ ] `.ndf/worktree.json` を読むコードは `lib/repo.py` だけにあり、新しいスクリプトと SKILL.md に写しが無い

既存の指摘（PR モード）:

- [ ] PR モードで、未解決のスレッドが `gh_parts.py pr-info --with threads` の結果から LLM（または外部 AI のプロンプト）へ渡る
- [ ] SKILL.md に `gh api "repos/.../pulls/<番号>/comments"` の呼び出しが無い

event の決定:

- [ ] event が指摘ファイルからスクリプトで決まり、次の表のとおりになることがテストで確かめられている

  | 指摘の集合 | event |
  | --- | --- |
  | `critical` か `major` が 1 件以上、または仕様適合（第 1 段）を満たさない指摘が 1 件以上 | `REQUEST_CHANGES` |
  | `minor` と `nit` だけ | `COMMENT` |
  | 0 件 | `APPROVE` |
  | 重要度が `critical` / `major` / `minor` / `nit` のどれでもない指摘がある | 投稿せず非 0 で止まる |

投稿:

- [ ] PR モードの投稿が `gh_parts.py review-post` の 1 回の呼び出しで行われ、SKILL.md に `jq -n` の payload の組み立てと `gh api -X POST .../reviews` が無い
- [ ] 自分の PR では `COMMENT` で投稿され、結果に本来の判定（`REQUEST_CHANGES` など）が残る
- [ ] `--branch` モードは投稿しない（今と同じ）

外部 AI への委譲:

- [ ] `codex` / `agy` を指定すると、スクリプトがプロンプトを組み、`external-ai.py run <CLI> --phase review` で起動する。SKILL.md にプロンプトの雛形・CLI の起動フラグ・結果サマリの形が無い
- [ ] プロンプトの観点（第 1 段・第 2 段・インラインコメントの書式・重要度）は 1 か所の正本から組まれ、SKILL.md と別ファイルに同じ文が 2 つ無い
- [ ] 外部 AI は投稿せず、指摘ファイルを書く。投稿は E8・E9 のスクリプトが行う
- [ ] 外部 AI が上限（`--phase review` の上限）を越えても指摘ファイルを書かないとき、呼び出しは非 0 で終わり、投稿しない。応答の無い偽の CLI と短い上限で確かめるテストがある（#345）

SKILL.md と退行:

- [ ] `wc -c plugins/ndf/skills/pr-review/SKILL.md` が 10,240 以下
- [ ] SKILL.md に第 1 段・第 2 段の観点（LLM が行う判断）が残る
- [ ] Skill 名 `pr-review` と引数（`[PR番号 | --branch] [codex|agy] [--focus AREA]`）が変わらない。4 ランタイムの manifests に `pr-review` が残る
- [ ] `python3 scripts/check-skill-frontmatter.py` と全体テストが通る

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| 性能・拡張性 | SKILL.md の大きさを変更の前後で `wc -c` で測り、PR 本文に載せる（21,907 B → 10,240 B 以下） |
| 運用・保守性 | ベースブランチの解決・投稿と格下げ・外部 CLI の起動と待ちは、それぞれ既存の 1 か所（`lib/repo.py` / `gh_parts.py review-post` / `external-ai.py run`）を呼び、新しい写しを作らない |
| セキュリティ | payload とプロンプトはスクリプトが JSON・ファイルとして組み、シェルの文字列へ値を埋め込まない。外部 AI に `gh api` の投稿を許す必要が無くなる（投稿はスクリプトが行う）。GitHub の認証の扱いは変えない |
| システム環境 | スクリプトは Claude Code / Codex / Kiro / agy のどのホストからも `$SCRIPTS` の解決（`development-workflow` の `references/scripts-lookup.md`）で呼べる。Python の標準ライブラリと既存の `scripts/lib` だけを使う |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | 変わらない（`/ndf:pr-review` の引数と出力先）。外部 AI が書くファイルは結果サマリから指摘ファイルへ変わるが、`pr-review` の内部の受け渡しで、他の Skill は読まない（`cross-review` は自分の結果ファイルを読む） |
| データ | 無し |
| 既存の振る舞い | 外部 AI が自分で投稿していた流れが、スクリプトによる投稿へ変わる。重複防止で見る既存の指摘が、PR の全コメントから未解決のスレッドへ狭まる（前提 2）。位置の拒否のときに総評へ移して送り直す振る舞いが加わる（`review-post` の既存の振る舞い） |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析 | `python3 scripts/check-skill-frontmatter.py`、`claude plugin validate .` |
| 大きさ | `wc -c plugins/ndf/skills/pr-review/SKILL.md` |
| 手動確認 | 配布の後、開発版で `/ndf:pr-review <PR番号>` と `/ndf:pr-review <PR番号> codex` を 1 回ずつ通し、PR 上にインラインコメントと総評が 1 件のレビューで載ることを見る（リリース後テスト） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 共通の部品は `plugins/ndf/scripts/lib/`、Skill 固有の手順は Skill の `scripts/` か `plugins/ndf/scripts/<Skill>-steps.py`（`pr-steps.py` / `merged-steps.py` の前例）。置き場は設計で決める |
| コーディング規約 | `plugins/ndf/skills/AUTHORING.md`、結果は `step_result` の JSON（`tool` / `status` / `summary` / `items` / `metrics`） |
| テスト戦略 | ベースブランチの解決・event の決定・プロンプトの組み立ては単体テスト。外部 CLI と GitHub は偽のコマンドで置き換える。`.md` の文言を照合するテストは書かない（`AGENTS.md`） |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存の部品（`lib/repo.py` / `gh_parts.py` / `external-ai.py`）を呼ぶ。全体テストを通す |
| 確認してから行う | 既存の部品の公開の引数・結果の形を変えること（`cross-review` など他の呼び手に効く） |
| 行わない | 観点の中身の変更、他の 4 Skill のベースブランチの解決の置き換え、委譲先の CLI の追加 |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| スクリプトの置き場と名前（`pr-review/scripts/` か `scripts/pr-review-steps.py` か） | 設計 | 設計の承認まで |
| ベースブランチの実在の確認を `lib/repo.py` に足すか、スクリプト側に置くか | 設計 | 設計の承認まで |
| 仕様適合（第 1 段）を満たさない指摘を指摘ファイルでどう表すか（カテゴリの値か専用の印か） | 設計 | 設計の承認まで |
| 外部 AI のプロンプトに `review_criteria.py reviewer` の節（指摘の基準と MVV）を含めるか | 設計 | 設計の承認まで |
| `review-post` が求める `--round` / `--seat` に単発のレビューで何を渡すか | 設計 | 設計の承認まで |

## 関連

#602、#357、#345、#656
