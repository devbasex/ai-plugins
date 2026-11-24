# 用語集: このリポジトリの開発の検査（`repo-dev-checks`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

ai-plugins の開発で formatter と静的解析を手元と CI で走らせるときの語。NDF が利用者へ配る振る舞いではない

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| 検査のコマンド | — | 手元で formatter の確認と静的解析を、CI と同じバージョン・同じ設定で走らせる 1 つのコマンド | — | — | `docs/specifications/ndf-sprint-acceptance-check-and-prepush-fix.md` |
| 古いバイトコード | — | Python が、書き換えたソースを、前の内容から作った .pyc で読むこと。.pyc はソースの更新時刻（秒）と大きさだけで新しさを照らすため、同じ秒に同じ大きさで書き換えると見分けられない | — | — | `docs/specifications/cross-refactoring-test-stale-bytecode.md` |
| git を使うテストの基盤 | — | cross-refactoring のテストで、git のリポジトリの作業ディレクトリ・pytest の起動口・状態ファイルを用意する補助（crossref_helpers.build_git_flow） | — | — | `docs/specifications/cross-refactoring-test-stale-bytecode.md` |
| CLI のバージョンの一覧 | — | スモークの像へ入れる 4 ランタイムの CLI の版数と取得先と checksum を書いたファイル（tests/runtime-smoke/cli-versions.env）。バージョンを上げるときに書き換える唯一の場所 | — | — | `docs/specifications/runtime-smoke-cli-versions.md` |
| リポジトリ前提の検査 | — | リリース対象の Skill とスクリプトに ai-plugins にしか無い語が無いかを見る CI のジョブ `skill-repo-assumptions-check` | — | — | `docs/specifications/ndf-repo-assumptions-check.md` |
| 既知の当たり | — | リポジトリ前提の検査に当たるが、NDF 自身の保存先を読むなど違反でない箇所か、ルートの課題が直すまで残す箇所。語とファイルの組ごとに当たりの数を例外リストへ定義する | — | — | `docs/specifications/ndf-repo-assumptions-check.md` |
| import の輪 | — | NDF のスクリプトのモジュールの import を有向グラフにしたときの強連結成分。最上位の import だけで輪になるものを実行時の輪、関数の中の import などを含めて初めて輪になるものを遅延の輪と呼ぶ | — | — | `docs/specifications/ndf-scripts-import-cycles.md` |
| 一番下の層 | — | NDF のスクリプトのうち、他のモジュールから最も多く読まれる共通ライブラリ。結果を出す層 step_result がこれに当たる | — | — | `docs/specifications/ndf-scripts-import-cycles.md` |
| 終了コードの表 | — | NDF のスクリプトが使う終了コードの値と見出しの一覧。lib/exit_codes.py が持ち、結果を出す層 step_result が同じ名前で公開する | — | — | `docs/specifications/ndf-scripts-import-cycles.md` |
