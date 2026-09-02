# Product

## Register

product

## Users

Developers and project maintainers who keep their backlog in Markdown and want to review, organize, and update work without leaving the repository. Their primary WebUI workflow is fast board triage: scanning columns, sorting tasks, filtering by labels or milestones, and making small project-configuration changes.

## Product Purpose

backlog-md-py provides a Python implementation of a Markdown-native task tracker with CLI, MCP, and browser interfaces over the same repository data. The WebUI should make common planning work convenient while preserving Markdown files as the source of truth and remaining compatible with Backlog.md data where practical.

## Brand Personality

Focused, practical, and quiet. Copy should be direct and controls should feel familiar, dependable, and unobtrusive.

## Anti-references

Avoid a generic SaaS-dashboard redesign, decorative motion, novelty interactions, and visual changes unrelated to the workflow being improved. Do not hide repository behavior behind UI-only state or introduce frontend machinery that is disproportionate to the dependency-free browser architecture.

## Design Principles

1. Keep Markdown as the visible and recoverable source of truth.
2. Make frequent board actions quick without crowding the board.
3. Reuse existing browser patterns before introducing new components or dependencies.
4. Preserve behavior across CLI, MCP, and WebUI whenever a feature changes repository data.
5. Prefer explicit validation and actionable errors over surprising or partial changes.

## Accessibility & Inclusion

Target WCAG AA contrast. Keep controls keyboard accessible, provide visible focus states, avoid color-only meaning, and respect reduced-motion preferences. No additional project-specific accessibility requirements are currently identified.
