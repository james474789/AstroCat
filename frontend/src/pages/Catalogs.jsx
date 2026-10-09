import { useState, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { fetchMessierCatalog, fetchNGCCatalog, fetchCaldwellCatalog, fetchNamedStarCatalog, fetchSh2Catalog, fetchTargetKeys, formatRA, formatDec } from '../api/client';
import { Orbit, Sparkles, Star, Cloud, CloudFog, CircleDot, Circle, Zap, Search, ArrowUp, ArrowDown, ArrowRight } from 'lucide-react';
import TelescopeIcon from '../components/icons/TelescopeIcon';
import { Button, Tabs, TabPanel, PageHeader, EmptyState, Spinner, Pagination } from '../components/ui';
import './Catalogs.css';

// Mirrors the backend's app.services.targets.normalize_designation just
// enough to check "does a target exist for this designation" (F2):
// upper-case, strip separators, strip leading zeros off the trailing digits.
function normalizeDesignation(s) {
    if (!s) return '';
    let text = s.trim().toUpperCase().replace(/[\s_-]+/g, '');
    const match = text.match(/^(.*?)(\d+)$/);
    if (match) {
        const digits = match[2].replace(/^0+/, '') || '0';
        text = match[1] + digits;
    }
    return text;
}

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
    const [counts, setCounts] = useState({ messier: 0, ngc: 0, caldwell: 0, stars: 0, sh2: 0 });
    const [targetKeySet, setTargetKeySet] = useState(new Set());

    // Load the set of target keys once so cards can link to their Target page (F2).
    useEffect(() => {
        fetchTargetKeys()
            .then((keys) => setTargetKeySet(new Set(keys)))
            .catch((err) => console.error('Failed to load target keys:', err));
    }, []);

    // Initial load for counts
    useEffect(() => {
        async function loadCounts() {
            try {
                const [messierData, ngcData, caldwellData, starsData, sh2Data] = await Promise.all([
                    fetchMessierCatalog({ page: 1, page_size: 1 }),
                    fetchNGCCatalog({ page: 1, page_size: 1, catalog: 'NGC' }),
                    fetchCaldwellCatalog({ page: 1, page_size: 1 }),
                    fetchNamedStarCatalog({ page: 1, page_size: 1 }),
                    fetchSh2Catalog({ page: 1, page_size: 1 })
                ]);
                setCounts({
                    messier: messierData.total,
                    ngc: ngcData.total,
                    caldwell: caldwellData.total,
                    stars: starsData.total,
                    sh2: sh2Data.total
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
            } else if (activeTab === 'caldwell') {
                data = await fetchCaldwellCatalog(params);
            } else if (activeTab === 'stars') {
                data = await fetchNamedStarCatalog(params);
            } else if (activeTab === 'sh2') {
                data = await fetchSh2Catalog(params);
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
        const iconProps = { size: 32, strokeWidth: 1.5 };
        if (!type) {
            if (activeTab === 'stars') return <Star {...iconProps} />;
            return <TelescopeIcon {...iconProps} />;
        }
        const types = {
            'Spiral Galaxy': <Orbit {...iconProps} />,
            'Elliptical Galaxy': <Circle {...iconProps} />,
            'Globular Cluster': <Sparkles {...iconProps} />,
            'Open Cluster': <Star {...iconProps} />,
            'Diffuse Nebula': <Cloud {...iconProps} />,
            'Planetary Nebula': <CircleDot {...iconProps} />,
            'Emission Nebula': <CloudFog {...iconProps} />,
            'Supernova Remnant': <Zap {...iconProps} />,
            'SHARPLESS': <CloudFog {...iconProps} />,
        };
        return types[type] || <TelescopeIcon {...iconProps} />;
    };

    const catalogTabs = [
        { value: 'messier', label: 'Messier', icon: <Orbit size={18} strokeWidth={1.5} />, count: counts.messier },
        { value: 'ngc', label: 'NGC', icon: <TelescopeIcon size={18} strokeWidth={1.5} />, count: counts.ngc },
        { value: 'caldwell', label: 'Caldwell', icon: <Sparkles size={18} strokeWidth={1.5} />, count: counts.caldwell },
        { value: 'stars', label: 'Stars', icon: <Star size={18} strokeWidth={1.5} />, count: counts.stars },
        { value: 'sh2', label: 'Sharpless', icon: <CloudFog size={18} strokeWidth={1.5} />, count: counts.sh2 },
    ];

    return (
        <div className="page-catalogs">
            <PageHeader title="Catalogs" subtitle="Browse celestial catalogs" />

            <Tabs
                aria-label="Catalogs"
                className="catalog-tabs"
                items={catalogTabs}
                value={activeTab}
                onChange={handleTabChange}
                panelIdPrefix="catalog"
            />

            <TabPanel panelIdPrefix="catalog" value={activeTab}>
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
                            <option value="default">Default Sort ({activeTab === 'messier' ? 'M#' : (activeTab === 'ngc' ? 'NGC#' : activeTab === 'caldwell' ? 'C#' : activeTab === 'sh2' ? 'Sh2#' : 'Name')})</option>
                            <option value="exposure">Cumulative Exposure</option>
                            <option value="ra">Right Ascension (RA)</option>
                        </select>

                        <Button
                            iconOnly
                            variant="tinted"
                            icon={sortOrder === 'asc' ? <ArrowUp size={16} /> : <ArrowDown size={16} />}
                            aria-label={sortOrder === 'asc' ? 'Sort ascending' : 'Sort descending'}
                            onClick={() => {
                                setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
                                setCurrentPage(1);
                            }}
                        />
                    </div>
                </div>
            </div>

            {/* Objects Grid */}
            {loading ? (
                <div className="loading-state">
                    <Spinner size={32} label="Loading catalog" />
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
                                        View Images <ArrowRight size={14} />
                                    </Link>
                                    {obj.image_count > 0 && targetKeySet.has(normalizeDesignation(obj.designation)) && (
                                        <Link
                                            to={`/targets/${encodeURIComponent(normalizeDesignation(obj.designation))}`}
                                            className="view-images-link"
                                        >
                                            Target page <ArrowRight size={14} />
                                        </Link>
                                    )}
                                </div>
                            </div>
                        ))}
                    </div>

                    {/* Pagination */}
                    <Pagination
                        page={currentPage}
                        totalPages={totalPages}
                        totalItems={totalResults}
                        itemLabel="objects"
                        onPageChange={setCurrentPage}
                    />
                </>
            )}

            {objects.length === 0 && !loading && (
                <EmptyState
                    icon={<Search size={48} strokeWidth={1.5} />}
                    title="No objects found"
                    description="Try a different search term"
                />
            )}
            </TabPanel>
        </div>
    );
}
