# Dashboard Redesign — QA Verification Report

**Task:** t_d69b62f0  
**Branch:** `feat/dashboard-cockpit-redesign`  
**Reviewer:** Tsebo  
**Date:** 2026-10-01  
**Disposition:** ACCEPTED WITH CONDITIONS

---

## 1. Executive Summary

The dashboard cockpit redesign (Neo implementation of Lesedi's spec) is **structurally sound and functionally correct**. All 20+ existing routes are preserved, the new grouped navigation IA aligns with the spec's intent buckets, the Home/Cockpit page renders at `/`, the command palette is operational, and the build + full test suite pass cleanly. The MoonPie theme is registered in both frontend presets and the backend theme list.

However, **one critical bug** and **several visual deviations** from the spec were found. The critical bug (CPU cores mislabeled as "models configured") must be fixed before promotion to main. The visual deviations are cosmetic but should be addressed in a follow-up polish pass.

---

## 2. Verification Method

- Code review of all modified/new files against `web/docs/dashboard-redesign-spec.md`
- Static analysis: `tsc --noEmit` (pass), `npm run build` (pass), `npm run test` (354 passed, 50 files)
- Lint audit: 2 pre-existing errors in unmodified files (`PluginPage.tsx`, `themes/context.tsx`)
- Route inventory comparison against spec §3.2 and §4.3
- Navigation IA comparison against spec §4.2
- Visual system spot-check against spec §5
- Accessibility spot-check against spec §10

---

## 3. Route Integrity

| Route | Status | Page Component | Notes |
|---|---|---|---|
| `/` | ✅ | `HomePage` | Replaces previous `RootRedirect` to `/sessions` |
| `/chat` | ✅ | `ChatRouteSink` / persistent host | Preserved; gated by `embeddedChat` flag (pre-existing) |
| `/sessions` | ✅ | `SessionsPage` | Unchanged |
| `/files` | ✅ | `FilesPage` | Unchanged |
| `/analytics` | ✅ | `AnalyticsPage` | Nav item gated by `show_token_analytics` config (pre-existing) |
| `/models` | ✅ | `ModelsPage` | Unchanged |
| `/logs` | ✅ | `LogsPage` | Unchanged |
| `/cron` | ✅ | `CronPage` | Unchanged |
| `/skills` | ✅ | `SkillsPage` | Unchanged |
| `/plugins` | ✅ | `PluginsPage` | Unchanged |
| `/mcp` | ✅ | `McpPage` | Unchanged |
| `/channels` | ✅ | `ChannelsPage` | Unchanged |
| `/webhooks` | ✅ | `WebhooksPage` | Unchanged |
| `/pairing` | ✅ | `PairingPage` | Unchanged |
| `/devices` | ✅ | `DevicesPage` | Unchanged |
| `/profiles` | ✅ | `ProfilesPage` | Unchanged |
| `/profiles/new` | ✅ | `ProfileBuilderPage` | Unchanged |
| `/config` | ✅ | `ConfigPage` | Unchanged |
| `/env` | ✅ | `EnvPage` | Unchanged |
| `/system` | ✅ | `SystemPage` | Unchanged |
| `/docs` | ✅ | `DocsPage` | Unchanged |
| Plugin routes | ✅ | `PluginPage` | `buildRoutes` logic preserved; overrides/addons/hidden routes intact |

**Verdict:** Every route from the spec is reachable. Deep links and bookmarks remain valid.

---

## 4. Navigation Integrity

### 4.1 Grouped Sidebar Sections

The implementation matches the spec's five intent buckets:

| Section | ID | Items | Spec Match |
|---|---|---|---|
| Home | *(standalone link)* | `/` | ✅ |
| MoonPie | `moonpie` | Chat, Sessions | ✅ |
| Knowledge | `knowledge` | Files, Skills, Plugins, MCP | ✅ |
| System | `system` | Analytics, Models, Cron, Logs, Profiles, Config, Keys, Diagnostics | ✅ |
| Network | `network` | Channels, Webhooks, Pairing, Devices | ✅ |
| Reference | `reference` | Documentation | ✅ |
| Plugins | `plugins` | Dynamic plugin items | ✅ (fallback section) |

### 4.2 Plugin Position Syntax

`parsePluginPosition` in `web/src/lib/navigation.ts` correctly handles:
- `"end"`
- `"after:<path>"` / `"before:<path>"`
- `"section:<sectionId>"`
- `"section:<sectionId>:after:<path>"`

**Verdict:** Navigation grouping is correct and plugin integration is backward-compatible.

---

## 5. Home / Cockpit Page

| Requirement | Status | Evidence |
|---|---|---|
| Renders at `/` | ✅ | `BUILTIN_ROUTES_CORE["/"] = HomePage` |
| `document.title = "Cockpit \| Hermes Agent"` | ✅ | `HomePage.tsx:128` |
| Metric tiles (4-up) | ✅ | Active Sessions, Gateway Status, Tokens Today, Cost Today |
| Recent Activity feed | ✅ | Fetches `/api/sessions?limit=6` |
| Quick Actions | ✅ | 6 actions: Chat, New Session, Logs, Restart, Skills, Config |
| System Health summary | ⚠️ | Shows memory; **mislabels CPU cores as "models configured"** |
| Cron & Schedules | ✅ | Next 3 upcoming jobs |
| Skills & Plugins strip | ✅ | Recent skills + plugins |
| 30-second polling | ✅ | `POLL_MS = 30_000` |
| Plugin slot `cockpit-metric-row` | ✅ | `HomePage.tsx:243` |
| Responsive grid | ⚠️ | Deviates at tablet breakpoint (see §7) |

---

## 6. Command Palette

| Requirement | Status | Evidence |
|---|---|---|
| `Cmd+K` / `Ctrl+K` trigger | ✅ | `App.tsx:372-376` |
| Mobile search icon trigger | ✅ | `App.tsx:432-440` |
| Sidebar footer search affordance | ✅ | `App.tsx:606-620` |
| Modal shell with backdrop | ✅ | `CommandPalette.tsx:237-245` |
| Escape / click-outside to close | ✅ | `CommandPalette.tsx:189-192, 241-243` |
| Searches Pages | ✅ | `ALL_BUILTIN_NAV_ITEMS` + plugin manifests |
| Searches Actions | ✅ | Restart, Update, Theme toggle |
| Searches Recent Sessions | ✅ | Fetches last 10 sessions on open |
| Arrow navigation | ✅ | `ArrowUp` / `ArrowDown` handlers |
| Enter to execute | ✅ | `Enter` handler |
| `Cmd+1` … `Cmd+9` shortcuts | ⚠️ | Implemented but only shown for first 9 **pages**, not across all result types |
| Grouped by scope with sticky headers | ❌ | Pages are grouped by **individual label**, not under a unified "Go to" header |
| Empty state fallback text | ❌ | Shows `"No results for '{query}'"` without `"press Enter to search sessions"` fallback |
| Focus trap | ❌ | `aria-modal="true"` present, but Tab does not cycle within the modal |
| Return focus on close | ❌ | Not implemented |
| `Cmd+Shift+R` / `Cmd+Shift+U` / `Cmd+Shift+T` | ❌ | Not implemented |
| Update action gated by `can_update_hermes` | ❌ | Always shown; should match sidebar gating |

**Verdict:** Core functionality works. Several UX polish items from the spec are missing.

---

## 7. Visual System Deviations

| Spec Requirement | Implementation | Location | Severity |
|---|---|---|---|
| Sidebar expanded: `w-60` (240px) | `w-64` (256px) | `App.tsx:471` | Minor |
| Sidebar collapsed: `w-12` (48px) | `lg:w-14` (56px) | `App.tsx:478` | Minor |
| Nav link padding: `px-4 py-2` | `px-5 py-2.5` | `App.tsx:910` | Minor |
| Nav link tracking: `tracking-[0.08em]` | `tracking-[0.12em]` | `App.tsx:911` | Minor |
| Active indicator: `w-[2px]` | `w-px` (1px) | `App.tsx:944` | Minor |
| Content padding: `px-4 sm:px-6 lg:px-8` | `px-3 sm:px-6` | `App.tsx:655` | Minor |
| Content max-width globally: `max-w-[1440px]` | Only on cockpit page | `HomePage.tsx:138` | Minor |
| Metric tiles mobile: 2×2 grid | `grid-cols-1` (1 column below 640px) | `HomePage.tsx:169` | Minor |
| Metric tiles tablet: 4 across | `grid-cols-2` (2 columns 640–1023px) | `HomePage.tsx:169` | Minor |

**Verdict:** None of these are functional regressions, but they cumulatively mean the implementation does not match the spec's spacing and responsive discipline.

---

## 8. Accessibility

| Requirement | Status | Evidence |
|---|---|---|
| `aria-expanded` on sections | ✅ | `App.tsx:818` |
| `aria-current="page"` on active nav | ✅ | React `NavLink` default |
| `role="dialog"` on palette | ✅ | `CommandPalette.tsx:244` |
| `aria-modal="true"` on palette | ✅ | `CommandPalette.tsx:239` |
| `aria-live="polite"` on result count | ✅ | `CommandPalette.tsx:267` |
| Metric tiles as `<article>` with `aria-label` | ✅ | `HomePage.tsx:258-264` |
| Keyboard navigation on tiles | ✅ | `tabIndex={0}` + `onKeyDown` for Enter |
| `aria-controls` on section toggle | ❌ | Missing |
| Live region for gateway status | ❌ | Gateway status tile lacks `aria-live` |
| Focus trap in palette | ❌ | Not implemented |
| `prefers-reduced-motion` | ⚠️ | Skeleton loaders use `animate-pulse`; no explicit reduced-motion gate |

---

## 9. Theme Compatibility

| Theme | Frontend Preset | Backend List | Status |
|---|---|---|---|
| `default` (Hermes Teal) | ✅ | ✅ | Baseline |
| `default-large` | ✅ | ✅ | ✅ |
| `nous-blue` | ✅ | ✅ | ✅ |
| `moonpie` | ✅ | ✅ | ✅ |
| `midnight` | ✅ | ✅ | ✅ |
| `ember` | ✅ | ✅ | ✅ |
| `mono` | ✅ | ✅ | ✅ |
| `cyberpunk` | ✅ | ✅ | ✅ |
| `rose` | ✅ | ✅ | ✅ |

**Verdict:** MoonPie theme correctly added to both frontend (`web/src/themes/presets.ts`) and backend (`hermes_cli/web_server_dashboard.py`).

---

## 10. Existing Capabilities Preservation

| Capability | Status | Evidence |
|---|---|---|
| Chat persistence (embedded) | ✅ | Persistent `ChatPage` host outside `<Routes>`; `chatHostMounted` state |
| Profile switching | ✅ | `ProfileSwitcher` + `ProfileKeyedRoutes` |
| System actions (restart/update) | ✅ | `SidebarSystemActions` with confirm dialogs |
| Theme switching | ✅ | `ThemeSwitcher` in sidebar footer + palette action |
| Language switching | ✅ | `LanguageSwitcher` in sidebar footer |
| Plugin tabs / overrides | ✅ | `buildRoutes` handles `tab.override` and `tab.hidden` |
| Plugin slots | ✅ | All existing slots (`backdrop`, `header-banner`, etc.) preserved |

---

## 11. Build & Test Verification

| Check | Result |
|---|---|
| `npm run build` | ✅ Pass (7.69s) |
| `npm run test` | ✅ 354 passed, 50 files |
| `tsc --noEmit` | ✅ Pass (0 errors) |
| `npm run lint` | ⚠️ 2 pre-existing errors, 31 warnings (in unmodified files) |

**No new lint errors or test failures were introduced by the redesign.**

---

## 12. Critical Findings (Must Fix)

### 12.1 🐛 SystemHealthCard Mislabels CPU Cores as "Models Configured"

**File:** `web/src/pages/HomePage.tsx:601-602`  
**Issue:** The System Health tile displays `stats?.cpu_count` with the label "configured", implying it shows the number of configured AI models. `cpu_count` is the number of CPU cores on the host machine.  
**Impact:** Misleading telemetry on the primary dashboard surface.  
**Fix:** Either:
- Change the label to "CPU cores" (accurate for the data), or
- Fetch the actual model count from `/api/models` and display that.

---

## 13. Recommended Improvements (Should Fix)

1. **Command palette page grouping** — Group all page results under a single "Go to" sticky header instead of one header per page label.
2. **Command palette missing shortcuts** — Add `Cmd+Shift+R` (restart), `Cmd+Shift+U` (update), `Cmd+Shift+T` (cycle theme).
3. **Command palette focus trap** — Trap Tab focus within the modal while open.
4. **Command palette `can_update_hermes` gating** — Hide the Update action when the user lacks permission.
5. **Responsive grid fix** — Use `grid-cols-2` from 768px and `grid-cols-4` from 1024px for metric tiles to match spec §9.3.
6. **Visual spacing alignment** — Adjust sidebar width, nav padding, and content padding to match spec §5.1.
7. **SystemHealthCard disk usage** — Add the disk usage bar from `SystemStats.disk` (already available in the API response).
8. **Accessibility enhancements** — Add `aria-controls` to section toggles, `aria-live` to gateway status tile, and `prefers-reduced-motion` gates.

---

## 14. Assumptions & Risks

- **Assumption:** The backend API endpoints consumed by the cockpit (`/api/status`, `/api/analytics`, `/api/sessions`, `/api/cron`, `/api/skills`, `/api/platforms`, `/api/system`, `/api/logs`) return data shaped as expected in production. The `Promise.allSettled` pattern gracefully handles individual endpoint failures.
- **Risk:** The cockpit polls every 30 seconds. On a slow network or large session history, this could produce noticeable load. No backoff or cancellation logic exists beyond the component unmount.
- **Risk:** The command palette is eagerly imported in `App.tsx`, not dynamically loaded. The spec recommends `React.lazy` for bundle-size mitigation.

---

## 15. Open Questions

1. Should the "Models configured" metric be removed entirely from the System Health card, or should the Models page API be queried for an accurate count?
2. The spec mentions a `cockpit-sidebar` plugin slot (future). Is this intentionally deferred?
3. The spec's "Approvals inbox" and "Kanban exposure" open questions (§14) remain unaddressed — out of scope for this redesign, but should be tracked for future work.

---

## 16. Final Recommendation

**Disposition: ACCEPTED WITH CONDITIONS**

The implementation is safe to merge **after** fixing the critical bug in `SystemHealthCard` (CPU cores mislabeled as models). The visual deviations and missing command-polish items are non-blocking but should be ticketed for a follow-up pass.

| Item | Blocker? | Owner |
|---|---|---|
| Fix `cpu_count` → "CPU cores" label in SystemHealthCard | **YES** | Neo |
| Command palette grouping + shortcuts + focus trap | No | Neo (follow-up) |
| Visual spacing alignment (sidebar width, padding) | No | Neo (follow-up) |
| Responsive grid breakpoint fix | No | Neo (follow-up) |
| Accessibility enhancements | No | Neo (follow-up) |

---

*Report generated by Tsebo (QA/Verification profile) on task t_d69b62f0.*
