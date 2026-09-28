# Agentic Engineering — Curated Resource Guide

A self-maintaining, opinionated guide to building, evaluating, operating, and securing
**agentic systems**. Automated jobs discover, vet, and link-check entries; humans review
only the editorially-significant cases.

## Scope

The guide's **topic** is fixed: agentic systems. It is *not* a general "AI engineering" or
"big tech engineering" list.

- **Discipline-agnostic** — the agentic content may serve any engineering discipline
  (frontend, backend, infra, QA, data). Broadened in #40.
- **Source-agnostic** — a **Source** may be an individual author's blog, a framework
  vendor's blog, a GitHub release feed, *or* a big-tech company engineering blog. Company
  blogs are admitted because they publish agentic content too — **not** because the topic
  widened to general engineering.
- The single inclusion test is unchanged: _does it carry a reproducible technique or
  architecture decision for building/operating an agentic system?_ Capability
  announcements and vibes essays are rejected regardless of source.

## Language

**Source**:
A feed (RSS / Atom / GitHub-releases) in `sources.yaml` that **Scout** polls for new entries.
_Avoid_: feed (when precision matters), site.

**Resource**:
A curated entry that appears in the published guide (`resources.yaml` → `README.md`).
_Avoid_: link, item, post.

**Candidate**:
A scouted entry the editorial judge marked `include`, pending merge into the guide.
_Avoid_: suggestion, pick.

**Scout**:
The weekly job that discovers **Candidates** by polling known **Sources**.

**Verify**:
The weekly job that link-checks every **Resource** and quarantines dead ones.

**Source discovery**:
A periodic job (planned) that discovers new **Sources** to add — distinct from **Scout**,
which discovers **Resources** from *existing* Sources.

## Relationships

- A **Source** yields zero or more **Candidates** per Scout run.
- A **Candidate** becomes a **Resource** when its Scout PR merges.
- **Verify** acts only on existing **Resources**; **Scout** only adds new ones.
- **Source discovery** feeds **Scout** by growing the **Source** list.

## Flagged ambiguities

- "scope" was used to mean both _topic breadth_ (agentic vs. general AI) and _discipline
  breadth_ (BE-only vs. all disciplines) — resolved: the topic is fixed on agentic systems;
  only discipline and source breadth widen.
- "research / deep research step" and "scout" both sound like discovery — resolved:
  **Scout** finds Resources from known Sources; **Source discovery** finds new Sources.
