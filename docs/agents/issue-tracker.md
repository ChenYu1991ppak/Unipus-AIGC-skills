# Issue tracker: Local Markdown

Issues and specs for this repo live as markdown files in
`.internal-docs/scratch/` — **internal, not committed**.

## Conventions

- One feature per directory: `.internal-docs/scratch/<feature-slug>/`
- The spec is `.internal-docs/scratch/<feature-slug>/spec.md`
- Implementation issues are one file per ticket at `.internal-docs/scratch/<feature-slug>/issues/<NN>-<slug>.md`, numbered from `01`, never a single combined tickets file
- Triage state is recorded as a `Status:` line near the top of each issue file (see `triage-labels.md` for the role strings)
- Comments and conversation history append to the bottom of the file under a `## Comments` heading

## When a skill says "publish to the issue tracker"

Create a new file under `.internal-docs/scratch/<feature-slug>/` (creating the directory if needed).

## When a skill says "fetch the relevant ticket"

Read the file at the referenced path. The user will normally pass the path or the issue number directly.

## Wayfinding operations

Used by `/wayfinder`. The **map** is a file with one **child** file per ticket.

- **Map**: `.internal-docs/scratch/<effort>/map.md` (the Notes / Decisions-so-far / Fog body).
- **Child ticket**: `.internal-docs/scratch/<effort>/issues/NN-<slug>.md`, numbered from `01`, with the question in the body. A `Type:` line records the ticket type (`research`/`prototype`/`grilling`/`task`); a `Status:` line records `claimed`/`resolved`.
- **Blocking**: a `Blocked by: NN, NN` line near the top. A ticket is unblocked when every file it lists is `resolved`.
- **Frontier**: scan `.internal-docs/scratch/<effort>/issues/` for files that are open, unblocked, and unclaimed; first by number wins.
- **Claim**: set `Status: claimed` and save before any work.
- **Resolve**: append the answer under an `## Answer` heading, set `Status: resolved`, then append a context pointer (gist + link) to the map's Decisions-so-far in `map.md`.

## Repo notes

- Everything under `.internal-docs/` is **internal material and is not committed**:
  it records reverse-engineered interface details, internal host names and page IDs.
  Write tickets here following the conventions above — they just never leave the machine.
- The reverse-engineering reference (`call-chains.md`, `app-catalog.md`) lives in
  `.internal-docs/`. Link to it from tickets rather than restating its contents.
