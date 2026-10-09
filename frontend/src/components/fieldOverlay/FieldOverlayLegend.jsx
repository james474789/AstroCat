import './FieldOverlay.css';

const KINDS = [
    { key: 'masters', label: 'Masters', hint: 'Integrated masters' },
    { key: 'subs', label: 'Subs', hint: 'Individual sub-frames' },
];

/** Swatch + count per kind of image in the field; click a row to hide/show it (like the annotations legend). */
export default function FieldOverlayLegend({ overlays, className = '' }) {
    const { counts, isCountsLoading, isError } = overlays;
    return (
        <div className={`sky-overlay-legend field-overlay-legend ${className}`} onPointerDown={(e) => e.stopPropagation()}>
            <div className="so-legend-title">Images in field</div>
            {isCountsLoading && <div className="so-legend-note">Loading images…</div>}
            {isError && <div className="so-legend-note">Couldn't load images in field</div>}
            {KINDS.map(({ key, label, hint }) => {
                const off = !overlays[key];
                return (
                    <button key={key} type="button" className={`so-legend-row${off ? ' off' : ''}`}
                        onClick={() => overlays.toggleKind(key)} title={`${off ? 'Show' : 'Hide'} ${hint.toLowerCase()}`}>
                        <span className={`fo-swatch fo-swatch-${key}`} />
                        <span className="so-legend-name">{label}</span>
                        <span className="so-legend-count">{isCountsLoading ? '…' : counts[key]}</span>
                    </button>
                );
            })}
            {overlays.truncated && <div className="so-legend-note">List capped; some images left out</div>}
        </div>
    );
}
