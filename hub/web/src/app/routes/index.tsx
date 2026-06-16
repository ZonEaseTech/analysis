import type { ScriptGroup } from "@/shared/lib/api-types";
import { createFileRoute } from "@tanstack/react-router";
import { Info, Play } from "lucide-react";
import * as React from "react";
import {
  useRunScriptMutation,
  useScriptDetailQuery,
  useScriptsQuery,
} from "@/features/scripts/api";
import { useRunStream } from "@/features/scripts/use-run-stream";
import { Page } from "@/shared/components/page";
import { EmptyView, ErrorView, LoadingView } from "@/shared/components/state-view";
import { Badge } from "@/shared/components/ui/badge";
import { Button } from "@/shared/components/ui/button";
import { Drawer } from "@/shared/components/ui/drawer";
import { Table, TBody, TD, TH, THead, TR } from "@/shared/components/ui/table";

const GROUP_COLOR: Record<ScriptGroup, string> = {
  bq_reports: "text-sky-500 border-sky-500/40",
  scripts: "text-violet-500 border-violet-500/40",
  adhoc: "text-amber-500 border-amber-500/40",
};

function LogPanel({
  runId,
  scriptName,
  onClose,
}: {
  runId: string;
  scriptName: string;
  onClose: () => void;
}): React.ReactElement {
  const { lines, running, exitCode, error } = useRunStream(runId);
  const endRef = React.useRef<HTMLDivElement>(null);
  React.useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [lines]);

  return (
    <div className="fixed bottom-0 right-0 z-40 m-4 flex h-80 w-[640px] max-w-[calc(100vw-2rem)] flex-col rounded-lg border border-border bg-card shadow-xl">
      <div className="flex items-center justify-between border-b border-border px-3 py-2">
        <div className="flex items-center gap-2 text-sm">
          <span className="font-medium truncate">运行日志 · {scriptName}</span>
          {running ? (
            <Badge className="text-amber-500 border-amber-500/40">运行中</Badge>
          ) : exitCode === 0 ? (
            <Badge className="text-emerald-500 border-emerald-500/40">完成 (0)</Badge>
          ) : exitCode !== null ? (
            <Badge className="text-red-500 border-red-500/40">退出 {exitCode}</Badge>
          ) : null}
        </div>
        <Button size="sm" variant="ghost" onClick={onClose}>
          关闭
        </Button>
      </div>
      <div className="flex-1 overflow-auto bg-black/40 p-3 font-mono text-xs leading-relaxed">
        {lines.length === 0 && running ? (
          <div className="text-muted-foreground">等待输出…</div>
        ) : (
          lines.map((l, i) => (
            <div key={i} className="whitespace-pre-wrap break-all">
              {l}
            </div>
          ))
        )}
        {error ? <div className="text-red-400">{error}</div> : null}
        <div ref={endRef} />
      </div>
    </div>
  );
}

function ScriptDetailDrawer({
  id,
  onClose,
}: {
  id: string | null;
  onClose: () => void;
}): React.ReactElement {
  const { data, isLoading, isError, error } = useScriptDetailQuery(id);
  return (
    <Drawer open={id !== null} onClose={onClose} title={data?.name ?? "脚本详情"}>
      {isLoading ? (
        <LoadingView />
      ) : isError ? (
        <ErrorView error={error} />
      ) : data ? (
        <div className="space-y-4">
          <dl className="grid grid-cols-[6rem_1fr] gap-y-2 text-sm">
            <dt className="text-muted-foreground">脚本路径</dt>
            <dd className="font-mono break-all">{data.path}</dd>
            <dt className="text-muted-foreground">分组</dt>
            <dd>{data.group}</dd>
            <dt className="text-muted-foreground">谁要的</dt>
            <dd>{data.whoAsked}</dd>
            <dt className="text-muted-foreground">做什么</dt>
            <dd>{data.what}</dd>
          </dl>
          <div>
            <div className="mb-1 text-sm text-muted-foreground">源码</div>
            <pre className="max-h-[60vh] overflow-auto rounded-md border border-border bg-black/40 p-3 font-mono text-xs leading-relaxed">
              {data.source}
            </pre>
          </div>
        </div>
      ) : null}
    </Drawer>
  );
}

function ScriptsPage(): React.ReactElement {
  const { data, isLoading, isError, error } = useScriptsQuery();
  const runMutation = useRunScriptMutation();
  const [detailId, setDetailId] = React.useState<string | null>(null);
  const [run, setRun] = React.useState<{ runId: string; name: string } | null>(
    null,
  );

  function handleRun(id: string, name: string): void {
    runMutation.mutate(id, {
      onSuccess: res => setRun({ runId: res.runId, name }),
    });
  }

  return (
    <Page title="脚本中心" description="所有报表脚本，一键运行并查看实时日志">
      {isLoading ? (
        <LoadingView />
      ) : isError ? (
        <ErrorView error={error} />
      ) : !data || data.scripts.length === 0 ? (
        <EmptyView label="暂无脚本" />
      ) : (
        <Table>
          <THead>
            <TR>
              <TH>报表名</TH>
              <TH>脚本路径</TH>
              <TH>分组</TH>
              <TH>谁要的</TH>
              <TH>做什么</TH>
              <TH>上次运行</TH>
              <TH className="text-right">操作</TH>
            </TR>
          </THead>
          <TBody>
            {data.scripts.map(s => (
              <TR key={s.id}>
                <TD className="font-medium">{s.name}</TD>
                <TD className="font-mono text-xs text-muted-foreground">
                  {s.path}
                </TD>
                <TD>
                  <Badge className={GROUP_COLOR[s.group]}>{s.group}</Badge>
                </TD>
                <TD>{s.whoAsked}</TD>
                <TD className="max-w-xs truncate" title={s.what}>
                  {s.what}
                </TD>
                <TD className="text-xs text-muted-foreground">
                  {s.lastRunAt ?? "—"}
                </TD>
                <TD className="text-right whitespace-nowrap">
                  <Button
                    size="sm"
                    onClick={() => handleRun(s.id, s.name)}
                    disabled={runMutation.isPending}
                  >
                    <Play className="size-3.5" />
                    运行
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    className="ml-1"
                    onClick={() => setDetailId(s.id)}
                  >
                    <Info className="size-3.5" />
                  </Button>
                </TD>
              </TR>
            ))}
          </TBody>
        </Table>
      )}

      <ScriptDetailDrawer id={detailId} onClose={() => setDetailId(null)} />
      {run ? (
        <LogPanel
          runId={run.runId}
          scriptName={run.name}
          onClose={() => setRun(null)}
        />
      ) : null}
    </Page>
  );
}

export const Route = createFileRoute("/")({ component: ScriptsPage });
