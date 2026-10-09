import './SpatialSearchInput.css';
import { Star, Diamond, Circle, Lightbulb } from 'lucide-react';

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
                            type="number"
                            className="input spatial-input"
                            placeholder="+45.5"
                            value={dec}
                            onChange={(e) => onDecChange(e.target.value)}
                            step="0.1"
                        />
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
