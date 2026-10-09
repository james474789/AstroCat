import { AlertTriangle, Loader2 } from 'lucide-react';
import { SKY_CATALOGS, SKY_CATALOG_LABELS } from '../../utils/skyOverlay';
import './SkyOverlay.css';

/**
 * Swatch + count per catalog present in the field; click a row to hide/show that catalog.
 * `online` (O1) lists the admin-enabled online layers, each with its own loading/error state;
 * they show even when empty so a viewer can tell "none here" from "not looked up".
 */
export default function SkyOverlayLegend({ counts, hidden, onToggle, warning, isLoading, isError, online = [], className = '' }) {
    const present = SKY_CATALOGS.filter((c) => counts[c]);
    const row = (key, label, count, status = null, title = null) => {
        const off = hidden.includes(key);
        const toggleable = count > 0;
        return (
            <button key={key} type="button" className={`so-legend-row${off && toggleable ? ' off' : ''}${toggleable ? '' : ' static'}`}
                onClick={toggleable ? () => onToggle(key) : undefined} aria-disabled={!toggleable || undefined}
                title={title || (toggleable ? `${off ? 'Show' : 'Hide'} ${label}` : label)}>
                <span className={`so-swatch so-cat-${key}`} />
                <span className="so-legend-name">{label}</span>
                {status || <span className="so-legend-count">{count}</span>}
            </button>
        );
    };
    return (
        <div className={`sky-overlay-legend ${className}`} onPointerDown={(e) => e.stopPropagation()}>
            {isLoading && <div className="so-legend-note">Loading catalog objects…</div>}
            {isError && <div className="so-legend-note">Couldn't load catalog objects</div>}
            {!isLoading && !isError && present.length === 0 && <div className="so-legend-note">No catalog objects in field</div>}
            {present.map((c) => row(c, SKY_CATALOG_LABELS[c], counts[c]))}
            {online.length > 0 && <div className="so-legend-group">Online</div>}
            {online.map((o) => {
                let status = null;
                let title = o.notice;
                if (o.loading) {
                    status = <span className="so-legend-status" aria-label="Loading"><Loader2 size={12} className="so-spin" /></span>;
                } else if (o.error) {
                    status = <span className="so-legend-status error" aria-label="Lookup failed"><AlertTriangle size={12} /></span>;
                    title = `${o.label}: ${o.error}`;
                } else if (o.note) {
                    status = <span className="so-legend-status">{o.note}</span>;
                } else if (o.notice) {
                    status = <span className="so-legend-status error"><AlertTriangle size={12} />&nbsp;{o.count}</span>;
                }
                return row(o.key, o.label, o.count, status, title);
            })}
            {warning && (
                <div className="so-legend-warning" title={warning}>
                    <AlertTriangle size={12} /> Approximate positions
                </div>
            )}
        </div>
    );
}
