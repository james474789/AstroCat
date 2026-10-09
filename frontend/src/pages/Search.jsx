import { useState, useEffect, useMemo } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import {
    Download, RefreshCw, Contrast, Search as SearchIcon, FolderOpen, Image as ImageIcon, Settings, Sparkles, Moon,
    Check, X, ArrowRight, ArrowUp, ArrowDown, SlidersHorizontal, LayoutGrid, List, MoreHorizontal, Pencil, Tags,
    FileText, AlertTriangle, Star,
} from 'lucide-react';
import TelescopeIcon from '../components/icons/TelescopeIcon';
import { useQuery } from '@tanstack/react-query';
import {
    fetchImages, API_BASE_URL, bulkUpdateImageType, bulkSyncMetadata, bulkUpdateFrameType,
    bulkAssignRig, fetchEquipment,
} from '../api/client';
import ImageCard from '../components/images/ImageCard';
import FilterSection from '../components/layout/FilterSection';
import FilterChips from '../components/layout/FilterChips';
import RangeInput from '../components/layout/RangeInput';
import SpatialSearchInput from '../components/layout/SpatialSearchInput';
import FolderTree from '../components/layout/FolderTree';
import ActionMenu from '../components/layout/ActionMenu';
import { useQualityUnits } from '../context/QualityUnitsContext';
import { useIsMobile } from '../hooks/useMediaQuery';
import { Button, Dialog, EmptyState, PageHeader, Pagination, SegmentedControl, Skeleton, useToast } from '../components/ui';
import './Search.css';

const PAGE_SIZE = 100;

// Q1d: star quality filters (units for fwhm/hfr bounds travel in quality_units).
const QUALITY_KEYS = ['fwhm_min', 'fwhm_max', 'hfr_max', 'eccentricity_max', 'star_count_min',
    'quality_flag', 'star_metrics_status', 'quality_units'];

// Every string filter that lives in the URL and is sent to the images API.
// telescope, gain_*, header_* came over from the retired Metadata Search page.
const FILTER_KEYS = [
    'subtype', 'format', 'rating', 'search', 'object_name', 'exposure_min', 'exposure_max',
    'rotation_min', 'rotation_max', 'camera', 'telescope', 'filter', 'gain_min', 'gain_max',
    'header_key', 'header_value', 'ra', 'dec', 'radius', 'is_plate_solved', 'pixel_scale_min',
    'pixel_scale_max', 'start_date', 'end_date', 'path',
    // F1: '' means the default (Lights only, not written to the URL).
    // 'ALL' means no filter. Otherwise DARK/FLAT/BIAS/DARK_FLAT.
    'frame_type', 'target_key', 'rig_id', 'rig_bucket',
    ...QUALITY_KEYS,
];

// URL params that are not filters (they don't count towards the Filters badge).
const NON_FILTER_PARAMS = ['sort_by', 'sort_order', 'quality_units', 'page', 'view', 'pixel_scale_max_exclusive'];

function filtersFromParams(sp) {
    return {
        ...Object.fromEntries(FILTER_KEYS.map((k) => [k, sp.get(k) || ''])),
        pixel_scale_max_exclusive: sp.get('pixel_scale_max_exclusive') === 'true',
        sort_by: sp.get('sort_by') || 'capture_date',
        sort_order: sp.get('sort_order') || 'desc',
    };
}

// [value, menu label, short toolbar label]
const SORT_FIELDS = [
    ['capture_date', 'Capture date', 'Captured'],
    ['exposure_time_seconds', 'Exposure time', 'Exposure'],
    ['file_name', 'File name', 'Name'],
    ['file_size_bytes', 'File size', 'Size'],
    ['rating', 'Rating', 'Rating'],
    ['file_last_modified', 'File modified', 'Modified'],
    ['file_created', 'File created', 'Created'],
    ['fwhm_arcsec', 'FWHM (arcsec)', 'FWHM ″'],
    ['fwhm_px', 'FWHM (pixels)', 'FWHM px'],
    ['hfr_px', 'HFR (pixels)', 'HFR'],
    ['eccentricity', 'Eccentricity', 'Eccentricity'],
    ['star_count', 'Star count', 'Stars'],
];
// Extra sorts reachable from the list view's column headers.
const LIST_SORT_FIELDS = [
    ['object_name', 'Object', 'Object'],
    ['camera_name', 'Camera', 'Camera'],
    ['telescope_name', 'Telescope', 'Telescope'],
    ['is_plate_solved', 'Plate solved', 'Solved'],
];

// List view columns (from the old Metadata Search table); `sort` = API sort_by.
const LIST_COLUMNS = [
    { key: 'file_name', label: 'File name', sort: 'file_name' },
    { key: 'object', label: 'Object', sort: 'object_name' },
    { key: 'camera', label: 'Camera', sort: 'camera_name' },
    { key: 'telescope', label: 'Telescope', sort: 'telescope_name' },
    { key: 'exposure', label: 'Exposure (s)', sort: 'exposure_time_seconds', numeric: true },
    { key: 'date', label: 'Date', sort: 'capture_date' },
    { key: 'solved', label: 'Plate solved', sort: 'is_plate_solved', centered: true },
    { key: 'rating', label: 'Rating', sort: 'rating', centered: true },
];

const SUBTYPE_NAMES = {
    SUB_FRAME: 'Sub frames', INTEGRATION_MASTER: 'Masters', INTEGRATION_DEPRECATED: 'Deprecated',
    PLANETARY: 'Planetary', ALLSKY: 'All-sky', AURORA: 'Aurora',
};
const FRAME_SCOPE_NAMES = {
    LIGHT: 'Lights only', ALL: 'All frame types', DARK: 'Darks only', FLAT: 'Flats only',
    BIAS: 'Bias only', DARK_FLAT: 'Dark flats only',
};
const SOLVE_NAMES = { solved: 'Plate solved', imported: 'WCS imported', unsolved: 'Unsolved' };
const SUSPECT_NAMES = { ANY: 'any reason', SOFT: 'soft', CLOUD: 'few stars', TRAILED: 'elongated' };

const imagesLabel = (n) => `${n.toLocaleString()} ${n === 1 ? 'image' : 'images'}`;

function rangeText(label, min, max, unit = '') {
    if (min && max) return `${label} ${min}–${max}${unit}`;
    if (min) return `${label} ≥ ${min}${unit}`;
    return `${label} ≤ ${max}${unit}`;
}

// Filters the API actually applies: a header value needs a key, a cone needs RA and Dec.
function effectiveFilterEntries(sp) {
    return [...sp.entries()].filter(([k, v]) => {
        if (!v || NON_FILTER_PARAMS.includes(k)) return false;
        if (k === 'frame_type' && v === 'LIGHT') return false;
        if (k === 'header_value' && !sp.get('header_key')) return false;
        if (['ra', 'dec', 'radius'].includes(k) && !(sp.get('ra') && sp.get('dec'))) return false;
        return true;
    });
}

// Readable summary of the scope a bulk edit applies to, e.g. "Lights only · Camera “ASI2600”".
function describeScope(sp, rigNames) {
    const g = (k) => sp.get(k) || '';
    const parts = [FRAME_SCOPE_NAMES[g('frame_type') || 'LIGHT'] || `Frame type ${g('frame_type')}`];
    const add = (cond, text) => { if (cond) parts.push(text); };
    const sizeUnit = g('quality_units') === 'PX' ? ' px' : '″';
    add(g('search'), `Search “${g('search')}”`);
    add(g('path'), `Folder ${g('path')}`);
    add(g('subtype'), `Type: ${SUBTYPE_NAMES[g('subtype')] || g('subtype')}`);
    add(g('format'), `Format: ${g('format')}`);
    add(g('rating'), `Rating ≥ ${g('rating')}`);
    add(g('object_name'), `Object “${g('object_name')}”`);
    add(g('camera'), `Camera “${g('camera')}”`);
    add(g('telescope'), `Telescope “${g('telescope')}”`);
    add(g('filter'), `Filter “${g('filter')}”`);
    add(g('exposure_min') || g('exposure_max'), rangeText('Exposure', g('exposure_min'), g('exposure_max'), ' s'));
    add(g('gain_min') || g('gain_max'), rangeText('Gain', g('gain_min'), g('gain_max')));
    add(g('rotation_min') || g('rotation_max'), rangeText('Rotation', g('rotation_min'), g('rotation_max'), '°'));
    add(g('pixel_scale_min') || g('pixel_scale_max'), rangeText('Pixel scale', g('pixel_scale_min'), g('pixel_scale_max'), '″/px'));
    add(g('start_date') || g('end_date'), rangeText('Captured', g('start_date'), g('end_date')));
    add(g('header_key'), g('header_value')
        ? `Header ${g('header_key')} contains “${g('header_value')}”`
        : `Header ${g('header_key')} present`);
    add(g('ra') && g('dec'), `Within ${g('radius') || '1'}° of RA ${g('ra')}°, Dec ${g('dec')}°`);
    add(g('is_plate_solved'), SOLVE_NAMES[g('is_plate_solved')] || `Plate solved: ${g('is_plate_solved')}`);
    add(g('target_key'), `Target: ${g('target_key') === '__none__' ? 'unassigned' : g('target_key')}`);
    add(g('rig_id'), `Rig: ${g('rig_id') === 'none' ? 'unassigned' : (rigNames[g('rig_id')] || `#${g('rig_id')}`)}`);
    add(g('rig_bucket'), 'One unassigned-rig bucket');
    add(g('fwhm_min') || g('fwhm_max'), rangeText('FWHM', g('fwhm_min'), g('fwhm_max'), sizeUnit));
    add(g('hfr_max'), `HFR ≤ ${g('hfr_max')}${sizeUnit}`);
    add(g('eccentricity_max'), `Eccentricity ≤ ${g('eccentricity_max')}`);
    add(g('star_count_min'), `Stars ≥ ${g('star_count_min')}`);
    add(g('quality_flag'), `Suspect: ${SUSPECT_NAMES[g('quality_flag')] || g('quality_flag')}`);
    add(g('star_metrics_status'), `Measurement: ${g('star_metrics_status').replace(/,/g, ' / ').toLowerCase().replace(/_/g, ' ')}`);
    return parts.join(' · ');
}

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

// Result line inside the bulk-edit dialogs. Messages start with a check or cross.
function BulkMessage({ message }) {
    if (!message) return null;
    const ok = message.includes('✓');
    return (
        <div className={`bulk-message ${ok ? 'ok' : 'err'}`} role="status">
            {message.startsWith('✓') ? <Check size={14} aria-hidden="true" /> : <X size={14} aria-hidden="true" />}{' '}{message.replace(/^[✓✗]\s*/, '')}
        </div>
    );
}

// Shown in a bulk dialog when no filter narrows the scope: the edit hits the whole library.
function BulkUnfilteredWarning({ frameType, ack, onAck, disabled }) {
    const what = frameType === 'ALL' ? 'image' : 'Light frame';
    return (
        <div className="bulk-warning">
            <p className="bulk-warning-text">
                <AlertTriangle size={16} aria-hidden="true" />
                <span><strong>No filters are active.</strong> This changes every {what} in your library, not just the ones on screen.</span>
            </p>
            <label className="bulk-ack">
                <input type="checkbox" checked={ack} onChange={(e) => onAck(e.target.checked)} disabled={disabled} />
                I understand this changes every image
            </label>
        </div>
    );
}

// List view: one row per image, sortable column headers.
function ImageTable({ images, sortBy, sortOrder, onSort, onContextMenu }) {
    const remember = (id) => sessionStorage.setItem('lastClickedImageId', id);
    return (
        <div className="image-table-wrap">
            <table className="image-table">
                <thead>
                    <tr>
                        {LIST_COLUMNS.map((col) => {
                            const active = sortBy === col.sort;
                            return (
                                <th
                                    key={col.key}
                                    scope="col"
                                    className={col.numeric ? 'is-numeric' : col.centered ? 'is-centered' : undefined}
                                    aria-sort={active ? (sortOrder === 'asc' ? 'ascending' : 'descending') : 'none'}
                                >
                                    <button type="button" className="th-sort" onClick={() => onSort(col.sort)}>
                                        {col.label}
                                        {active && (sortOrder === 'asc'
                                            ? <ArrowUp size={12} aria-hidden="true" />
                                            : <ArrowDown size={12} aria-hidden="true" />)}
                                    </button>
                                </th>
                            );
                        })}
                        <th scope="col"><span className="ui-visually-hidden">Actions</span></th>
                    </tr>
                </thead>
                <tbody>
                    {images.map((img) => (
                        <tr key={img.id} id={`image-${img.id}`} onContextMenu={(e) => onContextMenu(e, img)}>
                            <td className="td-filename">
                                <span className="format-badge">{img.file_format}</span>
                                <Link to={`/images/${img.id}`} onClick={() => remember(img.id)} title={img.file_name}>
                                    {img.file_name}
                                </Link>
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
                                <span className={`solve-badge ${img.is_plate_solved ? 'is-solved' : 'is-unsolved'}`}>
                                    {img.is_plate_solved
                                        ? <Check size={14} aria-label="Solved" />
                                        : <X size={14} aria-label="Not solved" />}
                                </span>
                            </td>
                            <td className="td-centered">
                                {img.rating ? <span className="td-rating"><Star size={14} aria-hidden="true" /> {img.rating}</span> : '-'}
                            </td>
                            <td className="td-actions">
                                <Button
                                    to={`/images/${img.id}/metadata`}
                                    variant="plain"
                                    size="sm"
                                    iconOnly
                                    icon={<FileText size={16} />}
                                    aria-label={`View metadata for ${img.file_name}`}
                                    onClick={() => remember(img.id)}
                                />
                            </td>
                        </tr>
                    ))}
                </tbody>
            </table>
        </div>
    );
}

export default function Search() {
    const [searchParams, setSearchParams] = useSearchParams();
    const toast = useToast();
    const [images, setImages] = useState([]);
    const [loading, setLoading] = useState(true);
    const [totalCount, setTotalCount] = useState(0);
    const [totalPages, setTotalPages] = useState(1);
    const currentPage = parseInt(searchParams.get('page')) || 1;
    const view = searchParams.get('view') === 'list' ? 'list' : 'grid';
    const [thumbnailSize, setThumbnailSize] = useState(() => {
        const saved = localStorage.getItem('thumbnailSize');
        return saved ? parseInt(saved, 10) : 280;
    });
    const [imageContextMenu, setImageContextMenu] = useState(null); // { x, y, image }
    const [bulkChangeModalOpen, setBulkChangeModalOpen] = useState(false);
    const [bulkChangeSubtype, setBulkChangeSubtype] = useState('SUB_FRAME');
    const [bulkChangeLoading, setBulkChangeLoading] = useState(false);
    const [bulkChangeMessage, setBulkChangeMessage] = useState('');
    const [syncMetadataLoading, setSyncMetadataLoading] = useState(false);
    // F1: bulk "Set frame type..." action
    const [bulkFrameTypeModalOpen, setBulkFrameTypeModalOpen] = useState(false);
    const [bulkFrameTypeValue, setBulkFrameTypeValue] = useState('DARK');
    const [bulkFrameTypeLoading, setBulkFrameTypeLoading] = useState(false);
    const [bulkFrameTypeMessage, setBulkFrameTypeMessage] = useState('');
    // R0b: bulk "Assign Rig..." action
    const [bulkRigModalOpen, setBulkRigModalOpen] = useState(false);
    const [bulkRigValue, setBulkRigValue] = useState('');
    const [bulkRigLoading, setBulkRigLoading] = useState(false);
    const [bulkRigMessage, setBulkRigMessage] = useState('');
    // "I understand this changes every image" (only asked when no filter is active)
    const [bulkAck, setBulkAck] = useState(false);

    // Same key as Equipment.jsx, so the cache is shared.
    const equipmentQuery = useQuery({ queryKey: ['equipment'], queryFn: fetchEquipment, staleTime: 60_000 });
    const rigs = useMemo(() => equipmentQuery.data?.rigs || [], [equipmentQuery.data]);
    const rigNames = useMemo(() => Object.fromEntries(rigs.map((r) => [String(r.id), r.name])), [rigs]);

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

    // Filter states (the panel edits these; the URL is the applied state)
    const [filters, setFilters] = useState(() => filtersFromParams(searchParams));
    const { units } = useQualityUnits();

    // Local state for RA input to allow HH:MM editing
    const [raInput, setRaInput] = useState('');

    // Phones/tablets: filters live in a full-screen sheet, closed by default
    const isMobile = useIsMobile();
    const [showFilters, setShowFilters] = useState(() => !window.matchMedia('(max-width: 1024px)').matches);

    // The full-screen filter sheet must not let the page behind it scroll
    useEffect(() => {
        if (!(isMobile && showFilters)) return undefined;
        const prev = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => { document.body.style.overflow = prev; };
    }, [isMobile, showFilters]);

    // Switching Grid/List only changes `view`, so it must not refetch.
    const queryKey = useMemo(() => {
        const params = new URLSearchParams(searchParams);
        params.delete('view');
        return params.toString();
    }, [searchParams]);

    useEffect(() => {
        loadImages();
    }, [queryKey]);

    // Sync filters form with URL params
    useEffect(() => {
        setFilters(filtersFromParams(searchParams));

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
                page_size: PAGE_SIZE,
            };

            FILTER_KEYS.forEach((k) => {
                if (k !== 'frame_type' && searchParams.get(k)) params[k] = searchParams.get(k);
            });
            if (searchParams.get('pixel_scale_max_exclusive')) params.pixel_scale_max_exclusive = searchParams.get('pixel_scale_max_exclusive');
            if (searchParams.get('sort_by')) params.sort_by = searchParams.get('sort_by');
            if (searchParams.get('sort_order')) params.sort_order = searchParams.get('sort_order');
            // F1: default to Lights only when no frame_type URL param is present.
            // "ALL" is sent through as-is (backend treats it as no filter).
            params.frame_type = searchParams.get('frame_type') || 'LIGHT';

            const data = await fetchImages(params);
            setImages(data.items);
            setTotalCount(data.total);
            setTotalPages(data.total_pages);

            // Store search context for navigation
            sessionStorage.setItem('currentSearchContext', JSON.stringify({
                ids: data.items.map(img => img.id),
                total: data.total,
                page: currentPage,
                pageSize: PAGE_SIZE,
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

    // Write a new filter set to the URL, keeping the Grid/List choice.
    function commitParams(params) {
        if (view === 'list') params.set('view', 'list');
        setSearchParams(params);
    }

    // Change some URL params in place (sort, search, view), back to page 1 unless keepPage.
    function updateParams(changes, { keepPage = false } = {}) {
        const params = new URLSearchParams(searchParams);
        Object.entries(changes).forEach(([key, value]) => {
            if (value) params.set(key, value);
            else params.delete(key);
        });
        if (!keepPage) params.set('page', '1');
        setSearchParams(params);
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
        commitParams(params);
    }

    function applyFilters() {
        const params = new URLSearchParams();

        // Convert RA input (HH:MM) to degrees for URL
        const filtersToApply = { ...filters };
        // Q1d: FWHM/HFR bounds are in the units the viewer entered them in.
        const hasSize = filtersToApply.fwhm_min || filtersToApply.fwhm_max || filtersToApply.hfr_max;
        filtersToApply.quality_units = hasSize ? (filtersToApply.quality_units || units) : '';
        if (raInput) {
            filtersToApply.ra = hmsToDegrees(raInput).toFixed(4);
        } else {
            filtersToApply.ra = '';
        }

        Object.entries(filtersToApply).forEach(([key, value]) => {
            if (value) params.set(key, value);
        });
        params.set('page', '1');
        commitParams(params);
        if (isMobile) setShowFilters(false);
    }

    function clearFilters() {
        // F1: "Clear filters" resets to the Lights default, not All.
        setFilters(filtersFromParams(new URLSearchParams()));
        setRaInput('');
        commitParams(new URLSearchParams());
    }

    function setView(next) {
        updateParams({ view: next === 'list' ? 'list' : '' }, { keepPage: true });
    }

    function setSort(sortBy, sortOrder) {
        updateParams({ sort_by: sortBy, sort_order: sortOrder });
    }

    // List headers: a new column sorts ascending, the active one flips.
    function handleColumnSort(column) {
        if (filters.sort_by === column) setSort(column, filters.sort_order === 'asc' ? 'desc' : 'asc');
        else setSort(column, 'asc');
    }

    function submitSearch(e) {
        e.preventDefault();
        updateParams({ search: filters.search.trim() });
    }

    // F1: current searchParams with the implicit Lights default made explicit,
    // so CSV export and bulk actions operate on the same scope the grid shows.
    function effectiveSearchParams() {
        const params = new URLSearchParams(searchParams);
        params.delete('view');
        if (!params.get('frame_type')) {
            params.set('frame_type', 'LIGHT');
        }
        return params;
    }

    function handleExportCsv() {
        // Use current search scope (including the implicit Lights default)
        const params = effectiveSearchParams();
        const url = `${API_BASE_URL}/images/export_csv?${params.toString()}`;
        // Trigger download
        window.location.href = url;
    }

    async function handleSyncMetadata() {
        setSyncMetadataLoading(true);

        try {
            const result = await bulkSyncMetadata(effectiveSearchParams());
            toast.success(`Queued metadata sync for ${result.queued} image(s).`);
        } catch (error) {
            console.error('Failed to sync metadata:', error);
            toast.error(`Metadata sync failed: ${error.message}`);
        } finally {
            setSyncMetadataLoading(false);
        }
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

    const handleBulkChangeImageType = async () => {
        if (!bulkChangeSubtype || images.length === 0) {
            setBulkChangeMessage('No valid selection');
            return;
        }

        setBulkChangeLoading(true);
        setBulkChangeMessage('');

        try {
            // Pass current search params to update all matching images
            const result = await bulkUpdateImageType(bulkChangeSubtype, effectiveSearchParams());

            if (result.updated_count > 0) {
                setBulkChangeMessage(`✓ Successfully updated ${result.updated_count} image(s)`);
                // Reload images to reflect the changes
                setTimeout(() => {
                    loadImages();
                    setBulkChangeModalOpen(false);
                }, 1500);
            } else if (result.failed_count > 0) {
                setBulkChangeMessage(`✗ Failed to update ${result.failed_count} image(s)`);
            } else {
                setBulkChangeMessage('No images were updated');
            }
        } catch (error) {
            console.error('Error updating image types:', error);
            setBulkChangeMessage(`Error: ${error.message}`);
        } finally {
            setBulkChangeLoading(false);
        }
    };

    const handleBulkChangeFrameType = async () => {
        if (!bulkFrameTypeValue || images.length === 0) {
            setBulkFrameTypeMessage('No valid selection');
            return;
        }

        setBulkFrameTypeLoading(true);
        setBulkFrameTypeMessage('');

        try {
            const result = await bulkUpdateFrameType(bulkFrameTypeValue, effectiveSearchParams());

            if (result.updated_count > 0) {
                setBulkFrameTypeMessage(`✓ Successfully updated ${result.updated_count} image(s)`);
                setTimeout(() => {
                    loadImages();
                    setBulkFrameTypeModalOpen(false);
                }, 1500);
            } else if (result.failed_count > 0) {
                setBulkFrameTypeMessage(`✗ Failed to update ${result.failed_count} image(s)`);
            } else {
                setBulkFrameTypeMessage('No images were updated');
            }
        } catch (error) {
            console.error('Error updating frame types:', error);
            setBulkFrameTypeMessage(`Error: ${error.message}`);
        } finally {
            setBulkFrameTypeLoading(false);
        }
    };

    // R0b: allocate a rig to every light sub / master in the current results.
    const handleBulkAssignRig = async () => {
        if (!bulkRigValue || images.length === 0) {
            setBulkRigMessage('No valid selection');
            return;
        }

        setBulkRigLoading(true);
        setBulkRigMessage('');

        try {
            const result = await bulkAssignRig(bulkRigValue, effectiveSearchParams());

            if (result.errors?.length) {
                setBulkRigMessage(`✗ ${result.errors.join('; ')}`);
            } else if (result.updated_count > 0) {
                setBulkRigMessage(`✓ Updated ${result.updated_count} image(s); skipped ${result.skipped_count}`);
                setTimeout(() => {
                    loadImages();
                    setBulkRigModalOpen(false);
                }, 1500);
            } else {
                setBulkRigMessage(`No images were updated; skipped ${result.skipped_count}`);
            }
        } catch (error) {
            console.error('Error assigning rig:', error);
            setBulkRigMessage(`Error: ${error.message}`);
        } finally {
            setBulkRigLoading(false);
        }
    };

    // Filters badge counts applied (URL) filters, not unsaved panel edits.
    const activeFilterCount = [...searchParams.entries()].filter(([k, v]) =>
        v && !NON_FILTER_PARAMS.includes(k) && !(k === 'frame_type' && v === 'LIGHT')
    ).length;

    // Bulk-edit scope: what the dialogs say they will change.
    const frameTypeParam = searchParams.get('frame_type') || '';
    const scopeUnfiltered = effectiveFilterEntries(searchParams)
        .filter(([k, v]) => !(k === 'frame_type' && v === 'ALL')).length === 0;
    const scopeSummary = describeScope(searchParams, rigNames);
    const scopeCount = imagesLabel(totalCount);
    const bulkBlocked = loading || totalCount === 0 || (scopeUnfiltered && !bulkAck);

    function openBulk(setOpen, setMessage) {
        setMessage('');
        setBulkAck(false);
        setOpen(true);
    }

    const allSortFields = [...SORT_FIELDS, ...LIST_SORT_FIELDS];
    const currentSort = allSortFields.find(([value]) => value === filters.sort_by)
        || [filters.sort_by, filters.sort_by, filters.sort_by];
    const sortMenuFields = SORT_FIELDS.some(([value]) => value === filters.sort_by)
        ? SORT_FIELDS
        : [...SORT_FIELDS, currentSort];
    const sortAscending = filters.sort_order === 'asc';
    const sortItems = [
        { type: 'heading', label: 'Sort by' },
        ...sortMenuFields.map(([value, label]) => ({
            id: `sort-${value}`,
            label,
            checked: value === filters.sort_by,
            onSelect: () => setSort(value, filters.sort_order),
        })),
        { type: 'separator' },
        { type: 'heading', label: 'Order' },
        { id: 'order-asc', label: 'Ascending', checked: sortAscending, onSelect: () => setSort(filters.sort_by, 'asc') },
        { id: 'order-desc', label: 'Descending', checked: !sortAscending, onSelect: () => setSort(filters.sort_by, 'desc') },
    ];

    const noResults = totalCount === 0;
    const bulkItems = [
        {
            id: 'bulk-type',
            label: 'Change image type…',
            icon: <Tags size={14} />,
            disabled: noResults,
            onSelect: () => openBulk(setBulkChangeModalOpen, setBulkChangeMessage),
        },
        {
            id: 'bulk-frame',
            label: 'Set frame type…',
            icon: <Contrast size={14} />,
            disabled: noResults,
            onSelect: () => openBulk(setBulkFrameTypeModalOpen, setBulkFrameTypeMessage),
        },
        {
            id: 'bulk-rig',
            label: 'Assign rig…',
            hint: 'Light sub-frames and masters only',
            icon: <TelescopeIcon size={14} />,
            disabled: noResults,
            onSelect: () => openBulk(setBulkRigModalOpen, setBulkRigMessage),
        },
    ];
    const moreItems = [
        { id: 'export-csv', label: 'Export CSV', hint: 'All matching results', icon: <Download size={14} />, onSelect: handleExportCsv },
        {
            id: 'sync-metadata',
            label: syncMetadataLoading ? 'Syncing metadata…' : 'Sync metadata',
            hint: 'Queue a re-read for all matching results',
            icon: <RefreshCw size={14} />,
            disabled: noResults || syncMetadataLoading,
            onSelect: handleSyncMetadata,
        },
    ];

    return (
        <div className="page-search">
            <PageHeader
                title="Images"
                actions={(
                    <div className="search-toolbar">
                        <form role="search" className="toolbar-search" onSubmit={submitSearch}>
                            <SearchIcon size={16} className="toolbar-search-icon" aria-hidden="true" />
                            <input
                                type="search"
                                className="input"
                                placeholder="Search files and objects"
                                aria-label="Search file names and objects"
                                value={filters.search}
                                onChange={(e) => handleFilterChange('search', e.target.value)}
                            />
                        </form>
                        <Button
                            variant="tinted"
                            className="toolbar-filters-btn"
                            icon={<SlidersHorizontal size={16} />}
                            aria-expanded={showFilters}
                            aria-controls={showFilters ? 'search-filters' : undefined}
                            onClick={() => setShowFilters(!showFilters)}
                        >
                            Filters
                            {activeFilterCount > 0 && (
                                <span className="toolbar-badge">
                                    {activeFilterCount}
                                    <span className="ui-visually-hidden"> active</span>
                                </span>
                            )}
                        </Button>
                        <SegmentedControl
                            aria-label="View"
                            size="sm"
                            value={view}
                            onChange={setView}
                            items={[
                                { value: 'grid', label: 'Grid', icon: <LayoutGrid size={14} /> },
                                { value: 'list', label: 'List', icon: <List size={14} /> },
                            ]}
                        />
                        <ActionMenu
                            items={sortItems}
                            menuLabel="Sort"
                            aria-label={`Sort by ${currentSort[1]}, ${sortAscending ? 'ascending' : 'descending'}`}
                            variant="plain"
                            className="toolbar-sort"
                        >
                            {currentSort[2]}
                            {sortAscending ? <ArrowUp size={14} aria-hidden="true" /> : <ArrowDown size={14} aria-hidden="true" />}
                        </ActionMenu>
                        <ActionMenu items={bulkItems} align="end" variant="tinted" icon={<Pencil size={14} />} disabled={noResults && !loading}>
                            Bulk edit
                        </ActionMenu>
                        <ActionMenu
                            items={moreItems}
                            align="end"
                            variant="plain"
                            iconOnly
                            icon={<MoreHorizontal size={18} />}
                            aria-label="More"
                        />
                    </div>
                )}
            />

            <div className="search-layout">
                {/* Filters Sidebar */}
                {showFilters && (
                    <div className="filters-backdrop" onClick={() => setShowFilters(false)} aria-hidden="true" />
                )}
                {showFilters && (
                    <form id="search-filters" className="filters-sidebar" onSubmit={(e) => { e.preventDefault(); applyFilters(); }}>
                        <div className="filters-header">
                            <h3><SearchIcon size={16} /> Search & Filter</h3>
                            <Button variant="plain" size="sm" onClick={clearFilters}>
                                Clear All
                            </Button>
                            <Button variant="filled" size="sm" className="filters-done" onClick={() => setShowFilters(false)}>
                                Done
                            </Button>
                        </div>

                        {/* Scrolls on phones while the header and Apply button stay pinned */}
                        <div className="filters-body">
                        {/* Active Filters Display */}
                        <FilterChips
                            filters={filters}
                            rigNames={rigNames}
                            onRemove={(key) => {
                                if (key === 'frame_type') {
                                    // Removing the "Lights only" chip switches to All;
                                    // removing an explicit type (Darks, etc.) reverts to
                                    // the Lights default. See F1-frame-types.md §3.10.
                                    const isDefaultLights = !filters.frame_type || filters.frame_type === 'LIGHT';
                                    const updatedFilters = { ...filters, frame_type: isDefaultLights ? 'ALL' : '' };
                                    setFilters(updatedFilters);
                                    applyCurrentFilters(updatedFilters);
                                    return;
                                }
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
                        <FilterSection title="Folder Structure" icon={<FolderOpen size={16} />} defaultOpen={!isMobile}>
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
                        <FilterSection title="Image Properties" icon={<ImageIcon size={16} />} defaultOpen={true}>
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
                                    <option value="ALLSKY">All-sky</option>
                                    <option value="AURORA">Aurora</option>
                                </select>
                            </div>

                            <div className="filter-group">
                                <label className="label">Frame Type</label>
                                <select
                                    className="input select"
                                    value={filters.frame_type || ''}
                                    onChange={(e) => {
                                        const updatedFilters = { ...filters, frame_type: e.target.value };
                                        setFilters(updatedFilters);
                                        applyCurrentFilters(updatedFilters);
                                    }}
                                >
                                    <option value="">Lights (default)</option>
                                    <option value="ALL">All Frame Types</option>
                                    <option value="DARK">Darks</option>
                                    <option value="FLAT">Flats</option>
                                    <option value="BIAS">Bias</option>
                                    <option value="DARK_FLAT">Dark Flats</option>
                                </select>
                            </div>

                            <div className="filter-group">
                                <label className="label">Rig</label>
                                <select
                                    className="input select"
                                    value={filters.rig_id || ''}
                                    onChange={(e) => {
                                        const updatedFilters = { ...filters, rig_id: e.target.value };
                                        setFilters(updatedFilters);
                                        applyCurrentFilters(updatedFilters);
                                    }}
                                >
                                    <option value="">Any rig</option>
                                    <option value="none">Unassigned</option>
                                    {rigs.map((r) => <option key={r.id} value={String(r.id)}>{r.name}</option>)}
                                    {filters.rig_id && filters.rig_id !== 'none' && !rigNames[filters.rig_id] && (
                                        <option value={filters.rig_id}>{`Rig #${filters.rig_id}`}</option>
                                    )}
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
                                    <option value="1">1 star or higher</option>
                                    <option value="2">2 stars or higher</option>
                                    <option value="3">3 stars or higher</option>
                                    <option value="4">4 stars or higher</option>
                                    <option value="5">5 stars</option>
                                </select>
                            </div>
                        </FilterSection>

                        {/* Capture Settings Section */}
                        <FilterSection title="Capture Settings" icon={<Settings size={16} />} defaultOpen={false}>
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
                                    label="Gain"
                                    minValue={filters.gain_min}
                                    maxValue={filters.gain_max}
                                    onMinChange={(v) => handleFilterChange('gain_min', v)}
                                    onMaxChange={(v) => handleFilterChange('gain_max', v)}
                                    placeholder={{ min: '0', max: '300' }}
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

                            <div className="filter-group">
                                <label className="label">Telescope</label>
                                <input
                                    type="text"
                                    className="input"
                                    placeholder="e.g., RedCat 51"
                                    value={filters.telescope}
                                    onChange={(e) => handleFilterChange('telescope', e.target.value)}
                                />
                            </div>
                        </FilterSection>

                        {/* Header Fields Section (FITS/EXIF keywords, from the old Metadata Search) */}
                        <FilterSection
                            title="Header fields"
                            icon={<FileText size={16} />}
                            defaultOpen={Boolean(filters.header_key || filters.header_value)}
                        >
                            <div className="filter-group">
                                <label className="label" htmlFor="search-header-key">Keyword</label>
                                <input
                                    id="search-header-key"
                                    type="text"
                                    className="input"
                                    placeholder="e.g., IMAGETYP, FOCUSPOS"
                                    value={filters.header_key}
                                    onChange={(e) => handleFilterChange('header_key', e.target.value)}
                                />
                            </div>
                            <div className="filter-group">
                                <label className="label" htmlFor="search-header-value">Value contains</label>
                                <input
                                    id="search-header-value"
                                    type="text"
                                    className="input"
                                    placeholder="Leave empty to match any value"
                                    value={filters.header_value}
                                    onChange={(e) => handleFilterChange('header_value', e.target.value)}
                                />
                                <span className="filter-hint">Matches images whose FITS/EXIF header has this keyword.</span>
                            </div>
                        </FilterSection>

                        {/* Star Quality Section (Q1d) */}
                        <FilterSection title="Star Quality" icon={<Sparkles size={16} />} defaultOpen={QUALITY_KEYS.some((k) => k !== 'quality_units' && filters[k])}>
                            <div className="filter-group">
                                <label className="label">Suspect subs</label>
                                <select className="input select" value={filters.quality_flag}
                                    onChange={(e) => handleFilterChange('quality_flag', e.target.value)}>
                                    <option value="">Any sub</option>
                                    <option value="ANY">Suspect only (any reason)</option>
                                    <option value="SOFT">Soft: FWHM well above the night’s median</option>
                                    <option value="CLOUD">Few stars: cloud or haze</option>
                                    <option value="TRAILED">Elongated stars</option>
                                </select>
                            </div>
                            <div className="filter-group">
                                <RangeInput
                                    label={`FWHM (${(filters.quality_units || units) === 'PX' ? 'pixels' : 'arcsec'})`}
                                    minValue={filters.fwhm_min}
                                    maxValue={filters.fwhm_max}
                                    onMinChange={(v) => handleFilterChange('fwhm_min', v)}
                                    onMaxChange={(v) => handleFilterChange('fwhm_max', v)}
                                    placeholder={{ min: '0', max: (filters.quality_units || units) === 'PX' ? '4' : '3' }}
                                    unit={(filters.quality_units || units) === 'PX' ? 'px' : '″'}
                                    step="0.1"
                                />
                            </div>
                            <div className="filter-group">
                                <label className="label">Max HFR ({(filters.quality_units || units) === 'PX' ? 'px' : '″'})</label>
                                <input type="number" className="input" step="0.1" min="0" value={filters.hfr_max}
                                    onChange={(e) => handleFilterChange('hfr_max', e.target.value)} />
                            </div>
                            <div className="filter-group">
                                <label className="label">Max eccentricity (0 = round)</label>
                                <input type="number" className="input" step="0.05" min="0" max="1" value={filters.eccentricity_max}
                                    onChange={(e) => handleFilterChange('eccentricity_max', e.target.value)} />
                            </div>
                            <div className="filter-group">
                                <label className="label">Min stars</label>
                                <input type="number" className="input" step="10" min="0" value={filters.star_count_min}
                                    onChange={(e) => handleFilterChange('star_count_min', e.target.value)} />
                            </div>
                            <div className="filter-group">
                                <label className="label">Measurement</label>
                                <select className="input select" value={filters.star_metrics_status}
                                    onChange={(e) => handleFilterChange('star_metrics_status', e.target.value)}>
                                    <option value="">Any</option>
                                    <option value="OK">Measured</option>
                                    <option value="NEVER,PENDING">Not measured yet</option>
                                    <option value="NO_STARS">No stars found</option>
                                    <option value="FAILED">Failed</option>
                                    <option value="SKIPPED,HINT">Not measurable</option>
                                </select>
                            </div>
                        </FilterSection>

                        {/* Observation Data Section */}
                        <FilterSection title="Observation Data" icon={<Moon size={16} />} defaultOpen={false}>
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
                                    <span className="range-separator"><ArrowRight size={14} aria-hidden="true" /></span>
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
                        <FilterSection title="Advanced Search" icon={<TelescopeIcon size={16} />} defaultOpen={false}>
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
                        </div>

                        <Button type="submit" variant="filled" className="btn-apply-filters" icon={<Check size={16} />}>
                            Apply Filters
                        </Button>
                    </form>
                )}

                {/* Results */}
                <div className="search-results">
                    <div className="results-header">
                        <span className="results-count" aria-live="polite">
                            {loading ? 'Loading…' : `${imagesLabel(totalCount)} found`}
                        </span>
                        {view === 'grid' && (
                            <div className="size-control">
                                <span className="size-label">Size</span>
                                <input
                                    type="range"
                                    min="150"
                                    max="500"
                                    value={thumbnailSize}
                                    onChange={(e) => setThumbnailSize(Number(e.target.value))}
                                    className="size-slider"
                                    aria-label="Thumbnail size"
                                    title="Adjust thumbnail size"
                                />
                            </div>
                        )}
                    </div>

                    {loading ? (
                        view === 'list' ? (
                            <div className="list-loading" aria-busy="true">
                                {Array.from({ length: 10 }).map((_, i) => (
                                    <Skeleton key={i} height={36} />
                                ))}
                            </div>
                        ) : (
                            <div className="loading-grid" aria-busy="true" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${thumbnailSize}px, 1fr))` }}>
                                {Array.from({ length: 8 }).map((_, i) => (
                                    <Skeleton key={i} className="image-skeleton" />
                                ))}
                            </div>
                        )
                    ) : images.length === 0 && totalCount === 0 && searchParams.get('rig_bucket') ? (
                        // R0c: a bucket link whose images have since been assigned (or regrouped).
                        <EmptyState
                            icon={<TelescopeIcon size={64} strokeWidth={1.5} />}
                            title="No images found"
                            description={(
                                <>
                                    This bucket no longer exists. Its images may have been assigned to a rig, or the rig list changed.
                                    Go back to <Link to="/equipment">Equipment → Unassigned images</Link> to see the current buckets.
                                </>
                            )}
                        />
                    ) : images.length === 0 ? (
                        <EmptyState
                            icon={<TelescopeIcon size={64} strokeWidth={1.5} />}
                            title="No images found"
                            description="Try adjusting your filters or search criteria"
                        />
                    ) : view === 'list' ? (
                        <ImageTable
                            images={images}
                            sortBy={filters.sort_by}
                            sortOrder={filters.sort_order}
                            onSort={handleColumnSort}
                            onContextMenu={handleImageContextMenu}
                        />
                    ) : (
                        <div className="image-grid" style={{ gridTemplateColumns: `repeat(auto-fill, minmax(${thumbnailSize}px, 1fr))` }}>
                            {images.map(image => (
                                <ImageCard showQuality
                                    key={image.id}
                                    image={image}
                                    onContextMenu={handleImageContextMenu}
                                />
                            ))}
                        </div>
                    )}

                    {/* Pagination */}
                    <Pagination
                        page={currentPage}
                        totalPages={totalPages}
                        totalItems={totalCount}
                        itemLabel="images"
                        onPageChange={(n) => {
                            const params = new URLSearchParams(searchParams);
                            params.set('page', n.toString());
                            setSearchParams(params);
                            window.scrollTo(0, 0);
                        }}
                    />
                </div>
            </div>

            {imageContextMenu && (
                <div
                    className="search-context-menu"
                    role="menu"
                    style={{
                        position: 'fixed',
                        top: imageContextMenu.y,
                        left: imageContextMenu.x,
                        zIndex: 1000
                    }}
                    onClick={e => e.stopPropagation()}
                >
                    <button type="button" role="menuitem" className="search-menu-item" onClick={handleExpandPath}>
                        <FolderOpen size={14} /> Expand Path
                    </button>
                </div>
            )}

            <Dialog
                open={bulkChangeModalOpen}
                onClose={() => setBulkChangeModalOpen(false)}
                title="Change image type"
                description={`This will change ${scopeCount} matching: ${scopeSummary}.`}
                size="sm"
                destructive
                dismissible={!bulkChangeLoading}
                closeOnBackdrop={!bulkChangeLoading}
                className="dlg-search-bulk"
                footer={(
                    <>
                        <Button variant="plain" onClick={() => setBulkChangeModalOpen(false)} disabled={bulkChangeLoading}>
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={handleBulkChangeImageType}
                            loading={bulkChangeLoading}
                            disabled={bulkBlocked}
                        >
                            {`Change type on ${scopeCount}`}
                        </Button>
                    </>
                )}
            >
                {scopeUnfiltered && (
                    <BulkUnfilteredWarning frameType={frameTypeParam} ack={bulkAck} onAck={setBulkAck} disabled={bulkChangeLoading} />
                )}
                <div className="bulk-field">
                    <label className="label" htmlFor="bulk-change-subtype">New image type</label>
                    <select
                        id="bulk-change-subtype"
                        className="input select"
                        value={bulkChangeSubtype}
                        onChange={(e) => setBulkChangeSubtype(e.target.value)}
                        disabled={bulkChangeLoading}
                    >
                        <option value="SUB_FRAME">Sub Frames</option>
                        <option value="INTEGRATION_MASTER">Masters</option>
                        <option value="INTEGRATION_DEPRECATED">Deprecated</option>
                        <option value="PLANETARY">Planetary</option>
                        <option value="ALLSKY">All-sky</option>
                        <option value="AURORA">Aurora</option>
                    </select>
                </div>
                <BulkMessage message={bulkChangeMessage} />
            </Dialog>

            <Dialog
                open={bulkFrameTypeModalOpen}
                onClose={() => setBulkFrameTypeModalOpen(false)}
                title="Set frame type"
                description={`This will change ${scopeCount} matching: ${scopeSummary}.`}
                size="sm"
                destructive
                dismissible={!bulkFrameTypeLoading}
                closeOnBackdrop={!bulkFrameTypeLoading}
                className="dlg-search-bulk"
                footer={(
                    <>
                        <Button variant="plain" onClick={() => setBulkFrameTypeModalOpen(false)} disabled={bulkFrameTypeLoading}>
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={handleBulkChangeFrameType}
                            loading={bulkFrameTypeLoading}
                            disabled={bulkBlocked}
                        >
                            {`Set frame type on ${scopeCount}`}
                        </Button>
                    </>
                )}
            >
                {scopeUnfiltered && (
                    <BulkUnfilteredWarning frameType={frameTypeParam} ack={bulkAck} onAck={setBulkAck} disabled={bulkFrameTypeLoading} />
                )}
                <p className="bulk-note">
                    The new type is a manual override that survives re-indexing. To relabel mislabelled frames, choose "All Frame Types" or the current (wrong) type in the Frame Type filter first.
                </p>
                <div className="bulk-field">
                    <label className="label" htmlFor="bulk-frame-type">New frame type</label>
                    <select
                        id="bulk-frame-type"
                        className="input select"
                        value={bulkFrameTypeValue}
                        onChange={(e) => setBulkFrameTypeValue(e.target.value)}
                        disabled={bulkFrameTypeLoading}
                    >
                        <option value="LIGHT">Light</option>
                        <option value="DARK">Dark</option>
                        <option value="FLAT">Flat</option>
                        <option value="BIAS">Bias</option>
                        <option value="DARK_FLAT">Dark Flat</option>
                    </select>
                </div>
                <BulkMessage message={bulkFrameTypeMessage} />
            </Dialog>

            <Dialog
                open={bulkRigModalOpen}
                onClose={() => setBulkRigModalOpen(false)}
                title="Assign rig"
                description={`This will change up to ${scopeCount} matching: ${scopeSummary}.`}
                size="sm"
                destructive
                dismissible={!bulkRigLoading}
                closeOnBackdrop={!bulkRigLoading}
                className="dlg-search-bulk"
                footer={(
                    <>
                        <Button variant="plain" onClick={() => setBulkRigModalOpen(false)} disabled={bulkRigLoading}>
                            Cancel
                        </Button>
                        <Button
                            variant="destructive"
                            onClick={handleBulkAssignRig}
                            loading={bulkRigLoading}
                            disabled={bulkBlocked || !bulkRigValue}
                        >
                            {`${bulkRigValue === 'none' ? 'Clear rig on' : 'Assign rig to'} ${scopeCount}`}
                        </Button>
                    </>
                )}
            >
                {scopeUnfiltered && (
                    <BulkUnfilteredWarning frameType={frameTypeParam} ack={bulkAck} onAck={setBulkAck} disabled={bulkRigLoading} />
                )}
                <p className="bulk-note">
                    Only <strong>Light sub-frames and masters</strong> get the rig; other frames are skipped. Assigned rigs are marked manual and won't be changed by auto-assignment.
                </p>
                <div className="bulk-field">
                    <label className="label" htmlFor="bulk-rig">Rig</label>
                    <select
                        id="bulk-rig"
                        className="input select"
                        value={bulkRigValue}
                        onChange={(e) => setBulkRigValue(e.target.value)}
                        disabled={bulkRigLoading}
                    >
                        <option value="">Choose rig…</option>
                        {rigs.map((r) => <option key={r.id} value={String(r.id)}>{r.name}</option>)}
                        <option value="none">Clear rig (let auto-assign decide)</option>
                    </select>
                </div>
                <BulkMessage message={bulkRigMessage} />
            </Dialog>
        </div>
    );
}
