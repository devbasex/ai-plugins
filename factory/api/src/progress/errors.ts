// 進捗の受け口の失敗。本文は #2170 の ApiError と同じ {"error": {"code", "message"}}
import { ApiError } from "../errors.ts";

// 400。message は失敗の位置と規則の名前だけで、値を含めない（決定 11）
export class InvalidEventError extends ApiError {
  constructor(message: string) {
    super(400, "invalid_event", message);
  }
}

// 401。理由によらず同じ本文（I30）
export class IngestUnauthorizedError extends ApiError {
  constructor() {
    super(401, "unauthorized", "認証できません");
  }
}

// 403。事象・依頼・スプリントの実行の有無を明かさないため、理由によらず同じ本文（I30）
export class IngestForbiddenError extends ApiError {
  constructor() {
    super(403, "forbidden", "この事象を書く権限がありません");
  }
}

// 503。保存・保存済みの答え・秘密を読み書きできない。送り手は同じ事象のまま送り直す
export class IngestUnavailableError extends ApiError {
  constructor() {
    super(503, "ingest_unavailable", "時間をおいて送り直してください");
  }
}

// appendControlEvent（#2186 が呼ぶ内側の関数）の失敗。HTTP の経路には出ない
export class ProgressEventError extends Error {
  constructor(
    readonly code: "invalid_event" | "forbidden",
    message: string,
  ) {
    super(message);
    this.name = "ProgressEventError";
  }
}
