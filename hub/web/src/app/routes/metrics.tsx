import type { Metric } from "@/shared/lib/api-types";
import { createFileRoute } from "@tanstack/react-router";
import { Search } from "lucide-react";
import * as React from "react";
import { useMetricsCatalogQuery } from "@/features/metrics/api";
import { Page } from "@/shared/components/page";
import { EmptyView, ErrorView, LoadingView } from "@/shared/components/state-view";
import { Badge } from "@/shared/components/ui/badge";
import { Card } from "@/shared/components/ui/card";
import { Drawer } from "@/shared/components/ui/drawer";
import { Input } from "@/shared/components/ui/input";

const CONFIDENCE_COLOR: Record<Metric["confidence"], string> = {
  ACTUAL: "text-emerald-500 border-emerald-500/40",
  ESTIMATED: "text-amber-500 border-amber-500/40",
  NA: "text-muted-foreground border-border",
};

function ConfidenceBadge({ m }: { m: Metric }): React.ReactElement {
  return <Badge className={CONFIDENCE_COLOR[m.confidence]}>{m.confidenceLabel}</Badge>;
}

function Field({
  label,
  children,
}: {
  label: string;
  children: React.ReactNode;
}): React.ReactElement | null {
  if (children == null || children === "")
    return null;
  return (
    <div>
      <div className="mb-1 text-xs font-medium text-muted-foreground">{label}</div>
      <div className="text-sm">{children}</div>
    </div>
  );
}

function MetricDetail({
  metric,
  byId,
  onJump,
  onClose,
}: {
  metric: Metric | null;
  byId: Map<string, Metric>;
  onJump: (id: string) => void;
  onClose: () => void;
}): React.ReactElement {
  return (
    <Drawer open={metric !== null} onClose={onClose} title={metric?.name ?? "口径详情"}>
      {metric ? (
        <div className="space-y-4">
          <div className="flex flex-wrap items-center gap-1.5">
            <ConfidenceBadge m={metric} />
            <Badge>{metric.statusLabel}</Badge>
            {metric.grain ? <Badge>粒度 {metric.grain}</Badge> : null}
            {metric.unit ? <Badge>单位 {metric.unit}</Badge> : null}
            <Badge className="font-mono">{metric.id}</Badge>
          </div>

          <Field label="业务含义">{metric.definition}</Field>

          <Field label="公式">
            <code className="rounded bg-black/30 px-1.5 py-0.5 font-mono text-xs">
              {metric.formula.business}
            </code>
          </Field>
          {metric.formula.excel ? (
            <Field label="Excel 公式">
              <code className="rounded bg-black/30 px-1.5 py-0.5 font-mono text-xs">
                {metric.formula.excel}
              </code>
            </Field>
          ) : null}
          {metric.formula.sqlRefs.length > 0 ? (
            <Field label="SQL 实现">
              <ul className="space-y-1">
                {metric.formula.sqlRefs.map((ref, i) => (
                  <li
                    key={i}
                    className="rounded bg-black/30 px-2 py-1 font-mono text-xs break-all"
                  >
                    {ref}
                  </li>
                ))}
              </ul>
            </Field>
          ) : null}

          {metric.lineage.sourceTables.length > 0 ? (
            <Field label="源表">
              <div className="flex flex-wrap gap-1.5">
                {metric.lineage.sourceTables.map(t => (
                  <Badge key={t} className="font-mono">
                    {t}
                  </Badge>
                ))}
              </div>
            </Field>
          ) : null}
          {metric.lineage.upstreamMetrics.length > 0 ? (
            <Field label="上游指标（点击跳转）">
              <div className="flex flex-wrap gap-1.5">
                {metric.lineage.upstreamMetrics.map(id => (
                  <button
                    key={id}
                    type="button"
                    onClick={() => onJump(id)}
                    className="rounded-md border border-sky-500/40 px-2 py-0.5 text-xs font-mono text-sky-500 hover:bg-sky-500/10"
                  >
                    {byId.get(id)?.name ?? id}
                  </button>
                ))}
              </div>
            </Field>
          ) : null}

          {metric.reconciliation ? (
            <Field label="对账锚">
              <div className="space-y-0.5">
                <div>{metric.reconciliation.anchor}</div>
                {metric.reconciliation.impl ? (
                  <div className="font-mono text-xs text-muted-foreground">
                    {metric.reconciliation.impl}
                  </div>
                ) : null}
                {metric.reconciliation.status ? (
                  <div className="text-xs text-emerald-500">
                    {metric.reconciliation.status}
                  </div>
                ) : null}
              </div>
            </Field>
          ) : null}

          <Field label="行业基准">{metric.industryBenchmark}</Field>
          <Field label="当前实测">{metric.currentValue}</Field>
          <Field label="报表展示">{metric.reportDisplay}</Field>
          <Field label="注意 / 排障">{metric.notes}</Field>
          {metric.relatedDocs.length > 0 ? (
            <Field label="相关文档">
              <div className="flex flex-col gap-0.5">
                {metric.relatedDocs.map(d => (
                  <span key={d} className="font-mono text-xs text-muted-foreground">
                    docs/{d}
                  </span>
                ))}
              </div>
            </Field>
          ) : null}
        </div>
      ) : null}
    </Drawer>
  );
}

function MetricCard({
  metric,
  onOpen,
}: {
  metric: Metric;
  onOpen: () => void;
}): React.ReactElement {
  return (
    <Card
      className="cursor-pointer p-3 transition-colors hover:bg-accent"
      onClick={onOpen}
    >
      <div className="mb-1 flex items-start justify-between gap-2">
        <div className="text-sm font-medium">{metric.name}</div>
        <ConfidenceBadge m={metric} />
      </div>
      <div className="mb-2 line-clamp-2 text-xs text-muted-foreground">
        {metric.definition}
      </div>
      <code className="block truncate rounded bg-black/20 px-1.5 py-0.5 font-mono text-xs text-muted-foreground">
        {metric.formula.business}
      </code>
    </Card>
  );
}

function MetricsPage(): React.ReactElement {
  const { data, isLoading, isError, error } = useMetricsCatalogQuery();
  const [q, setQ] = React.useState("");
  const [selectedId, setSelectedId] = React.useState<string | null>(null);

  const byId = React.useMemo(() => {
    const m = new Map<string, Metric>();
    for (const d of data?.domains ?? []) {
      for (const x of d.metrics) m.set(x.id, x);
    }
    return m;
  }, [data]);

  const needle = q.trim().toLowerCase();
  const match = (m: Metric): boolean =>
    !needle
    || m.id.toLowerCase().includes(needle)
    || m.name.toLowerCase().includes(needle)
    || m.definition.toLowerCase().includes(needle)
    || m.formula.business.toLowerCase().includes(needle);

  return (
    <Page
      title="口径中心"
      description="每个指标的唯一真源：含义 / 公式 / 数据来源 / 对账锚 / 置信度。客户问“这个数怎么算的”，点开即答。"
    >
      <div className="relative mb-4 max-w-sm">
        <Search className="pointer-events-none absolute left-2.5 top-2.5 size-4 text-muted-foreground" />
        <Input
          value={q}
          onChange={e => setQ(e.target.value)}
          placeholder="搜指标名 / 公式 / 含义…"
          className="pl-8"
        />
      </div>

      {isLoading ? (
        <LoadingView />
      ) : isError ? (
        <ErrorView error={error} />
      ) : !data || data.domains.length === 0 ? (
        <EmptyView label="暂无指标" />
      ) : (
        <div className="space-y-6">
          {data.domains.map((d) => {
            const items = d.metrics.filter(match);
            if (items.length === 0)
              return null;
            return (
              <div key={d.key}>
                <h2 className="mb-2 flex items-center gap-2 text-sm font-semibold">
                  {d.label}
                  <span className="text-xs font-normal text-muted-foreground">
                    {items.length}
                  </span>
                </h2>
                <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                  {items.map(m => (
                    <MetricCard
                      key={m.id}
                      metric={m}
                      onOpen={() => setSelectedId(m.id)}
                    />
                  ))}
                </div>
              </div>
            );
          })}
        </div>
      )}

      <MetricDetail
        metric={selectedId ? (byId.get(selectedId) ?? null) : null}
        byId={byId}
        onJump={id => setSelectedId(id)}
        onClose={() => setSelectedId(null)}
      />
    </Page>
  );
}

export const Route = createFileRoute("/metrics")({ component: MetricsPage });
