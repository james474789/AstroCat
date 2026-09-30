# E1 — Compact Equipment rig list

Status: **Proposed** · Written: 2026-09-30 · Base commit: `40d5e58` (`main`) · Size: M · Frontend only

## 1. Problem

Equipment > Rigs renders every rig as a tall, always-expanded card (`RigCard`, `frontend/src/pages/Equipment.jsx:132`). Each card shows about 8–12 rows: name, Mounted pill, Active checkbox, camera · optic · modifier, scale/FOV/f-ratio, filter chips, sampling and scale-check badges, delivered FWHM, a subs link, and four buttons. With many rigs the page is a wall of near-identical cards.

Specific redundancy:
- Each card has an **Assign images** button, but its handler (`handleAssignImages`, `triggerEquipmentAssign('unassigned')`) is global and ignores the rig. It duplicates the tab-bar "Assign images now" button N times.
- **View images** duplicates the "N subs" link (both use `rigSearchLink`).
- The **Add rig** tile is an extra grid item that moves as rigs are added.

## 2. Goals / Non-goals

**Goals**
1. One scannable line per rig, showing only what is needed to tell rigs apart and to see their status.
2. Detail on demand (accordion), not always visible.
3. Find rigs quickly: search plus status filters.
4. Remove the misleading per-rig "Assign images" button.

**Non-goals**
- No backend or API change (`GET /api/equipment` already returns everything).
- No change to the Cameras, Optics, Filters and Sites tabs, the modals, or `UnassignedImagesSection`.

## 3. Design

### 3.1 Toolbar (above the list)
- Search box: matches rig name, camera name, optic name.
- Filter chips: **All · Mounted · Active · Inactive**, with counts.
- Existing `.mount-summary` line ("N of M rigs mounted…").
- **Add rig** button (replaces the grid tile).

### 3.2 Collapsed row (`RigRow`)
One line, in order: expand chevron · name · `camera · optic (+modifier)` · scale ″/px · delivered FWHM (existing `QualityValue`) · sub count (link to `rigSearchLink`) · Mounted pill toggle · Active checkbox · kebab menu (Edit, Delete, View images).

Sort order: mounted first, then active, then name. Inactive rows keep the existing dimmed style (`opacity: 0.6`).

### 3.3 Expanded panel
Single-open accordion (`expandedRigId` state). Shows: FOV, f/ratio, filter chips, sampling badge, scale-check badge, best FWHM, last used, and a "View images" link. It reuses the existing chip/badge markup.

### 3.4 Narrow screens
Below the mobile breakpoint (see the recent `feat/mobile-responsive` work) the row keeps only name, FWHM and the controls. The rest moves into the expanded panel.

### 3.5 Removed
- Per-rig **Assign images** (the tab-bar button stays and is the only one).
- Per-rig **View images** button (kept in the kebab and in the subs count link).
- The dashed "Add rig" grid tile.

## 4. Files
- `frontend/src/pages/Equipment.jsx`: new `RigRow`, a `RigsToolbar` (or inline), and updated Rigs tab render. `RigCard` is removed once unused.
- `frontend/src/pages/Equipment.css`: replace `.rig-card` rules with row/accordion/toolbar styles, using the existing CSS variables.

## 5. Acceptance
- With 20+ rigs the list fits on roughly two screens at desktop width, and each collapsed row is one line.
- Search and the filter chips narrow the list; the counts on the chips are correct.
- Mount toggling still respects `max_mounted_rigs`. Active toggle, Edit and Delete behave as before.
- No "Assign images" button on rows. The tab-bar button still works.
- `npm run lint` and `npm run build` pass.
