// 生成したファイル。手で直さない。定義（schemas/）を直して `npm run generate -w contracts` で作り直す

/**
 * 実行の制御からランナーへ渡す実行ジョブの指示（確定仕様の「実行ジョブの指示」）。環境変数と、形態 ① の受け取りの応答で同じ形にするため、値はどれも文字列
 */
export type RunJobInstruction = {
  /**
   * 事象の宛先
   */
  SPRINT_RUN_ID: string;
  /**
   * 冪等の鍵の頭
   */
  JOB_ID: string;
  /**
   * 取り込むリポジトリ（owner/name）
   */
  REPO: string;
  /**
   * スプリントの開始で固定した NDF の版
   */
  NDF_VERSION: string;
  /**
   * new は課題から始め、resume は保存した状態から続ける
   */
  START: "new" | "resume";
  /**
   * スプリントへ入れる課題の番号（カンマ区切り）
   */
  ISSUES?: string;
  /**
   * 答えの出た承認の依頼の識別子。答えの本文は載せない
   */
  APPROVAL_REQUEST_ID?: string;
  /**
   * 進捗の受け口の runner の経路（POST /ingest/events）の URL
   */
  INGEST_URL: string;
  /**
   * このジョブだけに効く実行ジョブの token の置き場
   */
  INGEST_TOKEN_SECRET_ARN: string;
  /**
   * スプリントの開始でアカウントから写した実行の形態
   */
  EXECUTION_MODE: "local" | "byok" | "managed";
  /**
   * 利用者の Anthropic API キーか、引き受けるロールの ARN の置き場
   */
  MODEL_CREDENTIAL_SECRET_ARN?: string;
};
