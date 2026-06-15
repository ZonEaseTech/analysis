import { useQuery } from "@tanstack/react-query";
import type {
  DataDictMetricsResponse,
  DataDictTablesResponse,
} from "@/shared/lib/api-types";
import { apiGet } from "@/shared/lib/http";

export function useTablesQuery() {
  return useQuery({
    queryKey: ["datadict-tables"],
    queryFn: () => apiGet<DataDictTablesResponse>("/datadict/tables"),
  });
}

export function useMetricsQuery() {
  return useQuery({
    queryKey: ["datadict-metrics"],
    queryFn: () => apiGet<DataDictMetricsResponse>("/datadict/metrics"),
  });
}
