# #3672 の影響評価

設計文書 [issue-3672-design.md](issue-3672-design.md) から分けた文書である。決定の番号は
[issue-3672-design-decisions.md](issue-3672-design-decisions.md) を、削除の一覧の番号（`D<数>`）は
[issue-3672-design-removal.md](issue-3672-design-removal.md) を指す。

この設計が入ると、ブランチ戦略を読む部品だけでなく、開発版のチャネルを前提にした工程と運用も変わる。
とくに ai-plugins と代表の種類 `web-trunk`・`docs`・`plugin` は開発版を持たないため、開発版を条件にしていた機能が断られる。
この文書は、何が影響を受け、どこで扱うかを 4 つの観点で並べる。

| 扱い | 意味 |
| --- | --- |
| この設計で直す | この課題の実装の PR で直す。直し方は決定の記録か、表の「中身」の列が指すセクションにある |
| 別の Issue へ回す | この課題の範囲の外。行き先の Issue を書く |
| 人へ残す | 設計文書の「人へ残す判断」に、当たるレッドラインつきで載せた |
| — | 影響が無い |

## `development-workflow` の工程表の行

工程表は `plugins/ndf/skills/development-workflow/SKILL.md` の「モードごとに起動する Skill」の表である。

| 工程 | 影響 | 中身 | 扱い |
| --- | --- | --- | --- |
| 要求と受け入れ条件 | 無し | 宣言を読まない | — |
| 作業場所の用意 | 有り | worktree の起点を `branches.base` と `sprint.start` から決める。メインディレクトリの追従先（`follow_branch`）も `branches.base` を読む（決定 1・D13・D31） | この設計で直す |
| 設計・詳細設計 | 無し | 宣言を読まない | — |
| 素材の収集と出典の確定 | 無し | 同上 | — |
| ドキュメント再構成・ドキュメントレビュー | 有り | 設計 PR の宛先が `branches.base` になる（`trunk` では本番ブランチ）。決めるのは PR の作成の部品 | この設計で直す（D32-18） |
| 計画 | 有り | リリースのプランの組み方を `release_strategy` から決める。「開発版」のステージは、`channels` に `dev` が無い宣言では承認前の試用のステージに代わる（決定 15） | この設計で直す |
| 実装 | 無し | 宣言を読まない | — |
| リファクタリング・コードレビュー | 有り | レビューの単位（`reviews`）と比べる基点をセクションから読む | この設計で直す（D32-3・D32-4） |
| 完了判定 | 無し | 宣言を読まない | — |
| Pull Request | 有り | 宛先を `branches.base` から決める。`--base develop` の付け忘れの検査は、宛先のブランチのセクションを読む形へ替える | この設計で直す（D27・D32-18） |
| 確定仕様化 | 有り | 旧いキーを説明する確定仕様 20 本を、この課題の確定仕様が置き換える | この設計で直す（D34） |
| 後片付け | 有り | 同期の PR（本番ブランチをベースブランチへ戻す）は `web-develop`・`mobile` だけで作る。`trunk` では作らない | この設計で直す（D32-16） |
| デプロイ | 有り | 承認の対象・受け方・マージの主体と方式をセクションから読む（決定 7・8）。開発版のデプロイは `channels` に `dev` がある宣言だけで行う。承認資料の「開発版のデプロイで確かめたこと」は、開発版の無いリポジトリで書けない（p6-lunify:3-3） | この設計で直す（決定 17・D32-22） |
| 体裁レビュー | 無し | 宣言を読まない | — |
| リリース後テスト | 有り | `verify-install --ref` は宣言のベースブランチか本番チャネルしか受けず、ai-plugins の確認は develop から入れている（p6-lunify:3-3） | この設計で直す（決定 17） |
| 振り返り | 有り（文書だけ） | Skill の本文に旧いキーの説明がある | この設計で直す（D32-23） |
| 棚卸し | 無し | 宣言を読まない | — |

## 既存の機能と設定

| 機能・設定 | 影響 | 中身 | 扱い |
| --- | --- | --- | --- |
| `pace: auto`・`fast`（p6-lunify:3-1） | 有り | `supervise.py new sprint --pace` は、開発版のチャネル（`lib/delivery.py` の `dev_channel`。起点と本番ブランチが違うか、手動反映の本番系の形）と導入の確認（`.ndf/pace.json` の `<セクション>.verify`）を使ってよい条件にする（`references/pace.md` の「使ってよい条件」）。開発版の無い ai-plugins と、種類 `web-trunk`・`docs`・`plugin` の既定は断られる。「実践投入」も開発版のデプロイを前提に定義されている | この設計で直す（決定 15。読み手は D12・D16、文書は D32-10・D33-12） |
| 開発ループ（`--loop`、p6-lunify:3-1） | 有り | ループの 1 回は `auto` で組むため、`auto` と同じく断られる。`--dev <開発版>` と、パイプラインの「開発版」のステージも開発版を前提にする | この設計で直す（決定 15。文書は D32-9） |
| 並列（オーガナイザーとレーン） | 有り | 組んだ版のスナップショットと今のセクションの比べ（決定 9）、選んで集める取り込み方の分岐（D32-11）が旧いキーを読む | この設計で直す |
| 版の要るまとめの一時レーン | 有り | 作り直しは #3679 が持つ | 別の Issue へ回す（#3679。範囲の外） |
| relay | 有り（小） | `sprint-state.py init` の `--plan 開発版=`・`--dev` と、`/goal` のテンプレートの `{dev}` は開発版を前提にする。開発版の無い宣言では `--dev` を省き、`{dev}` は空になる | この設計で直す（`--dev` を省いた起動を受けることをテストで確かめる。文書は relay.md） |
| リリース前の直しを使う手段（p6-lunify:3-2） | 有り | 今は開発版（`-dev.N`）でリリース前の直しをレーンへすぐ入れている（例: lguard の 10.27.73-dev.2）。開発版をやめると、この手段が無くなる | この設計で直す（決定 16。ai-plugins の値に書く）。版を出す単位の規則の変更は人へ残す（P1・C7） |
| 承認資料とリリース後テストの導入の確認（p6-lunify:3-3） | 有り | 承認資料の「開発版のデプロイで確かめたこと」（`references/approval-request.md` の「リリース（`release`）」）と、`release-verification-steps.py verify-install --ref` の develop からの導入は、開発版を前提にする | この設計で直す（決定 17） |
| 実験版の経路 | 無し | 決定 16 で開発版の代わりに使わないと決めたため、置き場・経路・台帳を変えない | — |
| ai-plugins の `.ndf/pace.json` | 有り | `fast.verify`・`auto.verify` が `verify-install --ref develop` を打つ | この設計で直す（決定 15・17。移行の PR に載せる。[issue-3672-design-ai-plugins.md](issue-3672-design-ai-plugins.md) の「PR への割り当て」） |
| MVV 判定（`mvv-gate.py`） | 有り（小） | ai-plugins のプロジェクト MVV の Value 5 が「開発版のチャネル・導入の確認・自動テストがそれを守る」と書く。改めると承認済みのプロジェクト MVV のハッシュが変わり、`pace` の条件の「プロジェクト MVV が改訂されていない」で断られるため、改訂の承認が要る | 人へ残す（C7） |
| hook と CI の宛先の検査 | 有り | `scripts/check-pr-base.sh`・`.github/workflows/pr-base-guard.yml` が develop 宛てを前提にする | この設計で直す（D27。移行の順序の手順 4） |

## 方針の文書

| 文書とセクション | 影響 | 中身 | 扱い |
| --- | --- | --- | --- |
| `AGENTS.md` の「Git運用ルール」（p6-lunify:3-4） | 有り | PR の宛先 develop・`.ndf/worktree.json` の `base_branch`・`--base develop` の付け忘れの検査 | この設計で直す（D36。C7 で人が見る） |
| `AGENTS.md` の「版とリリースの方針」（p6-lunify:3-4） | 有り | チャネルの表（正式版 main・開発版 develop）・開発版の接尾辞・「版を上げるのはスプリント単位でマージが終わった後」 | この設計で直す（D36・決定 16。版を出す単位の変更は人へ残す。P1・C7） |
| `AGENTS.md` の「安定版と実験版」（p6-lunify:3-4） | 有り | リリースの待ちを避ける経路として実験版だけを挙げる。安定版の直しは正式版の PATCH で届けること（決定 16）を足す | この設計で直す（D36） |
| `AGENTS.md` の「ベストプラクティス」 | 有り | 「`develop` 宛の PR を通じてマージ」「`main` / `develop` に直接コミット」の 2 行 | この設計で直す（D36） |
| `CLAUDE.md` の「版ごとの判断の記録」（p6-lunify:3-4） | 無し | 今は develop・開発版を書いていない。版の判断の記録の置き場は変えない。書き足されないよう D36 の検査で照らす | この設計で直す（D36） |
| `CONTRIBUTING.md` | 有り | `--base develop`・`origin/develop` から worktree を切る手順・develop の取り込み | この設計で直す（D36） |
| `plugins/ndf/README.md`（37〜39 行目） | 有り | 開発版を `#develop` から試す案内 | この設計で直す（D36） |
| `docs/versioning-and-distribution.md` | 有り | チャネル・開発版のデプロイ・開発版を試す手順 | この設計で直す（D36） |
| 確定仕様（`docs/specifications/`） | 有り | 旧いキーの説明 20 本 | この設計で直す（D34） |
| 用語集 | 有り | 旧いキーの語（D35）と、「実践投入」の意味（開発版のデプロイを含む） | この設計で直す（D35・設計文書の「用語」） |
| ai-plugins のプロジェクト MVV（`.ndf/mvv.md`）の Value 5 | 有り | 上の「既存の機能と設定」の MVV 判定の行 | 人へ残す（C7） |

## 利用者の環境

| 利用者 | 影響 | 中身 | 扱い |
| --- | --- | --- | --- |
| Claude Code で正式版を入れた利用者 | 無し | 登録は既定ブランチ main のままで、版数が変わったときだけ届く（STD4） | — |
| 開発版を入れた利用者（登録に `#develop` を付けた） | 有り | 移行の後は develop が更新されず、直しが届かなくなる | この設計で直す（D36 で `docs/versioning-and-distribution.md` に登録を main へ戻す手順を書く）。開発版チャネルをやめること自体は人へ残す（既存の P1 の行） |
| Codex の利用者 | 有り | main が進むたびに入れ直す（STD10）。`fast` の実践投入（決定 15）も、スプリントの PR のマージのたびに届く | 人へ残す（既存の Codex の取得元の行。P1） |
| 新しく入れる利用者・入れ直す利用者 | 有り | main の先端を前の版数の表示のまま受け取る | ai-plugins の値の「残る危険」に書いた（既存） |
| NDF を使うほかのリポジトリの持ち主 | 有り | 旧いキーがあると読む部品が止まり、移行のコマンドを打つ | この設計で直す（決定 10・I4） |
| ai-plugins の開発者の手元（develop から切った worktree・ブランチ） | 有り | 移行の後は起点が main になる | この設計で直す（移行の順序の手順 1 の前に develop へ入れ終える。既存） |

## この評価で足す触るファイル

設計文書の「設計の結果」の表に無く、この評価で直すと決めたファイルである。計画は「設計の結果」の表とあわせて実装の PR へ割る。

| ファイル | 直すこと | 決定 |
| --- | --- | --- |
| `plugins/ndf/scripts/release-verification-steps.py` | `--ref` に、本番ブランチ宛ての開いた版上げの PR の head を受ける | 決定 17 |
| `plugins/ndf/skills/release-verification/SKILL.md` | `verify-install --ref` の受ける ref | 決定 17 |
| `plugins/ndf/skills/development-workflow/references/approval-request.md` | リリースの「判断に使うもの」を「承認前の試用で確かめたこと」へ | 決定 17 |
| `plugins/ndf/scripts/tests/test_supervise_pace.py`・`test_supervise_pace_auto.py`・`test_supervise_pace_manual.py`・`test_supervise_pace_verify.py`・`test_step_scripts.py` | 決定 15 の表の 5 行と、`verify-install` の受ける ref のテスト | 決定 15・17 |
| `.ndf/pace.json`（ai-plugins） | `verify` の `--ref develop` を `--ref {ref}` へ | 決定 15・17 |
| `CLAUDE.md`・`CONTRIBUTING.md`・`plugins/ndf/README.md`（ai-plugins） | D36 で足した行 | — |
