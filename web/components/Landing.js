"use client";
import Link from "next/link";
import { ArrowRightIcon, ImageIcon } from "lucide-react";
import { track, EVENTS } from "@/lib/analytics";
import { useVariant } from "@/lib/flags";

// Full-page blueprint grid — no fade.
const BLUEPRINT = {
  backgroundImage:
    "linear-gradient(to right, color-mix(in oklch, var(--foreground) 7%, transparent) 1px, transparent 1px)," +
    "linear-gradient(to bottom, color-mix(in oklch, var(--foreground) 7%, transparent) 1px, transparent 1px)",
  backgroundSize: "34px 34px",
};

const EXAMPLES = [
  { title: "Deriaz turbine runner", desc: "LLM: DeepSeek V4 Flash", img: "/examples/deriaz-turbine-runner.jpeg" },
  { title: "Francis turbine assembly", desc: "LLM: GLM 5.2", img: "/examples/francis-turbine.png" },
  { title: "Chain drive", desc: "LLM: DeepSeek V4 Flash", img: "/examples/chain-drive.png" },
  { title: "Ball bearing", desc: "LLM: DeepSeek V4 Flash", img: "/examples/bearings.png" },
  { title: "Planetary helical gear", desc: "LLM: DeepSeek V4 Flash", img: "/examples/planetary-gear.png" },
  { title: "M3 bolt, nut & heatsert", desc: "LLM: DeepSeek V4 Flash", img: "/examples/fastner.png" },
  { title: "Workbench", desc: "LLM: Xiaomi Mimo V2.5", img: "/examples/workbench.jpeg" },
  { title: "Flower vase", desc: "LLM: GPT OSS", img: "/examples/twisted-hexa-vase.jpeg" },
  { title: "Roller coaster toy", desc: "LLM: GLM 5.2", img: "/examples/roller-coaster.png" },
  { title: "Texture totem", desc: "LLM: DeepSeek V4 Flash", img: "/examples/texture-totem.png" },
  { title: "Materials", desc: "LLM: DeepSeek V4 Flash", img: "/examples/materials.png" },
  { title: "Textures", desc: "LLM: DeepSeek V4 Flash", img: "/examples/textures.png" },
];

// Own scroll container + select-text: the globally-loaded three-cad-viewer.css
// sets `body { overflow: hidden; user-select: none }`, which we override here.
export default function Landing() {
  // Sample A/B experiment: create a "landing-headline" experiment in PostHog with a
  // "test" variant to try alternate copy; goal metric = landing:get_started_click.
  // Defaults to the control headline until/unless the experiment is running.
  const headlineVariant = useVariant("landing-headline");
  const headline =
    headlineVariant === "test"
      ? "Turn plain text into 3D CAD models !!!" // test
      : "Turn plain text into 3D CAD models"; // control

  return (
    <div className="relative flex h-screen flex-col overflow-y-auto bg-background text-foreground select-text">
      {/* Blueprint field framing the edges, fading out behind the text */}
      <div aria-hidden className="pointer-events-none fixed inset-0 z-0" style={BLUEPRINT} />

      <main className="relative z-10 flex flex-col items-center gap-6 px-6 pt-28 pb-16 text-center">
        <h1 className="max-w-2xl text-4xl font-semibold tracking-tight text-balance sm:text-5xl">
          {headline}
        </h1>

        <div className="flex max-w-[24rem] flex-col gap-4 text-base text-pretty text-foreground/65">
          <p>
            Describe a part in words and watch it come to life.
            Ready for 3D printing, CNC, laser cutting & more.
          </p>
          <p>No modelling knowledge required.</p>
          <p>100% Free!</p>
        </div>

        <Link
          href="/login"
          onClick={() => track(EVENTS.GET_STARTED_CLICK)}
          className="inline-flex h-11 cursor-pointer items-center justify-center gap-2 rounded-lg bg-primary px-6 text-base font-medium text-primary-foreground outline-none transition-all hover:bg-primary/80 focus-visible:border-ring focus-visible:ring-3 focus-visible:ring-ring/50 active:translate-y-px"
        >
          Get started
          <ArrowRightIcon className="size-4" />
        </Link>
      </main>

      <section className="relative z-10 mx-auto w-full max-w-5xl px-6 pt-8 pb-24">
        <div className="mb-8 flex items-center gap-4">
          <div className="h-px flex-1 bg-foreground/20" />
          <h2 className="text-lg font-semibold tracking-tight whitespace-nowrap">
            Built by our AI
          </h2>
          <div className="h-px flex-1 bg-foreground/20" />
        </div>

        <div className="grid grid-cols-1 gap-5 sm:grid-cols-2 lg:grid-cols-3">
          {EXAMPLES.map((ex) => (
            <div
              key={ex.title}
              className="overflow-hidden border border-foreground/15 bg-card transition-colors hover:border-foreground/30"
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
