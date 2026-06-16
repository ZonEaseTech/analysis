import type { ReportPreview, ReportsResponse } from "@/shared/lib/api-types";
import { useQuery } from "@tanstack/react-query";
import { apiGet } from "@/shared/lib/http";

export function useReportsQuery() {
  return useQuery({
    queryKey: ["reports"],
    queryFn: () => apiGet<ReportsResponse>("/reports"),
  });
}

export function useReportPreviewQuery(
  file: string | null,
  sheet: number,
  offset: number,
  limit: number,
) {
  return useQuery({
    queryKey: ["report-preview", file, sheet, offset, limit],
    queryFn: () =>
      apiGet<ReportPreview>("/reports/preview", {
        file: file ?? "",
        sheet,
        offset,
        limit,
      }),
    enabled: file !== null,
  });
}
