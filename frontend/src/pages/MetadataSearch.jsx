import React, { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useNavigate } from 'react-router-dom';
import axios from 'axios';
import { fetchImages } from '../api/client';
import {
    Search, Filter, X, ChevronLeft, ChevronRight, Settings,
    Download, Eye, Zap, Plus, Minus, RotateCcw
} from 'lucide-react';
import { API_BASE_URL } from '../api/client';
import './MetadataSearch.css';

const MetadataSearch = () => {
    const navigate = useNavigate();
    const [page, setPage] = useState(1);
    const [selectedImages, setSelectedImages] = useState(new Set());
    const [showFilters, setShowFilters] = useState(true);
    const [sortBy, setSortBy] = useState('capture_date');
    const [sortOrder, setSortOrder] = useState('desc');

    // Search Filters
    const [filters, setFilters] = useState({
        // Text search
        search: '',
        headerKey: '',
        headerValue: '',

        // Equipment
        camera: '',
        telescope: '',
        filter: '',

        // Exposure
        exposureMin: '',
        exposureMax: '',
        gainMin: '',
        gainMax: '',

        // Plate Solving
        plateSolved: 'all', // all, solved, unsolved

        // Classification
        subtype: 'all', // all, SUB_FRAME, INTEGRATION_MASTER, INTEGRATION_DEPRECATED

        // Dates
        dateFrom: '',
        dateTo: '',

        // Object
        object: '',
    });

    // Fetch images with advanced filters
    const { data, isLoading, isError } = useQuery({
        queryKey: ['metadata-search', page, filters, sortBy, sortOrder],
        queryFn: () => fetchImages({
            page,
            page_size: 15,
            sort_by: sortBy,
            sort_order: sortOrder,
            search: filters.search,
            header_key: filters.headerKey,
            header_value: filters.headerValue,
            camera: filters.camera,
            telescope: filters.telescope,
            filter: filters.filter,
            exposure_min: parseFloat(filters.exposureMin) || undefined,
            exposure_max: parseFloat(filters.exposureMax) || undefined,
            gain_min: parseFloat(filters.gainMin) || undefined,
            gain_max: parseFloat(filters.gainMax) || undefined,
            is_plate_solved: filters.plateSolved !== 'all' ? filters.plateSolved : undefined,
            subtype: filters.subtype !== 'all' ? filters.subtype : undefined,
            start_date: filters.dateFrom,
            end_date: filters.dateTo,
            object_name: filters.object,
        }),
        keepPreviousData: true
    });

    const handleFilterChange = (field, value) => {
        setFilters(prev => ({ ...prev, [field]: value }));
        setPage(1);
    };

    const handleReset = () => {
        setFilters({
            search: '',
            headerKey: '',
            headerValue: '',
            camera: '',
            telescope: '',
            filter: '',
            exposureMin: '',
            exposureMax: '',
            gainMin: '',
            gainMax: '',
            plateSolved: 'all',
            subtype: 'all',
            dateFrom: '',
            dateTo: '',
            object: '',
        });
    };

    const handleSort = (column) => {
        if (sortBy === column) {
            setSortOrder(sortOrder === 'asc' ? 'desc' : 'asc');
        } else {
            setSortBy(column);
            setSortOrder('asc');
        }
        setPage(1);
    };

    const toggleImageSelection = (imageId) => {
        const newSelected = new Set(selectedImages);
        if (newSelected.has(imageId)) {
            newSelected.delete(imageId);
        } else {
            newSelected.add(imageId);
        }
        setSelectedImages(newSelected);
    };

    const toggleSelectAll = () => {
        if (selectedImages.size === data?.items?.length) {
            setSelectedImages(new Set());
        } else {
            const allIds = new Set(data?.items?.map(img => img.id) || []);
            setSelectedImages(allIds);
        }
    };

    const handleExportSelected = async () => {
        if (selectedImages.size === 0) {
            alert('Please select images to export');
            return;
        }

        const selectedData = data.items.filter(img => selectedImages.has(img.id));
        const csv = generateCSV(selectedData);
        const blob = new Blob([csv], { type: 'text/csv' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `metadata_export_${new Date().toISOString().split('T')[0]}.csv`;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const generateCSV = (images) => {
        const headers = [
            'File Name', 'Format', 'Object', 'Camera', 'Telescope', 'Filter',
            'Exposure (s)', 'Gain', 'ISO', 'Plate Solved', 'RA', 'Dec',
            'Capture Date', 'Rating', 'Classification'
        ];

        let csv = headers.join(',') + '\n';

        images.forEach(img => {
            const row = [
                `"${img.file_name}"`,
                img.file_format,
                `"${img.object_name || ''}"`,
                `"${img.camera_name || ''}"`,
                `"${img.telescope_name || ''}"`,
                `"${img.filter_name || ''}"`,
                img.exposure_time_seconds || '',
                img.gain || '',
                img.iso_speed || '',
                img.is_plate_solved ? 'Yes' : 'No',
                img.ra_center_degrees || '',
                img.dec_center_degrees || '',
                img.capture_date || '',
                img.rating || '',
                img.subtype || '',
            ];
            csv += row.join(',') + '\n';
        });

        return csv;
    };

    const hasActiveFilters = Object.values(filters).some(v => v && v !== 'all');

    return (
        <div className="metadata-search-page">
            {/* Header */}
            <header className="page-header">
                <h1>Metadata Search</h1>
                <p className="subtitle">Powerful search and filtering across all image metadata</p>
            </header>

            {/* Main Content */}
            <div className="search-layout">
                {/* Sidebar Filters */}
                <aside className={`filters-sidebar ${showFilters ? 'show' : 'hide'}`}>
                    <div className="filter-header">
                        <h2>Filters</h2>
                        <button
                            className="btn-filter-toggle"
                            onClick={() => setShowFilters(!showFilters)}
                            title="Toggle filters"
                        >
                            <Settings size={18} />
                        </button>
                    </div>

                    <div className="filters-content">
                        {/* Quick Search */}
                        <div className="filter-group">
                            <label className="filter-label">Quick Search</label>
                            <input
                                type="text"
                                className="filter-input"
                                placeholder="File name, object..."
                                value={filters.search}
                                onChange={(e) => handleFilterChange('search', e.target.value)}
                            />
                        </div>

                        {/* Header Search */}
                        <div className="filter-group">
                            <label className="filter-label">FITS/EXIF Header</label>
                            <input
                                type="text"
                                className="filter-input filter-input-sm"
                                placeholder="Header key"
                                value={filters.headerKey}
                                onChange={(e) => handleFilterChange('headerKey', e.target.value)}
                            />
                            <input
                                type="text"
                                className="filter-input filter-input-sm"
                                placeholder="Header value"
                                value={filters.headerValue}
                                onChange={(e) => handleFilterChange('headerValue', e.target.value)}
                            />
                        </div>

                        {/* Equipment Filters */}
                        <div className="filter-group">
                            <label className="filter-label">🎥 Equipment</label>
                            <input
                                type="text"
                                className="filter-input filter-input-sm"
                                placeholder="Camera"
                                value={filters.camera}
                                onChange={(e) => handleFilterChange('camera', e.target.value)}
                            />
                            <input
                                type="text"
                                className="filter-input filter-input-sm"
                                placeholder="Telescope"
                                value={filters.telescope}
                                onChange={(e) => handleFilterChange('telescope', e.target.value)}
                            />
                            <input
                                type="text"
                                className="filter-input filter-input-sm"
                                placeholder="Filter"
                                value={filters.filter}
                                onChange={(e) => handleFilterChange('filter', e.target.value)}
                            />
                        </div>

                        {/* Exposure Filters */}
                        <div className="filter-group">
                            <label className="filter-label">⏱️ Exposure (seconds)</label>
                            <div className="filter-range">
                                <input
                                    type="number"
                                    className="filter-input filter-input-sm"
                                    placeholder="Min"
                                    value={filters.exposureMin}
                                    onChange={(e) => handleFilterChange('exposureMin', e.target.value)}
                                />
                                <span className="range-sep">—</span>
                                <input
                                    type="number"
                                    className="filter-input filter-input-sm"
                                    placeholder="Max"
                                    value={filters.exposureMax}
                                    onChange={(e) => handleFilterChange('exposureMax', e.target.value)}
                                />
                            </div>
                        </div>

                        {/* Gain Filters */}
                        <div className="filter-group">
                            <label className="filter-label">⚡ Gain</label>
                            <div className="filter-range">
                                <input
                                    type="number"
                                    className="filter-input filter-input-sm"
                                    placeholder="Min"
                                    value={filters.gainMin}
                                    onChange={(e) => handleFilterChange('gainMin', e.target.value)}
                                />
                                <span className="range-sep">—</span>
                                <input
                                    type="number"
                                    className="filter-input filter-input-sm"
                                    placeholder="Max"
                                    value={filters.gainMax}
                                    onChange={(e) => handleFilterChange('gainMax', e.target.value)}
                                />
                            </div>
                        </div>

                        {/* Object Filter */}
                        <div className="filter-group">
                            <label className="filter-label">🔭 Object Name</label>
                            <input
                                type="text"
                                className="filter-input filter-input-sm"
                                placeholder="e.g., M31, NGC7000"
                                value={filters.object}
                                onChange={(e) => handleFilterChange('object', e.target.value)}
                            />
                        </div>

                        {/* Plate Solved Filter */}
                        <div className="filter-group">
                            <label className="filter-label">📍 Plate Solve Status</label>
                            <select
                                className="filter-input"
                                value={filters.plateSolved}
                                onChange={(e) => handleFilterChange('plateSolved', e.target.value)}
                            >
                                <option value="all">All</option>
                                <option value="solved">Solved ✓</option>
                                <option value="unsolved">Not Solved ✗</option>
                            </select>
                        </div>

                        {/* Classification Filter */}
                        <div className="filter-group">
                            <label className="filter-label">⭐ Classification</label>
                            <select
                                className="filter-input"
                                value={filters.subtype}
                                onChange={(e) => handleFilterChange('subtype', e.target.value)}
                            >
                                <option value="all">All</option>
                                <option value="SUB_FRAME">Sub Frame</option>
                                <option value="INTEGRATION_MASTER">Integration Master</option>
                                <option value="INTEGRATION_DEPRECATED">Deprecated</option>
                                <option value="PLANETARY">Planetary</option>
                            </select>
                        </div>

                        {/* Date Range Filter */}
                        <div className="filter-group">
                            <label className="filter-label">📅 Capture Date</label>
                            <input
                                type="date"
                                className="filter-input filter-input-sm"
                                value={filters.dateFrom}
                                onChange={(e) => handleFilterChange('dateFrom', e.target.value)}
                            />
                            <input
                                type="date"
                                className="filter-input filter-input-sm"
                                value={filters.dateTo}
                                onChange={(e) => handleFilterChange('dateTo', e.target.value)}
                            />
                        </div>

                        {/* Reset Button */}
                        {hasActiveFilters && (
                            <button
                                className="btn btn-secondary"
                                onClick={handleReset}
                                style={{ width: '100%' }}
                            >
                                <RotateCcw size={16} />
                                Reset Filters
                            </button>
                        )}
                    </div>
                </aside>

                {/* Results Section */}
                <main className="results-section">
                    {/* Results Header */}
                    <div className="results-header">
                        <div className="results-info">
                            <h2>Results</h2>
                            {data?.total > 0 && (
                                <span className="result-count">
                                    {data.total} {data.total === 1 ? 'image' : 'images'}
                                    {selectedImages.size > 0 && ` • ${selectedImages.size} selected`}
                                </span>
                            )}
                        </div>

                        {selectedImages.size > 0 && (
                            <div className="action-buttons">
                                <button
                                    className="btn btn-secondary btn-sm"
                                    onClick={handleExportSelected}
                                >
                                    <Download size={16} />
                                    Export ({selectedImages.size})
                                </button>
                            </div>
                        )}
                    </div>

                    {/* Results Table */}
                    <div className="results-container">
                        {isLoading ? (
                            <div className="loading-state">Searching metadata...</div>
                        ) : isError ? (
                            <div className="error-state">Failed to load data</div>
                        ) : data?.items?.length === 0 ? (
                            <div className="empty-state">
                                <p>No images found matching your filters.</p>
                                {hasActiveFilters && (
                                    <button className="btn btn-secondary" onClick={handleReset}>
                                        Clear Filters
                                    </button>
                                )}
                            </div>
                        ) : (
                            <>
                                <div className="table-wrapper">
                                    <table className="metadata-table">
                                        <thead>
                                            <tr>
                                                <th className="th-checkbox">
                                                    <input
                                                        type="checkbox"
                                                        checked={selectedImages.size === data.items.length && data.items.length > 0}
                                                        onChange={toggleSelectAll}
                                                        title="Select all"
                                                    />
                                                </th>
                                                <th className="sortable" onClick={() => handleSort('file_name')}>
                                                    File Name {sortBy === 'file_name' && (sortOrder === 'asc' ? '▲' : '▼')}
                                                </th>
                                                <th className="sortable" onClick={() => handleSort('object_name')}>
                                                    Object {sortBy === 'object_name' && (sortOrder === 'asc' ? '▲' : '▼')}
                                                </th>
                                                <th className="sortable" onClick={() => handleSort('camera_name')}>
                                                    Camera {sortBy === 'camera_name' && (sortOrder === 'asc' ? '▲' : '▼')}
                                                </th>
                                                <th className="sortable" onClick={() => handleSort('telescope_name')}>
                                                    Telescope {sortBy === 'telescope_name' && (sortOrder === 'asc' ? '▲' : '▼')}
                                                </th>
                                                <th className="sortable" onClick={() => handleSort('exposure_time_seconds')}>
                                                    Exposure (s) {sortBy === 'exposure_time_seconds' && (sortOrder === 'asc' ? '▲' : '▼')}
                                                </th>
                                                <th className="sortable" onClick={() => handleSort('capture_date')}>
                                                    Date {sortBy === 'capture_date' && (sortOrder === 'asc' ? '▲' : '▼')}
                                                </th>
                                                <th className="sortable" onClick={() => handleSort('is_plate_solved')}>
                                                    Plate Solved {sortBy === 'is_plate_solved' && (sortOrder === 'asc' ? '▲' : '▼')}
                                                </th>
                                                <th className="sortable" onClick={() => handleSort('rating')}>
                                                    Rating {sortBy === 'rating' && (sortOrder === 'asc' ? '▲' : '▼')}
                                                </th>
                                                <th>Actions</th>
                                            </tr>
                                        </thead>
                                        <tbody>
                                            {data.items.map((img) => (
                                                <tr
                                                    key={img.id}
                                                    className={`data-row ${selectedImages.has(img.id) ? 'selected' : ''}`}
                                                    onClick={() => navigate(`/images/${img.id}/metadata`)}
                                                    style={{ cursor: 'pointer' }}
                                                >
                                                    <td className="td-checkbox" onClick={(e) => e.stopPropagation()}>
                                                        <input
                                                            type="checkbox"
                                                            checked={selectedImages.has(img.id)}
                                                            onChange={() => toggleImageSelection(img.id)}
                                                        />
                                                    </td>
                                                    <td className="td-filename">
                                                        <span className="filename-badge">{img.file_format}</span>
                                                        {img.file_name}
                                                    </td>
                                                    <td>{img.object_name || '-'}</td>
                                                    <td className="td-mono">{img.camera_name || '-'}</td>
                                                    <td className="td-mono">{img.telescope_name || '-'}</td>
                                                    <td className="td-numeric">
                                                        {img.exposure_time_seconds ? img.exposure_time_seconds.toFixed(2) : '-'}
                                                    </td>
                                                    <td className="td-date">
                                                        {img.capture_date ? new Date(img.capture_date).toLocaleDateString() : '-'}
                                                    </td>
                                                    <td className="td-centered">
                                                        <span className={`badge ${img.is_plate_solved ? 'badge-solved' : 'badge-unsolved'}`}>
                                                            {img.is_plate_solved ? '✓' : '✗'}
                                                        </span>
                                                    </td>
                                                    <td className="td-centered">
                                                        {img.rating ? `⭐ ${img.rating}` : '-'}
                                                    </td>
                                                    <td className="td-actions" onClick={(e) => e.stopPropagation()}>
                                                        <button
                                                            className="action-btn"
                                                            onClick={() => navigate(`/images/${img.id}`)}
                                                            title="View image details"
                                                        >
                                                            <Eye size={16} />
                                                        </button>
                                                    </td>
                                                </tr>
                                            ))}
                                        </tbody>
                                    </table>
                                </div>

                                {/* Pagination */}
                                <div className="pagination">
                                    <button
                                        disabled={page === 1}
                                        onClick={() => setPage(p => Math.max(1, p - 1))}
                                    >
                                        <ChevronLeft size={20} />
                                    </button>
                                    <span>Page {page} of {data?.total_pages || 1}</span>
                                    <button
                                        disabled={page >= (data?.total_pages || 1)}
                                        onClick={() => setPage(p => p + 1)}
                                    >
                                        <ChevronRight size={20} />
                                    </button>
                                </div>
                            </>
                        )}
                    </div>
                </main>
            </div>
        </div>
    );
};

export default MetadataSearch;
