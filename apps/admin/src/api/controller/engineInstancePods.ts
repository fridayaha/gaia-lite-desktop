import { http } from "@/utils/http";

export type PodInfo = {
  name: string;
  status: string;
  node: string;
  cpu: string;
  memory: string;
  restarts: number;
  age: string;
  agent_id: string;
  deployment_id: string;
};

export type PodListResponse = {
  items: PodInfo[];
  total: number;
};

export type PodLogsResponse = {
  pod_name: string;
  logs: string;
};

export type PodRestartResponse = {
  status: string;
  pod_name: string;
  message: string;
};

/** 获取 EngineInstance 下所有 Pod */
export const getInstancePodsApi = (instanceId: string) => {
  return http.request<PodListResponse>(
    "get",
    `/api/controller/engine-instances/${instanceId}/pods`
  );
};

/** 获取指定 Pod 的日志 */
export const getPodLogsApi = (
  instanceId: string,
  podName: string,
  tailLines?: number
) => {
  return http.request<PodLogsResponse>(
    "get",
    `/api/controller/engine-instances/${instanceId}/pods/${podName}/logs`,
    { params: { tail_lines: tailLines ?? 200 } }
  );
};

/** 重启指定 Pod */
export const restartPodApi = (instanceId: string, podName: string) => {
  return http.request<PodRestartResponse>(
    "post",
    `/api/controller/engine-instances/${instanceId}/pods/${podName}/restart`
  );
};
