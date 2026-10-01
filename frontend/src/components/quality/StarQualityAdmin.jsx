import { useEffect, useState, useCallback } from 'react';
import { fetchStarMetricsStatus, requestStarMetricsRemeasure, updateSettings } from '../../api/client';
import './Quality.css';

// Q1 Admin section: star-quality settings, backfill progress, re-measure actions
// (docs/design/20260927-Q1-star-quality.md §7.8, §7.9).

const COUNT_LABELS = [
    ['ok', 'measured'], ['no_stars', 'no stars'], ['skipped', 'not measurable'], ['hint', 'from capture software'],
    ['failed', 'failed'], ['pending', 'queued'], ['never', 'not yet measured'],
];

function Toggle({ checked, onChange, disabled, label }) {
    return (
        <div className="toggle-group" style={{ display: 'flex', gap: '0.5rem', background: '#2d3748', padding: '0.25rem', borderRadius: '0.5rem' }} role="group" aria-label={label}>
            <button type="button" className={`btn btn-sm ${checked ? 'btn-primary' : 'btn-ghost'}`} onClick={() => onChange(true)} disabled={disabled} aria-pressed={checked}>On</button>
            <button type="button" className={`btn btn-sm ${!checked ? 'btn-primary' : 'btn-ghost'}`} onClick={() => onChange(false)} disabled={disabled} aria-pressed={!checked}>Off</button>
        </div>
    );
}

function SettingRow({ title, description, children, first }) {
    return (
        <div className="setting-row" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem', padding: '1rem', flexWrap: 'wrap', borderTop: first ? 'none' : '1px solid #2d3748' }}>
            <div style={{ flex: '1 1 260px', minWidth: 0 }}>
                <div className="setting-label" style={{ fontWeight: 'bold' }}>{title}</div>
                <div className="setting-description text-muted text-sm" style={{ marginTop: '0.25rem' }}>{description}</div>
            </div>
            {children}
        </div>
    );
}

export default function StarQualityAdmin({ systemSettings, onSettingsChange }) {
    const [status, setStatus] = useState(null);
    const [saving, setSaving] = useState(false);
    const [message, setMessage] = useState('');

    const load = useCallback(() => fetchStarMetricsStatus().then(setStatus).catch(() => {}), []);

    useEffect(() => {
        load();
        const timer = setInterval(load, 10000);
        return () => clearInterval(timer);
    }, [load]);

    async function save(patch) {
        setSaving(true);
        try {
            onSettingsChange(await updateSettings({ ...systemSettings, ...patch }));
            load();
        } catch (err) {
            alert('Failed to update settings: ' + err.message);
        } finally {
            setSaving(false);
        }
    }

    async function remeasure(scope, confirmText) {
        if (confirmText && !confirm(confirmText)) return;
        try {
            const res = await requestStarMetricsRemeasure(scope);
            setMessage(`${res.queued_for_remeasure.toLocaleString()} images will be re-measured by the background sweep.`);
            load();
        } catch (err) {
            setMessage('Failed: ' + err.message);
        }
    }

    const envOff = status && ((systemSettings.star_metrics_enabled !== false && !status.settings.star_metrics_enabled)
        || (systemSettings.star_metrics_backfill !== false && !status.settings.star_metrics_backfill && status.settings.star_metrics_enabled));
    const pct = status && status.eligible ? Math.round((status.done / status.eligible) * 1000) / 10 : 0;
    const remaining = status ? status.counts.never + status.counts.pending : 0;
    // Prefer the actual measured-per-hour rate; fall back to a rough estimate
    // from the sweep batch size until enough recent history exists.
    const perDay = status ? (status.measured_per_hour ? status.measured_per_hour * 24 : status.sweep_batch * 6 * 24) : 0;
    const days = status && perDay ? remaining / perDay : null;

    return (
        <section className="settings-section">
            <h2 className="section-title">⭐ Star Quality (HFR / FWHM)</h2>
            <div className="card">
                <SettingRow first title="Default units"
                    description="How FWHM and HFR are shown until a viewer picks their own with the ″ / px switch (bottom of the sidebar).">
                    <div className="toggle-group" style={{ display: 'flex', gap: '0.5rem', background: '#2d3748', padding: '0.25rem', borderRadius: '0.5rem' }}>
                        <button type="button" className={`btn btn-sm ${systemSettings.quality_units !== 'PX' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => save({ quality_units: 'ARCSEC' })} disabled={saving}>Arcseconds</button>
                        <button type="button" className={`btn btn-sm ${systemSettings.quality_units === 'PX' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => save({ quality_units: 'PX' })} disabled={saving}>Pixels</button>
                    </div>
                </SettingRow>
                <SettingRow title="Measure new images"
                    description="Measure star size, shape and count for each Light sub or master as it is indexed. Runs in the background on its own queue.">
                    <Toggle label="Measure new images" checked={systemSettings.star_metrics_enabled !== false} disabled={saving}
                        onChange={(v) => save({ star_metrics_enabled: v })} />
                </SettingRow>
                <SettingRow title="Measure existing library"
                    description={`Work through images indexed before measuring existed, newest first, in batches of ${status ? status.sweep_batch : 200} that top the queue back up as soon as it runs short. Each image is read in full from storage.`}>
                    <Toggle label="Measure existing library" checked={systemSettings.star_metrics_backfill !== false} disabled={saving || systemSettings.star_metrics_enabled === false}
                        onChange={(v) => save({ star_metrics_backfill: v })} />
                </SettingRow>
                {envOff && (
                    <div style={{ padding: '0 1rem 1rem', color: '#fbbf24', fontSize: '0.85rem' }}>
                        Switched off by the server’s STAR_METRICS_* environment settings, which override these switches.
                    </div>
                )}

                <div style={{ padding: '1rem', borderTop: '1px solid #2d3748' }}>
                    <div className="setting-label" style={{ fontWeight: 'bold' }}>Progress</div>
                    {status ? (
                        <>
                            <div className="quality-progress" role="progressbar" aria-valuenow={pct} aria-valuemin={0} aria-valuemax={100}>
                                <div style={{ width: `${pct}%` }} />
                            </div>
                            <div className="text-sm" style={{ color: '#e2e8f0', marginBottom: '0.5rem' }}>
                                {status.done.toLocaleString()} of {status.eligible.toLocaleString()} Light images ({pct}%)
                                {remaining > 0 && days != null && status.settings.star_metrics_backfill && (
                                    <span className="text-muted"> · about {days < 1 ? `${Math.max(1, Math.round(days * 24))} h` : `${days.toFixed(1)} days`} to go</span>
                                )}
                                {status.queue_depth != null && <span className="text-muted"> · queue {status.queue_depth}</span>}
                            </div>
                            <div className="quality-counts">
                                {COUNT_LABELS.map(([k, label]) => status.counts[k] > 0 && (
                                    <span key={k}><b>{status.counts[k].toLocaleString()}</b> {label}</span>
                                ))}
                            </div>
                        </>
                    ) : <div className="text-muted text-sm" style={{ marginTop: '0.5rem' }}>Loading…</div>}
                    <div className="cache-actions" style={{ marginTop: '1rem', display: 'flex', gap: '0.75rem', flexWrap: 'wrap' }}>
                        <button type="button" className="btn btn-secondary" disabled={!status?.counts.failed}
                            onClick={() => remeasure('failed')}>Retry failed</button>
                        <button type="button" className="btn btn-secondary" disabled={!status?.counts.no_stars}
                            onClick={() => remeasure('no_stars')}>Re-check “no stars”</button>
                        <button type="button" className="btn btn-secondary" disabled={!status?.done}
                            onClick={() => remeasure('all', 'Re-measure every image? Current values stay visible until each one is redone. This re-reads the whole library from storage.')}>
                            Re-measure all
                        </button>
                    </div>
                    {message && <div className="text-sm text-muted" style={{ marginTop: '0.5rem' }}>{message}</div>}
                </div>
            </div>
        </section>
    );
}
