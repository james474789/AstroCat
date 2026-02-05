import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { fetchMessierCatalog, fetchNGCCatalog, fetchNamedStarCatalog, formatRA, formatDec } from '../api/client';
import './Catalogs.css';

export default function Catalogs() {
    const [activeTab, setActiveTab] = useState('messier');
    const [objects, setObjects] = useState([]);
    const [searchQuery, setSearchQuery] = useState('');
    const [debouncedQuery, setDebouncedQuery] = useState('');
    const [loading, setLoading] = useState(true);
    const [currentPage, setCurrentPage] = useState(1);
    const [totalPages, setTotalPages] = useState(1);
    const [totalResults, setTotalResults] = useState(0);
    const [hasImagesOnly, setHasImagesOnly] = useState(false);
    const [sortBy, setSortBy] = useState('default');
    const [sortOrder, setSortOrder] = useState('asc');
    const [counts, setCounts] = useState({ messier: 0, ngc: 0, stars: 0 });

    // Initial load for counts
    useEffect(() => {
        async function loadCounts() {
            try {
                const [messierData, ngcData, starsData] = await Promise.all([
                    fetchMessierCatalog({ page: 1, page_size: 1 }),
                    fetchNGCCatalog({ page: 1, page_size: 1, catalog: 'NGC' }),
                    fetchNamedStarCatalog({ page: 1, page_size: 1 })
                ]);
                setCounts({
                    messier: messierData.total,
                    ngc: ngcData.total,
                    stars: starsData.total
                });
            } catch (error) {
                console.error('Failed to load counts:', error);
            }
        }
        loadCounts();
    }, []);

    // Debounce search query
    useEffect(() => {
        const timer = setTimeout(() => {
            setDebouncedQuery(searchQuery);
            setCurrentPage(1); // Reset to page 1 on search
        }, 500);
        return () => clearTimeout(timer);
    }, [searchQuery]);

    useEffect(() => {
        loadCatalog();
    }, [activeTab, debouncedQuery, currentPage, hasImagesOnly, sortBy, sortOrder]);

    async function loadCatalog() {
        setLoading(true);
        try {
            const params = {
                page: currentPage,
                page_size: activeTab === 'messier' ? 24 : 50,
                q: debouncedQuery,
                catalog: activeTab === 'ngc' ? 'NGC' : undefined,
                has_images: hasImagesOnly,
                sort_by: sortBy,
                sort_order: sortOrder
            };

            let data;
            if (activeTab === 'messier') {
                data = await fetchMessierCatalog(params);
            } else if (activeTab === 'ngc') {
                data = await fetchNGCCatalog(params);
            } else if (activeTab === 'stars') {
                data = await fetchNamedStarCatalog(params);
            }

            setObjects(data.items);
            setTotalResults(data.total);
            setTotalPages(data.total_pages);
        } catch (error) {
            console.error('Failed to load catalog:', error);
        } finally {
            setLoading(false);
        }
    }

    // Reset page when switching tabs
    const handleTabChange = (tab) => {
        setActiveTab(tab);
        setCurrentPage(1);
        setSearchQuery('');
    };

    // Object type icons
    const getTypeIcon = (type) => {
        if (!type) {
            if (activeTab === 'stars') return '⭐';
            return '🔭';
        }
        const types = {
            'Spiral Galaxy': '🌀',
            'Elliptical Galaxy': '⚪',
            'Globular Cluster': '✨',
            'Open Cluster': '⭐',
            'Diffuse Nebula': '☁️',
            'Planetary Nebula': '💫',
            'Emission Nebula': '🌫️',
            'Supernova Remnant': '💥',
        };
        return types[type] || '🔭';
    };

    return (
        <div className="catalogs-page">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Catalogs</h1>
                    <p className="page-subtitle">Browse celestial catalogs</p>
                </div>
            </div>

            {/* Tabs */}
            <div className="catalog-tabs">
                <button
                    className={`catalog-tab ${activeTab === 'messier' ? 'active' : ''}`}
                    onClick={() => handleTabChange('messier')}
                >
                    <span className="tab-icon">🌌</span>
                    <span className="tab-label">Messier</span>
                    <span className="tab-count">{counts.messier.toLocaleString()} objects</span>
                </button>
                <button
                    className={`catalog-tab ${activeTab === 'ngc' ? 'active' : ''}`}
                    onClick={() => handleTabChange('ngc')}
                >
                    <span className="tab-icon">🔭</span>
                    <span className="tab-label">NGC</span>
                    <span className="tab-count">{counts.ngc.toLocaleString()} objects</span>
                </button>
                <button
                    className={`catalog-tab ${activeTab === 'stars' ? 'active' : ''}`}
                    onClick={() => handleTabChange('stars')}
                >
                    <span className="tab-icon">⭐</span>
                    <span className="tab-label">Stars</span>
                    <span className="tab-count">{counts.stars.toLocaleString()} objects</span>
                </button>
            </div>

            {/* Filters & Search */}
            <div className="catalog-toolbar">
                <div className="catalog-search">
                    <input
                        type="text"
                        className="input"
                        placeholder={`Search ${activeTab.toUpperCase()} objects...`}
                        value={searchQuery}
                        onChange={(e) => setSearchQuery(e.target.value)}
                    />
                    <span className="search-results-count">
                        {loading ? '...' : `${totalResults.toLocaleString()} objects matching`}
                    </span>
                </div>

                <div className="catalog-filters">
                    <label className="checkbox-label">
                        <input
                            type="checkbox"
                            checked={hasImagesOnly}
                            onChange={(e) => {
                                setHasImagesOnly(e.target.checked);
                                setCurrentPage(1);
                            }}
                        />
                        <span>Only show items with images</span>
                    </label>

                    <div className="sort-controls">
                        <select
                            className="input sort-select"
                            value={sortBy}
                            onChange={(e) => {
                                setSortBy(e.target.value);
                                setCurrentPage(1);
                            }}
                        >
                            <option value="default">Default Sort ({activeTab === 'messier' ? 'M#' : 'NGC#'})</option>
                            <option value="exposure">Cumulative Exposure</option>
                            <option value="ra">Right Ascension (RA)</option>
                        </select>

                        <button
                            className="btn btn-icon sort-order-btn"
                            title={sortOrder === 'asc' ? 'Ascending' : 'Descending'}
                            onClick={() => {
                                setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
                                setCurrentPage(1);
                            }}
                        >
                            {sortOrder === 'asc' ? '↑' : '↓'}
                        </button>
                    </div>
                </div>
            </div>

            {/* Objects Grid */}
            {loading ? (
                <div className="loading-state">
                    <div className="spinner" />
                    <p>Loading catalog...</p>
                </div>
            ) : (
                <>
                    <div className="catalog-grid">
                        {objects.map(obj => (
                            <div key={obj.id} className="catalog-card">
                                <Link
                                    to={`/search?object_name=${encodeURIComponent(obj.designation)}`}
                                    className="catalog-card-header-link"
                                >
                                    <div className="catalog-card-header">
                                        <span className="object-type-icon">{getTypeIcon(obj.object_type)}</span>
                                        <div className="object-designation">
                                            <h3>{obj.designation}</h3>
                                            <span className="object-type">{obj.object_type}</span>
                                        </div>
                                    </div>

                                    <div className="catalog-card-body">
                                        <h4 className="object-name">{obj.common_name || 'N/A'}</h4>

                                        <div className="detail-item">
                                            <span className="detail-label">RA</span>
                                            <span className="detail-value">{formatRA(obj.ra_degrees)}</span>
                                        </div>
                                        <div className="detail-item">
                                            <span className="detail-label">Dec</span>
                                            <span className="detail-value">{formatDec(obj.dec_degrees)}</span>
                                        </div>
                                        <div className="detail-item">
                                            <span className="detail-label">Size</span>
                                            <span className="detail-value">
                                                {obj.angular_size_arcmin
                                                    ? obj.angular_size_arcmin
                                                    : (obj.major_axis_arcmin
                                                        ? `${obj.major_axis_arcmin}' ${obj.minor_axis_arcmin ? `× ${obj.minor_axis_arcmin}'` : ''}`
                                                        : '--')}
                                            </span>
                                        </div>
                                        <div className="detail-item">
                                            <span className="detail-label">Magnitude</span>
                                            <span className="detail-value">{obj.apparent_magnitude?.toFixed(1) || '--'}</span>
                                        </div>
                                        <div className="detail-item">
                                            <span className="detail-label">Exposure Time</span>
                                            <span className="detail-value">
                                                {obj.cumulative_exposure_seconds > 0
                                                    ? (obj.cumulative_exposure_seconds / 3600).toFixed(1) + 'h'
                                                    : 'None'}
                                            </span>
                                        </div>
                                        <div className="detail-item">
                                            <span className="detail-label">Constellation</span>
                                            <span className="detail-value">{obj.constellation}</span>
                                        </div>
                                    </div>
                                </Link>

                                <div className="catalog-card-footer">
                                    <Link
                                        to={`/search?object_name=${encodeURIComponent(obj.designation)}`}
                                        className="view-images-link"
                                    >
                                        View Images →
                                    </Link>
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Pagination */}
                    {totalPages > 1 && (
                        <div className="pagination">
                            <button
                                className="btn btn-secondary"
                                disabled={currentPage === 1}
                                onClick={() => setCurrentPage(p => p - 1)}
                            >
                                Previous
                            </button>

                            <div className="pagination-info">
                                Page {currentPage} of {totalPages}
                            </div>

                            <button
                                className="btn btn-secondary"
                                disabled={currentPage === totalPages}
                                onClick={() => setCurrentPage(p => p + 1)}
                            >
                                Next
                            </button>
                        </div>
                    )}
                </>
            )}

            {objects.length === 0 && !loading && (
                <div className="empty-state">
                    <div className="empty-state-icon">🔍</div>
                    <h3 className="empty-state-title">No objects found</h3>
                    <p className="empty-state-text">
                        Try a different search term
                    </p>
                </div>
            )}
        </div>
    );
}
