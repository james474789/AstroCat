import { AlertTriangle } from 'lucide-react';
import { SKY_CATALOGS, SKY_CATALOG_LABELS } from '../../utils/skyOverlay';
import './SkyOverlay.css';

/** Swatch + count per catalog present in the field; click a row to hide/show that catalog. */
export default function SkyOverlayLegend({ counts, hidden, onToggle, warning, isLoading, isError, className = '' }) {
    const present = SKY_CATALOGS.filter((c) => counts[c]);
    return (
        <div className={`sky-overlay-legend ${className}`} onPointerDown={(e) => e.stopPropagation()}>
            {isLoading && <div className="so-legend-note">Loading catalog objects…</div>}
            {isError && <div className="so-legend-note">Couldn't load catalog objects</div>}
            {!isLoading && !isError && present.length === 0 && <div className="so-legend-note">No catalog objects in field</div>}
            {present.map((c) => {
                const off = hidden.includes(c);
                return (
                    <button key={c} type="button" className={`so-legend-row${off ? ' off' : ''}`}
                        onClick={() => onToggle(c)} title={off ? `Show ${SKY_CATALOG_LABELS[c]}` : `Hide ${SKY_CATALOG_LABELS[c]}`}>
                        <span className={`so-swatch so-cat-${c}`} />
                        <span className="so-legend-name">{SKY_CATALOG_LABELS[c]}</span>
                        <span className="so-legend-count">{counts[c]}</span>
                    </button>
                );
            })}
            {warning && (
                <div className="so-legend-warning" title={warning}>
                    <AlertTriangle size={12} /> Approximate positions
                </div>
            )}
        </div>
    );
}
