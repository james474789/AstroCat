import { useEffect, useRef, useState } from 'react';
import { RefreshCw } from 'lucide-react';
import { fetchImage, remeasureStarMetrics } from '../../api/client';
import { useQualityUnits } from '../../context/QualityUnitsContext';
import {
    STATUS_LABELS, SKIP_REASONS, SAMPLING_LABELS,
    formatSize, formatSecondary, formatDelta, deltaTone,
} from '../../utils/quality';
import QualityUnitsToggle from './QualityUnitsToggle';
import './Quality.css';

// Q1 "Star Quality" section for Image Detail (docs/design/Q1-star-quality.md §8.3).

const HINT_LABELS = {
    HFR: 'HFR', FWHM: 'FWHM', STARS: 'Stars', GUIDE_RMS: 'Guide RMS', ECCENTRICITY: 'Eccentricity',
    FOCPOS: 'Focuser', FOCTEMP: 'Focuser temp', AMBTEMP: 'Ambient', AIRMASS: 'Airmass', PIERSIDE: 'Pier side',
};

function Metric({ label, value, sub, title }) {
    return (
        <div className="metadata-item" title={title}>
            <dt>{label}</dt>
            <dd>
                <span className="quality-value">{value}</span>
                {sub && <span className="quality-sub">{sub}</span>}
            </dd>
        </div>
    );
}

// 3x3 median HFR across the frame: tilt (one side worse) or curvature (corners worse).
function RegionGrid({ gridPx, gridArcsec, units }) {
    const useArcsec = units === 'ARCSEC' && gridArcsec;
    const grid = useArcsec ? gridArcsec : gridPx;
    const values = grid.flat().filter((v) => v != null);
    if (values.length < 5) return null;
    const lo = Math.min(...values);
    const hi = Math.max(...values);
    const centre = gridPx[1][1];
    const corners = [gridPx[0][0], gridPx[0][2], gridPx[2][0], gridPx[2][2]].filter((v) => v != null);
    const cornerRatio = centre && corners.length ? corners.reduce((a, b) => a + b, 0) / corners.length / centre : null;
    const col = (c) => gridPx.map((row) => row[c]).filter((v) => v != null);
    const mean = (a) => (a.length ? a.reduce((x, y) => x + y, 0) / a.length : null);
    const left = mean(col(0));
    const right = mean(col(2));
    const top = mean(gridPx[0].filter((v) => v != null));
    const bottom = mean(gridPx[2].filter((v) => v != null));
    const notes = [];
    if (cornerRatio && cornerRatio >= 1.15) notes.push(`corners ${Math.round((cornerRatio - 1) * 100)}% larger than centre (field curvature / backfocus)`);
    const side = (a, b, na, nb) => {
        if (a && b && Math.max(a, b) / Math.min(a, b) >= 1.15) {
            notes.push(`${a > b ? na : nb} side ${Math.round((Math.max(a, b) / Math.min(a, b) - 1) * 100)}% larger (tilt?)`);
        }
    };
    side(left, right, 'left', 'right');
    side(top, bottom, 'top', 'bottom');

    const tone = (v) => {
        if (v == null || hi - lo < 1e-6) return 'neutral';
        const t = (v - lo) / (hi - lo);
        return t < 0.34 ? 'good' : t < 0.67 ? 'warn' : 'bad';
    };
    return (
        <div className="quality-grid-block">
            <div className="quality-grid" aria-label="Median HFR by region of the frame">
                {grid.map((row, r) => row.map((v, c) => (
                    <div key={`${r}-${c}`} className={`quality-grid-cell tone-${tone(v)}`}>
                        {v == null ? '—' : useArcsec ? `${v.toFixed(1)}″` : v.toFixed(2)}
                    </div>
                )))}
            </div>
            <div className="quality-grid-caption">
                <strong>HFR across the frame</strong>
                <span>{notes.length ? notes.join('; ') : 'Even across the field.'}</span>
            </div>
        </div>
    );
}

export default function StarQualityCard({ image, onImageUpdated }) {
    const { units } = useQualityUnits();
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState(null);
    const pollRef = useRef(null);
    const q = image.quality || {};
    const status = q.status ?? image.star_metrics_status ?? null;

    useEffect(() => () => clearTimeout(pollRef.current), []);

    async function handleRemeasure() {
        setBusy(true);
        setError(null);
        try {
            await remeasureStarMetrics(image.id);
            onImageUpdated?.({ ...image, star_metrics_status: 'PENDING', quality: { ...q, status: 'PENDING' } });
            let tries = 0;
            const poll = async () => {
                tries += 1;
                try {
                    const fresh = await fetchImage(image.id);
                    if (fresh.quality?.status !== 'PENDING' || tries >= 40) {
                        onImageUpdated?.(fresh);
                        setBusy(false);
                        return;
                    }
                } catch {
                    // keep polling; a transient error shouldn't strand the spinner
                }
                pollRef.current = setTimeout(poll, 3000);
            };
            pollRef.current = setTimeout(poll, 3000);
        } catch (e) {
            setError(e.message || 'Could not queue the measurement');
            setBusy(false);
        }
    }

    const fwhm = formatSize(q.fwhm_px, q.fwhm_arcsec, units);
    const hfr = formatSize(q.hfr_px, q.hfr_arcsec, units);
    const night = q.night;
    const nightMedian = night ? formatSize(night.median_fwhm_px, night.median_fwhm_arcsec, units) : null;
    const hints = Object.entries(q.hints || {}).filter(([k]) => HINT_LABELS[k]);
    const sampling = q.sampling ? SAMPLING_LABELS[q.sampling] : null;
    const showMetrics = status === 'OK' || status === 'HINT';

    return (
        <section className="metadata-section star-quality">
            <div className="star-quality-header">
                <h3 className="section-title">Star Quality</h3>
                {status && <span className={`quality-status status-${status.toLowerCase()}`}>{STATUS_LABELS[status] || status}</span>}
                <span className="star-quality-actions">
                    <QualityUnitsToggle compact />
                    <button type="button" className="btn btn-ghost btn-sm" onClick={handleRemeasure} disabled={busy || status === 'PENDING'}
                        title="Measure this image again now">
                        <RefreshCw size={14} className={busy ? 'spin' : ''} /> Re-measure
                    </button>
                </span>
            </div>

            {!status && <p className="quality-note">Not measured yet. AstroCat is measuring the library in the background, newest images first.</p>}
            {status === 'PENDING' && <p className="quality-note">Queued for measurement…</p>}
            {status === 'NO_STARS' && (
                <p className="quality-note">
                    Fewer than 10 usable stars{q.star_count != null ? ` (${q.star_count} found)` : ''}. Usually cloud, heavy
                    defocus, or a frame that isn’t really a Light.
                </p>
            )}
            {status === 'SKIPPED' && <p className="quality-note">{SKIP_REASONS[q.reason] || `Not measurable (${q.reason || 'unsupported file'}).`}</p>}
            {status === 'FAILED' && <p className="quality-note quality-error">Measurement failed: {q.error || 'unknown error'}. It will be retried automatically.</p>}
            {status === 'HINT' && <p className="quality-note">AstroCat can’t measure this file, so these values come from the capture software.</p>}
            {error && <p className="quality-note quality-error">{error}</p>}

            {showMetrics && (
                <dl className="metadata-grid">
                    {q.fwhm_px != null && (
                        <Metric label="FWHM" value={fwhm.text} sub={formatSecondary(q.fwhm_px, q.fwhm_arcsec, units)}
                            title="Full width at half maximum: median over up to 100 fitted stars (Moffat profile)." />
                    )}
                    <Metric label="HFR" value={hfr.text} sub={formatSecondary(q.hfr_px, q.hfr_arcsec, units)}
                        title="Half-flux radius: median over all usable stars. AstroCat’s own measurement; other tools define HFR differently." />
                    {q.eccentricity != null && (
                        <Metric label="Eccentricity" value={q.eccentricity.toFixed(2)}
                            sub={q.eccentricity >= 0.6 ? 'elongated' : q.eccentricity >= 0.45 ? 'slightly elongated' : 'round'}
                            title="0 is round. Above about 0.5 shows as visibly elongated stars: guiding, wind, tilt or coma." />
                    )}
                    {q.star_count != null && <Metric label="Stars" value={q.star_count.toLocaleString()} title="Usable stars after rejecting saturated, blended and edge stars." />}
                    {sampling && (
                        <Metric label="Sampling" value={sampling.label} sub={`${q.fwhm_px.toFixed(1)} px per FWHM`} title={sampling.hint} />
                    )}
                    {night && night.fwhm_delta_pct != null && (
                        <Metric label="vs night median"
                            value={<span className={`quality-delta tone-${deltaTone(night.fwhm_delta_pct)}`}>{formatDelta(night.fwhm_delta_pct)}</span>}
                            sub={`median ${nightMedian.text} over ${night.subs} subs`}
                            title={`FWHM compared with the other measured subs of the night of ${night.night} on the same rig and filter. Lower is sharper.`} />
                    )}
                    {q.bkg_adu != null && <Metric label="Sky background" value={`${Math.round(q.bkg_adu).toLocaleString()} ADU`} title="Median sky level: rises with moonlight, twilight and light pollution." />}
                </dl>
            )}

            {showMetrics && units === 'ARCSEC' && fwhm.fellBack && (
                <p className="quality-note">No plate scale yet (not plate-solved, rig scale unknown), so sizes are shown in pixels.</p>
            )}
            {showMetrics && q.scale_source === 'RIG' && units === 'ARCSEC' && (
                <p className="quality-note">Arcseconds use the rig’s measured scale ({q.scale_arcsec?.toFixed(2)}″/px) because this image isn’t plate-solved.</p>
            )}
            {showMetrics && q.scale_source === 'RIG_OVERRIDE' && units === 'ARCSEC' && (
                <p className="quality-note">
                    Arcseconds use the rig’s measured scale ({q.scale_arcsec?.toFixed(2)}″/px): this image’s stored plate
                    scale ({image.pixel_scale_arcsec?.toFixed(2)}″/px) disagrees with it too much to be right.
                </p>
            )}

            {status === 'OK' && q.grid_hfr_px && <RegionGrid gridPx={q.grid_hfr_px} gridArcsec={q.grid_hfr_arcsec} units={units} />}

            {hints.length > 0 && (
                <p className="quality-hints">
                    <span className="quality-hints-label">Capture software:</span>
                    {hints.map(([k, v]) => <span key={k} className="quality-hint">{HINT_LABELS[k]} {typeof v === 'number' ? Number(v.toFixed(2)) : v}</span>)}
                </p>
            )}
        </section>
    );
}
