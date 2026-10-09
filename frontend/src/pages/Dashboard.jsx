import { useState, useEffect, useCallback } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { Camera, Clock, Target, Star, Moon, Circle, Square, Contrast, AlertTriangle } from 'lucide-react';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';
import { fetchStatsOverview, fetchImages, fetchStatsByMonth, fetchTopObjects, fetchRecommendations } from '../api/client';
import { CHART_PRIMARY, CHART_AXIS_MUTED, CHART_TOOLTIP_DASHBOARD } from '../utils/chartColors';
import ImageCard from '../components/images/ImageCard';
import LastNightTile from '../components/quality/LastNightTile';
import { Button, EmptyState, PageHeader, Spinner } from '../components/ui';
import './Dashboard.css';

// Binding contract for the Tonight tile: docs/design/R2a-feedback-dashboard.md §7. The backend
// (feat/r2a-feedback-backend) is built in parallel and does not exist on this branch, so this
// tile is written strictly against the spec's response shape (same as Tonight.jsx's R1 shape).

const VERDICT_LABELS = { GO: 'GO', MARGINAL: 'MARGINAL', DONT_BOTHER: "DON'T BOTHER" };
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
    const [monthlyData, setMonthlyData] = useState([]);
    const [topObjects, setTopObjects] = useState([]);
    const [loading, setLoading] = useState(true);
    const [loadError, setLoadError] = useState(false);
    const navigate = useNavigate();

    const loadDashboard = useCallback(async () => {
        setLoading(true);
        setLoadError(false);
        try {
            const [statsData, imagesData, monthly, objects] = await Promise.all([
                fetchStatsOverview(),
                fetchImages({ page: 1, page_size: 6, sort_by: 'capture_date', sort_order: 'desc' }),
                fetchStatsByMonth(),
                fetchTopObjects(),
            ]);

            setStats(statsData);
            setRecentImages(imagesData.items);
            setMonthlyData(monthly);
            setTopObjects(objects);
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
                <PageHeader title="Dashboard" subtitle="Your astronomical image collection at a glance" />
                <EmptyState
                    icon={<AlertTriangle size={32} aria-hidden="true" />}
                    title="Couldn't load the dashboard"
                    description="The server didn't return your collection stats. Check that the backend is running, then try again."
                    action={<Button variant="filled" onClick={loadDashboard}>Retry</Button>}
                />
            </div>
        );
    }

    return (
        <div className="page-dashboard">
            <PageHeader title="Dashboard" subtitle="Your astronomical image collection at a glance" />

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

                {/* Monthly Activity Chart */}
                <div className="dashboard-card chart-card">
                    <div className="card-header">
                        <h3>Monthly Activity</h3>
                        <span className="text-muted text-sm">Images captured per month</span>
                    </div>
                    <div className="chart-container">
                        <ResponsiveContainer width="100%" height={250}>
                            <BarChart data={monthlyData}>
                                <defs>
                                    <linearGradient id="colorImages" x1="0" y1="0" x2="0" y2="1">
                                        <stop offset="5%" stopColor={CHART_PRIMARY} stopOpacity={0.3} />
                                        <stop offset="95%" stopColor={CHART_PRIMARY} stopOpacity={0} />
                                    </linearGradient>
                                </defs>
                                <XAxis
                                    dataKey="month"
                                    tickFormatter={(val) => {
                                        if (!val || val === 'Unknown') return val;
                                        const [y, m] = val.split('-');
                                        const date = new Date(parseInt(y), parseInt(m) - 1);
                                        return date.toLocaleDateString('default', { month: 'short', year: '2-digit' });
                                    }}
                                    stroke={CHART_AXIS_MUTED}
                                    fontSize={10}
                                />
                                <YAxis stroke={CHART_AXIS_MUTED} fontSize={12} />
                                <Tooltip
                                    contentStyle={{
                                        ...CHART_TOOLTIP_DASHBOARD,
                                        borderRadius: '8px'
                                    }}
                                    formatter={(value, name) => [value.toLocaleString(), name === 'count' ? 'Images' : 'Hours']}
                                    labelFormatter={(label) => {
                                        const [year, month] = label.split('-');
                                        return `${new Date(2024, parseInt(month) - 1).toLocaleString('default', { month: 'long' })} ${year}`;
                                    }}
                                    cursor={{ fill: 'rgba(255, 255, 255, 0.05)' }}
                                />
                                <Bar
                                    dataKey="count"
                                    fill="url(#colorImages)"
                                    radius={[4, 4, 0, 0]}
                                    onClick={(data) => {
                                        if (data && data.month) {
                                            const [year, month] = data.month.split('-');
                                            const startDate = `${year}-${month}-01T00:00:00`;
                                            // Get last day of month
                                            const lastDay = new Date(year, month, 0).getDate();
                                            const endDate = `${year}-${month}-${lastDay}T23:59:59`;
                                            navigate(`/search?start_date=${startDate}&end_date=${endDate}`);
                                        }
                                    }}
                                    style={{ cursor: 'pointer' }}
                                />
                            </BarChart>
                        </ResponsiveContainer>
                    </div>
                </div>

                {/* Top Objects */}
                <div className="dashboard-card">
                    <div className="card-header">
                        <h3>Top Objects</h3>
                        <Link to="/catalogs" className="link text-sm">View all →</Link>
                    </div>
                    <div className="top-objects-list">
                        {topObjects.slice(0, 6).map((obj, idx) => (
                            <Link
                                key={obj.designation}
                                to={`/search?object_name=${encodeURIComponent(obj.designation)}`}
                                className="top-object-item"
                            >
                                <div className="object-rank">{idx + 1}</div>
                                <div className="object-info">
                                    <span className="object-designation">{obj.designation}</span>
                                    <span className="object-name">{obj.name}</span>
                                </div>
                                <div className="object-stats">
                                    <span className="object-count">{obj.image_count}</span>
                                    <span className="object-exposure">{obj.total_exposure_hours.toFixed(1)}h</span>
                                </div>
                            </Link>
                        ))}
                    </div>
                </div>

                {/* Calibration Library (F1) */}
                <div className="dashboard-card">
                    <div className="card-header">
                        <h3>Calibration Library</h3>
                        <Link to="/search?frame_type=ALL" className="link text-sm">View all →</Link>
                    </div>
                    <div className="top-objects-list">
                        {[
                            { type: 'DARK', label: 'Darks', icon: <Moon size={18} aria-hidden="true" /> },
                            { type: 'FLAT', label: 'Flats', icon: <Circle size={18} aria-hidden="true" /> },
                            { type: 'BIAS', label: 'Bias', icon: <Square size={18} aria-hidden="true" /> },
                            { type: 'DARK_FLAT', label: 'Dark Flats', icon: <Contrast size={18} aria-hidden="true" /> },
                        ].map(({ type, label, icon }) => (
                            <Link
                                key={type}
                                to={`/search?frame_type=${type}`}
                                className="top-object-item"
                            >
                                <div className="object-rank">{icon}</div>
                                <div className="object-info">
                                    <span className="object-designation">{label}</span>
                                </div>
                                <div className="object-stats">
                                    <span className="object-count">
                                        {(stats?.calibration_counts?.[type] ?? 0).toLocaleString()}
                                    </span>
                                </div>
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

                <div className="quick-stat">
                    <span className="quick-stat-label">Storage Used</span>
                    <div className="progress-bar">
                        <div className="progress-fill" style={{ width: '57%' }} />
                    </div>
                    <span className="quick-stat-value">{stats?.total_file_size_gb?.toFixed(1)} GB</span>
                </div>
            </div>
        </div>
    );
}
