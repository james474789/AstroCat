import { useState, useEffect } from 'react';
import { useSearchParams } from 'react-router-dom';
import { fetchImages, API_BASE_URL } from '../api/client';
import ImageCard from '../components/images/ImageCard';
import FilterSection from '../components/layout/FilterSection';
import FilterChips from '../components/layout/FilterChips';
import RangeInput from '../components/layout/RangeInput';
import SpatialSearchInput from '../components/layout/SpatialSearchInput';
import FolderTree from '../components/layout/FolderTree';
import './Search.css';

// Helper: Convert degrees to HH:MM
function degreesToHMS(degrees) {
    if (!degrees && degrees !== 0) return '';
    const totalHours = parseFloat(degrees) / 15;
    const h = Math.floor(totalHours);
    const m = Math.round((totalHours - h) * 60);
    return `${h.toString().padStart(2, '0')}:${m.toString().padStart(2, '0')}`;
}

// Helper: Convert HH:MM to degrees
function hmsToDegrees(hms) {
    if (!hms) return '';
    const parts = hms.split(':');
    let h = 0, m = 0;
    if (parts.length >= 1) h = parseFloat(parts[0]) || 0;
    if (parts.length >= 2) m = parseFloat(parts[1]) || 0;
    return (h + m / 60) * 15;
}

export default function Search() {
    const [searchParams, setSearchParams] = useSearchParams();
    const [images, setImages] = useState([]);
    const [loading, setLoading] = useState(true);
    const [totalCount, setTotalCount] = useState(0);
    const [totalPages, setTotalPages] = useState(1);
    const currentPage = parseInt(searchParams.get('page')) || 1;
    const [thumbnailSize, setThumbnailSize] = useState(() => {
        const saved = localStorage.getItem('thumbnailSize');
        return saved ? parseInt(saved, 10) : 280;
    });
    const [imageContextMenu, setImageContextMenu] = useState(null); // { x, y, image }

    useEffect(() => {
        localStorage.setItem('thumbnailSize', thumbnailSize);
    }, [thumbnailSize]);

    // Handle Closing Context Menus
    useEffect(() => {
        const handleCloseMenu = () => setImageContextMenu(null);
        window.addEventListener('click', handleCloseMenu);
        window.addEventListener('scroll', handleCloseMenu, true);
        return () => {
            window.removeEventListener('click', handleCloseMenu);
            window.removeEventListener('scroll', handleCloseMenu, true);
        };
    }, []);

    // Filter states
    const [filters, setFilters] = useState({
        subtype: searchParams.get('subtype') || '',
        format: searchParams.get('format') || '',
        rating: searchParams.get('rating') || '',
        search: searchParams.get('search') || '',
        object_name: searchParams.get('object_name') || '',
        exposure_min: searchParams.get('exposure_min') || '',
        exposure_max: searchParams.get('exposure_max') || '',
        rotation_min: searchParams.get('rotation_min') || '',
        rotation_max: searchParams.get('rotation_max') || '',
        camera: searchParams.get('camera') || '',
        filter: searchParams.get('filter') || '',
        ra: searchParams.get('ra') || '',
        dec: searchParams.get('dec') || '',
        radius: searchParams.get('radius') || '',
        is_plate_solved: searchParams.get('is_plate_solved') || '',
        pixel_scale_min: searchParams.get('pixel_scale_min') || '',
        pixel_scale_max: searchParams.get('pixel_scale_max') || '',
        pixel_scale_max_exclusive: searchParams.get('pixel_scale_max_exclusive') === 'true',
        start_date: searchParams.get('start_date') || '',
        end_date: searchParams.get('end_date') || '',
        sort_by: searchParams.get('sort_by') || 'capture_date',
        sort_order: searchParams.get('sort_order') || 'desc',
        path: searchParams.get('path') || '',
    });

    // Local state for RA input to allow HH:MM editing
    const [raInput, setRaInput] = useState('');

    const [showFilters, setShowFilters] = useState(true);

    useEffect(() => {
        loadImages();
    }, [currentPage, searchParams]);

    // Sync filters form with URL params
    useEffect(() => {
        setFilters({
            subtype: searchParams.get('subtype') || '',
            format: searchParams.get('format') || '',
            rating: searchParams.get('rating') || '',
            search: searchParams.get('search') || '',
            object_name: searchParams.get('object_name') || '',
            exposure_min: searchParams.get('exposure_min') || '',
            exposure_max: searchParams.get('exposure_max') || '',
            rotation_min: searchParams.get('rotation_min') || '',
            rotation_max: searchParams.get('rotation_max') || '',
            camera: searchParams.get('camera') || '',
            filter: searchParams.get('filter') || '',
            ra: searchParams.get('ra') || '',
            dec: searchParams.get('dec') || '',
            radius: searchParams.get('radius') || '',
            is_plate_solved: searchParams.get('is_plate_solved') || '',
            pixel_scale_min: searchParams.get('pixel_scale_min') || '',
            pixel_scale_max: searchParams.get('pixel_scale_max') || '',
            pixel_scale_max_exclusive: searchParams.get('pixel_scale_max_exclusive') === 'true',
            start_date: searchParams.get('start_date') || '',
            end_date: searchParams.get('end_date') || '',
            sort_by: searchParams.get('sort_by') || 'capture_date',
            sort_order: searchParams.get('sort_order') || 'desc',
            path: searchParams.get('path') || '',
        });

        // Sync RA input display from URL param
        const raParam = searchParams.get('ra');
        if (raParam) {
            setRaInput(degreesToHMS(raParam));
        } else {
            setRaInput('');
        }
    }, [searchParams]);

    async function loadImages() {
        setLoading(true);
        try {
            // Build params directly from URL searchParams to ensure source of truth
            const params = {
                page: currentPage,
                page_size: 100,
            };

            // Add filters if present in URL
            if (searchParams.get('subtype')) params.subtype = searchParams.get('subtype');
            if (searchParams.get('format')) params.format = searchParams.get('format');
            if (searchParams.get('rating')) params.rating = searchParams.get('rating');
            if (searchParams.get('search')) params.search = searchParams.get('search');
            if (searchParams.get('object_name')) params.object_name = searchParams.get('object_name');
            if (searchParams.get('exposure_min')) params.exposure_min = searchParams.get('exposure_min');
            if (searchParams.get('exposure_max')) params.exposure_max = searchParams.get('exposure_max');
            if (searchParams.get('rotation_min')) params.rotation_min = searchParams.get('rotation_min');
            if (searchParams.get('rotation_max')) params.rotation_max = searchParams.get('rotation_max');
            if (searchParams.get('camera')) params.camera = searchParams.get('camera');
            if (searchParams.get('filter')) params.filter = searchParams.get('filter');
            if (searchParams.get('ra')) params.ra = searchParams.get('ra');
            if (searchParams.get('dec')) params.dec = searchParams.get('dec');
            if (searchParams.get('radius')) params.radius = searchParams.get('radius');
            if (searchParams.get('is_plate_solved')) params.is_plate_solved = searchParams.get('is_plate_solved');
            if (searchParams.get('pixel_scale_min')) params.pixel_scale_min = searchParams.get('pixel_scale_min');
            if (searchParams.get('pixel_scale_max')) params.pixel_scale_max = searchParams.get('pixel_scale_max');
            if (searchParams.get('pixel_scale_max_exclusive')) params.pixel_scale_max_exclusive = searchParams.get('pixel_scale_max_exclusive');
            if (searchParams.get('start_date')) params.start_date = searchParams.get('start_date');
            if (searchParams.get('end_date')) params.end_date = searchParams.get('end_date');
            if (searchParams.get('sort_by')) params.sort_by = searchParams.get('sort_by');
            if (searchParams.get('sort_order')) params.sort_order = searchParams.get('sort_order');
            if (searchParams.get('path')) params.path = searchParams.get('path');

            const data = await fetchImages(params);
            setImages(data.items);
            setTotalCount(data.total);
            setTotalPages(data.total_pages);

            // Store search context for navigation
            sessionStorage.setItem('currentSearchContext', JSON.stringify({
                ids: data.items.map(img => img.id),
                total: data.total,
                page: currentPage,
                pageSize: 100, // Matching the page_size in loadImages
                totalPages: data.total_pages,
                params: params // Store search params to potentially fetch more pages
            }));

            // Scroll restoration
            const lastClickedId = sessionStorage.getItem('lastClickedImageId');
            if (lastClickedId) {
                setTimeout(() => {
                    const element = document.getElementById(`image-${lastClickedId}`);
                    if (element) {
                        element.scrollIntoView({ behavior: 'smooth', block: 'center' });
                        sessionStorage.removeItem('lastClickedImageId');
                    }
                }, 100);
            }
        } catch (error) {
            console.error('Failed to load images:', error);
        } finally {
            setLoading(false);
        }
    }

    function handleFilterChange(key, value) {
        setFilters(prev => ({ ...prev, [key]: value }));
    }

    function applyCurrentFilters(updatedFilters, overrideRaString = null) {
        const params = new URLSearchParams();
        const raValue = overrideRaString !== null ? overrideRaString : (raInput ? hmsToDegrees(raInput).toFixed(4) : '');

        Object.entries({ ...updatedFilters, ra: raValue }).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') {
                params.set(key, value);
            }
        });
        params.set('page', '1');
        setSearchParams(params);
    }

    function applyFilters() {
        const params = new URLSearchParams();

        // Convert RA input (HH:MM) to degrees for URL
        const filtersToApply = { ...filters };
        if (raInput) {
            filtersToApply.ra = hmsToDegrees(raInput).toFixed(4);
        } else {
            filtersToApply.ra = '';
        }

        Object.entries(filtersToApply).forEach(([key, value]) => {
            if (value) params.set(key, value);
        });
        params.set('page', '1');
        setSearchParams(params);
        // loadImages(); // Handled by useEffect dependence on searchParams
    }

    function clearFilters() {
        setFilters({
            subtype: '',
            format: '',
            rating: '',
            object_name: '',
            exposure_min: '',
            exposure_max: '',
            rotation_min: '',
            rotation_max: '',
            camera: '',
            filter: '',
            ra: '',
            dec: '',
            radius: '',
            is_plate_solved: '',
            pixel_scale_min: '',
            pixel_scale_max: '',
            pixel_scale_max_exclusive: false,
            start_date: '',
            end_date: '',
            sort_by: 'capture_date',
            sort_order: 'desc',
            path: '',
        });
        setRaInput('');
        setSearchParams(new URLSearchParams());
    }

    function handleExportCsv() {
        // Use current searchParams which represent the active view
        const params = new URLSearchParams(searchParams);
        const url = `${API_BASE_URL}/images/export_csv?${params.toString()}`;
        // Trigger download
        window.location.href = url;
    }

    const handleImageContextMenu = (e, image) => {
        e.preventDefault();
        setImageContextMenu({
            x: e.clientX,
            y: e.clientY,
            image: image
        });
    };

    const handleExpandPath = () => {
        if (!imageContextMenu) return;
        const { image } = imageContextMenu;
        setImageContextMenu(null);

        if (image.file_path) {
            // Get directory path by removing filename
            const lastSlash = Math.max(image.file_path.lastIndexOf('/'), image.file_path.lastIndexOf('\\'));
            if (lastSlash !== -1) {
                const dirPath = image.file_path.substring(0, lastSlash);
                const updatedFilters = { ...filters, path: dirPath };
                setFilters(updatedFilters);
                applyCurrentFilters(updatedFilters);
            }
        }
    };

    return (
        <div className="search-page">
            <div className="page-header">
                <div>
                    <h1 className="page-title">Search Images</h1>
                    <p className="page-subtitle">
                        Browse and filter your astronomical image collection
                    </p>
                </div>
                <div className="header-actions" style={{ display: 'flex', gap: '10px' }}>
                    <button
                        className="btn btn-secondary"
                        onClick={handleExportCsv}
                        title="Export current results to CSV"
                    >
                        ⬇ Export CSV
                    </button>
                    <button
                        className="btn btn-secondary"
                        onClick={() => setShowFilters(!showFilters)}
                    >
                        {showFilters ? 'Hide Filters' : 'Show Filters'}
                    </button>
                </div>
            </div>

            <div className="search-layout">
                {/* Filters Sidebar */}
                {showFilters && (
                    <form className="filters-sidebar" onSubmit={(e) => { e.preventDefault(); applyFilters(); }}>
                        <div className="filters-header">
                            <h3>🔍 Search & Filter</h3>
                            <button type="button" className="btn btn-ghost btn-sm" onClick={clearFilters}>
                                Clear All
                            </button>
                        </div>

                        {/* Active Filters Display */}
                        <FilterChips
                            filters={filters}
                            onRemove={(key) => {
                                const updatedFilters = { ...filters, [key]: '' };
                                setFilters(updatedFilters);
                                if (key === 'ra') {
                                    setRaInput('');
                                    applyCurrentFilters(updatedFilters, '');
                                } else {
                                    applyCurrentFilters(updatedFilters);
                                }
                            }}
                        />

                        {/* Folder Structure */}
                        <FilterSection title="Folder Structure" icon="📂" defaultOpen={true}>
                            <FolderTree
                                selectedPath={filters.path}
                                onSelect={(path) => {
                                    const updatedFilters = { ...filters, path: path };
                                    setFilters(updatedFilters);
                                    applyCurrentFilters(updatedFilters);
                                }}
                            />
                        </FilterSection>

                        {/* Image Properties Section */}
                        <FilterSection title="Image Properties" icon="🖼️" defaultOpen={true}>
                            <div className="filter-group">
                                <label className="label">Image Type</label>
                                <select
                                    className="input select"
                                    value={filters.subtype}
                                    onChange={(e) => {
                                        const updatedFilters = { ...filters, subtype: e.target.value };
                                        setFilters(updatedFilters);
                                        applyCurrentFilters(updatedFilters);
                                    }}
                                >
                                    <option value="">All Types</option>
                                    <option value="SUB_FRAME">Sub Frames</option>
                                    <option value="INTEGRATION_MASTER">Masters</option>
                                    <option value="INTEGRATION_DEPRECATED">Deprecated</option>
                                    <option value="PLANETARY">Planetary</option>
                                </select>
                            </div>

                            <div className="filter-group">
                                <label className="label">File Format</label>
                                <select
                                    className="input select"
                                    value={filters.format}
                                    onChange={(e) => {
                                        const updatedFilters = { ...filters, format: e.target.value };
                                        setFilters(updatedFilters);
                                        applyCurrentFilters(updatedFilters);
                                    }}
                                >
                                    <option value="">All Formats</option>
                                    <option value="FITS">FITS</option>
                                    <option value="CR2">CR2</option>
                                    <option value="JPG">JPG</option>
                                    <option value="TIFF">TIFF</option>
                                </select>
                            </div>

                            <div className="filter-group">
                                <label className="label">Minimum Rating</label>
                                <select
                                    className="input select"
                                    value={filters.rating}
                                    onChange={(e) => {
                                        const updatedFilters = { ...filters, rating: e.target.value };
                                        setFilters(updatedFilters);
                                        applyCurrentFilters(updatedFilters);
                                    }}
                                >
                                    <option value="">All Ratings</option>
                                    <option value="1">★ 1 Star or higher</option>
                                    <option value="2">★★ 2 Stars or higher</option>
                                    <option value="3">★★★ 3 Stars or higher</option>
                                    <option value="4">★★★★ 4 Stars or higher</option>
                                    <option value="5">★★★★★ 5 Stars</option>
                                </select>
                            </div>

                            <div className="filter-group">
                                <label className="label">Search (Filename/Object)</label>
                                <input
                                    type="text"
                                    className="input"
                                    placeholder="e.g., M31_Light, NGC6888"
                                    value={filters.search}
                                    onChange={(e) => handleFilterChange('search', e.target.value)}
                                />
                            </div>
                        </FilterSection>

                        {/* Capture Settings Section */}
                        <FilterSection title="Capture Settings" icon="⚙️" defaultOpen={false}>
                            <div className="filter-group">
                                <RangeInput
                                    label="Exposure Time (seconds)"
                                    minValue={filters.exposure_min}
                                    maxValue={filters.exposure_max}
                                    onMinChange={(v) => handleFilterChange('exposure_min', v)}
                                    onMaxChange={(v) => handleFilterChange('exposure_max', v)}
                                    placeholder={{ min: '0', max: '300' }}
                                    unit="s"
                                />
                            </div>

                            <div className="filter-group">
                                <RangeInput
                                    label="Rotation (degrees)"
                                    minValue={filters.rotation_min}
                                    maxValue={filters.rotation_max}
                                    onMinChange={(v) => handleFilterChange('rotation_min', v)}
                                    onMaxChange={(v) => handleFilterChange('rotation_max', v)}
                                    placeholder={{ min: '0', max: '360' }}
                                    unit="°"
                                />
                            </div>

                            <div className="filter-group">
                                <label className="label">Filter Name</label>
                                <input
                                    type="text"
                                    className="input"
                                    placeholder="e.g. H-alpha, OIII"
                                    value={filters.filter}
                                    onChange={(e) => handleFilterChange('filter', e.target.value)}
                                />
                            </div>

                            <div className="filter-group">
                                <label className="label">Camera</label>
                                <input
                                    type="text"
                                    className="input"
                                    placeholder="e.g., ZWO ASI294MM"
                                    value={filters.camera}
                                    onChange={(e) => handleFilterChange('camera', e.target.value)}
                                />
                            </div>
                        </FilterSection>

                        {/* Observation Data Section */}
                        <FilterSection title="Observation Data" icon="🌙" defaultOpen={false}>
                            <div className="filter-group">
                                <label className="label">Object Name</label>
                                <input
                                    type="text"
                                    className="input"
                                    placeholder="e.g., NGC 6888, M31"
                                    value={filters.object_name}
                                    onChange={(e) => handleFilterChange('object_name', e.target.value)}
                                />
                            </div>

                            <div className="filter-group">
                                <label className="label">Capture Date Range</label>
                                <div className="date-range-inputs">
                                    <div>
                                        <label className="text-xs text-muted">From</label>
                                        <input
                                            type="date"
                                            className="input"
                                            value={filters.start_date}
                                            onChange={(e) => handleFilterChange('start_date', e.target.value)}
                                        />
                                    </div>
                                    <span className="range-separator">→</span>
                                    <div>
                                        <label className="text-xs text-muted">To</label>
                                        <input
                                            type="date"
                                            className="input"
                                            value={filters.end_date}
                                            onChange={(e) => handleFilterChange('end_date', e.target.value)}
                                        />
                                    </div>
                                </div>
                            </div>
                        </FilterSection>

                        {/* Advanced Search Section */}
                        <FilterSection title="Advanced Search" icon="🔭" defaultOpen={false}>
                            <div className="filter-group">
                                <SpatialSearchInput
                                    raHms={raInput}
                                    dec={filters.dec}
                                    radius={filters.radius}
                                    onRaChange={setRaInput}
                                    onDecChange={(v) => handleFilterChange('dec', v)}
                                    onRadiusChange={(v) => handleFilterChange('radius', v)}
                                />
                            </div>

                            <div className="filter-group">
                                <label className="label">Plate Solved</label>
                                <select
                                    className="input select"
                                    value={filters.is_plate_solved}
                                    onChange={(e) => {
                                        const updatedFilters = { ...filters, is_plate_solved: e.target.value };
                                        setFilters(updatedFilters);
                                        applyCurrentFilters(updatedFilters);
                                    }}
                                >
                                    <option value="">Any</option>
                                    <option value="solved">Solved (Astrometry)</option>
                                    <option value="imported">Imported (WCS Header)</option>
                                    <option value="unsolved">Unsolved</option>
                                </select>
                            </div>

                            <div className="filter-item">
                                <label className="filter-label">Pixel Scale (arcsec/px)</label>
                                <div className="flex gap-sm">
                                    <input
                                        type="number"
                                        className="input"
                                        placeholder="Min"
                                        value={filters.pixel_scale_min}
                                        onChange={(e) => handleFilterChange('pixel_scale_min', e.target.value)}
                                        step="0.1"
                                    />
                                    <span className="self-center">-</span>
                                    <input
                                        type="number"
                                        className="input"
                                        placeholder="Max"
                                        value={filters.pixel_scale_max}
                                        onChange={(e) => handleFilterChange('pixel_scale_max', e.target.value)}
                                        step="0.1"
                                    />
                                </div>
                            </div>
                        </FilterSection>

                        <button type="submit" className="btn btn-primary btn-lg btn-apply-filters">
                            ✓ Apply Filters
                        </button>
                    </form>
                )}

                {/* Results Grid */}
                <div className="search-results">
                    <div className="results-header">
                        <div style={{ display: 'flex', alignItems: 'center', gap: '24px' }}>
                            <span className="results-count">
                                {loading ? 'Loading...' : `${totalCount.toLocaleString()} images found`}
                            </span>
                            <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                <span style={{ fontSize: '0.8rem', color: 'var(--color-text-secondary)' }}>Size</span>
                                <input
                                    type="range"
                                    min="150"
                                    max="500"
                                    value={thumbnailSize}
                                    onChange={(e) => setThumbnailSize(Number(e.target.value))}
                                    style={{ width: '80px', cursor: 'pointer', accentColor: 'var(--color-primary)' }}
                                    title="Adjust thumbnail size"
                                />
                            </div>
                        </div>
                        <div className="sort-controls">
                            <div className="sort-group">
                                <label className="sort-label">Sort by:</label>
                                <select
                                    className="input select sort-select"
                                    value={filters.sort_by}
                                    onChange={(e) => {
                                        const updatedFilters = { ...filters, sort_by: e.target.value };
                                        setFilters(updatedFilters);
                                        applyCurrentFilters(updatedFilters);
                                    }}
                                >
                                    <option value="capture_date">Capture Date</option>
                                    <option value="exposure_time_seconds">Exposure Time</option>
                                    <option value="file_name">File Name</option>
                                    <option value="file_size_bytes">File Size</option>
                                    <option value="rating">Rating</option>
                                    <option value="file_last_modified">File Modified</option>
                                    <option value="file_created">File Created</option>
                                </select>
                                <select
                                    className="input select sort-select"
                                    value={filters.sort_order}
                                    onChange={(e) => {
                                        const updatedFilters = { ...filters, sort_order: e.target.value };
                                        setFilters(updatedFilters);
                                        applyCurrentFilters(updatedFilters);
                                    }}
                                >
                                    <option value="desc">Descending</option>
                                    <option value="asc">Ascending</option>
                                </select>
                            </div>
                        </div>
                    </div>

                    {loading ? (
                        <div className="loading-grid" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${thumbnailSize}px, 1fr))` }}>
                            {Array.from({ length: 8 }).map((_, i) => (
                                <div key={i} className="skeleton image-skeleton" />
                            ))}
                        </div>
                    ) : images.length === 0 ? (
                        <div className="empty-state">
                            <div className="empty-state-icon">🔭</div>
                            <h3 className="empty-state-title">No images found</h3>
                            <p className="empty-state-text">
                                Try adjusting your filters or search criteria
                            </p>
                        </div>
                    ) : (
                        <div className="image-grid" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${thumbnailSize}px, 1fr))` }}>
                            {images.map(image => (
                                <ImageCard
                                    key={image.id}
                                    image={image}
                                    onContextMenu={handleImageContextMenu}
                                />
                            ))}
                        </div>
                    )}

                    {/* Pagination */}
                    {totalPages > 1 && (
                        <div className="pagination">
                            <button
                                className="btn btn-secondary"
                                disabled={currentPage === 1}
                                onClick={() => {
                                    const params = new URLSearchParams(searchParams);
                                    params.set('page', (currentPage - 1).toString());
                                    setSearchParams(params);
                                    window.scrollTo(0, 0);
                                }}
                            >
                                Previous
                            </button>

                            <div className="pagination-info">
                                Page {currentPage} of {totalPages}
                            </div>

                            <button
                                className="btn btn-secondary"
                                disabled={currentPage === totalPages}
                                onClick={() => {
                                    const params = new URLSearchParams(searchParams);
                                    params.set('page', (currentPage + 1).toString());
                                    setSearchParams(params);
                                    window.scrollTo(0, 0);
                                }}
                            >
                                Next
                            </button>
                        </div>
                    )}
                </div>
            </div>

            {imageContextMenu && (
                <div
                    className="folder-context-menu"
                    style={{
                        position: 'fixed',
                        top: imageContextMenu.y,
                        left: imageContextMenu.x,
                        zIndex: 1000
                    }}
                    onClick={e => e.stopPropagation()}
                >
                    <div className="menu-item" onClick={handleExpandPath}>
                        📂 Expand Path
                    </div>
                </div>
            )}
        </div>
    );
}
