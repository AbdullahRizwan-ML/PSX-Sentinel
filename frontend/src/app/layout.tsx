import type { Metadata } from "next";
import { Inter, Fraunces } from "next/font/google";

import "./globals.css";
import { AuthProvider } from "@/lib/auth/context";

const inter = Inter({
  subsets: ["latin"],
  variable: "--font-inter",
  display: "swap",
});

const fraunces = Fraunces({
  subsets: ["latin"],
  variable: "--font-fraunces",
  display: "swap",
  axes: ["opsz", "SOFT"],
});

export const metadata: Metadata = {
  title: "PSX Sentinel",
  description:
    "AI financial intelligence for the Pakistan Stock Exchange — a 4-agent research pipeline distilled into a single conviction score.",
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <html lang="en" className={`${inter.variable} ${fraunces.variable}`}>
      <body>
        <AuthProvider>{children}</AuthProvider>
      </body>
    </html>
  );
}
