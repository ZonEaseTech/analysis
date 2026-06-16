import type { DataDictTable } from "@/shared/lib/api-types";
import { createFileRoute } from "@tanstack/react-router";
import { Search, Table2 } from "lucide-react";
import * as React from "react";
import { useMetricsQuery, useTablesQuery } from "@/features/datadict/api";
import { Page } from "@/shared/components/page";
import { EmptyView, ErrorView, LoadingView } from "@/shared/components/state-view";
import { Card } from "@/shared/components/ui/card";
import { Input } from "@/shared/components/ui/input";
import { Table, TBody, TD, TH, THead, TR } from "@/shared/components/ui/table";
import { Tabs } from "@/shared/components/ui/tabs";

function relationLinks(tables: DataDictTable[]): {
  from: string;
  field: string;
  to: string;
}[] {
  const names = new Set(tables.map(t => t.table));
  const links: { from: string; field: string; to: string }[] = [];
  for (const t of tables) {
    for (const f of t.fields) {
      const m = /^(.*)_(?:id|num|code)$/.exec(f.name);
      if (!m)
        continue;
      const stem = m[1];
      for (const cand of [
        stem,
        `ttpos_${stem}`,
        `${stem}s`,
        `ttpos_${stem}s`,
      ]) {
        if (names.has(cand) && cand !== t.table) {
          links.push({ from: t.table, field: f.name, to: cand });
          break;
        }
      }
    }
  }
  return links;
}

function TablesView(): React.ReactElement {
  const { data, isLoading, isError, error } = useTablesQuery();
  const [q, setQ] = React.useState("");
  const [selected, setSelected] = React.useState<string | null>(null);

  if (isLoading)
    return <LoadingView />;
  if (isError)
    return <ErrorView error={error} />;
  if (!data || data.tables.length === 0)
    return <EmptyView label="暂无表" />;

  const ql = q.trim().toLowerCase();
  const filtered = ql
    ? data.tables.filter(
        t =>
          t.table.toLowerCase().includes(ql)
          || t.description.toLowerCase().includes(ql),
      )
    : data.tables;
  const current
    = data.tables.find(t => t.table === selected) ?? filtered[0] ?? null;
  const links = relationLinks(data.tables);
  const relatedLinks = current
    ? links.filter(l => l.from === current.table || l.to === current.table)
    : [];

  return (
    <div className="grid grid-cols-1 gap-4 lg:grid-cols-[18rem_1fr]">
      <div className="space-y-2">
        <div className="relative">
          <Search className="absolute left-2 top-2.5 size-4 text-muted-foreground" />
          <Input
            className="pl-8"
            placeholder="搜索表名 / 描述"
            value={q}
            onChange={e => setQ(e.target.value)}
          />
        </div>
        <div className="max-h-[70vh] space-y-1 overflow-auto">
          {filtered.map(t => (
            <button
              key={t.table}
              type="button"
              onClick={() => setSelected(t.table)}
              className={`flex w-full items-start gap-2 rounded-md px-2 py-1.5 text-left text-sm transition-colors hover:bg-accent ${
                current?.table === t.table ? "bg-accent" : ""
              }`}
            >
              <Table2 className="mt-0.5 size-3.5 shrink-0 text-muted-foreground" />
              <span className="min-w-0">
                <span className="block truncate font-mono text-xs">{t.table}</span>
                <span className="block truncate text-xs text-muted-foreground">
                  {t.description}
                </span>
              </span>
            </button>
          ))}
        </div>
      </div>

      <div className="space-y-4">
        {current ? (
          <>
            <Card className="p-4">
              <div className="font-mono text-sm font-semibold">{current.table}</div>
              <p className="mt-1 text-sm text-muted-foreground">
                {current.description}
              </p>
            </Card>
            <Card>
              <Table>
                <THead>
                  <TR>
                    <TH>字段</TH>
                    <TH>类型</TH>
                    <TH>说明</TH>
                  </TR>
                </THead>
                <TBody>
                  {current.fields.map(f => (
                    <TR key={f.name}>
                      <TD className="font-mono text-xs">{f.name}</TD>
                      <TD className="text-xs text-muted-foreground">{f.type}</TD>
                      <TD>{f.comment}</TD>
                    </TR>
                  ))}
                </TBody>
              </Table>
            </Card>
            {relatedLinks.length > 0 ? (
              <Card className="p-4">
                <div className="mb-2 text-sm font-medium">表关系（推断）</div>
                <ul className="space-y-1 text-sm">
                  {relatedLinks.map((l, i) => (
                    <li key={i} className="font-mono text-xs">
                      <span className="text-sky-500">{l.from}</span>.{l.field}
                      <span className="mx-1 text-muted-foreground">→</span>
                      <span className="text-emerald-500">{l.to}</span>
                    </li>
                  ))}
                </ul>
              </Card>
            ) : null}
          </>
        ) : (
          <EmptyView label="选择左侧一张表查看字段" />
        )}
      </div>
    </div>
  );
}

function MetricsView(): React.ReactElement {
  const { data, isLoading, isError, error } = useMetricsQuery();
  if (isLoading)
    return <LoadingView />;
  if (isError)
    return <ErrorView error={error} />;
  if (!data || data.metrics.length === 0)
    return <EmptyView label="暂无指标" />;
  return (
    <Card>
      <Table>
        <THead>
          <TR>
            <TH>指标</TH>
            <TH>定义</TH>
            <TH>口径 / 公式</TH>
          </TR>
        </THead>
        <TBody>
          {data.metrics.map(m => (
            <TR key={m.name}>
              <TD className="font-medium">{m.name}</TD>
              <TD>{m.definition}</TD>
              <TD className="font-mono text-xs text-muted-foreground">
                {m.formula}
              </TD>
            </TR>
          ))}
        </TBody>
      </Table>
    </Card>
  );
}

function DataDictPage(): React.ReactElement {
  const [tab, setTab] = React.useState("tables");
  return (
    <Page title="数据字典" description="BigQuery 表结构、字段说明与指标口径目录">
      <Tabs
        className="mb-4"
        tabs={[
          { key: "tables", label: "数据表" },
          { key: "metrics", label: "指标目录" },
        ]}
        active={tab}
        onChange={setTab}
      />
      {tab === "tables" ? <TablesView /> : <MetricsView />}
    </Page>
  );
}

export const Route = createFileRoute("/datadict")({ component: DataDictPage });
