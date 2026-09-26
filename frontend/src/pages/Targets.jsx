import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { fetchTargets, fetchUnassignedTargetsSummary, formatHours, formatDate, API_BASE_URL } from '../api/client';
import './Targets.css';

const FILTER_COLORS = {
    L: '#d0d4dc', R: '#e05050', G: '#50c070', B: '#5080e0',
    Ha: '#c8283c', OIII: '#2f80ed', SII: '#a83246', Hb: '#3cc8ff',
    Duo: '#b060c0', None: '#a0a0a0', Other: '#707070',
};

function filterColor(name) {
    if (FILTER_COLORS[name]) return FILTER_COLORS[name];
    if (name && name.startsWith('Other:')) return FILTER_COLORS.Other;
    return FILTER_COLORS.Other;
}

function FilterBar({ filters, totalSeconds }) {
    if (!filters || filters.length === 0 || !totalSeconds) {
        return <div className="target-filter-bar empty" />;
    }
    return (
        <div className="target-filter-bar">
            {filters.map((f) => {
                const pct = totalSeconds > 0 ? (f.seconds / totalSeconds) * 100 : 0;
                if (pct <= 0) return null;
                const goalPct = f.goal_seconds ? Math.min(100, (f.seconds / f.goal_seconds) * 100) : null;
                const title = `${f.filter} ${formatHours(f.seconds)} · ${f.subs} subs` +
                    (f.goal_seconds ? ` · ${Math.round(goalPct)}% of ${formatHours(f.goal_seconds)} goal` : '');
                return (
                    <div
                        key={f.filter}
                        className="target-filter-seg"
                        style={{ width: `${pct}%`, backgroundColor: filterColor(f.filter) }}
                        title={title}
                    />
                );
            })}
        </div>
    );
}

export default function Targets() {
    const [items, setItems] = useState([]);
    const [loading, setLoading] = useState(true);
    const [total, setTotal] = useState(0);
    const [totalPages, setTotalPages] = useState(1);
    const [page, setPage] = useState(1);

    const [search, setSearch] = useState('');
    const [debouncedSearch, setDebouncedSearch] = useState('');
    const [sort, setSort] = useState('integration');
    const [order, setOrder] = useState('desc');
    const [catalog, setCatalog] = useState('');
    const [hasMaster, setHasMaster] = useState(false);
    const [minHours, setMinHours] = useState('');

    const [unassigned, setUnassigned] = useState({ count: 0, total_seconds: 0 });

    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedSearch(search);
            setPage(1);
        }, 400);
        return () => clearTimeout(timer);
    }, [search]);

    useEffect(() => {
        fetchUnassignedTargetsSummary().then(setUnassigned).catch(() => {});
    }, []);

    useEffect(() => {
        load();
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [debouncedSearch, sort, order, catalog, hasMaster, minHours, page]);

    async function load() {
        setLoading(true);
        try {
            const data = await fetchTargets({
                search: debouncedSearch,
                sort,
                order,
                catalog: catalog || undefined,
                has_master: hasMaster ? true : undefined,
                min_hours: minHours || undefined,
                page,
                page_size: 30,
            });
            setItems(data.items);
            setTotal(data.total);
            setTotalPages(data.total_pages);
        } catch (e) {
            console.error('Failed to load targets:', e);
        } finally {
            setLoading(false);
        }
    }

    return (
        <div className="targets-page">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Targets</h1>
                    <p className="page-subtitle">Integration time per target, by filter</p>
                </div>
            </div>

            <div className="targets-header-tile">
                <div className="targets-header-stat">
                    <span className="stat-value">{total.toLocaleString()}</span>
                    <span className="stat-label">Targets</span>
                </div>
                <div className="targets-header-stat">
                    <span className="stat-value">{formatHours(items.reduce((s, t) => s + t.total_seconds, 0))}</span>
                    <span className="stat-label">Integration (this page)</span>
                </div>
                {unassigned.count > 0 && (
                    <Link
                        to={`/search?target_key=__none__&frame_type=LIGHT`}
                        className="targets-unassigned-link"
                    >
                        Unassigned lights: {unassigned.count.toLocaleString()} ({formatHours(unassigned.total_seconds)})
                    </Link>
                )}
            </div>

            <div className="targets-toolbar">
                <input
                    type="text"
                    className="input"
                    placeholder="Search targets..."
                    value={search}
                    onChange={(e) => setSearch(e.target.value)}
                />

                <select className="input select" value={sort} onChange={(e) => { setSort(e.target.value); setPage(1); }}>
                    <option value="integration">Sort: Integration</option>
                    <option value="name">Sort: Name</option>
                    <option value="last">Sort: Last Captured</option>
                    <option value="subs">Sort: Sub Count</option>
                </select>

                <button
                    className="btn btn-icon"
                    onClick={() => setOrder(order === 'asc' ? 'desc' : 'asc')}
                    title={order === 'asc' ? 'Ascending' : 'Descending'}
                >
                    {order === 'asc' ? '↑' : '↓'}
                </button>

                <div className="catalog-chip-group">
                    {['', 'MESSIER', 'NGC', 'IC', 'CALDWELL', 'SH2', 'OTHER'].map((c) => (
                        <button
                            key={c || 'all'}
                            className={`catalog-chip ${catalog === c ? 'active' : ''}`}
                            onClick={() => { setCatalog(c); setPage(1); }}
                        >
                            {c || 'All'}
                        </button>
                    ))}
                </div>

                <label className="checkbox-label">
                    <input
                        type="checkbox"
                        checked={hasMaster}
                        onChange={(e) => { setHasMaster(e.target.checked); setPage(1); }}
                    />
                    <span>Has master</span>
                </label>

                <input
                    type="number"
                    className="input targets-min-hours"
                    placeholder="Min hours"
                    value={minHours}
                    onChange={(e) => { setMinHours(e.target.value); setPage(1); }}
                    min="0"
                    step="0.5"
                />
            </div>

            {loading ? (
                <div className="loading-state">
                    <div className="spinner" />
                    <p>Loading targets...</p>
                </div>
            ) : items.length === 0 ? (
                <div className="empty-state">
                    <div className="empty-state-icon">&#127919;</div>
                    <h3 className="empty-state-title">No targets found</h3>
                    <p className="empty-state-text">Try adjusting your filters, or run the target backfill.</p>
                </div>
            ) : (
                <div className="targets-list">
                    {items.map((t) => (
                        <Link key={t.target_key} to={`/targets/${encodeURIComponent(t.target_key)}`} className="target-row">
                            <div className="target-thumb">
                                {t.cover_image_id ? (
                                    <img src={`${API_BASE_URL}/images/${t.cover_image_id}/thumbnail`} alt={t.display_name} />
                                ) : (
                                    <div className="target-thumb-placeholder">&#127765;</div>
                                )}
                            </div>
                            <div className="target-info">
                                <div className="target-name-row">
                                    <span className="target-name">{t.display_name}</span>
                                    {(t.constellation || t.object_type) && (
                                        <span className="target-meta">
                                            {[t.object_type, t.constellation].filter(Boolean).join(' · ')}
                                        </span>
                                    )}
                                </div>
                                <FilterBar filters={t.filters} totalSeconds={t.total_seconds} />
                            </div>
                            <div className="target-stat">
                                <span className="target-stat-value">{formatHours(t.total_seconds)}</span>
                                <span className="target-stat-label">{t.total_subs} subs</span>
                            </div>
                            <div className="target-stat">
                                <span className="target-stat-value">{t.nights}</span>
                                <span className="target-stat-label">nights</span>
                            </div>
                            <div className="target-stat">
                                <span className="target-stat-value">{t.last_capture ? formatDate(t.last_capture) : '--'}</span>
                                <span className="target-stat-label">last capture</span>
                            </div>
                            <div className="target-rigs">
                                {(t.cameras || []).slice(0, 2).map((c) => (
                                    <span key={c} className="rig-chip">{c}</span>
                                ))}
                                {t.master_count > 0 && (
                                    <span className="rig-chip master-chip">{t.master_count} master{t.master_count > 1 ? 's' : ''}</span>
                                )}
                            </div>
                        </Link>
                    ))}
                </div>
            )}

            {totalPages > 1 && (
                <div className="pagination">
                    <button className="btn btn-secondary" disabled={page === 1} onClick={() => setPage((p) => p - 1)}>
                        Previous
                    </button>
                    <div className="pagination-info">Page {page} of {totalPages}</div>
                    <button className="btn btn-secondary" disabled={page === totalPages} onClick={() => setPage((p) => p + 1)}>
                        Next
                    </button>
                </div>
            )}
        </div>
    );
}
