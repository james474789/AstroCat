// API Client connecting to the FastAPI backend
export const API_BASE_URL = (() => {
    // 1. Check for Vite environment variable (highly recommended for production)
    const envUrl = import.meta.env.VITE_API_URL;
    if (envUrl && !envUrl.includes('localhost') && !envUrl.includes('127.0.0.1')) {
        return envUrl.replace(/\/$/, "");
    }

    // 2. If we are in a browser, use the current window location to derive the API path
    if (typeof window !== 'undefined') {
        const hostname = window.location.hostname;
        const protocol = window.location.protocol;

        // If the frontend is on port 8090, the backend is likely on 8089 (standard dev setup)
        // Otherwise, if they are served on the same port (proxy/prod), just use /api
        const port = window.location.port;

        if (port === '8090') {
            return `${protocol}//${hostname}:8089/api`;
        }

        // Fallback for production or when port handles themselves
        return '/api';
    }

    // 3. Fallback for non-browser environments or default
    return envUrl || '/api';
})();

const CSRF_COOKIE_NAME = 'csrf_token';
const CSRF_HEADER_NAME = 'X-CSRF-Token';

function getCookie(name) {
    if (typeof document === 'undefined') return null;
    const match = document.cookie.match(new RegExp('(^| )' + name + '=([^;]+)'));
    return match ? decodeURIComponent(match[2]) : null;
}

function getCsrfToken() {
    return getCookie(CSRF_COOKIE_NAME);
}

function withCsrfHeaders(headers = {}) {
    const token = getCsrfToken();
    return token ? { ...headers, [CSRF_HEADER_NAME]: token } : headers;
}

// Helper to handle responses
async function handleResponse(response) {
    if (response.status === 401) {
        // Authentication required or session expired
        const error = await response.json().catch(() => ({ detail: 'Authentication required' }));

        // We don't want to redirect if we're specifically checking 'me' or trying to 'login'
        const isAuthCheck = response.url.includes('/auth/me') || response.url.includes('/auth/login');

        if (!isAuthCheck && typeof window !== 'undefined') {
            // Optional: trigger a custom event that AuthContext can listen to
            window.dispatchEvent(new CustomEvent('api-unauthorized'));
        }

        throw new Error(error.detail || 'Unauthorized');
    }

    if (!response.ok) {
        const error = await response.json().catch(() => ({ detail: 'Unknown error' }));
        let message = error.detail || `API Error: ${response.status}`;

        // Handle FastAPI validation error lists
        if (Array.isArray(message)) {
            message = message.map(err => `${err.loc.join('.')}: ${err.msg}`).join(', ');
        } else if (typeof message === 'object') {
            message = JSON.stringify(message);
        }

        throw new Error(message);
    }

    // Handle 204 No Content
    if (response.status === 204) {
        return null;
    }

    return response.json();
}


function buildQueryString(params) {
    const searchParams = new URLSearchParams();
    Object.entries(params).forEach(([key, value]) => {
        if (value !== undefined && value !== null && value !== '') {
            searchParams.append(key, value);
        }
    });
    return searchParams.toString();
}

// ============ Images API ============

export async function fetchImages(params = {}) {
    // Map frontend params to backend match
    const queryParams = {
        page: params.page || 1,
        page_size: params.page_size || 20,
        subtype: params.subtype,
        format: params.format,
        rating: params.rating,
        is_plate_solved: params.is_plate_solved,
        search: params.search,
        object_name: params.object_name,
        exposure_min: params.exposure_min,
        exposure_max: params.exposure_max,
        rotation_min: params.rotation_min,
        rotation_max: params.rotation_max,
        filter: params.filter,
        camera: params.camera,
        ra: params.ra,
        dec: params.dec,
        radius: params.radius,
        path: params.path,
        pixel_scale_min: params.pixel_scale_min,
        pixel_scale_max: params.pixel_scale_max,
        pixel_scale_max_exclusive: params.pixel_scale_max_exclusive,
        start_date: params.start_date,
        end_date: params.end_date,
        header_key: params.header_key,
        header_value: params.header_value,
        telescope: params.telescope,
        gain_min: params.gain_min,
        gain_max: params.gain_max,
        sort_by: params.sort_by,
        sort_order: params.sort_order,
    };

    const queryString = buildQueryString(queryParams);
    return handleResponse(await fetch(`${API_BASE_URL}/images/?${queryString}`, { credentials: 'include' }));
}

export async function fetchImage(id) {
    return handleResponse(await fetch(`${API_BASE_URL}/images/${id}`, { credentials: 'include' }));
}

export async function updateImage(id, updates) {
    return handleResponse(await fetch(`${API_BASE_URL}/images/${id}`, {
        method: 'PUT',
        headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(updates),
        credentials: 'include'
    }));
}

export async function rescanImage(id) {
    return handleResponse(await fetch(`${API_BASE_URL}/images/${id}/rescan`, {
        method: 'POST',
        headers: withCsrfHeaders(),
        credentials: 'include'
    }));
}

export async function fetchAnnotation(id) {
    return handleResponse(await fetch(`${API_BASE_URL}/images/${id}/fetch_annotation`, {
        method: 'POST',
        headers: withCsrfHeaders(),
        credentials: 'include'
    }));
}

export async function regenerateImageThumbnail(id) {
    return handleResponse(await fetch(`${API_BASE_URL}/images/${id}/thumbnail/regenerate`, {
        method: 'POST',
        headers: withCsrfHeaders(),
        credentials: 'include'
    }));
}

export async function fetchImageThumbnail(id) {
    // Returns URL, not data, since it's an image source
    return `${API_BASE_URL}/images/${id}/thumbnail`;
}

export function getDownloadUrl(id, format = 'jpg') {
    return `${API_BASE_URL}/images/${id}/download?format=${format}`;
}

// ============ Search API ============

export async function searchByMessier(designation) {
    return handleResponse(await fetch(`${API_BASE_URL}/search/catalog/MESSIER/${designation}`, { credentials: 'include' }));
}

export async function searchByNGC(designation) {
    return handleResponse(await fetch(`${API_BASE_URL}/search/catalog/NGC/${designation}`, { credentials: 'include' }));
}

export async function searchByCoordinates(ra, dec, radiusDegrees) {
    const params = new URLSearchParams({
        ra: ra.toString(),
        dec: dec.toString(),
        radius: radiusDegrees.toString()
    });
    return handleResponse(await fetch(`${API_BASE_URL}/search/coordinates?${params}`, { credentials: 'include' }));
}

// ============ Catalogs API ============

export async function fetchMessierCatalog(params = {}) {
    const queryParams = {
        page: params.page || 1,
        page_size: params.page_size || 110,
        q: params.q,
        has_images: params.has_images,
        sort_by: params.sort_by,
        sort_order: params.sort_order
    };
    const queryString = buildQueryString(queryParams);
    return handleResponse(await fetch(`${API_BASE_URL}/catalogs/messier?${queryString}`, { credentials: 'include' }));
}

export async function fetchNGCCatalog(params = {}) {
    const queryParams = {
        page: params.page || 1,
        page_size: params.page_size || 50,
        constellation: params.constellation,
        q: params.q,
        catalog: params.catalog,
        has_images: params.has_images,
        sort_by: params.sort_by,
        sort_order: params.sort_order
    };
    const queryString = buildQueryString(queryParams);
    return handleResponse(await fetch(`${API_BASE_URL}/catalogs/ngc?${queryString}`, { credentials: 'include' }));
}

export async function fetchNamedStarCatalog(params = {}) {
    const queryParams = {
        page: params.page || 1,
        page_size: params.page_size || 50,
        q: params.q,
        has_images: params.has_images,
        sort_by: params.sort_by,
        sort_order: params.sort_order
    };
    const queryString = buildQueryString(queryParams);
    return handleResponse(await fetch(`${API_BASE_URL}/catalogs/named_stars?${queryString}`, { credentials: 'include' }));
}

export async function fetchCatalogObject(type, designation) {
    const cleanType = type.toLowerCase();
    const cleanDes = designation.trim();

    let objectData;
    if (cleanType === 'messier') {
        objectData = await handleResponse(await fetch(`${API_BASE_URL}/catalogs/messier/${cleanDes}`, { credentials: 'include' }));
    } else {
        objectData = { designation: cleanDes, object_type: 'NGC Object' };
    }

    const catalogTypeEnum = cleanType === 'messier' ? 'MESSIER' : 'NGC';
    const images = await handleResponse(await fetch(`${API_BASE_URL}/search/catalog/${catalogTypeEnum}/${cleanDes}`, { credentials: 'include' }));

    return {
        ...objectData,
        images: images,
        image_count: images ? images.length : 0,
        total_exposure_hours: 0
    };
}

// ============ Statistics API ============

export async function fetchStatsOverview() {
    return handleResponse(await fetch(`${API_BASE_URL}/stats/overview`, { credentials: 'include' }));
}

export async function fetchStatsByMonth() {
    return handleResponse(await fetch(`${API_BASE_URL}/stats/by-month`, { credentials: 'include' }));
}

export async function fetchStatsBySubtype() {
    return handleResponse(await fetch(`${API_BASE_URL}/stats/by-subtype`, { credentials: 'include' }));
}

export async function fetchStatsByFormat() {
    return handleResponse(await fetch(`${API_BASE_URL}/stats/by-format`, { credentials: 'include' }));
}

export async function fetchTopObjects() {
    return handleResponse(await fetch(`${API_BASE_URL}/stats/top-objects`, { credentials: 'include' }));
}

export async function fetchAllStats() {
    const [overview, by_month, by_subtype, by_format, top_objects] = await Promise.all([
        fetchStatsOverview(),
        fetchStatsByMonth(),
        fetchStatsBySubtype(),
        fetchStatsByFormat(),
        fetchTopObjects()
    ]);

    return {
        overview,
        by_subtype,
        by_format,
        by_month: by_month.map(m => ({ ...m, exposure_hours: m.exposure_hours || 0 })),
        top_objects
    };
}

export async function fetchAdminStats() {
    return handleResponse(await fetch(`${API_BASE_URL}/admin/stats`, { credentials: 'include' }));
}

export async function fetchWorkerStats() {
    return handleResponse(await fetch(`${API_BASE_URL}/admin/workers`, { credentials: 'include' }));
}

export async function fetchQueueDetails() {
    return handleResponse(await fetch(`${API_BASE_URL}/admin/queue`, { credentials: 'include' }));
}

export async function fetchFitsStats(filters = {}) {
    const queryString = buildQueryString(filters);
    return handleResponse(await fetch(`${API_BASE_URL}/stats/fits/?${queryString}`, { credentials: 'include' }));
}

// ============ Indexer API ============

export async function fetchIndexerStatus() {
    return handleResponse(await fetch(`${API_BASE_URL}/indexer/status`, { credentials: 'include' }));
}

export async function triggerScan() {
    return handleResponse(await fetch(`${API_BASE_URL}/indexer/scan`, {
        method: 'POST',
        headers: withCsrfHeaders(),
        credentials: 'include'
    }));
}

export async function fetchThumbnailStats() {
    return handleResponse(await fetch(`${API_BASE_URL}/indexer/thumbnails/stats`, { credentials: 'include' }));
}

export async function clearThumbnailCache() {
    return handleResponse(await fetch(`${API_BASE_URL}/indexer/thumbnails/clear`, {
        method: 'POST',
        headers: withCsrfHeaders(),
        credentials: 'include'
    }));
}

export async function regenerateThumbnails() {
    return handleResponse(await fetch(`${API_BASE_URL}/indexer/thumbnails/regenerate`, {
        method: 'POST',
        headers: withCsrfHeaders(),
        credentials: 'include'
    }));
}

export async function triggerMountMatches(path) {
    return handleResponse(await fetch(`${API_BASE_URL}/indexer/batch/matches`, {
        method: 'POST',
        headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ path }),
        credentials: 'include'
    }));
}

export async function triggerMountRescan(path, force) {
    return handleResponse(await fetch(`${API_BASE_URL}/indexer/batch/rescan`, {
        method: 'POST',
        headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ path, force }),
        credentials: 'include'
    }));
}

export async function triggerBulkThumbnails(path) {
    return handleResponse(await fetch(`${API_BASE_URL}/indexer/batch/thumbnails`, {
        method: 'POST',
        headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ path }),
        credentials: 'include'
    }));
}

export async function triggerBulkMetadata(path) {
    return handleResponse(await fetch(`${API_BASE_URL}/indexer/batch/metadata`, {
        method: 'POST',
        headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ path }),
        credentials: 'include'
    }));
}

// ============ Settings API ============

export async function fetchSettings() {
    return handleResponse(await fetch(`${API_BASE_URL}/settings/`, { credentials: 'include' }));
}

export async function updateSettings(settings) {
    return handleResponse(await fetch(`${API_BASE_URL}/settings/`, {
        method: 'POST',
        headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(settings),
        credentials: 'include'
    }));
}

// ============ Filesystem API ============
export async function fetchDirectoryListing(path) {
    const params = path ? `?path=${encodeURIComponent(path)}` : '';
    return handleResponse(await fetch(`${API_BASE_URL}/filesystem/list${params}`, { credentials: 'include' }));
}

// ============ Authentication API ============

export async function loginUser(email, password) {
    return handleResponse(await fetch(`${API_BASE_URL}/auth/login`, {
        method: 'POST',
        headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ email, password }),
        credentials: 'include'
    }));
}

export async function logoutUser() {
    return handleResponse(await fetch(`${API_BASE_URL}/auth/logout`, {
        method: 'POST',
        headers: withCsrfHeaders(),
        credentials: 'include'
    }));
}

export async function fetchCurrentUser() {
    return handleResponse(await fetch(`${API_BASE_URL}/auth/me`, {
        credentials: 'include'
    }));
}

export async function fetchSetupStatus() {
    return handleResponse(await fetch(`${API_BASE_URL}/auth/setup-status`, { credentials: 'include' }));
}

export async function signupAdmin(email, password) {
    return handleResponse(await fetch(`${API_BASE_URL}/auth/admin-sign-up`, {
        method: 'POST',
        headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ email, password }),
        credentials: 'include'
    }));
}

// ============ Users API ============

export async function fetchUsers() {
    return handleResponse(await fetch(`${API_BASE_URL}/users/`, { credentials: 'include' }));
}

export async function createUser(userData) {
    return handleResponse(await fetch(`${API_BASE_URL}/users/`, {
        method: 'POST',
        headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify(userData),
        credentials: 'include'
    }));
}

export async function deleteUser(userId) {
    return handleResponse(await fetch(`${API_BASE_URL}/users/${userId}`, {
        method: 'DELETE',
        headers: withCsrfHeaders(),
        credentials: 'include'
    }));
}

export async function updateUserRole(userId, isAdmin) {
    return handleResponse(await fetch(`${API_BASE_URL}/users/${userId}`, {
        method: 'PATCH',
        headers: withCsrfHeaders({ 'Content-Type': 'application/json' }),
        body: JSON.stringify({ is_admin: isAdmin }),
        credentials: 'include'
    }));
}


// ============ Utility Functions ============

export function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB', 'TB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

export function formatExposure(seconds) {
    if (!seconds && seconds !== 0) return 'N/A';
    if (seconds < 1) return `${(seconds * 1000).toFixed(0)}ms`;
    if (seconds < 60) return `${seconds.toFixed(1)}s`;
    if (seconds < 3600) return `${Math.floor(seconds / 60)}m ${seconds % 60}s`;
    const hours = Math.floor(seconds / 3600);
    const mins = Math.floor((seconds % 3600) / 60);
    return `${hours}h ${mins}m`;
}

export function formatRA(degrees) {
    if (degrees === undefined || degrees === null) return '--';
    const hours = degrees / 15;
    const h = Math.floor(hours);
    const m = Math.floor((hours - h) * 60);
    const s = ((hours - h) * 60 - m) * 60;

    const pad = (num) => num.toString().padStart(2, '0');
    const padS = (num) => num.toFixed(1).padStart(4, '0'); // "05.2" or "12.3"

    return `${pad(h)}:${pad(m)}:${padS(s)}`;
}

export function formatDec(degrees) {
    if (degrees === undefined || degrees === null) return '--';
    const sign = degrees >= 0 ? '+' : '-';
    const abs = Math.abs(degrees);
    const d = Math.floor(abs);
    const m = Math.floor((abs - d) * 60);
    const s = ((abs - d) * 60 - m) * 60;
    return `${sign}${d}° ${m}' ${s.toFixed(1)}"`;
}

export function formatDate(dateString) {
    if (!dateString) return 'Unknown';
    return new Date(dateString).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
    });
}

export function formatDateTime(dateString) {
    if (!dateString) return 'Unknown';
    return new Date(dateString).toLocaleString('en-US', {
        year: 'numeric',
        month: 'short',
        day: 'numeric',
        hour: '2-digit',
        minute: '2-digit',
    });
}

export function formatSubtype(subtype) {
    if (!subtype) return 'Unclassified';
    const mapping = {
        'SUB_FRAME': 'Sub Frame',
        'INTEGRATION_MASTER': 'Integration Master',
        'INTEGRATION_DEPRECATED': 'Deprecated',
        'PLANETARY': 'Planetary'
    };
    return mapping[subtype] || subtype;
}
