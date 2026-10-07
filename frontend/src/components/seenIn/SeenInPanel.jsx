import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { X, ChevronDown, ChevronRight } from 'lucide-react';
import { API_BASE_URL, formatDateTime, formatExposure } from '../../api/client';
import { SEEN_IN_MODES, SEEN_IN_MODE_LABELS } from '../../hooks/useSeenIn';
import './SeenIn.css';

const SUBTYPE_LABELS = { INTEGRATION_MASTER: 'Master', SUB_FRAME: 'Sub', PLANETARY: 'Planetary', ALLSKY: 'All-sky', AURORA: 'Aurora' };

function pct(coverage) {
    const p = coverage * 100;
    return p >= 99.5 ? '100%' : p < 1 ? '<1%' : `${Math.round(p)}%`;
}

function Row({ group, onClose }) {
    const [expanded, setExpanded] = useState(false);
    const more = group.count - group.members.length;
    return (
        <li className={`seen-in-row${group.subtype === 'INTEGRATION_MASTER' ? ' master' : ''}`}>
            <Link to={`/images/${group.id}`} onClick={onClose} className="seen-in-link">
                <img
                    className="seen-in-thumb"
                    src={`${API_BASE_URL}/images/${group.id}/thumbnail`}
                    alt=""
                    loading="lazy"
                />
                <span className="seen-in-info">
                    <span className="seen-in-name" title={group.file_name}>{group.object_name || group.file_name}</span>
                    <span className="seen-in-meta">
                        {[
                            SUBTYPE_LABELS[group.subtype] || group.subtype,
                            group.capture_date ? formatDateTime(group.capture_date) : null,
                            group.count > 1 ? `${group.count} similar framings` : null,
                        ].filter(Boolean).join(' · ')}
                    </span>
                </span>
                <span className="seen-in-coverage" title="Share of this image's field covered">{pct(group.coverage)}</span>
            </Link>
            {group.count > 1 && (
                <>
                    <button className="seen-in-toggle" onClick={() => setExpanded((v) => !v)}>
                        {expanded ? <ChevronDown size={14} /> : <ChevronRight size={14} />} {group.count} images
                    </button>
                    {expanded && (
                        <ul className="seen-in-members">
                            {group.members.map((m) => (
                                <li key={m.id}>
                                    <Link to={`/images/${m.id}`} onClick={onClose}>
                                        <span className="seen-in-name" title={m.file_name}>{m.file_name}</span>
                                        <span className="seen-in-meta">
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
                            {more > 0 && <li className="seen-in-more">+{more} more</li>}
                        </ul>
                    )}
                </>
            )}
        </li>
    );
}

/** Modal listing larger images whose field covers the current image, highest coverage first. */
export default function SeenInPanel({ seenIn, onClose }) {
    const { mode, setMode, groups, truncated, isLoading, isError } = seenIn;

    useEffect(() => {
        const onKey = (e) => {
            if (e.key === 'Escape') {
                e.stopImmediatePropagation(); // don't also trigger the page's own Esc (e.g. leave viewer)
                onClose();
            }
        };
        window.addEventListener('keydown', onKey, true);
        return () => window.removeEventListener('keydown', onKey, true);
    }, [onClose]);

    return (
        <div className="seen-in-backdrop" onClick={onClose}>
            <div className="seen-in-panel" role="dialog" aria-label="Seen in" onClick={(e) => e.stopPropagation()}>
                <header className="seen-in-header">
                    <h3>Seen in</h3>
                    <div className="seen-in-filter">
                        {SEEN_IN_MODES.map((m) => (
                            <button key={m} className={m === mode ? 'active' : undefined} onClick={() => setMode(m)}>
                                {SEEN_IN_MODE_LABELS[m]}
                            </button>
                        ))}
                    </div>
                    <button className="seen-in-close" onClick={onClose} title="Close"><X size={16} /></button>
                </header>
                <div className="seen-in-body">
                    {isLoading && <div className="seen-in-note">Searching…</div>}
                    {isError && <div className="seen-in-note">Couldn&apos;t load the list.</div>}
                    {!isLoading && !isError && groups.length === 0 && (
                        <div className="seen-in-note">No larger images cover this field.</div>
                    )}
                    {groups.length > 0 && (
                        <ul className="seen-in-list">
                            {groups.map((g) => <Row key={g.id} group={g} onClose={onClose} />)}
                        </ul>
                    )}
                    {truncated && <div className="seen-in-note">Showing the best matches only.</div>}
                </div>
            </div>
        </div>
    );
}
