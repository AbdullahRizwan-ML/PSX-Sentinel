import { cn } from "@/lib/utils";

interface SignalBadgeProps {
  signal: string | null | undefined;
  size?: "sm" | "md";
  className?: string;
}

const SIGNAL_STYLES: Record<string, string> = {
  STRONG_BUY: "bg-bullish text-bullish-foreground",
  BUY: "bg-bullish-muted text-bullish",
  NEUTRAL: "bg-muted text-muted-foreground",
  SELL: "bg-bearish-muted text-bearish",
  STRONG_SELL: "bg-bearish text-bearish-foreground",
};

const SIGNAL_LABEL: Record<string, string> = {
  STRONG_BUY: "Strong Buy",
  BUY: "Buy",
  NEUTRAL: "Neutral",
  SELL: "Sell",
  STRONG_SELL: "Strong Sell",
};

export function SignalBadge({
  signal,
  size = "md",
  className,
}: SignalBadgeProps) {
  const key = signal && SIGNAL_STYLES[signal] ? signal : "NEUTRAL";
  const label = SIGNAL_LABEL[key] ?? signal ?? "—";
  return (
    <span
      className={cn(
        "inline-flex items-center rounded-full font-medium uppercase tracking-wide",
        size === "sm" ? "px-2 py-0.5 text-[10px]" : "px-2.5 py-1 text-xs",
        SIGNAL_STYLES[key],
        className
      )}
    >
      {label}
    </span>
  );
}
