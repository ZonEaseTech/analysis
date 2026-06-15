import { useMutation, useQuery } from "@tanstack/react-query";
import type {
  RunResponse,
  ScriptDetail,
  ScriptsResponse,
} from "@/shared/lib/api-types";
import { apiGet, apiPost } from "@/shared/lib/http";

export function useScriptsQuery() {
  return useQuery({
    queryKey: ["scripts"],
    queryFn: () => apiGet<ScriptsResponse>("/scripts"),
  });
}

export function useScriptDetailQuery(id: string | null) {
  return useQuery({
    queryKey: ["script", id],
    queryFn: () => apiGet<ScriptDetail>(`/scripts/${id}`),
    enabled: id !== null,
  });
}

export function useRunScriptMutation() {
  return useMutation({
    mutationFn: (id: string) => apiPost<RunResponse>(`/scripts/${id}/run`),
  });
}
