import { useQuery } from "@tanstack/react-query";
import type {
  MetricsResponse,
  ReportBindingsResponse,
} from "@/shared/lib/api-types";
import { apiGet } from "@/shared/lib/http";

export function useMetricsCatalogQuery() {
  return useQuery({
    queryKey: ["metrics-catalog"],
    queryFn: () => apiGet<MetricsResponse>("/metrics"),
    staleTime: 60_000,
  });
}

export function useReportBindingsQuery() {
  return useQuery({
    queryKey: ["metrics-bindings"],
    queryFn: () => apiGet<ReportBindingsResponse>("/metrics/bindings"),
    staleTime: 60_000,
  });
}
