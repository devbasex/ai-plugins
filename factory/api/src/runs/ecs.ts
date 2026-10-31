// 実行基盤の口の本物（ECS Fargate・Secrets Manager・S3）。タスクの役割に S3 の権限を持たせず、状態は署名つき URL でだけ渡す（決定 8）
import { DescribeTasksCommand, ECSClient, RunTaskCommand, StopTaskCommand } from "@aws-sdk/client-ecs";
import { GetObjectCommand, HeadObjectCommand, PutObjectCommand, S3Client } from "@aws-sdk/client-s3";
import { CreateSecretCommand, DeleteSecretCommand, SecretsManagerClient } from "@aws-sdk/client-secrets-manager";
import { getSignedUrl } from "@aws-sdk/s3-request-presigner";
import type { RunJobInstruction } from "@ndf-factory/contracts";
import type { RunnerPlatform, SignedUrl, TaskStatus } from "./platform.ts";

export const STATE_URL_SECONDS = 15 * 60;
export const stateKey = (sprintRunId: string) => `sprint-runs/${sprintRunId}/ndf-state.tar.gz`;

export interface EcsPlatformConfig {
  clusterArn: string;
  subnetIds: string[];
  securityGroupIds: string[];
  managedTaskDefinitionArn: string;
  byokTaskDefinitionArn: string;
  stateBucket: string;
  containerName?: string;
}

export class EcsRunnerPlatform implements RunnerPlatform {
  constructor(
    private readonly config: EcsPlatformConfig,
    private readonly ecs = new ECSClient({}),
    private readonly secrets = new SecretsManagerClient({}),
    private readonly s3 = new S3Client({}),
  ) {}

  async putJobToken(jobId: string, token: string): Promise<string> {
    const out = await this.secrets.send(new CreateSecretCommand({ Name: `ndf-factory/run-jobs/${jobId}`, SecretString: token }));
    if (!out.ARN) throw new Error("実行ジョブの token の置き場を作れません");
    return out.ARN;
  }

  async deleteJobToken(secretArn: string): Promise<void> {
    await this.secrets.send(new DeleteSecretCommand({ SecretId: secretArn, ForceDeleteWithoutRecovery: true }));
  }

  async runTask(mode: "byok" | "managed", instruction: RunJobInstruction, startedBy: string): Promise<string> {
    const environment = Object.entries(instruction).map(([name, value]) => ({ name, value: String(value) }));
    const out = await this.ecs.send(
      new RunTaskCommand({
        cluster: this.config.clusterArn,
        taskDefinition: mode === "byok" ? this.config.byokTaskDefinitionArn : this.config.managedTaskDefinitionArn,
        launchType: "FARGATE",
        startedBy: startedBy.slice(0, 36),
        networkConfiguration: {
          awsvpcConfiguration: { subnets: this.config.subnetIds, securityGroups: this.config.securityGroupIds, assignPublicIp: "DISABLED" },
        },
        overrides: { containerOverrides: [{ name: this.config.containerName ?? "runner", environment }] },
      }),
    );
    const arn = out.tasks?.[0]?.taskArn;
    if (!arn) throw new Error(`タスクを起動できません（${out.failures?.[0]?.reason ?? "理由なし"}）`);
    return arn;
  }

  async stopTask(taskArn: string, reason: string): Promise<void> {
    await this.ecs.send(new StopTaskCommand({ cluster: this.config.clusterArn, task: taskArn, reason }));
  }

  async describeTask(taskArn: string): Promise<TaskStatus> {
    const out = await this.ecs.send(new DescribeTasksCommand({ cluster: this.config.clusterArn, tasks: [taskArn] }));
    const status = out.tasks?.[0]?.lastStatus;
    if (!status) return "MISSING";
    if (status === "STOPPED") return "STOPPED";
    if (status === "DEACTIVATING" || status === "STOPPING" || status === "DEPROVISIONING") return "STOPPING";
    return status === "RUNNING" ? "RUNNING" : "PENDING";
  }

  async stateUrl(sprintRunId: string, op: "put" | "get"): Promise<SignedUrl | null> {
    const Key = stateKey(sprintRunId);
    const Bucket = this.config.stateBucket;
    if (op === "get") {
      try {
        await this.s3.send(new HeadObjectCommand({ Bucket, Key }));
      } catch (err) {
        if ((err as { name?: string }).name === "NotFound") return null;
        throw err;
      }
    }
    const command = op === "put" ? new PutObjectCommand({ Bucket, Key }) : new GetObjectCommand({ Bucket, Key });
    const url = await getSignedUrl(this.s3, command, { expiresIn: STATE_URL_SECONDS });
    return { url, expiresAt: new Date(Date.now() + STATE_URL_SECONDS * 1000).toISOString() };
  }
}
