import * as React from "react";
import { cn } from "@/lib/utils";

/*
 * ConvictionDial — the visual anchor of the product.
 *
 * The arc sweeps from bearish (left, brick) through neutral (top, amber)
 * to bullish (right, sage). The needle's angle directly maps to the
 * conviction score (0..100), so you get an instant gut-read of "leaning
 * bullish / bearish / flat" before reading the number.
 *
 * This is deliberately not a generic radial progress ring. It mirrors a
 * speedometer because the cognitive map is already in everyone's head:
 * left = slow/bad, right = fast/good. Across PSX tickers right now, the
 * scoring formula clusters most needles just barely past center, which
 * is itself information — the design embraces that instead of hiding it.
 */

interface ConvictionDialProps {
  score: number | null | undefined;
  signal?: string;
  size?: "sm" | "md" | "lg";
  showLabel?: boolean;
  className?: string;
}

const SIZE_MAP = {
  sm: { width: 120, height: 80, fontSize: 22, signalSize: 9 },
  md: { width: 220, height: 140, fontSize: 44, signalSize: 11 },
  lg: { width: 320, height: 200, fontSize: 64, signalSize: 13 },
} as const;

function scoreToAngle(score: number): number {
  // 0  -> -90deg (hard left)
  // 50 -> 0deg   (top)
  // 100-> +90deg (hard right)
  return ((Math.max(0, Math.min(100, score)) - 50) / 50) * 90;
}

function signalLabel(signal?: string, score?: number | null): string {
  if (signal && signal !== "NEUTRAL") return signal.replace("_", " ");
  if (score === null || score === undefined) return "—";
  if (score >= 70) return "BULLISH";
  if (score >= 55) return "MILDLY BULLISH";
  if (score >= 45) return "BALANCED";
  if (score >= 30) return "MILDLY BEARISH";
  return "BEARISH";
}

export function ConvictionDial({
  score,
  signal,
  size = "md",
  showLabel = true,
  className,
}: ConvictionDialProps) {
  const dims = SIZE_MAP[size];
  const hasScore = typeof score === "number" && Number.isFinite(score);
  const angle = hasScore ? scoreToAngle(score) : 0;

  // SVG geometry: arc centered at (cx, cy) with radius r.
  const cx = dims.width / 2;
  const cy = dims.height - 12;
  const r = Math.min(dims.width / 2 - 12, dims.height - 24);
  const stroke = size === "sm" ? 8 : 12;

  // arc path from left end to right end (semicircle)
  const startX = cx - r;
  const startY = cy;
  const endX = cx + r;
  const endY = cy;
  const arcPath = `M ${startX} ${startY} A ${r} ${r} 0 0 1 ${endX} ${endY}`;

  // needle: starts at center, points up; we rotate via transform
  const needleLen = r - stroke - 4;
  const needleColor = hasScore
    ? score >= 55
      ? "hsl(var(--bullish))"
      : score <= 45
      ? "hsl(var(--bearish))"
      : "hsl(var(--neutral))"
    : "hsl(var(--muted-foreground))";

  return (
    <div className={cn("relative inline-flex flex-col items-center", className)}>
      <svg
        width={dims.width}
        height={dims.height}
        viewBox={`0 0 ${dims.width} ${dims.height}`}
        role="img"
        aria-label={
          hasScore
            ? `Conviction score ${score.toFixed(1)} of 100 — ${signalLabel(signal, score)}`
            : "Conviction score not available"
        }
      >
        <defs>
          <linearGradient id="dial-arc" x1="0" y1="0" x2="1" y2="0">
            <stop offset="0%" stopColor="hsl(var(--bearish))" stopOpacity="0.85" />
            <stop offset="50%" stopColor="hsl(var(--neutral))" stopOpacity="0.75" />
            <stop offset="100%" stopColor="hsl(var(--bullish))" stopOpacity="0.85" />
          </linearGradient>
        </defs>

        {/* track */}
        <path
          d={arcPath}
          fill="none"
          stroke="hsl(var(--surface))"
          strokeWidth={stroke}
          strokeLinecap="round"
        />
        {/* gradient sweep */}
        <path
          d={arcPath}
          fill="none"
          stroke="url(#dial-arc)"
          strokeWidth={stroke}
          strokeLinecap="round"
        />

        {/* tick marks at 25/50/75 for reference */}
        {[25, 50, 75].map((tick) => {
          const tickAngle = scoreToAngle(tick) - 90;
          const rad = (tickAngle * Math.PI) / 180;
          const inner = r - stroke / 2 - 4;
          const outer = r - stroke / 2 + 4;
          return (
            <line
              key={tick}
              x1={cx + Math.cos(rad) * inner}
              y1={cy + Math.sin(rad) * inner}
              x2={cx + Math.cos(rad) * outer}
              y2={cy + Math.sin(rad) * outer}
              stroke="hsl(var(--background))"
              strokeWidth={2}
              strokeLinecap="round"
            />
          );
        })}

        {/* needle */}
        <g
          style={{
            transform: `rotate(${angle}deg)`,
            transformOrigin: `${cx}px ${cy}px`,
            transition: "transform 0.9s cubic-bezier(0.22, 1, 0.36, 1)",
          }}
        >
          <line
            x1={cx}
            y1={cy}
            x2={cx}
            y2={cy - needleLen}
            stroke={needleColor}
            strokeWidth={size === "sm" ? 2 : 3}
            strokeLinecap="round"
          />
          <circle
            cx={cx}
            cy={cy}
            r={size === "sm" ? 4 : 6}
            fill={needleColor}
          />
          <circle
            cx={cx}
            cy={cy}
            r={size === "sm" ? 1.5 : 2.5}
            fill="hsl(var(--card))"
          />
        </g>
      </svg>

      {showLabel && (
        <div className="-mt-2 flex flex-col items-center">
          <span
            className="font-display tabular-nums leading-none text-foreground"
            style={{ fontSize: dims.fontSize }}
          >
            {hasScore ? score.toFixed(1) : "—"}
          </span>
          <span
            className="mt-1.5 font-display italic tracking-wide text-muted-foreground"
            style={{ fontSize: dims.signalSize }}
          >
            {signalLabel(signal, score)}
          </span>
        </div>
      )}
    </div>
  );
}
