// 生成したファイル。手で直さない。定義（schemas/）を直して `npm run generate -w contracts` で作り直す

/**
 * 進捗の受け口の応答の本文。202 は重複でも同じ本文、401・403 は理由によらず同じ本文
 */
export type IngestResponse = IngestAccepted | IngestError;

/**
 * 202。残した・同じ冪等の鍵が既にある・github で扱わないか宛先が引けない
 */
export interface IngestAccepted {
  accepted: true;
}
/**
 * 400 invalid_event・401 unauthorized・403 forbidden・503 ingest_unavailable
 */
export interface IngestError {
  error: {
    code: "invalid_event" | "unauthorized" | "forbidden" | "ingest_unavailable";
    message: string;
  };
}
