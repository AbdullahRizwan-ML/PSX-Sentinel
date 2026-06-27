import type { Config } from "tailwindcss";

const config: Config = {
  darkMode: ["class"],
  content: ["./src/**/*.{ts,tsx}"],
  theme: {
    container: {
      center: true,
      padding: "1.5rem",
      screens: {
        "2xl": "1280px",
      },
    },
    extend: {
      colors: {
        border: "hsl(var(--border))",
        input: "hsl(var(--input))",
        ring: "hsl(var(--ring))",
        background: "hsl(var(--background))",
        foreground: "hsl(var(--foreground))",
        primary: {
          DEFAULT: "hsl(var(--primary))",
          foreground: "hsl(var(--primary-foreground))",
        },
        accent: {
          DEFAULT: "hsl(var(--accent))",
          foreground: "hsl(var(--accent-foreground))",
        },
        muted: {
          DEFAULT: "hsl(var(--muted))",
          foreground: "hsl(var(--muted-foreground))",
        },
        card: {
          DEFAULT: "hsl(var(--card))",
          foreground: "hsl(var(--card-foreground))",
        },
        surface: {
          DEFAULT: "hsl(var(--surface))",
          foreground: "hsl(var(--surface-foreground))",
        },
        bullish: {
          DEFAULT: "hsl(var(--bullish))",
          foreground: "hsl(var(--bullish-foreground))",
          muted: "hsl(var(--bullish-muted))",
        },
        bearish: {
          DEFAULT: "hsl(var(--bearish))",
          foreground: "hsl(var(--bearish-foreground))",
          muted: "hsl(var(--bearish-muted))",
        },
        neutral: {
          DEFAULT: "hsl(var(--neutral))",
          foreground: "hsl(var(--neutral-foreground))",
        },
        destructive: {
          DEFAULT: "hsl(var(--destructive))",
          foreground: "hsl(var(--destructive-foreground))",
        },
      },
      fontFamily: {
        sans: ["var(--font-inter)", "system-ui", "sans-serif"],
        display: ["var(--font-fraunces)", "Georgia", "serif"],
        mono: ["ui-monospace", "SFMono-Regular", "Menlo", "monospace"],
      },
      fontSize: {
        "display-1": ["clamp(2.75rem, 5vw, 4rem)", { lineHeight: "1.05", letterSpacing: "-0.02em" }],
        "display-2": ["clamp(2rem, 3.5vw, 2.75rem)", { lineHeight: "1.1", letterSpacing: "-0.015em" }],
      },
      borderRadius: {
        lg: "var(--radius)",
        md: "calc(var(--radius) - 4px)",
        sm: "calc(var(--radius) - 8px)",
      },
      boxShadow: {
        soft: "0 1px 2px hsl(var(--shadow) / 0.04), 0 4px 16px hsl(var(--shadow) / 0.05)",
        lift: "0 1px 2px hsl(var(--shadow) / 0.06), 0 12px 28px hsl(var(--shadow) / 0.08)",
        ring: "0 0 0 1px hsl(var(--border)), 0 1px 2px hsl(var(--shadow) / 0.04)",
      },
      keyframes: {
        "fade-in": {
          from: { opacity: "0", transform: "translateY(4px)" },
          to: { opacity: "1", transform: "translateY(0)" },
        },
        "dial-sweep": {
          from: { transform: "rotate(-90deg)" },
          to: { transform: "rotate(var(--dial-angle, 0deg))" },
        },
        "soft-pulse": {
          "0%, 100%": { opacity: "0.55" },
          "50%": { opacity: "0.85" },
        },
      },
      animation: {
        "fade-in": "fade-in 0.32s cubic-bezier(0.22, 1, 0.36, 1)",
        "dial-sweep": "dial-sweep 0.9s cubic-bezier(0.22, 1, 0.36, 1) both",
        "soft-pulse": "soft-pulse 1.6s ease-in-out infinite",
      },
    },
  },
  plugins: [require("tailwindcss-animate")],
};

export default config;
