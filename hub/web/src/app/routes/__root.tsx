import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, createRootRoute } from "@tanstack/react-router";
import {
  BookOpen,
  CircleCheck,
  CircleX,
  FileSpreadsheet,
  Ruler,
  ShieldCheck,
  Terminal,
  Workflow,
} from "lucide-react";
import * as React from "react";
import type { HealthResponse } from "@/shared/lib/api-types";
import { cn } from "@/shared/lib/cn";
import { apiGet } from "@/shared/lib/http";

const NAV = [
  { to: "/", label: "脚本中心", icon: Terminal },
  { to: "/reports", label: "报表中心", icon: FileSpreadsheet },
  { to: "/metrics", label: "口径中心", icon: Ruler },
  { to: "/lineage", label: "报表血缘", icon: Workflow },
  { to: "/datadict", label: "数据字典", icon: BookOpen },
  { to: "/audit", label: "审计中心", icon: ShieldCheck },
] as const;

function HealthBadge(): React.ReactElement {
  const { data, isError } = useQuery({
    queryKey: ["health"],
    queryFn: () => apiGet<HealthResponse>("/health"),
    refetchInterval: 30_000,
    retry: 0,
  });
  const ok = !isError && data?.status === "ok";
  return (
    <div className="flex items-center gap-2 text-xs text-muted-foreground">
      {ok ? (
        <CircleCheck className="size-3.5 text-emerald-500" />
      ) : (
        <CircleX className="size-3.5 text-red-500" />
      )}
      {ok ? "后端在线" : "后端未就绪"}
    </div>
  );
}

function RootLayout(): React.ReactElement {
  return (
    <div className="flex h-screen w-screen overflow-hidden">
      <aside className="flex w-60 shrink-0 flex-col border-r border-border bg-card">
        <div className="px-4 py-4 border-b border-border">
          <div className="text-sm font-semibold">华莱士报表中心</div>
          <div className="text-xs text-muted-foreground">Wallace Report Hub</div>
        </div>
        <nav className="flex-1 p-2 space-y-1">
          {NAV.map((item) => (
            <Link
              key={item.to}
              to={item.to}
              activeOptions={{ exact: item.to === "/" }}
              className="flex items-center gap-2 rounded-md px-3 py-2 text-sm text-muted-foreground hover:bg-accent hover:text-foreground transition-colors [&.active]:bg-accent [&.active]:text-foreground [&.active]:font-medium"
            >
              <item.icon className="size-4" />
              {item.label}
            </Link>
          ))}
        </nav>
        <div className="p-4 border-t border-border">
          <HealthBadge />
        </div>
      </aside>
      <main className={cn("flex-1 overflow-auto")}>
        <Outlet />
      </main>
    </div>
  );
}

export const Route = createRootRoute({ component: RootLayout });
