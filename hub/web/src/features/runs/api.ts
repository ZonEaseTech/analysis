import type { RunDetail, RunsResponse } from "@/shared/lib/api-types";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/shared/lib/http";

export function useRunsQuery() {
  return useQuery({
    queryKey: ["runs"],
    queryFn: () => apiGet<RunsResponse>("/runs"),
    refetchInterval: 5_000, // surface running → done without a manual refresh
  });
}

export function useRunDetailQuery(id: string | null) {
  return useQuery({
    queryKey: ["run", id],
    queryFn: () => apiGet<RunDetail>(`/runs/${id}`),
    enabled: id !== null,
  });
}
