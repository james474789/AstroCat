# components/ui: shared primitives (U1 P1)

Import from the barrel: `import { Button, Dialog, useConfirm, useToast } from '../components/ui';`

All styles live in `ui.css`: tokens only (no hex, no `transition: all`), every class prefixed `ui-`, reduced-motion handled there. The only keyframes pages should use are `ui-spin`, `ui-fade-in`, `ui-slide-up` (plus `ui-shimmer` for skeletons). `ToastProvider` and `ConfirmProvider` are mounted once in `App.jsx`.

## Components

**Button** `variant` `'filled' | 'tinted' | 'plain' | 'destructive'` (default `tinted`), `size` `'sm' | 'md'`, `icon` (Lucide element, e.g. `<Plus size={16} />`), `iconOnly` (needs `aria-label`; dev warning otherwise; the label doubles as `title`), `loading` (spinner + disabled + `aria-busy`), `as` (any component/tag), `to` (renders a react-router `Link`), `type` (defaults to `button`), `disabled`, `className`, plus any DOM props. Forwards `ref`.
```jsx
<Button variant="filled" icon={<Save size={16} />} loading={saving} onClick={save}>Save rig</Button>
<Button variant="plain" iconOnly icon={<MoreHorizontal size={18} />} aria-label="More actions" />
<Button to="/search" variant="tinted">Open in Search</Button>
```

**Dialog** `open`, `onClose`, `title` (required, becomes `aria-labelledby`), `description` (`aria-describedby`), `children` (scrolling body), `footer` (buttons), `size` `'sm' | 'md' | 'lg'`, `destructive` (warning icon), `dismissible` (Esc + close button, default true), `closeOnBackdrop` (default true), `className`. Native `<dialog>` + `showModal()`: focus trap, inert background, focus returned to the opener, body scroll locked, bottom sheet under 640px. Initial focus is the first focusable element in body/footer, so put Cancel before the primary action.

**ConfirmDialog** (controlled) `open`, `title`, `description`, `children`, `confirmLabel`, `cancelLabel` (`'Cancel'`), `destructive`, `hideCancel`, `onConfirm`, `onCancel`.

**useConfirm()** returns `confirm(options) => Promise<boolean>`; options are the ConfirmDialog props above. `confirmLabel` must name the action and its count ("Delete 3 images", "Reset 12 solves"), never "OK"; it falls back to "Confirm" (or "Close" with `hideCancel`) and warns in dev. Use `hideCancel: true` for an info-only notice (replaces `alert()` when a toast is too easy to miss).
```jsx
const confirm = useConfirm();
if (!(await confirm({ title: `Delete ${n} images?`, description: 'Files on disk are not touched.', confirmLabel: `Delete ${n} images`, destructive: true }))) return;
```

**useToast()** returns `{ show, success, error, info, dismiss }`. `show({ message, type: 'success' | 'error' | 'info', durationMs = 6000 (0 = sticky), action: { label, onClick } })` returns an id for `dismiss(id)`; `success/error/info(message, opts)` are shorthands. Errors use `role="alert"`, others `role="status"`. Hovering or focusing a toast pauses its timer. Max 4 visible.
```jsx
const toast = useToast();
toast.success('Rig saved');
toast.info(`Hidden ${name}`, { action: { label: 'Undo', onClick: undo } });
```
Toasts sit under an open Dialog (the dialog is in the top layer and makes the page inert); show them after the dialog closes.

**Tabs** `items` `[{ value, label, icon, count, disabled, ariaLabel }]`, `value`, `onChange(value)`, `panelIdPrefix`, `aria-label`, `className`. Roving tabindex; Arrow keys / Home / End move and select. **TabPanel** `panelIdPrefix`, `value`, `children` links back to its tab.
```jsx
<Tabs aria-label="Equipment" items={tabs} value={tab} onChange={setTab} panelIdPrefix="equip" />
<TabPanel panelIdPrefix="equip" value={tab}>...</TabPanel>
```

**SegmentedControl** same props as Tabs plus `size` `'sm' | 'md'`; a pill-shaped `radiogroup` for 2-4 view options (Grid | List). For icon-only segments give the item an `ariaLabel`.

**PageHeader** `title` (the page's only `<h1>`, Large Title), `subtitle`, `count`, `icon` (Lucide element), `actions` (toolbar slot, wraps on narrow screens), `children` (filters etc. under the title row), `className`.

**EmptyState** `icon`, `title`, `description`, `action`, `className`. **Spinner** `size` (px, 24), `label` ('Loading', screen-reader only), `className`. **Skeleton** `width`, `height`, `radius` (number = px, or any CSS length), `lines`, `className`; decorative, so set `aria-busy` on the loading container.

## Migration map

| Old | New |
|---|---|
| `btn btn-primary` | `<Button variant="filled">` |
| `btn btn-secondary` | `<Button variant="tinted">` (or `plain` for Cancel) |
| `btn btn-ghost` | `<Button variant="plain">` |
| red one-off delete buttons | `<Button variant="destructive">` |
| `btn btn-icon` | `<Button iconOnly aria-label="...">` |
| `.modal-overlay/.modal-content` (Equipment `ModalShell`, Admin, Search), `.tonight-modal-*` | `<Dialog>` |
| `window.confirm(...)` | `await confirm({...})` from `useConfirm()` |
| `window.alert(...)` | `toast.error/info(...)`, or `confirm({ hideCancel: true })` |
| page-local `useToast`/`useTonightToast` + `.tonight-toast*` | `useToast()` (`onUndo` becomes `action: { label: 'Undo', onClick }`) |
| `.catalog-tab`, `.equip-tab`, `.tab-button`, `.tabs/.tab` | `<Tabs>`; Grid/List toggles become `<SegmentedControl>` |
| `.page-header/.page-title/.subtitle`, `.admin-header`, `.stats-header`, `.night-header` | `<PageHeader>` |
| `.empty-state*` | `<EmptyState>` |
| `.spinner`, `<Loader2 className="icon-spin">` | `<Spinner>` (inside buttons use `loading`) |
| `.skeleton` | `<Skeleton>` |
| page `@keyframes spin/fadeIn/slideUp` copies | `ui-spin`, `ui-fade-in`, `ui-slide-up` |

## Page CSS convention

Every page stylesheet is scoped under its root class `.page-<name>` (e.g. `.page-equipment .rig-row`), so no page can restyle another or these primitives. Don't target `ui-*` classes or bare elements (`h1`, `button`, `dialog`) from page CSS; pass `className` to the component instead. Dialogs and toasts are portalled to `<body>`, so page-scoped rules never reach them.
