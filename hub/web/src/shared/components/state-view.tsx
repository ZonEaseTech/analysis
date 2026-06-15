import { Loader2, ServerCrash } from "lucide-react";
import * as React from "react";
import { HttpError } from "@/shared/lib/http";

export function LoadingView({
  label = "加载中…",
}: {
  label?: string;
}): React.ReactElement {
  return (
    <div className="flex items-center justify-center gap-2 p-10 text-muted-foreground">
      <Loader2 className="size-4 animate-spin" />
      {label}
    </div>
  );
}

export function ErrorView({ error }: { error: unknown }): React.ReactElement {
  const notReady = error instanceof HttpError && error.status === 404;
  return (
    <div className="flex flex-col items-center justify-center gap-2 p-10 text-center text-muted-foreground">
      <ServerCrash className="size-6" />
      {notReady ? (
        <div>
          <div className="font-medium text-foreground">后端未就绪</div>
          <div className="text-sm">该接口尚未实现 (404)，请稍后再试。</div>
        </div>
      ) : (
        <div>
          <div className="font-medium text-foreground">加载失败</div>
          <div className="text-sm">
            {error instanceof Error ? error.message : String(error)}
          </div>
        </div>
      )}
    </div>
  );
}

export function EmptyView({
  label = "暂无数据",
}: {
  label?: string;
}): React.ReactElement {
  return (
    <div className="p-10 text-center text-sm text-muted-foreground">{label}</div>
  );
}
