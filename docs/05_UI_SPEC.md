# 05 — UI Specification

**What this is.** The design system as built, the screens as they exist, and
the rules that govern what the console is allowed to say. It documents a
working implementation rather than proposing one: every token, component and
screen below is in `frontend/src` and was checked against a running build.

**Where it departs from the brief.** The brief asks for annotated mockups
*before* coding. The console was already built when this audit began, so this
document is written the other way round — it records the system that exists
and marks what is missing. Section 9 lists the gaps.

---

## 1. The rule the whole console is built on

> **Never show a number you did not measure. Never show a control that does
> not reach anything.**

This is not a style preference; it is the direct lesson of the audit. F1
reported a successful launch while the profile browsed as the host. F16 had
two wizard steps that configured nothing. F19's WebRTC control matched nothing
downstream. F42's identity picker offered ten operating systems and produced
Windows every time. Each was invisible precisely because the interface looked
right.

Three consequences, applied everywhere:

**An unreadable value renders as `—`, never as `0`.** Zero is a measurement;
an em dash is an admission. `plans._current_usage()` returns `None` rather
than `0` for a field it could not read, and the Usage page renders that
difference.

**A control that is declarative says so.** The Usage page's limits table marks
each limit `enforced` or `declared only`. Two of five are real. Letting them
share a table unlabelled is how an interface comes to describe a product that
does not exist.

**A destructive action states what it will destroy, in counts.** The account
deletion dialog fetches a live preview and shows the real numbers before the
confirmation field unlocks.

---

## 2. Design tokens

Single source: `frontend/src/styles/tokens.css` (264 lines). No page styles
its own palette.

**Theming** is `data-theme="light|dark"` on `<html>`, set pre-paint by a
layout script and persisted as `mp_theme`, falling back to
`prefers-color-scheme`. Tailwind's `dark:` variant is rewired to that
attribute, so there is one mechanism rather than two.

### Colour

| Role | Token | Light | Dark |
|---|---|---|---|
| Brand (pine teal) | `--color-brand-50…900` | `brand-500: #0e7c6b` | shifted for dark surfaces |
| App background | `--color-surface-0` | `#f6f7f9` | deep blue-black |
| Raised surface | `--color-surface-1` | `#ffffff` | graphite |
| Sunken / inset | `--color-surface-2` | `#f0f2f5` | — |
| Hover wash | `--color-surface-3` | `#e7eaef` | — |
| Borders | `--color-border-subtle` / `-strong` | `#e3e7ec` / `#cfd6de` | — |
| Primary text | `--color-ink-1` | `#161b22` | — |
| Secondary | `--color-ink-2` | `#4b5563` | — |
| Metadata | `--color-ink-3` | `#8a94a1` | — |

**Status colours are fixed in meaning and never restyled per page:**

| | Token | Means |
|---|---|---|
| 🟢 | `--color-ok` `#16803c` | healthy, running |
| 🟠 | `--color-warn` `#b45309` | warning, overridden, needs attention |
| 🔴 | `--color-err` `#c92a2a` | error, destructive |
| 🔵 | `--color-info` `#1d5fc2` | informational |
| ⚪ | `--color-idle` `#8a94a1` | inactive, declarative, not applicable |

`idle` doing double duty as "inactive" and "declarative" is deliberate: a
limit nothing enforces and a profile that is not running are the same kind of
fact — present in the list, not currently doing anything.

### Type, radius, motion

- **Sans:** Geist → Inter → system stack. **Mono:** `ui-monospace` → SF Mono → Cascadia → Menlo → Consolas → Liberation Mono.
- **Radius:** `xs 4 · sm 6 · md 8 · lg 12 · xl 16 · full`.
- **Shadows:** three levels, restrained — *elevation comes from layering, not glow*.
- **Motion:** `--dur-1 80ms · --dur-2 140ms · --dur-3 220ms`, easing `cubic-bezier(0.16, 1, 0.3, 1)`. Everything is at or under the brief's 200 ms budget except the largest overlay transition at 220 ms. `@media (prefers-reduced-motion: reduce)` is honoured.

**Monospace is semantic, not decorative.** It marks a value the operator might
copy or compare: ids, UA strings, ports, proxy endpoints, tokens, audit
details. Prose is never mono.

---

## 3. Component inventory

26 primitives in `frontend/src/components/ui/`, all token-driven:

`alert · avatar · badge · button · card · checkbox · copy-button · dialog ·
dropdown-menu · empty-state · field · input · kbd · label · pagination ·
progress · radio · select · separator · skeleton · spinner · stat · switch ·
table · tabs · tooltip`

**Rules that are enforced by the components rather than by review:**

- `Dialog` is focus-trapped and labelled. `modal` disables overlay-click and Escape dismissal — reserved for destructive confirmations, so a misclick cannot dismiss a warning.
- `Button` has one `danger` variant. If it is red, it destroys something.
- `EmptyState` requires a `title` and a `description`; an empty table cannot render as blank.
- `Select` takes `options` as data, not children, so no screen can hand-roll a `<select>` that skips the label.
- `Skeleton` is the only loading affordance for content; `Spinner` is for in-button work.

---

## 4. Layout

```
┌────────────┬──────────────────────────────────────────────┐
│            │  Topbar: search / palette · health · theme   │
│  Sidebar   ├──────────────────────────────────────────────┤
│  (collaps- │                                              │
│   ible)    │  Page content, max-w-6xl, px-6 py-6          │
│            │                                              │
│  v1.9.0    │                                              │
└────────────┴──────────────────────────────────────────────┘
```

Sidebar groups, in order:

| Group | Items |
|---|---|
| **Workspace** | Overview · Profiles · Devices · Network |
| **Operations** | Fingerprints · Automation · Jobs |
| **System** | Gateways · Extensions · **Usage** · **Trash** · Audit · Settings |

Routing is hash-based (`#/profiles/<id>?tab=network`) because the console is
served by Flask as static files with no SPA fallback. The topbar health pill
reads `GET /api/health` and states what it checked — "Healthy · 3 profiles ·
Chrome OK" — rather than showing a bare green dot.

---

## 5. Screens

### Overview
Dashboard: running profiles, proxy health, recent activity, resource
measurements. Metrics come from `/proc` with a ≤ 5 s cache and are marked
**UNAVAILABLE** rather than fabricated when a sample fails.

### Profiles
The primary screen. Server-side search, state and group filters; the fleet
fits one page at the 150-profile target. Row: status pill · name + id ·
group · device · proxy · last launch · fingerprint summary.

- **Multi-select** → a contextual bar: Start · Stop · Park · Delete.
- **Selection is honest about scope.** If a filter matches more than one page, the bar says "*of N matching — this page only*". An operator who believes they acted on everything is how a bulk delete takes the wrong set.
- **Bulk create** dialog: N from a template, or a pasted proxy list. Remaining fleet room is shown *before* a number is chosen; the button disables when the batch will not fit.
- **Delete** moves to the trash and says so. The copy used to read "Browser storage will be purged. This cannot be undone" — true until F36, then false, then fixed.

### Profile detail
Tabbed: Overview · Proxy · Fingerprint · Advanced · Extensions · Cookies ·
Notes · Audit, with a live consistency score (F32) and a pre-launch gate that
blocks on CRITICAL mismatches unless forced.

### Usage *(new)*
Runtime, sessions, and how many are running against the concurrency limit;
breakdowns by profile and by workspace; the individual sessions one click
away. The limits table marks each limit `enforced` or `declared only`.

### Trash *(new)*
Deleted profiles with `deletedAt` and **retained disk per row and in total** —
the trash deliberately does not free space, and an operator deciding whether
to empty it needs to know what they would get back. Restore asks for nothing;
the two permanent actions confirm with counts and bytes.

### Network · Devices · Fingerprints · Jobs · Gateways · Audit
Proxies, pools, diagnostics · the device preset library · the fingerprint
inspector · the job queue · WireGuard/Azure/custom gateways · the audit log.

### Settings
Tabs: General · Clients & Templates · Backups · Extensions · **API tokens** ·
**Your data**.

- **API tokens:** list with scopes and last-used; creation shows the secret **once**, in a dismissible panel with a copy button and the note that there is no TLS on this API. Not a toast — a toast disappears while the operator reaches for the mouse.
- **Your data:** export and deletion. Deletion fetches the preview first, shows real counts, and keeps the button disabled until `DELETE EVERYTHING` is typed.

---

## 6. Keyboard

| Key | Action |
|---|---|
| `Ctrl/Cmd + K` | Command palette |
| `N` | New profile |
| `G` then `P` / `D` / `N` / `J` | Go to Profiles / Devices / Network / Jobs |
| `?` | Shortcut help |
| `Escape` | Close overlay |
| `Shift` / `Ctrl` + click | Range / toggle select |

Shortcuts are suppressed inside `INPUT`, `TEXTAREA`, `SELECT`,
`contenteditable`, and anything with `role="combobox"` — so typing a profile
name never triggers navigation.

---

## 7. State and feedback

| State | Treatment |
|---|---|
| Loading | `Skeleton` in the content's own shape, never a spinner over a blank page |
| Empty | `EmptyState` with a title, an explanation, and the action that fills it |
| Error | The API's message verbatim, plus **Retry**. Never "Something went wrong" |
| Unavailable | `—` with a reason on hover — distinct from empty and from zero |
| Success | Toast, with an action where one follows ("Open trash" after a delete) |
| Destructive | Modal, naming the count and what is lost |

**Errors quote the server.** `apiMessage(e)` surfaces the real text, so
`fleet_full`'s "150 of 150 stored, room for 0 more" reaches the operator
instead of being flattened into a generic failure. A message that does not say
what to do next is a message that generates a support request.

---

## 8. Performance

Budgets from the brief, against the **150/20** target rather than 10,000:

| Budget | Target | Measured |
|---|---|---|
| Profile launch | < 2 s | **1.59 s** |
| Fleet launch (20) | — | **~2.9 s** for 8, parallel (F33) |
| Profile list, 150 rows | 60 fps | no jank; one page, no virtualization needed |
| Bulk create, 149 profiles | — | **0.01 s**, one transaction (F35) |
| Trash listing, 150 trashed | — | **0.1 s** (was 2.0 s before the F36 correction) |
| RAM per running profile | — | **130–216 MB** PSS; 20 ≈ 2.8 GB of 13 GB |

Virtualization was dropped deliberately: 150 rows of a plain table is not a
rendering problem. What was *not* skipped is anything superlinear at this
size — the quadratic list, the unopenable hundredth profile, the sequential
launch were all real at 150 and all fixed.

---

## 9. Gaps

Stated rather than implied:

| Gap | Note |
|---|---|
| **No screenshot-level competitor comparison** | The brief asks for visual comparison against AdsPower/GoLogin/Multilogin. None was installed; see [`03`](03_FEATURE_MATRIX.md) for the same caveat. |
| **Accessibility not audited** | `aria-*` is used throughout and dialogs are focus-trapped, but no screen-reader pass, no contrast audit, no keyboard-only walkthrough was run. Do not claim WCAG conformance. |
| **No annotated mockups** | The console was built before this audit; this document is descriptive. |
| Inline table editing | Not implemented — the brief asks for it |
| Column customization, saved views | Not implemented |
| Drag-and-drop to folders | Not implemented |
| Undo on destructive actions | **Partial** — trash is undo for deletion (F36); nothing else has it |
| Automation / RPA screen | Placeholder; no builder exists |
| Responsiveness below ~1024 px | Untested; this is a desktop app |

The accessibility row is the one worth flagging to anyone selling this. The
primitives were built with the right attributes, which is necessary and not
sufficient, and nobody has verified the result with an actual screen reader.

---

## 10. What the console must never do

The list this document exists to enforce:

1. Render a fabricated number instead of "unavailable".
2. Offer a control that reaches nothing, or leave one unlabelled beside controls that do.
3. Report success for work that did not happen, or partial work as complete.
4. Describe a destructive action inaccurately — including describing it as *more* destructive than it is, which teaches the operator to distrust the copy.
5. Say "an error occurred" when the server said something specific.
6. Imply a selection covers more than it does.

Every one of these is an audit finding wearing an interface.

---

*Companions:* [`03_FEATURE_MATRIX`](03_FEATURE_MATRIX.md) for competitive
position, [`04_FINGERPRINT_TEST_RESULTS`](04_FINGERPRINT_TEST_RESULTS.md) for
the measurements behind the consistency score, and
[`02_AUDIT_REPORT`](02_AUDIT_REPORT.md) for the findings each rule came from.
