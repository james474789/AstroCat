# U1: UI/UX design system and information architecture (handover)

## P1a status (2026-10-09): done, uncommitted, deployed as VERSION 20261009.05

**Primitives** in `frontend/src/components/ui/` (README there has props, migration map, conventions): `Button`, `Dialog` (native `<dialog>`, portalled), `ConfirmDialog` + `useConfirm()`, `ToastProvider` + `useToast()` (Undo via `action`), `Tabs`/`TabPanel`, `SegmentedControl`, `PageHeader`, `EmptyState`, `Spinner`, `Skeleton`. Providers are mounted in `App.jsx`.

**Migrated:** Admin (+Settings.css, StarQualityAdmin), Search (+FolderTree), Equipment, Tonight (+PlanetarySeeingPanel), Catalogs, Targets, MetadataViewer/MetadataSearch (+metadata tabs), Dashboard, FITSExplore, FitsStats, ImageDetail, TargetDetail, NightReport, Login/Setup, FullResViewer. All `confirm()`/`alert()` are gone except Search's bulk-edit flows, which kept their logic but now render in `<Dialog>` (P1b redesigns them). Every page stylesheet is scoped under `.page-<name>` (dialog content under `.dlg-<name>`); `npm run lint` now runs stylelint, then `npm run lint:scope` (`frontend/scripts/check-css-scope.mjs`, fails on unscoped selectors in `src/pages/*.css`), then eslint. `.page-header` leak is gone; `.text-success/.text-error/.muted/.loading-state` moved to `index.css`.

**Notes for next stages:**
- Dialogs are in the top layer, so toasts raised while a dialog is open are hidden behind it; pages close the dialog first or show inline messages.
- ImageDetail's keyboard shortcuts are suppressed while a `dialog[open]` exists.
- Known leftovers: `transition: all` still in `index.css` (6) and a few component CSS files (P2); local keyframes remain in BottomSheet/FilterChips/FilterSection/Quality/Admin/Settings (`admin-pulse*`: a shared `ui-pulse` would remove them); `ConfirmDialog` has no `className`/rich-description option (Admin's mount-point steps render a default `<ol>`); PageHeader has no way to hide the subtitle on phones (Search lost that rule).
- Same pre-existing eslint errors as before (32 errors / 7 warnings); no new ones.

## P0 status (2026-10-09): done, uncommitted, deployed as VERSION 20261009.04

Base was `c3cf52c` (the sky-overlay edits mentioned below were already committed there; the tree held only the docs).

**Done:** 12 alias tokens + `--color-fill`/`-hover`, `--color-info`, `--color-warning-strong`, `--color-black`, semantic layer, type-scale tokens (`--fs-*`, `--fw-*`) and `.text-*`/`.tabular-nums` classes; `--color-text-muted #8a98ad`, error `#f87171`; h1 is now Large Title (34/700). Removed: `.btn-primary` gradient/glow/lift, `.card` hover shadow, `.stat-card` gradient bar, starfield (both copies, incl. keyframes), gradient-clipped headings (Admin, FITSExplore, FitsStats, MetadataSearch), hover lifts (ImageCard, Catalogs, FitsStats, Admin pipeline cards). Login/Setup (`Login.css`), App.jsx loading/error/Suspense screens (now `.app-screen*` / `.app-error-card` in index.css) and Admin.css are on tokens. Dead Tailwind classes removed from Admin.jsx (semantic `bulk-*`/`queue-*` rules added to Admin.css). Emoji/glyph icons replaced by Lucide across pages and components. `utils/chartColors.js` created. Stylelint (`color-no-hex`) added; `npm run lint` now runs stylelint then eslint.

**Left for later stages:**
- `npm run lint` still fails in eslint on 33 errors / 7 warnings that exist on `c3cf52c` too (e.g. `__APP_VERSION__`, `process` no-undef, unused vars). Not caused by P0.
- Stylelint temporary allowlist (`.stylelintrc.json` overrides): `skyOverlay/SkyOverlay.css`, `pages/Dashboard.css` (intentional palettes); migrate in P1/P2. `utils/filterColors.js` holds hex by design.
- Not touched (P1/P2): V4 class collisions (`.page-header`, `.modal-*`, etc.), 38 `transition: all`, Layout's hand-drawn SVG nav icons, Admin's two `<h1>`, the fake 57% storage bar (V10), h2-h6 sizes, Tailwind-ish names in ImageDetail/Search, reduced motion/focus rings.
- Judgement calls to review: `→` range separators and link arrows became `ArrowRight`; Catalogs object-type icon mapping; StarQualityAdmin warning colour moved `#fbbf24` to `--color-warning`; rating `<option>` text lost the star glyph.


Status: **Proposed, not started** · Written: 2026-10-09 · Author: Claude (design review session) · For: the agent or developer picking up U1

Base: `main` at **`f8c41ab`**. The working tree also has uncommitted, unrelated A1 sky-overlay edits (`VERSION`, `backend/app/api/images.py`, `backend/app/services/sky_overlay.py`, `backend/tests/test_sky_overlay.py`, `frontend/src/hooks/useSkyOverlay.js`). Don't fold those into U1 commits.

Visual companion (before/after mockups, contrast table, type specimen): **https://claude.ai/artifact/7UgQrXFAzkPAFDF7t5dvZj**. It's private to the owner; this doc is the full written record.

## 0. Read in this order

1. **This file.**
2. `CLAUDE.md`, especially:
   - ask until 95% confident;
   - never commit without explicit instruction;
   - rebuild the frontend container after any `frontend/` change;
   - bump `VERSION` (`YYYYMMDD.NN`) before rebuilding.
3. [README.md](README.md) §3–§5 (merge hotspots, conventions).
4. `frontend/src/index.css` (the token file) and `frontend/src/components/layout/Layout.jsx` + `Layout.css` (the shell).

Owner preferences (from memory):
- The owner does the browser/UI checks. You stop at lint, build and container rebuild.
- Every plan must include an effort + token estimate and a recommended Claude model for each stage.

## 1. Why

AstroCat has the feature depth of a pro tool, but every feature (R0, R1, F1, Q1, …) shipped its own colours, modals, tabs, toasts and headers. The tokens in `index.css` are sound but unenforced. The UI chrome (gradients, glow, starfield, hover lifts) competes with the astrophotographs.

**Goal:** make AstroCat feel like one calm, consistent instrument. Mostly that means *unifying* what exists, not redesigning it:
- one token layer;
- one set of shared primitives;
- a grouped navigation;
- a selection model for bulk edits.

**Owner's priorities:** (1) visual system, (2) information architecture. Workflows and accessibility come second.

**Design principles** (judge every change against these):
- **Content first.** Images are the hero, so chrome recedes.
- **Clarity.** One type scale, AA contrast, one primary action per screen.
- **Consistency.** A modal, tab or toast looks and behaves the same everywhere.
- **Deference.** Motion only when it explains something, and never under reduced motion.

## 2. Findings (evidence)

All paths are relative to `frontend/src/`. The audit read the whole frontend (~24.9k lines). The live app was not viewed.

### 2.1 Visual system

| # | Finding | Evidence |
|---|---|---|
| V1 | **Three dark palettes.** App tokens (`#0a0e17`/`#141b2d`), GitHub dark (`#0d1117`/`#161b22`/`#30363d`), Tailwind slate (`#0f172a`/`#111827`/`#e0e6ed`) | `index.css`; `pages/Login.css` (also used by Setup) + `App.jsx:44-85,105` loading/error/Suspense screens; `pages/Admin.css` (0 `var()` uses, 22 hex, 27 rgba) |
| V2 | **Dead Tailwind.** About 87 Tailwind-style class names in Admin (plus ~7 in ImageDetail, 2 in Search). Tailwind isn't installed, so they do nothing | `pages/Admin.jsx` e.g. `:610`, `:763`, `:1321` |
| V3 | **14 undefined tokens** that fall back silently | `--border-radius-md` (7 files: FilterChips, FilterSection, MetadataSearch, Settings, …), `--color-surface-alt` (FilterSection, Search, Targets), `--radius-md` (Settings, Tonight), `--shadow-xl` (Layout, Search), `--transition-normal` (Layout), `--color-border-hover` (FilterSection), `--text-primary/-secondary/-tertiary`, `--accent-primary` (FITSExplore.css, FolderTree.css), `--border` (FolderTree.css), `--color-bg-secondary` (Search.jsx bulk modal). `--sky-*` and `--inv-zoom` are set locally and are fine |
| V4 | **Global class collisions** (every page is imported eagerly, so all page CSS is global) | `.page-header`/`.page-title` (used by 9 pages) are only styled in `pages/MetadataSearch.css:10` and `pages/MetadataViewer.css:52`. `.modal-*` is in both `Admin.css:244` and `FITSExplore.css:185`. `.pagination` ×5 files, `.filter-group` ×3, `.results-header` ×2, `.section-title` ×3, `.chart-card` ×2, `.stat-card` in index.css and FitsStats |
| V5 | **Four icon languages**: Lucide, hand-drawn SVGs, emoji, Unicode glyphs | Custom SVGs in `Layout.jsx:13-68`. Emoji on Dashboard stat cards (`Dashboard.jsx:171-189`), calibration rows (`:296-299`), Search filter sections and header buttons (`Search.jsx:521-562,586`), ImageCard (`🌌 ⏱ 📷`), empty states (`🔭`). Glyphs `⋯ ⟳ ✦ ⬇` |
| V6 | **Duplicated primitives** | 7 modal implementations (Admin ×2 inline `:822/:843`, Search ×3 inline `:1106-1230,~1319`, Equipment `ModalShell` `:121`, Tonight `.tonight-modal-*`, Admin/FITSExplore `.modal-*`). 3 toasts (Admin inline `:815`, `.equip-toast` `Equipment.jsx:1545`, `.tonight-toast` with Undo `Tonight.jsx:293`). 3 tab styles (`.catalog-tab`, `.equip-tab`, `.tab-button`); the global `.tab` is unused. ~17 one-off button classes (`fr-btn`, `nav-btn`, `action-btn`, `copy-btn`, …). 4 spinners; `@keyframes spin` declared 3×, `fadeIn`/`slideDown` 2× each |
| V7 | **Ornament** | `.btn-primary` gradient + glow + `translateY` hover (`index.css:169-181`). Gradient-clipped headings (MetadataSearch `.page-header`, Admin title). `.starfield` twinkle forever (`index.css:576`), rendered twice on Stats (`Layout.jsx:120` + `FitsStats.jsx:357`). Hover lifts on `.card`, ImageCard, FolderTree, Admin, Catalogs, FitsStats. 37 × `transition: all` |
| V8 | **Typography unused / no tabular figures** | Literal font sizes: Equipment.css 23, Quality.css 23, Tonight.css 20. h1 = 40px everywhere. Admin has two `<h1>` (`:603`, `:906`) |
| V9 | **Contrast fails** (WCAG, computed) | `--color-text-muted #64748b` (~140 uses) on bg/surface/elevated/hover = 4.06 / 3.60 / 3.27 / 2.89, failing everywhere. White on `--color-primary #5b8dee` = 3.24, so every `.btn-primary` label fails. `--color-error #ef4444` on elevated/hover = 4.14 / 3.66 |
| V10 | **Fake number** | Dashboard "Storage Used" bar is hardcoded `width: '57%'` (`pages/Dashboard.jsx:353`) |

### 2.2 Information architecture

| # | Finding | Evidence |
|---|---|---|
| I1 | **Flat sidebar:** 10 items with no grouping; Logout sits in the list | `Layout.jsx:75-90,158-161` |
| I2 | **Search and Metadata Search split one job.** Different filter panels; grid of 100/page vs table of 15/page | `pages/Search.jsx` (1380 lines), `pages/MetadataSearch.jsx` (547 lines, 9 `filter-group`s with emoji) |
| I3 | **Bulk edits hit all results.** The header has 6 equal buttons. Change type / Set frame type / Assign rig rewrite *every matching image*, with no selection and no undo | `Search.jsx:515-570` |
| I4 | **Admin is one scroll of ~12 sections:** pipeline, health, settings header, indexer, plate solving, mounts, thumbnails, StarQualityAdmin, data maintenance, users, about. "+ Add Mount Point" is an `alert()` with instructions | `pages/Admin.jsx` (1392 lines), `:1042` |
| I5 | **An image lives in three places,** each with its own toolbar language | `ImageDetail.jsx` (`nav-btn`, toolbar `:698-780`), `MetadataViewer.jsx` (4 `tab-button` tabs), `FullResViewer.jsx` (`fr-btn`) |
| I6 | **Dashboard answers too many questions:** Tonight, last night, 4 stat cards, monthly chart, top objects, calibration, recent images, progress bars. A load failure only goes to `console.error` (`:141`) | `pages/Dashboard.jsx` |
| I7 | **33 `alert`/`confirm` calls.** Only BottomSheet is a proper `role="dialog"` + `aria-modal`. No focus trap or Esc handling elsewhere | Admin 16, ImageDetail 8, Equipment 5 (`:1463-1503`), FolderTree 2, StarQualityAdmin 1, MetadataSearch 1 |

### 2.3 Accessibility and polish (secondary)

- **Focus rings:** `:focus-visible` appears once (`components/quality/Quality.css:36`). Buttons, links, tabs and cards have no focus ring. `outline: none` appears in 9 places.
- **Motion and theming:** no `prefers-reduced-motion` and no `prefers-color-scheme` anywhere. The app is dark-only, and that's fine to keep.
- **Icon-only buttons** with no `aria-label`:
  - ImageDetail ←/→ (`:545-563`);
  - FullResViewer `fr-btn icon` (`:500,:504,:553`);
  - Targets ↑/↓ (`:200`);
  - Equipment modal × (`:127`), which has no label *or* title.
- **Unassociated labels and click-only elements:**
  - Search's "Sort by:" `<label>` isn't associated with its select.
  - The size range input has only a `title`.
  - About 22 `div`/`span`/`tr` elements have `onClick` handlers.
- **Pagination** is Previous/Next only (Search, Catalogs, Targets) across about 48k images.

## 3. Target design

### 3.1 Tokens (P0)

Keep the existing names. **Define every missing token** by aliasing it to an existing one; don't rename call sites in P0:

```css
--border-radius-md: var(--border-radius);      /* 8px */
--radius-md: var(--border-radius);
--color-surface-alt: var(--color-surface-elevated);
--color-border-hover: var(--color-border-light);
--shadow-xl: 0 20px 40px rgba(0,0,0,.45);
--transition-normal: var(--transition-base);
--text-primary: var(--color-text-primary);
--text-secondary: var(--color-text-secondary);
--text-tertiary: var(--color-text-muted);
--accent-primary: var(--color-primary);
--border: var(--color-border);
--color-bg-secondary: var(--color-surface-elevated);
```

**Colour changes** (contrast verified):

| Token | Now | New | Result |
|---|---|---|---|
| `--color-text-muted` | `#64748b` | **`#8a98ad`** | 6.59 / 5.86 / 5.32 / 4.70 on bg/surface/elevated/hover, AA everywhere |
| new `--color-fill` (primary button bg) | gradient | **`#3a6bd1`** | white text 5.0:1 |
| `--color-error` (text use) | `#ef4444` | **`#f87171`** | 5.63 / 4.98 |

Keep `--color-primary #5b8dee` for links, accents and tints.

**Add a small semantic layer:**
- `--fill-tint: rgba(91,141,238,.16)` and `--fill-tint-text: #a9c3f7`;
- `--separator` = border;
- `--material-thick: rgba(10,14,23,.66)` with `backdrop-filter: blur(10px)`.

**Type scale** (add as tokens, plus `.text-*` utility classes; keep Inter):

| Role | Size / weight |
|---|---|
| Large Title (page h1) | 34 / 700, letter-spacing −0.02em |
| Title 1 | 22 / 600 |
| Title 2 | 20 / 600 |
| Headline | 17 / 600 |
| Body | 15 / 400 |
| Callout | 14 / 400 |
| Subhead | 13 / 500 |
| Footnote | 12 / 400 |
| Caption | 11 / 500 |

Put `font-variant-numeric: tabular-nums` on tables, stats, counts and metadata values.

**Retire:**
- the gradient and glow on `.btn-primary`;
- gradient-clipped heading text;
- the hover `translateY` on cards;
- the always-on starfield. Make it static, or remove it and its duplicate in FitsStats.

**Guardrail:** add `stylelint` with `color-no-hex` (or `declaration-strict-value` for colours) for everything except `index.css` (or a new `styles/tokens.css`). Chart colours in JSX (`FitsStats.jsx` has 42 hex) move into one `utils/chartColors.js`, next to the existing `filterColors.js` and `components/quality/chartStyle.js`.

### 3.2 Shared primitives (P1): `frontend/src/components/ui/`

| Component | Replaces | Notes |
|---|---|---|
| `Button` (`variant`: filled, tinted, plain, destructive; `size`: sm, md) | `.btn-*` plus ~17 one-offs | Keep the `.btn` class names as the implementation, so old markup still works during migration |
| `Dialog` | 7 modals plus `confirm()` | Built on native `<dialog>` (`showModal()`): focus trap, Esc, backdrop. Props: title, description, confirmLabel (must name the action and count, never "OK"), destructive, onConfirm. Async-confirm helper `useConfirm()` |
| `Toast` + `useToast()` | Admin/Equipment/Tonight toasts plus `alert()` | Optional `action` (Undo). Base it on Tonight's toast, which already does Undo well |
| `SegmentedControl` / `Tabs` | `.catalog-tab`, `.equip-tab`, `.tab-button` | `role="tablist"`/`role="tab"`, arrow-key navigation |
| `PageHeader` | `.page-header` (owned by MetadataSearch.css), `admin-header`, `stats-header`, `night-header`, … | Large title, optional subtitle/count, toolbar slot |
| `EmptyState`, `Spinner`, `Skeleton` | many variants | Lucide icon, not emoji |

Then **scope page CSS** (CSS modules or a strict `.page-<name> ` prefix) so no page stylesheet can restyle another. Fix V4 collisions first, because moving `.page-header` out of MetadataSearch.css will visibly change 9 pages.

### 3.3 Information architecture (P1)

**Sidebar groups** (`Layout.jsx` `navItems` gets a `group` field):

| Group | Items |
|---|---|
| (top) | Home (was Dashboard) |
| Library | Images (was Search), Targets, Catalogs |
| Observe | Tonight, Nights |
| Insights | Statistics |
| Setup | Equipment, Admin |

- The footer becomes an account menu: user email, Star sizes units toggle, version, Log out.
- The mobile tab bar becomes Home · Images · Tonight · Targets · More; the More sheet is grouped the same way.
- **Keep the existing routes** (`/search`, `/metadata-search`). Only labels change, plus a redirect if a page is merged.

**Merge Metadata Search into Images** (`/search`):
- Add a "Header fields" `FilterSection` in the Search filter panel, which takes MetadataSearch's FITS-keyword filters.
- Add a Grid | List `SegmentedControl`. List is a table view (columns from MetadataSearch's results table).
- Then make `/metadata-search` redirect to `/search?view=list`.
- **Check first** whether the two pages hit different backend endpoints (`frontend/src/api/client.js`). If they do, the list view keeps calling the metadata endpoint, or the filters are unified server-side. That decision needs the owner.

**Selection model for bulk edits** (Search):
- Toolbar: search field · Filters (tinted, with count) · Grid|List · Sort menu (field + direction in one control, e.g. "Captured ↓") · Select · ⋯ (Export CSV, Sync metadata).
- In Select mode, clicking a card toggles selection, and a floating action bar shows: *N selected* · Type · Frame · Rig · Export · Done.
- "Select all N results" remains, but its Dialog must state the count and the filters.
- After any bulk edit, show an Undo toast. **Undo needs backend support** (record the previous values, or a bulk endpoint that takes the IDs plus old values). Confirm the scope with the owner. If Undo isn't feasible, the confirm Dialog is mandatory.

**Admin split view:**
- An inner sidebar (or `SegmentedControl` on mobile): Pipeline · Health · Indexer · Plate Solving · Mounts · Thumbnails · Star Quality · Data · Users · About.
- One `<h1>`.
- Replace the `alert()` behind "+ Add Mount Point" with a Dialog that explains how to add a mount.

**Image Inspector:**
- MetadataViewer's 4 tabs (Summary, Details, Raw, Export) move into a collapsible right-hand Inspector panel on ImageDetail, toggled with ⌘I / `I`. The existing shortcuts are 0-5, ←/→, G, F, O, so check `I` is free.
- `/images/:id/metadata` keeps working (it opens ImageDetail with the Inspector open).
- FullResViewer's top bar uses the same toolbar `Button` (plain, icon) as ImageDetail.

**Home (was Dashboard):**
- Keep: Tonight tile, Last night tile, Recent images, a compact stats row.
- Move the Monthly Activity chart and Top Objects to Statistics, and keep the Calibration Library as a Library filter shortcut.
- Replace the fake 57% storage bar with the real value, or remove the bar.
- Show an error state instead of only logging to the console.

**Image card** (`components/images/ImageCard.jsx`):
- The thumbnail runs edge to edge, with no card border or shadow and no lift.
- Title = the first catalog match (`catalog_matches[0]`), falling back to the file name. The full file name goes in a tooltip.
- One subhead line: `300 s · Ha · ASI2600MM · 2.14″ FWHM`. Use the camera model, not `camera.split(' ')[0]`.
- Status as small pills on a blurred material: a green dot plus "Solved" (still hidden for PLANETARY/ALLSKY/AURORA), plus the subtype.

### 3.4 Polish (P2)

- A global `:focus-visible` ring (2px `--color-primary`, offset 2px).
- `@media (prefers-reduced-motion: reduce)` disables animations and transitions.
- Replace `transition: all` with specific properties.
- Add `aria-label` to every icon-only button and associate labels with their controls.
- Make clickable `div`s and `tr`s into buttons or links.
- Numbered pagination with a jump field.

## 4. Stages, models, estimates

Each stage ships on its own: bump `VERSION`, run `npm run lint && npm run build`, then `docker compose build frontend && docker compose up -d frontend`, then hand to the owner for the browser check. Never leave the app half-migrated across a release.

| Stage | Scope | Recommended model | Why | Effort | Tokens |
|---|---|---|---|---|---|
| **P0 Foundation** | §3.1 tokens + aliases, contrast, type scale, retire ornament, Login/Setup/App.jsx/Admin onto tokens, delete dead Tailwind classes, Lucide-only icons, stylelint | **Opus 5.5** decides tokens and type, then **Sonnet 5.5** does the bulk edits | Once the rules are fixed, the CSS edits follow a pattern | 1–2 days | 300–500k |
| **P1a Primitives** | §3.2 components, then migrate Admin, Search, Equipment, Tonight, Catalogs, MetadataViewer; scope page CSS | **Opus 5.5** builds the primitives; **Sonnet 5.5** migrates each page (parallel worktrees are OK, since the pages don't share files) | The APIs set the pattern for everything after | 3–5 days | 0.8–1.2M |
| **P1b Structure** | §3.3 nav groups, Metadata Search merge, selection + action bar (+ Undo), Admin split, Inspector, Home refocus, ImageCard | **Opus 5.5**, with **Haiku 5.5** for initial code mapping | IA and state (selection, undo, merged search). Do these one at a time, because all touch `Search.jsx`/`Layout.jsx` | 4–6 days | 1.0–1.5M |
| **P2 Polish** | §3.4 | **Sonnet 5.5** | A checklist of small fixes | 1 day | ~200k |
| **Total** | | | | **9–14 days** | **2.3–3.4M** |

**Overrun risk:**
- **Medium on P1b.** It depends on the Undo backend work and on whether the metadata search endpoint differs.
- **Low on P0 and P2.**

**Merge hotspots:** `Search.jsx` (1380 lines), `Admin.jsx` (1392), `Equipment.jsx` (1759), `index.css`, `Layout.jsx`.

## 5. Open questions for the owner (ask before building)

1. **Undo for bulk edits:** build backend support (stores previous values), or settle for a confirm Dialog only?
2. **Metadata Search merge:** OK to retire the separate page (with a redirect), and to change the list view to 100/page?
3. **Starfield:** remove it, or keep it static?
4. **Nav naming:** "Images" instead of "Search", and "Home" instead of "Dashboard"?
5. **CSS scoping:** CSS modules (larger diff, stronger guarantee) or prefix convention?
6. **Light mode:** out of scope? (Assumed yes; the app stays dark-only.)

## 6. Verification for each stage

- `cd frontend && npm run lint && npm run build` passes. Once stylelint is added (P0), it passes too.
- `grep` checks that leftovers are gone:
  - after P0: no hex outside the token/chart-colour files, no `var(--…)` for undefined tokens (re-run the undefined-token check in §2.1 V3), no Tailwind classes in Admin;
  - after P1a: no `window.confirm|alert`;
  - after P2: no `transition: all`.
- Rebuild the frontend container after bumping `VERSION`. The owner then checks the pages in the browser; don't run browser tests yourself.
- Report what changed per page so the owner knows what to look at.
