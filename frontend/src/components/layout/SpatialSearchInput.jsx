import { useEffect, useState } from 'react';
import './SpatialSearchInput.css';
import { Star, Diamond, Circle, Lightbulb } from 'lucide-react';

// "47.2", "+47:12" and "-47 12 30" -> decimal degrees as a string; null when not a valid Dec.
export function parseDec(text) {
    const t = String(text ?? '').trim();
    if (!t) return '';
    const m = t.match(/^([+-]?)(\d+(?:\.\d*)?)(?:[:\s]+(\d+(?:\.\d*)?))?(?:[:\s]+(\d+(?:\.\d*)?))?$/);
    if (!m) return null;
    const [, sign, d, min = '0', sec = '0'] = m;
    const value = Number(d) + Number(min) / 60 + Number(sec) / 3600;
    if (Number(min) >= 60 || Number(sec) >= 60 || value > 90) return null;
    return `${sign === '-' ? '-' : ''}${Number(value.toFixed(5))}`;
}

/**
 * Improved spatial search component for celestial coordinates
 * Provides clean input for RA, Dec, and Radius with visual grouping
 */
export default function SpatialSearchInput({
    raHms = '',
    dec = '',
    radius = '',
    onRaChange,
    onDecChange,
    onRadiusChange,
}) {
    // Keep what the user typed (e.g. "+47:12"); report the decimal value upward.
    const [decText, setDecText] = useState(dec);
    useEffect(() => {
        if (parseDec(decText) !== String(dec ?? '')) setDecText(dec ?? '');
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [dec]);
    const decInvalid = decText !== '' && parseDec(decText) === null;

    return (
        <div className="spatial-search-group">
            <label className="label">Spatial Search</label>
            <div className="spatial-search-container">
                <div className="spatial-coords">
                    <div className="spatial-field">
                        <label className="spatial-field-label">
                            <span className="spatial-icon"><Star size={14} /></span>
                            RA (HH:MM)
                        </label>
                        <input
                            type="text"
                            className="input spatial-input"
                            placeholder="12:30"
                            value={raHms}
                            onChange={(e) => onRaChange(e.target.value)}
                        />
                    </div>

                    <div className="spatial-field">
                        <label className="spatial-field-label">
                            <span className="spatial-icon"><Diamond size={14} /></span>
                            Dec (°)
                        </label>
                        <input
                            type="text"
                            inputMode="text"
                            className="input spatial-input"
                            placeholder="+45.5 or +45:30"
                            value={decText}
                            aria-invalid={decInvalid}
                            title={decInvalid ? 'Use decimal degrees (45.5) or degrees:minutes (45:30), between -90 and 90' : undefined}
                            onChange={(e) => {
                                setDecText(e.target.value);
                                const parsed = parseDec(e.target.value);
                                onDecChange(parsed === null ? '' : parsed);
                            }}
                        />
                        {decInvalid && <span className="spatial-hint text-caption">Use 45.5 or 45:30 (-90 to 90)</span>}
                    </div>
                </div>

                <div className="spatial-radius">
                    <label className="spatial-field-label">
                        <span className="spatial-icon"><Circle size={14} /></span>
                        Search Radius (°)
                    </label>
                    <div className="spatial-radius-input">
                        <input
                            type="number"
                            className="input"
                            placeholder="1.0"
                            value={radius}
                            onChange={(e) => onRadiusChange(e.target.value)}
                            min="0"
                            step="0.1"
                        />
                        <span className="radius-help">Degrees</span>
                    </div>
                </div>

                <div className="spatial-hint">
                    <Lightbulb size={14} /> Enter RA/Dec coordinates and radius to search nearby observations
                </div>
            </div>
        </div>
    );
}
