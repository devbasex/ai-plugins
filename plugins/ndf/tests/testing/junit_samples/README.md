# JUnit の XML の見本の出所

`tests/testing/test_junit.py` が読む見本である。どの見本も、落ちたテスト・通ったテスト・`<skipped>` のテストを含む。

| ファイル | 出所 | 書かせ方 |
| --- | --- | --- |
| `vitest.xml` | 実際に動かして書かせた（vitest 5.1.0、Node.js 26.13.0。2026-11-15） | `npx vitest run web/src/sum.test.ts --reporter=junit --outputFile=…`（既定の設定） |
| `jest-file.xml` | 実際に動かして書かせた（jest 30.5.2・jest-junit 17.0.0。2026-11-15） | `JEST_JUNIT_ADD_FILE_ATTRIBUTE=true npx jest --reporters=jest-junit`（`addFileAttribute` を有効にした形） |
| `jest-default.xml` | 同上 | `npx jest --reporters=jest-junit`（既定の設定。testcase にファイルの属性が無い） |
| `playwright.xml` | 実際に動かして書かせた（@playwright/test 1.65.1。2026-11-15） | `testDir: "e2e"`、`reporter: [["junit", …]]`（既定の設定。testsuite の `name` が `testDir` からの相対パス） |
| `pytest-xunit1.xml` | 実際に動かして書かせた（pytest 9.0.3。2026-11-15） | `pytest -o junit_family=xunit1 --junitxml=…` |
| `pytest-xunit2.xml` | 同上 | `pytest -o junit_family=xunit2 --junitxml=…`（pytest の既定。testcase にファイルの属性が無い） |
| `phpunit.xml` | PHPUnit の公式のソースが書く形（`src/Logging/JUnit/JunitXmlLogger.php` の testsuite と testcase の `file` 属性。2026-11-15 に読んだ）。手元に PHP が無く、動かしていない | `phpunit --log-junit …` の形を、GitHub Actions の作業ディレクトリ（`/home/runner/work/app/app`）の絶対パスで書いた |

書かせた見本は、`<failure>` の本文（スタックトレース）と `hostname` を短くし、要素と属性はそのまま残した。
