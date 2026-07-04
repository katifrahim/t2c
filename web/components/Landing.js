import Link from "next/link";
import { ArrowRightIcon, ImageIcon } from "lucide-react";

const EXAMPLES = [
  { title: "Twisted Hexagonal Vase", desc: "LLM: Sonnet 4.6 High", img: "/examples/twisted-hexa-vase.jpeg" },
  { title: "Deriaz Turbine Runner", desc: "LLM: Sonnet 4.6 High", img: "/examples/deriaz-turbine-runner.jpeg" },
  { title: "Full Francis Turbine Assembly", desc: "LLM: Opus 4.8 High", img: "/examples/francis-turbine.jpeg" },
];

// Own scroll container + select-text: the globally-loaded three-cad-viewer.css
// sets `body { overflow: hidden; user-select: none }`, which we override here.
export default function Landing() {
  return (
    <div className="flex h-screen flex-col overflow-y-auto bg-background text-foreground select-text">
      <main className="flex flex-col items-center gap-6 px-6 pt-28 pb-16 text-center">
        <h1 className="max-w-2xl text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
          Turn plain text into 3D CAD models
        </h1>

        <p className="max-w-[24rem] text-base text-pretty text-muted-foreground">
          Describe a part in words and watch it come to life.
          Ready for 3D printing, CNC, laser cutting & more.
          <br />
          <br/>
          No modelling knowledge required.
        </p>

        <Link
          href="/login"
          className="inline-flex h-11 items-center justify-center gap-2 rounded-lg border border-foreground bg-background px-6 text-base font-medium text-foreground transition-colors hover:bg-foreground hover:text-background"
        >
          Get started
          <ArrowRightIcon className="size-4" />
        </Link>

        <p className="font-mono text-xs text-muted-foreground/90">
          You get 2,500 free credits
        </p>
      </main>

      <section className="mx-auto w-full max-w-5xl px-6 pt-8 pb-24">
        <div className="mb-8 flex items-center gap-4">
          <div className="h-px flex-1 bg-border" />
          <h2 className="text-lg font-semibold tracking-tight whitespace-nowrap">
            Built with Text2CAD AI
          </h2>
          <div className="h-px flex-1 bg-border" />
        </div>

        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {EXAMPLES.map((ex) => (
            <div
              key={ex.title}
              className="overflow-hidden border border-border transition-colors hover:border-foreground/30"
            >
              <div className="flex aspect-[4/3] items-center justify-center bg-muted text-muted-foreground">
                {ex.img ? (
                  // eslint-disable-next-line @next/next/no-img-element
                  <img src={ex.img} alt={ex.title} className="h-full w-full object-cover" />
                ) : (
                  <ImageIcon className="size-8 opacity-40" />
                )}
              </div>
              <div className="p-4">
                <div className="text-sm font-medium">{ex.title}</div>
                <div className="mt-1 text-xs text-muted-foreground">{ex.desc}</div>
              </div>
            </div>
          ))}
        </div>
      </section>
    </div>
  );
}
