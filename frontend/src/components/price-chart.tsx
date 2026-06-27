"use client";

/*
 * PriceChart — close-price line + MA20/MA50 overlays + volume histogram
 * in a synced lower pane, rendered via lightweight-charts (TradingView).
 *
 * Why close-line not candlesticks: PSX DPS doesn't provide real intraday
 * high/low — `daily_prices.high`/`low` are derived as max/min of
 * open/close (see docs/KNOWN_ISSUES.md). Candle wicks would be
 * structurally invisible on every candle, which is dishonest decoration.
 * A close-price line uses only real, unapproximated data. The caption
 * below the chart states this limitation plainly rather than hiding it.
 *
 * Why client-side MAs: getCompanyPriceHistory pulls the full ~2-year
 * series in one request (backend ceiling raised from 365 → 2000 rows
 * this session, on the /prices endpoint's query window only — no agent
 * code touched). MA20/MA50 are computed once over the whole series, so
 * the overlays are accurate from the first visible day of the smallest
 * range (1M), not ramping up out of zero. Formula matches
 * backend/app/ml/features.py::_compute_indicators exactly: simple
 * unweighted rolling mean of close, window=20 / 50, min_periods=window.
 *
 * Why one chart, two panes: lightweight-charts' v5 pane API
 * (`addSeries(..., paneIndex)`) shares the time axis between panes
 * automatically — no custom sync logic, no separate components to wire.
 *
 * Why no candles for color: there's no real intraday range to color, so
 * we tint the *volume* histogram by direction (close > prev close =
 * bullish-muted, close < prev close = bearish-muted) instead. The close
 * line itself stays a single primary-brand color — honest about what it
 * is (one value per day).
 */

import * as React from "react";
import {
  AreaSeries,
  ColorType,
  CrosshairMode,
  HistogramSeries,
  LineSeries,
  LineStyle,
  PriceScaleMode,
  createChart,
  type IChartApi,
  type ISeriesApi,
  type LineData,
  type HistogramData,
  type Time,
  type AreaData,
} from "lightweight-charts";

import { AlertTriangle, Info } from "lucide-react";

import { getCompanyPriceHistory } from "@/lib/api/companies";
import { ApiError } from "@/lib/api/client";
import type { PricePoint } from "@/lib/api/types";
import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

type Range = "1M" | "3M" | "6M" | "1Y" | "ALL";

const RANGE_TRADING_DAYS: Record<Exclude<Range, "ALL">, number> = {
  "1M": 22,
  "3M": 63,
  "6M": 126,
  "1Y": 252,
};

const RANGES: Range[] = ["1M", "3M", "6M", "1Y", "ALL"];

const DEFAULT_RANGE: Range = "6M";

interface PriceChartProps {
  ticker: string;
  className?: string;
}

interface SeriesPoint {
  date: string; // YYYY-MM-DD
  close: number;
  volume: number;
  changeUp: boolean | null; // close > prev close ? true : false ; null at index 0
}

/*
 * Resolve a CSS variable like "--primary" (which we store as raw HSL
 * components, e.g. "192 65% 22%") into a real "hsl(192 65% 22%)" string
 * lightweight-charts will accept. We re-resolve on mount so the chart
 * picks up whatever theme is currently applied to <html>.
 */
function cssHsl(name: string, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  if (!raw) return fallback;
  return `hsl(${raw})`;
}

function cssHsla(name: string, alpha: number, fallback: string): string {
  if (typeof window === "undefined") return fallback;
  const raw = getComputedStyle(document.documentElement)
    .getPropertyValue(name)
    .trim();
  if (!raw) return fallback;
  return `hsla(${raw} / ${alpha})`;
}

function computeMA(
  points: SeriesPoint[],
  window: number
): LineData<Time>[] {
  if (points.length < window) return [];
  const out: LineData<Time>[] = [];
  let sum = 0;
  for (let i = 0; i < points.length; i++) {
    sum += points[i].close;
    if (i >= window) sum -= points[i - window].close;
    if (i >= window - 1) {
      out.push({
        time: points[i].date as Time,
        value: sum / window,
      });
    }
  }
  return out;
}

export function PriceChart({ ticker, className }: PriceChartProps) {
  const containerRef = React.useRef<HTMLDivElement | null>(null);
  const chartRef = React.useRef<IChartApi | null>(null);
  const priceSeriesRef = React.useRef<ISeriesApi<"Area"> | null>(null);
  const ma20SeriesRef = React.useRef<ISeriesApi<"Line"> | null>(null);
  const ma50SeriesRef = React.useRef<ISeriesApi<"Line"> | null>(null);
  const volumeSeriesRef = React.useRef<ISeriesApi<"Histogram"> | null>(null);

  const [prices, setPrices] = React.useState<SeriesPoint[] | null>(null);
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);
  const [range, setRange] = React.useState<Range>(DEFAULT_RANGE);

  // 1. Fetch ticker history.
  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const raw = await getCompanyPriceHistory(ticker);
      // Backend returns desc; sort ascending and compute changeUp.
      const sorted = [...raw].sort((a, b) =>
        a.date < b.date ? -1 : a.date > b.date ? 1 : 0
      );
      const out: SeriesPoint[] = sorted.map((p, i) => {
        const prev = i > 0 ? sorted[i - 1].close : null;
        const changeUp =
          prev === null
            ? null
            : p.close > prev
            ? true
            : p.close < prev
            ? false
            : null;
        return {
          date: p.date,
          close: p.close,
          volume: p.volume,
          changeUp,
        };
      });
      setPrices(out);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError(`No price history for ${ticker} yet.`);
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Couldn't load price history.");
      }
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  React.useEffect(() => {
    void load();
  }, [load]);

  // 2. Create chart once we have a container and data.
  React.useEffect(() => {
    if (!containerRef.current) return;
    if (!prices || prices.length === 0) return;

    const fg = cssHsl("--foreground", "hsl(195 30% 12%)");
    const fgMuted = cssHsla(
      "--foreground",
      0.42,
      "hsla(195 30% 12% / 0.42)"
    );
    const border = cssHsl("--border", "hsl(195 14% 86%)");
    const primary = cssHsl("--primary", "hsl(192 65% 22%)");
    const primaryFaint = cssHsla(
      "--primary",
      0.06,
      "hsla(192 65% 22% / 0.06)"
    );
    const accent = cssHsl("--accent", "hsl(14 65% 56%)");
    const neutral = cssHsl("--neutral", "hsl(38 35% 55%)");
    const bullishMuted = cssHsla(
      "--bullish",
      0.55,
      "hsla(158 28% 38% / 0.55)"
    );
    const bearishMuted = cssHsla(
      "--bearish",
      0.55,
      "hsla(6 50% 48% / 0.55)"
    );

    const chart = createChart(containerRef.current, {
      autoSize: true,
      layout: {
        background: { type: ColorType.Solid, color: "transparent" },
        textColor: fgMuted,
        fontFamily:
          "Inter, ui-sans-serif, system-ui, -apple-system, sans-serif",
        fontSize: 11,
        panes: {
          separatorColor: border,
          separatorHoverColor: border,
          enableResize: false,
        },
      },
      grid: {
        vertLines: { color: "transparent" },
        horzLines: { color: cssHsla("--border", 0.5, border) },
      },
      rightPriceScale: {
        borderVisible: false,
        mode: PriceScaleMode.Normal,
        scaleMargins: { top: 0.1, bottom: 0.05 },
      },
      timeScale: {
        borderVisible: false,
        timeVisible: false,
        secondsVisible: false,
      },
      crosshair: {
        mode: CrosshairMode.Magnet,
        vertLine: {
          color: fgMuted,
          width: 1,
          style: LineStyle.Dotted,
        },
        horzLine: {
          color: fgMuted,
          width: 1,
          style: LineStyle.Dotted,
        },
      },
      handleScale: { mouseWheel: true, pinch: true, axisPressedMouseMove: true },
      handleScroll: { mouseWheel: false, pressedMouseMove: true },
    });
    chartRef.current = chart;

    // Close-price as an area (subtle teal fill = "this is a price level,
    // not a candle"). Stays on pane 0.
    const priceSeries = chart.addSeries(AreaSeries, {
      lineColor: primary,
      lineWidth: 2,
      topColor: primaryFaint,
      bottomColor: "transparent",
      priceLineVisible: true,
      priceLineColor: fgMuted,
      priceLineStyle: LineStyle.Dotted,
      lastValueVisible: true,
    });
    priceSeriesRef.current = priceSeries;

    const ma20Series = chart.addSeries(LineSeries, {
      color: accent,
      lineWidth: 1,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: true,
    });
    ma20SeriesRef.current = ma20Series;

    const ma50Series = chart.addSeries(LineSeries, {
      color: neutral,
      lineWidth: 1,
      lineStyle: LineStyle.Dashed,
      priceLineVisible: false,
      lastValueVisible: false,
      crosshairMarkerVisible: true,
    });
    ma50SeriesRef.current = ma50Series;

    // Volume on its own pane (paneIndex=1), shares the time axis.
    const volumeSeries = chart.addSeries(
      HistogramSeries,
      {
        priceFormat: { type: "volume" },
        priceLineVisible: false,
        lastValueVisible: false,
      },
      1
    );
    volumeSeriesRef.current = volumeSeries;

    // Push initial data.
    const priceData: AreaData<Time>[] = prices.map((p) => ({
      time: p.date as Time,
      value: p.close,
    }));
    const ma20Data = computeMA(prices, 20);
    const ma50Data = computeMA(prices, 50);
    const volumeData: HistogramData<Time>[] = prices.map((p) => ({
      time: p.date as Time,
      value: p.volume,
      color:
        p.changeUp === true
          ? bullishMuted
          : p.changeUp === false
          ? bearishMuted
          : fgMuted,
    }));

    priceSeries.setData(priceData);
    ma20Series.setData(ma20Data);
    ma50Series.setData(ma50Data);
    volumeSeries.setData(volumeData);

    // Make the volume pane shorter than the price pane.
    try {
      chart.panes()[1]?.setHeight(80);
    } catch {
      // setHeight may not exist in older builds — fall back silently.
    }

    return () => {
      chart.remove();
      chartRef.current = null;
      priceSeriesRef.current = null;
      ma20SeriesRef.current = null;
      ma50SeriesRef.current = null;
      volumeSeriesRef.current = null;
    };
  }, [prices]);

  // 3. Apply visible range whenever the user changes it or data arrives.
  React.useEffect(() => {
    const chart = chartRef.current;
    if (!chart || !prices || prices.length === 0) return;

    if (range === "ALL") {
      chart.timeScale().fitContent();
      return;
    }

    const want = RANGE_TRADING_DAYS[range];
    // Degrade gracefully if the ticker has fewer rows than the requested
    // range — e.g. ENGRO has a shorter history (see KNOWN_ISSUES.md), and
    // a hypothetical newly-seeded ticker could have < 22 rows. Fall back
    // to ALL in that case so the chart never shows an empty window.
    if (prices.length <= want) {
      chart.timeScale().fitContent();
      return;
    }
    const first = prices[prices.length - want];
    const last = prices[prices.length - 1];
    chart.timeScale().setVisibleRange({
      from: first.date as Time,
      to: last.date as Time,
    });
  }, [range, prices]);

  const lastPoint = prices && prices.length > 0 ? prices[prices.length - 1] : null;
  const firstPoint = prices && prices.length > 0 ? prices[0] : null;

  return (
    <Card className={cn("overflow-hidden", className)}>
      <CardContent className="p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
              <span>Price history</span>
              {firstPoint && lastPoint && (
                <span className="text-muted-foreground/70">
                  · {firstPoint.date} → {lastPoint.date}
                </span>
              )}
            </div>
            <p className="mt-1 text-sm text-foreground">
              Close-price line with{" "}
              <span className="font-medium" style={{ color: "hsl(var(--accent))" }}>
                MA20
              </span>{" "}
              and{" "}
              <span
                className="font-medium"
                style={{ color: "hsl(var(--neutral))" }}
              >
                MA50
              </span>{" "}
              overlays, volume below.
            </p>
          </div>
          <RangeSelector
            range={range}
            onChange={setRange}
            disabled={loading || !!error || !prices}
          />
        </div>

        <div className="mt-4">
          {loading && <ChartSkeleton />}
          {!loading && error && (
            <div className="flex h-[360px] items-center justify-center rounded-md border border-bearish/30 bg-bearish-muted/30 p-6">
              <div className="flex items-start gap-3 text-sm">
                <AlertTriangle className="mt-0.5 h-4 w-4 text-bearish" />
                <div>
                  <div className="font-medium text-foreground">
                    Couldn&apos;t load chart
                  </div>
                  <div className="mt-0.5 text-muted-foreground">{error}</div>
                  <button
                    type="button"
                    className="focus-ring mt-3 rounded-md border border-border bg-card px-3 py-1.5 text-xs hover:bg-surface"
                    onClick={() => void load()}
                  >
                    Try again
                  </button>
                </div>
              </div>
            </div>
          )}
          {!loading && !error && prices && prices.length === 0 && (
            <div className="flex h-[360px] items-center justify-center rounded-md border border-dashed border-border bg-surface/40 p-6">
              <div className="text-center text-sm text-muted-foreground">
                No price history yet for {ticker}.
              </div>
            </div>
          )}
          {!loading && !error && prices && prices.length > 0 && (
            <div
              ref={containerRef}
              className="h-[360px] w-full"
              role="img"
              aria-label={`${ticker} close-price chart with MA20 and MA50 overlays`}
            />
          )}
        </div>

        <div className="mt-3 flex items-start gap-1.5 text-[11px] leading-relaxed text-muted-foreground">
          <Info className="mt-0.5 h-3 w-3 shrink-0" />
          <span>
            PSX DPS doesn&apos;t publish intraday high/low — only daily open,
            close, and volume — so this chart shows the close-price line
            rather than a candlestick. Volume bars are tinted by close-vs-
            previous-close direction.
          </span>
        </div>
      </CardContent>
    </Card>
  );
}

function RangeSelector({
  range,
  onChange,
  disabled,
}: {
  range: Range;
  onChange: (r: Range) => void;
  disabled: boolean;
}) {
  return (
    <div
      role="group"
      aria-label="Chart time range"
      className="inline-flex rounded-md border border-border bg-card p-0.5"
    >
      {RANGES.map((r) => {
        const active = r === range;
        return (
          <button
            key={r}
            type="button"
            disabled={disabled}
            onClick={() => onChange(r)}
            className={cn(
              "focus-ring rounded-[0.3rem] px-2.5 py-1 text-[11px] font-medium uppercase tracking-wider transition-colors",
              active
                ? "bg-primary text-primary-foreground"
                : "text-muted-foreground hover:bg-surface hover:text-foreground",
              disabled && "cursor-not-allowed opacity-50"
            )}
            aria-pressed={active}
          >
            {r}
          </button>
        );
      })}
    </div>
  );
}

function ChartSkeleton() {
  return (
    <div className="h-[360px] w-full animate-soft-pulse rounded-md border border-border bg-surface/40" />
  );
}

// re-export PricePoint type for the page so its imports stay tight
export type { PricePoint };
