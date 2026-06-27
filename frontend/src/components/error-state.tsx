"use client";

import { AlertTriangle, RefreshCw } from "lucide-react";
import { Button } from "@/components/ui/button";
import { cn } from "@/lib/utils";

interface ErrorStateProps {
  title?: string;
  message: string;
  onRetry?: () => void;
  className?: string;
}

export function ErrorState({
  title = "Something went wrong",
  message,
  onRetry,
  className,
}: ErrorStateProps) {
  return (
    <div
      role="alert"
      className={cn(
        "flex items-start gap-3 rounded-lg border border-bearish/30 bg-bearish-muted/40 p-4",
        className
      )}
    >
      <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-bearish" />
      <div className="flex-1">
        <div className="font-medium text-foreground">{title}</div>
        <div className="mt-0.5 text-sm text-muted-foreground">{message}</div>
        {onRetry && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="mt-3"
            onClick={onRetry}
          >
            <RefreshCw className="h-3.5 w-3.5" />
            Try again
          </Button>
        )}
      </div>
    </div>
  );
}
