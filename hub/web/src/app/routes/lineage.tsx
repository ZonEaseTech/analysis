import { createFileRoute } from "@tanstack/react-router";
import { FileSpreadsheet } from "lucide-react";
import * as React from "react";
import { useReportBindingsQuery } from "@/features/metrics/api";
import { Page } from "@/shared/components/page";
import { EmptyView, ErrorView, LoadingView } from "@/shared/components/state-view";
import { Badge } from "@/shared/components/ui/badge";
import { TBody, TD, TH, THead, TR, Table } from "@/shared/components/ui/table";
import type { ReportBindingColumn } from "@/shared/lib/api-types";

const CONFIDENCE_COLOR: Record<string, string> = {
  ACTUAL: "text-emerald-500 border-emerald-500/40",
  ESTIMATED: "text-amber-500 border-amber-500/40",
  NA: "text-muted-foreground border-border",
};

function ConfidenceCell({ c }: { c: ReportBindingColumn }): React.ReactElement {
  if (!c.confidence) return <span className="text-muted-foreground">—</span>;
  return (
    <Badge className={CONFIDENCE_COLOR[c.confidence] ?? ""}>{c.confidence}</Badge>
  );
}

function LineagePage(): React.ReactElement {
  const { data, isLoading, isError, error } = useReportBindingsQuery();

  return (
    <Page
      title="报表血缘"
      description="每张报表的每一列，绑定到哪个口径、用什么公式、数据来自哪张源表。客户问“这一列怎么来的”，一目了然。"
    >
      {isLoading ? (
        <LoadingView />
      ) : isError ? (
        <ErrorView error={error} />
      ) : !data || data.reports.length === 0 ? (
        <EmptyView label="暂无绑定（在 resources/reports/*.yaml 列上加 metric: <id> 即可出现）" />
      ) : (
        <div className="space-y-6">
          {data.reports.map((r) => (
            <div key={r.report}>
              <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                <FileSpreadsheet className="size-4 text-emerald-500" />
                {r.report}
                <span className="text-xs font-normal text-muted-foreground">
                  {r.columns.length} 列已绑定口径
                </span>
              </h2>
              <Table>
                <THead>
                  <TR>
                    <TH>报表列</TH>
                    <TH>绑定口径</TH>
                    <TH>业务域</TH>
                    <TH>公式</TH>
                    <TH>源表</TH>
                    <TH>置信度</TH>
                  </TR>
                </THead>
                <TBody>
                  {r.columns.map((c) => (
                    <TR key={c.column}>
                      <TD className="font-medium">{c.column}</TD>
                      <TD>
                        {c.found ? (
                          <code className="font-mono text-xs">{c.metricId}</code>
                        ) : (
                          <Badge className="text-red-500 border-red-500/40">
                            未知 {c.metricId}
                          </Badge>
                        )}
                      </TD>
                      <TD className="text-muted-foreground">{c.domain ?? "—"}</TD>
                      <TD className="font-mono text-xs text-muted-foreground">
                        {c.formula ?? "—"}
                      </TD>
                      <TD className="text-xs">
                        {c.sourceTables.length > 0 ? (
                          <div className="flex flex-wrap gap-1">
                            {c.sourceTables.map((t) => (
                              <Badge key={t} className="font-mono">
                                {t}
                              </Badge>
                            ))}
                          </div>
                        ) : (
                          "—"
                        )}
                      </TD>
                      <TD>
                        <ConfidenceCell c={c} />
                      </TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </div>
          ))}
        </div>
      )}
    </Page>
  );
}

export const Route = createFileRoute("/lineage")({ component: LineagePage });
