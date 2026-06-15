import * as React from "react";
import { cn } from "@/shared/lib/cn";

interface TabsProps {
  tabs: { key: string; label: React.ReactNode }[];
  active: string;
  onChange: (key: string) => void;
  className?: string;
}

export function Tabs({
  tabs,
  active,
  onChange,
  className,
}: TabsProps): React.ReactElement {
  return (
    <div
      className={cn(
        "flex gap-1 border-b border-border overflow-x-auto",
        className,
      )}
    >
      {tabs.map((t) => (
        <button
          key={t.key}
          type="button"
          onClick={() => onChange(t.key)}
          className={cn(
            "px-3 py-2 text-sm font-medium border-b-2 -mb-px whitespace-nowrap transition-colors",
            active === t.key
              ? "border-primary text-foreground"
              : "border-transparent text-muted-foreground hover:text-foreground",
          )}
        >
          {t.label}
        </button>
      ))}
    </div>
  );
}
