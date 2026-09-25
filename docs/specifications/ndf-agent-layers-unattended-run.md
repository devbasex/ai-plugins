# 工程を conductor / supervisor / worker の 3 層で通す

`/ndf:development-workflow <指示>` で呼ばれたとき、工程を 3 層のサブエージェントへ出し、
2 つの関門以外を無人で通す運転を決めた。あわせて、利用上限（429）で中断した層を上の層が
**通知ではなく記録から**見分け、解除時刻を過ぎてから直下だけを再開する手順を置いた。この文書は、
決めたことの理由と、層の間で交わす報告と指示の契約を残す。

**手順と文言は `development-workflow` の `references/agent-layers.md` が正である。** 起動の指示の
規則の文言・報告の雛形・点検のコマンドをここへ書き写さない。

| 何を読むか | 正本 |
| --- | --- |
| 3 層の責務、フェーズの表、モードごとの組み方、起動の指示、報告の形、続けさせる回数、モデルの基準、委譲の線、並行の本数、到達点の置き直し、中断と再開 | `plugins/ndf/skills/development-workflow/references/agent-layers.md` |
| 自走の入口 | `plugins/ndf/skills/development-workflow/SKILL.md` の「自走で工程を通す」 |
| モデルに依る目安とリポジトリに依る固定費の区別、粒度の比の基準、委譲してよい作業の表 | `plugins/ndf/skills/development-workflow/references/context-window.md` |
| `cross-review` の「メイン」の定義 | `plugins/ndf/skills/cross-review/references/context-budget.md` |
| 中断した記録の一覧と解除の待ち（`interrupted` / `wait-reset`） | [ndf-context-window-metrics.md](ndf-context-window-metrics.md) と `plugins/ndf/scripts/lib/transcript_agents.py` |

## 概要

**conductor は人間と話し、supervisor はフェーズを通し、worker は 1 つの作業を行う。** 人間へ問える
（`AskUserQuestion` を持つ）のは conductor だけで、supervisor は関門に達したら承認を求めずに
`結果: 関門` で返す。worker は葉であり、別のサブエージェントを起動しない。

**フェーズは 設計 / 実装 / 検査 / 取り込み / 仕上げ の 5 つである。** `context-window.md` の切れ目
4 点で工程表を切ると 5 つになり、関門 2 つはそれぞれ設計と取り込みの終わりに当たる。

**報告は 2 段で、worker の報告を conductor へ転送しない。** worker は 5 項目の作業の報告を
supervisor へ返し、supervisor はそれを畳んで 11 項目のフェーズの報告を conductor へ返す。
conductor が 1 つのフェーズについて読むのは 1 件である。

**上限の中断は記録で見分け、直下だけを再開する。** 通知は目を覚ます契機にだけ使い、分類と
解除時刻は会話の記録から取る。conductor は supervisor と自分が直接起動した worker を、
supervisor は自分の worker を再開する。

## 用語

| 用語 | 意味 |
| --- | --- |
| セッション | 会話を保持しているプロセス。1 つのセッションが 1 つの context window を持つ |
| conductor | 人間と対話しているセッション。`/ndf:development-workflow` を受け、supervisor を起動し、報告を受け取り、関門で人間に問う |
| supervisor | 1 つのフェーズ（連続する工程の束）を通すサブエージェント。工程の Skill を起動し、進行を記録し、worker を起動する |
| worker | 1 つの作業を行うサブエージェント。終われば消え、supervisor には作業の報告だけが残る。別のサブエージェントを起動しない |
| フェーズ | supervisor 1 つが通す工程の束。`設計` / `実装` / `検査` / `取り込み` / `仕上げ` |
| 作業の種類 | worker 1 つが行う作業の分類。`調査` / `修正` / `検証` / `集計`。どれにも当たらなければ `その他` |
| context window | セッションが保持している内容の全体と、その量。捨てると前の内容は残らない |
| 報告 | 下の層が最後に返す、項目の決まった結果。supervisor と worker で形が違う |
| 直下 | 上の層が自分で起動した相手。conductor から見れば深さ 1、supervisor から見れば自分が起動した worker |
| 上限の中断 | 利用上限（429）で層が途中で終わること。記録の `ending` が `rate_limit` |
| 解除時刻 | 上限が解ける時刻。記録の `quotaLimits.resetsAt` が持つ |
| 自動の継続 | 解除時刻に Claude Code が conductor へ積む入力（`origin.kind` が `auto-continuation`） |
| 起動元 | その記録を起動した相手。`.meta.json` の `toolUseId` でたどる |

層の語は `conductor` / `supervisor` / `worker`、量を指す語は `context window`、起動した側は
`起動元` と書く。「窓」「親」は規約の文書に使わない。

## 背景

**`/goal` の工程を 1 つのセッションで通すと、context window が工程ごとに積み上がる。** #550 は
工程をサブエージェントへ出し、2 つの承認以外を無人で通すことと、工程ごとの context window の
大きさを測ることを求めた。当初の 2 層（進行側と担当）は 3 層へ見直され、責務・報告の経路・測定・
中断と再開のすべてを設計し直した。

**担当が利用上限で落ちたことを進行側が検知できず、再開を手で指示していた（#657）。** 実測では、
下の層が 429 で落ちると上の層には `failed` の通知が届き、記録の合成の応答が `apiErrorStatus` と
`quotaLimits.resetsAt` を値として持つ。3 層は同じ割り当てを共有するため、同時に落ちるのが普通で
ある。解除の後に Claude Code が自動で続けた例が 1 つあり、`/goal` の見回りは 120 分の後に止まる。

## 決定と理由

**3 層の責務は、問える相手と持つ単位で分ける。** conductor はミッション、supervisor はフェーズ、
worker は 1 つの作業を持つ。conductor は `development-workflow` と `issue-plan-strategy` 以外を
起動しない。worker を葉にするのは、深さ 3 を実測していないためである。

**supervisor の単位は、切れ目で区切ったフェーズにする。** 工程表の行は 18 あり、
1 行ごとに supervisor を立てると固定費が 18 回掛かる。前の工程の出力がそのまま次の判断の入力に
なる工程は `context-window.md` が「切らない」と定めている。設計・実装・検査の単位は Pull Request、
取り込みと仕上げはミッションである。

**worker へ出すのは `context-window.md` の「委譲してよい」の行に限る。** 新しい判断基準を作らない。
supervisor は委譲しない 5 つ（モード判定・関門の判断・収束の判定・設計の決定と理由の記録・
受け入れ条件の書き換え）を自分で行う。

**報告を 2 段に分け、worker の報告を conductor へ転送しない。** worker を 5 つ使っても conductor
の読む量が変わらないため、層を増やしたことが conductor の context window を押し上げない。押し上がる
のは固定費の合計であり、それは測定が見せる。

**着手の判定は conductor が行い、読解だけを worker へ出す。** 課題の束ね方・モード・到達点は、
最初の supervisor を起動する前に conductor が決める。課題が多く本文の読解が conductor の
context window を埋めるときだけ、読解を worker へ出す（conductor が直接 worker を起動する唯一の
場面）。設計のフェーズでモードを上げるべきだと分かったら、supervisor は `結果: 止まった` で返し、
conductor が判定をやり直す。

**設計 Pull Request のマージは、承認を取った conductor が行う。** マージは 1 つのコマンドで済み、
承認の印の判定（`workflow-guard.sh`）は `development-workflow` を起動した conductor のセッションで
確実に働く。**本番の系へ届く操作は、承認を受け取った次の supervisor が行う。** 配布は `release` の
手順そのもので 1 つのコマンドに収まらず、conductor が起動すると手順が conductor の context window
に載る。承認は起動の指示の `承認` の項目で渡す。

**関門以外の Skill の確認は、提示して進める。** supervisor も worker も `AskUserQuestion` を持たない
ため、確認を待つと止まる。`pr` は起動の指示が工程として名指しするため「依頼が push と Pull Request
の作成まで明示的に含む場合」に当たり、`merged` は取り消せる削除を同意なしに行う
（[ndf-cleanup-and-bundle-closing.md](ndf-cleanup-and-bundle-closing.md)）。

**報告の形を持たずに終わった相手は、同じ相手で 3 回まで続けさせる。** 待ちで応答を終える事象
（#656）は上の層から `completed` で届く。上の層は報告の見出しが無ければ完了として扱わず、
`SendMessage` で続けさせる。3 回続けても報告が出なければ、相手の名前と最後の応答の 1 行を添えて
上へ返す。上限の中断からの再開はこの回数に数えない。

**モデルの既定は上の層と同じにし、落とすのは worker から始める。** 軽いモデルへ落としてよいのは、
決定・受け入れ条件・収束の判定を書かず、かつ出力を外の検査が受け止める相手である。worker の
`調査` と `集計` は 2 つとも満たし、supervisor は満たさない。初期値は全層で既定のモデルとし、
落とすかどうかは測定を見て振り返りで決める。

**cross-review の「メイン」は、収束ループを駆動している supervisor である。** 「メイン context に
diff を載せない」の対象は `state.py` の骨組みを回している層で、3 層では設計と検査のフェーズの
supervisor に当たり、conductor ではない。修正はその supervisor が起動する worker（`修正`）で行う。
定義は `cross-review/references/context-budget.md` に置く。手順書（`SKILL.md`）は 420 行の余白の
規約を持つため、手順書からは指し示すだけにする。

**上限の中断は、通知ではなく記録で見分ける。** 通知の本文は人が読む文言で、書式を約束していない。
上の層が自動の要約で通知を失っても、記録からは同じ一覧が得られる。

**再開は直下だけを見る。** conductor が supervisor の下の worker を直接再開すると、再開した
supervisor と worker が同じ作業や外部への書き込みを重ねる。同時に落ちたときは conductor が起きた
後に上から順に 1 段ずつ再開する。**既定は同じ相手で続けること**で、工程の頭からを後段にした。
実測で `SendMessage` の再開が成立しており、途中まで進めた判断を捨てずに済む。

**解除を待つ手段は 3 段である。** Claude Code の自動の継続（conductor も上限に当たったとき）、
背景で起動した待ち（`wait-reset`）の終わりの通知（下の層だけが中断したとき）、人が送る 1 通
（どちらも起きなかったとき。承認ではなく、関門に数えない）。どの段で起きても conductor は同じ
「中断の点検」を 1 回行う。`/goal` の見回りには頼らない。

**`StopFailure` フックは使わない。** API の失敗で応答が終わったときに発火するが、出力も終了コードも
無視されるため conductor を起こせない。

**3 層の規約は新設の `agent-layers.md` に置き、中断と再開も同じ文書へ入れる。** 中断は並行して
いなくても起きるため、並行の本数を扱う `parallel-work.md` へ置くと並行しないときに読まれない。
無人でない進行にも同じ手順が効くよう、対象を「上の層が起動した相手」と書く。

**自走の節は入口だけを残し、細部を参照文書へ置く。** `development-workflow/SKILL.md` は分割の
基準（500 行）の内に収める。「到達点を置き直す」の小節は `agent-layers.md` に置き、節には 3 層へ
出すことと参照先だけを書く。**フェーズの実行は常に 3 層へ出す。** 人がその場にいて指示を変えたい
ときは、conductor へ伝えれば次のフェーズから反映する。`/goal` は無人で続けるかどうか
（引き継ぎの 1 行の先頭に `/goal ` を付けるか）だけを分ける。

**識別子とファイル名は層の側を揃え、量を指す名前は `window` のまま残す。** `agent-layers.md` /
`transcript_agents.py` / `--agents` / `AgentRecord` は層の語、`context-window.md` / `--window-limit` /
`fixed` / `peak` / `work` は量の語である。

**並行の本数は supervisor で数え、worker はその 1 本の中に収める。** 実行計画の「担当 1 本」は
supervisor 1 つ（1 つの作業ツリー）に当たる。同時に動かす worker は 1 つの supervisor につき既定
1 つで、増やすときは `parallel-measure.py capacity` を測り直して 1 本の見込みを上げてから増やす
（[ndf-execution-plan-and-parallel-capacity.md](ndf-execution-plan-and-parallel-capacity.md)）。

## 仕様

### 常に成り立つ条件

- 関門は 2 つで増えない。承認を取れるのは conductor だけである
- worker は葉である（深さ 3 を作らない）
- worker の報告は conductor へ転送されない
- 規約の文書は固定費の実測値を持たない（比だけを持つ）
- `/ndf:development-workflow` を呼べば 3 層へ出す
- 報告の見出しが無い相手を続けさせるのは、同じ相手で 3 回までである
- 上の層が再開するのは自分の直下だけである

### フェーズ

| フェーズ | 通す工程 | 単位 | 返す関門 |
| --- | --- | --- | --- |
| 設計 | 要求と受け入れ条件 / 作業場所の用意 / 設計 / ドキュメント再構成 / ドキュメントレビュー（cross-review が収束するまで） | Pull Request | 設計 Pull Request のマージ |
| 実装 | ドキュメントレビューの後片付け / 作業場所の用意（実装用）/ 計画 / 実装。終わりに `pr` を Draft で 1 度呼ぶ | Pull Request | なし |
| 検査 | 構造改善 / 実装レビュー / 完了判定 / Pull Request（Draft を外す） | Pull Request | なし |
| 取り込み | 確定仕様化 / 実装 Pull Request のマージ / 後片付け / 配布（検証まで） | ミッション | 本番の系へ届く操作（要るときだけ） |
| 仕上げ | 配布（本番）/ リリース後テスト / 振り返り | ミッション | なし |

フェーズの名前はモードで変えない。モードは各フェーズがどの工程を含むかと関門を返すかだけを変え、
作らないフェーズ（`light` の設計と仕上げなど）の工程は次のフェーズの先頭へ入る。関門を返すフェーズは、
中身が少なくても作る。`operation` では設計のフェーズが本番の系へ届く実行の前で関門 2 を返し、
実装のフェーズが承認を受け取って実行する。

### 層の判定と `description` の形

| 層 | 決め方 | `description` の形 |
| --- | --- | --- |
| conductor | `<セッション>.jsonl`（深さ 0） | — |
| supervisor | `spawnDepth` が 1 で、先頭語がフェーズの語彙にある | `<フェーズ>: <課題番号を空白区切り>`（例 `設計: #550 #657`） |
| worker | `spawnDepth` が 2 以上、または 1 で先頭語が作業の種類の語彙にある | `<作業の種類>: <一言>`（例 `調査: 既存の規約の突き合わせ`） |

層を `description` へ書かせない。書かせると、書き忘れた相手が層の分からない行になる。深さ 1 で
語彙が決められないときは supervisor とし、`role` は `その他` になる。**この形は規約であり、測定
（`skill-stats --agents`）の語彙と一致することをテストが固定する。** 実物の記録で supervisor 8 件が
すべて `その他` に落ちたことが、形を規約にした理由である。

### 起動の指示

conductor → supervisor の `prompt` は 8 項目（フェーズ・課題・モード・作業場所・前のフェーズの報告・
承認・到達点・提示物の置き場所）と守る規則 12 個を持つ。supervisor → worker は 4 項目（作業・
入力・返す形・置き場所）と守る規則 4 個を持つ。`model` は省く（上の層と同じ）。

conductor → supervisor の `subagent_type` は、その supervisor が最初に通す工程で選ぶ。収束ループ
（構造改善・実装レビュー・ドキュメントレビュー）から始めるなら `ndf:supervisor-waits`（キャッシュの
寿命 1 時間）、それ以外は `ndf:supervisor`（寿命 5 分）である。2 つの定義は本文が同じで、寿命だけが違う。
使えるエージェントの一覧に `ndf:supervisor` が無いとき、または `Agent` が知らない `subagent_type` で
失敗したときは、`subagent_type` を省いて起動する（`general-purpose`）。

規則の要点は次のとおりで、文言は `agent-layers.md` が持つ。

| 層 | 規則の要点 |
| --- | --- |
| supervisor | 関門に達したら承認を求めずに `結果: 関門` で返す / 関門以外の確認は提示して進める / 工程に入った時点で `progress-tracking` を呼び、記録は 1 回の Bash 実行に 1 件 / 待ちで応答を終えない / 外部へ書く前に既に書いたものを確かめる / 範囲外は `out-of-scope` / 委譲してよい作業は worker へ出し、委譲しない 5 つは自分で行う / worker の報告をそのまま渡さない / 末尾に `## フェーズの報告` / モードを上げるべきなら `結果: 止まった`、置き直した到達点に達したら `結果: 完了`・`次のフェーズ: 無し` / 収束ループの Skill を hook に止められたら、やり直さずに `結果: 区切り`・`次の工程: <その工程>` で返す |
| worker | 人間へ問わず、別のサブエージェントを起動しない / 進行を記録しない / 収束の判定・設計の決定・受け入れ条件の書き換えを行わず、判断が要るときは `結果: 判断が要る` / 末尾に `## 作業の報告` |

### 報告の形

| 報告 | 項目 |
| --- | --- |
| `## 作業の報告`（worker → supervisor） | 作業（作業の種類）/ 結果（`完了` / `判断が要る` / `できなかった`）/ 見つけたもの / 置き場所 / 次にすること |
| `## フェーズの報告`（supervisor → conductor） | フェーズ / 課題 / 結果（`完了` / `関門` / `止まった` / `区切り`）/ 関門（`設計 Pull Request のマージ` / `本番の系へ届く操作`）/ 次のフェーズ / 次の工程（`区切り` のときだけ）/ Pull Request / 最後に記録した工程 / 使った worker / 提示物 / 理由 |

supervisor の報告は最後の応答の**末尾**に置き、見出しの後ろに他の見出しを置かない。空の項目は `無し`。

**conductor が見るのは、見出しの有無と `結果` の 2 つである。**

| 見出し | `結果` | conductor の動き |
| --- | --- | --- |
| 無い | — | `SendMessage` で続けさせる。3 回続けても出なければ 4 回目は送らずに止まる |
| ある | `完了` | `次のフェーズ` を起動する。`無し` なら到達の報告 |
| ある | `関門` | `提示物` を読み、`AskUserQuestion` で承認を求める。`設計 Pull Request のマージ` なら conductor がマージしてから、`本番の系へ届く操作` ならマージせずに `次のフェーズ` を起動する。モードやフェーズの名前からは分岐しない |
| ある | `止まった` | `理由` を添えて利用者へ報告し、終える |
| ある | `区切り` | 同じフェーズを `次の工程` から通す supervisor として起動し直す（`subagent_type` は上の選び方）。関門を問わない。同じ `次の工程` の `区切り` が続けて 2 回返ったら `止まった` と同じに扱う |

**区切りは hook が決める。** `token-guard.sh` は、寿命 5 分の supervisor（入力の `agent_type` が
`ndf:supervisor`）が `cross-review` / `cross-refactoring` を起動するとき、自身の記録の今の文脈が最初の
呼び出しの文脈の 1.5 倍以上なら止め、やり直しても止め続ける（比は `NDF_SUPERVISOR_CUT_RATIO`、
無効化は `NDF_SUPERVISOR_CUT_GUARD=0`）。`ndf:supervisor-waits`・worker・`general-purpose`・conductor は止めない。

supervisor が worker の報告を見るときも同じ規則を使う。`結果: 判断が要る` は supervisor が判断する。

**conductor は、止まるときに `## フェーズの一覧` を 1 回出す。** 出す時点は `AskUserQuestion` を出す前・
失敗を報告する前・背景の待ちを起動して応答を終える前の 3 つである。列は フェーズ / 課題 / 結果 /
使った worker / 報告なしで続けさせた回数 / 上限の中断から再開した回数 で、続けさせた回数は記録から
見分けられないため conductor が `SendMessage` を送るたびに数えて持つ。同じフェーズを工程の頭から
起動し直したときと、`区切り` の後に工程の途中から起動し直したときは別の行にする。

### 中断と再開

| 落ちた層 | 検知する側 | 再開する側 | 起こす手段 |
| --- | --- | --- | --- |
| worker | supervisor（失敗の通知）。supervisor も落ちていれば conductor が supervisor を起こした後に supervisor が見る | supervisor | 生きていれば `SendMessage`。生きていなければ conductor → supervisor → worker の順 |
| supervisor | conductor（失敗の通知） | conductor | `SendMessage`。失敗したら進行の記録が指す工程の頭から新しい supervisor（`subagent_type` はその工程で選ぶ） |
| conductor | 人間か Claude Code（自動の継続） | conductor 自身 | 自動の継続・人の 1 通・背景の待ちの終わり |

**conductor の中断の点検**は、目を覚ますたびに 1 回行う。`interrupted --depth 1` で中断した
supervisor と直接起動した worker を一覧し、`resets_passed` が真の記録ごとに `SendMessage` で続け
させる。失敗（結果が `"success": true` を持たない）した supervisor は `最後に記録した工程` の頭から
同じフェーズの名前で起動し直し、起動の指示に旧 supervisor の `agent_id` を渡す。まだ過ぎていない
相手があれば `wait-reset --depth 1` を背景で起動して応答を終える。**通常は `--max-sleep` を付けない。**
背景の待ちを数時間続けられないと分かったときだけ `--max-sleep 540` を付け、終了コード 3 で起きる
たびに一覧と待ちだけを行い、解除前に `SendMessage` しない。

**再開した supervisor は、同じ点検を worker に対して行う。** 起動し直された supervisor の初回は
`--parent <旧 agent_id>` で旧 worker を見て、自分で worker を起動した後は `--agent <id>` で絞る。
`<id>` は worker を起動したときの結果に出た `agentId` で、supervisor が自分の context window に持つ
（`CLAUDE_AGENT_ID` のような環境変数は無い）。続けられない worker は同じ作業をもう一度起動し、
起動の指示に「旧 worker が外部へ書いたものを先に確かめ、済んでいる分はやり直さない」を入れる。

`CLAUDE_CODE_SESSION_ID` は conductor でもサブエージェントでも conductor のセッションの ID を持つ。
取れないときは `Agent` の起動の結果に出る `output_file` のパスの `<セッション>` の部分を渡す。

## テスト観点

| 観点 | 確かめ方 |
| --- | --- |
| 3 層の責務の表、フェーズ 5 つと関門の列、報告の 11 項目 / 5 項目、続けさせる回数 3、モデルの基準 2 つ、委譲しない 5 つ、並行の単位が supervisor、止まるときにフェーズの一覧を出す 3 つの時点 | 文書を読んで確かめる |
| 中断の契機 4 つ、落ちた層ごとの 3 通り、解除を待つ手段 3 段、点検の手順、`--max-sleep` を付けない規約 | 同上 |
| 「メイン」の定義が supervisor を指す | 同上 |
| `context-window.md` にモデルに依る目安とリポジトリに依る固定費の区別があり、規約の文書に固定費の実測値が無い | 同上 |
| 3 つの規約の文書に「窓」「親」が無い | 同上 |
| `/ndf:development-workflow` を呼べば 3 層へ出す、関門以外は提示して進める | 同上 |
| 関門の数・承認の形が変わらない | `test_workflow_guard.py` が無改変で通る（`test_approval_gates.py` は #885 で削除） |
| 実機での無人の通過（人の入力の数、conductor の報告のフェーズの一覧と記録の `SendMessage` の数の一致） | リリース後テスト（`light` の課題 1 件を `/goal` で通し、次のミッションで `standard` を含む複数の課題を関門 2 つと振り返りまで通す） |
| 上限からの再開そのもの | リリース後テスト（上限を意図して起こせないため、発生した実行の記録を読む） |

## 運用

**承認の印の判定がサブエージェントの Bash に掛かるかは確かめていない。** conductor がマージする
ため関門は保たれるが、掛からないと supervisor の進行の記録が通過工程の控えに積まれない。その場合は
起動の指示で supervisor の最初に `development-workflow` を「判定済み」として起動させる。

**自動の継続が 5 時間の上限・見回りが止まった後でも入るか、背景の待ちを数時間続けられるか、
`--resume` で開き直した後も `SendMessage` が効くかは、リリース後テストで確かめる。** 効かなければ
それぞれ人の 1 通・`--max-sleep 540`・工程の頭からの新しい supervisor へ落ちる。

**supervisor が落ちている間に worker が動き続けられるかは分かっていない。** 動き続けるなら
supervisor の再開の後に worker の点検を 1 度行えば足りる。

**進行の記録は 1 回の Bash 実行に 1 件である。** #487 が直れば起動の指示の 1 行を消す。

## 関連リンク

- [issue #550](https://github.com/devbasex/ai-plugins/issues/550) — `/goal` の工程をサブエージェントへ出す
- [issue #657](https://github.com/devbasex/ai-plugins/issues/657) — 上限で落ちた担当の検知と再開
- [PR #740](https://github.com/devbasex/ai-plugins/pull/740) — 要求と設計
- [PR #752](https://github.com/devbasex/ai-plugins/pull/752) — 無人の運転
- [PR #757](https://github.com/devbasex/ai-plugins/pull/757) — 中断と再開
- [ndf-context-window-metrics.md](ndf-context-window-metrics.md) — 記録の読み取りと測定
- 要求・設計・契約・計画の元の文書は `issues/old/milestone-18-unattended/` にある
