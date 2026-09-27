import { useQualityUnits } from '../../context/QualityUnitsContext';
import { formatSize, formatSecondary } from '../../utils/quality';

// A star size (FWHM/HFR) in the viewer's units, with the other unit on hover (Q1).
// Falls back to px (dimmed) when no plate/rig scale is known.
export default function QualityValue({ px, arcsec, title, emptyText = '—' }) {
    const { units } = useQualityUnits();
    if (px == null && arcsec == null) return <span className="text-muted">{emptyText}</span>;
    const main = formatSize(px, arcsec, units);
    const other = formatSecondary(px, arcsec, units);
    const hover = [title, other && `= ${other}`, main.fellBack && 'no plate scale known, shown in px']
        .filter(Boolean).join(' · ');
    return (
        <span className={`quality-inline${main.fellBack ? ' fell-back' : ''}`} title={hover || undefined}>
            {main.text}
        </span>
    );
}
