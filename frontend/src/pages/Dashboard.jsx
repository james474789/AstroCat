import { useState, useEffect, useCallback } from 'react';
import { Link } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Camera, Clock, Target, Star, Moon, Circle, Square, Contrast, AlertTriangle } from 'lucide-react';
import { fetchStatsOverview, fetchImages, fetchRecommendations } from '../api/client';
import ImageCard from '../components/images/ImageCard';
import LastNightTile from '../components/quality/LastNightTile';
import { Button, EmptyState, PageHeader, Spinner } from '../components/ui';
import './Dashboard.css';

// Binding contract for the Tonight tile: docs/design/R2a-feedback-dashboard.md §7. The backend
// (feat/r2a-feedback-backend) is built in parallel and does not exist on this branch, so this
// tile is written strictly against the spec's response shape (same as Tonight.jsx's R1 shape).

const VERDICT_LABELS = { GO: 'GO', MARGINAL: 'MARGINAL', DONT_BOTHER: "DON'T BOTHER" };
const CALIBRATION_TYPES = [
    { type: 'DARK', label: 'Darks', icon: <Moon size={16} aria-hidden="true" /> },
    { type: 'FLAT', label: 'Flats', icon: <Circle size={16} aria-hidden="true" /> },
    { type: 'BIAS', label: 'Bias', icon: <Square size={16} aria-hidden="true" /> },
    { type: 'DARK_FLAT', label: 'Dark Flats', icon: <Contrast size={16} aria-hidden="true" /> },
];

const VERDICT_CLASSES = { GO: 'verdict-go', MARGINAL: 'verdict-marginal', DONT_BOTHER: 'verdict-dont-bother' };

function formatLocalWindow(startIso, endIso, timeZone) {
    if (!startIso || !endIso) return '—';
    try {
        const fmt = new Intl.DateTimeFormat('en-US', { timeZone, hour: '2-digit', minute: '2-digit' });
        return `${fmt.format(new Date(startIso))}–${fmt.format(new Date(endIso))}`;
    } catch {
        return '—';
    }
}

function TonightTile() {
    const recQuery = useQuery({
        queryKey: ['dashboardTonight'],
        queryFn: () => fetchRecommendations({ perLane: 1 }),
        staleTime: 10 * 60 * 1000,
        retry: false,
    });

    // Never break the Dashboard on a Tonight failure: a missing site (404) gets a one-line hint,
    // any other error just hides the tile quietly.
    if (recQuery.isError) {
        if (recQuery.error?.status === 404) {
            return (
                <div className="dashboard-card tonight-tile">
                    <div className="card-header">
                        <h3>Tonight</h3>
                    </div>
                    <div className="tonight-tile-body">
                        <p className="text-muted text-sm">
                            <Link to="/equipment" className="link">Set up a site in Equipment</Link> to see tonight&apos;s picks.
                        </p>
                    </div>
                </div>
            );
        }
        return null;
    }

    if (recQuery.isLoading || !recQuery.data) return null;

    const data = recQuery.data;
    const context = data.context;
    const hero = data.hero;
    const verdict = data.verdict;
    const tz = context?.site?.timezone || 'UTC';
    const moonPct = context?.moon ? Math.round(context.moon.illumination * 100) : null;
    const reasons = (verdict?.reasons?.length ? verdict.reasons : hero?.reasons) || [];

    return (
        <div className="dashboard-card tonight-tile">
            <div className="card-header">
                <h3>Tonight</h3>
                {verdict?.level && (
                    <span className={`verdict-pill ${VERDICT_CLASSES[verdict.level] || 'verdict-unknown'}`}>
                        {VERDICT_LABELS[verdict.level] || verdict.level}
                    </span>
                )}
            </div>
            <div className="tonight-tile-body">
                {hero ? (
                    <>
                        <div className="tonight-tile-hero">
                            <span className="tonight-tile-hero-name">{hero.name || hero.target_key}</span>
                            <span className="text-muted text-sm">
                                {hero.rig?.name}{hero.mode ? ` · mode ${hero.mode}` : ''}
                            </span>
                        </div>
                        {reasons.length > 0 && (
                            <div className="chip-row">
                                {reasons.slice(0, 2).map((r, i) => (
                                    <span key={r.code ? `${r.code}-${i}` : i} className="chip reason-chip" title={r.text}>{r.text}</span>
                                ))}
                            </div>
                        )}
                    </>
                ) : (
                    <p className="text-muted text-sm">No feasible picks tonight.</p>
                )}
                {data.rig_plan?.length > 0 && (
                    <ul className="tonight-tile-rig-plan text-sm">
                        {data.rig_plan.map((entry) => (
                            <li key={entry.rig.id}>
                                <span className="text-muted">{entry.rig.name}:</span>{' '}
                                {entry.items?.[0] ? (entry.items[0].name || entry.items[0].target_key) : '—'}
                            </li>
                        ))}
                    </ul>
                )}
                <div className="tonight-tile-meta text-muted text-sm">
                    <span>{formatLocalWindow(context?.dark_start_utc, context?.dark_end_utc, tz)} local</span>
                    {moonPct != null && <span>Moon {moonPct}%</span>}
                </div>
                {context?.rig_mode === 'ALL_FALLBACK' && (
                    <p className="tonight-tile-hint text-muted text-sm">No rig mounted: showing the best rig per target.</p>
                )}
                <Button to="/tonight" size="sm">Open Tonight →</Button>
            </div>
        </div>
    );
}

export default function Dashboard() {
    const [stats, setStats] = useState(null);
    const [recentImages, setRecentImages] = useState([]);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState(false);

    const loadDashboard = useCallback(async () => {
        setLoading(true);
        setLoadError(false);
        try {
            const [statsData, imagesData] = await Promise.all([
                fetchStatsOverview(),
                fetchImages({ page: 1, page_size: 6, sort_by: 'capture_date', sort_order: 'desc' }),
            ]);

            setStats(statsData);
            setRecentImages(imagesData.items);
        } catch (error) {
            console.error('Failed to load dashboard:', error);
            setLoadError(true);
        } finally {
            setLoading(false);
        }
    }, []);

    useEffect(() => {
        loadDashboard();
    }, [loadDashboard]);

    if (loading) {
        return (
            <div className="page-dashboard">
                <div className="dashboard-loading">
                    <Spinner size={32} />
                    <p>Loading dashboard...</p>
                </div>
            </div>
        );
    }

    if (loadError) {
        return (
            <div className="page-dashboard">
                <PageHeader title="Home" subtitle="Your astronomical image collection at a glance" />
                <EmptyState
                    icon={<AlertTriangle size={32} aria-hidden="true" />}
                    title="Couldn't load Home"
                    description="The server didn't return your collection stats. Check that the backend is running, then try again."
                    action={<Button variant="filled" onClick={loadDashboard}>Retry</Button>}
                />
            </div>
        );
    }

    return (
        <div className="page-dashboard">
            <PageHeader title="Home" subtitle="Your astronomical image collection at a glance" />

            {/* Stats Grid */}
            <div className="stats-grid">
                <div className="stat-card">
                    <div className="stat-card-value">{stats?.total_images?.toLocaleString()}</div>
                    <div className="stat-card-label">Total Images</div>
                    <div className="stat-card-icon"><Camera size={32} aria-hidden="true" /></div>
                </div>

                <div className="stat-card">
                    <div className="stat-card-value">{stats?.total_exposure_hours?.toLocaleString(undefined, { maximumFractionDigits: 1 })}</div>
                    <div className="stat-card-label">Hours of Exposure</div>
                    <div className="stat-card-icon"><Clock size={32} aria-hidden="true" /></div>
                </div>

                <div className="stat-card">
                    <div className="stat-card-value">{stats?.plate_solved_percentage}%</div>
                    <div className="stat-card-label">Plate Solved</div>
                    <div className="stat-card-icon"><Target size={32} aria-hidden="true" /></div>
                </div>

                <div className="stat-card">
                    <div className="stat-card-value">{stats?.unique_objects_imaged}</div>
                    <div className="stat-card-label">Unique Objects</div>
                    <div className="stat-card-icon"><Star size={32} aria-hidden="true" /></div>
                </div>
            </div>

            {/* Main Content Grid */}
            <div className="dashboard-grid">
                {/* Tonight (R2a) */}
                <TonightTile />
                <LastNightTile />

                {/* Calibration Library (F1): shortcuts into the Images page filtered by frame type */}
                <div className="dashboard-card">
                    <div className="card-header">
                        <h3>Calibration Library</h3>
                        <Link to="/search?frame_type=ALL" className="link text-sm">View all →</Link>
                    </div>
                    <div className="calibration-list">
                        {CALIBRATION_TYPES.map(({ type, label, icon }) => (
                            <Link key={type} to={`/search?frame_type=${type}`} className="calibration-item">
                                <span className="calibration-icon">{icon}</span>
                                <span className="calibration-label">{label}</span>
                                <span className="calibration-count">
                                    {(stats?.calibration_counts?.[type] ?? 0).toLocaleString()}
                                </span>
                            </Link>
                        ))}
                    </div>
                </div>
            </div>

            {/* Recent Images */}
            <div className="recent-images-section">
                <div className="section-header">
                    <h3>Recent Images</h3>
                    <Button to="/search" size="sm">View All Images</Button>
                </div>

                <div className="image-grid">
                    {recentImages.map(image => (
                        <ImageCard key={image.id} image={image} />
                    ))}
                </div>
            </div>

            {/* Quick Stats */}
            <div className="quick-stats">
                <div className="quick-stat">
                    <span className="quick-stat-label">Messier Coverage</span>
                    <div className="progress-bar">
                        <div
                            className="progress-fill"
                            style={{ width: `${(stats?.messier_coverage / 110) * 100}%` }}
                        />
                    </div>
                    <span className="quick-stat-value">{stats?.messier_coverage}/110</span>
                </div>

                {stats?.total_file_size_gb != null && (
                    <div className="quick-stat">
                        <span className="quick-stat-label">Library Size</span>
                        <span className="quick-stat-value">{stats.total_file_size_gb.toFixed(1)} GB</span>
                    </div>
                )}
            </div>
        </div>
    );
}
