import * as React from "react";
import { apiUrl } from "@/shared/lib/http";

export interface RunStreamState {
  lines: string[];
  running: boolean;
  exitCode: number | null;
  error: string | null;
}

export function useRunStream(runId: string | null): RunStreamState {
  const [state, setState] = React.useState<RunStreamState>({
    lines: [],
    running: false,
    exitCode: null,
    error: null,
  });

  React.useEffect(() => {
    if (!runId) return;
    setState({ lines: [], running: true, exitCode: null, error: null });

    const es = new EventSource(apiUrl(`/runs/${runId}/stream`));

    es.onmessage = (ev) => {
      setState((s) => ({ ...s, lines: [...s.lines, ev.data] }));
    };

    es.addEventListener("done", (ev) => {
      let exitCode: number | null = null;
      try {
        exitCode = (JSON.parse((ev as MessageEvent).data) as { exitCode: number })
          .exitCode;
      } catch {
        exitCode = null;
      }
      setState((s) => ({ ...s, running: false, exitCode }));
      es.close();
    });

    es.onerror = () => {
      setState((s) =>
        s.running
          ? { ...s, running: false, error: "日志流中断（后端可能未就绪）" }
          : s,
      );
      es.close();
    };

    return () => es.close();
  }, [runId]);

  return state;
}
