// Singleton Langfuse client, used only to attach numeric scores (real cost,
// credits) to a trace by id after a turn finishes. Trace spans themselves go
// through the OTel processor in instrumentation.js — this is a separate REST
// path. null when tracing is off.
import { LangfuseClient } from "@langfuse/client";

export const langfuse = process.env.LANGFUSE_PUBLIC_KEY ? new LangfuseClient() : null;
