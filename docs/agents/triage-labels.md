# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## How this maps onto local markdown

The tracker here is markdown files, not an issue tracker with labels. A "label" is the value of the `Status:` line near the top of an issue file:

```markdown
Status: needs-triage
```

Nested workflow states, and which role they mean:

| `Status:` value  | Triage role      |
| ---------------- | ---------------- |
| `needs-triage`   | `needs-triage`   |
| `needs-info`     | `needs-info`     |
| `ready-for-agent`| `ready-for-agent`|
| `ready-for-human`| `ready-for-human`|
| `wontfix`        | `wontfix`        |

`/wayfinder` uses the same line with two extra values, which are **not** triage roles and must not be mixed up with them: `claimed` (work in progress) and `resolved` (answer appended under `## Answer`). See [issue-tracker.md](issue-tracker.md).

When a ticket has no `Status:` line at all, treat it as `needs-triage`.
