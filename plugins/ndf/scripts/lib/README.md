# プラグインのライブラリ

`plugins/ndf/scripts/lib/` は、**どの Skill にも属さない部品**を置く。プラグイン
ルート直下にあるため、配る Skill を絞る配布先でも残る。

## 用語

| 語 | この文書での意味 |
| --- | --- |
| プラグインルート | 配布した先で `scripts/` と `skills/` が並ぶディレクトリ |
| 配布の基準 | `plugins/ndf/manifests/*-skills.txt`。配る Skill の名前だけを持つ |
| 収束ループ | `cross-review` / `cross-refactoring` が回す「起動 → 監視 → 判定」の繰り返し |

## 置いてあるもの

| ファイル | 役割 | 読む側 |
| --- | --- | --- |
| [worktree-common.sh](worktree-common.sh) | worktree の判定のエントリポイント。位置の解決・パスの判定と正規化を持ち、下の 7 本を順に source する（1 本でも読めなければ 1 を返す）。読む側はこのファイルだけを source する | `worktree` / hook |
| [worktree-declaration.sh](worktree-declaration.sh) | 宣言ファイルと個人の宣言の読み取り（`wt_declaration_get` ほか） | `worktree-common.sh` |
| [worktree-branch.sh](worktree-branch.sh) | worktree の一覧・追従先・ブランチの判定（`wt_current_branch` ほか） | 同上 |
| [worktree-shell-lex.sh](worktree-shell-lex.sh) | 書き込み先の推定に使うシェルコマンドの字句解析 | 同上 |
| [worktree-write-target.sh](worktree-write-target.sh) | シェルコマンドとパッチ本文からの書き込み先の推定のエントリポイントと前処理 | 同上 |
| [worktree-write-target-scan.sh](worktree-write-target-scan.sh) | 書き込み先の推定の走査の本体と、書き込み先を出す関数 | 同上 |
| [worktree-write-target-track.sh](worktree-write-target-track.sh) | 走査が使う現在地と複合構文の入れ子の追跡 | 同上 |
| [worktree-registry.sh](worktree-registry.sh) | テスト環境の採番と台帳・排他・スロット | 同上 |
| [projects-common.sh](projects-common.sh) | GitHub Projects のボードへの記録 | `development-workflow` |
| [lock-common.sh](lock-common.sh) | 排他の取得と解放（#293） | 上の 2 つと `development-workflow` |
| [monitor.py](monitor.py) | 別プロセスの多軸監視。対象と命名規則を引数で受ける | 収束ループの 2 つ / `external-ai.py` |
| [monitor_patterns.py](monitor_patterns.py) | 監視の照合の表（利用上限・致命・警告・無害・CLI の上限・codex の終わりの印）。標準ライブラリだけを読む | `monitor.py` / `supervise_lib/claude.py`（`USAGE_LIMIT_FATAL`） |
| [monitor_scan.py](monitor_scan.py) | 監視のログの末尾の読み取りと、照合の表による致命・警告・利用上限の判定 | `monitor.py` |
| [monitor_proc.py](monitor_proc.py) | 監視する CLI の pid ファイル・生死とゾンビの判定・cmdline の照合・停止（プロセスグループごと）。標準ライブラリだけを読む | `monitor.py` |
| [monitor_types.py](monitor_types.py) | 監視の既定値（上限の表の別名）・一時ディレクトリ・設定と状態の型 | `monitor.py` / `monitor_outcome.py` |
| [monitor_loop.py](monitor_loop.py) | 担当 1 者の監視ループ（上限・無進捗・早期のエラー・プロセスの終了から結末を決める） | `monitor.py` |
| [limits.py](limits.py) | 監視の上限（工程ごと）・無進捗の許容（担当ごと）・CLI の上限（監視の上限 + 120 秒）の表。既定値はここだけが持つ（#598 / #537）。CLI の上限の上書きは `resolve_cli_timeout` と `cli-timeout --override N [--no-floor]` の 1 つで決める（既定では導いた値より短くできない） | 同上 |
| [monitor_outcome.py](monitor_outcome.py) | 監視の結果の理由の語彙（9 語）と起動し直しの可否、結果ファイル・監視の記録の読み書き、起動 1 回の結末を 1 つの値として読む `read_launch_outcome`、担当 1 者の結末を書く `_record_outcome` | 同上 |
| [bg-wait.sh](bg-wait.sh) | 600 秒を超える待ちを、背景の起動（`run`）と 540 秒以内に区切った待ち（`wait`）に分ける。終了コードは rc ファイルに残る | 収束ループの 2 つ（Codex / Kiro / agy で `drive.py` を待つ）/ `cross-review` の手順の監視 |
| [launch-cli.sh](launch-cli.sh) | claude / codex / agy / kiro をランタイム名で分岐して背景起動する | 同上 |
| （`skills/external-ai/scripts/external-ai.py`） | 上の 2 つと `auth.py` / `limits.py` を束ね、外部 CLI 1 回の起動・上限つきの待ち・回収（結果ファイル → stdout → stderr）を 1 本で行う。結果は `step_result` の形 | `external-ai` / `corder` / supervisor の worker |
| [_tmpdir.sh](_tmpdir.sh) | 一時ディレクトリの解決。環境変数名とディレクトリ名を引数で受ける | 同上 |
| [statefile.py](statefile.py) | 状態ファイルの読み書きと KEY=VALUE 出力、保存の後の差し込み口、再開で渡した引数の反映。`now`・`die`・`info`・`write_json_atomic` は `clock`・`proc`・`jsonio` の再エクスポート | 同上 |
| [auth.py](auth.py) | 参加する CLI の認証の確認。止めずに結果だけを返す形を持つ（#727） | 同上 |
| [run_metrics.py](run_metrics.py) | 実行の要約をworktree の外へ書き、集計して出す（`aggregate`、#662） | 同上 |
| [assignment.py](assignment.py) | ホスト判定、母集合の確定、使える者の解決、席の埋め方と席の名前、担当の輪番（#727） | 同上 |
| [models.py](models.py) | `--model` の解析、フラグ生成、実測値の突き合わせ | `cross-refactoring` / `external-ai.py` / `metrics.py` |
| [metrics.py](metrics.py) | 担当ごとの指標算出と報告の整形 | テストだけ（収束ループの 2 つはまだ読まない） |
| [post_queue.py](post_queue.py) | 上限のときに投稿を積む待ち行列と、上限の見分け | `cross-review`（`review_lib/` / `rotate-pr.sh`） |
| [result_posts.py](result_posts.py) | 結果ファイル（指摘ファイル・修正の戻り値）を投稿へ組み立て、待ち行列から送る | `cross-review`（`review_lib/` / `drive.py`） / `fix-steps.py` |
| [git-credential.sh](git-credential.sh) | credential helper が応答しない環境で git を通す退避の値 | `cross-refactoring`（`refactor_lib/publish.py`） |
| [closing-issues.sh](closing-issues.sh) | Pull Request の本文から、閉じる語が指す issue を取り出す | `progress-tracking`（ミッションを閉じる） / `merged`（OPEN の一覧） / `development-workflow` の hook |
| [refresh.py](refresh.py) | 観点の出典の取得・指紋の比較・一覧の提示・待ちの扱い（#554）。**提示するだけで書き換えない** | `instructions-check.py` |
| [transcript_agents.py](transcript_agents.py) | 会話の記録を conductor / supervisor / worker の層の単位で読む（#550）。上限の中断の一覧（`interrupted`）と解除の待ち（`wait-reset`）も持つ（#657）。**読むだけで送信の経路を持たない** | `skill-stats` / `development-workflow` |
| [step_result.py](step_result.py) | 手順のスクリプトの結果 JSON の形・検証（`validate_result`）・出力と終了（`emit`）・承認資料（`approval_present`）と、git / gh を呼ぶ小関数（`StepError`・`run`・`git`・`git_root` は `proc` の再エクスポート） | `merged-steps.py` / `plan-to-spec-steps.py` / `release-steps.py` / `release-verification-steps.py` / `mission-close.py` / `drive_pause.py` |
| [gh_parts.py](gh_parts.py) | PR / issue の取得と本文の節の差し替え。`pr-info`（メタ・本文・差分の統計・checks を名前ごとの最新の実行へ畳んだもの・未解決のスレッド。GraphQL が上限なら REST へ退避。差分とログはファイルへ書く）、`unresolved-threads`、`body-section`（節の取得・置換・末尾への 1 行の追記。節の終わりに `<!-- ndf:section-end -->` を置き、後ろへ足した行を節に含めない）、`review-post`（自分の PR なら REQUEST_CHANGES を COMMENT へ下げる）。結果は `step_result` の形。エントリポイントと再エクスポートだけを持ち、部品は下の `gh_*` の 8 本にある | `cross-review`（`review_lib/` の未解決のスレッドと checks） |
| [gh_call.py](gh_call.py) | GitHub の呼び出しの最下層。GitHub を呼ぶのはここだけで、テストは `RUNNER` を差し替える。REST の 1 回の要求（githubkit が import できれば githubkit、できなければ `gh api`）と、ETag 付きの読み直し（`rest_cached`。変わっていなければ 304 で上限に数えられない） | `gh_*` |
| [gh_quota.py](gh_quota.py) | 上限の見分け・枠（GraphQL と REST）の残り・枠の代替（`with_fallback`）・回復の時刻までの待ち（`wait_for_reset`）・変化が無い間に伸ばす待ちの間隔（`PollInterval`・`poll_until`） | `gh_*` |
| [gh_fields.py](gh_fields.py) | REST の応答を GraphQL の `--json` の形へ変える対応表 | `gh_rest` |
| [gh_rest.py](gh_rest.py) | PR と課題の単発の読み書き（読み取り・一覧・作成・編集・コメント・マージ）。REST で行い、REST が上限なら `gh pr` / `gh issue` で代わる。値は `Attempt`（`value`・`error`・`via`） | `gh_parts` |
| [gh_graphql.py](gh_graphql.py) | 入れ子の読み取り（未解決のスレッド）と GraphQL の 1 回の要求（`graphql`） | `gh_parts` / `gh_pr_info` |
| [gh_checks.py](gh_checks.py) | チェックジョブの読み取りと、名前ごとの最新の実行への畳み方・失敗のログの保存 | `gh_parts` / `gh_pr_info` |
| [gh_pr_info.py](gh_pr_info.py) | `pr-info` の組み立て | `gh_parts` |
| [gh_sections.py](gh_sections.py) | 本文の節の取得・置換・末尾への 1 行の追記（GitHub を呼ばない） | `gh_parts` |
| [clock.py](clock.py) | 今の時刻・ISO の書き出し（`local` / `utc` / `naive` / `z-ms` を引数で選ぶ）と読み取り（`Z` を含む）・秒の差。標準ライブラリだけ | `statefile.py` / `cross-review`（`review_lib/`） / `cross-refactoring`（`refactor_lib/clock.py`）（C1〜C7 で各スクリプト） |
| [jsonio.py](jsonio.py) | JSON の読み（無い・壊れた・形が違うときの扱いを引数で選ぶ）と原子的な書き込み。標準ライブラリだけ | 同上 |
| [proc.py](proc.py) | 子プロセスと git の起動（失敗は `StepError(msg, code)`）・`die`・`info` | `step_result.py` / `statefile.py` / `repo.py` / `cross-review`（`review_lib/`） / `cross-refactoring`（`refactor_lib/paths.py`・`commands/setup.py`） |
| [repo.py](repo.py) | メインディレクトリ・`owner/repo`・slug・宣言のベースブランチ（git だけで決める） | `cross-review`（`review_lib/`） / `cross-refactoring`（`drive.py`・`commands/setup.py`）（C1〜C7 で各スクリプト） |
| [loop_drive.py](loop_drive.py) | 収束ループの drive の部品（`call`・`parse_vars`・`review_status`） | 収束ループの 2 つの `drive.py` |
| [deps.py](deps.py) | 外部パッケージを使うエントリポイントが最初に呼ぶ `require("<グループ>")`。import できなければ uv の環境（宣言と版の固定はプラグインルートの `pyproject.toml` と `uv.lock`）で起動し直し、uv が無ければ版を固定して入れる。入れられなければ終了コード 3。hook とラッパーのバージョンディレクトリは使わない | 外部パッケージを使うエントリポイント |
| [md.py](md.py) | Markdown の構造の読み取り（囲み・見出し・節・表・地の文・リンクとアンカー）。markdown-it-py を呼ぶのはここだけ。書き込みは読み取った行の区間で呼び出し側が行う | `cross-refactoring`（`refactor_lib/vocabulary.py`）。ほかは D1〜D8 が呼び出し側を置き換える |
| [mdtable.py](mdtable.py) | Markdown の表の組み立て（列の幅を揃えない行・セルの縦棒のエスケープ・数の列の右寄せ）。tabulate を呼ぶのはここだけ | `cross-refactoring`（`refactor_lib/plan.py`・`commands/report.py`）。ほかは D1〜D8 が呼び出し側を置き換える |
| [schema.py](schema.py) | JSON と設定の形の検証（`Shape` と `load_shape`）。pydantic の誤りを日本語の 1 行（`ShapeError`）へ直し、語彙に無い値を下げる読みは `lenient_choice` | L1 の時点では無し（D1〜D8 が呼び出し側を置き換える） |
| [procs.py](procs.py) | プロセスの生死（ゾンビは死）・親子・木の停止（グループの先頭ならグループへ）・メモリと cgroup。psutil を呼び、`/proc/` を読むのはここだけ | 同上 |
| [locks.py](locks.py) | ファイルロック（`<対象>.lock` で取る排他・待たない取得・排他つきの 1 行の追記）。filelock を呼び、`fcntl` を使うのはここだけ | 同上 |
| [shparse.py](shparse.py) | シェルの構文木（tree-sitter-bash）。試行 T2 で見つけた構文木の癖 5 つを直して渡す。hook の経路で使うため `deps.require()` を呼ばない | 同上 |
| [versions.py](versions.py) | 版数（`X.Y.Z`・`-dev.N`・`-rc.N`）の比較と次の版、bump-my-version の `replace`。semver と bump-my-version を呼ぶのはここだけ | 同上 |
| [pathmatch.py](pathmatch.py) | パスのパターン照合（git の wildmatch を根からのパス全体に当てる。`*` は `/` をまたがない）。pathspec を呼ぶのはここだけ | 同上 |
| [textparse.py](textparse.py) | unified diff の足した行の番号と、コードのコメントを空白へ置き換える処理。unidiff と pygments を呼ぶのはここだけ | 同上 |
| [yamlio.py](yamlio.py) | frontmatter と YAML の往復の読み書き（値は YAML の型。引用符とコメントを保つ）。ruamel.yaml を呼ぶのはここだけ | 同上 |
| [waits.py](waits.py) | 条件が揃うまでの問い合わせ（変化が無い間は間隔を伸ばす）とやり直し。tenacity を呼ぶのはここだけ。GitHub の上限の待ちは `gh_quota` | 同上 |
| [notify.py](notify.py) | HTTP の 1 回の要求・Slack の Web API・`.env` の読み取り。httpx・slack_sdk・python-dotenv を呼び、`urllib.request` を使うのはここだけ | 同上 |
| [wait_notice.py](wait_notice.py) | Slack の待ち通知の判定（応答の本文から回答待ち・承認待ち・待ちでない）・フックの事象の訳し・復帰先・関連 URL・本文の組み立て。入出力を持たない | `scripts/wait-notify.py` |
| [drive_pause.py](drive_pause.py) | 収束ループの駆動が止まるときの結果の形（pause の 1 行 JSON）と終了コードの表（0 完了 / 20 fix / 21 sweep / 22 newtext / 23 cross-review / 1 中断） | 収束ループの 2 つの `drive.py` |

## 手順のスクリプトの結果

Skill から呼ぶ手順のスクリプト（`scripts/*-steps.py`）は、最後に 1 行の JSON を標準出力へ出し、
終了コードで終える。読み手（LLM）は `status` だけで次の手を決める。形は
[step_result.py](step_result.py) の `validate_result` が確かめる。

例（`merged-steps.py cleanup 812` で、未マージのコミットを持つブランチが残ったとき。終了コード 10）:

```json
{"tool": "merged", "status": "gate", "summary": "作業ツリー 1 件を外し、ブランチ 0 件を消した（残した 0 件・止まった 1 件）",
 "items": [{"kind": "branch", "name": "feature/x", "result": "stopped", "reason": "git branch -d が拒否: ...", "sha": "..."}],
 "metrics": {"removed_worktrees": 1, "deleted_branches": 0, "stopped": 1},
 "presentation_path": "/tmp/ndf/merged-812-gate.md", "next": "同意を得たら git branch -D feature/x"}
```

| 項目 | 必須 | 形 | 意味 |
| --- | --- | --- | --- |
| `tool` | はい | 文字列 | 呼んだ Skill の名前（`merged` / `plan-to-spec` / `release` / `release-verification`）。`mission-close.py` は `mission-close` |
| `status` | はい | `ok` / `gate` / `stopped` | 3 値に固定する |
| `summary` | はい | 文字列 | 1 行の要約。報告へそのまま写せる |
| `items` | はい | オブジェクトの配列 | 対象ごとの結果。`kind` / `name` / `result` を持つ。`result` の語彙はスクリプトごとに決めてよい |
| `metrics` | はい | オブジェクト | 件数・版数・コミットなどの値 |
| `presentation_path` | いいえ | 文字列 | 承認ゲートで利用者へ示す承認資料（`approval_present` が書く） |
| `next` | いいえ | 文字列 | 次に打つコマンドか、LLM が書く説明文 |

`gate` のときは `presentation_path` か `next` を必ず添える。表に無い項目は置かない。

| 終了コード | status | 意味 |
| --- | --- | --- |
| 0 | `ok` | 手順が終わった |
| 1 | `stopped` | チェックで違反があった・手順が失敗した |
| 2 | `stopped` | 読めない・呼び出しの誤り。「一致」「0 件」と読まない |
| 3 | `stopped` | 前提が無い（宣言・認証・対象のファイル）、または各スクリプトが定めた正常な否定の結果（立たない・変更なし・飛ばしてよい。例 `check-trigger.py eval`・`refactor.py assess`）。読めないときは 2 で返し、3 と混ぜない |
| 10〜19 | `gate` | 承認ゲート。人の同意が要る |
| 20〜29 | `gate` | LLM の判断待ち |

承認資料は `development-workflow/references/approval-request.md` の 2 層（対象を開くもの・判断に
使うもの）に、同意を求めること・戻し方を足した形で書く。置き場所は `NDF_PRESENTATION_DIR`
（既定は一時ディレクトリの `ndf/`）。戻し方の無い承認資料は書かない。

## 置いてよいもの・いけないもの

**2 つ以上の読み手が使う部品だけを置く。** Skill 固有の処理を混ぜない。

固有として**置かないもの**の例:

- `cross-review`: 振動検知、Pull Request のローテーション、レビュー観点、修正の指示
- `cross-refactoring`: 提案のマージ、改善項目の管理、適用結果の検証、見送り処理

## プラグインルート直下に置く理由

**配る Skill を絞る配布先がある。** 配布の基準に無い Skill が配布した先に残るかを、
実在する欠落（`official-skills-autoloader`）で測った。

| ランタイム | 基準に無い Skill | 隣の Skill から相対で届くか |
| --- | --- | --- |
| Claude Code | 残る | 届く |
| Codex | 残る | 届く |
| Kiro CLI | `.kiro/skills/` からは消える | 届く（`..` がメインディレクトリの実体へ抜ける） |
| agy | 消える | **届かない** |

Skill の下にライブラリを置くと、その Skill を配らない配布先で読み込みが失敗する。
プラグインルートの `scripts/` は配布の基準の対象ではないため、4 ランタイムすべてへ
届く（基準は Skill の名前だけを持ち、`scripts` という語を含まない）。

ライブラリのための Skill を新設する形は採らない。利用者が呼ばない Skill でも初期一覧へ
載り、発動の候補に混ざる（`disable-model-invocation` を付けても名前は残る）。

## ここを指す書き方

**物理的な解決を求める側と避ける側が、シェルと Python で入れ替わる。** Kiro CLI が
`.kiro/skills/<名前>` を symlink にするためである。シェルの `cd` は `..` を字句で
畳んで symlink の手前へ戻り、Python の `parents[]` は `.resolve()` を通さないと
`.kiro` で止まる。

| 読み込む側の位置 | 言語 | 書き方 |
| --- | --- | --- |
| `<プラグインルート>/skills/<名前>/scripts/` | シェル | `"$DIR/../../../scripts/lib/<名前>"`（文字列のまま渡す） |
| 同上 | Python | `pathlib.Path(__file__).resolve().parents[3] / "scripts" / "lib"` |
| `<プラグインルート>/skills/<名前>/scripts/lib/` | シェル | `"$DIR/../../../../scripts/lib/<名前>"` |

`DIR` は `$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)` で求めた読み込む側の
ディレクトリである。**`cd` で登った結果を `pwd` で取らない。**

## 収束ループの 2 つの Skill から見た現状

`cross-review` は既存の呼び出しパスを保つため、`scripts/` 側に 2 つのシムを残す。

| 部品 | `cross-refactoring` | `cross-review` |
| --- | --- | --- |
| `drive_pause.py` / `step_result.py` | 使う（`drive.py`） | 使う（`drive.py`） |
| `monitor.py` | ライブラリを直接使う（`drive.py` / `refactor_lib/timeline.py`） | `scripts/monitor.py` がシムとしてライブラリを読む |
| `_tmpdir.sh` | 使わない（一時ディレクトリは `refactor_lib/paths.py` が決める） | `scripts/_tmpdir.sh` が固有の名前をまとめてライブラリを読む |
| `launch-cli.sh` | `scripts/launch-cli.sh` が委譲する | `launch-reviewer.sh` / `critique.sh` が使う（`launch-codex.sh` / `launch-agy.sh` は `launch-reviewer.sh` へ委譲する） |
| `limits.py` | 使う | `critique.sh` が使う（監視の上限は `monitor.py` がライブラリの表から引く） |
| `assignment.py` / `auth.py` / `statefile.py` / `run_metrics.py` / `monitor_outcome.py` | 使う | 使う（`review_lib/`） |
| `models.py` | 使う | 未移行 |
| `metrics.py` | 未移行 | 未移行 |
| `post_queue.py` / `result_posts.py` | 未移行（リファクタリング計画のコメントは `refactor_lib/plan.py` が `gh` で書く） | 使う（`review_lib/` / `rotate-pr.sh` / `drive.py`） |
| `git-credential.sh` | 使う（`refactor_lib/publish.py`） | 使わない |
| `bg-wait.sh` | Codex / Kiro / agy で `drive.py` を待つ（SKILL.md の「実行」） | Codex / Kiro / agy で `drive.py` を待つ（SKILL.md の「実行」）。手順の監視の待ち（`docs/01-state-and-review.md`） |
