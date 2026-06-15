import { QueryClientProvider } from "@tanstack/react-query";
import * as React from "react";
import { queryClient } from "@/shared/lib/query-client";

export function Providers({
  children,
}: {
  children: React.ReactNode;
}): React.ReactElement {
  return (
    <QueryClientProvider client={queryClient}>{children}</QueryClientProvider>
  );
}
