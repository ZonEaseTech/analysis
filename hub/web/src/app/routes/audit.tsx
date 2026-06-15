import { createFileRoute } from "@tanstack/react-router";
import { FileText, Folder, Table2 } from "lucide-react";
import * as React from "react";
import { useAuditFileQuery, useAuditRunsQuery } from "@/features/audit/api";
import { Page } from "@/shared/components/page";
import { EmptyView, ErrorView, LoadingView } from "@/shared/components/state-view";
import { Badge } from "@/shared/components/ui/badge";
import { Card } from "@/shared/components/ui/card";
import { TBody, TD, TH, THead, TR, Table } from "@/shared/components/ui/table";

function FileContent({
  dir,
  file,
}: {
  dir: string;
  file: string;
}): React.ReactElement {
  const { data, isLoading, isError, error } = useAuditFileQuery(dir, file);
  if (isLoading) return <LoadingView />;
  if (isError) return <ErrorView error={error} />;
  if (!data) return <EmptyView />;

  if (data.kind === "txt") {
    return (
      <pre className="max-h-[70vh] overflow-auto rounded-md border border-border bg-black/40 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap">
        {data.text}
      </pre>
    );
  }

  return (
    <Card>
      <Table>
        <THead>
          <TR>
            {data.header.map((h, i) => (
              <TH key={i}>{h}</TH>
            ))}
          </TR>
        </THead>
        <TBody>
          {data.rows.map((r, ri) => (
            <TR key={ri}>
              {r.map((c, ci) => (
                <TD key={ci} className="whitespace-nowrap font-mono text-xs">
                  {c}
                </TD>
              ))}
            </TR>
          ))}
        </TBody>
      </Table>
    </Card>
  );
}

function AuditPage(): React.ReactElement {
  const { data, isLoading, isError, error } = useAuditRunsQuery();
  const [sel, setSel] = React.useState<{ dir: string; file: string } | null>(
    null,
  );

  return (
    <Page title="审计中心" description="逐步核对报表数字的审计产物（CSV / TXT）">
      {isLoading ? (
        <LoadingView />
      ) : isError ? (
        <ErrorView error={error} />
      ) : !data || data.runs.length === 0 ? (
        <EmptyView label="暂无审计记录" />
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-[20rem_1fr]">
          <div className="max-h-[78vh] space-y-3 overflow-auto">
            {data.runs.map((run) => (
              <Card key={run.dir} className="p-3">
                <div className="mb-2 flex items-center gap-2 text-sm font-medium">
                  <Folder className="size-4 text-amber-500" />
                  <span className="truncate font-mono text-xs">{run.dir}</span>
                </div>
                <div className="space-y-1">
                  {run.files.map((f) => {
                    const active =
                      sel?.dir === run.dir && sel?.file === f.file;
                    return (
                      <button
                        key={f.file}
                        type="button"
                        onClick={() => setSel({ dir: run.dir, file: f.file })}
                        className={`flex w-full items-center gap-2 rounded-md px-2 py-1 text-left text-xs transition-colors hover:bg-accent ${
                          active ? "bg-accent" : ""
                        }`}
                      >
                        {f.kind === "csv" ? (
                          <Table2 className="size-3.5 text-sky-500" />
                        ) : (
                          <FileText className="size-3.5 text-muted-foreground" />
                        )}
                        <span className="min-w-0 flex-1 truncate font-mono">
                          {f.file}
                        </span>
                        <Badge>{f.sizeKb} KB</Badge>
                      </button>
                    );
                  })}
                </div>
              </Card>
            ))}
          </div>

          <div>
            {sel ? (
              <FileContent dir={sel.dir} file={sel.file} />
            ) : (
              <EmptyView label="选择左侧一个审计文件查看内容" />
            )}
          </div>
        </div>
      )}
    </Page>
  );
}

export const Route = createFileRoute("/audit")({ component: AuditPage });
