# DESIGN — Diggypaper frontend design system

Single source of truth for visual decisions. Tokens live in
`frontend/src/app/globals.css`; components in `frontend/src/components/`.

## Color tokens (OKLCH, 3 levels)

`primitives (oklch values) → semantic (CSS variables) → components (Tailwind classes)`.
No pure grey/pure black — every neutral carries a tint (hue ≈ 250).

| Token | Light | Dark | Role |
|---|---|---|---|
| `--background` | oklch(0.985 0.002 250) | oklch(0.17 0.01 260) | page background |
| `--surface` | oklch(1 0 0) | oklch(0.22 0.012 260) | cards, inputs, modals |
| `--surface-muted` | oklch(0.962 0.004 250) | oklch(0.26 0.014 260) | hovers, skeleton, muted fills |
| `--foreground` | oklch(0.24 0.02 260) | oklch(0.93 0.005 250) | text |
| `--muted` / `--muted-foreground` | 0.52 / 0.44 | 0.70 / 0.78 | secondary text |
| `--border` | oklch(0.9 0.006 250) | oklch(0.32 0.015 260) | borders |
| `--primary` / `--primary-foreground` | oklch(0.55 0.19 258) / white | oklch(0.65 0.19 258) / oklch(0.15 0.01 260) | CTA, links, active tab |
| `--danger` | oklch(0.55 0.2 25) | oklch(0.65 0.2 25) | errors, destructive |
| `--success` | oklch(0.48 0.16 155) | oklch(0.7 0.15 155) | confirmed, completed |
| `--warning` | oklch(0.50 0.15 75) | oklch(0.8 0.14 75) | in-progress, warnings |
| `--focus` | oklch(0.55 0.19 258) | oklch(0.7 0.19 258) | focus ring (2px, WCAG 2.4.11) |

### Contrast rule (WCAG 1.4.3)

All text on any token background must reach **≥ 4.5:1** in both themes. Light-theme
`--success` and `--warning` were lowered (L=0.55→0.48, L=0.72→0.50) to clear the
threshold on every surface they sit on (verified by WCAG computation: warning 6.12:1 on
white, 5.29:1 on `bg-warning/10`, 4.55:1 on `bg-warning/20` (Badge yellow); success
5.80:1 on white, 4.65:1 on `bg-success/15` (Badge green), 5.20:1 on `bg-surface-muted`).
Dark-theme `--primary-foreground` on `--primary` measures 5.96:1 — the audit's 2.70:1
claim is a false positive; do not "fix" it based on unverified ratios. Never introduce hardcoded Tailwind
color classes (`text-emerald-500`, `bg-white`, …): they bypass the tokens and break the
dark theme.

## House components

- **`Card`** (`ui/Card.tsx`) — `rounded-xl border bg-surface p-4`, optional `<h2>` title.
  Use for every content block; never restyle with raw divs.
- **`Badge`** (`ui/Card.tsx`) — status pill, `bg-{state}/15 text-{state}`. The **text
  carries the meaning**; color is complementary (SC 1.4.1). Use `statusColor(status)` for
  job/project states — never a raw `<Badge color>` guess.
- **`Field` / `Input` / `Textarea` / `Select` / `Checkbox`** (`ui/Field.tsx`) — label +
  field + hint + error in one `<label>` (implicit association). Every input needs a
  visible label; placeholders are never labels. Errors show text + ⚠ icon, `role="alert"`.
- **`EmptyState`** (`ui/Feedback.tsx`) — title + hint + optional action. Required on every
  list: a page is never silently empty.
- **`Skeleton` / `Spinner`** (`ui/Feedback.tsx`) — skeleton for lists (preferred),
  spinner only for whole-page waits.
- **`ErrorBanner`** (`ui/Feedback.tsx`) — icon + message + optional retry; `role="alert"`.
  Retry/close labels come from `common.retry` / `common.close` (i18n).
- **`Toast`** (`ui/Feedback.tsx`) — auto-dismiss success confirmation; `role="status"`.
- **`Modal`** (`ui/Modal.tsx`) — dialog with backdrop, Escape, outside-click close; the
  close button's accessible name defaults to `common.close`.
- **`Menu` / `MenuItem`** (`ui/Menu.tsx`) — dropdown; pass `ariaLabel` for icon-only
  toggles (e.g. the `⋯` project menu).
- **`SwipeableCard`** (`SwipeableCard.tsx`) — accept/reject swipe; card background is
  `bg-surface` (theme-aware).
- **`JobProgress`** (`JobProgress.tsx`) — analysis/import progress: counter, ETA, rate,
  named segments + legend.

## Motion

`--ease-out: cubic-bezier(0.16,1,0.3,1)`, 120ms (fast) / 180ms (base). Respect
`prefers-reduced-motion` (global rule in `globals.css` + `motion-reduce:` utilities).
JS `scrollIntoView` must check `prefers-reduced-motion` before using `smooth`.

## Anti-patterns (do not reintroduce)

- Hardcoded color classes or `bg-white`/`text-emerald-*` — tokens only.
- French (or any locale) strings hard-coded in components — everything via `useTranslations`.
- Raw backend error details shown to users — map through `errors.*` i18n keys.
- Raw English job statuses (`PENDING`…) in badges — use `jobs.status_*`.
- Placeholder as the only label; tooltip-only info on content that matters.
- Decorative emojis as icons in data surfaces.
- Disabled buttons with no visible explanation of why.
