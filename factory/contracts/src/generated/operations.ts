// 生成したファイル。手で直さない。契約（openapi/）を直して `npm run generate -w contracts` で作り直す
import type { Operation } from "../operations.ts";

export const OPERATIONS = [
  {
    "operationId": "addSeat",
    "method": "POST",
    "path": "/accounts/{accountId}/seats",
    "kind": "create",
    "access": "human",
    "keys": [
      "user_id"
    ],
    "idField": null,
    "summary": "席を足す",
    "description": "チームの持ち主が、既に 1 度ログインした利用者を ID で指して席を足す。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "user_id",
        "seat_type"
      ],
      "properties": {
        "user_id": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        },
        "seat_type": {
          "type": "string",
          "enum": [
            "standard",
            "premium"
          ]
        }
      }
    },
    "source": "accounts.yaml"
  },
  {
    "operationId": "answerLoopPending",
    "method": "POST",
    "path": "/accounts/{accountId}/loops/answers",
    "kind": "action",
    "access": "human",
    "keys": [
      "loop_id",
      "issue"
    ],
    "idField": null,
    "summary": "人の判断待ちの課題に答える",
    "description": "答えは次のジョブの LOOP_ANSWERS に載り、ランナーが NDF の loop answer で書く。人の判断待ちに無い番号は 409 not_pending。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "loop_id",
        "issue",
        "decision"
      ],
      "additionalProperties": false,
      "properties": {
        "loop_id": {
          "type": "string",
          "minLength": 1
        },
        "issue": {
          "type": "integer",
          "minimum": 1
        },
        "decision": {
          "type": "string",
          "enum": [
            "approved",
            "rejected"
          ]
        },
        "pr": {
          "type": "integer",
          "minimum": 1
        }
      }
    },
    "source": "loops.yaml"
  },
  {
    "operationId": "cancelLoop",
    "method": "POST",
    "path": "/accounts/{accountId}/loops/cancel",
    "kind": "action",
    "access": "write",
    "keys": [
      "loop_id"
    ],
    "idField": null,
    "summary": "開発ループを止める",
    "description": "動いている実行があればスプリントの実行の取り消しと同じ形で止め、停止の後に cancelled へ移す。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "loop_id"
      ],
      "additionalProperties": false,
      "properties": {
        "loop_id": {
          "type": "string",
          "minLength": 1
        }
      }
    },
    "source": "loops.yaml"
  },
  {
    "operationId": "cancelSprintRun",
    "method": "POST",
    "path": "/accounts/{accountId}/sprint-runs/cancel",
    "kind": "action",
    "access": "write",
    "keys": [
      "sprint_run_id"
    ],
    "idField": null,
    "summary": "スプリントの実行を取り消す",
    "description": "動いている実行ジョブがあれば止め、停止の後に cancelled へ移す。main へマージした後のリリースの実行は取り消さず、止まったリリース（release_stalled）として残す（ジョブが無ければ 409 release_after_main）。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "sprint_run_id"
      ],
      "additionalProperties": false,
      "properties": {
        "sprint_run_id": {
          "type": "string",
          "minLength": 1
        }
      }
    },
    "source": "runs.yaml"
  },
  {
    "operationId": "changeSeat",
    "method": "PATCH",
    "path": "/accounts/{accountId}/seats",
    "kind": "update",
    "access": "human",
    "keys": [
      "user_id"
    ],
    "idField": null,
    "summary": "席の種類を変える",
    "description": "チームの持ち主だけが変えられる。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "user_id",
        "seat_type"
      ],
      "properties": {
        "user_id": {
          "type": "string",
          "minLength": 1,
          "maxLength": 128
        },
        "seat_type": {
          "type": "string",
          "enum": [
            "standard",
            "premium"
          ]
        }
      }
    },
    "source": "accounts.yaml"
  },
  {
    "operationId": "createDelegation",
    "method": "POST",
    "path": "/delegations",
    "kind": "create",
    "access": "human",
    "keys": [
      "idempotency_key"
    ],
    "idField": "delegation.credential_id",
    "summary": "委任の資格情報を発行する",
    "description": "値（ndfd_<資格情報の ID>_<秘密>）は発行の結果で 1 度だけ返し、平文を保存しない。同じ冪等の鍵の 2 回目は同じ資格情報を返し、credential を null にする。期限は 300〜3,600 秒。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "client_name",
        "scope",
        "expires_in_seconds",
        "idempotency_key"
      ],
      "additionalProperties": false,
      "properties": {
        "client_name": {
          "type": "string",
          "minLength": 1,
          "maxLength": 64,
          "description": "人が付けるクライアントの名前。操作の記録には写さない"
        },
        "scope": {
          "type": "string",
          "description": "read（読み取りだけ）・read_write（読み書き）。どちらも人だけが確定できる操作は呼べない",
          "enum": [
            "read",
            "read_write"
          ]
        },
        "expires_in_seconds": {
          "type": "integer",
          "minimum": 300,
          "maximum": 3600
        },
        "idempotency_key": {
          "type": "string",
          "pattern": "^[0-9A-Za-z-]{1,64}$",
          "description": "作成の冪等の鍵"
        }
      }
    },
    "source": "delegations.yaml"
  },
  {
    "operationId": "createTeam",
    "method": "POST",
    "path": "/teams",
    "kind": "create",
    "access": "human",
    "keys": [
      "idempotency_key"
    ],
    "idField": "account_id",
    "summary": "チームのアカウントを作る",
    "description": "作った利用者がチームの持ち主になり、持ち主の席が 1 つできる。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "idempotency_key",
        "name",
        "seat_type"
      ],
      "properties": {
        "idempotency_key": {
          "type": "string",
          "pattern": "^[0-9A-Za-z-]{1,64}$",
          "description": "作成の冪等の鍵。同じ利用者・同じ操作・同じ鍵の作成は 24 時間の間 1 件にする"
        },
        "name": {
          "type": "string",
          "minLength": 1,
          "maxLength": 64
        },
        "seat_type": {
          "type": "string",
          "enum": [
            "standard",
            "premium"
          ]
        }
      }
    },
    "source": "accounts.yaml"
  },
  {
    "operationId": "deleteModelCredential",
    "method": "DELETE",
    "path": "/accounts/{accountId}/model-credential",
    "kind": "delete",
    "access": "human",
    "keys": [],
    "idField": null,
    "summary": "持ち込んだキーを消す",
    "description": "復元の期間を置かずに消す（決定 14）。無くても成功の結果を返す。",
    "parameters": [],
    "itemSchema": null,
    "source": "runs.yaml"
  },
  {
    "operationId": "getAccount",
    "method": "GET",
    "path": "/accounts",
    "kind": "select",
    "access": "read",
    "keys": [
      "account_id"
    ],
    "idField": null,
    "summary": "アカウントの料金プラン・実行の形態・使える量・席",
    "description": "個人のアカウントの持ち主か、チームの席を持つ利用者だけが読める。",
    "parameters": [
      {
        "name": "account_id",
        "required": true,
        "array": true,
        "description": "要素の鍵。繰り返して 1〜100 個を指す",
        "schema": {
          "type": "array",
          "minItems": 1,
          "maxItems": 100,
          "items": {
            "type": "string"
          }
        }
      }
    ],
    "itemSchema": null,
    "source": "accounts.yaml"
  },
  {
    "operationId": "getAuthorizeUrl",
    "method": "GET",
    "path": "/accounts/{accountId}/github/authorize-url",
    "kind": "list",
    "access": "read",
    "keys": [],
    "idField": null,
    "summary": "GitHub App の利用者の認可の URL",
    "description": "state は利用者とアカウントを署名した 10 分の値。持ち主だけが使える。",
    "parameters": [],
    "itemSchema": null,
    "source": "runs.yaml"
  },
  {
    "operationId": "getBacklog",
    "method": "GET",
    "path": "/accounts/{accountId}/backlogs",
    "kind": "select",
    "access": "read",
    "keys": [
      "repository_id"
    ],
    "idField": null,
    "summary": "バックログ",
    "description": "リポジトリの着手の順位の表（マイルストーンの説明の表）と、そのリポジトリの実行（新しい順に 50 件まで）の棚卸しの工程の記録。 読むだけで、課題・マイルストーン・ボードへ書かない。登録の無いリポジトリの要素も、他人のアカウントと同じ本文の forbidden の結果になる。",
    "parameters": [
      {
        "name": "repository_id",
        "required": true,
        "array": true,
        "description": "要素の鍵。繰り返して 1〜100 個を指す",
        "schema": {
          "type": "array",
          "minItems": 1,
          "maxItems": 100,
          "items": {
            "type": "integer",
            "minimum": 1
          }
        }
      }
    ],
    "itemSchema": null,
    "source": "runs.yaml"
  },
  {
    "operationId": "getInstallUrl",
    "method": "GET",
    "path": "/accounts/{accountId}/github/install-url",
    "kind": "list",
    "access": "read",
    "keys": [],
    "idField": null,
    "summary": "GitHub App の導入の画面の URL",
    "description": "GitHub App の導入の URL。要素 1 個の配列で返す。",
    "parameters": [],
    "itemSchema": null,
    "source": "runs.yaml"
  },
  {
    "operationId": "getLoop",
    "method": "GET",
    "path": "/accounts/{accountId}/loops/detail",
    "kind": "select",
    "access": "read",
    "keys": [
      "loop_id"
    ],
    "idField": null,
    "summary": "開発ループの詳細",
    "description": "締めた周回・周回ごとの実行と区間の数・人の判断待ちと答え。",
    "parameters": [
      {
        "name": "loop_id",
        "required": true,
        "array": true,
        "description": "要素の鍵。繰り返して 1〜100 個を指す",
        "schema": {
          "type": "array",
          "minItems": 1,
          "maxItems": 100,
          "items": {
            "type": "string"
          }
        }
      }
    ],
    "itemSchema": null,
    "source": "loops.yaml"
  },
  {
    "operationId": "getMe",
    "method": "GET",
    "path": "/me",
    "kind": "list",
    "access": "read",
    "keys": [],
    "idField": null,
    "summary": "自分の利用者と、席を持つアカウントの一覧",
    "description": "初めての要求では、Free・local の個人のアカウントを 1 つ作る。",
    "parameters": [],
    "itemSchema": null,
    "source": "accounts.yaml"
  },
  {
    "operationId": "getModelCredential",
    "method": "GET",
    "path": "/accounts/{accountId}/model-credential",
    "kind": "list",
    "access": "read",
    "keys": [],
    "idField": null,
    "summary": "持ち込んだキーの有無",
    "description": "登録済みか・種類・更新の時刻・外部 ID だけを返し、値も置き場の ARN も返さない（I38）。",
    "parameters": [],
    "itemSchema": null,
    "source": "runs.yaml"
  },
  {
    "operationId": "getSprintRunDetail",
    "method": "GET",
    "path": "/accounts/{accountId}/sprint-runs/detail",
    "kind": "select",
    "access": "read",
    "keys": [
      "sprint_run_id"
    ],
    "idField": null,
    "summary": "スプリントの詳細",
    "description": "フェーズの欄・課題ごとの工程の記録と Pull Request と CI・承認の欄・使用量。進捗の事象から導き、課題の本文・ボード・GitHub を読まない。 工程の中身の本文（触ったファイル・決めたこと・出力）は返さず、工程の中身の操作（getStepDetail）で読む。 step.recorded は新しい方から 2,000 件までで、省いた数を omitted_steps に返す。 無い実行・アカウントに属さない実行の要素は、他人のアカウントと同じ本文の forbidden の結果になる。",
    "parameters": [
      {
        "name": "sprint_run_id",
        "required": true,
        "array": true,
        "description": "要素の鍵。繰り返して 1〜100 個を指す",
        "schema": {
          "type": "array",
          "minItems": 1,
          "maxItems": 100,
          "items": {
            "type": "string"
          }
        }
      }
    ],
    "itemSchema": null,
    "source": "runs.yaml"
  },
  {
    "operationId": "getStepDetail",
    "method": "GET",
    "path": "/accounts/{accountId}/sprint-runs/steps",
    "kind": "select",
    "access": "read",
    "keys": [
      "step_key"
    ],
    "idField": null,
    "summary": "工程の中身",
    "description": "1 件の工程の記録の、触ったファイル・コミットの範囲・決めたこと・出力（作業の報告かログ）と、差分とファイルのリンク。 step_key は事象の idempotency_key。記録の無い項目は null。 無い工程・別の実行の工程の要素も、他人のアカウントと同じ本文の forbidden の結果になる。",
    "parameters": [
      {
        "name": "sprint_run_id",
        "required": true,
        "array": false,
        "description": "工程を持つ実行。他人の実行なら要求全体が 403",
        "schema": {
          "type": "string"
        }
      },
      {
        "name": "step_key",
        "required": true,
        "array": true,
        "description": "工程の鍵（事象の idempotency_key）。繰り返して 1〜100 個を指す",
        "schema": {
          "type": "array",
          "minItems": 1,
          "maxItems": 100,
          "items": {
            "type": "string"
          }
        }
      }
    ],
    "itemSchema": null,
    "source": "runs.yaml"
  },
  {
    "operationId": "listApprovalRequests",
    "method": "GET",
    "path": "/accounts/{accountId}/sprint-runs/approval-requests",
    "kind": "list",
    "access": "read",
    "keys": [],
    "idField": null,
    "summary": "承認待ちの一覧",
    "description": "アカウントのスプリントの実行のうち、答えの無い承認の依頼（ゲート 1・ゲート 2・判断）を、ループの名前と周回の番号とともに 待ち始めた日時の古い順（同じ日時は実行の ID の順）に返す。1 つの実行に 0 か 1 行で、状態が awaiting_approval で待つ理由が無く、 最後の gate.reached より後に同じ種類の gate.answered が無い実行だけが載る。読むだけで、承認を確定しない。分割せず 1 回で返す。",
    "parameters": [],
    "itemSchema": null,
    "source": "runs.yaml"
  },
  {
    "operationId": "listDelegations",
    "method": "GET",
    "path": "/delegations",
    "kind": "list",
    "access": "read",
    "keys": [],
    "idField": null,
    "summary": "自分が発行した委任の資格情報の一覧",
    "description": "新しい順に 100 件まで。値と秘密のハッシュは返さない。",
    "parameters": [],
    "itemSchema": null,
    "source": "delegations.yaml"
  },
  {
    "operationId": "listLoops",
    "method": "GET",
    "path": "/accounts/{accountId}/loops",
    "kind": "list",
    "access": "read",
    "keys": [],
    "idField": null,
    "summary": "リポジトリの開発ループの一覧",
    "description": "新しい順に 50 件。",
    "parameters": [
      {
        "name": "repository_id",
        "required": true,
        "array": false,
        "description": null,
        "schema": {
          "type": "integer",
          "minimum": 1
        }
      }
    ],
    "itemSchema": null,
    "source": "loops.yaml"
  },
  {
    "operationId": "listRepositories",
    "method": "GET",
    "path": "/accounts/{accountId}/repositories",
    "kind": "list",
    "access": "read",
    "keys": [],
    "idField": null,
    "summary": "登録したリポジトリの一覧",
    "description": "リポジトリごとの終わりの状態でないスプリントの実行の数を添える。",
    "parameters": [],
    "itemSchema": null,
    "source": "runs.yaml"
  },
  {
    "operationId": "listRepositoryApprovalRequests",
    "method": "GET",
    "path": "/accounts/{accountId}/approval-requests",
    "kind": "select",
    "access": "read",
    "keys": [
      "repository_id"
    ],
    "idField": null,
    "summary": "リポジトリの承認と問いの一覧",
    "description": "同じリポジトリの承認待ちの実行の承認の依頼（ゲート 1・ゲート 2・判断）、止まったリリース、開発ループの人の判断待ちを、 作った時刻の古い順に 1 つの並びで返す。承認の依頼の中身（承認資料・答え）は承認の依頼の経路が持ち、ここは識別子と要約だけを返す。",
    "parameters": [
      {
        "name": "repository_id",
        "required": true,
        "array": true,
        "description": "要素の鍵。繰り返して 1〜100 個を指す",
        "schema": {
          "type": "array",
          "minItems": 1,
          "maxItems": 100,
          "items": {
            "type": "integer",
            "minimum": 1
          }
        }
      }
    ],
    "itemSchema": null,
    "source": "organizer.yaml"
  },
  {
    "operationId": "listRepositoryReleases",
    "method": "GET",
    "path": "/accounts/{accountId}/releases",
    "kind": "list",
    "access": "read",
    "keys": [],
    "idField": null,
    "summary": "まとめたリリースの履歴",
    "description": "新しい順に limit 件（既定 20、最大 100）。提示・確定・結果と、待ち・組む・出し直し・確定・結果の記録を返す。",
    "parameters": [
      {
        "name": "repository_id",
        "required": true,
        "array": false,
        "description": null,
        "schema": {
          "type": "integer",
          "minimum": 1
        }
      },
      {
        "name": "limit",
        "required": false,
        "array": false,
        "description": null,
        "schema": {
          "type": "integer",
          "minimum": 1,
          "maximum": 100
        }
      }
    ],
    "itemSchema": null,
    "source": "organizer.yaml"
  },
  {
    "operationId": "listSprintRuns",
    "method": "GET",
    "path": "/accounts/{accountId}/sprint-runs",
    "kind": "list",
    "access": "read",
    "keys": [],
    "idField": null,
    "summary": "スプリントの実行の一覧",
    "description": "新しい順に 50 件。repository_id で絞れる。各行の今のフェーズ・今の工程・最後の動きは進捗の事象から導く。",
    "parameters": [
      {
        "name": "repository_id",
        "required": false,
        "array": false,
        "description": "省くとアカウントの新しい順の 50 件",
        "schema": {
          "type": "integer",
          "minimum": 1
        }
      }
    ],
    "itemSchema": null,
    "source": "runs.yaml"
  },
  {
    "operationId": "postIssueCandidates",
    "method": "POST",
    "path": "/accounts/{accountId}/issue-candidates",
    "kind": "action",
    "access": "write",
    "keys": [
      "repository_id"
    ],
    "idField": null,
    "summary": "AI が選んだ課題の候補",
    "description": "棚卸しの順位の表（無ければ開いている課題の新しい順）から、サービスの Bedrock で採否と理由を作る。保存しない。言語モデルを呼び費用が掛かるため、扱いは書き込み。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "properties": {
        "repository_id": {
          "type": "integer",
          "minimum": 1
        },
        "exclude": {
          "type": "array",
          "items": {
            "type": "integer",
            "minimum": 1
          }
        }
      },
      "required": [
        "repository_id"
      ]
    },
    "source": "runs.yaml"
  },
  {
    "operationId": "postRepositoryCandidates",
    "method": "POST",
    "path": "/accounts/{accountId}/github/candidates",
    "kind": "action",
    "access": "write",
    "keys": [],
    "idField": null,
    "summary": "認可の戻りから登録の候補を返す",
    "description": "導入の対象で利用者の届くリポジトリと、10 分の候補の証明を返す。利用者の token は保存しない。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "code",
        "state"
      ],
      "properties": {
        "code": {
          "type": "string",
          "minLength": 1
        },
        "state": {
          "type": "string",
          "minLength": 1
        }
      }
    },
    "source": "runs.yaml"
  },
  {
    "operationId": "putModelCredential",
    "method": "PUT",
    "path": "/accounts/{accountId}/model-credential",
    "kind": "update",
    "access": "human",
    "keys": [],
    "idField": null,
    "summary": "キーを保存・差し替える",
    "description": "Anthropic API キーか AWS のロールの ARN と地域のどちらか 1 つ。持ち主だけが使える。",
    "parameters": [],
    "itemSchema": {
      "oneOf": [
        {
          "type": "object",
          "required": [
            "kind",
            "api_key"
          ],
          "additionalProperties": false,
          "properties": {
            "kind": {
              "const": "anthropic_api_key"
            },
            "api_key": {
              "type": "string",
              "minLength": 1,
              "maxLength": 512
            }
          }
        },
        {
          "type": "object",
          "required": [
            "kind",
            "role_arn",
            "region"
          ],
          "additionalProperties": false,
          "properties": {
            "kind": {
              "const": "aws_role"
            },
            "role_arn": {
              "type": "string",
              "maxLength": 2048,
              "pattern": "^arn:aws[a-z-]*:iam::\\d{12}:role/.+$"
            },
            "region": {
              "type": "string",
              "maxLength": 32,
              "pattern": "^[a-z]{2}(-[a-z]+)+-\\d$"
            }
          }
        }
      ]
    },
    "source": "runs.yaml"
  },
  {
    "operationId": "registerRepository",
    "method": "POST",
    "path": "/accounts/{accountId}/repositories",
    "kind": "create",
    "access": "write",
    "keys": [
      "repository_id"
    ],
    "idField": null,
    "summary": "リポジトリを登録する",
    "description": "候補の証明と、登録の直前の導入の token の作成で確かめる（I33）。同じリポジトリは 409（I32）。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "proof",
        "repository_id"
      ],
      "properties": {
        "proof": {
          "type": "string"
        },
        "repository_id": {
          "type": "integer",
          "minimum": 1
        }
      }
    },
    "source": "runs.yaml"
  },
  {
    "operationId": "removeRepository",
    "method": "DELETE",
    "path": "/accounts/{accountId}/repositories",
    "kind": "delete",
    "access": "write",
    "keys": [
      "repository_id"
    ],
    "idField": null,
    "summary": "リポジトリの登録を外す",
    "description": "終わりの状態でないスプリントの実行があれば 409（I36）。持ち主だけが使える。",
    "parameters": [
      {
        "name": "repository_id",
        "required": true,
        "array": true,
        "description": "要素の鍵。繰り返して 1〜100 個を指す",
        "schema": {
          "type": "array",
          "minItems": 1,
          "maxItems": 100,
          "items": {
            "type": "integer",
            "minimum": 1
          }
        }
      }
    ],
    "itemSchema": null,
    "source": "runs.yaml"
  },
  {
    "operationId": "removeSeat",
    "method": "DELETE",
    "path": "/accounts/{accountId}/seats",
    "kind": "delete",
    "access": "human",
    "keys": [
      "user_id"
    ],
    "idField": null,
    "summary": "席を外す",
    "description": "チームの持ち主だけが外せる。持ち主自身の席は外せない。",
    "parameters": [
      {
        "name": "user_id",
        "required": true,
        "array": true,
        "description": "要素の鍵。繰り返して 1〜100 個を指す",
        "schema": {
          "type": "array",
          "minItems": 1,
          "maxItems": 100,
          "items": {
            "type": "string"
          }
        }
      }
    ],
    "itemSchema": null,
    "source": "accounts.yaml"
  },
  {
    "operationId": "resolveStalledRelease",
    "method": "POST",
    "path": "/accounts/{accountId}/releases/resolve",
    "kind": "action",
    "access": "human",
    "keys": [
      "repository_id",
      "seq"
    ],
    "idField": null,
    "summary": "止まったリリースを解く",
    "description": "リリースの実行が失敗して止まったまとめたリリースを「続きから流す」（continue）か「差し戻す」（reject）で解く。 止まっていなければ 409 not_stalled、main へのマージの後の差し戻しは 409 release_after_main（I63）。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "repository_id",
        "seq",
        "choice"
      ],
      "properties": {
        "repository_id": {
          "type": "integer",
          "minimum": 1
        },
        "seq": {
          "type": "integer",
          "minimum": 1
        },
        "choice": {
          "type": "string",
          "enum": [
            "continue",
            "reject"
          ]
        },
        "body": {
          "type": "string",
          "maxLength": 4000
        }
      }
    },
    "source": "organizer.yaml"
  },
  {
    "operationId": "revokeDelegation",
    "method": "DELETE",
    "path": "/delegations",
    "kind": "delete",
    "access": "human",
    "keys": [
      "credential_id"
    ],
    "idField": null,
    "summary": "委任の資格情報を取り消す",
    "description": "取り消しはその後の最初の要求から効く。期限切れと取り消し済みの資格情報は何も変えずに成功を返す。他人の資格情報は forbidden の結果になる。",
    "parameters": [
      {
        "name": "credential_id",
        "required": true,
        "array": true,
        "description": "要素の鍵。繰り返して 1〜100 個を指す",
        "schema": {
          "type": "array",
          "minItems": 1,
          "maxItems": 100,
          "items": {
            "type": "string"
          }
        }
      }
    ],
    "itemSchema": null,
    "source": "delegations.yaml"
  },
  {
    "operationId": "setExecutionMode",
    "method": "PATCH",
    "path": "/accounts",
    "kind": "update",
    "access": "write",
    "keys": [
      "account_id"
    ],
    "idField": null,
    "summary": "実行の形態を変える",
    "description": "アカウントの持ち主だけが変えられる。今と同じ値なら履歴を足さずに 200 を返す。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "account_id",
        "execution_mode"
      ],
      "properties": {
        "account_id": {
          "type": "string",
          "minLength": 1
        },
        "execution_mode": {
          "type": "string",
          "description": "local（手元のマシン）・byok（キーの持ち込み）・managed（サービスの Bedrock）",
          "enum": [
            "local",
            "byok",
            "managed"
          ]
        }
      }
    },
    "source": "accounts.yaml"
  },
  {
    "operationId": "startLoop",
    "method": "POST",
    "path": "/accounts/{accountId}/loops",
    "kind": "create",
    "access": "write",
    "keys": [
      "idempotency_key"
    ],
    "idField": "loop_id",
    "summary": "開発ループを始める",
    "description": "マイルストーンの持ち分とループの行を 1 回のトランザクションで書き、続けて最初の周回を起こす。 枠で待つときも成功の結果（between_laps）。マイルストーンが同じリポジトリの終わりの状態でないループと重なると 409 milestone_in_use（details.milestones・details.loop_name）、名前が重なると 409 loop_name_in_use。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "repository_id",
        "idempotency_key",
        "name",
        "milestones"
      ],
      "additionalProperties": false,
      "properties": {
        "repository_id": {
          "type": "integer",
          "minimum": 1
        },
        "idempotency_key": {
          "type": "string",
          "pattern": "^[0-9A-Za-z-]{1,64}$",
          "description": "作成の冪等の鍵。同じ利用者・同じ操作・同じ鍵の作成は 24 時間の間 1 件にする"
        },
        "name": {
          "type": "string",
          "pattern": "^[0-9A-Za-z][0-9A-Za-z_-]{0,63}$"
        },
        "milestones": {
          "type": "array",
          "minItems": 1,
          "maxItems": 10,
          "uniqueItems": true,
          "items": {
            "type": "string",
            "minLength": 1,
            "maxLength": 100,
            "pattern": "^[^,]+$"
          }
        },
        "budget": {
          "type": "object",
          "description": "単位ごとに数か null。null の単位はリポジトリの予算の既定（.ndf/pace.json の loop.budget）に従う",
          "additionalProperties": false,
          "properties": {
            "laps": {
              "type": [
                "number",
                "null"
              ],
              "exclusiveMinimum": 0
            },
            "usd": {
              "type": [
                "number",
                "null"
              ],
              "exclusiveMinimum": 0
            },
            "hours": {
              "type": [
                "number",
                "null"
              ],
              "exclusiveMinimum": 0
            }
          }
        }
      }
    },
    "source": "loops.yaml"
  },
  {
    "operationId": "startNextLap",
    "method": "POST",
    "path": "/accounts/{accountId}/loops/next-lap",
    "kind": "action",
    "access": "write",
    "keys": [
      "loop_id"
    ],
    "idField": null,
    "summary": "次の周回を始める（形態 local）",
    "description": "次の周回を始める操作を待っている（wait_reason が user_start の）ループだけ。ほかは 409 not_awaiting_user_start。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "loop_id"
      ],
      "additionalProperties": false,
      "properties": {
        "loop_id": {
          "type": "string",
          "minLength": 1
        }
      }
    },
    "source": "loops.yaml"
  },
  {
    "operationId": "startSprintRun",
    "method": "POST",
    "path": "/accounts/{accountId}/sprint-runs",
    "kind": "create",
    "access": "write",
    "keys": [
      "idempotency_key"
    ],
    "idField": "sprint_run_id",
    "summary": "スプリントの実行を始める",
    "description": "確かめの順は 課題（400）→ 登録（404）→ キー（409）→ 導入（409）→ 使える量（409）。 課題が動いているループのマイルストーンに属すると 409 issue_in_loop（details.numbers・details.loop_name）、 同じリポジトリの終わりの状態でない実行が持つ課題を指すと 409 issue_in_use（details.numbers）、 並行を許さない版どうしなら 409 parallel_unsupported_version。limits_exceeded の reasons に concurrent_repo_runs がある（#2347）。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "idempotency_key",
        "repository_id",
        "issues"
      ],
      "properties": {
        "idempotency_key": {
          "type": "string",
          "pattern": "^[0-9A-Za-z-]{1,64}$",
          "description": "作成の冪等の鍵。同じ利用者・同じ操作・同じ鍵の作成は 24 時間の間 1 件にする"
        },
        "repository_id": {
          "type": "integer",
          "minimum": 1
        },
        "issues": {
          "type": "array",
          "minItems": 1,
          "maxItems": 20,
          "uniqueItems": true,
          "items": {
            "type": "integer",
            "minimum": 1
          }
        }
      }
    },
    "source": "runs.yaml"
  },
  {
    "operationId": "updateLoopBudget",
    "method": "PATCH",
    "path": "/accounts/{accountId}/loops",
    "kind": "update",
    "access": "write",
    "keys": [
      "loop_id"
    ],
    "idField": null,
    "summary": "開発ループの予算を変える",
    "description": "終わりの状態でないループにだけ効き、次のジョブの指示から NDF の予算へ渡る。3 つの単位をまとめて置き換え、送らない単位と null の単位は空（リポジトリの既定）として扱う。 値のある単位を空にする変更は、次のジョブが使う NDF の版（ループの今の実行の版、無ければ今のマーケットプレイスの版）が予算を既定へ戻せないとき 409 budget_reset_unsupported（details.ndf_version・details.units）で断る。値を入れる・変える・空のままにする変更は版によらず受ける。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "loop_id",
        "budget"
      ],
      "additionalProperties": false,
      "properties": {
        "loop_id": {
          "type": "string",
          "minLength": 1
        },
        "budget": {
          "type": "object",
          "description": "単位ごとに数か null。null の単位はリポジトリの予算の既定（.ndf/pace.json の loop.budget）に従う",
          "additionalProperties": false,
          "properties": {
            "laps": {
              "type": [
                "number",
                "null"
              ],
              "exclusiveMinimum": 0
            },
            "usd": {
              "type": [
                "number",
                "null"
              ],
              "exclusiveMinimum": 0
            },
            "hours": {
              "type": [
                "number",
                "null"
              ],
              "exclusiveMinimum": 0
            }
          }
        }
      }
    },
    "source": "loops.yaml"
  },
  {
    "operationId": "updateRepository",
    "method": "PATCH",
    "path": "/accounts/{accountId}/repositories",
    "kind": "update",
    "access": "write",
    "keys": [
      "repository_id"
    ],
    "idField": null,
    "summary": "リポジトリの同時の実行の数を宣言する",
    "description": "料金プランの同時の数より小さい値だけが効く。null は宣言を外す。持ち主だけが使える（#2347）。",
    "parameters": [],
    "itemSchema": {
      "type": "object",
      "required": [
        "repository_id",
        "max_concurrent_runs"
      ],
      "additionalProperties": false,
      "properties": {
        "repository_id": {
          "type": "integer",
          "minimum": 1
        },
        "max_concurrent_runs": {
          "type": [
            "integer",
            "null"
          ],
          "minimum": 1,
          "maximum": 100
        }
      }
    },
    "source": "runs.yaml"
  }
] as const satisfies readonly Operation[];
