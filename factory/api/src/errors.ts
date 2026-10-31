// API が返すエラー。本文は {"error": {"code", "message"}} に揃える（docs/specifications/ndf-factory-accounts-and-plans.md の「API の経路」）
export type ErrorStatus = 400 | 401 | 403 | 404 | 409 | 422 | 503;

export class ApiError extends Error {
  constructor(
    readonly status: ErrorStatus,
    readonly code: string,
    message: string,
    readonly details?: Record<string, unknown>,
  ) {
    super(message);
    this.name = new.target.name;
  }

  body(): { error: { code: string; message: string; details?: Record<string, unknown> } } {
    return { error: { code: this.code, message: this.message, ...(this.details ? { details: this.details } : {}) } };
  }
}

export class AuthError extends ApiError {
  constructor() {
    super(401, "unauthorized", "ログインし直してください");
  }
}

// アカウントの有無も中身も出さないため、理由にかかわらず同じ本文にする（I18）
export class ForbiddenError extends ApiError {
  constructor() {
    super(403, "forbidden", "このアカウントを見る権限がありません");
  }
}

export class InvalidRequestError extends ApiError {
  constructor(message = "要求の形が正しくありません", code = "invalid_request", details?: Record<string, unknown>) {
    super(400, code, message, details);
  }
}

export class ConflictError extends ApiError {
  constructor(code: string, message: string, details?: Record<string, unknown>) {
    super(409, code, message, details);
  }
}

export class NotFoundError extends ApiError {
  constructor(code: string, message: string) {
    super(404, code, message);
  }
}

export class UnprocessableError extends ApiError {
  constructor(code: string, message: string) {
    super(422, code, message);
  }
}

export class ServiceUnavailableError extends ApiError {
  constructor(code: string) {
    super(503, code, "時間をおいてやり直してください");
  }
}

export class AccountUnavailableError extends ApiError {
  constructor() {
    super(503, "account_unavailable", "時間をおいてやり直してください");
  }
}

// setPlan（#2174 が呼ぶ内側の関数）の失敗。HTTP の経路には出ない（E6）
export class PlanError extends Error {
  constructor(
    readonly code: "unknown_plan" | "plan_kind_mismatch" | "account_not_found",
    message: string,
  ) {
    super(message);
    this.name = "PlanError";
  }
}
