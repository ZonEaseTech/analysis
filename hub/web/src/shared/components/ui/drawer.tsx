import { X } from "lucide-react";
import * as React from "react";
import { cn } from "@/shared/lib/cn";

interface DrawerProps {
  open: boolean;
  onClose: () => void;
  title?: React.ReactNode;
  children: React.ReactNode;
  width?: string;
}

export function Drawer({
  open,
  onClose,
  title,
  children,
  width = "max-w-2xl",
}: DrawerProps): React.ReactElement | null {
  if (!open)
    return null;
  return (
    <div className="fixed inset-0 z-50 flex">
      <button
        type="button"
        aria-label="关闭"
        className="flex-1 bg-black/50 animate-in fade-in"
        onClick={onClose}
      />
      <div
        className={cn(
          "h-full w-full bg-card border-l border-border shadow-xl flex flex-col animate-in slide-in-from-right",
          width,
        )}
      >
        <div className="flex items-center justify-between p-4 border-b border-border">
          <div className="font-medium truncate">{title}</div>
          <button
            type="button"
            onClick={onClose}
            className="rounded-md p-1 hover:bg-accent"
            aria-label="关闭"
          >
            <X className="size-4" />
          </button>
        </div>
        <div className="flex-1 overflow-auto p-4">{children}</div>
      </div>
    </div>
  );
}
