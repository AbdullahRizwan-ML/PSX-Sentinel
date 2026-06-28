"use client";

/*
 * NewsList — paginated relevant-articles list for a company.
 *
 * Design lock from the Phase 4 Session 5 prompt:
 *   "Show only the LLM-judged-relevant articles as the primary list —
 *    do not show the noisy raw-keyword-matched set, and do not build
 *    a collapsed/expandable 'other matched articles' section."
 *
 * The honest implementation runs into one data constraint:
 * NewsSynthesizer (see backend/app/agents/news_synthesizer.py) only
 * persists an aggregate `relevant_articles` *count* — it doesn't tag
 * per-article relevance flags. Per the session's hard rules, the
 * agent's LLM prompt cannot be changed to start emitting per-article
 * judgments, only additive metadata. So we can faithfully render the
 * matched articles ONLY when the LLM judged 100% of them relevant
 * (when relevant_articles === article_count); for any partial case we
 * cannot identify which specific articles to keep, so we honor the
 * lock by showing none with an honest explanation rather than picking
 * an arbitrary subset that would imply the LLM had judged those
 * specific ones relevant.
 *
 * That gives us four meaningfully different "what to render" states,
 * plus the obvious loading/error/no-report-yet states:
 *
 *   - LOADING                             → skeleton
 *   - ERROR                               → ErrorState with retry
 *   - NO_REPORT_YET                       → empty CTA pointing at the analyze button
 *                                            (no news_synthesis present means we
 *                                            have no LLM judgment to filter on; the
 *                                            raw article list would be the noisy
 *                                            keyword-matched set the lock forbids)
 *   - NO_ARTICLES_MATCHED                 → "No news articles found for [TICKER]"
 *                                            (article_count === 0, LLM call skipped
 *                                            entirely per "skip when no real data")
 *   - MATCHED_BUT_NONE_RELEVANT           → "N articles mentioned [TICKER] but
 *                                            NewsSynthesizer judged none genuinely
 *                                            relevant — likely tangential
 *                                            keyword-match noise"
 *                                            (article_count > 0, relevant_articles === 0)
 *   - PARTIAL_RELEVANCE                   → "M of N articles judged relevant; per-article
 *                                            details aren't surfaced — see narrative summary"
 *                                            (0 < relevant_articles < article_count)
 *   - FULL_RELEVANCE                      → render all matched articles
 *                                            (relevant_articles === article_count > 0)
 *
 * Live DB at Phase 4 Session 5 (2026-06-28) has real examples of
 * NO_ARTICLES_MATCHED (MCB, UBL) and MATCHED_BUT_NONE_RELEVANT (PPL).
 * No live ticker exercises PARTIAL_RELEVANCE or FULL_RELEVANCE today —
 * those code paths are wired but untestable against current data, and
 * that's flagged explicitly in the session's BUILD_LOG entry.
 */

import * as React from "react";
import {
  AlertTriangle,
  CheckCircle2,
  ExternalLink,
  Newspaper,
  RefreshCw,
  ScanSearch,
} from "lucide-react";

import { getCompanyNews } from "@/lib/api/companies";
import { ApiError } from "@/lib/api/client";
import type {
  IntelligenceReportResponse,
  NewsArticleResponse,
  NewsSynthesis,
} from "@/lib/api/types";

import { Card, CardContent } from "@/components/ui/card";
import { cn } from "@/lib/utils";

interface NewsListProps {
  ticker: string;
  report: IntelligenceReportResponse | null;
  className?: string;
}

type Mode =
  | "loading"
  | "error"
  | "no-report-yet"
  | "no-articles-matched"
  | "matched-none-relevant"
  | "partial-relevance"
  | "full-relevance";

function classifyMode(
  articles: NewsArticleResponse[] | null,
  loading: boolean,
  error: string | null,
  report: IntelligenceReportResponse | null
): Mode {
  if (loading) return "loading";
  if (error) return "error";
  if (!report || !report.news_synthesis) return "no-report-yet";
  const { article_count, relevant_articles } = report.news_synthesis;
  if (article_count === 0) return "no-articles-matched";
  if (relevant_articles === 0) return "matched-none-relevant";
  if (relevant_articles < article_count) return "partial-relevance";
  if (articles && articles.length > 0) return "full-relevance";
  return "no-articles-matched";
}

export function NewsList({ ticker, report, className }: NewsListProps) {
  const [articles, setArticles] = React.useState<NewsArticleResponse[] | null>(
    null
  );
  const [loading, setLoading] = React.useState(true);
  const [error, setError] = React.useState<string | null>(null);

  const load = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await getCompanyNews(ticker, { limit: 50 });
      // Backend already sorts by published_at DESC.
      setArticles(res.items);
    } catch (err) {
      if (err instanceof ApiError && err.status === 404) {
        setError(`No company found with ticker "${ticker}".`);
      } else if (err instanceof ApiError) {
        setError(err.message);
      } else {
        setError("Couldn't load news articles.");
      }
    } finally {
      setLoading(false);
    }
  }, [ticker]);

  React.useEffect(() => {
    void load();
  }, [load]);

  const mode = classifyMode(articles, loading, error, report);
  const ns = report?.news_synthesis ?? null;

  return (
    <Card className={cn("overflow-hidden", className)}>
      <CardContent className="p-6">
        <Header ticker={ticker} mode={mode} synthesis={ns} onRetry={load} />

        <div className="mt-5">
          {mode === "loading" && <ListSkeleton />}
          {mode === "error" && (
            <ErrorBlock message={error ?? "Couldn't load news."} onRetry={load} />
          )}
          {mode === "no-report-yet" && <NoReportYet ticker={ticker} />}
          {mode === "no-articles-matched" && (
            <ZeroState
              icon={<ScanSearch className="h-4 w-4" />}
              title={`No news articles found for ${ticker}`}
              body={
                `NewsSynthesizer didn't find any articles mentioning ` +
                `${ticker} in the ARY News business feed for this run, ` +
                `so it skipped its relevance judgment entirely. New ` +
                `coverage will show up here once the next data ` +
                `collection cycle picks it up.`
              }
            />
          )}
          {mode === "matched-none-relevant" && ns && (
            <ZeroState
              icon={<AlertTriangle className="h-4 w-4 text-accent" />}
              title={
                `${ns.article_count} ${
                  ns.article_count === 1 ? "article mentioned" : "articles mentioned"
                } ${ticker}, but none judged genuinely relevant`
              }
              body={
                `NewsSynthesizer reviewed each headline and judged 0 ` +
                `of ${ns.article_count} as actually about the company ` +
                `(typically tangential keyword matches — e.g. general ` +
                `"petroleum" or "gold prices" headlines that happen ` +
                `to mention ${ticker}). The matched set isn't shown ` +
                `because it would be noise, not signal. See the bull ` +
                `and bear cases above for the analyst narrative.`
              }
            />
          )}
          {mode === "partial-relevance" && ns && (
            <ZeroState
              icon={<AlertTriangle className="h-4 w-4 text-accent" />}
              title={
                `${ns.relevant_articles} of ${ns.article_count} matched articles ` +
                `judged relevant`
              }
              body={
                `NewsSynthesizer judged ${ns.relevant_articles} of the ` +
                `${ns.article_count} matched headlines as genuinely ` +
                `about ${ticker}, but doesn't currently surface ` +
                `per-article relevance — only the aggregate count. ` +
                `Listing all matched articles would mix signal with ` +
                `noise (showing articles the system itself judged ` +
                `irrelevant), so the list is held back. See the bull ` +
                `and bear cases above for the analyst narrative ` +
                `built from the relevant subset.`
              }
            />
          )}
          {mode === "full-relevance" && articles && ns && (
            <ArticleList
              articles={articles}
              synthesis={ns}
            />
          )}
        </div>
      </CardContent>
    </Card>
  );
}

function Header({
  ticker,
  mode,
  synthesis,
  onRetry,
}: {
  ticker: string;
  mode: Mode;
  synthesis: NewsSynthesis | null;
  onRetry: () => void;
}) {
  const subtitle = headerSubtitle(mode, synthesis, ticker);
  return (
    <div className="flex flex-wrap items-start justify-between gap-3">
      <div>
        <div className="flex items-center gap-2 text-xs uppercase tracking-wider text-muted-foreground">
          <Newspaper className="h-3.5 w-3.5" />
          News coverage
        </div>
        <p className="mt-1.5 text-sm text-foreground">{subtitle}</p>
      </div>
      {(mode === "full-relevance" || mode === "error") && (
        <button
          type="button"
          className="focus-ring inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-surface hover:text-foreground"
          onClick={onRetry}
        >
          <RefreshCw className="h-3 w-3" />
          Refresh
        </button>
      )}
    </div>
  );
}

function headerSubtitle(
  mode: Mode,
  synthesis: NewsSynthesis | null,
  ticker: string
): React.ReactNode {
  switch (mode) {
    case "loading":
      return `Loading news for ${ticker}…`;
    case "error":
      return `Couldn't load news for ${ticker}.`;
    case "no-report-yet":
      return (
        <>
          Run the analysis pipeline to see which articles NewsSynthesizer
          judges relevant to {ticker}.
        </>
      );
    case "no-articles-matched":
      return (
        <>
          No articles mentioning {ticker} in the current news set.
        </>
      );
    case "matched-none-relevant": {
      const count = synthesis?.article_count ?? 0;
      return (
        <>
          {count} {count === 1 ? "matched article" : "matched articles"} —{" "}
          <span className="font-medium text-foreground">
            0 judged relevant
          </span>{" "}
          by NewsSynthesizer.
        </>
      );
    }
    case "partial-relevance": {
      const rel = synthesis?.relevant_articles ?? 0;
      const total = synthesis?.article_count ?? 0;
      return (
        <>
          <span className="font-medium text-foreground">
            {rel} of {total}
          </span>{" "}
          matched articles judged relevant by NewsSynthesizer.
        </>
      );
    }
    case "full-relevance": {
      const count = synthesis?.article_count ?? 0;
      return (
        <>
          {count} {count === 1 ? "article" : "articles"} —{" "}
          <span className="font-medium text-foreground">
            all judged relevant
          </span>{" "}
          by NewsSynthesizer.
        </>
      );
    }
  }
}

function ArticleList({
  articles,
  synthesis,
}: {
  articles: NewsArticleResponse[];
  synthesis: NewsSynthesis;
}) {
  return (
    <>
      <div className="flex items-center gap-2 rounded-md border border-bullish/30 bg-bullish-muted/30 px-3 py-2 text-xs text-foreground">
        <CheckCircle2 className="h-3.5 w-3.5 text-bullish" />
        <span>
          All {synthesis.article_count}{" "}
          {synthesis.article_count === 1 ? "article was" : "articles were"}{" "}
          judged genuinely relevant. Listed by publication date, most
          recent first.
        </span>
      </div>
      <ul className="mt-4 divide-y divide-border">
        {articles.map((article) => (
          <ArticleRow key={article.id} article={article} />
        ))}
      </ul>
    </>
  );
}

function ArticleRow({ article }: { article: NewsArticleResponse }) {
  const published = formatPublishedDate(article.published_at);
  const hasUrl = !!article.url;
  return (
    <li className="py-4 first:pt-0 last:pb-0">
      <div className="flex items-baseline justify-between gap-3">
        <div className="flex items-center gap-2 text-[10px] uppercase tracking-wider text-muted-foreground">
          <span>{prettySource(article.source)}</span>
          <span aria-hidden>·</span>
          <span className="tabular-nums">{published}</span>
        </div>
      </div>
      {hasUrl ? (
        <a
          href={article.url}
          target="_blank"
          rel="noopener noreferrer"
          className="focus-ring group mt-1.5 inline-flex items-start gap-1.5 rounded-sm text-sm font-medium leading-snug text-foreground hover:text-primary"
        >
          <span className="underline decoration-transparent underline-offset-2 transition-colors group-hover:decoration-primary/40">
            {article.headline}
          </span>
          <ExternalLink className="mt-0.5 h-3 w-3 shrink-0 text-muted-foreground transition-colors group-hover:text-primary" />
        </a>
      ) : (
        <p className="mt-1.5 text-sm font-medium leading-snug text-foreground">
          {article.headline}
        </p>
      )}
      {article.summary && (
        <p className="mt-1.5 line-clamp-2 text-sm leading-relaxed text-muted-foreground">
          {article.summary}
        </p>
      )}
    </li>
  );
}

function ZeroState({
  icon,
  title,
  body,
}: {
  icon: React.ReactNode;
  title: string;
  body: string;
}) {
  return (
    <div className="rounded-md border border-dashed border-border bg-surface/40 p-5">
      <div className="flex items-start gap-3">
        <div className="mt-0.5 text-muted-foreground">{icon}</div>
        <div className="flex-1">
          <p className="text-sm font-medium text-foreground">{title}</p>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            {body}
          </p>
        </div>
      </div>
    </div>
  );
}

function NoReportYet({ ticker }: { ticker: string }) {
  return (
    <div className="rounded-md border border-dashed border-border bg-surface/40 p-5">
      <div className="flex items-start gap-3">
        <Newspaper className="mt-0.5 h-4 w-4 text-muted-foreground" />
        <div className="flex-1">
          <p className="text-sm font-medium text-foreground">
            No relevance judgment yet for {ticker}
          </p>
          <p className="mt-1.5 text-xs leading-relaxed text-muted-foreground">
            Generate an intelligence report (using the button above) and
            NewsSynthesizer will review the current article set to decide
            which headlines are genuinely about {ticker} versus tangential
            keyword matches. Only judged-relevant articles are shown
            here.
          </p>
        </div>
      </div>
    </div>
  );
}

function ErrorBlock({
  message,
  onRetry,
}: {
  message: string;
  onRetry: () => void;
}) {
  return (
    <div
      role="alert"
      className="flex items-start gap-3 rounded-md border border-bearish/30 bg-bearish-muted/40 p-4"
    >
      <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-bearish" />
      <div className="flex-1">
        <div className="text-sm font-medium text-foreground">
          Couldn&apos;t load news
        </div>
        <div className="mt-0.5 text-xs text-muted-foreground">{message}</div>
        <button
          type="button"
          className="focus-ring mt-3 inline-flex items-center gap-1.5 rounded-md border border-border bg-card px-2.5 py-1.5 text-xs hover:bg-surface"
          onClick={onRetry}
        >
          <RefreshCw className="h-3 w-3" />
          Try again
        </button>
      </div>
    </div>
  );
}

function ListSkeleton() {
  return (
    <ul className="space-y-4">
      {[0, 1, 2].map((i) => (
        <li key={i} className="space-y-2">
          <div className="h-3 w-32 animate-soft-pulse rounded bg-surface" />
          <div className="h-4 w-3/4 animate-soft-pulse rounded bg-surface" />
          <div className="h-3 w-full animate-soft-pulse rounded bg-surface" />
        </li>
      ))}
    </ul>
  );
}

const SOURCE_LABELS: Record<string, string> = {
  arynews: "ARY News",
  ary_news: "ARY News",
};

function prettySource(source: string): string {
  const lower = (source ?? "").toLowerCase();
  return SOURCE_LABELS[lower] ?? source;
}

function formatPublishedDate(iso: string): string {
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso.slice(0, 10);
  return d.toLocaleDateString("en-PK", {
    year: "numeric",
    month: "short",
    day: "numeric",
  });
}
