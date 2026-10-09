import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import { AlertTriangle, Clock, Image as ImageIcon, Zap, Hash } from 'lucide-react';
import {
    BarChart, Bar, PieChart, Pie, Cell, ScatterChart, Scatter,
    XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, Legend, LabelList
} from 'recharts';
import { fetchFitsStats } from '../api/client';
import { MILKY_WAY_DATA } from '../data/mw_data';
import { CONSTELLATION_LINES } from '../data/constellations_data';
import { CONSTELLATION_LABELS } from '../data/constellations_labels';
import RigStarSizeChart from '../components/quality/RigStarSizeChart';
import QualityStatsSection from '../components/quality/QualityStatsSection';
import { SERIES, CHART_PRIMARY, CHART_SECONDARY, CHART_ACCENT, CHART_SUCCESS, CHART_AXIS, CHART_TEXT, CHART_TOOLTIP, HEAT, FILTER_SERIES } from '../utils/chartColors';
import { EmptyState, PageHeader, Spinner } from '../components/ui';
import './FitsStats.css';

const COLORS = SERIES;

// Helper to interpolate colors
const interpolateColor = (value, minColor, maxColor) => {
    // Simple linear interpolation between two hex colors
    const start = parseInt(minColor.slice(1), 16);
    const end = parseInt(maxColor.slice(1), 16);

    const r1 = (start >> 16) & 255;
    const g1 = (start >> 8) & 255;
    const b1 = start & 255;

    const r2 = (end >> 16) & 255;
    const g2 = (end >> 8) & 255;
    const b2 = end & 255;

    const r = Math.round(r1 + (r2 - r1) * value);
    const g = Math.round(g1 + (g2 - g1) * value);
    const b = Math.round(b1 + (b2 - b1) * value);

    return `#${(1 << 24 | r << 16 | g << 8 | b).toString(16).slice(1)}`;
};

const getHeatmapColor = (density) => {
    // Density is 0 to 1
    // Gradient: Blue (Low) -> Cyan -> Green -> Yellow -> Red (High)
    if (density < 0.25) return interpolateColor(density * 4, HEAT.blue, HEAT.cyan); // Blue -> Cyan
    if (density < 0.5) return interpolateColor((density - 0.25) * 4, HEAT.cyan, HEAT.green); // Cyan -> Green
    if (density < 0.75) return interpolateColor((density - 0.5) * 4, HEAT.green, HEAT.yellow); // Green -> Yellow
    return interpolateColor((density - 0.75) * 4, HEAT.yellow, HEAT.red); // Yellow -> Red
};

const formatRaToTime = (degrees) => {
    const totalHours = degrees / 15;
    const hours = Math.floor(totalHours);
    const minutes = Math.round((totalHours - hours) * 60);
    return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}`;
};

const CustomTooltip = ({ active, payload, label }) => {
    if (active && payload && payload.length) {
        const data = payload[0].payload;
        return (
            <div className="custom-tooltip" style={{
                ...CHART_TOOLTIP,
                borderRadius: '8px',
                padding: '10px',
            }}>
                <p style={{ margin: 0, fontWeight: 'bold' }}>{`RA: ${formatRaToTime(data.x)}`}</p>
                <p style={{ margin: 0 }}>{`Dec: ${data.y.toFixed(2)}°`}</p>
                {data.density > 0 && <p style={{ margin: 0, fontSize: '0.8em', opacity: 0.7 }}>{`Density: ${(data.density * 100).toFixed(0)}%`}</p>}
            </div>
        );
    }
    return null;
};

export default function FitsStats() {
    const navigate = useNavigate();
    const [filters, setFilters] = useState({
        date_from: '',
        date_to: '',
        cameras: '',
        telescopes: '',
        // F1: defaults to Lights only, like Search; "ALL" removes the filter.
        frame_type: 'LIGHT'
    });

    // Debounced filters state for the actual query
    const [debouncedFilters, setDebouncedFilters] = useState(filters);

    // Effect for debouncing
    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedFilters(filters);
        }, 500);
        return () => clearTimeout(timer);
    }, [filters]);

    const { data: stats, isLoading, isError } = useQuery({
        queryKey: ['fitsStats', debouncedFilters],
        queryFn: () => fetchFitsStats(debouncedFilters),
        staleTime: 5 * 60 * 1000 // 5 minutes
    });

    const handleFilterChange = (e) => {
        const { name, value } = e.target;
        setFilters(prev => ({
            ...prev,
            [name]: value
        }));
    };

    if (isLoading) {
        return (
            <div className="page-fits-stats">
                <div className="loading-container">
                    <Spinner size={40} />
                    <p>Loading statistics...</p>
                </div>
            </div>
        );
    }

    if (isError) {
        return (
            <div className="page-fits-stats">
                <EmptyState
                    icon={<AlertTriangle size={40} aria-hidden="true" />}
                    title="Failed to load statistics"
                    description="Please check your connection or try again later."
                />
            </div>
        );
    }

    // Prepare data for charts
    const exposureData = stats?.exposure_distribution?.map(bin => ({
        range: `${bin.bin_start}-${bin.bin_end}s`,
        count: bin.count
    })) || [];

    // Process camera data: Top 10 + Other
    let cameraData = [];
    if (stats?.cameras) {
        // Sort by count descending
        const sortedCameras = [...stats.cameras].sort((a, b) => b.count - a.count);

        // Take top 10
        const topCameras = sortedCameras.slice(0, 10);

        // Calculate 'Other'
        const otherCount = sortedCameras.slice(10).reduce((sum, cam) => sum + cam.count, 0);

        cameraData = topCameras.map(cam => ({
            name: cam.name,
            value: cam.count
        }));

        if (otherCount > 0) {
            cameraData.push({
                name: 'Other',
                value: otherCount
            });
        }
    }



    // Helper to normalize filter names
    const normalizeFilterName = (name) => {
        if (!name) return 'Unknown';
        const lower = name.toLowerCase().replace(/[^a-z0-9]/g, ''); // alphanumeric only

        if (['r', 'red'].includes(lower)) return 'Red';
        if (['g', 'green'].includes(lower)) return 'Green';
        if (['b', 'blue'].includes(lower)) return 'Blue';
        if (['l', 'lum', 'luminance', 'uvircut', 'lpro'].includes(lower)) return 'Luminance';
        if (['h', 'ha', 'halpha', 'h_alpha'].includes(lower)) return 'H-Alpha';
        if (['o', 'o3', 'oiii', 'oxygen3'].includes(lower)) return 'OIII';
        if (['s', 's2', 'sii', 'sulfur2'].includes(lower)) return 'SII';
        if (['dark'].includes(lower)) return 'Dark';
        if (['flat'].includes(lower)) return 'Flat';
        if (['bias', 'offset'].includes(lower)) return 'Bias';

        // Capitalize first letter for others
        return name.charAt(0).toUpperCase() + name.slice(1);
    };

    // Helper to get semantic color
    const getFilterColor = (name) => {
        const lower = name.toLowerCase();
        return FILTER_SERIES[lower] || null; // Fallback to palette
    };

    // Process filter data with normalization and grouping
    let filterData = [];
    if (stats?.filters) {
        const filterCounts = {};

        // 1. Normalize and Aggregate
        stats.filters.forEach(f => {
            const normalized = normalizeFilterName(f.name);
            filterCounts[normalized] = (filterCounts[normalized] || 0) + f.count;
        });

        // 2. Convert to array and sort
        const sortedFilters = Object.entries(filterCounts)
            .map(([name, count]) => ({ name, value: count }))
            .sort((a, b) => b.value - a.value);

        // 3. Take Top 10 and Group Others
        const topFilters = sortedFilters.slice(0, 10);
        const otherCount = sortedFilters.slice(10).reduce((sum, f) => sum + f.value, 0);

        filterData = topFilters.map((f, i) => ({
            ...f,
            color: getFilterColor(f.name) || COLORS[i % COLORS.length]
        }));

        if (otherCount > 0) {
            filterData.push({
                name: 'Other',
                value: otherCount,
                color: getFilterColor('Other')
            });
        }
    }

    // Process rotation data
    const rotationData = stats?.rotation_distribution?.map(bin => ({
        range: `${bin.bin_start}-${bin.bin_end}°`,
        count: bin.count
    })) || [];

    // Process pixel scale data
    const scaleData = stats?.pixel_scale_distribution?.map(bin => ({
        range: bin.bin_end > 100 ? `${bin.bin_start}+"` : `${bin.bin_start}-${bin.bin_end}"`,
        count: bin.count,
        bin_start: bin.bin_start,
        bin_end: bin.bin_end
    })) || [];

    // Calculate density map for heatmap effect
    const binSize = 5; // degrees
    const densityMap = {};
    let maxDensity = 0;

    stats?.sky_coverage?.forEach(pt => {
        const raBin = Math.floor(pt.ra / binSize);
        const decBin = Math.floor(pt.dec / binSize);
        const key = `${raBin}-${decBin}`;
        densityMap[key] = (densityMap[key] || 0) + 1;
        if (densityMap[key] > maxDensity) maxDensity = densityMap[key];
    });

    const skyCoverage = stats?.sky_coverage?.map(pt => {
        const raBin = Math.floor(pt.ra / binSize);
        const decBin = Math.floor(pt.dec / binSize);
        const count = densityMap[`${raBin}-${decBin}`] || 0;
        const normalizedDensity = maxDensity > 0 ? count / maxDensity : 0;

        return {
            x: pt.ra,
            y: pt.dec,
            z: 1, // default size
            density: normalizedDensity,
            color: getHeatmapColor(normalizedDensity)
        };
    }) || [];

    // Safe checks for overview values
    const overview = stats?.overview || {};

    // Navigation handlers
    const handleCameraClick = (data) => {
        if (data && data.name && data.name !== 'Other') {
            navigate(`/search?camera=${encodeURIComponent(data.name)}`);
        }
    };

    const handleExposureClick = (data) => {
        // Parse range "0-60s" -> min=0, max=60
        if (data && data.range) {
            // Remove 's' suffix if present
            const cleanRange = data.range.replace('s', '');

            // Check for single bound "300+"
            if (cleanRange.includes('+')) {
                const min = cleanRange.replace('+', '');
                navigate(`/search?exposure_min=${min}`);
                return;
            }

            // Standard range "min-max"
            const parts = cleanRange.split('-');
            if (parts.length === 2) {
                navigate(`/search?exposure_min=${parts[0]}&exposure_max=${parts[1]}&max_exposure_exclusive=true`);
            }
        }
    };

    const handleScaleClick = (data) => {
        if (data && data.bin_start !== undefined) {
            // For "5.0+", we just set min
            if (data.bin_end > 100) {
                navigate(`/search?pixel_scale_min=${data.bin_start}`);
            } else {
                navigate(`/search?pixel_scale_min=${data.bin_start}&pixel_scale_max=${data.bin_end}&pixel_scale_max_exclusive=true`);
            }
        }
    };

    const handleSkyClick = (data) => {
        // Recharts passes the clicked point data. 
        // IMPORTANT: 'data' (the event object) contains 'x' and 'y' which are pixel coordinates 
        // relative to the chart container. The actual data points are in 'data.payload'.
        // We must check payload first to get the real RA/Dec values.

        const point = data?.payload || data;

        if (point && point.x !== undefined && point.y !== undefined) {
            // Default search radius 5.0 degrees
            navigate(`/search?ra=${point.x}&dec=${point.y}&radius=5.0`);
        }
    };

    const handleRotationClick = (data) => {
        if (data && data.range) {
            // Parse range "0-15°" -> min=0, max=15
            const cleanRange = data.range.replace('°', '');
            const parts = cleanRange.split('-');
            if (parts.length === 2) {
                navigate(`/search?rotation_min=${parts[0]}&rotation_max=${parts[1]}`);
            }
        }
    };

    const handleFilterClick = (data) => {
        if (data && data.name) {
            if (data.name === 'Other') return; // Don't filter on mixed group
            navigate(`/search?filter=${encodeURIComponent(data.name)}`);
        }
    };

    // Default to 0 if values are missing
    const totalHours = (overview.total_exposure_hours || 0).toFixed(1);
    const avgExposure = (overview.average_exposure_seconds || 0).toFixed(1);
    const totalImages = overview.total_images || 0;

    return (
        <div className="page-fits-stats">
            <PageHeader
                title="Statistics"
                subtitle="Deep dive into technical metadata from your FITS headers."
            />

            {/* Filters */}
            <div className="stats-filters">
                <div className="filter-group">
                    <label className="filter-label">Date Range</label>
                    <div className="flex gap-sm">
                        <input
                            type="date"
                            name="date_from"
                            value={filters.date_from}
                            onChange={handleFilterChange}
                            className="filter-input"
                        />
                        <span className="text-muted self-center">-</span>
                        <input
                            type="date"
                            name="date_to"
                            value={filters.date_to}
                            onChange={handleFilterChange}
                            className="filter-input"
                        />
                    </div>
                </div>

                <div className="filter-group">
                    <label className="filter-label">Camera</label>
                    <input
                        type="text"
                        name="cameras"
                        placeholder="Filter by camera..."
                        value={filters.cameras}
                        onChange={handleFilterChange}
                        className="filter-input"
                    />
                </div>

                <div className="filter-group">
                    <label className="filter-label">Telescope</label>
                    <input
                        type="text"
                        name="telescopes"
                        placeholder="Filter by telescope..."
                        value={filters.telescopes}
                        onChange={handleFilterChange}
                        className="filter-input"
                    />
                </div>

                <div className="filter-group">
                    <label className="filter-label">Frame Type</label>
                    <select
                        name="frame_type"
                        value={filters.frame_type}
                        onChange={handleFilterChange}
                        className="filter-input"
                    >
                        <option value="LIGHT">Lights (default)</option>
                        <option value="ALL">All Frame Types</option>
                        <option value="DARK">Darks</option>
                        <option value="FLAT">Flats</option>
                        <option value="BIAS">Bias</option>
                        <option value="DARK_FLAT">Dark Flats</option>
                    </select>
                </div>
            </div>

            {/* Overview Cards */}
            <div className="stats-overview">
                <div className="stat-card highlight">
                    <div className="stat-value">{totalHours}h</div>
                    <div className="stat-label">Total Integration</div>
                    <div className="stat-icon"><Clock size={24} aria-hidden="true" /></div>
                </div>
                <div className="stat-card">
                    <div className="stat-value">{totalImages.toLocaleString()}</div>
                    <div className="stat-label">Total Images</div>
                    <div className="stat-icon"><ImageIcon size={24} aria-hidden="true" /></div>
                </div>
                <div className="stat-card">
                    <div className="stat-value">{avgExposure}s</div>
                    <div className="stat-label">Avg Exposure</div>
                    <div className="stat-icon"><Zap size={24} aria-hidden="true" /></div>
                </div>
                <div className="stat-card">
                    <div className="stat-value">{overview.total_subs ?? 'N/A'}</div>
                    <div className="stat-label">Total Subs</div>
                    <div className="stat-icon"><Hash size={24} aria-hidden="true" /></div>
                </div>
            </div>

            {/* Charts Grid */}
            <div className="charts-grid">

                {/* Sky Coverage Scatter */}
                <div className="chart-card full-width">
                    <div className="chart-header">
                        <h3 className="chart-title">Sky Coverage (RA / Dec)</h3>
                    </div>
                    <ResponsiveContainer width="100%" height={300}>
                        <ScatterChart
                            margin={{ top: 20, right: 20, bottom: 20, left: 20 }}

                        >
                            <CartesianGrid stroke="rgba(255,255,255,0.1)" strokeDasharray="3 3" />
                            <XAxis
                                type="number"
                                dataKey="x"
                                name="RA"
                                unit="h"
                                domain={[0, 360]}
                                reversed={true}
                                ticks={[0, 45, 90, 135, 180, 225, 270, 315, 360]}
                                tickFormatter={(val) => `${val / 15}h`}
                                stroke={CHART_AXIS}
                                fontSize={12}
                            />
                            <YAxis
                                type="number"
                                dataKey="y"
                                name="Dec"
                                unit="°"
                                domain={[-90, 90]}
                                stroke={CHART_AXIS}
                                fontSize={12}
                            />
                            <Tooltip
                                cursor={{ strokeDasharray: '3 3' }}
                                content={<CustomTooltip />}
                            />
                            {/* Milky Way Overlay */}
                            {MILKY_WAY_DATA["0"].map((segment, idx) => (
                                <Scatter
                                    key={`mw-line-${idx}`}
                                    name="Milky Way"
                                    data={segment}
                                    line={{ stroke: 'rgba(255, 255, 255, 0.2)', strokeWidth: 2, strokeDasharray: '5 5' }}
                                    shape={() => null}
                                    isAnimationActive={false}
                                    pointerEvents="none"
                                    legendType="none"
                                />
                            ))}
                            {MILKY_WAY_DATA["10"].map((segment, idx) => (
                                <Scatter
                                    key={`mw-band-${idx}`}
                                    name="Milky Way Band"
                                    data={segment}
                                    line={{ stroke: 'rgba(255, 255, 255, 0.05)', strokeWidth: 40 }}
                                    shape={() => null}
                                    isAnimationActive={false}
                                    pointerEvents="none"
                                    legendType="none"
                                />
                            ))}
                            {/* Constellation Lines */}
                            {CONSTELLATION_LINES.map((segment, idx) => (
                                <Scatter
                                    key={`constellation-${idx}`}
                                    name="Constellation"
                                    data={segment}
                                    line={{ stroke: 'rgba(255, 255, 255, 0.1)', strokeWidth: 1 }}
                                    shape={() => null}
                                    isAnimationActive={false}
                                    pointerEvents="none"
                                    legendType="none"
                                />
                            ))}
                            {/* Constellation Labels */}
                            <Scatter
                                name="Constellation Labels"
                                data={CONSTELLATION_LABELS}
                                shape={() => null}
                                isAnimationActive={false}
                                pointerEvents="none"
                                legendType="none"
                            >
                                <LabelList
                                    dataKey="name"
                                    position="center"
                                    style={{
                                        fill: 'rgba(255, 255, 255, 0.3)',
                                        fontSize: '9px',
                                        pointerEvents: 'none',
                                        userSelect: 'none'
                                    }}
                                />
                            </Scatter>
                            <Scatter
                                name="Images"
                                data={skyCoverage}
                                shape="circle"
                                onClick={handleSkyClick}
                                style={{ cursor: 'pointer' }}
                            >
                                {skyCoverage.map((entry, index) => (
                                    <Cell key={`cell-${index}`} fill={entry.color || CHART_ACCENT} />
                                ))}
                            </Scatter>
                        </ScatterChart>
                    </ResponsiveContainer>
                </div>

                {/* Camera Usage */}
                <div className="chart-card">
                    <div className="chart-header">
                        <h3 className="chart-title">Camera Usage</h3>
                    </div>
                    <ResponsiveContainer width="100%" height={300}>
                        <PieChart>
                            <Pie
                                data={cameraData}
                                cx="50%"
                                cy="50%"
                                innerRadius={60}
                                outerRadius={80}
                                paddingAngle={5}
                                dataKey="value"
                                onClick={handleCameraClick}
                                style={{ cursor: 'pointer' }}
                            >
                                {cameraData.map((entry, index) => (
                                    <Cell key={`cell-${index}`} fill={COLORS[index % COLORS.length]} style={{ cursor: 'pointer' }} />
                                ))}
                            </Pie>
                            <Tooltip
                                contentStyle={{
                                    ...CHART_TOOLTIP,
                                    borderRadius: '8px',
                                }}
                                itemStyle={{ color: CHART_TEXT }}
                            />
                            <Legend />
                        </PieChart>
                    </ResponsiveContainer>
                </div>

                {/* Exposure Distribution */}
                <div className="chart-card">
                    <div className="chart-header">
                        <h3 className="chart-title">Exposure Time Distribution</h3>
                    </div>
                    <ResponsiveContainer width="100%" height={300}>
                        <BarChart data={exposureData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" vertical={false} />
                            <XAxis
                                dataKey="range"
                                stroke={CHART_AXIS}
                                fontSize={12}
                                tickLine={false}
                                axisLine={false}
                            />
                            <YAxis
                                stroke={CHART_AXIS}
                                fontSize={12}
                                tickLine={false}
                                axisLine={false}
                            />
                            <Tooltip
                                cursor={{ fill: 'rgba(255,255,255,0.05)' }}
                                contentStyle={{
                                    ...CHART_TOOLTIP,
                                    borderRadius: '8px',
                                }}
                            />

                            <Bar
                                dataKey="count"
                                fill={CHART_PRIMARY}
                                radius={[4, 4, 0, 0]}
                                onClick={handleExposureClick}
                                style={{ cursor: 'pointer' }}
                            />
                        </BarChart>
                    </ResponsiveContainer>
                </div>

                {/* Filter Usage */}
                <div className="chart-card">
                    <div className="chart-header">
                        <h3 className="chart-title">Filter Usage</h3>
                    </div>
                    <ResponsiveContainer width="100%" height={300}>
                        <PieChart>
                            <Pie
                                data={filterData}
                                cx="50%"
                                cy="50%"
                                innerRadius={60}
                                outerRadius={80}
                                paddingAngle={5}
                                dataKey="value"
                                label={({ name, percent }) => `${(percent * 100).toFixed(0)}%`}
                                onClick={handleFilterClick}
                                style={{ cursor: 'pointer' }}
                            >
                                {filterData.map((entry, index) => (
                                    <Cell key={`cell-${index}`} fill={entry.color} style={{ cursor: 'pointer' }} />
                                ))}
                            </Pie>
                            <Tooltip
                                contentStyle={{
                                    ...CHART_TOOLTIP,
                                    borderRadius: '8px',
                                }}
                                itemStyle={{ color: CHART_TEXT }}
                            />
                            <Legend />
                        </PieChart>
                    </ResponsiveContainer>
                </div>

                {/* Rotation Angles */}
                <div className="chart-card">
                    <div className="chart-header">
                        <h3 className="chart-title">Rotation Angles (0-360°)</h3>
                    </div>
                    <ResponsiveContainer width="100%" height={300}>
                        <BarChart data={rotationData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" vertical={false} />
                            <XAxis
                                dataKey="range"
                                stroke={CHART_AXIS}
                                fontSize={10}
                                tickLine={false}
                                axisLine={false}
                                interval={1}
                            />
                            <YAxis
                                stroke={CHART_AXIS}
                                fontSize={12}
                                tickLine={false}
                                axisLine={false}
                            />
                            <Tooltip
                                cursor={{ fill: 'rgba(255,255,255,0.05)' }}
                                contentStyle={{
                                    ...CHART_TOOLTIP,
                                    borderRadius: '8px',
                                }}
                            />
                            <Bar
                                dataKey="count"
                                fill={CHART_SECONDARY}
                                radius={[4, 4, 0, 0]}
                                onClick={handleRotationClick}
                                style={{ cursor: 'pointer' }}
                            />
                        </BarChart>
                    </ResponsiveContainer>
                </div>

                {/* Pixel Scale Distribution */}
                <div className="chart-card">
                    <div className="chart-header">
                        <h3 className="chart-title">Pixel Scale Distribution (arcsec/px)</h3>
                    </div>
                    <ResponsiveContainer width="100%" height={300}>
                        <BarChart data={scaleData}>
                            <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.1)" vertical={false} />
                            <XAxis
                                dataKey="range"
                                stroke={CHART_AXIS}
                                fontSize={12}
                                tickLine={false}
                                axisLine={false}
                            />
                            <YAxis
                                stroke={CHART_AXIS}
                                fontSize={12}
                                tickLine={false}
                                axisLine={false}
                            />
                            <Tooltip
                                cursor={{ fill: 'rgba(255,255,255,0.05)' }}
                                contentStyle={{
                                    ...CHART_TOOLTIP,
                                    borderRadius: '8px',
                                }}
                            />
                            <Bar
                                dataKey="count"
                                fill={CHART_SUCCESS}
                                radius={[4, 4, 0, 0]}
                                onClick={handleScaleClick}
                                style={{ cursor: 'pointer' }}
                            />
                        </BarChart>
                    </ResponsiveContainer>
                </div>

                {/* Q2: median star size per rig */}
                <RigStarSizeChart />
            </div>

            {/* Q1d: star quality statistics */}
            <QualityStatsSection />
        </div >
    );
}
