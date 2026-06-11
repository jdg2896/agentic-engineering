# Agentic Engineering Workflow — A Curated Resource Guide

_Backend & infrastructure focus. Agent/language-agnostic. Continuously maintained — links verified weekly._

---

## 0. If you only read 7 things

1. [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) — Anthropic (Schluntz/Zhang). The "workflows vs agents" mental model that everything else builds on.
2. [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — Anthropic. The successor concept to prompt engineering; defines the actual job.
3. [12-Factor Agents](https://github.com/humanlayer/12-factor-agents) — Dex Horthy / HumanLayer. Heroku's 12-factor reframed for LLM systems; the canonical "agents are mostly software" doctrine.
4. [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) — Anthropic. The best single multi-agent case study, with concrete failure modes.
5. [Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents) — Cognition. Read alongside #4 — the productive disagreement at the heart of agent architecture in 2025/26.
6. [A practical guide to building agents (PDF)](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) — OpenAI. The complementary canonical from the other lab.
7. [What We Learned From a Year of Building with LLMs](https://applied-llms.org/) — Yan/Bischof/Frye/Husain/Liu/Shankar. Tactical → operational → strategic; the field's distilled playbook.

---

## 1. Foundational design & "what is an agent"

- [Building Effective Agents](https://www.anthropic.com/engineering/building-effective-agents) — Anthropic. Workflows (orchestrator/router/parallel/eval-optimizer) vs true agents.
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) — Anthropic. Compaction, sub-agents, structured note-taking.
- [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents) — Anthropic. Tools as contracts between deterministic and non-deterministic systems; eval-driven tool refinement.
- [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) — Anthropic. Initializer + worker pattern for cross-context continuity (claude-progress.txt + git).
- [How Anthropic teams use Claude Code](https://claude.com/blog/how-anthropic-teams-use-claude-code) — Anthropic. Internal-team patterns across infra, security, data science.
- [Building agents with the Claude Agent SDK](https://claude.com/blog/building-agents-with-the-claude-agent-sdk) — Anthropic.
- [A practical guide to building agents (PDF)](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) — OpenAI. Use cases, design foundations, multi-agent, guardrails.
- [12-Factor Agents](https://github.com/humanlayer/12-factor-agents) — Dex Horthy. Coined "context engineering" in April 2025.
- [12-Factor Agents talk (YouTube)](https://www.youtube.com/watch?v=8kMaTybvDUw) — Dex Horthy. ~30 min version of the README.
- [Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents) — Cognition. Context fragmentation as the core failure mode.
- [Agentic Engineering Patterns](https://simonw.substack.com/p/agentic-engineering-patterns) — Simon Willison. Living catalog of patterns for code-generating-and-executing agents.
- [The lethal trifecta for AI agents](https://simonw.substack.com/p/the-lethal-trifecta-for-ai-agents) — Simon Willison. Private data + untrusted content + exfiltration channel = the agent threat model.
- [Simon Willison's ai-agents tag](https://simonwillison.net/tags/ai-agents/) — Best running curation in the field.
- [Patterns for Building LLM-based Systems & Products](https://eugeneyan.com/writing/llm-patterns/) — Eugene Yan. Evals/RAG/Cache/Guardrails/Defensive-UX.
- [Building A Generative AI Platform](https://huyenchip.com/2024/07/25/genai-platform.html) — Chip Huyen. Reference architecture: gateway → routing → cache → guardrails → telemetry.

## 2. Tool integration & MCP

- [Model Context Protocol — Specification](https://modelcontextprotocol.io/specification/2025-11-25) — Latest spec; JSON-RPC 2.0; tools/resources/prompts primitives.
- [MCP — Architecture overview](https://modelcontextprotocol.io/docs/learn/architecture) — Mental model.
- [MCP — Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) — OAuth flow; required reading before exposing MCP servers.
- [The 2026 MCP Roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/) — Where the protocol is headed.
- [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) — Official reference implementations.
- [wong2/awesome-mcp-servers](https://github.com/wong2/awesome-mcp-servers) and [appcypher/awesome-mcp-servers](https://github.com/appcypher/awesome-mcp-servers) — Two best-maintained registries.
- [Anthropic — Tool use docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/overview) — Schema design, parallel calls, structured outputs.
- [Announcing the Agent2Agent Protocol (A2A)](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/) — Google. Agent-to-agent (vs agent-to-tool) protocol.
- [A2A specification](https://a2a-protocol.org/latest/specification/) — Now under Linux Foundation; gRPC support since v0.3.

## 3. Multi-agent orchestration frameworks

- [LangGraph overview](https://docs.langchain.com/oss/python/langgraph/overview) — Stateful graph runtime; 1.0 shipped Oct 2025.
- [LangGraph — persistence & checkpointing](https://langchain-ai.github.io/langgraph/concepts/persistence/) — Threads, checkpointers, cross-thread memory.
- [LangGraph — human-in-the-loop](https://langchain-ai.github.io/langgraph/concepts/human_in_the_loop/) — `interrupt()`, time-travel, approval gates.
- [LangGraph — multi-agent systems](https://langchain-ai.github.io/langgraph/concepts/multi_agent/) — Supervisor, swarm, hierarchical patterns.
- [OpenAI Agents SDK (Python)](https://openai.github.io/openai-agents-python/) — Successor to Swarm.
- [openai/openai-agents-python](https://github.com/openai/openai-agents-python) — Source.
- [Microsoft Agent Framework](https://learn.microsoft.com/en-us/agent-framework/) — Successor to AutoGen + Semantic Kernel; .NET/Python.
- [AutoGen v0.4](https://microsoft.github.io/autogen/stable/user-guide/core-user-guide/index.html) — Asynchronous actor-model multi-agent runtime.
- [Google ADK (Agent Development Kit) docs](https://adk.dev/) — Open-source, multi-language (Python/TS/Go/Java).
- [google/adk-python](https://github.com/google/adk-python) — Source.
- [CrewAI docs](https://docs.crewai.com/) — Role/task/crew abstraction; lighter than LangGraph.
- [Pydantic AI](https://pydantic.dev/docs/ai/overview/) — Type-safe agents with DI; the pleasant Python option.
- [smolagents](https://huggingface.co/docs/smolagents/index) — Hugging Face. Minimalist code-acting agent library.
- [Mastra](https://mastra.ai/docs) — TypeScript-first.
- [Inngest AgentKit](https://agentkit.inngest.com/) — TS framework on top of Inngest's durable runtime.

## 4. Durable execution for agents

- [Temporal — Build resilient Agentic AI with Temporal](https://temporal.io/blog/build-resilient-agentic-ai-with-temporal) — Why agent loops belong in workflow engines.
- [Temporal — Durable Execution meets AI](https://temporal.io/blog/durable-execution-meets-ai-why-temporal-is-the-perfect-foundation-for-ai) — Tools as activities, signals for HITL, child workflows for sub-agents.
- [temporal-community/temporal-ai-agent](https://github.com/temporal-community/temporal-ai-agent) — Reference implementation.
- [Inngest — Durable Execution: The Key to Harnessing AI Agents in Production](https://www.inngest.com/blog/durable-execution-key-to-harnessing-ai-agents) — Step functions wrapping LLM calls.
- [Restate — AI agents](https://docs.restate.dev/use-cases/ai-agents) — Lightweight durable execution; agents as virtual objects.
- [Hatchet — Durable Tasks](https://docs.hatchet.run/v1/durable-tasks) — Postgres-backed task queue with agent-aware patterns (agentic loops, HITL).
- [DBOS — Durable Execution for Building Crashproof AI Agents](https://www.dbos.dev/blog/durable-execution-crashproof-ai-agents) — Postgres-as-runtime; smaller-team alternative to Temporal.
- [Fault Tolerance in LangGraph: Retries, Timeouts and Error Handlers](https://www.langchain.com/blog/fault-tolerance-in-langgraph) — LangChain. Three fault-tolerance primitives inside LangGraph — RetryPolicy (backoff retries), TimeoutPolicy (wall-clock and idle caps), and error_handler (post-retry cleanup) — plus how to compose them and apply the SAGA pattern for multi-step workflows with real-world side effects.

## 5. Memory systems

- [Letta docs](https://docs.letta.com/) — Production fork of MemGPT; tiered memory (core/archival/recall).
- [MemGPT paper — LLMs as Operating Systems](https://arxiv.org/abs/2310.08560) — The paged-memory paper that started the wave.
- [Mem0 docs](https://docs.mem0.ai/introduction) — Drop-in memory layer with extraction/consolidation.
- [Zep / Graphiti](https://help.getzep.com/overview) — Bi-temporal knowledge graph for memory with fact validity windows.
- [getzep/graphiti](https://github.com/getzep/graphiti) — The temporal-graph engine standalone.
- [LangMem](https://langchain-ai.github.io/langmem/) — Semantic / episodic / procedural primitives over LangGraph stores.
- [Generative Agents (Park et al.)](https://arxiv.org/abs/2304.03442) — Reflection + episodic memory; still the best single read.

## 6. Sandboxing & code execution

- [E2B docs](https://e2b.dev/docs) — Firecracker microVM sandboxes; the de-facto hosted choice.
- [Modal Sandboxes](https://modal.com/docs/guide/sandboxes) — gVisor + filesystem snapshots; good for batch fleets.
- [Daytona docs](https://www.daytona.io/docs) — OSS sandbox repurposed for agents; sub-200ms cold start claims.
- [Cloudflare — Containers for Agents](https://developers.cloudflare.com/containers/) — Per-agent containers tied to Durable Objects.
- [cloudflare/sandbox-sdk](https://github.com/cloudflare/sandbox-sdk) — Reference SDK for spawning sandboxes from Workers.
- [apple/container](https://github.com/apple/container) — Native macOS container runtime; useful for local agent dev.
- [hyperlight-dev/hyperlight](https://github.com/hyperlight-dev/hyperlight) — Microsoft's sub-millisecond WASM/VM micro-sandbox.
- [gVisor docs](https://gvisor.dev/docs/) — User-space kernel; understand it before trusting "sandboxed" claims.
- [Interpreters in Deep Agents: Code Between Tool Calls and Sandboxes](https://www.langchain.com/blog/give-your-agents-an-interpreter) — LangChain. Embedded interpreter runtimes let agents write code to coordinate tool calls, manage working state between steps, and control what gets surfaced into model context — reducing token pressure and enabling finer-grained orchestration than pure tool-dispatch.

## 7. Inference & gateway infrastructure

- [vLLM docs](https://docs.vllm.ai/en/latest/) — Highest-throughput OSS inference; PagedAttention + prefix caching.
- [SGLang](https://docs.sglang.io/) — RadixAttention; great for tool-using agents that share prefixes.
- [Hugging Face TGI](https://huggingface.co/docs/text-generation-inference/index) — Mature self-hosted with constrained decoding.
- [LiteLLM](https://docs.litellm.ai/) — 100+ provider proxy; OpenAI-shaped API; the boring-but-essential routing layer.
- [Portkey AI Gateway](https://portkey.ai/docs/introduction/what-is-portkey) — OSS gateway with guardrails, caching, conditional routing.
- [OpenRouter docs](https://openrouter.ai/docs/quickstart) — Hosted multi-provider routing.
- [Anthropic — Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) — Cache-key design, 5-min TTL, 85% latency reduction reference.
- [OpenAI — Latency optimization](https://developers.openai.com/api/docs/guides/latency-optimization) — TTFT vs total time; streaming patterns.

## 8. Evaluation — philosophy (read these first)

- [Your AI Product Needs Evals](https://hamel.dev/blog/posts/evals/) — Hamel Husain. The canonical "stop vibe-checking, start measuring."
- [A Field Guide to Rapidly Improving AI Products](https://hamel.dev/blog/posts/field-guide/) — Hamel Husain. Error-analysis loops, eval-driven iteration.
- [LLM Evals: Everything You Need to Know](https://hamel.dev/blog/posts/evals-faq/) — Hamel Husain. FAQ from the Hamel/Shreya course.
- [Task-Specific LLM Evals That Do & Don't Work](https://eugeneyan.com/writing/evals/) — Eugene Yan. Why ROUGE/BLEU/BERTScore mislead.
- [LLM-Evaluators a.k.a. LLM-as-Judge](https://eugeneyan.com/writing/llm-evaluators/) — Eugene Yan. Pairwise vs pointwise, position bias, calibration.
- [SPADE (Shankar et al.)](https://arxiv.org/abs/2401.03038) — Auto-synthesized assertions from prompt deltas.
- [Who Validates the Validators? (EvalGen)](https://arxiv.org/abs/2404.12272) — Shankar et al. Critical paper on grader drift.
- [Judging LLM-as-a-Judge (MT-Bench)](https://arxiv.org/abs/2306.05685) — The original position/verbosity/self-preference bias paper.
- [Low-Hanging Fruit for RAG Search](https://jxnl.co/writing/2024/05/11/low-hanging-fruit-for-rag-search/) — Jason Liu. Retrieval-side instrumentation.

## 9. Evaluation — frameworks & benchmarks

- [Inspect AI](https://inspect.aisi.org.uk/) — UK AISI. **OS.** Best-in-class for agent evals; sandboxed tool use, MCP support, used by Anthropic/DeepMind.
- [UKGovernmentBEIS/inspect_ai](https://github.com/UKGovernmentBEIS/inspect_ai) — Source.
- [OpenAI Evals](https://github.com/openai/evals) — **OS.** Original registry-of-evals framework.
- [Promptfoo](https://www.promptfoo.dev/docs/intro/) — **OS + SaaS.** YAML matrix testing + red-team module; CI-friendly.
- [DeepEval](https://github.com/confident-ai/deepeval) — **OS.** Pytest-style assertions + 14 default metrics.
- [Ragas](https://docs.ragas.io/en/stable/) — **OS.** RAG-specific metrics standard.
- [LangSmith Evaluations](https://docs.langchain.com/langsmith/evaluation-concepts) — **SaaS.**
- [Braintrust](https://www.braintrust.dev/docs) — **SaaS.** Hill-climbing dev loop with strong DX.
- [Arize Phoenix](https://arize.com/docs/phoenix) — **OS.** OTel-native traces + evals.
- [Langfuse Evaluations](https://langfuse.com/docs/evaluation/overview) — **OS + SaaS.**
- [Patronus AI](https://docs.patronus.ai:443/docs) — **SaaS.** Managed judge models (Lynx for hallucination).
- **Benchmarks:** [SWE-bench](https://www.swebench.com/), [SWE-bench Verified](https://openai.com/index/introducing-swe-bench-verified/), [GAIA](https://arxiv.org/abs/2311.12983), [τ-bench](https://github.com/sierra-research/tau-bench), [WebArena](https://webarena.dev/), [OSWorld](https://os-world.github.io/), [MLE-bench](https://github.com/openai/mle-bench), [SWE-Lancer](https://arxiv.org/abs/2502.12115).

## 10. Observability & tracing

- [OpenTelemetry GenAI Semantic Conventions](https://opentelemetry.io/docs/specs/semconv/gen-ai/) — **Foundational.** The vendor-neutral schema for LLM/agent spans. Build to this and swap backends.
- [OTel GenAI Metrics Spec](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-metrics/) — Standard metric names (`gen_ai.client.token.usage`, etc.).
- [OpenLLMetry](https://github.com/traceloop/openllmetry) — **OS.** OTel SDK + auto-instrumentation for LLM/vector/agent libs.
- [Langfuse](https://langfuse.com/docs) — **OS + SaaS.** Self-hostable observability + evals + prompt mgmt.
- [Arize Phoenix](https://github.com/Arize-ai/phoenix) — **OS.** OpenInference traces; runs locally.
- [LangSmith Tracing](https://docs.langchain.com/langsmith/observability) — **SaaS.** Framework-agnostic via SDK despite the name.
- [Helicone](https://docs.helicone.ai/getting-started/quick-start) — **OS + SaaS.** Proxy-based logging — lowest-friction integration.
- [Datadog LLM Observability](https://docs.datadoghq.com/llm_observability/) — **SaaS.** Strongest if already on Datadog.

## 11. Production testing patterns & cost/latency

- [Promptfoo in CI (GitHub Action)](https://www.promptfoo.dev/docs/integrations/github-action/) — Block PRs on eval regressions.
- [LangSmith — Online evaluations](https://docs.langchain.com/langsmith/online-evaluations-llm-as-judge) — Sampling prod traces back into eval datasets (the data flywheel).
- [Honeycomb — We shipped AI](https://www.honeycomb.io/blog/we-shipped-ai-product) — Honest postmortem-style writing on shadow traffic + Query Assistant.
- [Langfuse — Cost tracking](https://langfuse.com/docs/observability/features/token-and-cost-tracking) — Per-trace, per-user, per-prompt cost attribution.
- [Helicone — Caching dashboards](https://docs.helicone.ai/features/advanced-usage/caching) — Per-route token spend + cache hit rates.

## 12. Security for agents

- [OWASP Top 10 for LLM Applications 2025](https://genai.owasp.org/llm-top-10/) — Use as a checklist.
- [Embrace The Red](https://embracethered.com/blog/) — Johann Rehberger. The best running blog on real agent exploits.
- [Simon Willison — prompt injection tag](https://simonwillison.net/tags/prompt-injection/) — Ongoing curation of every notable incident.
- [The lethal trifecta](https://simonw.substack.com/p/the-lethal-trifecta-for-ai-agents) — Simon Willison. The threat model in one essay.
- [CaMeL: Defeating Prompt Injections by Design](https://arxiv.org/abs/2503.18813) — Google DeepMind. Capabilities-based dual-LLM design; strongest published defense pattern.
- [Anthropic Responsible Scaling Policy](https://www.anthropic.com/responsible-scaling-policy) — Frontier-lab safety framework; a template for your own deployment gates.
- [NIST AI RMF + Generative AI Profile](https://www.nist.gov/itl/ai-risk-management-framework) — Risk-management vocabulary auditors will use.
- [MITRE ATLAS](https://atlas.mitre.org/) — ATT&CK-style matrix for ML/agent threats.
- [Trail of Bits — Prompt injection to RCE in AI agents](https://blog.trailofbits.com/2025/10/22/prompt-injection-to-rce-in-ai-agents/) — Recent, concrete RCE chain.

## 13. Coding agent infrastructure (read for harness design even if not building one)

- [Best Practices for Claude Code](https://code.claude.com/docs/en/best-practices) — Anthropic. CLAUDE.md, tools, harness design.
- [Claude Agent SDK overview](https://code.claude.com/docs/en/agent-sdk/overview) — Anthropic. Hooks (PreToolUse/PostToolUse/Stop/etc.), tool allowlists, custom tools as in-process MCP.
- [anthropics/claude-agent-sdk-python](https://github.com/anthropics/claude-agent-sdk-python) — Source.
- [Cognition — How Cognition uses Devin to build Devin](https://cognition.ai/blog/how-cognition-uses-devin-to-build-devin) — Internal dogfooding patterns.
- [Cognition — Multi-Agents: What's Actually Working](https://cognition.ai/blog/multi-agents-working) — The pragmatic update to "Don't Build Multi-Agents."
- [Aider blog](https://aider.chat/docs/) — Repo-map, edit formats; the leaderboard is one of the best practical evals.
- [Sourcegraph Amp — engineering posts](https://ampcode.com/chronicle) — Long-form on tool design and oracle patterns.
- [openai/codex](https://github.com/openai/codex) — Reference open-source coding-agent CLI.
- [Geoffrey Huntley — how to build a coding agent (workshop)](https://ghuntley.com/agent/) — Free workshop on building one from scratch.
- [Geoffrey Huntley — Ralph Wiggum loop](https://ghuntley.com/ralph/) — The brute-force feedback-loop pattern essay.
- [Open SWE: An Open-Source Framework for Internal Coding Agents](https://www.langchain.com/blog/open-swe-an-open-source-framework-for-internal-coding-agents) — LangChain. Open-source SWE-agent framework built on LangGraph; covers core architectural components — task manager, programmer agent, and sandboxed execution — for deploying internal coding agents at scale.

## 14. Backend-specific agent patterns (SRE, K8s, IaC)

- [k8sgpt](https://docs.k8sgpt.ai/) — CNCF Sandbox. Read-only K8s diagnosis agent; canonical example.
- [Datadog — Bits AI SRE](https://www.datadoghq.com/blog/bits-ai-sre/) — Datadog's autonomous incident-response agent design.
- [HashiCorp — Terraform MCP server](https://github.com/hashicorp/terraform-mcp-server) — Reference IaC tool surface.
- [Anthropic — How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) — Best multi-agent case study, period.

---

## Worth following for ongoing signal

- [Anthropic Engineering](https://www.anthropic.com/engineering) — Engineering posts from the model-maker — agents, tooling, and operational patterns.
- [Cognition blog](https://cognition.ai/blog) — Case studies on building Devin; the productive disagreement to "Don't Build Multi-Agents."
- [Simon Willison](https://simonwillison.net/) — The field's running curator — daily-ish takes on LLMs, agents, and prompt injection.
- [Embrace The Red](https://embracethered.com/blog/) — Johann Rehberger's red-team blog — concrete agent exploits and bypass techniques.
- [LangChain blog](https://www.langchain.com/blog) — Framework updates, multi-agent patterns, and ecosystem news.
- [Hamel's blog](https://hamel.dev/) — Applied LLM engineering and evals from a practitioner perspective.
