# auto-aigc-tasks

## Agent skills

### Issue tracker

Issues and specs live as local markdown files under `.scratch/<feature-slug>/`. See `docs/agents/issue-tracker.md`.

### Triage labels

The five canonical triage roles, label string equal to role name. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context. See `docs/agents/domain.md`.

## Plugin layout

This repo is a Claude Code plugin (`.claude-plugin/plugin.json` at the root). Keep
plugin components at the root level (`skills/`, `.claude-plugin/`); this file is
repo-development context only and deliberately lives in `.claude/` so it is not
mistaken for shippable plugin context.
