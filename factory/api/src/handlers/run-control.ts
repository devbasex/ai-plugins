// 停止の知らせと見回りの Lambda の handler（HTTP を持たない）。
// EventBridge の「ECS Task State Change」はタスクの startedBy（ジョブの ID）で、5 分ごとの起動は見回りで受ける
import type { RunControl } from "../runs/control.ts";
import { factoryDepsFromEnv } from "../runs/deps.ts";

export interface TaskStateChange {
  "detail-type": "ECS Task State Change";
  detail: { startedBy?: string; lastStatus?: string; taskArn?: string };
}

export interface ScheduledSweep {
  "detail-type": "Scheduled Event";
}

export async function handleRunControlEvent(control: RunControl, event: TaskStateChange | ScheduledSweep): Promise<void> {
  if (event["detail-type"] === "Scheduled Event") {
    await control.sweep();
    return;
  }
  const { startedBy, lastStatus } = event.detail;
  if (!startedBy) return;
  if (lastStatus === "RUNNING" || lastStatus === "STOPPED") await control.onTaskStateChange(startedBy, lastStatus);
}

export const handler = (event: TaskStateChange | ScheduledSweep) => handleRunControlEvent(factoryDepsFromEnv().control, event);
