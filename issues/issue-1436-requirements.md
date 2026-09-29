# #1436: cross-review: Serena MCP が起動時に書き換える .serena/*.yml が「未 push の変更」とされ、start-round が exit 8 で止まる

正は課題の本文（#1436）で、この文書はその写しである。`spec-copy.py write` で作り直す。手で直さない。

## 依頼（原文）

> ## 何が起きたか
>
> volareinc/carmo-cdk の設計 PR #424 の cross-review で、ラウンド 2 の `state.py start-round` が終了コード 8 で止まった（ndf 10.17.40、2026-09-28）。
>
> ```
> ❌ 作業ツリーに未 push の変更が残っています: .serena/project.yml .serena/serena_config.yml
> ```
>
> 原因は、レビュー担当の CLI が起動した Serena MCP が、追跡対象の `.serena/project.yml` / `.serena/serena_config.yml` を新しい形式へ書き換えたこと。利用者の変更ではない。書き換えた内容には `auth_secret` が含まれるため、コミットして解決することもできない。設計の作業ツリー（`.worktrees/design/issue-421`）と `pr` のステップ（`Warning: 2 uncommitted changes`）でも同じ差分が出た。
>
> ## 期待する振る舞い
>
> - `start-round` の検査で、ツールが生成する既知のパス（`.serena/` など）を除外できる設定を持つ。または `init` が作る作業ツリーで `git update-index --skip-worktree` を掛ける
> - 止めるなら、利用者の変更かツールの書き換えかを区別できる案内を出す
>
> ## 回避
>
> 作業ツリーごとに `git update-index --skip-worktree .serena/project.yml .serena/serena_config.yml` を掛け、`/tmp/ndf-worktrees/*/pr*` と `rf*/*` に掛け続ける見張りを背景で動かした。

## 目的

- CLI が起動したツール（Serena MCP など）が worktree の追跡対象のファイルを書き換えても、cross-review・cross-refactoring・`pr` の工程が止まらずに進む
- ツールが書き換えた内容（`auth_secret` を含み得る）が、NDF の経路で作るコミットと push に入らない
- 止めるときは、利用者の変更とツールの書き換えを案内で区別できる

## 調べたこと

止まる・混ざる箇所は 4 つある（develop の a6b7499b で確認）。

| 箇所 | 今の振る舞い | ツールのパスが変わっていると |
| --- | --- | --- |
| cross-review のラウンドの開始（`review_lib/workspace.py` の `_is_synced`） | 追跡対象の変更が 1 件でもあれば終了コード 8 | 止まる（報告の現象） |
| cross-review の fix 担当のコミット | fix 担当が `git add -A` を使い得る（`docs/01-state-and-review.md`） | `auth_secret` を含む差分が Pull Request へ push され得る |
| cross-refactoring の同期の前（`refactor_lib/worktree.py` の `_require_clean_worktree`） | 未コミットの変更があれば中断 | 止まる。止まらなかった場合も、同期コミット（`_commit_sync_changes`）が差分を拾う |
| `pr` の手順（`pr-steps.py`） | `plan` は `git status --short` を未コミットとして数え、`commit` は `git add -A` | 未コミットとして数え、コミットへ入れる |

Serena の起動は NDF と mcp-serena の両方が `SERENA_HOME=.serena` で行う（`supervise_lib/claude.py`、`plugins/mcp/mcp-serena/.mcp.json` / `.codex.mcp.json`）。このリポジトリは `.serena/serena_config.yml` を `.gitignore` に入れているが、carmo-cdk のように追跡しているリポジトリもある。

## 前提

- 前提 1: 既定のツールのパスは `.serena/project.yml` と `.serena/serena_config.yml` の 2 つである。NDF と mcp-serena が自分で起動する Serena の書き先であり、ai-plugins の形ではない（プロジェクト Value 5 の範囲）
- 前提 2: プロジェクトは `.ndf/` の設定でツールのパスを足すことも、既定を外すこともできる。どの設定ファイルのどのキーに置くかは設計が決める
- 前提 3: ツールのパスの変更は、NDF の経路（cross-review・cross-refactoring・`pr`）では検査の対象から外し、コミットにも入れない。捨てるか残すかは設計が決める。ただし残した変更が利用者の開発 worktree の内容を失わせてはならない
- 前提 4: 除外の手段（検査での除外か `git update-index --skip-worktree` か、その組み合わせか）は設計が決める。受け入れ条件は手段に依らない形で書く
- 前提 5: 対象の worktree は、レビュー worktree（`/tmp/ndf-worktrees/*/pr*`・`rf*/*`）と、`pr` の手順を打つ開発 worktree である

## 対象範囲

含む:
- cross-review のラウンドの開始の検査と、ラウンドで push されるコミット
- cross-refactoring の同期の前の検査と同期コミット
- `pr` の手順の `plan` の件数と `commit` の対象
- ツールのパスの既定と、`.ndf/` の設定での足し引き
- 止めるときの案内で、利用者の変更とツールのパスの変更を分けて示すこと

含まない:
- Serena がファイルを書き換えること自体を止める（`SERENA_HOME` の置き場の変更、上流への報告）
- worktree の設定の `guard.allow_paths`（メインディレクトリの編集の案内）との統合。意味が違う（編集してよい場所と、ツールが書き換える場所）
- 利用者が手で動かしている見張り（回避）の撤去
- mcp-serena の `language-servers` が `.gitignore` へ書く行の変更

## ドメインイベント

| # | イベント | 引き金 | 失敗したとき | 順序の前提 |
| --- | --- | --- | --- | --- |
| E1 | レビュー worktree を作った | cross-review / cross-refactoring の `init` | 既存の扱いのまま（止まる） | — |
| E2 | ツールがツールのパスを書き換えた | 担当の CLI が Serena MCP を起動した | —（利用者に見えない） | E1 |
| E3 | fix 担当が修正をコミットして push した | ラウンドの修正の工程 | ツールのパスが Pull Request へ混ざる → 受け入れ条件 4 | E2 と前後し得る |
| E4 | ラウンドの開始で worktree の変更を検査した | `state.py start-round` | 利用者の変更があれば終了コード 8 → 受け入れ条件 1・2 | E3 |
| E5 | worktree を Pull Request の head へ同期した | E4 が同期を要すると判定した | 既存の扱いのまま | E4。同期の後にツールが再び書き換えることがある（E2 へ戻る） |
| E6 | cross-refactoring が同期の前に変更を検査した | 公開の直前 | 利用者の変更があれば中断 → 受け入れ条件 3 | E2 |
| E7 | `pr` の手順が変更を数え、コミットした | `pr-steps.py plan` / `commit` | ツールのパスがコミットへ混ざる → 受け入れ条件 5 | E2 |

## 用語

| 用語 | 意味 |
| --- | --- |
| ツールのパス | CLI が起動したツール（MCP サーバーなど）が、利用者の操作なしに worktree の中で書き換える既知のパス。既定と `.ndf/` の設定で決まる |

## 受け入れ条件

- [ ] 1. 前提: レビュー worktree で、追跡対象の `.serena/project.yml` と `.serena/serena_config.yml` だけが Pull Request の head から変わっている
      操作: `state.py start-round` を打つ
      結果: 終了コード 0 でラウンドが始まり、2 つのパスをツールのパスとして扱ったことが出力に出る
- [ ] 2. 前提: 1 の状態に加えて、ツールのパスでない追跡対象のファイルが 1 つ変わっている
      操作: `state.py start-round` を打つ
      結果: 終了コード 8 で止まり、案内の利用者の変更の一覧にはそのファイルだけが載る。ツールのパスは別の行に分けて載る
- [ ] 3. 前提: cross-refactoring の作業の worktree で、ツールのパスだけが変わっている
      操作: 同期のある公開（`--sync-command` かリファクタリング計画の書き出し）を走らせる
      結果: 中断せずに進み、同期コミットにツールのパスの変更が入らない
- [ ] 4. 前提: cross-review のレビュー worktree で、ツールのパスが変わっている
      操作: fix 担当が `git add -A` でコミットし、ラウンドが push する
      結果: push されたコミットにツールのパスの変更が入らない
- [ ] 5. 前提: 開発 worktree で、ツールのパスと、ツールのパスでないファイル 1 つが変わっている
      操作: `pr-steps.py plan` と `pr-steps.py commit` を打つ
      結果: `plan` の未コミットの件数は 1 で、ツールのパスは別の項目に載る。`commit` のコミットにツールのパスの変更が入らない
- [ ] 6. `.ndf/` の設定でツールのパスを 1 つ足すと、足したパスが 1〜5 と同じに扱われる。設定が無いリポジトリでは既定の 2 つだけが扱われる
- [ ] 7. `.ndf/` の設定で既定のツールのパスを外すと、外したパスは利用者の変更として扱われる（1 では終了コード 8 で止まる）
- [ ] 8. 退行しない: ツールのパスでない追跡対象の変更だけがあるとき、`start-round` は終了コード 8 で止まり、cross-refactoring の同期の前の検査は中断し、`pr-steps.py commit` はその変更をコミットする（いずれも今と同じ）
- [ ] 9. cross-review・cross-refactoring・`pr` の 3 つは、ツールのパスを同じ 1 つの定義から読む。既定を変えると 3 つの振る舞いが同時に変わることをテストで確かめる
- [ ] 10. 開発 worktree でツールのパスに加えた変更の内容は、`pr` の手順の後もファイルに残る（捨てない）

## 非機能の条件

| 大項目 | 条件 |
| --- | --- |
| セキュリティ | ツールのパスの内容（`auth_secret` を含み得る）は、NDF の経路で作るコミット・push・Pull Request の本文・ログのどれにも出ない。案内に出すのはパスだけで、中身を出さない |
| 運用・保守性 | ツールのパスとして扱ったパスは、工程ごとに出力へ 1 行で残り、利用者が後から何を外したかを確かめられる |

## 影響

| 対象 | 影響 |
| --- | --- |
| 公開インタフェース | `.ndf/` の設定に項目が 1 つ増える（任意。無ければ既定）。`pr-steps.py plan` の出力に項目の種類が 1 つ増える。既存の項目と終了コードは変わらない |
| データ | 移行なし。設定が無いリポジトリはそのまま動く |
| 既存の振る舞い | ツールのパスの変更だけでは止まらなくなる。ツールのパスの変更がコミットに入らなくなる |

## 検証手段

| 項目 | 手段 |
| --- | --- |
| テスト | `uv run --frozen --project . --all-extras pytest plugins/ndf/skills/cross-review plugins/ndf/skills/cross-refactoring plugins/ndf/scripts/tests -q -n 4` |
| 全体テスト | `uv run --frozen --project . --all-extras pytest . -q -n 4` |
| 静的解析 | `python3 scripts/check-skill-frontmatter.py`、`claude plugin validate .` |
| 手動確認 | 一時リポジトリで `.serena/project.yml` を追跡させ、書き換えた状態で受け入れ条件 1・2・5 の終了コードと出力を見る（テストの一時リポジトリで自動化できるなら手動は不要） |

## 前提とする取り決め

| 項目 | 参照先 / 決めたこと |
| --- | --- |
| プロジェクト構造 | 共通の定義は `plugins/ndf/scripts/lib/` のような共通のライブラリに置き、Skill ごとに同じ役割の定数を持たない（プロジェクト Value 6、#1142） |
| コーディング規約 | `AGENTS.md` のベストプラクティス。呼ぶ git のコマンド（`status --porcelain`・`update-index --skip-worktree`・`add -A`）の挙動は書く前に実行して確かめる |
| テスト戦略 | 一時 git リポジトリを作るスクリプトのテストで、受け入れ条件 1〜10 の振る舞いを確かめる。`.md` の文言は照合しない |

## 境界

| 区分 | 内容 |
| --- | --- |
| 常に行う | 既存テストの実行、3 つの工程で同じ定義を使うこと |
| 確認してから行う | 既定のツールのパスを 2 つより広げる（`.serena/` 全体など。`language-servers` が意図して書く `project.yml` の扱いが変わる） |
| 行わない | ツールのパスの内容を読んで出力する、Serena の起動の引数を変える、利用者の `.gitignore` を書き換える |

## 未決

| 項目 | 誰が決めるか | 期限 |
| --- | --- | --- |
| U1: 利用者が意図してツールのパスを変えた（`language-servers` が `project.yml` を書いた）ときに、`pr` の手順でそれをコミットへ含める手段を設けるか。設けないなら利用者が手で `git add` する旨を案内に出すか | 設計（`design`） | 設計 PR |
| U2: 除外の手段（検査での除外 / `skip-worktree` / 両方）と、レビュー worktree でツールのパスの変更を捨てるか残すか | 設計（`design`） | 設計 PR |
| U3: 設定を置くファイルとキー（`.ndf/worktree.json` に足すか、新しいファイルか） | 設計（`design`） | 設計 PR |
