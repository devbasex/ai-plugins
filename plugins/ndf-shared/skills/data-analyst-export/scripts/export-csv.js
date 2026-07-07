#!/usr/bin/env node

/**
 * JSON配列をCSV形式に変換するスクリプト
 *
 * 使用方法:
 *   node export-csv.js input.json output.csv [options]
 *
 * オプション:
 *   --delimiter=","  : デリミタ（デフォルト: カンマ）
 *   --bom           : UTF-8 BOMを付与（Excel互換）
 */

const fs = require('fs');

/**
 * JSON配列をCSV文字列に変換
 */
function jsonToCSV(data, options = {}) {
  if (!Array.isArray(data) || data.length === 0) {
    throw new Error('データは空でない配列である必要があります');
  }

  const delimiter = options.delimiter || ',';
  const includeHeaders = options.includeHeaders !== false;

  // ヘッダー行を生成
  const headers = Object.keys(data[0]);
  const headerLine = headers.map(h => escapeCSVValue(h, delimiter)).join(delimiter);

  // データ行を生成
  const dataLines = data.map(row => {
    return headers.map(header => {
      const value = row[header];
      return escapeCSVValue(value, delimiter);
    }).join(delimiter);
  });

  // ヘッダーとデータを結合
  const lines = includeHeaders ? [headerLine, ...dataLines] : dataLines;
  return lines.join('\n');
}

/**
 * CSV値をエスケープ
 */
function escapeCSVValue(value, delimiter) {
  if (value === null || value === undefined) {
    return '';
  }

  const stringValue = String(value);

  // デリミタ、改行、ダブルクォートを含む場合はクォートで囲む
  const needsQuoting = stringValue.includes(delimiter) ||
                       stringValue.includes('\n') ||
                       stringValue.includes('\r') ||
                       stringValue.includes('"');

  if (needsQuoting) {
    // ダブルクォートをエスケープ（""に変換）
    const escapedValue = stringValue.replace(/"/g, '""');
    return `"${escapedValue}"`;
  }

  return stringValue;
}

/**
 * CSVファイルに書き込み
 */
function writeCSV(csv, outputPath, options = {}) {
  let content = csv;

  // UTF-8 BOMを付与（Excel互換）
  if (options.bom) {
    content = '\uFEFF' + content;
  }

  fs.writeFileSync(outputPath, content, 'utf-8');
}

/**
 * メイン処理
 */
function main() {
  const args = process.argv.slice(2);

  if (args.length < 2) {
    console.error('使用方法: node export-csv.js <input.json> <output.csv> [options]');
    console.error('');
    console.error('オプション:');
    console.error('  --delimiter=","  : デリミタ（デフォルト: カンマ）');
    console.error('  --bom           : UTF-8 BOMを付与（Excel互換）');
    console.error('');
    console.error('例:');
    console.error('  node export-csv.js data.json output.csv');
    console.error('  node export-csv.js data.json output.tsv --delimiter="\\t"');
    console.error('  node export-csv.js data.json output.csv --bom');
    process.exit(1);
  }

  const inputPath = args[0];
  const outputPath = args[1];

  // オプションを解析
  const options = {};
  for (let i = 2; i < args.length; i++) {
    const arg = args[i];
    if (arg.startsWith('--delimiter=')) {
      options.delimiter = arg.split('=')[1];
      // エスケープシーケンスを処理
      options.delimiter = options.delimiter.replace(/\\t/g, '\t');
    } else if (arg === '--bom') {
      options.bom = true;
    }
  }

  try {
    // JSONファイルを読み込み
    console.log(`📖 JSONファイルを読み込み中: ${inputPath}`);
    const jsonData = fs.readFileSync(inputPath, 'utf-8');
    const data = JSON.parse(jsonData);

    // CSVに変換
    console.log('🔄 CSVに変換中...');
    const csv = jsonToCSV(data, options);

    // ファイルに書き込み
    console.log(`💾 CSVファイルを保存中: ${outputPath}`);
    writeCSV(csv, outputPath, options);

    console.log('');
    console.log('✅ CSVエクスポート完了');
    console.log(`   入力: ${inputPath}`);
    console.log(`   出力: ${outputPath}`);
    console.log(`   行数: ${data.length}行`);
    console.log(`   列数: ${Object.keys(data[0] || {}).length}列`);

  } catch (error) {
    console.error('❌ エラーが発生しました:', error.message);
    process.exit(1);
  }
}

// モジュールとしてエクスポート（他のスクリプトから使用可能）
if (require.main === module) {
  main();
} else {
  module.exports = { jsonToCSV, escapeCSVValue, writeCSV };
}
