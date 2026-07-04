// OpenTelemetry wiring for Langfuse LLM tracing. Next.js runs register() once on
// server boot. The AI SDK's experimental_telemetry spans (and our own Langfuse
// spans) flow through this processor to Langfuse. Disabled when no keys are set,
// so local dev works without a Langfuse account.
import { LangfuseSpanProcessor } from "@langfuse/otel";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";
import { setLangfuseTracerProvider } from "@langfuse/tracing";

// Exported so the chat route can forceFlush() before the serverless function
// exits — otherwise buffered spans are dropped. null when tracing is off.
export const langfuseSpanProcessor = process.env.LANGFUSE_PUBLIC_KEY
  ? new LangfuseSpanProcessor({
      // Separates dev vs prod in the Langfuse UI's environment filter.
      environment: process.env.VERCEL_ENV || process.env.NODE_ENV || "development",
      // Serverless: export each span as it ends instead of batching. The default
      // BatchSpanProcessor is a warm-instance singleton whose buffer only flushed on
      // the NEXT invocation, so the globally-latest turn was always left unsent.
      exportMode: "immediate",
    })
  : null;

export function register() {
  // Only the Node.js runtime handles LLM calls; skip the edge runtime.
  if (process.env.NEXT_RUNTIME !== "nodejs" || !langfuseSpanProcessor) return;
  const tracerProvider = new NodeTracerProvider({ spanProcessors: [langfuseSpanProcessor] });
  // register(): make this the global provider so the AI SDK's `ai`-scope spans
  // (generations/tool calls) flow to our processor.
  tracerProvider.register();
  // setLangfuseTracerProvider(): bind @langfuse/tracing to THIS provider explicitly.
  // Otherwise startActiveObservation("chat-turn") resolves its tracer via the global
  // @opentelemetry/api, which in Next's bundled prod build points at a provider WITHOUT
  // our span processor — so the root chat-turn span is created but never exported (its
  // AI-SDK children still nest under it via shared context, hence loose generations and
  // an empty Sessions tab in prod only). Dev works by luck: one shared OTel instance.
  setLangfuseTracerProvider(tracerProvider);
}
