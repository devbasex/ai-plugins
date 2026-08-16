# cross-refactoring 不具合修正の引継ぎ

> **対応済み（NDF v8.2.0）。** 9 件すべてを修正した。設計判断と結果は
> [issue-113-cross-refactoring-defect-fixes.md](issue-113-cross-refactoring-defect-fixes.md)
> にある。以下は着手時点のメモとして残す。着手前と変わった点は 2 つ。
>
> - **不具合 7 の回避は編集元へ反映済み**（`prepare-worktrees.sh`）。
>   プラグインキャッシュへの手当ては不要になった
> - **取り消しは項目単位を保てないことがある。** 同一ファイルの隣接行を触る項目どうしは
>   git だけでは分離できず、そのラウンドは全件取り消しへ退避する（実測で確認）
>
> 「未検証の範囲」（レビューフェーズ以降）は**依然として未検証**である。

実機検証で見つかった 9 件を修正するための作業メモ。
不具合の内容・エビデンス・修正の方向は
[issue-113-cross-refactoring-trial-report.md](issue-113-cross-refactoring-trial-report.md) にある。
ここには**次に何をどの順で触るか**だけを書く。

## 編集対象

編集元だけを直し、配布物は生成する。

```
plugins/ndf-shared/skills/
├── cross-refactoring/
│   ├── scripts/refactor.py            # 検証・取り消し・状態記録
│   ├── scripts/prepare-worktrees.sh   # 手順書の配置
│   ├── prompts/propose.md             # 提案の語彙
│   └── docs/                          # 手順書本文（挙動を変えたら追従する）
└── cross-review/scripts/lib/          # 収束ループ共通層
```

```bash
bash scripts/build-runtime-plugins.sh   # 配布物を生成する
```

## 着手順

進行を止める不具合から直す。上 3 つが片付くまで実機での再検証はできない。

| 順 | 不具合 | 主な編集先 |
| --- | --- | --- |
| 1 | 取り消しが他項目のコミットと競合する | `refactor.py`（取り消し処理） |
| 2 | 取り消し失敗を握り潰して進行する | `refactor.py`（失敗時の中断）+ 進行スクリプト |
| 3 | 適用結果が状態ファイルへ残らない | `refactor.py`（検証の逐次記録） |
| 4 | 検証未通過の変更が公開されたまま残る | `refactor.py`（再送信の印） |
| 5 | 範囲外の変更を検証しない | `refactor.py`（範囲の検査） |
| 6 | 提案の記録が次ラウンドで上書きされる | 進行スクリプトの命名規則 |
| 7 | gemini が配置した手順書を読めない | `prepare-worktrees.sh` |
| 8 | 語彙の許容値をプロンプトが列挙しない | `propose.md` + 語彙集合の共有 |
| 9 | 初期化が CLI の認証を確認しない | `refactor.py`（初期化の事前確認） |

1 と 2 は同じ経路にあるため一緒に設計する。取り消しの単位を項目のままにするか、
ラウンド単位へ変えるかで 3 の記録内容も変わる。

## 先に決めること

- **取り消しの単位**。項目単位を保つなら、範囲内の全項目を新しい順にまとめて戻し、
  残す項目を積み直す形になる。ラウンド単位へ変えると実装は単純になるが、
  「合意済みの項目は残す」という設計方針を捨てることになる
- **配布物の同期を誰の責務にするか**。実装担当に同期させると範囲外の変更が生まれ、
  差分予算にも影響する。進行側が収束後にまとめて生成する形が範囲の指定と整合する

## 検証済みの回避策

不具合 7 の回避は**プラグインキャッシュにだけ**入れてある。編集元へは未反映。

```
~/.claude/plugins/cache/ai-plugins/ndf/8.1.0/skills/cross-refactoring/scripts/prepare-worktrees.sh
```

作業ディレクトリの `.gemini/` へ設定を置き、読み取り側の除外を無効にする。
`.gemini/` ごと無視して差分に出さない。設定の項目名は gemini の版で変わるため、
`context.fileFiltering` と `fileFiltering` の両方を書く（0.55.1 で読み取り成功を確認）。

キャッシュは再インストールで失われる。編集元へ移すこと。

## 再検証の手順

```bash
# 着手前テスト（実測 387 件成功 / 23.6 秒）
uv run --with pytest python -m pytest \
  plugins/ndf-shared/skills/cross-refactoring/tests \
  plugins/ndf-shared/skills/cross-review/tests -q

# 実機（`--scope` には現状固定テストの置き場所も含める。範囲は検証にも効く）
/ndf:cross-refactoring <PR番号> \
  --scope plugins/ndf-shared/skills/cross-refactoring/scripts \
          plugins/ndf-shared/skills/cross-refactoring/tests \
          plugins/ndf-shared/skills/cross-review/scripts/lib \
  --baseline-test "<上のテストコマンド>"
```

実行前に確認すること。

- 認証は `init` が確認する（不具合 9 は対応済み）。手で確認する必要は無くなった
- 進行を駆動する作業ディレクトリが対象ブランチを掴んでいない
  （同じブランチを 2 か所へ展開できないため初期化に失敗する）

## 未検証の範囲

レビューフェーズより後は一度も実行できていない。修正後の再検証では、
少なくとも次を通すまで確認する。

- レビュー担当 2 者の並列実行と指摘の投稿、承認判定
- 指摘の修正と再レビューの繰り返し、上限到達時の項目単位の見送り
- 実装担当の輪番
- 提案の重複率による収束判定
- 集計値の出力
