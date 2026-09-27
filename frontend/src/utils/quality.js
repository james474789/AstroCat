// Q1 star quality formatting (docs/design/Q1-star-quality.md §8).
// Values arrive in native pixels plus, when a plate/rig scale is known, arcsec.

export const STATUS_LABELS = {
    OK: 'Measured',
    PENDING: 'Measuring…',
    NO_STARS: 'No stars found',
    SKIPPED: 'Not measurable',
    HINT: 'From capture software',
    FAILED: 'Measurement failed',
};

export const SKIP_REASONS = {
    NONLINEAR_FORMAT: 'JPG/PNG files are stretched and compressed, so star profiles can’t be measured.',
    '8BIT_TIFF': '8-bit TIFFs are stretched, so star profiles can’t be measured.',
    NO_IMAGE_DATA: 'The file contains no image data.',
};

function fmt(value, digits) {
    return value == null || Number.isNaN(value) ? null : Number(value).toFixed(digits);
}

// A size in the viewer's units. Falls back to px when no scale is known.
// Returns { text, unit, fellBack }.
export function formatSize(px, arcsec, units) {
    if (units === 'ARCSEC' && arcsec != null) return { text: `${fmt(arcsec, 2)}″`, unit: 'ARCSEC', fellBack: false };
    if (px == null) return { text: '—', unit: null, fellBack: false };
    return { text: `${fmt(px, 2)} px`, unit: 'PX', fellBack: units === 'ARCSEC' };
}

// The other unit, for a secondary label: "2.56″ (3.20 px)".
export function formatSecondary(px, arcsec, units) {
    if (units === 'ARCSEC' && arcsec != null && px != null) return `${fmt(px, 2)} px`;
    if (units === 'PX' && arcsec != null) return `${fmt(arcsec, 2)}″`;
    return null;
}

export function formatDelta(pct) {
    if (pct == null) return null;
    const sign = pct > 0 ? '+' : '';
    return `${sign}${pct.toFixed(0)}%`;
}

// Relative verdict vs the night median: lower is sharper.
export function deltaTone(pct) {
    if (pct == null) return 'neutral';
    if (pct <= -5) return 'good';
    if (pct >= 30) return 'bad';
    if (pct >= 10) return 'warn';
    return 'neutral';
}

export const SAMPLING_LABELS = {
    UNDER: { label: 'Undersampled', hint: 'Under 1 px per FWHM: stars are smaller than a pixel. Fine detail is lost; drizzle can help.' },
    OK: { label: 'Well sampled', hint: '1–3 px per FWHM.' },
    OVER: { label: 'Oversampled', hint: 'Over 3 px per FWHM: binning 2x2 would lose little detail and improve signal-to-noise.' },
};
