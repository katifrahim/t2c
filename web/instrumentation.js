// OpenTelemetry wiring for Langfuse LLM tracing. Next.js runs register() once on
// server boot. The AI SDK's experimental_telemetry spans (and our own Langfuse
// spans) flow through this processor to Langfuse. Disabled when no keys are set,
// so local dev works without a Langfuse account.
import { LangfuseSpanProcessor } from "@langfuse/otel";
import { NodeTracerProvider } from "@opentelemetry/sdk-trace-node";

// Exported so the chat route can forceFlush() before the serverless function
// exits — otherwise buffered spans are dropped. null when tracing is off.
export const langfuseSpanProcessor = process.env.LANGFUSE_PUBLIC_KEY
  ? new LangfuseSpanProcessor({
      // Separates dev vs prod in the Langfuse UI's environment filter.
      environment: process.env.VERCEL_ENV || process.env.NODE_ENV || "development",
    })
  : null;

export function register() {
  // Only the Node.js runtime handles LLM calls; skip the edge runtime.
  if (process.env.NEXT_RUNTIME !== "nodejs" || !langfuseSpanProcessor) return;
  new NodeTracerProvider({ spanProcessors: [langfuseSpanProcessor] }).register();
}
