import { useEffect, useState, useCallback } from 'react';
import { Globe } from 'lucide-react';
import { useQueryClient } from '@tanstack/react-query';
import { fetchOnlineCatalogsAdmin, testOnlineCatalog, updateSettings } from '../../api/client';
import { Button, useToast } from '../ui';

// O1 Admin section: which online catalogs the Annotations overlay may query
// (docs/design/20261009-O1-online-catalog-overlays.md). All are off by default;
// enabled ones appear in every viewer's overlay legend.

const LIMIT_TEXT = {
    mag: { label: 'Faintest mag', placeholder: 'none', step: 0.5 },
    size: { label: 'Min size (′)', placeholder: 'auto', step: 0.1 },
};

function Toggle({ checked, onChange, disabled, label }) {
    return (
        <div className="toggle-group" style={{ display: 'flex', gap: '0.5rem', background: 'var(--color-border)', padding: '0.25rem', borderRadius: '0.5rem' }} role="group" aria-label={label}>
            <button type="button" className={`btn btn-sm ${checked ? 'btn-primary' : 'btn-ghost'}`} onClick={() => onChange(true)} disabled={disabled} aria-pressed={checked}>On</button>
            <button type="button" className={`btn btn-sm ${!checked ? 'btn-primary' : 'btn-ghost'}`} onClick={() => onChange(false)} disabled={disabled} aria-pressed={!checked}>Off</button>
        </div>
    );
}

function LimitInput({ catalog, value, disabled, onCommit }) {
    const meta = LIMIT_TEXT[catalog.limit_kind];
    const [text, setText] = useState(value ?? '');
    if (!meta) return null;
    const placeholder = catalog.default_limit != null ? String(catalog.default_limit) : meta.placeholder;
    const commit = () => {
        const v = text === '' ? null : Number(text);
        if (v !== null && !Number.isFinite(v)) return;
        if (v !== (value ?? null)) onCommit(v);
    };
    return (
        <label className="text-sm text-muted" style={{ display: 'flex', alignItems: 'center', gap: '0.4rem' }}>
            {meta.label}
            <input type="number" className="input" step={meta.step} min="0" value={text} placeholder={placeholder}
                disabled={disabled} onChange={(e) => setText(e.target.value)} onBlur={commit}
                onKeyDown={(e) => { if (e.key === 'Enter') e.currentTarget.blur(); }}
                style={{ width: '80px', background: 'var(--color-border)', border: '1px solid var(--color-border-light)', color: 'var(--color-text-primary)', padding: '0.25rem 0.5rem', borderRadius: '0.25rem' }} />
        </label>
    );
}

export default function OnlineCatalogsAdmin({ systemSettings, onSettingsChange }) {
    const toast = useToast();
    const queryClient = useQueryClient();
    const [catalogs, setCatalogs] = useState(null);
    const [saving, setSaving] = useState(false);
    const [tests, setTests] = useState({});

    const load = useCallback(() => fetchOnlineCatalogsAdmin().then(setCatalogs)
        .catch((err) => toast.error('Failed to load online catalogs: ' + err.message)), [toast]);
    useEffect(() => { load(); }, [load]);

    async function save(key, patch) {
        const current = systemSettings.online_catalogs || {};
        const entry = { enabled: false, limit: null, ...current[key], ...patch };
        setSaving(true);
        try {
            onSettingsChange(await updateSettings({ ...systemSettings, online_catalogs: { ...current, [key]: entry } }));
            setCatalogs((cs) => cs.map((c) => (c.key === key ? { ...c, ...entry } : c)));
            queryClient.invalidateQueries({ queryKey: ['sky-online-catalogs'] });
            queryClient.invalidateQueries({ queryKey: ['sky-overlay-online'] });
        } catch (err) {
            toast.error('Failed to update settings: ' + err.message);
        } finally {
            setSaving(false);
        }
    }

    async function runTest(key) {
        setTests((t) => ({ ...t, [key]: { running: true } }));
        try {
            const res = await testOnlineCatalog(key);
            setTests((t) => ({ ...t, [key]: res }));
        } catch (err) {
            setTests((t) => ({ ...t, [key]: { ok: false, error: err.message } }));
        }
    }

    const groups = [];
    for (const c of catalogs || []) {
        let g = groups.find((x) => x.name === c.group);
        if (!g) groups.push(g = { name: c.group, items: [] });
        g.items.push(c);
    }

    return (
        <section className="settings-section">
            <h2 className="section-title"><Globe size={18} aria-hidden="true" style={{ verticalAlign: '-3px', marginRight: '0.4rem' }} /> Online Catalogs</h2>
            <p className="text-muted text-sm" style={{ margin: '0 0 1rem' }}>
                Extra Annotations layers looked up live for the image being viewed. Each enabled catalog appears in the overlay
                legend for everyone; results are cached for 30 days, so each field is only fetched once. Off by default:
                nothing is sent to these services until a catalog is switched on.
            </p>
            {!catalogs && <div className="card text-muted text-sm" style={{ padding: '1rem' }}>Loading…</div>}
            {groups.map((g) => (
                <div key={g.name} style={{ marginBottom: '1.25rem' }}>
                    <h3 className="text-sm" style={{ margin: '0 0 0.5rem', color: 'var(--color-text-secondary)', textTransform: 'uppercase', letterSpacing: '0.05em' }}>{g.name}</h3>
                    <div className="card">
                        {g.items.map((c, i) => {
                            const test = tests[c.key];
                            return (
                                <div key={c.key} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem', padding: '1rem', flexWrap: 'wrap', borderTop: i ? '1px solid var(--color-border)' : 'none' }}>
                                    <div style={{ flex: '1 1 280px', minWidth: 0 }}>
                                        <div style={{ fontWeight: 'bold' }}>{c.label}</div>
                                        <div className="text-muted text-sm" style={{ marginTop: '0.25rem' }}>{c.description}</div>
                                        <div className="text-sm" style={{ marginTop: '0.25rem' }}>
                                            <a href={c.source_url} target="_blank" rel="noopener noreferrer">{c.source_name}</a>
                                            {test && (
                                                <span style={{ marginLeft: '0.75rem', color: test.running ? 'var(--color-text-secondary)' : test.ok ? 'var(--color-success)' : 'var(--color-error)' }}>
                                                    {test.running ? 'Testing…'
                                                        : test.ok ? `OK: ${test.rows} objects in ${test.seconds}s`
                                                            : `Failed: ${test.error}`}
                                                </span>
                                            )}
                                        </div>
                                    </div>
                                    <div style={{ display: 'flex', alignItems: 'center', gap: '0.75rem', flexWrap: 'wrap' }}>
                                        <LimitInput key={`${c.key}:${c.limit}`} catalog={c} value={c.limit} disabled={saving}
                                            onCommit={(v) => save(c.key, { limit: v })} />
                                        <Button size="sm" variant="plain" onClick={() => runTest(c.key)} disabled={test?.running}>Test</Button>
                                        <Toggle label={c.label} checked={!!c.enabled} disabled={saving}
                                            onChange={(v) => save(c.key, { enabled: v })} />
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                </div>
            ))}
            {catalogs && (
                <p className="text-muted text-sm">
                    Data courtesy of CDS VizieR (Strasbourg) and IMCCE SkyBoT (Paris Observatory). Asteroid positions are
                    geocentric, at the middle of the exposure.
                </p>
            )}
        </section>
    );
}
