# #542 #786: cross-review のラウンドを減らす — 設計

要求と受け入れ条件は [issue-542-786-requirements.md](issue-542-786-requirements.md) にある。決定の理由は
[issue-542-786-design-decisions.md](issue-542-786-design-decisions.md) にある。この文書は「どう作るか」だけを扱う。

## 例: 同じ PR の 2 ラウンド目で担当が受け取るもの

設計 PR（`issues/issue-900-design.md` を変える）の 1 ラウンド目で major 3 件・minor 2 件が出て、修正が
2 コミット入った後の 2 ラウンド目。`start-round` は次の 2 つを書く。

- 既存コメントの控え（取り直し）: 1 ラウンド目の 5 件の指摘と、各件への「対応しました（<SHA>）」の返信の行
- 前のラウンドからの変更: `cross-review-pr900-round2-changes.md`

```markdown
## 前のラウンドからの変更

前のラウンド（round 1）の head `1a2b3c4` から今の head `5d6e7f8` までに、次のファイルが変わった。

- issues/issue-900-design.md
- issues/issue-900-requirements.md

差分は作業ツリーで `git diff 1a2b3c4 5d6e7f8` を実行して読む。
**変わった節と、それを参照する節・同じ契約を使う節を先に見る。** 修正が新しく作った経路（状態・分岐・引数）に
穴が無いかを確かめる。直った指摘を繰り返さない（既存コメントの控えに返信がある）。
```

担当のプロンプトには、この節と、控えと、観点テンプレート（`common` / `docs_only` / `design`）が入る。担当は
修正が入った節から読み、その修正が作った穴を 2 ラウンド目のうちに出す。収束の判定は変えない。

## 機能一覧

| # | 機能 | 誰が使うか |
| --- | --- | --- |
| F1 | 既定の母集合を claude / codex / kiro とホストにする | `init`（cross-review） |
| F2 | 既定の母集合に無い者を外す指定を、止めずに無視する | `init`（cross-review / cross-refactoring） |
| F3 | ラウンドの開始ごとに既存コメントの控えを取り直す | `start-round` |
| F4 | 前のラウンドからの変更の節を書く | `start-round` → `launch-reviewer.sh` |
| F5 | 出し切りの指示・テストと背景の処理を起動しない指示をプロンプトに入れる | `launch-reviewer.sh` |
| F6 | 設計 PR を分類し、設計向けの観点を渡す | `init`（観点の組み立て） |
| F7 | 適用担当に、背景で起動したテストを残して終わらないよう求める | cross-refactoring の適用のプロンプト |

## 構成要素

| 要素 | 責務 | 変更 |
| --- | --- | --- |
| `plugins/ndf/scripts/lib/assignment.py` | 母集合と座席と参加者の解決 | 定数 `DEFAULT_REVIEW_RUNTIMES` を足し、`review_pool` をそれとホストから作る。`resolve_participants` は母集合に無い者の除外を無視し、`Participants.ignored_exclude` に残す |
| `plugins/ndf/skills/cross-review/scripts/state.py` | 状態ファイル・観点・参加者の解決・ラウンドの開始 | 控えの取得を関数 `_fetch_existing_comments` に分け、`start-round` からも呼ぶ。`start-round` が変更の節のファイルを書く。分類 `design` と `DESIGN_REVIEW_TEMPLATE` を足す。`_resolve_reviewers` が無視した除外を 1 行で出す |
| `plugins/ndf/skills/cross-review/scripts/launch-reviewer.sh` | レビューのプロンプトを組んで担当を起動する | 変更の節のファイルがあれば埋め込む。出し切りの指示と、テストと背景の処理を起動しない指示を足す |
| `plugins/ndf/skills/cross-refactoring/scripts/refactor_lib/commands/setup.py` | cross-refactoring の参加者の解決 | 無視した除外を 1 行で出す |
| `plugins/ndf/skills/cross-refactoring/prompts/apply.md` | 適用担当のプロンプト | テストを前景で終わるまで待つ指示を足す |
| 文書 | 使い方と仕様 | `cross-review/SKILL.md`・`docs/02-fix-and-rotation.md`・`docs/05-pool-and-convergence.md`・`docs/06-evidence.md`・`docs/specifications/cross-review-participants-and-seats.md`・`plugins/ndf/README.md`・`CLAUDE.md` の cross-review 節 |
| テスト | 下の「テスト設計」 | 既存の母集合のテストを直し、新しい分岐のテストを足す |

変えないもの:

| 対象 | 持つもの |
| --- | --- |
| `review_seats` | 輪番の式 |
| `_new_finding_count` / `_evaluate_convergence` | 新しい指摘の数え方と収束の判定 |
| `cmd_check_oscillation` | 振動の検知 |
| `_handle_no_result_round` | 結果が無い担当の起動し直し |
| `fix/scripts/fetch-pr-comments.sh` | 控えの形 |
| `refactor_pool` | cross-refactoring の母集合 |

### 構成要素図

```mermaid
graph LR
  subgraph lib["plugins/ndf/scripts/lib"]
    A["assignment.py<br/>DEFAULT_REVIEW_RUNTIMES<br/>review_pool / resolve_participants"]
  end
  subgraph cr["cross-review/scripts"]
    S["state.py<br/>init / start-round"]
    L["launch-reviewer.sh"]
  end
  subgraph rf["cross-refactoring"]
    SU["setup.py"]
    AP["prompts/apply.md"]
  end
  F["fix/scripts/fetch-pr-comments.sh"]
  G[("GitHub API")]
  W[("作業ツリー<br/>.cross_review/")]
  S -->|母集合・参加者| A
  SU -->|参加者| A
  S -->|控えの取得| F
  F --> G
  S -->|控え・変更の節・state.json| W
  L -->|読む| W
```

### システムの文脈と配置

変更はすべて利用者の手元で動くスクリプトの中に閉じる。外との境界は GitHub API（控えの取得。今は `init` の 1 回、
変更後はラウンドごと）と、担当の CLI（codex / kiro / claude / agy。プロンプトのファイルを受け取る）の 2 つで、
どちらの境界の形も変わらない。担当の CLI の数が既定で 1 つ減る（agy）。

```mermaid
graph LR
  U["進行側（ホストの LLM）"] -->|state.py のコマンド| S["state.py"]
  S -->|gh api| G[("GitHub")]
  S -->|launch-reviewer.sh| C["担当の CLI<br/>claude / codex / kiro<br/>（agy は --include）"]
  C -->|payload / result.json| S
```

### パッケージ・モジュール構成

```text
plugins/ndf/
├── scripts/lib/assignment.py                 # 変える
├── scripts/tests/test_lib_assignment.py      # 変える
└── skills/
    ├── cross-review/
    │   ├── SKILL.md                          # 変える
    │   ├── docs/02-fix-and-rotation.md       # 変える
    │   ├── docs/05-pool-and-convergence.md   # 変える
    │   ├── docs/06-evidence.md               # 変える
    │   ├── scripts/state.py                  # 変える
    │   ├── scripts/launch-reviewer.sh        # 変える
    │   └── tests/                            # 変える・足す
    └── cross-refactoring/
        ├── prompts/apply.md                  # 変える
        ├── scripts/refactor_lib/commands/setup.py  # 変える
        └── tests/test_assignment.py          # 変える
```

## 構造

変える型は `assignment.Participants`（参加者の解決の結果）の 1 つで、項目を 1 つ足す。

```mermaid
classDiagram
  class Participants {
    +list~str~ pool
    +list~str~ included
    +list~str~ excluded
    +list~str~ ignored_exclude
    +list~str~ available
    +dict~str,str~ unavailable
    +bool probe_skipped
    +bool require_all
  }
  note for Participants "ignored_exclude を足す。既定は空。\n外した者（excluded）とは別に持つ"
```

## 入出力の契約

### 母集合と参加者（`assignment.py`）

```python
DEFAULT_REVIEW_RUNTIMES = ("claude", "codex", "kiro")  # ホストを除いた部分。agy は --include で足す

def review_pool(host: str) -> list[str]:
    # ホストになれない名前は今と同じく AssignmentError
    return _in_fixed_order((*DEFAULT_REVIEW_RUNTIMES, host))
```

| ホスト | `review_pool` | 引数なしの座席（round 1 / 2 / 3） |
| --- | --- | --- |
| claude / codex / kiro | `[claude, codex, kiro]` | codex+kiro / claude+kiro / claude+codex |
| agy | `[claude, codex, agy, kiro]` | codex+agy / agy+kiro / claude+kiro（今の既定と同じ輪番） |

`resolve_participants` の除外の検査を次のように変える。

| 除外に書いた名前 | 今 | 変更後 |
| --- | --- | --- |
| 母集合か `--include` にある | 外す | 外す（同じ） |
| 綴りの正しいランタイム名で、母集合にも `--include` にも無い | `AssignmentError`（終了コード 1） | 外さずに無視し、`Participants.ignored_exclude` に固定順で残す |
| 綴りの誤り | `AssignmentError` | `AssignmentError`（同じ） |

`Participants` に `ignored_exclude: list[str]`（既定は空）を足す。呼び出し側（cross-review の `_resolve_reviewers`、
cross-refactoring の `setup.py`）は、空でなければ次の 1 行を標準エラーへ出して続ける。

```text
ℹ --exclude agy は既定の母集合に無いため無視しました（母集合: claude, codex, kiro）
```

状態ファイルの `participants` には `ignored_exclude` を書き足す（`excluded` には入れない。外した者と区別するため）。

### 既存コメントの控え（`state.py`）

```python
def _fetch_existing_comments(repo: str, pr: int, path: pathlib.Path) -> str | None:
    """fetch-pr-comments.sh を呼び、成功なら path へ書いて None、失敗なら理由の文を返す。"""
```

| 呼ぶ場所 | 失敗したとき |
| --- | --- |
| `init` の新規開始（今の場所） | 今と同じく `die`（終了コード 1） |
| `start-round`（2 ラウンド目以降。ラウンドを開いた後、担当を起動する前） | 前の控えを残し、`⚠ 既存コメントの控えを取り直せませんでした（<理由の先頭 200 字>）。前の控えのまま進めます` を標準エラーへ出して続ける |

1 ラウンド目は `init` が取った直後であるため取り直さない。控えの形（1 行 1 件、`[PR-COMMENT]` などの接頭辞）は変えない。

### 前のラウンドからの変更の節（`state.py start-round` → `launch-reviewer.sh`）

`start-round` はラウンドを開いた後に `$TMP_DIR/cross-review-pr<STATE_PR>-round<ROUND>-changes.md` を扱う。

| 条件 | ファイル |
| --- | --- |
| 同じ PR の前のラウンドが無い（1 ラウンド目・PR の切り替え直後） | 書かない |
| 前のラウンドか今のラウンドの `head_sha` が無い | 書かない |
| 2 つの `head_sha` が同じ | 書かない |
| 違う | 書く（形は冒頭の例） |
| `git diff --name-only <前> <今>` が失敗した | 書かず、`⚠ 前のラウンドからの変更を取れませんでした` を出して続ける |

- 変わったファイルは `git -C <worktree> diff --name-only <前> <今>` の出力の順に並べ、50 件を上限にする。超えた分は
  「ほか N 件」の 1 行にする
- 差分の本文は書かない
- 同じ名前のファイルが前の起動で残っていれば、書かない場合も消す（古い節を次のプロンプトへ入れないため）

`launch-reviewer.sh` は、このファイルが空でなければ中身を「既存コメントスナップショット」の節の直後へ入れる。
無ければ何も入れない。

### プロンプトに足す指示（`launch-reviewer.sh`）

| 置く節 | 足す指示 |
| --- | --- |
| `## 指摘に含めてはいけないもの` の直前 | 「見つけた指摘はこのラウンドですべて出す。次のラウンドへ回さない。重要度が minor のものも書く」 |
| `## 守るべきこと` | 「テストを実行しない。実行して確かめる手順は `suggested_check` に書く（進行側の `verify-findings` が実行する）」「背景で処理を起動しない。起動した処理の終わりを待たずに結果のファイルを書かないまま終わると、結果が無い担当として扱われる」 |

### 設計向けの観点（`state.py`）

分類の述語 `_is_design_doc_path(path)` は、次の 2 つを満たすパスで真を返す。

- `issues/` で始まる
- ファイル名が `-requirements.md` / `-design.md` / `-design-decisions.md` のどれかで終わる

2 つの表へ 1 行ずつ足す。

| 表 | 足す行 |
| --- | --- |
| `PATH_CATEGORY_RULES` | `("design", _is_design_doc_path)` |
| `CATEGORY_TEMPLATES` | `"design": DESIGN_REVIEW_TEMPLATE` |
`docs_only` の判定は変えない
（設計 PR は `common` / `docs_only` / `design` の 3 つになる）。

```text
### 設計 PR
- 要求・設計・決定の記録の 3 文書で、受け入れ条件ごとに設計の要素とテスト設計の行があるか。決定で退けた案が他の節に残っていないか
- 状態（ファイル・環境変数・状態ファイルの項目・引数）ごとに、書き手と読み手を並べる。同じ状態を 2 つの経路が書く・読む側が書く側より先に動く・失敗した書き手の後に読む、の矛盾が無いか
- 外部コマンド・外部ツールの挙動（優先順位・終了コード・一致の範囲）を断定する記述に、実測の根拠（コマンドと出力）があるか
```


## 処理の流れ

### 1 ラウンド（変わる段を太字にする）

図に含めないもの: cross-refactoring の `setup.py` と `prompts/apply.md`（cross-review のラウンドを通らない。
変わるのは参加者の解決の 1 行の表示と、適用担当への指示の文だけである）。

```mermaid
sequenceDiagram
  participant P as 進行側
  participant S as state.py
  participant G as GitHub
  participant L as launch-reviewer.sh
  participant C as 担当 2 者
  P->>S: start-round
  S->>S: 作業ツリーを head へ同期・ラウンドを開く
  S->>G: **控えを取り直す（2 ラウンド目以降）**
  S->>S: **変更の節のファイルを書く / 消す**
  P->>L: 席ごとに起動
  L->>C: プロンプト（控え・**変更の節**・観点・**出し切りの指示**）
  C-->>S: payload / result.json
  P->>S: read-result・verify-findings・critique
  P->>S: judge（変えない）
  alt 0（収束）
    P->>P: 最終スイープ → verify-sweep → report
  else 2（新しい指摘がある）
    P->>S: check-oscillation → 修正 → merge-fix → 次のラウンド
  end
```


## 非機能の実現方式

| 条件 | 実現方式 |
| --- | --- |
| 変更の節は 50 件で 6,000 バイト以下 | ファイル名だけを並べ、差分の本文を入れない。1 件 100 バイトとして 5,000 バイトと、定型の文 約 600 バイト |
| 控えの増分は同じ実行の前のラウンドの分だけ | 取り直しは `fetch-pr-comments.sh` の全件の取得で、増えるのは前の取得の後に投稿された行だけである |

## 決定の記録

決定の見出し・理由・採らなかった案は [issue-542-786-design-decisions.md](issue-542-786-design-decisions.md) にある（11 件）。

## テスト設計

`.md` の文言は固定しない（#885）。プロンプトは節の有無と、条件で入る・入らないの分岐を見る。

| 受け入れ条件 | 何で確かめるか |
| --- | --- |
| AC1 AC2 AC3 | `test_lib_assignment.py`: `review_pool(h)` をホスト 4 通りで比べる。`resolve_participants` + `review_seats` で host=claude の round 1〜3 を比べる |
| AC4 | 同上: `--include agy` の座席が今の既定の輪番と一致する。`--exclude agy` が例外を出さず `ignored_exclude == ["agy"]`、`available` に agy が無い。`test_state_review_pool.py`: `init --exclude agy` が終了コード 0 で `ℹ` の行を出し、状態ファイルに `ignored_exclude` が残る。綴りの誤りは今どおり 1 |
| AC5 | 目で見る。`grep -rn "4 者" plugins/ndf/skills/cross-review CLAUDE.md plugins/ndf/README.md docs/specifications/cross-review-participants-and-seats.md` が母集合の説明として 0 件 |
| AC6 AC7 AC8 | 目で見る（文言）。`test_launch_reviewer_prompt_context.py` の既存の組み立てのテストが通る |
| AC9 | `start-round` のテスト（新規 `test_state_round_changes.py`）: 一時の git リポジトリで 2 つの head を作り、2 ラウンド目で変更の節のファイルが書かれ、2 つの SHA とファイル名が入る。`launch-reviewer.sh` の組み立てで、ファイルがあるときプロンプトに節が入る |
| AC10 | 同上: 1 ラウンド目・同じ head・`head_sha` の無いラウンドでファイルが無く、前の起動の残りも消える。プロンプトに節が入らない |
| AC11 | 同上: 53 ファイルの差分で一覧が 50 件と「ほか 3 件」、ファイルの大きさが 6,000 バイト以下 |
| AC12 | 同上: `fetch-pr-comments.sh` を差し替えた偽物で、2 ラウンド目の `start-round` が呼び、控えが新しい中身になる。1 ラウンド目では呼ばない |
| AC13 | 同上: 偽物が失敗すると `start-round` が終了コード 0 で `⚠` の行を出し、控えは前の中身のまま |
| AC14 AC16 | `test_state_auto_review_templates.py`: `issues/issue-1-design.md` を含む変更が `common` / `docs_only` / `design` に、`issues/notes.md` と `docs/x-design.md` だけの変更が `design` を含まない |
| AC15 | 目で見る（テンプレートの文言） |
| AC17 | 既存の `test_classify_findings.py`（minor は数えない区分へ落ちる）と judge のテストが変更なしで通る |
| AC18 | 既存の `test_state_check_oscillation.py`・`test_judge_no_result_reason.py`・`--only` のテストが変更なしで通る |
| AC19 | `_evaluate_convergence` と `review_seats` を変えない（差分に現れない）ことを実装の PR で確かめる |
| AC20 | `uv run --project plugins/playwright-kit/skills/playwright-kit-ops --with pytest pytest . -q -n 4` が通る |

## 未確認のまま残ること

| 項目 | 内容 |
| --- | --- |
| ラウンドが実際に減るか | この変更の効果は、配布後の設計 PR のラウンド数で見る。比べる手段は #893 が作る。手元では退避された状態ファイル（`.git/ndf/worktree-trash/*/.cross_review/`）の `rounds` を数えて比べられる |
| 出し切りの指示で 1 ラウンド目の出力が増えるか | 増えると担当の所要が延びる。agy は既定から外れるため、無進捗の許容（480 秒）に当たる担当は既定では居ない。codex の無進捗の許容は 180 秒で、実装の PR の cross-review で所要を見る |
| 控えの大きさ | #908 の実行 3 で控えは 54,957 バイトだった。取り直しで同じ実行の指摘と返信の行が増える。実装の PR の cross-review で大きさを見る（圧縮は範囲外） |
| #892 の後の母集合で動いた実行が無い | claude が座席に入った実行は手元の記録にまだ無い。この変更の後の既定（claude / codex / kiro）で 2 ラウンド目に claude が座る |
| 設計 PR の見分けの取りこぼし | ファイル名の規約（`-requirements.md` / `-design.md` / `-design-decisions.md`）から外れた設計文書は `design` に分類されない。`issues/` 配下の既存の設計文書の名前は、実装の時点で `ls issues/*design*` で確かめる |
