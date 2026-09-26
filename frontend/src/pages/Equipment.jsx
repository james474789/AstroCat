import { useState, useMemo } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
    Camera as CameraIcon, Aperture, SlidersHorizontal, MapPin,
    Plus, Pencil, Trash2, Sparkles, Upload, Download, RefreshCw,
} from 'lucide-react';
import {
    LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts';
import { useAuth } from '../context/AuthContext';
import TelescopeIcon from '../components/icons/TelescopeIcon';
import {
    fetchEquipment, fetchEquipmentDetect, applyEquipmentDetect, importFromTelescopius,
    triggerEquipmentAssign,
    createCamera, updateCamera, deleteCamera,
    createOptic, updateOptic, deleteOptic,
    createFilter, updateFilter, deleteFilter,
    createRig, updateRig, deleteRig, mountRig, unmountRig,
    fetchSites, createSite, updateSite, deleteSite,
    fetchLearnedHorizon, updateSiteHorizon, importSiteHorizon, exportSiteHorizon,
    computePixelScale, computeFovDeg, computeFocalRatio,
    formatDateTime,
} from '../api/client';
import './Equipment.css';

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
    { key: 'rigs', label: 'Rigs', icon: TelescopeIcon },
    { key: 'cameras', label: 'Cameras', icon: CameraIcon },
    { key: 'optics', label: 'Optics', icon: Aperture },
    { key: 'filters', label: 'Filters', icon: SlidersHorizontal },
    { key: 'sites', label: 'Sites', icon: MapPin },
];

function formatArcsec(v) {
    return v == null ? '—' : `${v.toFixed(2)}″`;
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
    if (check.verdict === 'ok') return `measured ${check.measured.toFixed(2)}″ ✓`;
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

function ModalShell({ title, onClose, children, wide }) {
    return (
        <div className="modal-overlay" onClick={onClose}>
            <div className={`modal-content equipment-modal${wide ? ' wide' : ''}`} onClick={(e) => e.stopPropagation()}>
                <header className="modal-header">
                    <h2>{title}</h2>
                    <button className="close-button" onClick={onClose}>×</button>
                </header>
                <div className="modal-body">{children}</div>
            </div>
        </div>
    );
}

// ============ Toast (page-local, matches Admin.jsx pattern) ============

function useToast() {
    const [toast, setToast] = useState(null);
    function showToast(message, type = 'info', durationMs = 4000) {
        setToast({ message, type });
        if (durationMs > 0) setTimeout(() => setToast(null), durationMs);
    }
    return [toast, showToast];
}

// ============ Rig card ============

function RigCard({ rig, isAdmin, onEdit, onDelete, onMountToggle, onActiveToggle, onAssign }) {
    const scaleCheckMsg = scaleCheckText(rig.scale_check);
    return (
        <div className={`equip-card rig-card${rig.is_active ? '' : ' inactive'}`}>
            <div className="rig-card-header">
                <div className="rig-card-title">
                    <span className="rig-name">{rig.name}</span>
                    <button
                        className={`pill-toggle${rig.is_mounted ? ' active' : ''}`}
                        onClick={() => onMountToggle(rig)}
                        disabled={!isAdmin}
                        title={rig.is_mounted ? 'Mounted (click to unmount)' : 'Click to mount (unmounts any other rig)'}
                    >
                        {rig.is_mounted ? `Mounted${rig.mount_name ? ` · ${rig.mount_name}` : ''}` : 'Not mounted'}
                    </button>
                </div>
                <label className="active-toggle" title="Active">
                    <input
                        type="checkbox"
                        checked={rig.is_active}
                        onChange={() => onActiveToggle(rig)}
                        disabled={!isAdmin}
                    />
                    Active
                </label>
            </div>

            <div className="rig-card-body">
                <div className="rig-line">
                    {rig.camera_name} · {rig.optic_name}
                    {rig.modifier_name ? ` · ${rig.modifier_name}` : ''}
                </div>
                <div className="rig-line muted">
                    {formatArcsec(rig.scale)} /px · {formatFov(rig.fov_deg)} · f/{rig.focal_ratio != null ? rig.focal_ratio.toFixed(1) : '—'}
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
                        <span className={`badge ${samplingClass(rig.sampling.verdict)}`}>
                            {samplingLabel(rig.sampling.verdict)}
                        </span>
                    )}
                    {scaleCheckMsg && (
                        <span className={`badge ${rig.scale_check.verdict === 'ok' ? 'badge-success' : 'badge-warning'}`}>
                            {scaleCheckMsg}
                        </span>
                    )}
                </div>
                <div className="rig-line muted small">
                    {rig.image_count} subs · last used {rig.last_used ? formatDateTime(rig.last_used) : 'never'}
                </div>
            </div>

            <div className="equip-card-actions">
                <button className="btn btn-ghost btn-sm" onClick={() => onAssign(rig)} disabled={!isAdmin} title="Assign images now">
                    <RefreshCw size={14} /> Assign images
                </button>
                <button className="btn btn-ghost btn-sm" onClick={() => onEdit(rig)} disabled={!isAdmin}>
                    <Pencil size={14} /> Edit
                </button>
                <button className="btn btn-ghost btn-sm danger" onClick={() => onDelete(rig)} disabled={!isAdmin}>
                    <Trash2 size={14} /> Delete
                </button>
            </div>
        </div>
    );
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
        <ModalShell title={rig ? 'Edit Rig' : 'Add Rig'} onClose={onClose}>
            <form onSubmit={handleSubmit} className="equip-form">
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

                <div className="modal-footer">
                    <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                    <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
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
        <ModalShell title={camera ? 'Edit Camera' : 'Add Camera'} onClose={onClose}>
            <form onSubmit={handleSubmit} className="equip-form">
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
                <div className="modal-footer">
                    <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                    <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
                </div>
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
        <ModalShell title={optic ? 'Edit Optic' : 'Add Optic'} onClose={onClose}>
            <form onSubmit={handleSubmit} className="equip-form">
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
                <div className="modal-footer">
                    <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                    <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
                </div>
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
        <ModalShell title={filter ? 'Edit Filter' : 'Add Filter'} onClose={onClose}>
            <form onSubmit={handleSubmit} className="equip-form">
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
                <div className="modal-footer">
                    <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                    <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
                </div>
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
        <ModalShell title={site ? 'Edit Site' : 'Add Site'} onClose={onClose}>
            <form onSubmit={handleSubmit} className="equip-form">
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
                <div className="modal-footer">
                    <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                    <button type="submit" className="btn btn-primary" disabled={saving}>{saving ? 'Saving…' : 'Save'}</button>
                </div>
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

    function toggleAccept(id) {
        setAccepted((prev) => ({ ...prev, [id]: !prev[id] }));
    }

    function updateEdit(id, patch) {
        setEdits((prev) => ({ ...prev, [id]: { ...prev[id], ...patch } }));
    }

    async function handleImportTelescopius() {
        setImporting(true);
        try {
            const result = await importFromTelescopius();
            const summary = ['cameras', 'optics', 'filters']
                .map((k) => `${k}: +${result[k].created}/${result[k].updated}~/${result[k].skipped} skip`)
                .join(', ');
            showToast(`Telescopius import done — ${summary}`, 'success', 6000);
        } catch (err) {
            showToast(`Telescopius import failed: ${err.message}`, 'error', 6000);
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
        <ModalShell title="Review detected setups" onClose={onClose} wide>
            {error && <div className="form-error">{error}</div>}

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

            <div className="modal-footer">
                {telescopiusAvailable && (
                    <button type="button" className="btn btn-secondary" onClick={handleImportTelescopius} disabled={importing || !isAdmin}>
                        {importing ? 'Importing…' : 'Import from Telescopius'}
                    </button>
                )}
                <button type="button" className="btn btn-secondary" onClick={onClose}>Cancel</button>
                <button type="button" className="btn btn-primary" onClick={handleApply} disabled={applying || !isAdmin}>
                    {applying ? 'Applying…' : 'Apply'}
                </button>
            </div>
        </ModalShell>
    );
}

// ============ Sites tab ============

function SitesTab({ sites, isAdmin, onEdit, onDelete, onAdd, showToast, refetchSites }) {
    const [selectedSiteId, setSelectedSiteId] = useState(null);
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
            <div className="empty-state">
                <div className="empty-state-icon">🗺️</div>
                <h3 className="empty-state-title">No sites yet</h3>
                <p className="empty-state-text">Add an observing site to enable horizon-aware planning.</p>
                <button className="btn btn-primary" onClick={onAdd} disabled={!isAdmin}><Plus size={16} /> Add site</button>
            </div>
        );
    }

    return (
        <div className="sites-tab">
            <div className="equip-list">
                {sites.map((s) => (
                    <div key={s.id} className={`equip-card site-card${s.id === effectiveSiteId ? ' selected' : ''}`} onClick={() => setSelectedSiteId(s.id)}>
                        <div className="rig-card-header">
                            <span className="rig-name">{s.name}{s.is_default && <span className="badge badge-primary" style={{ marginLeft: '0.5rem' }}>Default</span>}</span>
                        </div>
                        <div className="rig-line muted small">{s.timezone} · Bortle {s.bortle ?? '—'} · seeing {s.typical_seeing_arcsec}″ · {s.image_count} subs</div>
                        <div className="equip-card-actions">
                            <button className="btn btn-ghost btn-sm" onClick={(e) => { e.stopPropagation(); onEdit(s); }} disabled={!isAdmin}><Pencil size={14} /> Edit</button>
                            <button className="btn btn-ghost btn-sm danger" onClick={(e) => { e.stopPropagation(); onDelete(s); }} disabled={!isAdmin}><Trash2 size={14} /> Delete</button>
                        </div>
                    </div>
                ))}
                <button className="btn btn-secondary add-card-btn" onClick={onAdd} disabled={!isAdmin}><Plus size={16} /> Add site</button>
            </div>

            {selectedSite && (
                <div className="horizon-panel">
                    <div className="horizon-header">
                        <h3>Horizon — {selectedSite.name}</h3>
                        <div className="horizon-actions">
                            <button className="btn btn-secondary btn-sm" onClick={handleUseLearned} disabled={!isAdmin || !learnedQuery.data?.points?.length}>Use learned</button>
                            <label className="btn btn-secondary btn-sm file-btn">
                                <Upload size={14} /> Import .hrz
                                <input type="file" accept=".hrz,.txt" onChange={handleImportFile} disabled={!isAdmin} hidden />
                            </label>
                            <button className="btn btn-secondary btn-sm" onClick={handleExport} disabled={!selectedSite.horizon}><Download size={14} /> Export .hrz</button>
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
                        <p className="empty-state-text">No horizon data yet. Import a .hrz file, or use the learned profile once enough subs have been captured at this site.</p>
                    )}
                </div>
            )}
        </div>
    );
}

// ============ Main page ============

export default function Equipment() {
    const { user } = useAuth();
    const isAdmin = !!user?.is_admin;
    const queryClient = useQueryClient();
    const [activeTab, setActiveTab] = useState('rigs');
    const [toast, showToast] = useToast();

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
        if (!window.confirm(`Delete rig "${rig.name}"? Images using it keep their history but lose the rig link.`)) return;
        try {
            await deleteRig(rig.id);
            invalidateAll();
        } catch (err) {
            showToast(err.message, 'error', 6000);
        }
    }

    async function handleDeleteCamera(camera) {
        if (!window.confirm(`Delete camera "${camera.name}"?`)) return;
        try {
            await deleteCamera(camera.id);
            invalidateAll();
        } catch (err) {
            showToast(err.message, 'error', 6000);
        }
    }

    async function handleDeleteOptic(optic) {
        if (!window.confirm(`Delete optic "${optic.name}"?`)) return;
        try {
            await deleteOptic(optic.id);
            invalidateAll();
        } catch (err) {
            showToast(err.message, 'error', 6000);
        }
    }

    async function handleDeleteFilter(filter) {
        if (!window.confirm(`Delete filter "${filter.name}"?`)) return;
        try {
            await deleteFilter(filter.id);
            invalidateAll();
        } catch (err) {
            showToast(err.message, 'error', 6000);
        }
    }

    async function handleDeleteSite(site) {
        if (!window.confirm(`Delete site "${site.name}"?`)) return;
        try {
            await deleteSite(site.id);
            invalidateAll();
        } catch (err) {
            showToast(err.message, 'error', 6000);
        }
    }

    if (equipmentQuery.isLoading) {
        return (
            <div className="loading-state">
                <div className="spinner" />
                <p>Loading equipment…</p>
            </div>
        );
    }

    if (equipmentQuery.isError) {
        return (
            <div className="empty-state">
                <h3 className="empty-state-title">Failed to load equipment</h3>
                <p className="empty-state-text">{equipmentQuery.error?.message}</p>
                <button className="btn btn-primary" onClick={() => equipmentQuery.refetch()}>Retry</button>
            </div>
        );
    }

    const cameras = data?.cameras || [];
    const optics = data?.optics || [];
    const filters = data?.filters || [];
    const rigs = data?.rigs || [];

    return (
        <div className="equipment-page">
            {toast && (
                <div className={`equip-toast ${toast.type}`} role="status" aria-live="polite">{toast.message}</div>
            )}

            <div className="page-header">
                <h1 className="page-title">Equipment &amp; Sites</h1>
                <p className="page-subtitle">Rigs, cameras, optics, filters and observing sites</p>
            </div>

            {hasDetected && (
                <div className="detect-banner">
                    <Sparkles size={18} />
                    <span>AstroCat found {detectedRigCount || totalNewProposals} setup{(detectedRigCount || totalNewProposals) === 1 ? '' : 's'} in your library.</span>
                    <button className="btn btn-primary btn-sm" onClick={() => setReviewOpen(true)}>Review</button>
                </div>
            )}

            <div className="equip-tabs">
                {TABS.map((tab) => {
                    const TabIcon = tab.icon;
                    return (
                        <button
                            key={tab.key}
                            className={`equip-tab${activeTab === tab.key ? ' active' : ''}`}
                            onClick={() => setActiveTab(tab.key)}
                        >
                            <TabIcon size={16} /> {tab.label}
                        </button>
                    );
                })}
                {activeTab === 'rigs' && (
                    <button className="btn btn-secondary btn-sm assign-now-btn" onClick={handleAssignImages} disabled={!isAdmin}>
                        <RefreshCw size={14} /> Assign images now
                    </button>
                )}
            </div>

            {activeTab === 'rigs' && (
                rigs.length === 0 ? (
                    <div className="empty-state">
                        <div className="empty-state-icon">🔭</div>
                        <h3 className="empty-state-title">No rigs yet</h3>
                        <p className="empty-state-text">Add a rig manually, or review AstroCat's detected setups above once your library has been scanned.</p>
                        <button className="btn btn-primary" onClick={() => setRigModal({ rig: null })} disabled={!isAdmin || cameras.length === 0 || optics.length === 0}>
                            <Plus size={16} /> Add rig
                        </button>
                    </div>
                ) : (
                    <div className="equip-grid">
                        {rigs.map((rig) => (
                            <RigCard
                                key={rig.id}
                                rig={rig}
                                isAdmin={isAdmin}
                                onEdit={(r) => setRigModal({ rig: r })}
                                onDelete={handleDeleteRig}
                                onMountToggle={handleMountToggle}
                                onActiveToggle={handleActiveToggle}
                                onAssign={handleAssignImages}
                            />
                        ))}
                        <button className="btn btn-secondary add-card-btn" onClick={() => setRigModal({ rig: null })} disabled={!isAdmin}>
                            <Plus size={16} /> Add rig
                        </button>
                    </div>
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
                                <button className="btn btn-ghost btn-sm" onClick={() => setCameraModal({ camera: c })} disabled={!isAdmin}><Pencil size={14} /> Edit</button>
                                <button className="btn btn-ghost btn-sm danger" onClick={() => handleDeleteCamera(c)} disabled={!isAdmin}><Trash2 size={14} /> Delete</button>
                            </div>
                        </div>
                    ))}
                    <button className="btn btn-secondary add-card-btn" onClick={() => setCameraModal({ camera: null })} disabled={!isAdmin}><Plus size={16} /> Add camera</button>
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
                                <button className="btn btn-ghost btn-sm" onClick={() => setOpticModal({ optic: o })} disabled={!isAdmin}><Pencil size={14} /> Edit</button>
                                <button className="btn btn-ghost btn-sm danger" onClick={() => handleDeleteOptic(o)} disabled={!isAdmin}><Trash2 size={14} /> Delete</button>
                            </div>
                        </div>
                    ))}
                    <button className="btn btn-secondary add-card-btn" onClick={() => setOpticModal({ optic: null })} disabled={!isAdmin}><Plus size={16} /> Add optic</button>
                </div>
            )}

            {activeTab === 'filters' && (
                <div className="equip-grid">
                    {filters.map((f) => (
                        <div key={f.id} className="equip-card">
                            <div className="rig-card-header"><span className="rig-name">{f.name}</span><span className="badge">{f.band}</span></div>
                            <div className="rig-line muted small">{f.bandwidth_nm ? `${f.bandwidth_nm}nm` : 'bandwidth unknown'} · {f.source}</div>
                            <div className="equip-card-actions">
                                <button className="btn btn-ghost btn-sm" onClick={() => setFilterModal({ filter: f })} disabled={!isAdmin}><Pencil size={14} /> Edit</button>
                                <button className="btn btn-ghost btn-sm danger" onClick={() => handleDeleteFilter(f)} disabled={!isAdmin}><Trash2 size={14} /> Delete</button>
                            </div>
                        </div>
                    ))}
                    <button className="btn btn-secondary add-card-btn" onClick={() => setFilterModal({ filter: null })} disabled={!isAdmin}><Plus size={16} /> Add filter</button>
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
                    refetchSites={() => queryClient.invalidateQueries({ queryKey: ['sites'] })}
                />
            )}

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
