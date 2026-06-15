import { createFileRoute } from "@tanstack/react-router";
import { Download, FileSpreadsheet } from "lucide-react";
import * as React from "react";
import { useReportPreviewQuery, useReportsQuery } from "@/features/reports/api";
import { Page } from "@/shared/components/page";
import { EmptyView, ErrorView, LoadingView } from "@/shared/components/state-view";
import { Badge } from "@/shared/components/ui/badge";
import { Button } from "@/shared/components/ui/button";
import { Card } from "@/shared/components/ui/card";
import { Drawer } from "@/shared/components/ui/drawer";
import { Tabs } from "@/shared/components/ui/tabs";
import { TBody, TD, TH, THead, TR, Table } from "@/shared/components/ui/table";
import { apiUrl } from "@/shared/lib/http";

const PAGE_SIZE = 100;

function PreviewDrawer({
  file,
  onClose,
}: {
  file: string | null;
  onClose: () => void;
}): React.ReactElement {
  const [sheet, setSheet] = React.useState(0);
  const [offset, setOffset] = React.useState(0);

  React.useEffect(() => {
    setSheet(0);
    setOffset(0);
  }, [file]);

  const { data, isLoading, isError, error } = useReportPreviewQuery(
    file,
    sheet,
    offset,
    PAGE_SIZE,
  );

  return (
    <Drawer
      open={file !== null}
      onClose={onClose}
      title={file ?? "预览"}
      width="max-w-5xl"
    >
      <div className="mb-3 flex items-center justify-between">
        <div className="text-sm text-muted-foreground truncate">{file}</div>
        {file ? (
          <a href={apiUrl("/reports/download", { file })}>
            <Button size="sm" variant="outline">
              <Download className="size-3.5" />
              下载
            </Button>
          </a>
        ) : null}
      </div>

      {isLoading ? (
        <LoadingView />
      ) : isError ? (
        <ErrorView error={error} />
      ) : data ? (
        <div className="space-y-3">
          {data.sheetNames.length > 1 ? (
            <Tabs
              tabs={data.sheetNames.map((n, i) => ({
                key: String(i),
                label: n,
              }))}
              active={String(sheet)}
              onChange={(k) => {
                setSheet(Number(k));
                setOffset(0);
              }}
            />
          ) : null}
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
                    <TD key={ci} className="whitespace-nowrap">
                      {c}
                    </TD>
                  ))}
                </TR>
              ))}
            </TBody>
          </Table>
          <div className="flex items-center justify-between text-sm text-muted-foreground">
            <span>
              第 {offset + 1}–{Math.min(offset + PAGE_SIZE, data.total)} 行，共{" "}
              {data.total} 行
            </span>
            <div className="flex gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={offset === 0}
                onClick={() => setOffset(Math.max(0, offset - PAGE_SIZE))}
              >
                上一页
              </Button>
              <Button
                size="sm"
                variant="outline"
                disabled={offset + PAGE_SIZE >= data.total}
                onClick={() => setOffset(offset + PAGE_SIZE)}
              >
                下一页
              </Button>
            </div>
          </div>
        </div>
      ) : null}
    </Drawer>
  );
}

function ReportsPage(): React.ReactElement {
  const { data, isLoading, isError, error } = useReportsQuery();
  const [file, setFile] = React.useState<string | null>(null);

  return (
    <Page title="报表中心" description="已生成的 xlsx 报表，按报表名 / 月份 / 版本分组">
      {isLoading ? (
        <LoadingView />
      ) : isError ? (
        <ErrorView error={error} />
      ) : !data || data.groups.length === 0 ? (
        <EmptyView label="暂无报表" />
      ) : (
        <div className="space-y-6">
          {data.groups.map((g) => (
            <div key={g.name}>
              <h2 className="mb-2 text-sm font-semibold">{g.name}</h2>
              <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {g.files.map((f) => (
                  <Card
                    key={f.file}
                    className="cursor-pointer p-3 transition-colors hover:bg-accent"
                    onClick={() => setFile(f.file)}
                  >
                    <div className="flex items-start gap-2">
                      <FileSpreadsheet className="mt-0.5 size-4 shrink-0 text-emerald-500" />
                      <div className="min-w-0 flex-1">
                        <div className="truncate text-sm font-medium" title={f.file}>
                          {f.file}
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-1.5 text-xs text-muted-foreground">
                          {f.month ? <Badge>{f.month}</Badge> : null}
                          {f.version ? <Badge>{f.version}</Badge> : null}
                          <span>{f.sizeKb} KB</span>
                          <span>{f.mtime}</span>
                        </div>
                      </div>
                    </div>
                  </Card>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      <PreviewDrawer file={file} onClose={() => setFile(null)} />
    </Page>
  );
}

export const Route = createFileRoute("/reports")({ component: ReportsPage });
