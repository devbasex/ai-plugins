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
   * new は課題から始め、resume は保存した状態から続け、loop は開発ループの周回を新しく始め、release はリリースの実行の手（RELEASE_STEP）を流す
   */
  START: "new" | "resume" | "loop" | "release";
  /**
   * スプリントへ入れる課題の番号（カンマ区切り）
   */
  ISSUES?: string;
  /**
   * 答えの出た承認の依頼の識別子。答えの本文は載せない。無い resume は答えを書かずに続ける
   */
  APPROVAL_REQUEST_ID?: string;
  /**
   * NDF の開発ループの名前。loop と、ループの実行の resume に載せる
   */
  LOOP_NAME?: string;
  /**
   * 選定の対象のマイルストーンの題を , で結ぶ
   */
  LOOP_MILESTONES?: string;
  /**
   * 単位ごとに数か default（その単位をリポジトリの既定へ戻す）を並べる。並べない単位と、LOOP_BUDGET が無いときは NDF の記録の値のまま（新しいループでは NDF の既定）
   */
  LOOP_BUDGET?: string;
  /**
   * 同じリポジトリの別の実行が持つ課題の番号（カンマ区切り）。ループの選定から外す
   */
  EXCLUDE_ISSUES?: string;
  /**
   * 人の判断待ちへの答え（<番号>=approved|rejected、承認なら #<PR の番号> を続けてよい）を , で結ぶ
   */
  LOOP_ANSWERS?: string;
  /**
   * リリースの実行のまとめたリリースの連番（#2439）
   */
  RELEASE_SEQ?: string;
  /**
   * リリースの実行の手。present / represent は提示を作り、confirm:<k> は確定した提示 k のリリースを流し、continue は止まったプランを続ける
   */
  RELEASE_STEP?: "present" | "represent" | "confirm:1" | "confirm:2" | "continue";
  /**
   * 手動の実行の再開で、答えの出たリリースの依頼の id
   */
  RELEASE_REQUEST_ID?: string;
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
