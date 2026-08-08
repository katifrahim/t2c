// Singleton Langfuse client for the REST path (separate from the OTel processor
// in instrumentation.js, which handles trace spans). Used after a turn finishes
// to: attach numeric scores (real cost, credits) to the trace, and record the
// agent's self-improvement reports (categorical "learning" scores + items in the
// "agent-learnings" dataset). null when tracing is off.
import { LangfuseClient } from "@langfuse/client";

export const langfuse = process.env.LANGFUSE_PUBLIC_KEY ? new LangfuseClient() : null;
