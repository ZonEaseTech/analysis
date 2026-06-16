import type { RunStatus, RunSummary } from "@/shared/lib/api-types";
import { createFileRoute } from "@tanstack/react-router";
import * as React from "react";
import { useRunDetailQuery, useRunsQuery } from "@/features/runs/api";
import { Page } from "@/shared/components/page";
import { EmptyView, ErrorView, LoadingView } from "@/shared/components/state-view";
import { Badge } from "@/shared/components/ui/badge";
import { Drawer } from "@/shared/components/ui/drawer";
import { Table, TBody, TD, TH, THead, TR } from "@/shared/components/ui/table";

function StatusBadge({ s, exitCode }: { s: RunStatus; exitCode: number | null }) {
  if (s === "running")
    return <Badge className="text-amber-500 border-amber-500/40">运行中</Badge>;
  if (s === "done")
    return <Badge className="text-emerald-500 border-emerald-500/40">完成 (0)</Badge>;
  return <Badge className="text-red-500 border-red-500/40">退出 {exitCode ?? "?"}</Badge>;
}

function fmt(ts: string | null): string {
  return ts ? ts.replace("T", " ").replace(/\.\d+Z$/, "") : "—";
}

function duration(start: string, end: string | null): string {
  if (!end)
    return "—";
  const ms = Date.parse(end) - Date.parse(start);
  if (Number.isNaN(ms))
    return "—";
  const s = Math.round(ms / 1000);
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m${s % 60}s`;
}

function RunLogDrawer({
  id,
  onClose,
}: {
  id: string | null;
  onClose: () => void;
}): React.ReactElement {
  const { data, isLoading, isError, error } = useRunDetailQuery(id);
  return (
    <Drawer open={id !== null} onClose={onClose} title={data?.scriptName ?? "运行日志"} width="max-w-3xl">
      {isLoading ? (
        <LoadingView />
      ) : isError ? (
        <ErrorView error={error} />
      ) : data ? (
        <div className="space-y-3">
          <dl className="grid grid-cols-[5rem_1fr] gap-y-1 text-sm">
            <dt className="text-muted-foreground">脚本</dt>
            <dd className="font-mono text-xs">{data.scriptPath}</dd>
            <dt className="text-muted-foreground">参数</dt>
            <dd className="font-mono text-xs">{data.args || "—"}</dd>
            <dt className="text-muted-foreground">开始</dt>
            <dd>{fmt(data.startedAt)}</dd>
            <dt className="text-muted-foreground">耗时</dt>
            <dd>{duration(data.startedAt, data.finishedAt)}</dd>
            <dt className="text-muted-foreground">状态</dt>
            <dd>
              <StatusBadge s={data.status} exitCode={data.exitCode} />
            </dd>
          </dl>
          <pre className="max-h-[60vh] overflow-auto rounded-md border border-border bg-black/40 p-3 font-mono text-xs leading-relaxed whitespace-pre-wrap break-all">
            {data.log || "（无日志）"}
          </pre>
        </div>
      ) : null}
    </Drawer>
  );
}

function RunsPage(): React.ReactElement {
  const { data, isLoading, isError, error } = useRunsQuery();
  const [openId, setOpenId] = React.useState<string | null>(null);

  return (
    <Page title="运行历史" description="所有报表脚本的运行记录，持久化在 SQLite，重启不丢。点开看完整日志。">
      {isLoading ? (
        <LoadingView />
      ) : isError ? (
        <ErrorView error={error} />
      ) : !data || data.runs.length === 0 ? (
        <EmptyView label="暂无运行记录（去脚本中心跑一个）" />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>报表脚本</TH>
              <TH>参数</TH>
              <TH>开始时间</TH>
              <TH>耗时</TH>
              <TH>状态</TH>
            </TR>
          </THead>
          <TBody>
            {data.runs.map((r: RunSummary) => (
              <TR
                key={r.id}
                className="cursor-pointer hover:bg-accent"
                onClick={() => setOpenId(r.id)}
              >
                <TD className="font-medium">{r.scriptName}</TD>
                <TD className="font-mono text-xs text-muted-foreground">{r.args || "—"}</TD>
                <TD className="text-xs">{fmt(r.startedAt)}</TD>
                <TD className="text-xs">{duration(r.startedAt, r.finishedAt)}</TD>
                <TD>
                  <StatusBadge s={r.status} exitCode={r.exitCode} />
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}

      <RunLogDrawer id={openId} onClose={() => setOpenId(null)} />
    </Page>
  );
}

export const Route = createFileRoute("/runs")({ component: RunsPage });
