# 用語集: NDF の Google Workspace の操作（`ndf-google-workspace`）

この文書は `docs/glossary/glossary.json` から作る。手で直さない。索引は `docs/glossary.md` にある。

google-workspace の Skill が Google Workspace（Drive・Docs など）を操作するときの語

| 語 | 識別子 | 意味 | 廃止した語 | 廃止した識別子 | SSOT |
| --- | --- | --- | --- | --- | --- |
| gws | — | Google Workspace CLI（npm の @googleworkspace/cli）。Google の公式サポート外の CLI で、NDF の Google Workspace の操作はすべてこれで行う | — | — | `docs/specifications/ndf-google-workspace-gws.md` |
| gws の状態 | — | google-workspace の確認のスクリプト（gws-check.py）が返す 4 つの値（authenticated / missing / unauthenticated / uninstallable）。状態ごとに次の手が 1 つに決まる | — | — | `docs/specifications/ndf-google-workspace-gws.md` |
| 導入の同意のフラグ | — | gws の確認の引数 --install。利用者が gws の導入に同意したことを示し、付いたときだけ npm で全体インストールする | — | — | `docs/specifications/ndf-next-gws-subcommands.md` |
