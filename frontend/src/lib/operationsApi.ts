import { api } from "@/lib/api";

export type OperationsRange = "week" | "month" | "quarter";

export interface RetentionMetric {
  rate: number | null;
  cohort_users: number;
}

export interface OperationsTrendPoint {
  date: string;
  requests: number;
  active_users: number;
}

export interface OperationsAgentRow {
  agent_id: string;
  agent_version: string;
  eligible_users: number | null;
  active_users: number;
  penetration: number | null;
  requests: number;
  positive_rate: number | null;
  d7_retention: number | null;
}

export interface OperationsCohortRow {
  cohort: string;
  users: number;
  retention: Array<number | null>;
}

export interface OperationsOverview {
  range: OperationsRange;
  context: {
    version: string;
    timezone: string;
    window: string;
    from_at: string;
    to_at: string;
    as_of: string;
  };
  availability: {
    operations: boolean;
    eligible_users: boolean;
    department: boolean;
    user_type: boolean;
  };
  summary: {
    dau: number;
    wau: number;
    mau: number;
    dau_mau: number | null;
    request_count: number;
    successful_requests: number;
    failed_requests: number;
    pending_requests: number;
    active_users: number;
    requests_per_active_user: number | null;
    platform_penetration: number | null;
    d7_retention: number | null;
  };
  trend: OperationsTrendPoint[];
  retention: {
    d1: RetentionMetric;
    d7: RetentionMetric;
    d30: RetentionMetric;
  };
  agents: OperationsAgentRow[];
  cohorts: {
    week: OperationsCohortRow[];
    month: OperationsCohortRow[];
  };
}

export function getOperationsOverview(range: OperationsRange): Promise<OperationsOverview> {
  return api.get<OperationsOverview>(`/admin/operations/overview?range=${encodeURIComponent(range)}`);
}
