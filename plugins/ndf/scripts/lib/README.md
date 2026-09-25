# プラグイン共通層

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
| [worktree-common.sh](worktree-common.sh) | 作業ツリーの判定・台帳・書き込み先の推定 | `worktree` / hook |
| [projects-common.sh](projects-common.sh) | GitHub Projects の盤面への記録 | `development-workflow` |
| [lock-common.sh](lock-common.sh) | 排他の取得と解放（#293） | 上の 2 つと `development-workflow` |
| [monitor.py](monitor.py) | 別プロセスの多軸監視。対象と命名規則を引数で受ける | 収束ループの 2 つ / `external-ai.py` |
| [limits.py](limits.py) | 監視の上限（工程ごと）・無進捗の許容（担当ごと）・CLI の上限（監視の上限 + 120 秒）の表。既定値はここだけが持つ（#598 / #537） | 同上 |
| [monitor_outcome.py](monitor_outcome.py) | 監視の結果の理由の語彙（9 語）と起動し直しの可否、結果ファイル・監視の記録の読み書き（#662）、起動 1 回の結末を 1 つの値として読む `read_launch_outcome`（#729） | 同上 |
| [bg-wait.sh](bg-wait.sh) | 600 秒を超える待ちを、背景の起動（`run`）と 540 秒以内に区切った待ち（`wait`）に分ける。終了コードは rc ファイルに残る | 収束ループの 2 つ（Codex / Kiro / agy で `drive.py` を待つ）/ `cross-review` の手順の監視 |
| [launch-cli.sh](launch-cli.sh) | claude / codex / agy / kiro をランタイム名で分岐して背景起動する | 同上 |
| （`skills/external-ai/scripts/external-ai.py`） | 上の 2 つと `auth.py` / `limits.py` を束ね、外部 CLI 1 回の起動・上限つきの待ち・回収（結果ファイル → stdout → stderr）を 1 本で行う。結果は `step_result` の形 | `external-ai` / `corder` / supervisor の worker |
| [_tmpdir.sh](_tmpdir.sh) | 一時ディレクトリの解決。環境変数名とディレクトリ名を引数で受ける | 同上 |
| [statefile.py](statefile.py) | 状態ファイルの読み書きと KEY=VALUE 出力、保存の後の差し込み口、再開で渡した引数の反映（#727） | 同上 |
| [auth.py](auth.py) | 参加する CLI の認証の確認。止めずに結果だけを返す形を持つ（#727） | 同上 |
| [run_metrics.py](run_metrics.py) | 実行の要約を作業ツリーの外へ書き、束ねて出す（`aggregate`、#662） | 同上 |
| [assignment.py](assignment.py) | ホスト判定、母集合の確定、使える者の解決、席の埋め方と席の名前、担当の輪番（#727） | 同上 |
| [models.py](models.py) | `--model` の解析、フラグ生成、実測値の突き合わせ | `cross-refactoring` / `external-ai.py` / `metrics.py` |
| [metrics.py](metrics.py) | 担当ごとの指標算出と報告の整形 | テストだけ（収束ループの 2 つはまだ読まない） |
| [post_queue.py](post_queue.py) | 上限のときに投稿を積む待ち行列と、上限の見分け | `cross-review`（`state.py` / `rotate-pr.sh`） |
| [result_posts.py](result_posts.py) | 結果ファイル（指摘の控え・修正の戻り値）を投稿へ組み立て、待ち行列から送る | `cross-review`（`state.py` / `drive.py`） / `fix-steps.py` |
| [git-credential.sh](git-credential.sh) | credential helper が応答しない環境で git を通す退避の値 | `cross-refactoring`（`refactor_lib/gitfacts.py`） |
| [closing-issues.sh](closing-issues.sh) | Pull Request の本文から、閉じる語が指す issue を取り出す | `progress-tracking`（ミッションを閉じる） / `merged`（OPEN の一覧） / `development-workflow` の hook |
| [refresh.py](refresh.py) | 観点の出典の取得・指紋の比較・一覧の提示・待ちの扱い（#554）。**提示するだけで書き換えない** | `instructions-check.py` |
| [transcript_agents.py](transcript_agents.py) | 会話の記録を conductor / supervisor / worker の層の単位で読む（#550）。上限の中断の一覧（`interrupted`）と解除の待ち（`wait-reset`）も持つ（#657）。**読むだけで送信の経路を持たない** | `skill-stats` / `development-workflow` |
| [step_result.py](step_result.py) | 手順のスクリプトの結果 JSON の形・検証（`validate_result`）・出力と終了（`emit`）・承認の提示物（`approval_present`）と、git / gh を呼ぶ小関数 | `merged-steps.py` / `plan-to-spec-steps.py` / `release-steps.py` / `release-verification-steps.py` / `mission-close.py` / `drive_pause.py` |
| [gh_parts.py](gh_parts.py) | PR / issue の取得と本文の節の差し替え。`pr-info`（メタ・本文・差分の統計・checks を名前ごとの最新の実行へ畳んだもの・未解決のスレッド。GraphQL が上限なら REST へ退避。差分とログはファイルへ書く）、`unresolved-threads`、`body-section`（節の取得・置換・末尾への 1 行の追記。節の終わりに `<!-- ndf:section-end -->` を置き、後ろへ足した行を節に含めない）、`review-post`（自分の PR なら REQUEST_CHANGES を COMMENT へ下げる）。結果は `step_result` の形。GitHub を呼ぶのは `RUNNER` 1 か所 | `cross-review`（`state.py` の未解決のスレッドと checks） |
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
| `presentation_path` | いいえ | 文字列 | 承認の関門で利用者へ示す提示物（`approval_present` が書く） |
| `next` | いいえ | 文字列 | 次に打つコマンドか、LLM が書く説明文 |

`gate` のときは `presentation_path` か `next` を必ず添える。表に無い項目は置かない。

| 終了コード | status | 意味 |
| --- | --- | --- |
| 0 | `ok` | 手順が終わった |
| 1 | `stopped` | 検査で違反があった・手順が失敗した |
| 2 | `stopped` | 読めない・呼び出しの誤り。「一致」「0 件」と読まない |
| 3 | `stopped` | 前提が無い（宣言・認証・対象のファイル）、または各スクリプトが定めた正常な否定の結果（立たない・変更なし・飛ばしてよい。例 `check-trigger.py eval`・`refactor.py assess`）。読めないときは 2 で返し、3 と混ぜない |
| 10〜19 | `gate` | 関門。人の同意が要る |
| 20〜29 | `gate` | LLM の判断待ち |

提示物は `development-workflow/references/approval-request.md` の 2 層（対象を開くもの・判断に
使うもの）に、同意を求めること・戻し方を足した形で書く。置き場所は `NDF_PRESENTATION_DIR`
（既定は一時ディレクトリの `ndf/`）。戻し方の無い提示物は書かない。

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
| Kiro CLI | `.kiro/skills/` からは消える | 届く（`..` が主ディレクトリの実体へ抜ける） |
| agy | 消える | **届かない** |

Skill の下に共通層を置くと、その Skill を配らない配布先で読み込みが失敗する。
プラグインルートの `scripts/` は配布の基準の対象ではないため、4 ランタイムすべてへ
届く（基準は Skill の名前だけを持ち、`scripts` という語を含まない）。

共通層のための Skill を新設する形は採らない。利用者が呼ぶものではないのに初期一覧へ
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
| `monitor.py` | 共通層を直接使う（`drive.py` / `refactor_lib/timeline.py`） | `scripts/monitor.py` がシムとして共通層を読む |
| `_tmpdir.sh` | 使わない（一時ディレクトリは `refactor_lib/paths.py` が決める） | `scripts/_tmpdir.sh` が固有の名前を束ねて共通層を読む |
| `launch-cli.sh` | `scripts/launch-cli.sh` が委譲する | `launch-reviewer.sh` / `critique.sh` が使う（`launch-codex.sh` / `launch-agy.sh` は `launch-reviewer.sh` へ委譲する） |
| `limits.py` | 使う | `critique.sh` が使う（監視の上限は `monitor.py` が共通層の表から引く） |
| `assignment.py` / `auth.py` / `statefile.py` / `run_metrics.py` / `monitor_outcome.py` | 使う | 使う（`state.py`） |
| `models.py` | 使う | 未移行 |
| `metrics.py` | 未移行 | 未移行 |
| `post_queue.py` / `result_posts.py` | 未移行（改修計画のコメントは `refactor_lib/plan.py` が `gh` で書く） | 使う（`state.py` / `rotate-pr.sh` / `drive.py`） |
| `git-credential.sh` | 使う（`refactor_lib/gitfacts.py`） | 使わない |
| `bg-wait.sh` | Codex / Kiro / agy で `drive.py` を待つ（SKILL.md の「実行」） | Codex / Kiro / agy で `drive.py` を待つ（SKILL.md の「実行」）。手順の監視の待ち（`docs/01-state-and-review.md`） |
