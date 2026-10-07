import { useEffect, useRef } from 'react';
import { Link } from 'react-router-dom';
import { X } from 'lucide-react';
import { formatDateTime, formatExposure } from '../../api/client';
import './FieldOverlay.css';

const SUBTYPE_LABELS = { INTEGRATION_MASTER: 'Master', SUB_FRAME: 'Sub', PLANETARY: 'Planetary', ALLSKY: 'All-sky', AURORA: 'Aurora' };

/** Member list for a grouped footprint, positioned at a viewport point. */
export default function FieldOverlayPopover({ group, x, y, onClose }) {
    const ref = useRef(null);

    useEffect(() => {
        const onDown = (e) => { if (ref.current && !ref.current.contains(e.target)) onClose(); };
        const onKey = (e) => {
            if (e.key === 'Escape') {
                e.stopImmediatePropagation(); // don't also trigger the page's own Esc (e.g. leave viewer)
                onClose();
            }
        };
        // Deferred so the click that opened the popover doesn't close it straight away
        const t = setTimeout(() => document.addEventListener('pointerdown', onDown), 0);
        window.addEventListener('keydown', onKey, true);
        return () => {
            clearTimeout(t);
            document.removeEventListener('pointerdown', onDown);
            window.removeEventListener('keydown', onKey, true);
        };
    }, [onClose]);

    const left = Math.max(8, Math.min(x + 8, window.innerWidth - Math.min(320, window.innerWidth - 16) - 8));
    const top = Math.max(8, Math.min(y + 8, window.innerHeight - 320));
    const more = group.count - group.members.length;

    return (
        <div className="field-overlay-popover" ref={ref} style={{ left, top }}>
            <div className="fo-pop-header">
                <span>
                    {group.object_name || group.file_name} · {group.count} images
                    {group.shape === 'circle' && <span className="fo-pop-note"> (rotation unknown)</span>}
                </span>
                <button className="fo-pop-close" onClick={onClose} title="Close"><X size={14} /></button>
            </div>
            <ul className="fo-pop-list">
                {group.members.map((m) => (
                    <li key={m.id} className={m.subtype === 'INTEGRATION_MASTER' ? 'master' : undefined}>
                        <Link to={`/images/${m.id}`} onClick={onClose}>
                            <span className="fo-pop-name" title={m.file_name}>{m.file_name}</span>
                            <span className="fo-pop-meta">
                                {[
                                    SUBTYPE_LABELS[m.subtype] || m.subtype,
                                    m.filter_name,
                                    m.exposure_time_seconds != null ? formatExposure(m.exposure_time_seconds) : null,
                                    m.capture_date ? formatDateTime(m.capture_date) : null,
                                ].filter(Boolean).join(' · ')}
                            </span>
                        </Link>
                    </li>
                ))}
            </ul>
            {more > 0 && <div className="fo-pop-more">+{more} more</div>}
        </div>
    );
}
