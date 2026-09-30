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
- **English-language only** — the guide includes only Resources written in English; a
  non-English entry is rejected on that ground alone, whatever its content.

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

**Source review**:
The monthly job that curates the **Source** list itself. It has two halves:
**Source discovery** and **Source retirement**. It is the only job that changes which
Sources exist or are enabled.

**Source discovery**:
The half of **Source review** that finds new **Sources** to add — distinct from **Scout**,
which discovers **Resources** from *existing* Sources.

**Prospective Source**:
A feed that **Source discovery** is considering adding, not yet on the Source list.
_Avoid_: candidate source (a **Candidate** is a scouted entry, not a feed).

**Citation mining**:
A **Source discovery** channel: finding **Prospective Sources** among the sites that
accepted **Resources** repeatedly link to. A site becomes a Prospective Source when
Resources on at least two different sites cite it; several Resources on one site are
one voice.

**Source suggestion**:
A **Source discovery** channel: a human proposes a **Prospective Source** by filing it on
the issue tracker. It is validated exactly like a mined one — a suggestion is not an
override.

**Trial**:
How a **Prospective Source** is validated: a sample of its recent entries is put through
the same editorial judge **Scout** uses. It is added only if the Trial shows it is alive,
would have yielded at least one **Resource** — the same bar that keeps a Source from
being **Unproductive** — and has **Topic fit**.

**Topic fit**:
Whether a **Prospective Source**, as a whole, is about agentic systems — judged once per
**Trial**, over the Trial's sample. A feed can yield an occasional **Resource** and still
lack Topic fit (e.g. a general ML-serving release feed); one that lacks it is rejected as
off-topic.
_Avoid_: relevance, source verdict.

**Source retirement**:
The half of **Source review** that turns **Dead** or **Unproductive Sources** into
**Retired Sources**.

**Source health**:
The facts **Scout** records about each **Source** on every run — whether its feed could be
fetched, and when it last published. Scout records health but never acts on it; **Source
review** decides.

**Dead Source**:
A **Source** whose feed no longer works, in one of two forms: **broken** (the feed
persistently fails to fetch or parse) or **silent** (it fetches, but has published nothing
for a long stretch). A broken feed that returns no entries is not "quiet" — it is broken.
It may be repairable (e.g. the feed moved), so it is distinct from an **Unproductive
Source**.
_Avoid_: broken feed, stale source.

**Unproductive Source**:
A **Source** that still publishes, but whose entries the judge persistently rejects — it
yields no **Resources** despite enough of its entries having been judged. A single
accepted entry in the window keeps it productive. A Source cannot be judged Unproductive
until it has a full window of attributed history — a new Source is in its **grace period**
until then.
_Avoid_: noisy feed, bad source.

**Retired Source**:
A **Source** taken out of Scout's polling because it was **Dead** or **Unproductive**. It
stays on the Source list with the reason and date, so it is remembered rather than
forgotten — and can be revived (e.g. a Dead Source whose feed moved).
_Avoid_: deleted source, removed source.

**Yield**:
The **Resources** a **Source** has produced — the measure of whether it is
**Unproductive** (within a recent window) and whether it is **Worth following** (over its
lifetime). Resources added by **Scout** record their Source; earlier Resources count
toward a Source only when their link belongs to exactly that one Source's site, and are
otherwise unattributed.

**Worth following**:
The guide's reader-facing list of **Sources** that give ongoing signal: every Source that
is not **Retired** and has a lifetime **Yield** of at least three **Resources**. It is
derived, never curated — a Source joins the moment it reaches that Yield and leaves the
moment it is Retired; a site that is not a Source (e.g. one with no feed) is never on it.
_Avoid_: recommended feeds, follow list.

## Relationships

- A **Source** yields zero or more **Candidates** per Scout run.
- A **Candidate** becomes a **Resource** when its Scout PR merges.
- **Verify** acts only on existing **Resources**; **Scout** only adds new ones.
- A **Resource** added by **Scout** is attributed to the **Source** it came from; this
  attribution is what makes **Yield** measurable.
- A **Source** can be **Dead**, **Unproductive**, both, or neither — the two are judged
  independently.
- Retiring a **Source** never removes the **Resources** it already yielded.
- **Source discovery** never re-proposes a **Retired Source**.
- **Source review** changes only the Source list; it never adds **Resources**. A Source
  added by it contributes Resources through **Scout**'s next run.
- **Source retirement** judges **Dead** from **Source health** and **Unproductive** from
  **Yield**.
- **Source discovery** feeds **Scout** by growing the **Source** list.
- **Worth following** is derived from **Yield** and **Source retirement** alone: retiring a
  Source removes it from the list, and nothing else does.
- **Topic fit** is judged only in a **Trial**; **Source retirement** never retires a Source
  for lacking it.

## Flagged ambiguities

- "scope" was used to mean both _topic breadth_ (agentic vs. general AI) and _discipline
  breadth_ (BE-only vs. all disciplines) — resolved: the topic is fixed on agentic systems;
  only discipline and source breadth widen.
- "research / deep research step" and "scout" both sound like discovery — resolved:
  **Scout** finds Resources from known Sources; **Source discovery** finds new Sources.
- "candidate" was about to mean both a scouted entry and a feed under consideration —
  resolved: **Candidate** stays the entry; a feed under consideration is a **Prospective
  Source**.
