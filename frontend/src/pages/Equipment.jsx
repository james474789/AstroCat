import { useState, useMemo } from 'react';
import { Link } from 'react-router-dom';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
    Camera as CameraIcon, Aperture, SlidersHorizontal, MapPin,
    Plus, Pencil, Trash2, ChevronDown, ChevronRight, MoreVertical, Sparkles, Upload, Download, RefreshCw, Search as SearchIcon,
} from 'lucide-react';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { useAuth } from '../context/AuthContext';
import TelescopeIcon from '../components/icons/TelescopeIcon';
import {
    fetchEquipment, fetchEquipmentDetect, applyEquipmentDetect, importFromTelescopius,
    triggerEquipmentAssign, fetchUnassignedImages, assignUnassignedGroups,
    createCamera, updateCamera, deleteCamera,
    createOptic, updateOptic, deleteOptic,
    createFilter, updateFilter, deleteFilter,
    createRig, updateRig, deleteRig, mountRig, unmountRig,
    fetchSites, createSite, updateSite, deleteSite,
    fetchLearnedHorizon, updateSiteHorizon, importSiteHorizon, exportSiteHorizon,
    computePixelScale, computeFovDeg, computeFocalRatio,
    formatDateTime, formatHours,
} from '../api/client';
import {
    Button, Dialog, EmptyState, PageHeader, Spinner, Tabs, TabPanel, useConfirm, useToast,
} from '../components/ui';
import './Equipment.css';
import QualityValue from '../components/quality/QualityValue';

const FILTER_BANDS = ['L', 'R', 'G', 'B', 'Ha', 'OIII', 'SII', 'Hb', 'Duo', 'None', 'Other'];
const OPTIC_KINDS = ['TELESCOPE', 'LENS'];
const FALLBACK_TIMEZONES = [
    'UTC', 'America/New_York', 'America/Chicago', 'America/Denver', 'America/Los_Angeles',
    'America/Anchorage', 'Europe/London', 'Europe/Berlin', 'Europe/Madrid', 'Europe/Moscow',
    'Asia/Kolkata', 'Asia/Tokyo', 'Asia/Shanghai', 'Australia/Sydney', 'Pacific/Auckland',
];

function timezoneOptions() {
    try {
        if (typeof Intl.supportedValuesOf === 'function') {
            const values = Intl.supportedValuesOf('timeZone');
            if (values && values.length) return values;
        }
    } catch {
        // fall through to fallback list
    }
    return FALLBACK_TIMEZONES;
}

function browserTimezone() {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC';
    } catch {
        return 'UTC';
    }
}

const TABS = [
    { value: 'rigs', label: 'Rigs', icon: <TelescopeIcon size={16} /> },
    { value: 'cameras', label: 'Cameras', icon: <CameraIcon size={16} /> },
    { value: 'optics', label: 'Optics', icon: <Aperture size={16} /> },
    { value: 'filters', label: 'Filters', icon: <SlidersHorizontal size={16} /> },
    { value: 'sites', label: 'Sites', icon: <MapPin size={16} /> },
];

function formatArcsec(v) {
    return v == null ? '—' : `${v.toFixed(2)}″`;
}

const arcminToDeg = (arcmin) => Math.round((arcmin / 60) * 100) / 100;

function degToArcmin(text) {
    const v = parseFloat(text);
    return Number.isFinite(v) && v > 0 ? Math.round(v * 60 * 10) / 10 : null;
}

// Mirrors backend utils/optics.target_size_window: 22% of the FOV short side .. 80% of the long side.
function defaultTargetWindowArcmin(fov) {
    return [0.22 * Math.min(fov[0], fov[1]) * 60, 0.8 * Math.max(fov[0], fov[1]) * 60];
}

function formatTargetWindow(w) {
    if (!w) return null;
    const f = (v) => (v < 60 ? `${Math.round(v)}′` : `${String(Math.round((v / 60) * 10) / 10)}°`);
    return `${f(w[0])}–${f(w[1])}`;
}

function formatFov(fov) {
    if (!fov) return '—';
    return `${fov[0].toFixed(2)}° × ${fov[1].toFixed(2)}°`;
}

function samplingLabel(verdict) {
    return { under: 'Undersampled', ok: 'Well sampled', over: 'Oversampled' }[verdict] || 'Unknown';
}

function samplingClass(verdict) {
    return { under: 'badge-warning', ok: 'badge-success', over: 'badge-warning' }[verdict] || '';
}

function scaleCheckText(check) {
    if (!check || check.verdict === 'unknown') return null;
    if (check.verdict === 'ok') return `measured ${check.measured.toFixed(2)}″`;
    const sign = check.delta_pct >= 0 ? '+' : '';
    return `declared ${check.declared.toFixed(2)}″ vs measured ${check.measured.toFixed(2)}″ (${sign}${check.delta_pct.toFixed(0)}% off, reducer?)`;
}

function mergeHorizonSeries(learnedPoints, savedPoints) {
    const map = new Map();
    (learnedPoints || []).forEach(([az, alt]) => {
        const key = Math.round(az * 10) / 10;
        map.set(key, { ...(map.get(key) || {}), az: key, learned: alt });
    });
    (savedPoints || []).forEach(([az, alt]) => {
        const key = Math.round(az * 10) / 10;
        map.set(key, { ...(map.get(key) || {}), az: key, saved: alt });
    });
    return Array.from(map.values()).sort((a, b) => a.az - b.az);
}

// ============ Modal shell ============

// Thin wrapper around the shared Dialog. Dialogs are portalled to <body>, so their content is
// styled from `.dlg-equipment` rather than the page scope.
function ModalShell({ title, onClose, children, footer, wide }) {
    return (
        <Dialog open onClose={onClose} title={title} size={wide ? 'lg' : 'md'} className="dlg-equipment" footer={footer}>
            {children}
        </Dialog>
    );
}

// The single-form modals share one id so the footer's submit button (outside the <form>) can target it.
const FORM_ID = 'equipment-form';

function FormFooter({ onClose, saving }) {
    return (
        <>
            <Button variant="plain" onClick={onClose}>Cancel</Button>
            <Button variant="filled" type="submit" form={FORM_ID} loading={saving}>Save</Button>
        </>
    );
}

// ============ Rig row ============

const rigSearchLink = (rig) => `/search?rig_id=${rig.id}&frame_type=LIGHT`;

function RigRow({ rig, isAdmin, mountLimit, expanded, onToggleExpand, onEdit, onDelete, onMountToggle, onActiveToggle }) {
    const [menuOpen, setMenuOpen] = useState(false);
    const scaleCheckMsg = scaleCheckText(rig.scale_check);
    const ChevronIcon = expanded ? ChevronDown : ChevronRight;
    const gear = `${rig.camera_name} · ${rig.optic_name}${rig.modifier_name ? ` · ${rig.modifier_name}` : ''}`;
    return (
        <div className={`rig-row${rig.is_active ? '' : ' inactive'}${expanded ? ' expanded' : ''}`}>
            <div className="rig-row-main">
                <button className="rig-row-chevron" onClick={() => onToggleExpand(rig.id)}
                    aria-expanded={expanded} aria-label={expanded ? 'Collapse details' : 'Expand details'}>
                    <ChevronIcon size={16} />
                </button>
                <span className="rig-name rig-row-name" title={rig.name}>{rig.name}</span>
                <span className="rig-row-gear rig-row-secondary" title={gear}>{gear}</span>
                <span className="rig-row-scale rig-row-secondary muted">{formatArcsec(rig.scale)} /px</span>
                <span className="rig-row-fwhm small"
                    title={rig.delivered_fwhm
                        ? `Median FWHM over all of this rig's measured subs (${rig.delivered_fwhm.n}). Includes seeing, optics, focus and guiding.`
                        : 'No measured subs yet'}>
                    {rig.delivered_fwhm
                        ? <QualityValue px={rig.delivered_fwhm.median_px} arcsec={rig.delivered_fwhm.median_arcsec} />
                        : <span className="muted">—</span>}
                </span>
                <Link className="rig-row-subs rig-row-secondary small" to={rigSearchLink(rig)}
                    title="Open in Search: all light frames on this rig (subs and masters)">
                    {rig.image_count} subs
                </Link>
                <button
                    className={`pill-toggle${rig.is_mounted ? ' active' : ''}`}
                    onClick={() => onMountToggle(rig)}
                    disabled={!isAdmin || (!rig.is_mounted && mountLimit != null)}
                    title={rig.is_mounted
                        ? 'Mounted (click to unmount)'
                        : mountLimit != null
                            ? `${mountLimit} rigs already mounted: unmount one first`
                            : 'Click to mount (other mounted rigs stay mounted)'}
                >
                    {rig.is_mounted ? `Mounted${rig.mount_name ? ` · ${rig.mount_name}` : ''}` : 'Not mounted'}
                </button>
                <label className="active-toggle" title="Active">
                    <input type="checkbox" checked={rig.is_active} onChange={() => onActiveToggle(rig)} disabled={!isAdmin} />
                    Active
                </label>
                <div className="rig-row-menu">
                    <Button variant="plain" size="sm" iconOnly icon={<MoreVertical size={16} />}
                        onClick={() => setMenuOpen((o) => !o)}
                        onBlur={() => setTimeout(() => setMenuOpen(false), 150)}
                        aria-label="Rig actions" aria-haspopup="menu" aria-expanded={menuOpen} />
                    {menuOpen && (
                        <div className="rig-row-menu-list" role="menu">
                            <button role="menuitem" onClick={() => onEdit(rig)} disabled={!isAdmin}><Pencil size={14} /> Edit</button>
                            <Link role="menuitem" to={rigSearchLink(rig)}><SearchIcon size={14} /> View images</Link>
                            <button role="menuitem" className="danger" onClick={() => onDelete(rig)} disabled={!isAdmin}><Trash2 size={14} /> Delete</button>
                        </div>
                    )}
                </div>
            </div>

            {expanded && (
                <div className="rig-row-detail">
                    <div className="rig-line rig-detail-narrow">{gear}</div>
                    <div className="rig-line muted">
                        <span className="rig-detail-narrow">{formatArcsec(rig.scale)} /px · </span>
                        {formatFov(rig.fov_deg)} · f/{rig.focal_ratio != null ? rig.focal_ratio.toFixed(1) : '—'}
                        {rig.target_window_arcmin && (
                            <span title={rig.min_target_arcmin != null || rig.max_target_arcmin != null
                                ? 'Target-size window for Tonight picks (edited)'
                                : 'Target-size window for Tonight picks (default from the field of view)'}>
                                {' · '}targets {formatTargetWindow(rig.target_window_arcmin)}
                            </span>
                        )}
                    </div>
                    {rig.filters && rig.filters.length > 0 && (
                        <div className="chip-row">
                            {rig.filters.map((f) => (
                                <span key={f.id} className="chip">{f.name}</span>
                            ))}
                        </div>
                    )}
                    <div className="badge-row">
                        {rig.sampling && (
                            <span className={`badge ${samplingClass(rig.sampling.verdict)}`}
                                title={rig.sampling_seeing_source === 'MEASURED'
                                    ? `${rig.sampling.ratio} px per FWHM, using this rig's measured delivered FWHM (${rig.sampling.seeing_arcsec}″)`
                                    : `${rig.sampling.ratio} px per FWHM, using the site's typical seeing (${rig.sampling.seeing_arcsec}″)`}>
                                {samplingLabel(rig.sampling.verdict)}
                                {rig.sampling_seeing_source === 'MEASURED' ? ' (measured)' : ''}
                            </span>
                        )}
                        {scaleCheckMsg && (
                            <span className={`badge ${rig.scale_check.verdict === 'ok' ? 'badge-success' : 'badge-warning'}`}>
                                {scaleCheckMsg}
                            </span>
                        )}
                    </div>
                    {rig.delivered_fwhm && (
                        <div className="rig-line small">
                            Best FWHM <QualityValue px={rig.delivered_fwhm.best_px} arcsec={rig.delivered_fwhm.best_arcsec} />
                            <span className="muted"> · median over {rig.delivered_fwhm.n} subs</span>
                        </div>
                    )}
                    <div className="rig-line muted small">
                        Last used {rig.last_used ? formatDateTime(rig.last_used) : 'never'}
                        {' · '}
                        <Link to={rigSearchLink(rig)}>View images</Link>
                    </div>
                </div>
            )}
        </div>
    );
}

const RIG_FILTERS = [
    { key: 'all', label: 'All', test: () => true },
    { key: 'mounted', label: 'Mounted', test: (r) => r.is_mounted },
    { key: 'active', label: 'Active', test: (r) => r.is_active },
    { key: 'inactive', label: 'Inactive', test: (r) => !r.is_active },
];

function sortRigs(rigs) {
    return [...rigs].sort((a, b) =>
        (b.is_mounted - a.is_mounted) || (b.is_active - a.is_active) || a.name.localeCompare(b.name));
}

// ============ Rig form modal ============

function RigModal({ rig, cameras, optics, filters, onClose, onSave }) {
    const [form, setForm] = useState(() => ({
        name: rig?.name || '',
        camera_id: rig?.camera_id ?? (cameras[0]?.id ?? ''),
        optic_id: rig?.optic_id ?? (optics[0]?.id ?? ''),
        modifier_name: rig?.modifier_name || '',
        modifier_factor: rig?.modifier_factor ?? 1.0,
        binning: rig?.binning ?? 1,
        is_active: rig?.is_active ?? true,
        mount_name: rig?.mount_name || '',
        filter_ids: rig?.filter_ids || [],
        // Target-size window, edited in degrees, stored as arcmin. Empty = the default from the FOV.
        min_target_deg: rig?.min_target_arcmin != null ? String(arcminToDeg(rig.min_target_arcmin)) : '',
        max_target_deg: rig?.max_target_arcmin != null ? String(arcminToDeg(rig.max_target_arcmin)) : '',
    }));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);

    const camera = cameras.find((c) => c.id === Number(form.camera_id));
    const optic = optics.find((o) => o.id === Number(form.optic_id));

    const preview = useMemo(() => {
        const scale = computePixelScale(camera?.pixel_size_um, optic?.focal_length_mm, Number(form.binning) || 1, Number(form.modifier_factor) || 1);
        const fov = scale ? computeFovDeg(camera?.sensor_width_px, camera?.sensor_height_px, scale) : null;
        const focalRatio = computeFocalRatio(optic?.focal_length_mm, optic?.aperture_mm, Number(form.modifier_factor) || 1);
        return { scale, fov, focalRatio };
    }, [camera, optic, form.binning, form.modifier_factor]);

    const defaultWindow = preview.fov ? defaultTargetWindowArcmin(preview.fov) : null;

    function toggleFilter(id) {
        setForm((prev) => ({
            ...prev,
            filter_ids: prev.filter_ids.includes(id)
                ? prev.filter_ids.filter((x) => x !== id)
                : [...prev.filter_ids, id],
        }));
    }

    async function handleSubmit(e) {
        e.preventDefault();
        setSaving(true);
        setError(null);
        try {
            const payload = {
                name: form.name,
                camera_id: Number(form.camera_id),
                optic_id: Number(form.optic_id),
                modifier_name: form.modifier_name || null,
                modifier_factor: Number(form.modifier_factor) || 1.0,
                binning: Number(form.binning) || 1,
                is_active: form.is_active,
                mount_name: form.mount_name || null,
                filter_ids: form.filter_ids,
                min_target_arcmin: degToArcmin(form.min_target_deg),
                max_target_arcmin: degToArcmin(form.max_target_deg),
            };
            const saved = rig ? await updateRig(rig.id, payload) : await createRig(payload);
            onSave(saved);
        } catch (err) {
            setError(err.message);
        } finally {
            setSaving(false);
        }
    }

    return (
        <ModalShell title={rig ? 'Edit Rig' : 'Add Rig'} onClose={onClose} footer={<FormFooter onClose={onClose} saving={saving} />}>
            <form id={FORM_ID} onSubmit={handleSubmit} className="equip-form">
                {error && <div className="form-error">{error}</div>}
                <label>Name
                    <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
                </label>
                <div className="form-row">
                    <label>Camera
                        <select className="input" value={form.camera_id} onChange={(e) => setForm({ ...form, camera_id: e.target.value })} required>
                            {cameras.map((c) => <option key={c.id} value={c.id}>{c.name}</option>)}
                        </select>
                    </label>
                    <label>Optic
                        <select className="input" value={form.optic_id} onChange={(e) => setForm({ ...form, optic_id: e.target.value })} required>
                            {optics.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                        </select>
                    </label>
                </div>
                <div className="form-row">
                    <label>Modifier name
                        <input className="input" placeholder="e.g. 0.8x reducer" value={form.modifier_name} onChange={(e) => setForm({ ...form, modifier_name: e.target.value })} />
                    </label>
                    <label>Modifier factor
                        <input className="input" type="number" step="0.01" value={form.modifier_factor} onChange={(e) => setForm({ ...form, modifier_factor: e.target.value })} />
                    </label>
                    <label>Binning
                        <input className="input" type="number" min="1" step="1" value={form.binning} onChange={(e) => setForm({ ...form, binning: e.target.value })} />
                    </label>
                </div>
                <label>Mount name
                    <input className="input" placeholder="e.g. EQ6-R Pro" value={form.mount_name} onChange={(e) => setForm({ ...form, mount_name: e.target.value })} />
                </label>
                <div className="form-group-label">Target size</div>
                <div className="form-row">
                    <label>Smallest (°)
                        <input className="input" type="number" min="0" step="0.1" value={form.min_target_deg}
                            placeholder={defaultWindow ? String(arcminToDeg(defaultWindow[0])) : 'default'}
                            onChange={(e) => setForm({ ...form, min_target_deg: e.target.value })} />
                    </label>
                    <label>Largest (°)
                        <input className="input" type="number" min="0" step="0.1" value={form.max_target_deg}
                            placeholder={defaultWindow ? String(arcminToDeg(defaultWindow[1])) : 'default'}
                            onChange={(e) => setForm({ ...form, max_target_deg: e.target.value })} />
                    </label>
                </div>
                <div className="muted small">
                    Tonight only suggests targets in this size range for the rig (unless you have already imaged
                    them with it). Clear a box to use the default: 22% of the short side up to 80% of the long side.
                </div>
                <div className="form-group-label">Filters</div>
                <div className="chip-row selectable">
                    {filters.map((f) => (
                        <button
                            type="button"
                            key={f.id}
                            className={`chip selectable-chip${form.filter_ids.includes(f.id) ? ' selected' : ''}`}
                            onClick={() => toggleFilter(f.id)}
                        >
                            {f.name}
                        </button>
                    ))}
                </div>
                <label className="checkbox-row">
                    <input type="checkbox" checked={form.is_active} onChange={(e) => setForm({ ...form, is_active: e.target.checked })} />
                    Active
                </label>

                <div className="preview-box">
                    <div className="preview-title">Computed preview</div>
                    <div className="preview-grid">
                        <div><span className="muted">Scale</span> {formatArcsec(preview.scale)}/px</div>
                        <div><span className="muted">FOV</span> {formatFov(preview.fov)}</div>
                        <div><span className="muted">f/ratio</span> {preview.focalRatio != null ? `f/${preview.focalRatio.toFixed(1)}` : '—'}</div>
                    </div>
                </div>

            </form>
        </ModalShell>
    );
}

// ============ Camera form modal ============

function CameraModal({ camera, onClose, onSave }) {
    const [form, setForm] = useState(() => ({
        name: camera?.name || '',
        maker: camera?.maker || '',
        sensor_width_px: camera?.sensor_width_px ?? '',
        sensor_height_px: camera?.sensor_height_px ?? '',
        pixel_size_um: camera?.pixel_size_um ?? '',
        is_color: camera?.is_color == null ? '' : String(camera.is_color),
        is_cooled: camera?.is_cooled == null ? '' : String(camera.is_cooled),
        match_patterns: (camera?.match_patterns || []).join(', '),
        notes: camera?.notes || '',
    }));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);

    async function handleSubmit(e) {
        e.preventDefault();
        setSaving(true);
        setError(null);
        try {
            const payload = {
                name: form.name,
                maker: form.maker || null,
                sensor_width_px: form.sensor_width_px === '' ? null : Number(form.sensor_width_px),
                sensor_height_px: form.sensor_height_px === '' ? null : Number(form.sensor_height_px),
                pixel_size_um: form.pixel_size_um === '' ? null : Number(form.pixel_size_um),
                is_color: form.is_color === '' ? null : form.is_color === 'true',
                is_cooled: form.is_cooled === '' ? null : form.is_cooled === 'true',
                match_patterns: form.match_patterns.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean),
                notes: form.notes || null,
            };
            const saved = camera ? await updateCamera(camera.id, payload) : await createCamera(payload);
            onSave(saved);
        } catch (err) {
            setError(err.message);
        } finally {
            setSaving(false);
        }
    }

    return (
        <ModalShell title={camera ? 'Edit Camera' : 'Add Camera'} onClose={onClose} footer={<FormFooter onClose={onClose} saving={saving} />}>
            <form id={FORM_ID} onSubmit={handleSubmit} className="equip-form">
                {error && <div className="form-error">{error}</div>}
                <label>Name
                    <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
                </label>
                <label>Maker
                    <input className="input" value={form.maker} onChange={(e) => setForm({ ...form, maker: e.target.value })} />
                </label>
                <div className="form-row">
                    <label>Sensor width (px)
                        <input className="input" type="number" value={form.sensor_width_px} onChange={(e) => setForm({ ...form, sensor_width_px: e.target.value })} />
                    </label>
                    <label>Sensor height (px)
                        <input className="input" type="number" value={form.sensor_height_px} onChange={(e) => setForm({ ...form, sensor_height_px: e.target.value })} />
                    </label>
                    <label>Pixel size (µm)
                        <input className="input" type="number" step="0.01" value={form.pixel_size_um} onChange={(e) => setForm({ ...form, pixel_size_um: e.target.value })} />
                    </label>
                </div>
                <div className="form-row">
                    <label>Color
                        <select className="input" value={form.is_color} onChange={(e) => setForm({ ...form, is_color: e.target.value })}>
                            <option value="">Unknown</option>
                            <option value="true">Color (OSC/DSLR)</option>
                            <option value="false">Mono</option>
                        </select>
                    </label>
                    <label>Cooled
                        <select className="input" value={form.is_cooled} onChange={(e) => setForm({ ...form, is_cooled: e.target.value })}>
                            <option value="">Unknown</option>
                            <option value="true">Cooled</option>
                            <option value="false">Uncooled</option>
                        </select>
                    </label>
                </div>
                <label>Match patterns (comma-separated)
                    <input className="input" value={form.match_patterns} onChange={(e) => setForm({ ...form, match_patterns: e.target.value })} />
                </label>
                <label>Notes
                    <textarea className="input" rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
                </label>
            </form>
        </ModalShell>
    );
}

// ============ Optic form modal ============

function OpticModal({ optic, onClose, onSave }) {
    const [form, setForm] = useState(() => ({
        name: optic?.name || '',
        kind: optic?.kind || 'TELESCOPE',
        aperture_mm: optic?.aperture_mm ?? '',
        focal_length_mm: optic?.focal_length_mm ?? '',
        notes: optic?.notes || '',
    }));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);

    async function handleSubmit(e) {
        e.preventDefault();
        setSaving(true);
        setError(null);
        try {
            const payload = {
                name: form.name,
                kind: form.kind,
                aperture_mm: form.aperture_mm === '' ? null : Number(form.aperture_mm),
                focal_length_mm: Number(form.focal_length_mm),
                notes: form.notes || null,
            };
            const saved = optic ? await updateOptic(optic.id, payload) : await createOptic(payload);
            onSave(saved);
        } catch (err) {
            setError(err.message);
        } finally {
            setSaving(false);
        }
    }

    return (
        <ModalShell title={optic ? 'Edit Optic' : 'Add Optic'} onClose={onClose} footer={<FormFooter onClose={onClose} saving={saving} />}>
            <form id={FORM_ID} onSubmit={handleSubmit} className="equip-form">
                {error && <div className="form-error">{error}</div>}
                <label>Name
                    <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
                </label>
                <div className="form-row">
                    <label>Kind
                        <select className="input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
                            {OPTIC_KINDS.map((k) => <option key={k} value={k}>{k}</option>)}
                        </select>
                    </label>
                    <label>Aperture (mm)
                        <input className="input" type="number" step="0.1" value={form.aperture_mm} onChange={(e) => setForm({ ...form, aperture_mm: e.target.value })} />
                    </label>
                    <label>Focal length (mm)
                        <input className="input" type="number" step="0.1" value={form.focal_length_mm} onChange={(e) => setForm({ ...form, focal_length_mm: e.target.value })} required />
                    </label>
                </div>
                <label>Notes
                    <textarea className="input" rows={2} value={form.notes} onChange={(e) => setForm({ ...form, notes: e.target.value })} />
                </label>
            </form>
        </ModalShell>
    );
}

// ============ Filter form modal ============

function FilterModal({ filter, onClose, onSave }) {
    const [form, setForm] = useState(() => ({
        name: filter?.name || '',
        band: filter?.band || 'L',
        bandwidth_nm: filter?.bandwidth_nm ?? '',
        match_patterns: (filter?.match_patterns || []).join(', '),
    }));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);

    async function handleSubmit(e) {
        e.preventDefault();
        setSaving(true);
        setError(null);
        try {
            const payload = {
                name: form.name,
                band: form.band,
                bandwidth_nm: form.bandwidth_nm === '' ? null : Number(form.bandwidth_nm),
                match_patterns: form.match_patterns.split(',').map((s) => s.trim().toLowerCase()).filter(Boolean),
            };
            const saved = filter ? await updateFilter(filter.id, payload) : await createFilter(payload);
            onSave(saved);
        } catch (err) {
            setError(err.message);
        } finally {
            setSaving(false);
        }
    }

    return (
        <ModalShell title={filter ? 'Edit Filter' : 'Add Filter'} onClose={onClose} footer={<FormFooter onClose={onClose} saving={saving} />}>
            <form id={FORM_ID} onSubmit={handleSubmit} className="equip-form">
                {error && <div className="form-error">{error}</div>}
                <label>Name
                    <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
                </label>
                <div className="form-row">
                    <label>Band
                        <select className="input" value={form.band} onChange={(e) => setForm({ ...form, band: e.target.value })}>
                            {FILTER_BANDS.map((b) => <option key={b} value={b}>{b}</option>)}
                        </select>
                    </label>
                    <label>Bandwidth (nm)
                        <input className="input" type="number" step="0.1" value={form.bandwidth_nm} onChange={(e) => setForm({ ...form, bandwidth_nm: e.target.value })} />
                    </label>
                </div>
                <label>Match patterns (comma-separated)
                    <input className="input" value={form.match_patterns} onChange={(e) => setForm({ ...form, match_patterns: e.target.value })} />
                </label>
            </form>
        </ModalShell>
    );
}

// ============ Site form modal ============

function SiteModal({ site, onClose, onSave }) {
    const tzOptions = useMemo(() => timezoneOptions(), []);
    const [form, setForm] = useState(() => ({
        name: site?.name || '',
        latitude: site?.latitude ?? '',
        longitude: site?.longitude ?? '',
        elevation_m: site?.elevation_m ?? '',
        timezone: site?.timezone || browserTimezone(),
        bortle: site?.bortle ?? '',
        sqm: site?.sqm ?? '',
        typical_seeing_arcsec: site?.typical_seeing_arcsec ?? 2.5,
        is_default: site?.is_default ?? false,
    }));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);

    async function handleSubmit(e) {
        e.preventDefault();
        setSaving(true);
        setError(null);
        try {
            const payload = {
                name: form.name,
                latitude: Number(form.latitude),
                longitude: Number(form.longitude),
                elevation_m: form.elevation_m === '' ? null : Number(form.elevation_m),
                timezone: form.timezone,
                bortle: form.bortle === '' ? null : Number(form.bortle),
                sqm: form.sqm === '' ? null : Number(form.sqm),
                typical_seeing_arcsec: Number(form.typical_seeing_arcsec) || 2.5,
                is_default: form.is_default,
            };
            const saved = site ? await updateSite(site.id, payload) : await createSite(payload);
            onSave(saved);
        } catch (err) {
            setError(err.message);
        } finally {
            setSaving(false);
        }
    }

    return (
        <ModalShell title={site ? 'Edit Site' : 'Add Site'} onClose={onClose} footer={<FormFooter onClose={onClose} saving={saving} />}>
            <form id={FORM_ID} onSubmit={handleSubmit} className="equip-form">
                {error && <div className="form-error">{error}</div>}
                <label>Name
                    <input className="input" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} required />
                </label>
                <div className="form-row">
                    <label>Latitude
                        <input className="input" type="number" step="0.0001" value={form.latitude} onChange={(e) => setForm({ ...form, latitude: e.target.value })} required />
                    </label>
                    <label>Longitude
                        <input className="input" type="number" step="0.0001" value={form.longitude} onChange={(e) => setForm({ ...form, longitude: e.target.value })} required />
                    </label>
                    <label>Elevation (m)
                        <input className="input" type="number" step="1" value={form.elevation_m} onChange={(e) => setForm({ ...form, elevation_m: e.target.value })} />
                    </label>
                </div>
                <label>Timezone
                    <select className="input" value={form.timezone} onChange={(e) => setForm({ ...form, timezone: e.target.value })}>
                        {tzOptions.map((tz) => <option key={tz} value={tz}>{tz}</option>)}
                    </select>
                </label>
                <div className="form-row">
                    <label>Bortle
                        <input className="input" type="number" min="1" max="9" value={form.bortle} onChange={(e) => setForm({ ...form, bortle: e.target.value })} />
                    </label>
                    <label>SQM
                        <input className="input" type="number" step="0.01" value={form.sqm} onChange={(e) => setForm({ ...form, sqm: e.target.value })} />
                    </label>
                    <label>Typical seeing (″)
                        <input className="input" type="number" step="0.1" value={form.typical_seeing_arcsec} onChange={(e) => setForm({ ...form, typical_seeing_arcsec: e.target.value })} />
                    </label>
                </div>
                <label className="checkbox-row">
                    <input type="checkbox" checked={form.is_default} onChange={(e) => setForm({ ...form, is_default: e.target.checked })} />
                    Default site
                </label>
            </form>
        </ModalShell>
    );
}

// ============ Detect review panel ============

function DetectReviewPanel({ detect, existingOptics, telescopiusAvailable, onClose, onApplied, isAdmin, showToast }) {
    const [accepted, setAccepted] = useState(() => {
        const initial = {};
        ['cameras', 'optics', 'filters', 'rigs', 'sites'].forEach((kind) => {
            (detect[kind] || []).forEach((p) => { initial[p.proposal_id] = !p.exists; });
        });
        return initial;
    });
    const [edits, setEdits] = useState(() => {
        const initial = {};
        (detect.rigs || []).forEach((p) => {
            initial[p.proposal_id] = {
                name: p.name,
                optic_id: p.optic_existing_id ?? '',
                modifier_factor: p.modifier_factor,
                modifier_name: '',
                binning: p.binning,
            };
        });
        (detect.sites || []).forEach((p) => {
            initial[p.proposal_id] = { name: p.name, timezone: browserTimezone() };
        });
        (detect.cameras || []).forEach((p) => { initial[p.proposal_id] = { name: p.name }; });
        (detect.filters || []).forEach((p) => { initial[p.proposal_id] = { name: p.name }; });
        return initial;
    });
    const [applying, setApplying] = useState(false);
    const [importing, setImporting] = useState(false);
    const [error, setError] = useState(null);
    const [notice, setNotice] = useState(null);

    function toggleAccept(id) {
        setAccepted((prev) => ({ ...prev, [id]: !prev[id] }));
    }

    function updateEdit(id, patch) {
        setEdits((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
    }

    async function handleImportTelescopius() {
        setImporting(true);
        setError(null);
        setNotice(null);
        try {
            const result = await importFromTelescopius();
            const summary = ['cameras', 'optics', 'filters']
                .map((k) => `${k}: +${result[k].created}/${result[k].updated}~/${result[k].skipped} skip`)
                .join(', ');
            // Shown inline: toasts sit under an open dialog.
            setNotice(`Telescopius import done — ${summary}`);
        } catch (err) {
            setError(`Telescopius import failed: ${err.message}`);
        } finally {
            setImporting(false);
        }
    }

    async function handleApply() {
        setApplying(true);
        setError(null);
        try {
            const accept = [];
            ['cameras', 'optics', 'filters', 'rigs', 'sites'].forEach((kind) => {
                (detect[kind] || []).forEach((p) => {
                    if (!accepted[p.proposal_id]) return;
                    const edit = edits[p.proposal_id] || {};
                    const entry = { proposal_id: p.proposal_id };
                    if (edit.name && edit.name !== p.name) entry.name = edit.name;
                    if (kind === 'rigs') {
                        if (edit.optic_id) entry.optic_id = Number(edit.optic_id);
                        entry.modifier_factor = Number(edit.modifier_factor) || 1.0;
                        entry.modifier_name = edit.modifier_name || null;
                        entry.binning = Number(edit.binning) || 1;
                    }
                    if (kind === 'sites' && edit.timezone) entry.timezone = edit.timezone;
                    accept.push(entry);
                });
            });
            const result = await applyEquipmentDetect({ timezone: browserTimezone(), accept });
            const summary = Object.entries(result.created).map(([k, v]) => `${v} ${k}`).join(', ');
            showToast(`Applied — created ${summary}.`, 'success', 6000);
            onApplied();
        } catch (err) {
            setError(err.message);
        } finally {
            setApplying(false);
        }
    }

    return (
        <ModalShell
            title="Review detected setups"
            onClose={onClose}
            wide
            footer={(
                <>
                    {telescopiusAvailable && (
                        <Button onClick={handleImportTelescopius} loading={importing} disabled={!isAdmin}>
                            Import from Telescopius
                        </Button>
                    )}
                    <Button variant="plain" onClick={onClose}>Cancel</Button>
                    <Button variant="filled" onClick={handleApply} loading={applying} disabled={!isAdmin}>Apply</Button>
                </>
            )}
        >
            {error && <div className="form-error">{error}</div>}
            {notice && <div className="form-notice" role="status">{notice}</div>}

            {(detect.rigs || []).length > 0 && (
                <div className="detect-section">
                    <h3>Rigs</h3>
                    <div className="detect-rig-grid">
                        {detect.rigs.map((p) => (
                            <div key={p.proposal_id} className={`equip-card detect-card${p.exists ? ' already-exists' : ''}`}>
                                <div className="detect-card-header">
                                    <label className="checkbox-row">
                                        <input
                                            type="checkbox"
                                            checked={!!accepted[p.proposal_id]}
                                            onChange={() => toggleAccept(p.proposal_id)}
                                            disabled={p.exists}
                                        />
                                        <input
                                            className="input detect-name-input"
                                            value={edits[p.proposal_id]?.name ?? p.name}
                                            onChange={(e) => updateEdit(p.proposal_id, { name: e.target.value })}
                                        />
                                    </label>
                                    {p.exists && <span className="badge">Already in library</span>}
                                </div>
                                <div className="form-row">
                                    <label>Optic
                                        <select
                                            className="input"
                                            value={edits[p.proposal_id]?.optic_id ?? ''}
                                            onChange={(e) => updateEdit(p.proposal_id, { optic_id: e.target.value })}
                                        >
                                            <option value="">Create detected (~{Math.round(p.predicted_focal_mm)} mm)</option>
                                            {existingOptics.map((o) => <option key={o.id} value={o.id}>{o.name}</option>)}
                                        </select>
                                    </label>
                                </div>
                                <div className="form-row">
                                    <label>Modifier
                                        <input
                                            className="input"
                                            type="number"
                                            step="0.01"
                                            value={edits[p.proposal_id]?.modifier_factor ?? p.modifier_factor}
                                            onChange={(e) => updateEdit(p.proposal_id, { modifier_factor: e.target.value })}
                                        />
                                    </label>
                                    <label>Binning
                                        <input
                                            className="input"
                                            type="number"
                                            min="1"
                                            value={edits[p.proposal_id]?.binning ?? p.binning}
                                            onChange={(e) => updateEdit(p.proposal_id, { binning: e.target.value })}
                                        />
                                    </label>
                                </div>
                                {p.filter_bands?.length > 0 && (
                                    <div className="chip-row">
                                        {p.filter_bands.map((b) => <span key={b} className="chip">{b}</span>)}
                                    </div>
                                )}
                                <div className="rig-line muted small">
                                    {p.image_count} subs · last used {p.last_used ? formatDateTime(p.last_used) : 'never'} · measured {formatArcsec(p.measured_scale_arcsec)}
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            )}

            {(detect.cameras || []).length > 0 && (
                <div className="detect-section">
                    <h3>Cameras</h3>
                    {detect.cameras.map((p) => (
                        <label key={p.proposal_id} className="checkbox-row detect-simple-row">
                            <input type="checkbox" checked={!!accepted[p.proposal_id]} onChange={() => toggleAccept(p.proposal_id)} disabled={p.exists} />
                            {p.name} <span className="muted small">({p.image_count} subs)</span>
                            {p.exists && <span className="badge">Already in library</span>}
                        </label>
                    ))}
                </div>
            )}

            {(detect.filters || []).length > 0 && (
                <div className="detect-section">
                    <h3>Filters</h3>
                    {detect.filters.map((p) => (
                        <label key={p.proposal_id} className="checkbox-row detect-simple-row">
                            <input type="checkbox" checked={!!accepted[p.proposal_id]} onChange={() => toggleAccept(p.proposal_id)} disabled={p.exists} />
                            {p.name} ({p.band}) <span className="muted small">({p.image_count} subs)</span>
                            {p.exists && <span className="badge">Already in library</span>}
                        </label>
                    ))}
                </div>
            )}

            {(detect.sites || []).length > 0 && (
                <div className="detect-section">
                    <h3>Sites</h3>
                    {detect.sites.map((p) => (
                        <div key={p.proposal_id} className="detect-simple-row form-row">
                            <label className="checkbox-row">
                                <input type="checkbox" checked={!!accepted[p.proposal_id]} onChange={() => toggleAccept(p.proposal_id)} disabled={p.exists} />
                                {p.name} <span className="muted small">({p.image_count} subs)</span>
                            </label>
                            {!p.exists && (
                                <label>Timezone
                                    <select
                                        className="input"
                                        value={edits[p.proposal_id]?.timezone ?? browserTimezone()}
                                        onChange={(e) => updateEdit(p.proposal_id, { timezone: e.target.value })}
                                    >
                                        {timezoneOptions().map((tz) => <option key={tz} value={tz}>{tz}</option>)}
                                    </select>
                                </label>
                            )}
                            {p.exists && <span className="badge">Already in library</span>}
                        </div>
                    ))}
                </div>
            )}
        </ModalShell>
    );
}

// ============ Sites tab ============

function SitesTab({ sites, isAdmin, onEdit, onDelete, onAdd, showToast, refetchSites }) {
    const [selectedSiteId, setSelectedSiteId] = useState(null);

    // Q1d: adopt the measured delivered FWHM as the site's typical seeing (feeds rig sampling checks).
    async function handleUseMeasuredSeeing(site) {
        const value = Math.round(site.measured_seeing.fwhm_arcsec * 100) / 100;
        try {
            await updateSite(site.id, { typical_seeing_arcsec: value });
            showToast(`${site.name}: typical seeing set to ${value}″`);
            refetchSites();
        } catch (err) {
            showToast(`Failed to update site: ${err.message}`, 'error');
        }
    }
    // Fall back to the first site until the viewer picks one explicitly (derived in render,
    // not an effect, so switching sites away and back doesn't fight a stale selection).
    const effectiveSiteId = selectedSiteId ?? sites[0]?.id ?? null;
    const selectedSite = sites.find((s) => s.id === effectiveSiteId) || null;

    const learnedQuery = useQuery({
        queryKey: ['learnedHorizon', effectiveSiteId],
        queryFn: () => fetchLearnedHorizon(effectiveSiteId),
        enabled: !!effectiveSiteId,
        staleTime: 60 * 60 * 1000,
    });

    const chartData = useMemo(
        () => mergeHorizonSeries(learnedQuery.data?.points, selectedSite?.horizon),
        [learnedQuery.data, selectedSite],
    );

    async function handleUseLearned() {
        if (!selectedSite || !learnedQuery.data?.points) return;
        try {
            await updateSiteHorizon(selectedSite.id, learnedQuery.data.points, 'LEARNED');
            showToast('Saved learned horizon.', 'success');
            refetchSites();
        } catch (err) {
            showToast(`Failed to save horizon: ${err.message}`, 'error');
        }
    }

    async function handleImportFile(e) {
        const file = e.target.files?.[0];
        e.target.value = '';
        if (!file || !selectedSite) return;
        try {
            await importSiteHorizon(selectedSite.id, file);
            showToast('Horizon imported.', 'success');
            refetchSites();
        } catch (err) {
            showToast(`Failed to import horizon: ${err.message}`, 'error');
        }
    }

    async function handleExport() {
        if (!selectedSite) return;
        try {
            const blob = await exportSiteHorizon(selectedSite.id);
            const url = window.URL.createObjectURL(blob);
            const link = document.createElement('a');
            link.href = url;
            link.download = `${selectedSite.name.replace(/[^a-z0-9]+/gi, '_')}.hrz`;
            document.body.appendChild(link);
            link.click();
            link.remove();
            window.URL.revokeObjectURL(url);
        } catch (err) {
            showToast(`Failed to export horizon: ${err.message}`, 'error');
        }
    }

    if (sites.length === 0) {
        return (
            <EmptyState
                icon={<MapPin strokeWidth={1.5} />}
                title="No sites yet"
                description="Add an observing site to enable horizon-aware planning."
                action={<Button variant="filled" icon={<Plus size={16} />} onClick={onAdd} disabled={!isAdmin}>Add site</Button>}
            />
        );
    }

    return (
        <div className="sites-tab">
            <div className="equip-list">
                {sites.map((s) => (
                    <div key={s.id} className={`equip-card site-card${s.id === effectiveSiteId ? ' selected' : ''}`} role="button" tabIndex={0} aria-pressed={s.id === effectiveSiteId}
                        onClick={() => setSelectedSiteId(s.id)}
                        onKeyDown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); setSelectedSiteId(s.id); } }}>
                        <div className="rig-card-header">
                            <span className="rig-name">{s.name}{s.is_default && <span className="badge badge-primary" style={{ marginLeft: '0.5rem' }}>Default</span>}</span>
                        </div>
                        <div className="rig-line muted small">{s.timezone} · Bortle {s.bortle ?? '—'} · seeing {s.typical_seeing_arcsec}″ · {s.image_count} subs</div>
                        {s.measured_seeing && (
                            <div className="rig-line small site-measured"
                                title={`Median delivered FWHM of the sharpest rig that can resolve seeing (${s.measured_seeing.rig_name}, ${s.measured_seeing.n} subs, its last ${s.measured_seeing.window_days} days). Includes optics and guiding, so the true seeing is this or better.`}>
                                Measured {s.measured_seeing.fwhm_arcsec.toFixed(2)}″
                                <span className="muted"> ({s.measured_seeing.rig_name})</span>
                                {Math.abs(s.measured_seeing.fwhm_arcsec - s.typical_seeing_arcsec) >= 0.1 && (
                                    <Button variant="plain" size="sm" disabled={!isAdmin}
                                        onClick={(e) => { e.stopPropagation(); handleUseMeasuredSeeing(s); }}>
                                        Use as typical seeing
                                    </Button>
                                )}
                            </div>
                        )}
                        <div className="equip-card-actions">
                            <Button variant="plain" size="sm" icon={<Pencil size={14} />} onClick={(e) => { e.stopPropagation(); onEdit(s); }} disabled={!isAdmin}>Edit</Button>
                            <Button variant="destructive" size="sm" icon={<Trash2 size={14} />} onClick={(e) => { e.stopPropagation(); onDelete(s); }} disabled={!isAdmin}>Delete</Button>
                        </div>
                    </div>
                ))}
                <Button className="add-card-btn" icon={<Plus size={16} />} onClick={onAdd} disabled={!isAdmin}>Add site</Button>
            </div>

            {selectedSite && (
                <div className="horizon-panel">
                    <div className="horizon-header">
                        <h3>Horizon — {selectedSite.name}</h3>
                        <div className="horizon-actions">
                            <Button size="sm" onClick={handleUseLearned} disabled={!isAdmin || !learnedQuery.data?.points?.length}>Use learned</Button>
                            <label className="btn btn-secondary btn-sm file-btn">
                                <Upload size={14} /> Import .hrz
                                <input type="file" accept=".hrz,.txt" onChange={handleImportFile} disabled={!isAdmin} hidden />
                            </label>
                            <Button size="sm" icon={<Download size={14} />} onClick={handleExport} disabled={!selectedSite.horizon}>Export .hrz</Button>
                        </div>
                    </div>
                    <ResponsiveContainer width="100%" height={300}>
                        <LineChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 0 }}>
                            <CartesianGrid strokeDasharray="3 3" stroke="var(--color-border)" />
                            <XAxis dataKey="az" type="number" domain={[0, 360]} ticks={[0, 90, 180, 270, 360]} stroke="var(--color-text-secondary)" fontSize={11} />
                            <YAxis domain={[0, 90]} stroke="var(--color-text-secondary)" fontSize={11} />
                            <Tooltip contentStyle={{ background: 'var(--color-surface-elevated)', border: '1px solid var(--color-border)' }} />
                            <Legend />
                            <Line type="monotone" dataKey="learned" stroke="var(--color-accent)" strokeDasharray="5 5" dot={false} name="Learned" connectNulls />
                            <Line type="monotone" dataKey="saved" stroke="var(--color-primary)" dot={false} name="Saved" connectNulls />
                        </LineChart>
                    </ResponsiveContainer>
                    {!selectedSite.horizon && !learnedQuery.data?.points?.length && (
                        <p className="muted small">No horizon data yet. Import a .hrz file, or use the learned profile once enough subs have been captured at this site.</p>
                    )}
                </div>
            )}
        </div>
    );
}

// ============ Main page ============

// R0b: rig-less light subs and masters, bucketed by camera, derived binning
// and calculated focal length, for bulk allocation.
const REASON_TEXT = {
    no_camera_match: "Camera or frame size doesn't match any rig",
    scale_mismatch: "Plate scale doesn't match any rig",
    ambiguous: "Several rigs fit; couldn't choose",
    no_active_rig: 'Only inactive rigs match',
};
const SUGGESTION_TEXT = {
    exact: 'matches rig',
    focal: 'Suggested (camera, binning, focal length)',
};
const UNASSIGNED_OPEN_KEY = 'equipment.unassigned.open';

function readUnassignedOpen() {
    try {
        return localStorage.getItem(UNASSIGNED_OPEN_KEY) === '1';
    } catch {
        return false;
    }
}

function writeUnassignedOpen(open) {
    try {
        localStorage.setItem(UNASSIGNED_OPEN_KEY, open ? '1' : '0');
    } catch {
        // storage unavailable: the section just starts collapsed next time
    }
}

function rigOptionLabel(rig) {
    const parts = [rig.name];
    if (rig.camera_name) parts.push(rig.camera_name);
    if (rig.effective_focal_mm) parts.push(`${Math.round(rig.effective_focal_mm)} mm`);
    if (rig.binning > 1) parts.push(`bin ${rig.binning}`);
    return parts.join(' · ');
}

const bucketSearchLink = (g) => `/search?rig_bucket=${encodeURIComponent(g.key)}`;

function formatFocal(g) {
    if (g.focal_mm == null) return 'unknown';
    const lo = Math.round(g.focal_min);
    const hi = Math.round(g.focal_max);
    return lo === hi ? `${lo} mm` : `${lo}–${hi} mm`;
}

function UnassignedImagesSection({ rigs, isAdmin, showToast }) {
    const queryClient = useQueryClient();
    const [open, setOpen] = useState(readUnassignedOpen);
    const [choices, setChoices] = useState({});   // key -> rig id string (overrides the suggestion)
    const [selected, setSelected] = useState({}); // key -> true
    const [busy, setBusy] = useState(false);

    const query = useQuery({
        queryKey: ['equipmentUnassigned'],
        queryFn: fetchUnassignedImages,
        staleTime: 60_000,
    });

    const groups = useMemo(() => query.data?.groups || [], [query.data]);

    if (query.isLoading) {
        return (
            <div className="unassigned-section">
                <p className="muted small"><Spinner size={14} className="spinner-inline" /> Looking for unassigned images…</p>
            </div>
        );
    }
    if (query.isError) {
        return (
            <div className="unassigned-section">
                <p className="muted small">Couldn't load unassigned images: {query.error?.message}</p>
            </div>
        );
    }

    const total = query.data?.total || 0;
    if (total === 0) return null;

    function rigFor(g) {
        if (choices[g.key] !== undefined) return choices[g.key];
        return g.suggested_rig_id != null ? String(g.suggested_rig_id) : '';
    }

    // Only buckets that are ticked and have a rig are applied.
    const ready = groups.filter((g) => selected[g.key] && rigFor(g));
    const readyImages = ready.reduce((sum, g) => sum + g.count, 0);
    const suggestedCount = groups.filter((g) => g.suggested_rig_id != null).length;

    function toggleOpen() {
        const next = !open;
        setOpen(next);
        writeUnassignedOpen(next);
    }

    function chooseRig(g, value) {
        setChoices((prev) => ({ ...prev, [g.key]: value }));
        setSelected((prev) => ({ ...prev, [g.key]: !!value }));
    }

    function selectSuggested() {
        setSelected(Object.fromEntries(groups.filter((g) => rigFor(g)).map((g) => [g.key, true])));
    }

    async function handleAssign() {
        if (ready.length === 0) return;
        setBusy(true);
        try {
            const result = await assignUnassignedGroups(ready.map((g) => ({ key: g.key, rig_id: Number(rigFor(g)) })));
            const n = result?.updated_count ?? 0;
            const buckets = result?.results?.length ?? 0;
            const stale = result?.stale_keys?.length ?? 0;
            let message = `Assigned ${n} image${n === 1 ? '' : 's'} in ${buckets} bucket${buckets === 1 ? '' : 's'}`;
            if (stale) message += ` — ${stale} changed and were skipped; refreshed`;
            showToast(message, stale ? 'info' : 'success', 6000);
            setChoices({});
            setSelected({});
            queryClient.invalidateQueries({ queryKey: ['equipmentUnassigned'] });
            queryClient.invalidateQueries({ queryKey: ['equipment'] });
        } catch (err) {
            if (err.status === 409) {
                showToast('These buckets changed — refreshed', 'error');
                setSelected({});
                query.refetch();
            } else {
                showToast(`Failed to assign: ${err.message}`, 'error', 6000);
            }
        } finally {
            setBusy(false);
        }
    }

    return (
        <div className="unassigned-section">
            <button type="button" className="unassigned-header" onClick={toggleOpen} aria-expanded={open}>
                <span className="unassigned-caret">{open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}</span>
                <h3>Unassigned images</h3>
                <span className="badge badge-warning">{total}</span>
                <span className="muted small">
                    Light subs and masters that couldn't be matched to a rig automatically, grouped by camera, binning and calculated focal length.
                </span>
            </button>
            {open && (
                <>
                    {isAdmin && (
                        <div className="unassigned-toolbar">
                            <Button variant="plain" size="sm" onClick={selectSuggested} disabled={suggestedCount === 0 && Object.keys(choices).length === 0}>
                                Select all with a rig ({groups.filter((g) => rigFor(g)).length})
                            </Button>
                            <Button variant="plain" size="sm" onClick={() => setSelected({})} disabled={ready.length === 0}>
                                Clear selection
                            </Button>
                            <Button variant="filled" size="sm" onClick={handleAssign} loading={busy} disabled={ready.length === 0}>
                                {busy ? 'Assigning…' : `Assign selected (${ready.length} bucket${ready.length === 1 ? '' : 's'} · ${readyImages} image${readyImages === 1 ? '' : 's'})`}
                            </Button>
                        </div>
                    )}
                    <div className="unassigned-table-wrap">
                        <table className="table unassigned-table">
                            <thead>
                                <tr>
                                    {isAdmin && <th />}
                                    <th>Camera</th>
                                    <th>Bin</th>
                                    <th>Focal length</th>
                                    <th>Images</th>
                                    <th>Frame sizes</th>
                                    <th>Filters</th>
                                    <th>Dates</th>
                                    <th>Why</th>
                                    <th>Rig</th>
                                    <th />
                                </tr>
                            </thead>
                            <tbody>
                                {groups.map((g) => {
                                    const rig = rigFor(g);
                                    const isSuggested = g.suggested_rig_id != null && rig === String(g.suggested_rig_id);
                                    return (
                                        <tr key={g.key} className={selected[g.key] ? 'selected' : undefined}>
                                            {isAdmin && (
                                                <td>
                                                    <input
                                                        type="checkbox"
                                                        aria-label="Select bucket"
                                                        checked={!!selected[g.key] && !!rig}
                                                        disabled={!rig}
                                                        title={rig ? 'Include in "Assign selected"' : 'Choose a rig first'}
                                                        onChange={(e) => setSelected((prev) => ({ ...prev, [g.key]: e.target.checked }))}
                                                    />
                                                </td>
                                            )}
                                            <td>{g.camera_name || <span className="muted">Unknown camera</span>}</td>
                                            <td className="nowrap">
                                                {g.binning}
                                                {g.pixel_um && <div className="muted small">{g.pixel_um} µm</div>}
                                            </td>
                                            <td className="nowrap">
                                                {formatFocal(g)}
                                                {g.focal_basis === 'header' && <div className="muted small">from header</div>}
                                            </td>
                                            <td className="nowrap">
                                                <Link to={bucketSearchLink(g)} title={`Show the ${g.count} images in this bucket`}>{g.count}</Link>
                                                <div className="muted small">
                                                    {g.master_count ? `${g.sub_count} subs · ${g.master_count} masters` : 'subs'}
                                                </div>
                                                <div className="muted small">{formatHours(g.total_exposure_s)}</div>
                                            </td>
                                            <td className="small">
                                                {g.frame_sizes.map((d) => <div key={d}>{d.replace('x', '×')}</div>)}
                                                {g.frame_size_count > g.frame_sizes.length && (
                                                    <div className="muted">+{g.frame_size_count - g.frame_sizes.length} more</div>
                                                )}
                                            </td>
                                            <td>
                                                <div className="chip-row">
                                                    {g.filters.map((f) => <span key={f} className="chip">{f.replace(/^Other:/, '')}</span>)}
                                                </div>
                                            </td>
                                            <td className="small">
                                                {g.first_capture ? formatDateTime(g.first_capture) : '—'}
                                                {g.last_capture && g.last_capture !== g.first_capture && (
                                                    <div className="muted">to {formatDateTime(g.last_capture)}</div>
                                                )}
                                            </td>
                                            <td className="small">{REASON_TEXT[g.reason] || g.reason}</td>
                                            <td>
                                                <select
                                                    className="input unassigned-rig-select"
                                                    value={rig}
                                                    onChange={(e) => chooseRig(g, e.target.value)}
                                                    disabled={!isAdmin || busy}
                                                >
                                                    <option value="">Choose rig…</option>
                                                    {rigs.map((r) => <option key={r.id} value={String(r.id)}>{rigOptionLabel(r)}</option>)}
                                                </select>
                                                {isSuggested && (
                                                    <div className="muted small">{SUGGESTION_TEXT[g.suggestion_basis] || 'Suggested'}</div>
                                                )}
                                            </td>
                                            <td>
                                                <Button
                                                    variant="plain"
                                                    size="sm"
                                                    to={bucketSearchLink(g)}
                                                    title={`Show the ${g.count} images in this bucket`}
                                                >
                                                    View images
                                                </Button>
                                            </td>
                                        </tr>
                                    );
                                })}
                            </tbody>
                        </table>
                    </div>
                </>
            )}
        </div>
    );
}

export default function Equipment() {
    const { user } = useAuth();
    const isAdmin = !!user?.is_admin;
    const queryClient = useQueryClient();
    const [activeTab, setActiveTab] = useState('rigs');
    const [expandedRigId, setExpandedRigId] = useState(null);
    const [rigSearch, setRigSearch] = useState('');
    const [rigFilter, setRigFilter] = useState('all');
    const toast = useToast();
    const confirm = useConfirm();
    // Adapter keeping the (message, type, durationMs) call shape used across this page's sections.
    const showToast = (message, type = 'info', durationMs) => toast.show({ message, type, durationMs });

    const [rigModal, setRigModal] = useState(null); // { rig: null|Rig }
    const [cameraModal, setCameraModal] = useState(null);
    const [opticModal, setOpticModal] = useState(null);
    const [filterModal, setFilterModal] = useState(null);
    const [siteModal, setSiteModal] = useState(null);
    const [reviewOpen, setReviewOpen] = useState(false);

    const equipmentQuery = useQuery({
        queryKey: ['equipment'],
        queryFn: fetchEquipment,
        staleTime: 60 * 1000,
    });
    const sitesQuery = useQuery({
        queryKey: ['sites'],
        queryFn: fetchSites,
        staleTime: 60 * 1000,
    });
    const detectQuery = useQuery({
        queryKey: ['equipmentDetect'],
        queryFn: () => fetchEquipmentDetect(false),
        staleTime: 5 * 60 * 1000,
    });

    function invalidateAll() {
        queryClient.invalidateQueries({ queryKey: ['equipment'] });
        queryClient.invalidateQueries({ queryKey: ['sites'] });
        queryClient.invalidateQueries({ queryKey: ['equipmentDetect'] });
    }

    const data = equipmentQuery.data;
    const sites = sitesQuery.data || [];

    const detectedRigCount = useMemo(
        () => (detectQuery.data?.rigs || []).filter((p) => !p.exists).length,
        [detectQuery.data],
    );
    const totalNewProposals = useMemo(() => {
        if (!detectQuery.data) return 0;
        return ['cameras', 'optics', 'filters', 'rigs', 'sites'].reduce(
            (sum, k) => sum + (detectQuery.data[k] || []).filter((p) => !p.exists).length,
            0,
        );
    }, [detectQuery.data]);
    const hasDetected = totalNewProposals > 0;

    async function handleAssignImages() {
        try {
            const result = await triggerEquipmentAssign('unassigned');
            showToast(result.queued ? 'Assignment queued in background.' : 'No images to assign.', 'success');
        } catch (err) {
            showToast(`Failed to queue assignment: ${err.message}`, 'error');
        }
    }

    async function handleMountToggle(rig) {
        try {
            if (rig.is_mounted) await unmountRig(rig.id);
            else await mountRig(rig.id);
            invalidateAll();
        } catch (err) {
            showToast(`Failed to update mount: ${err.message}`, 'error');
        }
    }

    async function handleActiveToggle(rig) {
        try {
            await updateRig(rig.id, { is_active: !rig.is_active });
            invalidateAll();
        } catch (err) {
            showToast(`Failed to update rig: ${err.message}`, 'error');
        }
    }

    async function handleDeleteRig(rig) {
        if (!(await confirm({
            title: `Delete rig "${rig.name}"?`,
            description: 'Images using it keep their history but lose the rig link.',
            confirmLabel: 'Delete rig',
            destructive: true,
        }))) return;
        try {
            await deleteRig(rig.id);
            invalidateAll();
        } catch (err) {
            showToast(err.message, 'error', 6000);
        }
    }

    async function handleDeleteCamera(camera) {
        if (!(await confirm({
            title: `Delete camera "${camera.name}"?`,
            confirmLabel: 'Delete camera',
            destructive: true,
        }))) return;
        try {
            await deleteCamera(camera.id);
            invalidateAll();
        } catch (err) {
            showToast(err.message, 'error', 6000);
        }
    }

    async function handleDeleteOptic(optic) {
        if (!(await confirm({
            title: `Delete optic "${optic.name}"?`,
            confirmLabel: 'Delete optic',
            destructive: true,
        }))) return;
        try {
            await deleteOptic(optic.id);
            invalidateAll();
        } catch (err) {
            showToast(err.message, 'error', 6000);
        }
    }

    async function handleDeleteFilter(filter) {
        if (!(await confirm({
            title: `Delete filter "${filter.name}"?`,
            confirmLabel: 'Delete filter',
            destructive: true,
        }))) return;
        try {
            await deleteFilter(filter.id);
            invalidateAll();
        } catch (err) {
            showToast(err.message, 'error', 6000);
        }
    }

    async function handleDeleteSite(site) {
        if (!(await confirm({
            title: `Delete site "${site.name}"?`,
            confirmLabel: 'Delete site',
            destructive: true,
        }))) return;
        try {
            await deleteSite(site.id);
            invalidateAll();
        } catch (err) {
            showToast(err.message, 'error', 6000);
        }
    }

    if (equipmentQuery.isLoading) {
        return (
            <div className="page-equipment loading-state">
                <Spinner />
                <p>Loading equipment…</p>
            </div>
        );
    }

    if (equipmentQuery.isError) {
        return (
            <EmptyState
                className="page-equipment"
                title="Failed to load equipment"
                description={equipmentQuery.error?.message}
                action={<Button variant="filled" onClick={() => equipmentQuery.refetch()}>Retry</Button>}
            />
        );
    }

    const cameras = data?.cameras || [];
    const optics = data?.optics || [];
    const filters = data?.filters || [];
    const rigs = data?.rigs || [];
    const maxMounted = data?.max_mounted_rigs ?? 5;
    const mountedCount = rigs.filter((r) => r.is_mounted).length;
    const rigQuery = rigSearch.trim().toLowerCase();
    const rigFilterTest = RIG_FILTERS.find((f) => f.key === rigFilter).test;
    const visibleRigs = sortRigs(rigs.filter((r) => rigFilterTest(r) && (!rigQuery
        || [r.name, r.camera_name, r.optic_name].some((v) => (v || '').toLowerCase().includes(rigQuery)))));

    return (
        <div className="page-equipment">
            <PageHeader title="Equipment & Sites" subtitle="Rigs, cameras, optics, filters and observing sites" />

            {hasDetected && (
                <div className="detect-banner">
                    <Sparkles size={18} />
                    <span>AstroCat found {detectedRigCount || totalNewProposals} setup{(detectedRigCount || totalNewProposals) === 1 ? '' : 's'} in your library.</span>
                    <Button variant="filled" size="sm" onClick={() => setReviewOpen(true)}>Review</Button>
                </div>
            )}

            <div className="equip-tabs">
                <Tabs className="equip-tabs-list" aria-label="Equipment" items={TABS} value={activeTab} onChange={setActiveTab} panelIdPrefix="equip" />
                {activeTab === 'rigs' && (
                    <Button size="sm" icon={<RefreshCw size={14} />} className="assign-now-btn" onClick={handleAssignImages} disabled={!isAdmin}>
                        Assign images now
                    </Button>
                )}
            </div>

            <TabPanel panelIdPrefix="equip" value={activeTab} className="equip-panel">
            {activeTab === 'rigs' && (
                rigs.length === 0 ? (
                    <EmptyState
                        icon={<TelescopeIcon strokeWidth={1.5} />}
                        title="No rigs yet"
                        description="Add a rig manually, or review AstroCat's detected setups above once your library has been scanned."
                        action={(
                            <Button variant="filled" icon={<Plus size={16} />} onClick={() => setRigModal({ rig: null })} disabled={!isAdmin || cameras.length === 0 || optics.length === 0}>
                                Add rig
                            </Button>
                        )}
                    />
                ) : (
                    <>
                    <p className="muted small mount-summary">
                        {mountedCount} of {maxMounted} rigs mounted. Tonight plans a separate target for each mounted rig.
                    </p>
                    <div className="rigs-toolbar">
                        <input
                            type="search"
                            className="rigs-search"
                            placeholder="Search rig, camera or optic"
                            value={rigSearch}
                            onChange={(e) => setRigSearch(e.target.value)}
                        />
                        <div className="rigs-filter-chips">
                            {RIG_FILTERS.map((f) => (
                                <button
                                    key={f.key}
                                    className={`chip selectable-chip${rigFilter === f.key ? ' selected' : ''}`}
                                    onClick={() => setRigFilter(f.key)}
                                >
                                    {f.label} {rigs.filter(f.test).length}
                                </button>
                            ))}
                        </div>
                        <Button size="sm" icon={<Plus size={14} />} className="rigs-add-btn" onClick={() => setRigModal({ rig: null })} disabled={!isAdmin}>
                            Add rig
                        </Button>
                    </div>
                    <div className="rig-list">
                        {visibleRigs.length === 0 && <p className="muted small rig-list-empty">No rigs match.</p>}
                        {visibleRigs.map((rig) => (
                            <RigRow
                                key={rig.id}
                                rig={rig}
                                isAdmin={isAdmin}
                                mountLimit={mountedCount >= maxMounted ? maxMounted : null}
                                expanded={expandedRigId === rig.id}
                                onToggleExpand={(id) => setExpandedRigId((cur) => (cur === id ? null : id))}
                                onEdit={(r) => setRigModal({ rig: r })}
                                onDelete={handleDeleteRig}
                                onMountToggle={handleMountToggle}
                                onActiveToggle={handleActiveToggle}
                            />
                        ))}
                    </div>
                    <UnassignedImagesSection rigs={rigs} isAdmin={isAdmin} showToast={showToast} />
                    </>
                )
            )}

            {activeTab === 'cameras' && (
                <div className="equip-grid">
                    {cameras.map((c) => (
                        <div key={c.id} className="equip-card">
                            <div className="rig-card-header"><span className="rig-name">{c.name}</span><span className="badge">{c.source}</span></div>
                            <div className="rig-line muted small">
                                {c.maker || 'Unknown maker'} · {c.sensor_width_px && c.sensor_height_px ? `${c.sensor_width_px}×${c.sensor_height_px}px` : 'dims unknown'} · {c.pixel_size_um ? `${c.pixel_size_um}µm` : 'pixel size unknown'}
                            </div>
                            <div className="rig-line muted small">Used by {c.rig_count} rig{c.rig_count === 1 ? '' : 's'}</div>
                            <div className="equip-card-actions">
                                <Button variant="plain" size="sm" icon={<Pencil size={14} />} onClick={() => setCameraModal({ camera: c })} disabled={!isAdmin}>Edit</Button>
                                <Button variant="destructive" size="sm" icon={<Trash2 size={14} />} onClick={() => handleDeleteCamera(c)} disabled={!isAdmin}>Delete</Button>
                            </div>
                        </div>
                    ))}
                    <Button className="add-card-btn" icon={<Plus size={16} />} onClick={() => setCameraModal({ camera: null })} disabled={!isAdmin}>Add camera</Button>
                </div>
            )}

            {activeTab === 'optics' && (
                <div className="equip-grid">
                    {optics.map((o) => (
                        <div key={o.id} className="equip-card">
                            <div className="rig-card-header"><span className="rig-name">{o.name}</span><span className="badge">{o.source}</span></div>
                            <div className="rig-line muted small">{o.kind} · {o.focal_length_mm}mm{o.aperture_mm ? ` · f/${(o.focal_length_mm / o.aperture_mm).toFixed(1)}` : ''}</div>
                            <div className="rig-line muted small">Used by {o.rig_count} rig{o.rig_count === 1 ? '' : 's'}</div>
                            <div className="equip-card-actions">
                                <Button variant="plain" size="sm" icon={<Pencil size={14} />} onClick={() => setOpticModal({ optic: o })} disabled={!isAdmin}>Edit</Button>
                                <Button variant="destructive" size="sm" icon={<Trash2 size={14} />} onClick={() => handleDeleteOptic(o)} disabled={!isAdmin}>Delete</Button>
                            </div>
                        </div>
                    ))}
                    <Button className="add-card-btn" icon={<Plus size={16} />} onClick={() => setOpticModal({ optic: null })} disabled={!isAdmin}>Add optic</Button>
                </div>
            )}

            {activeTab === 'filters' && (
                <div className="equip-grid">
                    {filters.map((f) => (
                        <div key={f.id} className="equip-card">
                            <div className="rig-card-header"><span className="rig-name">{f.name}</span><span className="badge">{f.band}</span></div>
                            <div className="rig-line muted small">{f.bandwidth_nm ? `${f.bandwidth_nm}nm` : 'bandwidth unknown'} · {f.source}</div>
                            <div className="equip-card-actions">
                                <Button variant="plain" size="sm" icon={<Pencil size={14} />} onClick={() => setFilterModal({ filter: f })} disabled={!isAdmin}>Edit</Button>
                                <Button variant="destructive" size="sm" icon={<Trash2 size={14} />} onClick={() => handleDeleteFilter(f)} disabled={!isAdmin}>Delete</Button>
                            </div>
                        </div>
                    ))}
                    <Button className="add-card-btn" icon={<Plus size={16} />} onClick={() => setFilterModal({ filter: null })} disabled={!isAdmin}>Add filter</Button>
                </div>
            )}

            {activeTab === 'sites' && (
                <SitesTab
                    sites={sites}
                    isAdmin={isAdmin}
                    onEdit={(s) => setSiteModal({ site: s })}
                    onDelete={handleDeleteSite}
                    onAdd={() => setSiteModal({ site: null })}
                    showToast={showToast}
                    refetchSites={() => {
                        queryClient.invalidateQueries({ queryKey: ['sites'] });
                        queryClient.invalidateQueries({ queryKey: ['equipment'] });
                    }}
                />
            )}
            </TabPanel>

            {rigModal && (
                <RigModal
                    rig={rigModal.rig}
                    cameras={cameras}
                    optics={optics}
                    filters={filters}
                    onClose={() => setRigModal(null)}
                    onSave={() => { setRigModal(null); invalidateAll(); }}
                />
            )}
            {cameraModal && (
                <CameraModal
                    camera={cameraModal.camera}
                    onClose={() => setCameraModal(null)}
                    onSave={() => { setCameraModal(null); invalidateAll(); }}
                />
            )}
            {opticModal && (
                <OpticModal
                    optic={opticModal.optic}
                    onClose={() => setOpticModal(null)}
                    onSave={() => { setOpticModal(null); invalidateAll(); }}
                />
            )}
            {filterModal && (
                <FilterModal
                    filter={filterModal.filter}
                    onClose={() => setFilterModal(null)}
                    onSave={() => { setFilterModal(null); invalidateAll(); }}
                />
            )}
            {siteModal && (
                <SiteModal
                    site={siteModal.site}
                    onClose={() => setSiteModal(null)}
                    onSave={() => { setSiteModal(null); invalidateAll(); }}
                />
            )}
            {reviewOpen && detectQuery.data && (
                <DetectReviewPanel
                    detect={detectQuery.data}
                    existingOptics={optics}
                    telescopiusAvailable={!!data?.telescopius_available}
                    onClose={() => setReviewOpen(false)}
                    onApplied={() => { setReviewOpen(false); invalidateAll(); }}
                    isAdmin={isAdmin}
                    showToast={showToast}
                />
            )}
        </div>
    );
}
