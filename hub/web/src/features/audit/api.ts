import { useQuery } from "@tanstack/react-query";
import type {
  AuditFileContent,
  AuditRunsResponse,
} from "@/shared/lib/api-types";
import { apiGet } from "@/shared/lib/http";

export function useAuditRunsQuery() {
  return useQuery({
    queryKey: ["audit-runs"],
    queryFn: () => apiGet<AuditRunsResponse>("/audit/runs"),
  });
}

export function useAuditFileQuery(dir: string | null, file: string | null) {
  return useQuery({
    queryKey: ["audit-file", dir, file],
    queryFn: () =>
      apiGet<AuditFileContent>("/audit/file", {
        dir: dir ?? "",
        file: file ?? "",
      }),
    enabled: dir !== null && file !== null,
  });
}
