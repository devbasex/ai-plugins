# 用語集: NDF のラッパー（`ndf-relay`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

ラッパーがセッションを切り替え、シェルの設定へ組み込まれるときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| セッション | `session` | ラッパーが起動する claude の 1 回の起動（1 つの会話）。番号を付けて「セッション 4」と呼ぶ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| シグナルファイル | — | ラッパーへ知らせるファイル（`next.json` と `stop`） | 合図 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| アナウンス | — | ndf-next のブロックの直前にそのまま置く 1 文 | 告知 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| アイドル | — | シグナルファイル・会話の記録・利用者の入力が動かない秒数。この秒数がたつまでラッパーは `/exit` を入力しない | 静止 | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| パススルー | — | ラッパーを挟まず、本物の claude をそのまま起動すること | 素通し | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 管理ブロック | — | シェルの設定の # >>> ndf relay >>> から # <<< ndf relay <<< までの行 | — | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| 自動追加ブロック | — | 10.17.4〜10.17.6 の SessionStart hook が足した管理ブロック。状態ディレクトリの rc-added に載り、rc-user に載らない | 自動の囲み | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| コピー | — | 元のファイルをそのまま別の場所へ置いたもの。ラッパーの relay.py（バージョンは横の relay.version。新しいバージョンを古いバージョンで置き直さない）・マイルストーンの説明から作る mvv.md・承認資料の issues/approval-*.md | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 状態ディレクトリ | `state_dir` | ${XDG_STATE_HOME:-~/.local/state}/ndf/relay/。記録（rc-added など）と作業ディレクトリを持つ | 状態の親 | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| 質問シグナルファイル | — | 作業ディレクトリの question。質問が表示されているあいだ在る | 質問のシグナルファイル | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| 入力注入 | — | ラッパーが子の擬似端末へ /exit 以外の入力を書くこと | 送り込み | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| 起動オプション | — | 最初のセッションの claude の引数のうち、セッションごとに変わらないもの（--dangerously-skip-permissions など） | 起動の方針の引数 | — | `docs/specifications/ndf-relay-install-and-restart.md` |
| ラッパー | `relay` | 利用者の claude を包んで常駐し、ndf-next のブロックを拾って /exit・プラグインの更新・次のセッションの起動を行う（Claude Code だけ） | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 停止シグナルファイル | — | ラッパーに次のセッションを起動させないために置く空のファイル（stop） | 停止のシグナルファイル | — | `docs/specifications/ndf-relay-segment-restart.md` |
| 再起動ループ | — | 起動から短い時間（既定 120 秒）で次のセグメントへ移ったセグメントが 3 つ続き、進まずに起動だけが重なる状態。ラッパーは次のセグメントを起動しない。続きの中で初めて使う起動先へアカウントを替えたセグメントは数えない | — | — | `docs/specifications/ndf-relay-segment-restart.md` |
| 再開コマンド | — | 新しい会話の最初に入力すれば、その工程から再開できるコマンド。ndf-next のブロックの中身 | 再開用のコマンド、引継ぎの 1 行 | — | `docs/specifications/ndf-token-waits-and-context-cut.md` |
| 引継ぎ文書 | `handoff` | 会話を切って新しいセッションで続けるための文書。メインディレクトリの .ndf/handoff/<名>.md に置き、コミットしない。1 本が 1 つの引継ぎの対象を受け持つ | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| 引継ぎの対象 | — | 引継ぎ文書 1 本が受け持つ仕事のまとまり（並列の計画・開発ループ・スプリント・マイルストーン・課題のどれか）。名の頭（parallel- / loop- / sprint- / milestone- / issue-）になる | — | — | — |
| 引継ぎの履歴 | — | 引継ぎ文書の本体から終わった項目を移す文書。本体と同じ場所の <名>-history.md | — | — | — |
| ndf-next | — | 次のセッションの最初の入力を置く、情報文字列が ndf-next のコードブロック。最後の応答に 1 つだけ置く | — | — | `plugins/ndf/skills/development-workflow/references/glossary.md` |
| ランチャー | `relay_launcher` | ~/.claude/ndf/relay.py（ndf relay install が置く複製）。隣の relay.root が指すプラグインの bin/ndf relay を呼ぶだけのエントリポイント | — | — | `docs/specifications/ndf-relay-docs-match-11-wrapper.md` |
| 登録済みアカウント | — | 利用者が登録のコマンドで認証情報を預けた claude のアカウント。アカウントの切り替えの候補になる | — | — | — |
| アカウントの切り替え | — | ラッパーが子の claude を、別の登録済みアカウントか従量の接続の認証で起動し直すこと（見回りの閾値・カットポイント・利用上限・認証の失敗の後）。動いているプロセスの認証は替えない。ndf plan の worker（claude -p）は、利用上限に当たったときだけ替え先のアカウントで打ち直す | — | — | — |
| 使用率 | — | 登録済みアカウントの 5 時間と 7 日の利用上限に対する使用の割合（%）。ラッパーが使用率の API（GET /api/oauth/usage）で、動いていないアカウントを含むすべての登録済みアカウントから取る。API が取れないときと応答の形が変わったときは、Claude Code が status line へ渡す rate_limits の値で代える。リセットの時刻を過ぎていない窓ごとに判断に使う（一覧はその大きいほうを出す） | — | — | — |
| 使用率のキャッシュ | — | 登録済みアカウントごとの使用率・リセットの時刻・記録の時刻を持つファイル。書き手は 2 つで、ラッパーの使用率の API での取得が登録簿の側の取得の記録（<アカウントの登録簿>/.usage-fetch/<名前>/<登録の世代>.json の windows）へ、NDF の status line が API の代わりとして状態の保存先の relay/accounts/usage/<名前>/<登録の世代>.json へ書く。ラッパーと一覧のコマンドは、窓ごとに 2 つを合わせて読む | — | — | — |
| 使用率の記録 | — | 使用率のキャッシュへ使用率を書くこと。ラッパーは使用率の API で取った値をすべての登録済みアカウントについて書き、NDF の status line は受け取った入力の rate_limits から今の登録済みアカウントの値を書く（API が取れないときと形が変わったときの代わり） | — | — | — |
| 支出上限 | — | 追加利用の支出の上限（individual spend limit）。extra_usage.spend_limit_reached が真なら達したとする。spend.percent ≥ 100・spend.severity = critical は表示の補足だけに使う（#2036） | — | — | — |
| 従量の接続 | — | 利用上限の無い、使った分だけ費用が掛かる claude の接続（Bedrock か Anthropic API の API キー）。中身は利用者が定義し、登録済みアカウントがすべて上限か認証できないときに使う | — | — | — |
| アカウントの保存先 | — | 登録済みアカウントごとの設定ディレクトリを並べた <共有の設定ディレクトリ>/ndf/accounts/。NDF の側で書くのは lib/claude_accounts.py とその部品だけで、認証ファイルと .claude.json は claude 自身も書く | — | — | — |
| 上限シグナルファイル | — | 子の claude の応答が API の失敗で終わったときに、StopFailure hook がラッパーの作業ディレクトリへ書く limit.json | — | — | — |
| 従量の接続の設定 | — | 従量の接続で起動する子へ足す変数の並び（KEY=VALUE を空白区切り）。環境変数 NDF_SUPERVISE_CLAUDE_FALLBACK か、relay の保存先に保存した設定が持ち、環境変数があればそちらだけが効く。ラッパーと supervise.py が同じものを読む | — | — | — |
| relay の保存先 | — | relay.py が登録と設定を置くディレクトリ ${CLAUDE_CONFIG_DIR:-~/.claude}/ndf/。アカウントの保存先と、保存した従量の接続の設定を持つ | — | — | — |
| 登録の途中の状態 | — | OAuth の account add を 2 回の呼び出しに分けたとき、1 回目（認可の URL を出す）と 2 回目（認可コードで登録を終える）の間に relay の保存先へ残す状態。期限を過ぎたら捨てる | — | — | — |
| 待機中のログイン | — | OAuth の account add の 1 回目で起動し、認可コードを受け取るまで生かしておく claude auth login のプロセス。登録の途中の状態が持ち、標準入力の FIFO でコードを受ける | — | — | — |
| 呼べるかの確認 | — | 従量の接続の設定を保存する前に、その変数で Bedrock の Claude を 1 回呼び、通らなければ理由を 4 つの区分（認証切れ・権限が無い・地域で使えない・モデルが有効でない）で示すこと。通ったものだけを保存する | — | — | — |
| 残りの量 | — | 登録済みアカウントが上限に達するまでに使える量の見積り。スロットごとの「スロットの大きさ ×（1 − 使用率 / 100）」の最小（USD 換算）。保存せず、選ぶ・一覧を出すたびに求める | — | — | — |
| スロットの大きさ | — | 1 つのスロット（5 時間のクォータ・週のクォータ・モデル別の週のクォータ）が 0% から 100% になるまでに使える量（USD 換算）。支出上限とは別の量 | — | — | — |
| スロットの大きさの対応表 | — | rateLimitTier から 5 時間のクォータと週のクォータの大きさを引く表（claude_accounts.CAPACITY） | — | — | — |
| スロットの大きさの設定 | — | 利用者が登録済みアカウントごとに書くスロットの大きさ（account.json の capacity）。対応表の値より先に効く | — | — | — |
| モデル別の週のクォータ | — | 使用量の応答の limits[] のうち kind が weekly_scoped のもの。特定のモデル（例: Fable）だけの週の上限 | — | — | — |
| claude.ai のコネクタ | — | 利用者が claude.ai で接続した MCP サーバー（Slack・Notion・Google Drive など）。Claude Code は起動時に claude.ai から一覧を取り、claude mcp list に claude.ai <名前> として並べる | — | — | — |
| アカウントのスコープ | — | 登録済みアカウントのトークンに付いた権限の並び。認証ファイルの claudeAiOauth.scopes で、claude が認証ファイルから自分で読む（環境変数では渡さない） | — | — | — |
| アカウントの設定ディレクトリ | `account_dir` | 登録済みアカウントごとのディレクトリ（アカウントの保存先の <名前>/）。認証ファイルと登録の記録を実体で持ち、そのアカウントで起動する claude の CLAUDE_CONFIG_DIR になる | — | — | — |
| 共有の設定ディレクトリ | — | ラッパーを通さずに起動した claude が使う設定ディレクトリ（${CLAUDE_CONFIG_DIR:-~/.claude}）。会話の記録・プラグイン・設定の正を持ち、登録済みアカウントの claude の中ではアカウントの設定ディレクトリの symlink の参照先になる | — | — | — |
| アカウント固有の項目 | — | アカウントの設定ディレクトリに実体で持ち、共有の設定ディレクトリへの symlink にしない項目（認証ファイル・.claude.json・更新の排他・NDF の記録など） | — | — | — |
| 共有する設定の部分 | — | .claude.json のうち、共有の設定ディレクトリの側を正とする projects と mcpServers | — | — | — |
| 同期のバックアップ | — | 前の同期で両側へ書いた共有する設定の部分の写しと、その時点のアカウント側の userID（アカウントの設定ディレクトリの .ndf-shared-base.json） | — | — | — |
| 更新の排他 | — | Claude Code がトークンを更新するときに設定ディレクトリの中に作る排他（.oauth_refresh.lock のディレクトリ） | — | — | — |
| 認証の失敗の観測 | — | 子の claude の応答が認証の失敗で終わったことの記録（account.json の auth_failed）。後の使用量の取得が成功するか 1 時間で解ける | — | — | — |
| 条件の Skill | — | `/goal` の条件の先頭の語が `/<プラグイン>:<Skill>` の形で名指しする Skill（例: `/ndf:development-workflow`） | — | — | `docs/specifications/ndf-relay-goal-skill-and-prose-wait.md` |
| 承認待ちの文 | — | `AskUserQuestion` を呼ばずに、応答の本文で利用者の承認・判断を待つと書いた文。判定は `lib/wait_notice.py` の `classify_reply_wait` で、回答待ちか承認待ちに当たる文を指す。待ち通知の `classify_text` より狭く、操作の案内（再起動・インストールなど）は待ちに数えない | — | — | `docs/specifications/ndf-relay-goal-skill-and-prose-wait.md` |
| 承認待ちの変更要求 | — | ラッパーの直接の子の応答が承認待ちの文で終わったとき、Stop hook が Stop を 1 度止め、`AskUserQuestion` で出し直すよう伝えること | — | — | `docs/specifications/ndf-relay-goal-skill-and-prose-wait.md` |
| 既定のログイン | — | 共有の設定ディレクトリ（${CLAUDE_CONFIG_DIR:-~/.claude}。親から引き継いだ CLAUDE_CONFIG_DIR の指定を含む）のログイン。登録済みアカウントでも従量の接続でもなく、relay のセグメントのアカウントが None のときに子が親から引き継いで使う | — | — | `docs/specifications/ndf-relay-auth-failure.md` |
| 再登録が要る | `needs_relogin` | 登録の記録の needs_relogin が真の登録済みアカウントの状態。アカウントの切り替えの候補から外れ、ndf relay account add で登録し直すまで戻らない | — | — | `docs/specifications/ndf-relay-auth-failure.md` |
| リレーハブ | `relay_hub` | 同じマシンで最初に起動したラッパーが兼ねるプロセス。名前つきのラッパーの登録・起動・同時に動かす数の判定・一覧だけを持ち、判断を持たず、各ラッパーの状態機械には触れない | 統括役 | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| プラグインの更新 | `plugin_update` | ラッパーが最初のセグメントの前とカットポイントで、マーケットプレイスと NDF を claude plugin のコマンドで最新のバージョンへ上げること。失敗しても今のバージョンで次のセグメントを起動する | — | — | `docs/specifications/ndf-relay-plugin-update.md` |
| 名前つきのラッパー | `named_relay` | リレーハブに名前で登録され、tmux の 1 つの window で動く relay.py run の 1 本。カットポイントでセグメントを切り替えても同じ名前で続く | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 人の起動 | `human_launch` | 端末で利用者が打った claude が、リレーハブへ by: terminal で頼んだ起動（端末の 2 本目）。上限で断らず、上限の数にも入れない | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| AI の起動 | `ai_launch` | リクエストの by が spawn・restart・block:<作業ディレクトリ> の起動。リレーハブが上限で判定する | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 端末の結び付け | `attach` | 利用者の端末を、名前つきのラッパーの tmux の window へ attach すること。端末を閉じても window の中のラッパーは動き続ける | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 登録簿 | `relay_registry` | リレーハブだけが書く、名前つきのラッパーの名前と cwd（親エージェントのリポジトリ）・最初の引数・tmux のセッションの表（registry.json。作業ディレクトリのパスは持たない） | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 起動のリクエスト | `spawn_request` | 頼む側がリレーハブの受付箱へ置く 1 件のファイル。起動・やり直しの種類と名前・作業ディレクトリ・引数を持つ | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 起動のブロック | `spawn_block` | conductor の応答に置く、情報文字列が ndf-spawn <名前> の囲み。中身が最初の入力になる | — | — | `docs/specifications/ndf-relay-multi-session-supervisor.md` |
| 共有の環境 | — | NDF のスクリプトとラッパーが使う Python の環境。保存先は ~/.cache/ndf/venv/<バージョン>（NDF_DEPS_VENV で変えられる）。プラグインのバージョンごとに 1 つで、同じバージョンのスクリプトとラッパーが共に使う | — | — | `docs/specifications/ndf-shared-venv-and-relay-ls-failure.md` |
| tmux の専用サーバー | — | 中継のレーンを動かす tmux サーバー（tmux -L ndf-relay）。利用者の既定の tmux サーバーとは別である | — | — | `docs/specifications/ndf-relay-lane-parent-session-env.md` |
| 親のセッションの環境 | — | 動いている claude のセッションとそのラッパーに固有の環境変数。別のプロセスへ継がれると、そのプロセスを親のセッションの中にいると誤らせる | — | — | `docs/specifications/ndf-relay-lane-parent-session-env.md` |
| アカウントの選定 | — | ラッパーがセグメントを起動するときに、指定・残量・上限から使うアカウントを決める手順 | — | — | `docs/specifications/ndf-relay-lane-parent-session-env.md` |
| サブスクリプション | — | 利用者が Anthropic と結ぶ Claude のコントラクト（Max・Ultra・Team）。利用上限（5 時間・週）と月の API クレジットを持つ。supervise.py の「プラン」とは別の物 | — | — | `docs/specifications/ndf-relay-api-keys.md` |
| API クレジット | — | サブスクリプションにリンクした Console 組織へ毎月付く API の残高。利用上限と別枠で、未使用分は請求の周期の終わりで消える | — | — | `docs/specifications/ndf-relay-api-keys.md` |
| 登録済み API キー | — | 利用者が預けた Anthropic の API キー。名前と、結び付いた登録済みアカウントを持つ。11 系は、API キーの使い方の設定が「使わない」でなければ、上限か認証の失敗の後の復帰先として子の起動に使う | — | — | `docs/specifications/ndf-relay-api-keys.md` |
| 残高切れ | — | API キーの要求が "credit balance is too low" で失敗した状態。そのキーはその 1 回の起動の中でだけ候補から外れる | — | — | `docs/specifications/ndf-relay-api-keys.md` |
| API キーの使い方の設定 | — | 登録済み API キーを使うかを決める 1 つの設定。値は「使わない」「常に使う」「上限に近いとき使う」で、既定は「使わない」。11 系は「使わない」でなければ上限か認証の失敗の後の復帰先にキーを使う（10 系はこの値で worker の起動を選んだ） | — | — | `docs/specifications/ndf-relay-api-keys.md` |
| API キーの登録簿 | — | 登録済み API キーと API キーの使い方の設定を持つ、アカウントの保存先の 1 つのファイル（0600） | — | — | `docs/specifications/ndf-relay-api-keys.md` |
| 起動先の決定 | — | 1 回の起動について、どのアカウントか、どの API キーか、待つかを決めた結果。当たった規則の名前と根拠の値を持つ | — | — | `docs/specifications/ndf-relay-launch-target.md` |
| 週の使用率 | — | 週のクォータとモデル別の週のクォータの使用率のうち最も大きい値 | — | — | `docs/specifications/ndf-relay-launch-target.md` |
| 月の額 | — | API キーごとに定義する、月に付与されるクレジットの額（ドル） | — | — | `docs/specifications/ndf-relay-launch-target.md` |
| 週の利用枠 | — | API キーごとの、直近 7 日に使ってよい額。月の額 × 7 ÷ 30 | — | — | `docs/specifications/ndf-relay-launch-target.md` |
| 使用の割合 | — | API キーの直近 7 日の使用額 ÷ その API キーの週の利用枠 | — | — | `docs/specifications/ndf-relay-launch-target.md` |
| 区切り | — | relay がもともと起動し直す時点（フェーズの境・文脈の量による切り替え）。戻るためだけに作らない | — | — | `docs/specifications/ndf-relay-launch-target.md` |
| 子の起動の失敗 | `launch_failure` | ラッパーが子の claude を起動できなかったこと。作業ディレクトリが無いか、擬似端末の起動が OSError を出した。ラッパーは打ち直さずに precondition で終わる | — | — | `docs/specifications/ndf-relay-launch-failure.md` |
| 回答待ちの質問 | `pending_question` | hook が書いた質問のファイル（question.json）のうち、まだ hook かラッパーが消していないもの。ラッパーはこの間、子を止めず、子の端末へ書かない | — | — | `docs/specifications/ndf-relay-pending-question-guard.md` |
| 質問の ID | `question_id` | 1 つの質問を見分ける値。質問のファイル（question.json）の tool_use_id、無ければ質問を書いた時刻 at。ラッパーは転送した質問の ID を覚え、同じ質問を 2 度転送しない | 質問の印 | — | `docs/specifications/ndf-relay-pending-question-guard.md` |
| 起動の前の用意 | — | 登録済みアカウントで子を起動する前に、そのアカウントが使えるかを確かめ、アカウントの設定ディレクトリの symlink を張り、共有する設定の部分を同期すること | — | — | — |
| 外した間 | — | 認証の失敗を見たアカウントを替え先と指定の候補から外す 1 時間。状態の保存先の relay/accounts/held.json が終わりの時刻を持つ | — | — | — |
| 選ぶ順の組 | — | 上限でない登録済みアカウントを使用率で分けた 4 つの組。余裕の組（5 時間が閾値 A より下で 7 日が閾値 B より下）・使用率の分からないもの・B 越えの組（7 日が B 以上 C より下）・使い切りの組（5 時間が A 以上か 7 日が C 以上）の順に選ぶ。同じ組では 7 日の窓のリセットの早いものが先 | — | — | — |
| 切り替えの余裕 | — | 今のアカウントが最良の候補と同じ選ぶ順の組にあれば替えない、という切り替えを抑える条件 | — | — | — |
| 止めてよい時点 | — | ラッパーがセグメントの途中で子を止めてアカウントを替えてよい時点。子の出力が静かな時間止まり、回答待ちの質問が無く、会話の記録の最後が終わった応答（結果を待つ Tool の呼び出しが無い）で、その応答がラッパーの最後の入力より後の時点 | — | — | — |
| 最後の砦 | — | 上限で停まった子を、上限でない登録済みアカウント・従量の接続（保存した設定・API キー）・最も早く上限が解けるアカウントの順に、人の入力無しで同じ会話へ戻すラッパーの振る舞い | — | — | — |
