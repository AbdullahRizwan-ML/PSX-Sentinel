import Link from "next/link";
import { cn } from "@/lib/utils";

interface BrandProps {
  className?: string;
  size?: "sm" | "md" | "lg";
  asLink?: boolean;
}

export function Brand({ className, size = "md", asLink = true }: BrandProps) {
  const inner = (
    <span
      className={cn(
        "inline-flex items-center gap-2 font-display tracking-tight text-foreground",
        size === "sm" && "text-lg",
        size === "md" && "text-2xl",
        size === "lg" && "text-3xl",
        className
      )}
    >
      <span
        aria-hidden
        className={cn(
          "relative inline-flex items-center justify-center rounded-md bg-primary text-primary-foreground",
          size === "sm" && "h-6 w-6 text-[10px]",
          size === "md" && "h-8 w-8 text-xs",
          size === "lg" && "h-10 w-10 text-sm"
        )}
      >
        <svg viewBox="0 0 24 24" className="h-1/2 w-1/2" fill="none">
          <path
            d="M3 12 L9 12 L12 6 L15 18 L18 12 L21 12"
            stroke="currentColor"
            strokeWidth="2.2"
            strokeLinecap="round"
            strokeLinejoin="round"
          />
        </svg>
      </span>
      <span>
        PSX <span className="italic text-primary">Sentinel</span>
      </span>
    </span>
  );
  if (asLink) {
    return (
      <Link href="/dashboard" className="focus-ring rounded-md">
        {inner}
      </Link>
    );
  }
  return inner;
}
