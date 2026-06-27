import Link from "next/link";
import { Brand } from "@/components/brand";

export default function AuthLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    <main className="relative min-h-screen">
      <header className="absolute left-0 right-0 top-0 px-6 py-5 sm:px-10">
        <Brand size="md" asLink={false} />
      </header>

      <div className="grid min-h-screen lg:grid-cols-2">
        {/* Left column: the form */}
        <section className="flex items-center justify-center px-6 py-24 sm:px-10">
          <div className="w-full max-w-sm animate-fade-in">{children}</div>
        </section>

        {/* Right column: a visual editorial pane.
            Hidden on small viewports so the form gets the full screen. */}
        <aside className="relative hidden overflow-hidden lg:block">
          <div className="absolute inset-0 bg-gradient-to-br from-primary via-primary to-[hsl(192_55%_30%)]" />
          <div className="absolute inset-0 opacity-20" style={{
            backgroundImage: "radial-gradient(circle at 20% 20%, hsl(14 65% 56% / 0.5), transparent 40%), radial-gradient(circle at 80% 70%, hsl(192 80% 60% / 0.4), transparent 50%)",
          }} />
          <div className="relative flex h-full flex-col justify-between p-12 text-primary-foreground">
            <div>
              <p className="text-xs uppercase tracking-[0.18em] text-primary-foreground/70">
                Pakistan Stock Exchange
              </p>
              <h2 className="mt-4 font-display text-display-2 leading-tight">
                One conviction score.<br />
                <span className="italic text-accent">Four agents.</span>{" "}
                Zero noise.
              </h2>
              <p className="mt-6 max-w-md text-sm leading-relaxed text-primary-foreground/80">
                A trend analyzer, a news synthesizer, a filing skeptic and
                an arbitrator each weigh in — then the system distills
                their disagreement into one number you can act on.
              </p>
            </div>

            <div className="space-y-4 text-sm text-primary-foreground/70">
              <p className="font-display italic">
                "Designed to read as production, not as a student project."
              </p>
              <p>
                <Link
                  href="https://github.com/AbdullahRizwan-ML/psx-sentinel"
                  className="underline-offset-4 hover:underline"
                >
                  View on GitHub →
                </Link>
              </p>
            </div>
          </div>
        </aside>
      </div>
    </main>
  );
}
