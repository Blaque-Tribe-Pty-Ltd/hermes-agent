# Hermes Dashboard — Cockpit Redesign Design Specification

**Task:** UX Audit & Design Spec — Dashboard IA & Visual System  
**Branch:** `feat/dashboard-cockpit-redesign`  
**Date:** 2026-10-01  
**Author:** Lesedi (Creative Direction)  
**Status:** Design specification — ready for implementation review

---

## 1. Executive Summary

The existing Hermes web dashboard presents 20+ navigation items as a flat list in the sidebar. There is no Home page — the root (`/`) redirects to `/sessions`. The visual language, while consistent with the Nous design system, lacks the information hierarchy and spatial discipline of a mission-control surface. Operators must hunt through implementation-named routes (`/mcp`, `/env`, `/pairing`) rather than intent-driven groupings.

This spec proposes:

1. A **new Home/Cockpit page** at `/` that surfaces status, active work, and attention items without breaking existing bookmarks.
2. A **grouped navigation IA** organised by operator intent (not implementation subsystem).
3. A **refined visual system** — tighter spacing discipline, card hierarchy, status affordances, and colour restraint.
4. A **Command Palette** (⌘K) for keyboard-first navigation and action dispatch.
5. **Preservation of every existing route** — URLs stay stable; only the navigation structure and the root landing experience change.

The design direction is inspired by the clean mission-control feel of reference dashboards (e.g. Hermy HQ) but is grounded in Hermes' actual capabilities, the Nous design system (`@nous-research/ui`), and the existing LENS_0 theme architecture. It is **MoonPie-native**, not generic SaaS.

---

## 2. Design Principles

| Principle | What it means in practice |
|---|---|
| **Cockpit, not catalog** | The dashboard is a control surface, not a settings directory. Every element earns its place by answering "what needs my attention?" |
| **Intent over implementation** | Navigation groups describe what the operator wants to do, not what the backend module is called. |
| **Progressive disclosure** | Frequently used surfaces are one click away; diagnostic and configuration surfaces are nested but never hidden. |
| **Status is ambient** | Health, activity, and queue state are visible without clicking. Colour is used for meaning, not decoration. |
| **Keyboard parity** | Every navigational action reachable by mouse must be reachable by keyboard (Command Palette + shortcuts). |
| **Theme-respectful** | All visual decisions must degrade gracefully across the 8 built-in themes and user-defined YAML themes. |

---

## 3. Route Classification by Operator Intent

### 3.1 Methodology
Each existing route is classified by the **operator goal** that motivates the visit, not the backend subsystem that serves it. A route can have a primary and secondary intent.

### 3.2 Classification Table

| Route | Current Label | Primary Intent | Secondary Intent | Notes |
|---|---|---|---|---|
| `/` | *(redirect)* | — | — | Redirects to `/sessions`; becomes Home/Cockpit |
| `/chat` | Chat | **Converse** | Operate | Real-time terminal interaction with the agent |
| `/sessions` | Sessions | **Review** | Converse | Browse, search, and manage conversation history |
| `/files` | Files | **Asset manage** | — | Upload, download, organise workspace files |
| `/analytics` | Analytics | **Observe** | Tune | Token usage, cost, model performance over time |
| `/models` | Models | **Tune** | Observe | Model routing, auxiliary task assignment, MOA config |
| `/logs` | Logs | **Diagnose** | Observe | System logs for debugging |
| `/cron` | Cron | **Observe** | Configure | Scheduled jobs — status and configuration |
| `/skills` | Skills | **Extend** | Discover | Skill library, install, update, scan |
| `/plugins` | Plugins | **Extend** | Configure | Plugin management and discovery |
| `/mcp` | MCP | **Extend** | Connect | External tool integration via Model Context Protocol |
| `/channels` | Channels | **Connect** | Configure | Messaging platform integrations |
| `/webhooks` | Webhooks | **Connect** | Configure | Webhook endpoint configuration |
| `/pairing` | Pairing | **Connect** | Secure | Mobile app device pairing |
| `/devices` | Devices | **Connect** | Observe | Paired device management |
| `/profiles` | Profiles | **Configure** | Tune | Agent personality/profile management |
| `/profiles/new` | New Profile | **Configure** | — | Profile creation wizard |
| `/config` | Config | **Configure** | — | Comprehensive system settings (16+ categories) |
| `/env` | Keys | **Secure** | Configure | Environment variables and API key management |
| `/system` | System | **Diagnose** | Maintain | Gateway health, restart, update, diagnostics |
| `/docs` | Documentation | **Reference** | — | In-app documentation viewer |

### 3.3 Intent Buckets (Derived)

From the classification, five operator-intent buckets emerge:

1. **Converse & Review** (`/chat`, `/sessions`) — Talking to and remembering conversations with the agent.
2. **Observe & Tune** (`/analytics`, `/models`, `/logs`, `/cron`) — Monitoring performance and adjusting behaviour.
3. **Extend & Connect** (`/skills`, `/plugins`, `/mcp`, `/channels`, `/webhooks`, `/pairing`, `/devices`, `/files`) — Adding capabilities and integrating external systems.
4. **Configure & Secure** (`/config`, `/env`, `/profiles`, `/profiles/new`) — System setup, credentials, and personality.
5. **Diagnose & Reference** (`/system`, `/docs`) — Troubleshooting and help.

---

## 4. Proposed Information Architecture

### 4.1 Conceptual Buckets (Target State)

The navigation is reorganised into **five top-level conceptual buckets** that align with the operator's mental model of managing an autonomous agent. This structure is aspirational where gaps exist, but every *existing* route is mapped to its closest bucket.

| Bucket | Concept | Existing Routes | Gap / Future |
|---|---|---|---|
| **Home** | Executive cockpit — status, attention, pulse | *(new page)* | Needs new `/` implementation |
| **MoonPie** | Conversations, active runs, history | `/chat`, `/sessions` | Approvals inbox, delegated-work queue (future) |
| **Knowledge** | Tools, memory-adjacent assets, extensions | `/files`, `/skills`, `/plugins`, `/mcp` | SecondBrain wiki, memory browser (future) |
| **System** | Health, config, infrastructure, diagnostics | `/analytics`, `/models`, `/logs`, `/cron`, `/system`, `/config`, `/env`, `/profiles` | Worker telemetry grid (future) |
| **Network** | Integrations, channels, devices, webhooks | `/channels`, `/webhooks`, `/pairing`, `/devices` | Gateway connection map (future) |

> **Note on gaps:** The Hermes web dashboard does not currently expose a kanban board, project workspaces, SecondBrain wiki, or approvals inbox. These are noted as **future expansion zones** within the IA so the navigation structure can accommodate them without re-architecture. The spec does not prescribe fake pages for these gaps.

### 4.2 Sidebar Navigation Map

The sidebar is restructured from a flat list into **grouped sections with clear hierarchy**.

```
┌─ HERMES AGENT (brand mark, collapse toggle)
│
├─ PROFILE SWITCHER (existing, unchanged)
│
├─ HOME ................................ [HomeIcon]
│
├─ MOONPIE ............................. [MessageSquare]
│  ├─ Chat ............................. [Terminal]
│  └─ Sessions ......................... [MessageSquare]
│
├─ KNOWLEDGE ........................... [Database]
│  ├─ Files ............................ [FolderOpen]
│  ├─ Skills ........................... [Package]
│  ├─ Plugins .......................... [Puzzle]
│  └─ MCP .............................. [Plug]
│
├─ SYSTEM .............................. [Cpu]
│  ├─ Analytics ........................ [BarChart3]
│  ├─ Models ........................... [Brain]
│  ├─ Cron ............................. [Clock]
│  ├─ Logs ............................. [FileText]
│  ├─ Profiles ......................... [Users]
│  ├─ Config ........................... [Settings]
│  ├─ Keys ............................. [KeyRound]
│  └─ Diagnostics ...................... [Wrench] → /system
│
├─ NETWORK ............................. [Globe]
│  ├─ Channels ......................... [Radio]
│  ├─ Webhooks ......................... [Webhook]
│  ├─ Pairing .......................... [ShieldCheck]
│  └─ Devices .......................... [Laptop]
│
├─ REFERENCE ........................... [BookOpen]
│  └─ Documentation .................... [BookOpen]
│
├─ [Plugin items] ...................... (dynamic, as today)
│
├─ SYSTEM ACTIONS
│  ├─ Gateway status text
│  ├─ Restart gateway
│  └─ Update Hermes (if available)
│
└─ FOOTER
   ├─ Theme switcher
   ├─ Language switcher
   ├─ Version
   └─ Auth / Org link
```

### 4.3 Route Preservation

Every existing route path is preserved. The only URL change is that `/` stops redirecting and instead renders the new Home/Cockpit page. All deep links, bookmarks, and plugin tab paths remain valid.

---

## 5. Visual System Specification

### 5.1 Design Tokens (Refinements)

The spec builds on the existing theme system (`@nous-research/ui`, LENS_0, `ThemeProvider`). No new design-token infrastructure is required. These are **usage rules** and **refinements**.

#### Spacing Discipline

| Context | Current | Proposed | Rationale |
|---|---|---|---|
| Sidebar section heading padding | `px-5 pt-2.5 pb-1` | `px-4 pt-3 pb-1.5` | Tighter horizontal, more vertical breathing room |
| Nav link padding | `px-5 py-2.5` | `px-4 py-2` | Slightly denser to fit groups without scrolling |
| Sidebar width (expanded) | `w-64` (256px) | `w-60` (240px) | Slimmer rail; content area gains 16px |
| Sidebar width (collapsed) | `w-14` (56px) | `w-12` (48px) | Icon-only rail; matches common 48px touch target |
| Content page max-width | unconstrained | `max-w-[1440px]` | Prevents line-length explosion on ultrawide monitors |
| Content horizontal padding | `px-3 sm:px-6` | `px-4 sm:px-6 lg:px-8` | More room at large breakpoints |

#### Typography Refinements

| Element | Current | Proposed |
|---|---|---|
| Sidebar section heading | `text-xs tracking-[0.12em] uppercase` | `text-[11px] tracking-[0.14em] uppercase font-medium text-text-tertiary` |
| Sidebar nav link | `text-sm tracking-[0.12em] uppercase` | `text-sm tracking-[0.08em] uppercase` | Reduce tracking for readability at small sizes |
| Active nav indicator | `w-px bg-midground` left border | `w-[2px] bg-midground` left border + subtle `bg-midground/5` background | Stronger affordance |
| Page title (H1) | varies by page | `text-2xl font-semibold tracking-[-0.01em] text-midground` | Consistent page chrome |
| Card title | varies | `text-sm font-medium tracking-[0.01em] text-midground` | Slightly heavier for hierarchy |
| Data label | ad-hoc | `text-[11px] uppercase tracking-[0.1em] text-text-tertiary font-medium` | Consistent telemetry label style |

#### Card System

The existing `@nous-research/ui` `Card` component is the foundation. The spec adds **card role variants** for the cockpit:

| Variant | Use case | Visual treatment |
|---|---|---|
| `default` | General content | Existing `Card` — `bg-card`, `border-border` |
| `metric` | KPI / telemetry tile | No border, `bg-midground/5`, inner padding `p-4`, corner radius matches `--radius` |
| `attention` | Alerts, approvals, errors | Left border `3px` in `warning`/`destructive`/`success` tone; background unchanged |
| `surface` | Grouping related controls | `bg-background-base`, `border-border`, slightly more padding (`p-5`) |

All cards share:
- `transition-shadow duration-200` on hover (subtle `shadow-sm` lift for interactive cards only)
- `focus-visible:ring-1 focus-visible:ring-midground/40` for keyboard focus
- No arbitrary `box-shadow` in default state — maintain the flat, cockpit aesthetic

### 5.2 Status Affordances

Status must be readable at a glance without colour being the sole carrier of meaning.

| State | Icon | Colour | Text treatment | Accessible label |
|---|---|---|---|---|
| Running / Healthy | `CheckCircle2` | `text-success` | Normal weight | "Healthy" |
| Warning / Degraded | `AlertTriangle` | `text-warning` | Normal weight | "Warning: {detail}" |
| Error / Failed | `XCircle` | `text-destructive` | Normal weight | "Error: {detail}" |
| Loading / Unknown | `Spinner` | `text-text-tertiary` | Italic or muted | "Loading status" |
| Active / Selected | — | `text-midground` | Medium weight | — |
| Inactive / Disabled | — | `text-text-disabled` | Normal weight | "Disabled" |

Rules:
- **Never use colour alone.** Every status indicator must have an icon or text label.
- **Animated states** (Spinner, pulse) must respect `prefers-reduced-motion`.
- **Collapsed sidebar** shows a single dot colour-coded to the worst current status (green → yellow → red).

### 5.3 Hover / Focus / Selected States

| Element | Hover | Focus | Selected / Active |
|---|---|---|---|
| Sidebar nav link | `bg-midground/5` overlay | `focus-visible:ring-1 focus-visible:ring-midground/40` | `bg-midground/5` + `w-[2px]` left midground bar + `text-midground` |
| Sidebar section heading | none | none | `text-midground` if section contains active child |
| Card (interactive) | `shadow-sm` | `focus-visible:ring-1 focus-visible:ring-midground/40` | `ring-1 ring-midground/20` |
| Button (ghost) | `bg-midground/5` | `focus-visible:ring-1 focus-visible:ring-midground/40` | `bg-midground/10` |
| Metric tile | `bg-midground/[0.07]` | `focus-visible:ring-1 focus-visible:ring-midground/40` | — |

### 5.4 Colour Discipline

| Rule | Enforcement |
|---|---|
| Background layers | Only `bg-background-base`, `bg-card`, `bg-midground/N` where N ≤ 10. No arbitrary hex backgrounds. |
| Text hierarchy | `text-midground` (primary), `text-text-secondary` (body), `text-text-tertiary` (labels/meta), `text-text-disabled` (inactive). |
| Accent usage | `midground` is the sole accent. Semantic colours (`success`, `warning`, `destructive`) are reserved for status only. |
| Borders | `border-border` (15% midground opacity) for all structural borders. No one-off border colours. |
| Charts | Use theme's `--series-input-token` and `--series-output-token`; derive additional series colours via HSL rotation from midground hue. |

### 5.5 Density Modes

The existing `ThemeDensity` (`compact` / `comfortable` / `spacious`) must be respected. The cockpit layout defaults to `comfortable`. When `compact` is active:
- Sidebar nav link padding reduces to `px-3 py-1.5`
- Card padding reduces to `p-3`
- Section headings hide their top padding
- Metric tiles show value + label on one line where possible

When `spacious` is active:
- Sidebar nav link padding increases to `px-4 py-2.5`
- Card padding increases to `p-5`
- Content max-width narrows to `max-w-[1280px]` for readability

---

## 6. Command Palette / Quick Switcher Specification

### 6.1 Purpose
A keyboard-first interface for jumping to any page, triggering system actions, and searching across sessions/files/skills. Inspired by the reference mission-control dashboards but scoped to Hermes' actual capabilities.

### 6.2 Trigger
- **Keyboard:** `Cmd+K` (macOS) / `Ctrl+K` (Linux/Windows)
- **Mouse:** A search icon in the mobile header and a subtle "Search…" affordance in the collapsed sidebar footer

### 6.3 UI Behaviour

```
┌─────────────────────────────────────────┐
│  ⌘  Search commands, pages, sessions…  │
├─────────────────────────────────────────┤
│  Go to                                  │
│    ○ Chat          ⌘1                   │
│    ○ Sessions      ⌘2                   │
│    ○ Files         ⌘3                   │
│    ● Analytics     ⌘4  ← selected       │
│    ○ Models        ⌘5                   │
│    …                                    │
│  Actions                                │
│    ○ Restart gateway                    │
│    ○ Update Hermes                      │
│    ○ Toggle theme                       │
│  Recent sessions                        │
│    ○ "Refactor auth module…"            │
│    ○ "Design system audit…"             │
└─────────────────────────────────────────┘
```

#### Modal Shell
- Centered modal, `max-w-lg`, `rounded-lg` (uses `--radius`)
- Backdrop: `bg-black/60` with `backdrop-blur-sm`
- Input field at top: transparent background, `border-b border-border`, placeholder text `text-text-tertiary`
- Escape or click outside to close
- `aria-modal="true"`, focus trap, return focus to trigger on close

#### Search Scope
The palette searches across these scopes simultaneously:

1. **Pages** — All built-in and plugin routes. Fuzzy match on label and path.
2. **Actions** — System actions (restart, update) and common operations (toggle theme, switch profile).
3. **Recent Sessions** — Last 10 sessions from `/api/sessions`, searchable by title and first message snippet.
4. **Skills** — Installed skills, searchable by name and description.
5. **Files** — Recent/top-level files, searchable by path.

> **Implementation note:** Skills and files search can be v2 enhancements. v1 must include Pages, Actions, and Recent Sessions.

#### Navigation
- `↑` / `↓` to move selection
- `Enter` to execute
- `Cmd+1` through `Cmd+9` for the first 9 results (shown as badges)
- Result count announced via `aria-live="polite"`

#### Grouping
Results are grouped by scope with sticky section headers:
- "Go to" (pages)
- "Actions" (system operations)
- "Recent" (recent sessions)
- "Skills" (skill matches)
- "Files" (file matches)

Empty state: "No results for '{query}' — press Enter to search sessions" (falls back to global session search).

### 6.4 Shortcut Registry

| Shortcut | Action | Context |
|---|---|---|
| `Cmd+K` | Open Command Palette | Global |
| `Cmd+1` … `Cmd+9` | Select result N | While palette open |
| `Cmd+Shift+K` | Focus sidebar search / filter | Sessions page |
| `Cmd+B` | Toggle sidebar collapse | Desktop only |
| `Cmd+Shift+R` | Restart gateway | Global (with confirmation) |
| `Cmd+Shift+U` | Check for updates | Global |
| `Cmd+Shift+T` | Cycle themes | Global |

> **Accessibility:** All shortcuts must be overridable and discoverable via the Command Palette itself (searching "keyboard shortcuts" shows the list).

---

## 7. Navigation Component Specification

### 7.1 Sidebar Structure

The sidebar is rebuilt as a **hierarchical, grouped rail**.

#### Desktop (≥1024px)

**Expanded state (default):**
- Width: `240px` (`w-60`)
- Background: `var(--component-sidebar-background, var(--background-base))`
- Border: `border-r border-border`
- Sections separated by `border-t border-border/50` (subtler than full `border-border`)
- Collapse toggle: chevron icon in the brand header

**Collapsed state:**
- Width: `48px` (`w-12`)
- Only icons visible
- Section headings hidden
- Tooltips on hover/focus: `sidebar-tooltip-in` animation, positioned to the right of the rail
- Active item indicated by `w-[2px]` left bar + `bg-midground/5`
- Gateway status dot visible below system actions

#### Mobile (<1024px)

- Drawer slides in from left, full height, `z-50`
- Backdrop overlay `bg-black/70` with click-to-close
- Always expanded (no collapsed mode on mobile)
- Escape key closes
- Body scroll locked while open

### 7.2 Section Component

Each bucket (MoonPie, Knowledge, System, Network, Reference) is a `SidebarSection`:

```tsx
interface SidebarSectionProps {
  id: string;           // e.g. "system"
  label: string;        // e.g. "System"
  icon: LucideIcon;     // Section icon (shown in collapsed rail as group marker)
  items: NavItem[];     // Child routes
  defaultOpen?: boolean;
}
```

**Behaviour:**
- Sections are **collapsible** via click on the section header
- `defaultOpen`: Home and the section containing the current route are open by default; others collapsed
- State persisted in `localStorage` key `hermes-sidebar-sections`
- When collapsed rail is active, clicking a section icon expands a **flyout panel** to the right showing the section's children

### 7.3 Active State Logic

A section is considered "active" if any of its child routes match the current `pathname`. The active section header uses `text-midground` instead of `text-text-secondary`.

For exact-match routes (`/sessions` with `end={true}`), the existing `NavLink` behaviour is preserved.

### 7.4 Plugin Integration

Plugins continue to inject tabs via `manifest.tab.position`. The spec adds two new position values:

| Position | Behaviour |
|---|---|
| `"section:<sectionId>"` | Places the plugin tab inside the named section (e.g. `"section:knowledge"` puts it under Knowledge). Falls back to end if section not found. |
| `"section:<sectionId>:after:<path>"` | Places inside section after a specific child path. |

If no section is specified, plugin tabs appear in a "Plugins" section at the bottom of the sidebar, as today.

### 7.5 System Actions Block

The existing restart/update actions are moved into a dedicated **System Actions** subsection within the sidebar, above the footer:

- Gateway status text link (links to `/system`)
- Restart button (with confirmation)
- Update button (with confirmation, gated by `can_update_hermes`)

In collapsed mode, only the gateway status dot is visible. Clicking it opens a tooltip with status + actions.

---

## 8. Home / Cockpit Page Specification

### 8.1 Purpose
Replace the root redirect (`/ → /sessions`) with an **executive cockpit** that answers four questions within 3 seconds:

1. **Is MoonPie healthy?** — Gateway status, active sessions, recent errors.
2. **What is happening now?** — Active runs, recent session activity, cron jobs firing.
3. **What needs my attention?** — Alerts, warnings, actionable items.
4. **Where do I go next?** — Quick-access tiles to the most-used surfaces.

### 8.2 Page Layout

The cockpit uses a **dashboard grid** layout, not a single content column.

```
┌──────────────────────────────────────────────────────────────────────┐
│  COCKPIT                                          [Search] [Profile] │
├──────────────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐ │
│  │ Sessions    │  │ Gateway     │  │ Tokens      │  │ Cost        │ │
│  │ 12 active   │  │ Running ✓   │  │ 2.4M today  │  │ $4.20 today │ │
│  └─────────────┘  └─────────────┘  └─────────────┘  └─────────────┘ │
│  ┌────────────────────────────┐  ┌─────────────────────────────────┐ │
│  │ Recent Activity            │  │ Quick Actions                   │ │
│  │ • "Design spec…" — 2m ago  │  │ [Open Chat] [New Session]       │ │
│  │ • "Refactor…" — 15m ago    │  │ [Check Logs] [Restart GW]       │ │
│  │ • "Update deps…" — 1h ago  │  │                                 │ │
│  └────────────────────────────┘  └─────────────────────────────────┘ │
│  ┌────────────────────────────┐  ┌─────────────────────────────────┐ │
│  │ System Health              │  │ Cron & Schedules                │ │
│  │ Channels: 4/5 healthy      │  │ • Daily backup — in 2h          │ │
│  │ Models: 12 configured      │  │ • Weekly report — in 1d         │ │
│  │ Disk: 72% used             │  │ • Skill scan — running          │ │
│  └────────────────────────────┘  └─────────────────────────────────┘ │
│  ┌─────────────────────────────────────────────────────────────────┐ │
│  │ Skills & Plugins — recently updated                             │ │
│  │ [skill-a] [skill-b] [plugin-c] [plugin-d]                       │ │
│  └─────────────────────────────────────────────────────────────────┘ │
└──────────────────────────────────────────────────────────────────────┘
```

#### Grid Specification

- **Container:** `max-w-[1440px] mx-auto px-4 sm:px-6 lg:px-8`
- **Grid:** CSS Grid, `gap-4` (16px with comfortable density)
- **Breakpoints:**
  - Mobile: 1 column, all tiles stack
  - Tablet (≥768px): 2 columns for metric row; activity + quick actions side by side
  - Desktop (≥1024px): 4 columns for metrics; 2×2 for lower sections; full-width for skills strip
- **No scrolling required** for the initial viewport on a 1080p display.

### 8.3 Tile Specifications

#### 8.3.1 Metric Tiles (Top Row)

Four metric tiles showing live data:

| Tile | Data Source | Display |
|---|---|---|
| **Active Sessions** | `status.active_sessions` | Number + "active" label + trend arrow vs last hour |
| **Gateway Status** | `gatewayLine(status)` | Icon + state label + "since {time}" |
| **Tokens Today** | `AnalyticsResponse` aggregated for current day | Compact number + input/output split bar |
| **Cost Today** | `AnalyticsResponse` aggregated for current day | Currency + "today" label |

Visual: `metric` card variant (see §5.1). No border. Value in `text-xl font-semibold text-midground`. Label in `text-[11px] uppercase tracking-[0.1em] text-text-tertiary`.

Click behaviour: each tile navigates to its detail page (`/sessions`, `/system`, `/analytics`).

#### 8.3.2 Recent Activity Feed

A scrollable list (max 6 items, `max-h-[280px]`) of recent session messages or events.

- Each row: session title (truncated), last message snippet (1 line, `text-text-secondary`), relative time (`timeAgo`), icon indicating channel (Discord, Telegram, Terminal, Web)
- Click: navigates to `/sessions?id={sessionId}`
- Empty state: "No recent activity — start a conversation in Chat"

Data source: `/api/sessions?limit=6&sort=updated`.

#### 8.3.3 Quick Actions

A grid of 4–6 shortcut buttons:

- **Open Chat** → `/chat`
- **New Session** → `/chat` (with `?new=true` or equivalent)
- **Check Logs** → `/logs`
- **Restart Gateway** → triggers system action with confirmation
- **Browse Skills** → `/skills`
- **View Config** → `/config`

Visual: `Button` component, `variant="secondary"`, `size="sm"`, arranged in a 2-column grid inside a `surface` card.

#### 8.3.4 System Health Summary

A compact readout of subsystem health:

- **Channels:** `{healthy}/{total} healthy` — links to `/channels`
- **Models:** `{count} configured` — links to `/models`
- **Disk / Memory:** usage bars (if available from `/api/system`)
- **Latest Error:** one-line snippet from `/api/logs?limit=1&level=error` — links to `/logs`

If any subsystem is unhealthy, the card gets an `attention` variant left border in `warning` or `destructive`.

#### 8.3.5 Cron & Schedules

Next 3 upcoming cron jobs from `/api/cron`:

- Job name, schedule expression, relative next-run time
- Running jobs show a `Spinner` + "Running" label
- Links to `/cron`

#### 8.3.6 Skills & Plugins Strip

Horizontal scrollable row of the 8 most recently updated skills/plugins:

- Icon + name in a compact pill/badge style
- Click navigates to `/skills` or `/plugins`
- Data source: `/api/skills` and `/api/plugins`, sorted by `updated_at` or equivalent

### 8.4 Empty / Loading States

- **Loading:** Metric tiles show `Skeleton` placeholders (pulsing `bg-midground/10` rectangles). Activity feed shows 3 skeleton rows.
- **Error:** A single `attention` card at the top: "Unable to load cockpit data. [Retry]"
- **First-run:** If no sessions exist, the Recent Activity tile shows a welcome message and prominent "Start your first conversation" CTA linking to `/chat`.

### 8.5 Accessibility

- Page title: `document.title = "Cockpit | Hermes Agent"`
- Live region for gateway status changes: `aria-live="polite"` on the status tile
- All metric tiles are `<article>` with `aria-label`
- Keyboard navigation: `Tab` moves through tiles in visual order; `Enter` activates

---

## 9. Responsive Behaviour

### 9.1 Breakpoints

| Name | Width | Key Changes |
|---|---|---|
| Mobile | < 768px | Drawer sidebar, single-column cockpit, metric tiles 2×2 grid, no collapsed rail |
| Tablet | 768–1023px | Drawer sidebar, 2-column cockpit grid, metric tiles 4 across |
| Desktop | 1024–1439px | Sticky sidebar, full cockpit grid, collapsed rail available |
| Wide | ≥1440px | Content max-width capped; sidebar can be expanded or collapsed |

### 9.2 Sidebar States

| Context | Expanded | Collapsed | Drawer |
|---|---|---|---|
| Mobile | — | — | ✅ (only option) |
| Tablet | — | — | ✅ (only option) |
| Desktop | ✅ default | ✅ user toggle | — |
| Wide | ✅ default | ✅ user toggle | — |

### 9.3 Content Adaptation

- **Cockpit metric tiles:** 4×1 on desktop, 2×2 on mobile
- **Activity + Quick Actions:** Side by side on desktop, stacked on mobile
- **System Health + Cron:** Side by side on desktop, stacked on mobile
- **Skills strip:** Horizontal scroll on all sizes; no wrapping

---

## 10. Accessibility Requirements

1. **Colour contrast:** All text meets WCAG 2.1 AA (4.5:1 for normal text, 3:1 for large text). Status icons supplement colour-coded indicators.
2. **Keyboard navigation:** Full tab order through sidebar, command palette, and cockpit tiles. No keyboard traps.
3. **Screen readers:**
   - Sidebar sections use `aria-expanded` and `aria-controls`
   - Active page announced via `aria-current="page"`
   - Command palette uses `role="dialog"` with focus trap
   - Live regions for status changes and search result counts
4. **Motion:** Respect `prefers-reduced-motion`. Disable sidebar transition animations and spinner animations when the preference is set.
5. **Touch targets:** All interactive elements ≥ 44×44px on mobile. Sidebar nav links are `py-2` minimum.

---

## 11. Theme Compatibility

The spec must work across all built-in themes and user-defined themes:

| Theme | Cockpit Adaptation |
|---|---|
| `default` (Hermes Teal) | Baseline — no adjustments needed |
| `midnight` | Inter font, larger radius — cards feel softer; maintain density |
| `ember` | Spectral serif — metric numbers should use `font-sans` override for tabular readability |
| `mono` | Zero radius — cards become strict rectangles; borders slightly heavier |
| `cyberpunk` | Share Tech Mono — all text becomes monospace; ensure metric labels remain legible at small sizes |
| `rose` | Fraunces serif — similar to ember; numbers need sans override |
| `nous-blue` (light) | Light background — status colours must darken (`text-success` may need override to `green-700`); card backgrounds use `bg-white/50` |
| `default-large` | 18px base — all spacing scales naturally via rem; verify metric tiles don't overflow 240px rail |

**Implementation note:** The cockpit page should consume the same `theme` object as every other page. No theme-specific hard-coding. Use `color-mix` and CSS variables for all dynamic colours.

---

## 12. Plugin System Impact

The navigation changes are designed to be **backward-compatible** with the existing plugin architecture:

1. **Route preservation:** Plugin routes (`manifest.tab.path`) continue to work. `buildRoutes` logic is unchanged.
2. **Tab position:** New position syntax `"section:<id>"` is additive; old syntax (`end`, `after:`, `before:`) continues to work.
3. **Override:** `tab.override` behaviour is unchanged.
4. **Slots:** All existing slots (`backdrop`, `header-banner`, `pre-main`, `post-main`, `overlay`, `header-left`, `header-right`) remain available. The new cockpit page adds two new optional slots:
   - `cockpit-metric-row` — plugins can inject additional metric tiles
   - `cockpit-sidebar` — plugins can inject a persistent widget in the cockpit sidebar area (future)

---

## 13. Implementation Phases

### Phase 1: Foundation (no user-facing change)
- [ ] Create `SidebarSection` component with collapse/expand logic
- [ ] Create grouped nav data structure (separate from flat `BUILTIN_NAV_REST`)
- [ ] Add `section:` position syntax to plugin `buildNavItems`
- [ ] Update `App.tsx` nav rendering to use grouped sections (behind feature flag or direct replacement)

### Phase 2: Visual System
- [ ] Refine sidebar spacing, typography, and active states per §5
- [ ] Implement metric card variant in page components (Analytics, Models)
- [ ] Update `index.css` with new tokens (no breaking changes)

### Phase 3: Command Palette
- [ ] Install `cmdk` or build lightweight custom palette
- [ ] Implement search across pages, actions, recent sessions
- [ ] Add keyboard shortcut registry

### Phase 4: Home / Cockpit
- [ ] Create `HomePage` (or `CockpitPage`) component
- [ ] Implement data fetching for all tiles
- [ ] Replace `RootRedirect` with `HomePage` in routes
- [ ] Add empty states, loading states, and error boundaries

### Phase 5: Polish
- [ ] Responsive testing across breakpoints
- [ ] Theme testing across all 8 presets
- [ ] Accessibility audit (keyboard, screen reader, contrast)
- [ ] Plugin compatibility verification

---

## 14. Risks & Open Questions

| Risk | Mitigation |
|---|---|
| Sidebar grouping increases cognitive load for users accustomed to the flat list | Keep sections collapsible; persist open/closed state; default-open the section containing the current route |
| New Home page may disorient users who expect `/` to go to Sessions | Add a prominent "Sessions" quick-action tile on the cockpit; monitor analytics for bounce |
| Command Palette adds bundle size | Use dynamic import (`React.lazy`) for the palette component; only load on first `Cmd+K` |
| Plugin tabs in sections may create visual inconsistency if plugin authors don't follow icon/style guidelines | Document the section convention in the plugin manifest schema; provide fallback to "Plugins" section |
| `nous-blue` light theme may have insufficient contrast for some status colours | Add `colorOverrides` for `success`/`warning`/`destructive` in the theme definition if needed |

### Open Questions

1. **Kanban exposure:** The Hermes API exposes kanban tasks (`/api/kanban`), but the web dashboard has no route for them. Should the cockpit surface "active tasks" from the kanban API as a future tile, or is that out of scope for this redesign?
2. **Approvals inbox:** Hermes has an approval system for side-effecting actions. Should the cockpit include an "Attention" tile for pending approvals?
3. **Real-time updates:** Should the cockpit poll for updates (e.g. every 30s) or use WebSocket/SSE if available?
4. **Mobile primary action:** On mobile, should the bottom navigation include a floating action button for "New Chat"?

---

## 15. Reference Analysis

### 15.1 Hermy HQ (Reference)
**What to learn from it:**
- **Information hierarchy:** The home dashboard uses a clear top-to-bottom priority: status → tasks → activity → tools. Nothing competes for attention.
- **Navigation discipline:** Sidebar sections are named for user goals ("Hermes control hub", "Memory", "Work & content") rather than backend modules.
- **Command palette centrality:** ⌘K is the primary way to move around; the sidebar is for discovery, not navigation.
- **Card density:** Tiles are information-dense but not cluttered. Each card has one job.

**What NOT to copy:**
- Its specific routes, page implementations, or component architecture.
- Its Postgres-message-bus bridge model (irrelevant to the built-in dashboard).
- Its feature set (Content OS, Client Pulse, Garden) — these are application features, not shell patterns.
- Its branding and colour palette.

### 15.2 Existing Hermes Dashboard
**What to preserve:**
- The robust plugin system with slot architecture and tab override/position.
- The theme engine with per-theme typography, layout, and palette.
- The embedded chat persistence model (ChatPage host outside Routes).
- The profile-scoped route keying (`ProfileKeyedRoutes`).
- The i18n architecture with full translation coverage.
- The existing component library (`@nous-research/ui`) — no new component library needed.

---

## 16. Handoff Checklist

- [ ] Design spec reviewed by Tsebo (QA/verification profile)
- [ ] Implementation plan agreed with Neo (implementation profile)
- [ ] All existing routes verified to remain reachable by URL
- [ ] Theme compatibility verified across 8 built-in presets
- [ ] Accessibility requirements (§10) are acceptance criteria
- [ ] Command Palette shortcut registry documented for users
- [ ] Plugin manifest documentation updated with `section:` position syntax

---

*End of specification.*
