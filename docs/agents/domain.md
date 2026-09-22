# Domain Docs

How the engineering skills should consume this repo's domain documentation when exploring the codebase.

## Before exploring, read these

- **`CONTEXT.md`** at the repo root, or
- **`CONTEXT-MAP.md`** at the repo root if it exists: it points at one `CONTEXT.md` per context. Read each one relevant to the topic.
- **`docs/adr/`**: read ADRs that touch the area you're about to work in.

If any of these files don't exist, **proceed silently**. Don't flag their absence; don't suggest creating them upfront. The `/domain-modeling` skill (reached via `/grill-with-docs` and `/improve-codebase-architecture`) creates them lazily when terms or decisions actually get resolved.

## File structure

Single-context repo, packaged as a Claude Code plugin:

```
/
├── .claude-plugin/plugin.json
├── CONTEXT.md
├── docs/adr/
│   ├── 0001-....md
│   └── 0002-....md
├── lib/unipus_aigc/          # the Python client (plugin payload)
└── skills/<name>/SKILL.md    # one skill per app + `guide`
```

## Use the glossary's vocabulary

When your output names a domain concept (in an issue title, a refactor proposal, a hypothesis, a test name), use the term as defined in `CONTEXT.md`. Don't drift to synonyms the glossary explicitly avoids.

If the concept you need isn't in the glossary yet, that's a signal: either you're inventing language the project doesn't use (reconsider) or there's a real gap (note it for `/domain-modeling`).

## Flag ADR conflicts

If your output contradicts an existing ADR, surface it explicitly rather than silently overriding:

> _Contradicts ADR-0007 (event-sourced orders), but worth reopening because…_

## Repo-specific notes

- `README.md` carries the domain's **pitfalls** (the 「几个容易踩的坑」 list — eleven items as of 2026-09-20, and items 4 and 5 were *corrected* that day against the platform, so treat the README as the current word and the internal `call-chains.md` as the evidence), and the internal `call-chains.md` carries the **wire-level vocabulary** (endpoint names, operation codes, and the field names that behave surprisingly: `submitData`, `responseData`, `evaluationStatus`, `ticket`). Those two are the de-facto glossary today. `CONTEXT.md` is the place to lift a term out of them once it has actually been resolved — don't duplicate them wholesale.
- This client is a **reverse-engineered** surface over a black-box platform. Two vocabularies coexist and must not be conflated: the **platform's** names (whatever the wire uses) and **ours** (what the Python package exposes). When a term is ambiguous, say which side it belongs to.
- The platform itself is **not** single-vocabulary either, and this has bitten us: there are two live language-code tables (the 202 three-letter codes from `translate/lang/list`, and the two-letter codes the document-translation path demands because it proxies to Baidu), and `status` means different things per application. When a term looks universal, verify it against the specific endpoint rather than assuming it carries over.
