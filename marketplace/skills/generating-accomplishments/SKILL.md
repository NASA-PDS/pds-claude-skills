---
name: generating-accomplishments
description: Generate a PDS accomplishment status report from activity.json or by running the lasso-issues pds-activity CLI. Groups accomplishments by PDS product team. Use when creating sprint reports, quarterly reports, annual reports, status updates, or accomplishment summaries. Triggers on "accomplishments", "status report", "sprint report", "quarterly report", "what did we accomplish", "activity report".
---

# PDS Accomplishment Status Report Generator

Generates a canonical PDS EN accomplishment report from a `activity.json` file, grouped by PDS product team. Can optionally run the `pds-activity` CLI (from the `lasso-issues` tool) to collect the data first.

## Inputs

- **activity.json path** (optional): Path to an existing `activity.json` file. If not provided, the skill will offer to run `pds-activity` to generate one.
- **Clarifying answers** (gathered interactively before generating): reporting period label, audience, priority themes, scope, format.

## Prerequisites

- **If running pds-activity to collect data**: Python 3.10+, `lasso-issues` installed (see below), `GITHUB_TOKEN` env var set
- **GitHub token**: `export GITHUB_TOKEN=<your-token>` (needs `repo` read scope for NASA-PDS org)

---

## Step 0 — Check pds-products.yaml for upstream changes

Before doing anything else, check whether `pds-products.yaml` (in this skill's directory) matches the upstream canonical version.

**Locate the skill directory:** `pds-products.yaml` lives alongside this `SKILL.md` — typically at `~/.claude/plugins/cache/pds-agent-skills/creating-pds-issues/<hash>/skills/generating-accomplishments/pds-products.yaml`.

**Fetch the upstream and diff:**
```bash
curl -fsSL https://raw.githubusercontent.com/NASA-PDS/.github/main/conf/pds-products.yaml > /tmp/pds-products-upstream.yaml
diff /tmp/pds-products-upstream.yaml <path-to-local-pds-products.yaml>
```

**If the diff shows changes to `products:` entries** (new repos, new products, changed `ignore:` or `work_stream:` or `core_backbone:` values):
1. Apply those structural changes to the local copy — preserve our local comment additions (the reporting-exclusion notes at the top).
2. Do not copy upstream `version:` or `updated:` fields — those are intentionally omitted from our copy.
3. Confirm: "Updated `pds-products.yaml` — applied N changes from upstream."

**If the fetch fails** (network error, non-200): warn and continue with the local copy.
   > ⚠️ Could not check `pds-products.yaml` for updates (fetch failed). Using local copy. Check manually: https://github.com/NASA-PDS/.github/blob/main/conf/pds-products.yaml

**If diff shows only comment/whitespace differences:** proceed silently — no update needed.

---

## Step 1 — Determine Input Source

Check whether an `activity.json` was provided by the user (as a file path argument or mentioned in conversation).

**If provided:** load it directly. Confirm: "I found `<path>` — using that as the data source."

**If NOT provided:** ask the user:

> I don't see an `activity.json`. I can either:
> 1. Run `pds-activity` to collect fresh data from GitHub (requires `GITHUB_TOKEN` and `lasso-issues` installed)
> 2. Use a file you point me to
>
> Which would you prefer, and if option 1, what date range should I collect? (e.g., `2026-06-01` to `2026-06-30`)

Then proceed per their answer:
- **Option 1:** Follow [Step 1a — Run pds-activity](#step-1a--run-pds-activity) below.
- **Option 2:** Ask for the file path and load it.

### Step 1a — Run pds-activity

Verify `pds-activity` is available:
```bash
pds-activity --version 2>/dev/null || python -m lasso.issues.activity.cli --version 2>/dev/null
```

If missing, offer to install from main branch:
```bash
pip install git+https://github.com/NASA-PDS/lasso-issues.git@main
```

Or if the repo is checked out locally at `~/proj/pds/pdsen/workspace/lasso-issues`:
```bash
pip install -e ~/proj/pds/pdsen/workspace/lasso-issues
```

Once available, run collection — save output to `~/.claude/tmp/activity.json`:
```bash
mkdir -p ~/.claude/tmp
pds-activity \
  --org NASA-PDS \
  --start-date <START> \
  --end-date <END> \
  --output ~/.claude/tmp/activity.json
```

Confirm success: "Collected activity data — `<N>` issues, `<M>` PRs, `<K>` releases."

---

## Step 2 — Ask Clarifying Questions

Before generating the report, ask the user ALL of the following questions in a single message. If the user says "just go" or "use defaults", apply the defaults shown and skip ahead.

```
Before I generate the report, a few quick questions:

1. **Reporting period label** — What should I call this period?
   (e.g., "Sprint 142", "Q3 FY2026", "June 2026", "FY2026 Annual")
   Default: derived from the activity.json date range

2. **Priority themes or highlights** — Are there any areas you want foregrounded or called out?
   (e.g., "Highlight Registry work", "Focus on cloud migration", "Lead with release milestones")
   Default: none — use natural grouping

4. **Scope** — Which product groups should I include?
   Options: All groups / Specific groups (list them) / Exclude specific groups
   Default: all groups with activity

5. **Output format** — Any specific format requirements?
   (e.g., Markdown, slide bullet points, email summary, Confluence wiki markup)
   Default: Markdown
```

Wait for answers before proceeding to Step 3.

---

## Step 3 — Parse and Group Activity Data

Load the `activity.json`. The top-level structure is:
```json
{
  "metadata": { "org": "...", "start_date": "...", "end_date": "...", "repo_count": N, ... },
  "issues": [ { "repo": "...", "title": "...", "state": "...", "labels": [...], "closed_at": "...", "html_url": "...", ... } ],
  "pull_requests": [ { "repo": "...", "title": "...", "merged_at": "...", "html_url": "...", ... } ],
  "releases": [ { "repo": "...", "tag": "...", "name": "...", "published_at": "...", "html_url": "...", ... } ]
}
```

### 3a — Build the repo → product group map

Use the product groupings embedded in [pds-products.yaml](./pds-products.yaml) in this skill directory. Map each repo name to its product group name. Repos not found in any product's `repositories` list go into **Uncategorized**.

Products with `ignore: true` (`dependencies`, `node-products`, `archived_repositories`) should be silently excluded — do not surface them in the report.

**Work stream grouping (use as top-level sections in this order):**
1. `core-data-services` — Core Data Services
2. `planetary-data-cloud` — Planetary Data Cloud
3. `web-modernization` — Web Modernization
4. *(no work_stream)* — Other / Cross-cutting

Within each work stream, group by product name (use the product's `description` field as a human-readable label).

**Core backbone products** (`core_backbone: true`) should be visually distinguished — add a ⭐ suffix to their section header.

### 3b — Collect accomplishments per product group

For each product group, gather:

**Closed issues** — `issues` where `state == "closed"` and `closed_at` is within the reporting window. Each issue is one accomplishment bullet.

**Merged PRs** — `pull_requests` where `merged_at` is within the reporting window. Surface only if NOT already represented by a linked issue (check `linked_issues` field — if non-empty and those issues are already captured, skip the PR to avoid duplication). If a PR has no linked issue, surface it as a standalone accomplishment.

**Releases** — `releases` where `published_at` is within the reporting window. Always surface releases as accomplishments (they represent completed milestones). **Skip pre-releases and dev builds**: exclude any release whose `tag` contains `SNAPSHOT`, `-dev`, `-alpha`, `-beta`, or `-rc` (case-insensitive) — these are not shipped deliverables.

**Filter out noise:** Skip issues/PRs with labels: `duplicate`, `invalid`, `wontfix`, `icebox`, `spam`. Skip bot-authored PRs (author contains `[bot]`).

**Exclude ignored products:** Any repo belonging to a product with `ignore: true` in `pds-products.yaml` (`ops`, `dependencies`, `node-products`, `archived_repositories`) must be silently excluded from all counts, bullets, and the metrics section. Never surface these repos anywhere in the report.

### 3c — Compute metrics

Before generating the report, compute the following counts across all non-ignored repos.

**Issue type counts** — classify each closed issue by its labels using this priority order (first match wins):

| Label(s) | Type |
|---|---|
| `bug`, `defect` | Bug |
| `requirement`, `feature` | Requirement / Feature |
| `enhancement`, `improvement` | Enhancement |
| `task` | Task |
| `theme` | Theme |
| `security` | Security |
| `breaking-change`, `backwards-incompatible` | Breaking Change |
| *(none of the above)* | Other |

Track per-type counts and per-product-group breakdowns. Also track:
- **Releases shipped**: count of `releases` items in the window (excluding SNAPSHOT / -dev / -alpha / -beta / -rc tags)
- **PRs merged** (standalone, not already counted via linked issue): count
- **Repositories active**: count of distinct repos with at least one closed issue, merged PR, or release
- **Themes addressed**: count of distinct parent issues / themes referenced by any closed issue
- **Must-have items completed**: count of closed issues with `p.must-have` label
- **Should-have items completed**: count of closed issues with `p.should-have` label

### 3d — Detect highlights

Across ALL product groups, identify the top 5–8 accomplishments that represent the most significant work. Criteria:
- Issues linked to a `theme` or `p.must-have` label
- Issues whose parent issue is a release theme
- Releases (always significant)
- Issues/PRs with labels: `breaking-change`, `backwards-incompatible`, `security`, `requirement`
- Issues with `closing_release` set (delivered in a release)

---

## Step 4 — Generate the Report

### Report structure

```markdown
# PDS EN Accomplishments — <REPORTING_PERIOD>

> **Period:** <START_DATE> – <END_DATE>

---

## At a Glance

| Metric | Count |
|--------|-------|
| 🐛 Bugs resolved | N |
| ✨ Requirements / Features delivered | N |
| 🔧 Enhancements completed | N |
| ✅ Tasks completed | N |
| 🎯 Themes addressed | N |
| 🔒 Security issues resolved | N |
| ⚠️ Breaking changes | N |
| 🚀 Releases shipped | N |
| 📦 Repositories active | N |
| 🔀 PRs merged (standalone) | N |
| 🏆 Must-have items completed | N |
| 📋 Should-have items completed | N |

*(Excludes noise labels: duplicate, invalid, wontfix, icebox, spam)*

---

## Highlights

- <Top accomplishment 1> ([repo#N](url))
- <Top accomplishment 2> ...
(5–8 bullets, outcome-focused, one link per bullet)

---

## Core Data Services

### <Product Name> [⭐ if core_backbone]
*<product description>*

**Releases**
- `v<tag>` — <release name> ([<repo>](url))

**Completed Work**
- <Issue/PR title> ([<repo>#<N>](url))
- ...

(repeat for each product with activity in this work stream)

---

## Planetary Data Cloud

(same structure)

---

## Web Modernization

(same structure)

---

## Uncategorized

*Repositories not mapped to a product group:*

- **<repo-name>**: <title> ([#N](url))
- ...

---

## Summary by Work Stream

| Work Stream | Products Active | Bugs | Requirements | Enhancements | Tasks | Releases |
|-------------|----------------|------|--------------|--------------|-------|----------|
| Core Data Services | N | N | N | N | N | N |
| Planetary Data Cloud | N | N | N | N | N | N |
| Web Modernization | N | N | N | N | N | N |
| Uncategorized | — | N | N | N | N | N |
| **Total** | **N** | **N** | **N** | **N** | **N** | **N** |
```

### Bullet style rules

- **Start with a verb**: "Adds…", "Fixes…", "Delivers…", "Implements…", "Resolves…"
- **Keep bullets ≤ ~15 words** before the link
- **Every bullet MUST end with a GitHub link** in the form `([repo#N](url))` or `([repo](url))` for releases
- **Breaking changes** get a ⚠️ prefix: `⚠️ Breaking: removes deprecated API v1 ([registry#123](url))`
- **Security fixes** get a 🔒 prefix
- For releases, format as: `` `v1.4.0` released — <brief description> ([registry](url)) ``
- If a parent issue / theme is known, add it as a sub-bullet: `  ↳ Theme: [Sprint 141 Theme: Cloud Migration](url)`
- Omit product sections with zero activity (don't create empty headers)

---

## Step 5 — Present and Offer Export

Display the full report in the conversation.

Then ask:
> Would you like me to:
> 1. Save this to a file (e.g., `accomplishments-<period>.md`)?
> 2. Adjust any sections, reorder priorities, or change the format?
> 3. Generate a condensed version (e.g., slide bullets or email summary)?

Apply any requested changes and save if asked.

---

## Edge Cases

- **Empty product groups**: Silently omit from report — do not create headers with no content.
- **Repo not in products.yaml**: Goes to Uncategorized. Never fail on unknown repos.
- **Duplicate accomplishments** (same issue appears in both `issues` and linked from a PR): Deduplicate — show the issue once, skip the PR.
- **No activity in a work stream**: Omit that entire work stream section.
- **Releases with empty body_summary**: Show as `` `vtag` released `` with just the link.
- **Very large datasets** (500+ items): Summarize by product group rather than listing every item — cap each product at 10 bullets, add "_(and N more)_" if truncated.
- **Missing dates** (`closed_at` or `merged_at` is null): Include the item but note "(date unknown)".
- **Parent issue / theme linking**: If `parent_issue` is present on an ActivityIssue, show the theme title as a sub-bullet to provide context.

---

## Additional Resources

- [pds-products.yaml](./pds-products.yaml) — PDS product-to-repository mapping used for grouping
- [lasso-issues CLI docs](https://github.com/NASA-PDS/lasso-issues) — source for `pds-activity` tool
