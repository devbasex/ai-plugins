#!/usr/bin/env node
"use strict";
// 台本の字数と尺を、ページごとと合計で出す。
//
//   node lib/script-time.js <台本> [--wpm 350] [--limit <分>] [--json]
//
// - 字数は読み上げ（`>` の行）の文字の数。空白と強調の記号（** と `）は数えない
// - 尺の秒は 字数 ÷ --wpm（1 分あたりの字数。既定 350）× 60。合計はページごとの値の和
// - 終了コード: 0 / 1 合計が --limit（分）を超えた・引数の失敗 / 2 台本の形の失敗
// 台本の読み取りは deck-frame.js と同じ関数を使う（スピーカーノートと同じ文を数える）。

const { readScript, DeckError, EXIT_INPUT } = require("./deck-frame");

function count(reading) {
  return [...reading.replace(/\s/g, "")].length;
}

function measure(pages, wpm) {
  const rows = pages.map((p) => {
    const chars = count(p.reading);
    return { no: p.no, title: p.title, chars, seconds: Math.round((chars / wpm) * 600) / 10, planned: p.seconds };
  });
  const chars = rows.reduce((a, r) => a + r.chars, 0);
  const seconds = Math.round(rows.reduce((a, r) => a + r.seconds, 0) * 10) / 10;
  const planned = rows.every((r) => r.planned == null) ? null : rows.reduce((a, r) => a + (r.planned || 0), 0);
  return { wpm, pages: rows, total: { pages: rows.length, chars, seconds, planned } };
}

function mmss(sec) {
  const s = Math.round(sec);
  return `${Math.floor(s / 60)}分${String(s % 60).padStart(2, "0")}秒`;
}

function table(m) {
  const lines = [
    `| # | 見出し | 字数 | 尺（秒） | 配分（秒） |`,
    `| ---: | --- | ---: | ---: | ---: |`,
  ];
  for (const r of m.pages) lines.push(`| ${r.no} | ${r.title.replace(/\|/g, "\\|")} | ${r.chars} | ${r.seconds.toFixed(1)} | ${r.planned == null ? "—" : r.planned} |`);
  const t = m.total;
  lines.push(`| 合計 | ${t.pages} ページ（${m.wpm} 字/分） | ${t.chars} | ${t.seconds.toFixed(1)}（${mmss(t.seconds)}） | ${t.planned == null ? "—" : t.planned} |`);
  return lines.join("\n");
}

function parseArgs(argv) {
  const a = { file: null, wpm: 350, limit: null, json: false };
  for (let i = 0; i < argv.length; i++) {
    const v = argv[i];
    if (v === "--json") a.json = true;
    else if (v === "--wpm" || v === "--limit") {
      const n = Number(argv[++i]);
      if (!(n > 0)) throw new DeckError(`${v} は正の数`, EXIT_INPUT);
      a[v.slice(2)] = n;
    } else if (v.startsWith("--")) throw new DeckError(`知らない引数: ${v}`, EXIT_INPUT);
    else if (!a.file) a.file = v;
    else throw new DeckError(`台本は 1 つだけ渡す: ${v}`, EXIT_INPUT);
  }
  if (!a.file) throw new DeckError("usage: node lib/script-time.js <台本> [--wpm 350] [--limit <分>] [--json]", EXIT_INPUT);
  return a;
}

function main() {
  try {
    const a = parseArgs(process.argv.slice(2));
    const m = measure(readScript(a.file), a.wpm);
    if (a.limit != null) m.limit = { minutes: a.limit, over: m.total.seconds > a.limit * 60 };
    console.log(a.json ? JSON.stringify(m) : table(m));
    if (m.limit && m.limit.over) {
      console.error(`尺が上限を超えた: ${mmss(m.total.seconds)} > ${a.limit} 分`);
      process.exit(1);
    }
  } catch (e) {
    console.error(e.message);
    process.exit(e instanceof DeckError ? e.code : 1);
  }
}

if (require.main === module) main();

module.exports = { count, measure, table };
