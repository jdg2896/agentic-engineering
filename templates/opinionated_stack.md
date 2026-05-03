## Opinionated minimal stack

If you want a starter kit and don't want to think about it:

| Layer             | Pick                                                                                              |
| ----------------- | ------------------------------------------------------------------------------------------------- |
| Tracing           | OpenLLMetry → Langfuse (self-hosted) or Phoenix locally                                           |
| Offline evals     | Inspect AI for agents; Promptfoo for prompt regression in CI; Ragas for RAG                       |
| Online evals      | Sample 1–5% of prod traces, run LLM-as-judge nightly, alert on score drift                        |
| Cost / latency    | OTel GenAI conventions + your existing APM; track `cache_read_tokens / total_input_tokens` as SLO |
| Routing           | LiteLLM (self-host) or Portkey / OpenRouter (hosted)                                              |
| Sandboxing        | E2B if hosted, Modal if you already use it, Cloudflare Sandbox if edge-native                     |
| Durable execution | Temporal if you already run it; Inngest / Restate otherwise                                       |
| Memory            | Letta for full agent runtime; Mem0 for drop-in; Graphiti when temporal validity matters           |
| Reading order     | Hamel's field guide → Eugene Yan's patterns → Applied-LLMs.org → "Who Validates the Validators"   |
