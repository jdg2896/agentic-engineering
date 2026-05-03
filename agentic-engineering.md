# Agentic Engineering Workflow — A Curated Resource Guide

_Backend & infrastructure focus. Agent/language-agnostic. Compiled May 2026._

URLs marked ✓ were live-verified during compilation; the rest are canonical paths that may have moved — spot-check before bookmarking.

---

## 0. If you only read 7 things

1. [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) ✓ — Anthropic (Schluntz/Zhang). The "workflows vs agents" mental model that everything else builds on.
2. [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) ✓ — Anthropic. The successor concept to prompt engineering; defines the actual job.
3. [12-Factor Agents](https://github.com/humanlayer/12-factor-agents) ✓ — Dex Horthy / HumanLayer. Heroku's 12-factor reframed for LLM systems; the canonical "agents are mostly software" doctrine.
4. [How we built our multi-agent research system](https://www.anthropic.com/engineering/multi-agent-research-system) ✓ — Anthropic. The best single multi-agent case study, with concrete failure modes.
5. [Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents) ✓ — Cognition. Read alongside #4 — the productive disagreement at the heart of agent architecture in 2025/26.
6. [A practical guide to building agents (PDF)](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) ✓ — OpenAI. The complementary canonical from the other lab.
7. [What We Learned From a Year of Building with LLMs](https://applied-llms.org/) ✓ — Yan/Bischof/Frye/Husain/Liu/Shankar. Tactical → operational → strategic; the field's distilled playbook.

---

## 1. Foundational design & "what is an agent"

- [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) ✓ — Anthropic. Workflows (orchestrator/router/parallel/eval-optimizer) vs true agents.
- [Effective context engineering for AI agents](https://www.anthropic.com/engineering/effective-context-engineering-for-ai-agents) ✓ — Anthropic. Compaction, sub-agents, structured note-taking.
- [Writing effective tools for AI agents](https://www.anthropic.com/engineering/writing-tools-for-agents) ✓ — Anthropic. Tools as contracts between deterministic and non-deterministic systems; eval-driven tool refinement.
- [Effective harnesses for long-running agents](https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents) ✓ — Anthropic. Initializer + worker pattern for cross-context continuity (claude-progress.txt + git).
- [How Anthropic teams use Claude Code](https://www.anthropic.com/news/how-anthropic-teams-use-claude-code) — Anthropic. Internal-team patterns across infra, security, data science.
- [Building agents with the Claude Agent SDK](https://www.anthropic.com/engineering/building-agents-with-the-claude-agent-sdk) ✓ — Anthropic.
- [A practical guide to building agents (PDF)](https://cdn.openai.com/business-guides-and-resources/a-practical-guide-to-building-agents.pdf) ✓ — OpenAI. Use cases, design foundations, multi-agent, guardrails.
- [12-Factor Agents](https://github.com/humanlayer/12-factor-agents) ✓ — Dex Horthy. Coined "context engineering" in April 2025.
- [12-Factor Agents talk (YouTube)](https://www.youtube.com/watch?v=8kMaTybvDUw) ✓ — Dex Horthy. ~30 min version of the README.
- [Don't Build Multi-Agents](https://cognition.ai/blog/dont-build-multi-agents) ✓ — Cognition. Context fragmentation as the core failure mode.
- [Agentic Engineering Patterns](https://simonw.substack.com/p/agentic-engineering-patterns) ✓ — Simon Willison. Living catalog of patterns for code-generating-and-executing agents.
- [The lethal trifecta for AI agents](https://simonw.substack.com/p/the-lethal-trifecta-for-ai-agents) ✓ — Simon Willison. Private data + untrusted content + exfiltration channel = the agent threat model.
- [Simon Willison's ai-agents tag](https://simonwillison.net/tags/ai-agents/) ✓ — Best running curation in the field.
- [Patterns for Building LLM-based Systems & Products](https://eugeneyan.com/writing/llm-patterns/) — Eugene Yan. Evals/RAG/Cache/Guardrails/Defensive-UX.
- [Building A Generative AI Platform](https://huyenchip.com/2024/07/25/genai-platform.html) ✓ — Chip Huyen. Reference architecture: gateway → routing → cache → guardrails → telemetry.

## 2. Tool integration & MCP

- [Model Context Protocol — Specification](https://modelcontextprotocol.io/specification/2025-11-25) ✓ — Latest spec; JSON-RPC 2.0; tools/resources/prompts primitives.
- [MCP — Architecture overview](https://modelcontextprotocol.io/docs/learn/architecture) ✓ — Mental model.
- [MCP — Authorization](https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization) ✓ — OAuth flow; required reading before exposing MCP servers.
- [The 2026 MCP Roadmap](https://blog.modelcontextprotocol.io/posts/2026-mcp-roadmap/) ✓ — Where the protocol is headed.
- [modelcontextprotocol/servers](https://github.com/modelcontextprotocol/servers) ✓ — Official reference implementations.
- [wong2/awesome-mcp-servers](https://github.com/wong2/awesome-mcp-servers) ✓ and [appcypher/awesome-mcp-servers](https://github.com/appcypher/awesome-mcp-servers) ✓ — Two best-maintained registries.
- [Anthropic — Tool use docs](https://platform.claude.com/docs/en/build-with-claude/tool-use) — Schema design, parallel calls, structured outputs.
- [Announcing the Agent2Agent Protocol (A2A)](https://developers.googleblog.com/en/a2a-a-new-era-of-agent-interoperability/) ✓ — Google. Agent-to-agent (vs agent-to-tool) protocol.
- [A2A specification](https://a2a-protocol.org/latest/specification/) ✓ — Now under Linux Foundation; gRPC support since v0.3.

---

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

---

## Caveats

- ~30 of the ~120 URLs above were live-verified at compile time; the rest are canonical paths and may have moved.
- Anthropic recently migrated docs from `docs.anthropic.com` → `platform.claude.com` / `code.claude.com`; if any Anthropic link 404s, try the new host.
- Skipped intentionally: vendor landing pages, Medium/SEO listicles, anything frontend-only, papers without practical infra implications.
- **Worth following for ongoing signal:** Anthropic Engineering, Cognition blog, Simon Willison, Embrace The Red, Cloudflare Agents tag, LangChain blog, Hamel's blog.
