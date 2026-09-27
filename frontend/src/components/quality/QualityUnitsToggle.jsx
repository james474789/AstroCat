import { useQualityUnits } from '../../context/QualityUnitsContext';
import './Quality.css';

// Segmented ″ / px switch for FWHM/HFR units (Q1). Shared by every page.
export default function QualityUnitsToggle({ compact = false, label = 'Star size units' }) {
    const { units, setUnits } = useQualityUnits();
    return (
        <div className={`quality-units-toggle${compact ? ' compact' : ''}`} role="group" aria-label={label} title={label}>
            <button type="button" className={units === 'ARCSEC' ? 'active' : ''} aria-pressed={units === 'ARCSEC'}
                onClick={() => setUnits('ARCSEC')}>″</button>
            <button type="button" className={units === 'PX' ? 'active' : ''} aria-pressed={units === 'PX'}
                onClick={() => setUnits('PX')}>px</button>
        </div>
    );
}
