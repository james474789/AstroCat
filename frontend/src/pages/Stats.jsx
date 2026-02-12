import { useState, useEffect } from 'react';
import {
    BarChart, Bar, PieChart, Pie, Cell, AreaChart, Area,
    XAxis, YAxis, Tooltip, ResponsiveContainer, Legend
} from 'recharts';
import { fetchAllStats } from '../api/client';
import './Stats.css';

const COLORS = ['#5b8dee', '#8b5cf6', '#22c55e', '#eab308', '#ef4444', '#22d3ee'];

export default function Stats() {
    const [stats, setStats] = useState(null);
    const [loading, setLoading] = useState(true);

    useEffect(() => {
        async function loadStats() {
            try {
                const data = await fetchAllStats();
                setStats(data);
            } catch (error) {
                console.error('Failed to load stats:', error);
            } finally {
                setLoading(false);
            }
        }
        loadStats();
    }, []);

    if (loading) {
        return (
            <div className="stats-loading">
                <div className="spinner" />
                <p>Loading statistics...</p>
            </div>
        );
    }

    // Format data for charts
    const subtypeData = stats.by_subtype
        .filter(s => s.subtype)
        .map(s => ({
            name: s.subtype === 'SUB_FRAME' ? 'Sub Frames'
                : s.subtype === 'INTEGRATION_MASTER' ? 'Masters'
                    : s.subtype === 'PLANETARY' ? 'Planetary'
                        : 'Deprecated',
            value: s.count,
            hours: s.total_exposure_hours,
        }));

    const formatData = stats.by_format.map(f => ({
        name: f.format,
        value: f.count,
        percentage: f.percentage,
    }));

    const monthlyData = stats.by_month.map(m => ({
        month: m.month.split('-')[1],
        images: m.count,
        hours: m.exposure_hours,
    }));

    return (
        <div className="stats-page">
            <div className="page-header">
                <h1 className="page-title">Statistics</h1>
                <p className="page-subtitle">Insights into your astronomical image collection</p>
            </div>

            {/* Overview Cards */}
            <div className="stats-overview">
                <div className="overview-card highlight">
                    <div className="overview-value">{stats.overview.total_exposure_hours.toLocaleString(undefined, { maximumFractionDigits: 0 })}</div>
                    <div className="overview-label">Total Hours of Exposure</div>
                    <div className="overview-icon">⏱️</div>
                </div>
                <div className="overview-card">
                    <div className="overview-value">{stats.overview.total_images.toLocaleString()}</div>
                    <div className="overview-label">Total Images</div>
                </div>
                <div className="overview-card">
                    <div className="overview-value">{stats.overview.unique_objects_imaged}</div>
                    <div className="overview-label">Unique Objects Imaged</div>
                </div>
                <div className="overview-card">
                    <div className="overview-value">{stats.overview.total_file_size_gb.toFixed(0)} GB</div>
                    <div className="overview-label">Storage Used</div>
                </div>
            </div>

            {/* Charts Grid */}
            <div className="charts-grid">
                {/* Monthly Trend */}
                <div className="chart-card large">
                    <h3 className="chart-title">Monthly Activity</h3>
                    <ResponsiveContainer width="100%" height={300}>
                        <AreaChart data={monthlyData}>
                            <defs>
                                <linearGradient id="colorImages" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#5b8dee" stopOpacity={0.3} />
                                    <stop offset="95%" stopColor="#5b8dee" stopOpacity={0} />
                                </linearGradient>
                                <linearGradient id="colorHours" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#8b5cf6" stopOpacity={0.3} />
                                    <stop offset="95%" stopColor="#8b5cf6" stopOpacity={0} />
                                </linearGradient>
                            </defs>
                            <XAxis dataKey="month" stroke="#64748b" fontSize={12} />
                            <YAxis yAxisId="left" stroke="#5b8dee" fontSize={12} />
                            <YAxis yAxisId="right" orientation="right" stroke="#8b5cf6" fontSize={12} />
                            <Tooltip
                                contentStyle={{
                                    background: '#1a2435',
                                    border: '1px solid #2d3a4f',
                                    borderRadius: '8px',
                                    color: '#f1f5f9'
                                }}
                            />
                            <Legend />
                            <Area yAxisId="left" type="monotone" dataKey="images" name="Images" stroke="#5b8dee" fill="url(#colorImages)" />
                            <Area yAxisId="right" type="monotone" dataKey="hours" name="Exposure Hours" stroke="#8b5cf6" fill="url(#colorHours)" />
                        </AreaChart>
                    </ResponsiveContainer>
                </div>

                {/* Image Types Pie */}
                <div className="chart-card">
                    <h3 className="chart-title">By Image Type</h3>
                    <ResponsiveContainer width="100%" height={250}>
                        <PieChart>
                            <Pie
                                data={subtypeData}
                                cx="50%"
                                cy="50%"
                                innerRadius={60}
                                outerRadius={90}
                                paddingAngle={5}
                                dataKey="value"
                                label={({ name, percent }) => `${name} ${(percent * 100).toFixed(0)}%`}
                                labelLine={false}
                            >
                                {subtypeData.map((entry, index) => (
                                    <Cell key={entry.name} fill={COLORS[index % COLORS.length]} />
                                ))}
                            </Pie>
                            <Tooltip
                                contentStyle={{
                                    background: '#1a2435',
                                    border: '1px solid #2d3a4f',
                                    borderRadius: '8px',
                                    color: '#f1f5f9'
                                }}
                                formatter={(value, name, props) => [
                                    `${value.toLocaleString()} images (${props.payload.hours.toFixed(1)}h)`,
                                    props.payload.name
                                ]}
                            />
                        </PieChart>
                    </ResponsiveContainer>
                </div>

                {/* File Formats Bar */}
                <div className="chart-card">
                    <h3 className="chart-title">By File Format</h3>
                    <ResponsiveContainer width="100%" height={250}>
                        <BarChart data={formatData} layout="vertical">
                            <XAxis type="number" stroke="#64748b" fontSize={12} />
                            <YAxis type="category" dataKey="name" stroke="#64748b" fontSize={12} width={50} />
                            <Tooltip
                                contentStyle={{
                                    background: '#1a2435',
                                    border: '1px solid #2d3a4f',
                                    borderRadius: '8px',
                                    color: '#f1f5f9'
                                }}
                                formatter={(value) => [value.toLocaleString(), 'Images']}
                            />
                            <Bar dataKey="value" fill="#5b8dee" radius={[0, 4, 4, 0]} />
                        </BarChart>
                    </ResponsiveContainer>
                </div>
            </div>

            {/* Top Objects Table */}
            <div className="top-objects-section">
                <h3 className="section-title">Most Imaged Objects</h3>
                <div className="table-wrapper">
                    <table className="table">
                        <thead>
                            <tr>
                                <th>Rank</th>
                                <th>Object</th>
                                <th>Name</th>
                                <th>Images</th>
                                <th>Exposure Time</th>
                            </tr>
                        </thead>
                        <tbody>
                            {stats.top_objects.map((obj, idx) => (
                                <tr key={obj.designation}>
                                    <td>
                                        <span className={`rank-badge rank-${idx + 1}`}>{idx + 1}</span>
                                    </td>
                                    <td className="font-bold text-primary">{obj.designation}</td>
                                    <td>{obj.name}</td>
                                    <td>{obj.image_count.toLocaleString()}</td>
                                    <td>{obj.total_exposure_hours.toFixed(1)} hours</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                </div>
            </div>

            {/* Coverage Stats */}
            <div className="coverage-section">
                <h3 className="section-title">Catalog Coverage</h3>
                <div className="coverage-cards">
                    <div className="coverage-card">
                        <div className="coverage-header">
                            <span className="coverage-label">Messier Catalog</span>
                            <span className="coverage-count">{stats.overview.messier_coverage}/110</span>
                        </div>
                        <div className="coverage-bar">
                            <div
                                className="coverage-fill messier"
                                style={{ width: `${(stats.overview.messier_coverage / 110) * 100}%` }}
                            />
                        </div>
                        <span className="coverage-percent">
                            {((stats.overview.messier_coverage / 110) * 100).toFixed(1)}% complete
                        </span>
                    </div>

                    <div className="coverage-card">
                        <div className="coverage-header">
                            <span className="coverage-label">NGC Objects Imaged</span>
                            <span className="coverage-count">{stats.overview.ngc_coverage}</span>
                        </div>
                        <div className="coverage-bar">
                            <div
                                className="coverage-fill ngc"
                                style={{ width: `${(stats.overview.ngc_coverage / 7840) * 100}%` }}
                            />
                        </div>
                        <span className="coverage-percent">
                            {((stats.overview.ngc_coverage / 7840) * 100).toFixed(1)}% of catalog
                        </span>
                    </div>
                </div>
            </div>
        </div>
    );
}
