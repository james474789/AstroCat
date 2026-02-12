import { useState, useEffect } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { BarChart, Bar, XAxis, YAxis, Tooltip, ResponsiveContainer, PieChart, Pie, Cell } from 'recharts';
import { fetchStatsOverview, fetchImages, fetchStatsByMonth, fetchTopObjects } from '../api/client';
import ImageCard from '../components/images/ImageCard';
import './Dashboard.css';

export default function Dashboard() {
    const [stats, setStats] = useState(null);
    const [recentImages, setRecentImages] = useState([]);
    const [monthlyData, setMonthlyData] = useState([]);
    const [topObjects, setTopObjects] = useState([]);
    const [loading, setLoading] = useState(true);
    const navigate = useNavigate();

    useEffect(() => {
        async function loadDashboard() {
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
            } finally {
                setLoading(false);
            }
        }

        loadDashboard();
    }, []);

    if (loading) {
        return (
            <div className="dashboard-loading">
                <div className="spinner" />
                <p>Loading dashboard...</p>
            </div>
        );
    }

    return (
        <div className="dashboard">
            <div className="page-header">
                <h1 className="page-title">Dashboard</h1>
                <p className="page-subtitle">Your astronomical image collection at a glance</p>
            </div>

            {/* Stats Grid */}
            <div className="stats-grid">
                <div className="stat-card">
                    <div className="stat-card-value">{stats?.total_images?.toLocaleString()}</div>
                    <div className="stat-card-label">Total Images</div>
                    <div className="stat-card-icon">📸</div>
                </div>

                <div className="stat-card">
                    <div className="stat-card-value">{stats?.total_exposure_hours?.toLocaleString(undefined, { maximumFractionDigits: 1 })}</div>
                    <div className="stat-card-label">Hours of Exposure</div>
                    <div className="stat-card-icon">⏱️</div>
                </div>

                <div className="stat-card">
                    <div className="stat-card-value">{stats?.plate_solved_percentage}%</div>
                    <div className="stat-card-label">Plate Solved</div>
                    <div className="stat-card-icon">🎯</div>
                </div>

                <div className="stat-card">
                    <div className="stat-card-value">{stats?.unique_objects_imaged}</div>
                    <div className="stat-card-label">Unique Objects</div>
                    <div className="stat-card-icon">⭐</div>
                </div>
            </div>

            {/* Main Content Grid */}
            <div className="dashboard-grid">
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
                                        <stop offset="5%" stopColor="#5b8dee" stopOpacity={0.3} />
                                        <stop offset="95%" stopColor="#5b8dee" stopOpacity={0} />
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
                                    stroke="#64748b"
                                    fontSize={10}
                                />
                                <YAxis stroke="#64748b" fontSize={12} />
                                <Tooltip
                                    contentStyle={{
                                        background: '#1a2435',
                                        border: '1px solid #2d3a4f',
                                        borderRadius: '8px',
                                        color: '#f1f5f9'
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
            </div>

            {/* Recent Images */}
            <div className="recent-images-section">
                <div className="section-header">
                    <h3>Recent Images</h3>
                    <Link to="/search" className="btn btn-secondary btn-sm">
                        View All Images
                    </Link>
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
