import { useState, useEffect, useRef, useMemo, useCallback } from 'react';
import { useAuth } from '../context/AuthContext';
import {
    fetchAdminStats,
    fetchWorkerStats,
    fetchIndexerStatus,
    fetchQueueDetails,
    triggerScan,
    fetchThumbnailStats,
    clearThumbnailCache,
    regenerateThumbnails,
    fetchSettings,
    updateSettings,
    triggerMountMatches,
    triggerMountRescan,
    fetchUsers,
    createUser,
    deleteUser,
    updateUserRole
} from '../api/client';
import './Admin.css';
import './Settings.css';

function Admin() {
    const { user } = useAuth();
    // Admin Dashboard State
    const [stats, setStats] = useState(null);
    const [workerStats, setWorkerStats] = useState(null);
    const [indexerStatus, setIndexerStatus] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [queueDetails, setQueueDetails] = useState(null);
    const [isModalOpen, setIsModalOpen] = useState(false);
    const [isQueueLoading, setIsQueueLoading] = useState(false);

    // Settings State
    const [scanning, setScanning] = useState(false);
    const [cacheStats, setCacheStats] = useState(null);
    const [cacheActionLoading, setCacheActionLoading] = useState(false);
    const [systemSettings, setSystemSettings] = useState({ astrometry_provider: 'nova' });
    const [settingsLoading, setSettingsLoading] = useState(false);
    const [bulkActionLoading, setBulkActionLoading] = useState({}); // { [path]: 'match' | 'rescan' | null }
    const [forceRescan, setForceRescan] = useState({}); // { [path]: bool }
    const [toast, setToast] = useState(null); // { message, type: 'info'|'success'|'error' }
    const [rescanModal, setRescanModal] = useState({ open: false, path: null, force: false, dontShowAgain: false });

    // User Management State
    const [users, setUsers] = useState([]);
    const [newUser, setNewUser] = useState({ email: '', password: '', confirmPassword: '' });
    const [userActionLoading, setUserActionLoading] = useState(false);

    // Lazy load settings section after main content renders
    const [settingsSectionReady, setSettingsSectionReady] = useState(false);

    const scanPollRef = useRef(null);

    const fetchAdminStatsRef = useRef(null);
    const fetchWorkerStatsRef = useRef(null);

    // Primary fast poll (Stats + Indexer)
    useEffect(() => {
        // Load all initial data in parallel
        Promise.all([
            loadData().catch(err => console.error('Failed to load stats:', err)),
            loadCacheStats().catch(err => console.error('Failed to load cache stats:', err)),
            loadSystemSettings().catch(err => console.error('Failed to load settings:', err)),
            loadUsers().catch(err => console.error('Failed to load users:', err))
        ]);

        // Start polling only after initial load
        const pollTimer = setTimeout(() => {
            fetchAdminStatsRef.current = setInterval(loadData, 4000); // Reduced frequency: 4s instead of 2s
        }, 500);

        return () => {
            clearTimeout(pollTimer);
            if (fetchAdminStatsRef.current) clearInterval(fetchAdminStatsRef.current);
        };
    }, []);

    // Secondary slow poll (Workers) - only if stats loaded
    useEffect(() => {
        if (!stats) return; // Skip if initial load not complete

        loadWorkerData();
        fetchWorkerStatsRef.current = setInterval(loadWorkerData, 8000); // Reduced frequency: 8s instead of 5s
        return () => {
            if (fetchWorkerStatsRef.current) clearInterval(fetchWorkerStatsRef.current);
        };
    }, [stats]);

    // Defer Settings section rendering until main pipeline loads
    useEffect(() => {
        if (stats) {
            const timer = setTimeout(() => setSettingsSectionReady(true), 300);
            return () => clearTimeout(timer);
        }
    }, [stats]);

    async function loadData() {
        try {
            const [adminStats, idxStatus] = await Promise.all([
                fetchAdminStats(),
                fetchIndexerStatus()
            ]);
            setStats(adminStats);
            setIndexerStatus(idxStatus);
            setScanning(idxStatus.is_running);
            setLoading(false);
            setError(null);
        } catch (err) {
            console.error(err);
            setError("Failed to fetch system stats");
            setLoading(false);
        }
    }

    async function loadWorkerData() {
        try {
            const data = await fetchWorkerStats();
            setWorkerStats(data);
        } catch (err) {
            console.error('Failed to fetch worker stats:', err);
        }
    }

    // Settings logic
    async function loadCacheStats() {
        try {
            const stats = await fetchThumbnailStats();
            setCacheStats(stats);
        } catch (err) {
            console.error('Failed to load cache stats:', err);
        }
    }

    async function loadSystemSettings() {
        try {
            const s = await fetchSettings();
            setSystemSettings(s);
        } catch (err) {
            console.error("Failed to load settings:", err);
        }
    }

    async function handleProviderChange(newProvider) {
        setSettingsLoading(true);
        try {
            const updated = await updateSettings({ ...systemSettings, astrometry_provider: newProvider });
            setSystemSettings(updated);
        } catch (err) {
            console.error("Failed to update settings:", err);
            alert("Failed to update settings: " + err.message);
            loadSystemSettings();
        } finally {
            setSettingsLoading(false);
        }
    }

    async function handleClearCache() {
        if (!confirm('Are you sure you want to clear all cached thumbnails? accessible images will need to regenerate them.')) return;

        setCacheActionLoading(true);
        try {
            await clearThumbnailCache();
            await loadCacheStats();
            alert('Thumbnail cache cleared successfully.');
        } catch (err) {
            console.error('Failed to clear cache:', err);
            alert('Failed to clear cache.');
        } finally {
            setCacheActionLoading(false);
        }
    }

    async function handleRegenerateThumbnails() {
        setCacheActionLoading(true);
        try {
            await regenerateThumbnails();
            alert('Thumbnail regeneration started in background.');
        } catch (err) {
            console.error('Failed to start regeneration:', err);
            alert('Failed to start regeneration.');
        } finally {
            setCacheActionLoading(false);
        }
    }

    async function handleStartScan() {
        if (scanPollRef.current) {
            clearInterval(scanPollRef.current);
            scanPollRef.current = null;
        }
        setScanning(true);
        try {
            const result = await triggerScan();
            showToast(`Scan started${result?.task_id ? ` (task ${result.task_id})` : ''}`, 'success', 2500);

            scanPollRef.current = setInterval(async () => {
                try {
                    const status = await fetchIndexerStatus();
                    setIndexerStatus(status);
                    if (!status.is_running) {
                        setScanning(false);
                        clearInterval(scanPollRef.current);
                        scanPollRef.current = null;
                    }
                } catch (pollErr) {
                    console.error('Failed to poll indexer status:', pollErr);
                    showToast('Lost connection while checking scan status.', 'error');
                    setScanning(false);
                    clearInterval(scanPollRef.current);
                    scanPollRef.current = null;
                }
            }, 1000);
        } catch (error) {
            console.error('Failed to start scan:', error);
            showToast(`Failed to start scan: ${error.message}`, 'error');
            setScanning(false);
        }
    }

    async function handleBulkMatch(path) {
        if (bulkActionLoading[path]) return;
        setBulkActionLoading(prev => ({ ...prev, [path]: 'match' }));
        try {
            await triggerMountMatches(path);
            alert(`Bulk matching started for ${path}`);
            // Single refresh after 2s instead of multiple staggered calls
            setTimeout(loadData, 2000);
        } catch (err) {
            console.error(err);
            alert("Failed to start bulk matching: " + err.message);
        } finally {
            setBulkActionLoading(prev => ({ ...prev, [path]: null }));
        }
    }

    function showToast(message, type = 'info', durationMs = 4000) {
        setToast({ message, type });
        if (durationMs > 0) {
            setTimeout(() => setToast(null), durationMs);
        }
    }

    async function startBulkRescan(path, force) {
        setBulkActionLoading(prev => ({ ...prev, [path]: 'rescan' }));
        try {
            const result = await triggerMountRescan(path, force);
            if (result.error) {
                showToast(`Failed to start bulk rescan: ${result.error}`, 'error');
                return;
            }
            showToast(`Bulk rescan started for ${path}. Task: ${result.task_id}`, 'success');
            // Single refresh after 2s instead of multiple staggered calls
            setTimeout(loadData, 2000);
        } catch (err) {
            console.error('[BULK RESCAN] Error:', err);
            showToast(`Failed to start bulk rescan: ${err.message}`, 'error');
        } finally {
            setBulkActionLoading(prev => ({ ...prev, [path]: null }));
        }
    }

    async function handleBulkRescan(path) {
        if (bulkActionLoading[path]) return;
        const force = forceRescan[path] || false;
        const suppress = localStorage.getItem('suppressBulkRescanConfirm') === '1';
        if (suppress) {
            startBulkRescan(path, force);
            return;
        }
        setRescanModal({ open: true, path, force, dontShowAgain: false });
    }

    function toggleForceRescan(path) {
        setForceRescan(prev => ({ ...prev, [path]: !prev[path] }));
    }

    function formatDuration(seconds) {
        if (seconds < 60) return `${seconds}s`;
        const mins = Math.floor(seconds / 60);
        const secs = seconds % 60;
        return `${mins}m ${secs}s`;
    }

    function formatDate(dateString) {
        if (!dateString) return 'Never';
        return new Date(dateString).toLocaleString();
    }

    // Admin-specific modal logic
    async function loadUsers() {
        try {
            const data = await fetchUsers();
            setUsers(data);
        } catch (err) {
            console.error('Failed to load users:', err);
        }
    }

    async function handleCreateUser(e) {
        e.preventDefault();
        if (newUser.password !== newUser.confirmPassword) {
            alert("Passwords do not match");
            return;
        }
        setUserActionLoading(true);
        try {
            await createUser({ email: newUser.email, password: newUser.password });
            setNewUser({ email: '', password: '', confirmPassword: '' });
            await loadUsers();
            showToast('User created successfully', 'success');
        } catch (err) {
            console.error('Failed to create user:', err);
            alert('Failed to create user: ' + err.message);
        } finally {
            setUserActionLoading(false);
        }
    }

    async function handleDeleteUser(userId, email) {
        if (!confirm(`Are you sure you want to delete user ${email}?`)) return;
        try {
            await deleteUser(userId);
            await loadUsers();
            showToast('User deleted', 'success');
        } catch (err) {
            console.error('Failed to delete user:', err);
            alert('Failed to delete user: ' + err.message);
        }
    }

    async function handleUpdateRole(userId, isAdmin) {
        try {
            await updateUserRole(userId, isAdmin);
            await loadUsers();
            showToast('User role updated', 'success');
        } catch (err) {
            console.error('Failed to update role:', err);
            alert('Failed to update role: ' + err.message);
        }
    }

    async function handleOpenQueueModal() {
        setIsModalOpen(true);
        setIsQueueLoading(true);
        try {
            const details = await fetchQueueDetails();
            setQueueDetails(details);
        } catch (err) {
            console.error(err);
        } finally {
            setIsQueueLoading(false);
        }
    }

    useEffect(() => {
        let interval;
        if (isModalOpen) {
            interval = setInterval(async () => {
                try {
                    const details = await fetchQueueDetails();
                    setQueueDetails(details);
                } catch (err) {
                    console.error(err);
                }
            }, 2000);
        }
        return () => clearInterval(interval);
    }, [isModalOpen]);

    // Derived states - Must be before early returns to follow Rules of Hooks
    const isBulkRunning = indexerStatus?.mount_points?.some(m => m.bulk_match?.status === 'running' || m.bulk_rescan?.status === 'running');
    const isScanning = indexerStatus?.is_running || isBulkRunning;
    const pendingTasks = (workerStats?.queue_active || 0) + (workerStats?.queue_reserved || 0) + (workerStats?.queue_scheduled || 0) + (stats?.queue?.pending || 0);
    const activeWorkers = workerStats?.concurrency || 0;

    const allActiveTasks = useMemo(() =>
        workerStats?.details?.flatMap(w => w.current_tasks) || [],
        [workerStats?.details]
    );

    const taskCounts = useMemo(() =>
        allActiveTasks.reduce((acc, task) => {
            if (!task) return acc;
            const name = task.split('.').pop().replace(/_/g, ' ');
            acc[name] = (acc[name] || 0) + 1;
            return acc;
        }, {}),
        [allActiveTasks]
    );

    const taskSummary = useMemo(() =>
        Object.entries(taskCounts)
            .map(([name, count]) => `${name} (${count})`)
            .join(', '),
        [taskCounts]
    );

    // Early returns after all hooks
    if (loading && !stats) return <div className="admin-page center">Loading System Telemetry...</div>;
    if (error && !stats) return (
        <div className="admin-page">
            <div style={{ color: '#ef4444', padding: '2rem', textAlign: 'center' }}>
                <p>{error}</p>
                <button className="btn btn-primary" onClick={() => window.location.reload()} style={{ marginTop: '1rem' }}>Retry</button>
            </div>
        </div>
    );

    // Render-time derived state (not hooks)
    let scannerClass = "";
    let queueClass = "";
    let processorClass = "";

    if (isScanning) scannerClass = "active pulsing";
    if (pendingTasks > 0) queueClass = "active";
    const activeTaskCount = workerStats?.queue_active || 0;
    if (activeTaskCount > 0) processorClass = "processing";

    return (
        <div className="admin-page">
            <header className="admin-header">
                <div className="logo-glow">🪐</div>
                <div>
                    <h1 className="admin-title">System Administration</h1>
                    <p className="admin-subtitle">Real-time Pipeline Observability</p>
                </div>
            </header>

            {/* PIPELINE VISUALIZATION */}
            <section className="pipeline-section">
                <h2 className="text-xl font-semibold mb-4 text-slate-300">Indexing Pipeline</h2>
                <div className="pipeline-container">
                    {/* 1. File Scanner */}
                    <div className={`pipeline-card ${scannerClass}`}>
                        <div className="card-icon">📂</div>
                        <div className="card-title">File Scanner</div>
                        <div className="card-value">
                            {indexerStatus?.files_scanned?.toLocaleString() || 0}
                        </div>
                        <div className="card-status">
                            <div className={`status-dot ${isScanning ? 'blue pulse' : 'gray'}`} />
                            {indexerStatus?.is_running ? 'Scanning Files...' : isBulkRunning ? 'Bulk Operation...' : 'Idle'}
                        </div>
                    </div>

                    {/* 2. Job Queue */}
                    <div className={`pipeline-card clickable ${queueClass}`} onClick={handleOpenQueueModal}>
                        <div className="card-icon">📚</div>
                        <div className="card-title">Job Queue</div>
                        <div className="card-value text-orange-400">
                            {pendingTasks}
                        </div>
                        <div className="card-status">
                            <div className={`status-dot ${pendingTasks > 0 ? 'orange' : 'gray'}`} />
                            {pendingTasks > 0 ? 'Pending' : 'Empty'}
                        </div>
                        {pendingTasks > 0 && (
                            <div className="progress-container">
                                <div className="progress-bar infinite-loader" style={{ width: '100%' }}></div>
                            </div>
                        )}
                        <div className="text-[10px] text-slate-500 mt-2 opacity-50">Click to inspect</div>
                    </div>

                    {/* 3. Processors */}
                    <div className={`pipeline-card ${processorClass}`}>
                        <div className="card-icon">⚙️</div>
                        <div className="card-title">Processors</div>
                        <div className="card-value text-blue-400">
                            <span className="text-2xl">{activeTaskCount}</span>
                            <span className="text-sm text-slate-500 mx-1">/</span>
                            <span className="text-lg text-slate-400">{activeWorkers}</span>
                        </div>
                        <div className="card-status">
                            <div className={`status-dot ${activeTaskCount > 0 ? 'blue pulse' : 'gray'}`} />
                            {activeTaskCount > 0 ? 'Active' : 'Idle'}
                        </div>
                        <div className="text-xs text-left w-full mt-2 text-slate-400 truncate h-4">
                            {taskSummary || 'System Ready'}
                        </div>
                    </div>

                    {/* 4. Database */}
                    <div className="pipeline-card">
                        <div className="card-icon">🗄️</div>
                        <div className="card-title">Database</div>
                        <div className="card-value">
                            {stats?.database?.record_count?.toLocaleString() || 0}
                        </div>
                        <div className="card-status">
                            <div className={`status-dot ${stats?.database?.status === 'connected' ? 'green' : 'red'}`} />
                            {stats?.database?.status === 'connected' ? 'Online' : 'Offline'}
                        </div>
                        {stats?.database?.astrometry_counts && (
                            <div className="text-[10px] text-slate-400 mt-2 text-left w-full leading-tight border-t border-slate-700/50 pt-2">
                                <span className="text-slate-500 uppercase font-bold text-[8px] block mb-1">Astrometry Status</span>
                                {Object.entries(stats.database.astrometry_counts)
                                    .sort(([a], [b]) => {
                                        const order = ['SOLVED', 'IMPORTED', 'SUBMITTED', 'PROCESSING', 'FAILED', 'UNSOLVED'];
                                        const idxA = order.indexOf(a);
                                        const idxB = order.indexOf(b);
                                        if (idxA !== -1 && idxB !== -1) return idxA - idxB;
                                        if (idxA !== -1) return -1;
                                        if (idxB !== -1) return 1;
                                        return a.localeCompare(b);
                                    })
                                    .map(([status, count]) => (
                                        <div key={status} className="flex justify-between">
                                            <span className={
                                                status === 'SOLVED' || status === 'IMPORTED' ? 'text-green-400' :
                                                    status === 'FAILED' ? 'text-red-400' :
                                                        status === 'SUBMITTED' || status === 'PROCESSING' ? 'text-blue-400' :
                                                            ''
                                            }>
                                                {String(status)}:
                                            </span>
                                            <span className="font-mono text-slate-300">
                                                {typeof count === 'object' ? JSON.stringify(count) : String(count)}
                                            </span>
                                        </div>
                                    ))}
                            </div>
                        )}
                    </div>

                    {/* ACTIVE BULK OPERATIONS */}
                    {isBulkRunning && (
                        <div className="active-bulk-ops mt-6">
                            {indexerStatus?.mount_points?.filter(m => m.bulk_match?.status === 'running' || m.bulk_rescan?.status === 'running').map(mount => (
                                <div key={mount.path} className="bulk-op-item p-3 mb-2 rounded bg-slate-800/50 border border-slate-700">
                                    <div className="flex justify-between mb-2">
                                        <span className="text-sm font-mono text-slate-400">{mount.path}</span>
                                        <span className="text-xs font-bold text-blue-400 uppercase tracking-wider">
                                            {mount.bulk_match?.status === 'running' ? 'Recalculating Matches' : 'Bulk Rescanning'}
                                        </span>
                                    </div>
                                    {mount.bulk_match?.status === 'running' && (
                                        <div className="flex items-center gap-3">
                                            <div className="flex-1 h-2 bg-slate-700 rounded-full overflow-hidden">
                                                <div
                                                    className="h-full bg-blue-500 transition-all duration-300"
                                                    style={{ width: `${(mount.bulk_match.processed / (mount.bulk_match.total || 1)) * 100}%` }}
                                                />
                                            </div>
                                            <span className="text-xs text-slate-300 whitespace-nowrap">
                                                {mount.bulk_match.processed} / {mount.bulk_match.total}
                                                <span className="opacity-50 ml-1">({mount.bulk_match.errors || 0} errors, {mount.bulk_match.skipped || 0} skipped)</span>
                                            </span>
                                        </div>
                                    )}
                                    {mount.bulk_rescan?.status === 'running' && (
                                        <div className="flex items-center gap-3">
                                            <div className="flex-1 h-2 bg-slate-700 rounded-full overflow-hidden">
                                                <div
                                                    className="h-full bg-cyan-500 transition-all duration-300"
                                                    style={{ width: `${(mount.bulk_rescan.processed / (mount.bulk_rescan.total || 1)) * 100}%` }}
                                                />
                                            </div>
                                            <span className="text-xs text-slate-300 whitespace-nowrap">
                                                {mount.bulk_rescan.processed} / {mount.bulk_rescan.total}
                                                <span className="opacity-50 ml-1">({mount.bulk_rescan.queued} queued, {mount.bulk_rescan.skipped || 0} skipped)</span>
                                            </span>
                                        </div>
                                    )}
                                </div>
                            ))}
                        </div>
                    )}
                </div>
            </section>

            {/* TECH STACK & HEALTH */}
            <section className="health-section">
                <h2 className="text-xl font-semibold mb-4 text-slate-300">System Health</h2>
                <div className="tech-grid">
                    <div className="tech-card">
                        <div className="tech-header">
                            <span className="tech-name">PostgreSQL + PostGIS</span>
                            <span className="tech-status-badge">HEALTHY</span>
                        </div>
                        <div className="tech-details">
                            <span className="tech-metric">{stats?.database?.size_str}</span>
                            <span className="tech-label">Size</span>
                        </div>
                    </div>
                    <div className="tech-card">
                        <div className="tech-header">
                            <span className="tech-name">Redis Broker</span>
                            <span className={`tech-status-badge ${stats?.redis?.status === 'connected' ? '' : 'bg-red-900 text-red-200'}`}>
                                {stats?.redis?.status === 'connected' ? 'CONNECTED' : 'ERROR'}
                            </span>
                        </div>
                        <div className="tech-details">
                            <span className="tech-metric">{stats?.redis?.memory_used_mb} MB</span>
                            <span className="tech-label">Memory</span>
                        </div>
                    </div>
                    <div className="tech-card">
                        <div className="tech-header">
                            <span className="tech-name">Celery Workers</span>
                            <span className="tech-status-badge">OPERATIONAL</span>
                        </div>
                        <div className="tech-details">
                            <span className="tech-metric">{activeWorkers}</span>
                            <span className="tech-label">Threads</span>
                        </div>
                    </div>
                    <div className="tech-card">
                        <div className="tech-header">
                            <span className="tech-name">Thumbnail Cache</span>
                            <span className="tech-status-badge">DISK</span>
                        </div>
                        <div className="tech-details">
                            <span className="tech-metric">{stats?.disk?.thumbnail_cache_gb} GB</span>
                            <span className="tech-label">Storage</span>
                        </div>
                    </div>
                </div>
            </section>

            {/* SETTINGS CONTENT BELOW */}
            {settingsSectionReady && (
                <div className="settings-page" style={{ padding: 0, marginTop: '4rem', borderTop: '1px solid rgba(255,255,255,0.1)', paddingTop: '4rem' }}>
                    {/* Toast */}
                    {toast && (
                        <div style={{ position: 'fixed', top: '16px', right: '16px', zIndex: 1000, background: toast.type === 'error' ? '#7f1d1d' : toast.type === 'success' ? '#065f46' : '#1f2937', color: 'white', padding: '10px 14px', borderRadius: '6px', boxShadow: '0 6px 18px rgba(0,0,0,0.25)' }} role="status" aria-live="polite">
                            {toast.message}
                        </div>
                    )}

                    {/* Bulk Rescan Confirm Modal */}
                    {rescanModal.open && (
                        <div style={{ position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.5)', zIndex: 999 }}>
                            <div style={{ width: 'min(480px, 92vw)', margin: '10vh auto', background: '#111827', color: 'white', borderRadius: '8px', border: '1px solid #374151', boxShadow: '0 10px 30px rgba(0,0,0,0.35)' }}>
                                <div style={{ padding: '16px 18px', borderBottom: '1px solid #374151', fontWeight: 600 }}>Confirm Bulk Rescan</div>
                                <div style={{ padding: '16px 18px' }}>
                                    <p style={{ marginBottom: '10px' }}>Start bulk rescan for <span className="font-mono">{rescanModal.path}</span>?</p>
                                    <p style={{ marginBottom: '12px', color: '#9CA3AF' }}>Force Re-solve: {rescanModal.force ? 'YES' : 'NO'}</p>
                                    <label style={{ display: 'flex', alignItems: 'center', gap: '8px', fontSize: '0.95em' }}>
                                        <input type="checkbox" checked={rescanModal.dontShowAgain} onChange={(e) => setRescanModal(prev => ({ ...prev, dontShowAgain: e.target.checked }))} />
                                        Don't show again
                                    </label>
                                </div>
                                <div style={{ padding: '12px 18px', display: 'flex', justifyContent: 'flex-end', gap: '10px', borderTop: '1px solid #374151' }}>
                                    <button className="btn btn-secondary btn-sm" onClick={() => setRescanModal({ open: false, path: null, force: false, dontShowAgain: false })}>Cancel</button>
                                    <button className="btn btn-primary btn-sm" onClick={() => { if (rescanModal.dontShowAgain) localStorage.setItem('suppressBulkRescanConfirm', '1'); const { path, force } = rescanModal; setRescanModal({ open: false, path: null, force: false, dontShowAgain: false }); startBulkRescan(path, force); }}>Start</button>
                                </div>
                            </div>
                        </div>
                    )}

                    <div className="page-header">
                        <h1 className="page-title">Settings</h1>
                        <p className="page-subtitle">Manage indexing and application preferences</p>
                    </div>

                    {/* Indexer Section */}
                    <section className="settings-section">
                        <h2 className="section-title">🔍 Indexer</h2>
                        <div className="indexer-card">
                            <div className="indexer-status">
                                <div className={`status-indicator ${scanning ? 'running' : 'idle'}`}>
                                    {scanning ? (
                                        <><div className="status-dot pulsing" /><span>Scanning...</span></>
                                    ) : (
                                        <><div className="status-dot" /><span>Idle</span></>
                                    )}
                                </div>
                                <button className="btn btn-primary" onClick={handleStartScan} disabled={scanning}>{scanning ? 'Scanning...' : 'Start Scan'}</button>
                            </div>
                            {indexerStatus && (
                                <div className="indexer-details">
                                    <div className="detail-row"><span className="detail-label">Last Scan</span><span className="detail-value">{formatDate(indexerStatus.last_scan_at)}</span></div>
                                    <div className="detail-row"><span className="detail-label">Duration</span><span className="detail-value">{formatDuration(indexerStatus.last_scan_duration_seconds)}</span></div>
                                    <div className="detail-row"><span className="detail-label">Files Scanned</span><span className="detail-value">{indexerStatus.files_scanned.toLocaleString()}</span></div>
                                    <div className="detail-row"><span className="detail-label">Files Added</span><span className="detail-value text-success">+{indexerStatus.files_added}</span></div>
                                    <div className="detail-row"><span className="detail-label">Files Updated</span><span className="detail-value">{indexerStatus.files_updated}</span></div>
                                    <div className="detail-row"><span className="detail-label">Files Removed</span><span className="detail-value text-error">-{indexerStatus.files_removed}</span></div>
                                </div>
                            )}
                        </div>
                    </section>

                    {/* Plate Solving Section */}
                    <section className="settings-section">
                        <h2 className="section-title">🔭 Plate Solving</h2>
                        <div className="card">
                            <div className="setting-row" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '1rem' }}>
                                <div>
                                    <div className="setting-label" style={{ fontWeight: 'bold' }}>Astrometry Provider</div>
                                    <div className="setting-description text-muted text-sm" style={{ marginTop: '0.25rem' }}>Choose between the public Nova.astrometry.net service or a local Astrometry server.</div>
                                </div>
                                <div className="toggle-group" style={{ display: 'flex', gap: '0.5rem', background: '#2d3748', padding: '0.25rem', borderRadius: '0.5rem' }}>
                                    <button className={`btn btn-sm ${systemSettings.astrometry_provider === 'nova' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => handleProviderChange('nova')} disabled={settingsLoading}>☁️ Nova Web</button>
                                    <button className={`btn btn-sm ${systemSettings.astrometry_provider === 'local' ? 'btn-primary' : 'btn-ghost'}`} onClick={() => handleProviderChange('local')} disabled={settingsLoading}>🏠 Local Server</button>
                                </div>
                            </div>
                            {systemSettings.astrometry_provider === 'local' && (
                                <div style={{ padding: '0 1rem 1rem 1rem', fontSize: '0.9em', color: '#a0aec0' }}>Using configured local URL. Ensure your local server is running.</div>
                            )}
                            <div className="setting-row" style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '1rem', borderTop: '1px solid #2d3748' }}>
                                <div>
                                    <div className="setting-label" style={{ fontWeight: 'bold' }}>Astrometry Max Submissions</div>
                                    <div className="setting-description text-muted text-sm" style={{ marginTop: '0.25rem' }}>Limit concurrent submissions to the astrometry server.</div>
                                </div>
                                <div>
                                    <input type="number" min="1" max="50" className="input" style={{ width: '80px', background: '#2d3748', border: '1px solid #4a5568', color: 'white', padding: '0.25rem 0.5rem', borderRadius: '0.25rem' }} value={systemSettings.astrometry_max_submissions || 8} onChange={(e) => { const val = parseInt(e.target.value) || 1; updateSettings({ ...systemSettings, astrometry_max_submissions: val }).then(setSystemSettings).catch(err => alert("Failed to update: " + err.message)); }} disabled={settingsLoading} />
                                </div>
                            </div>
                        </div>
                    </section>

                    {/* Mount Points Section */}
                    <section className="settings-section">
                        <h2 className="section-title">📁 Mount Points</h2>
                        <div className="mount-points-list">
                            {indexerStatus?.mount_points?.map((mount) => (
                                <div key={mount.path} className="mount-point-card">
                                    <div className="mount-header">
                                        <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
                                            <span className="mount-path font-mono">{mount.path}</span>
                                            <div className="mount-friendly-name-section">
                                                <input
                                                    type="text"
                                                    className="friendly-name-input"
                                                    placeholder="Assign a friendly name..."
                                                    value={systemSettings.mount_friendly_names?.[mount.path] || ''}
                                                    onChange={(e) => {
                                                        const newNames = { ...systemSettings.mount_friendly_names, [mount.path]: e.target.value };
                                                        setSystemSettings({ ...systemSettings, mount_friendly_names: newNames });
                                                    }}
                                                />
                                                <button
                                                    className="btn btn-ghost btn-sm"
                                                    title="Save Friendly Name"
                                                    onClick={() => {
                                                        updateSettings(systemSettings)
                                                            .then(() => showToast('Friendly name saved', 'success'))
                                                            .catch(err => showToast('Failed to save: ' + err.message, 'error'));
                                                    }}
                                                    disabled={settingsLoading}
                                                >
                                                    💾 Save
                                                </button>
                                            </div>
                                        </div>
                                        <span className={`mount-status ${mount.status}`}>{mount.status === 'connected' ? '✓ Connected' : '✗ Disconnected'}</span>
                                    </div>
                                    <div className="mount-stats">
                                        <div className="mount-stat"><span className="stat-value">{mount.file_count.toLocaleString()}</span><span className="stat-label">Files</span></div>
                                        <div className="mount-stat"><span className="stat-value">{mount.size_gb.toFixed(1)} GB</span><span className="stat-label">Size</span></div>
                                    </div>
                                    {(() => {
                                        const isMatchVisible = mount.bulk_match && (mount.bulk_match.status === 'running' || mount.bulk_match.status === 'failed' || (Date.now() / 1000 - parseInt(mount.bulk_match.updated_at || 0)) < 300);
                                        const isRescanVisible = mount.bulk_rescan && (mount.bulk_rescan.status === 'running' || mount.bulk_rescan.status === 'failed' || (Date.now() / 1000 - parseInt(mount.bulk_rescan.updated_at || 0)) < 300);
                                        if (!isMatchVisible && !isRescanVisible) return null;
                                        return (
                                            <div className="mount-progress-section">
                                                {isMatchVisible && (
                                                    <div className="bulk-status-row">
                                                        <span className="status-label">Matching:</span>
                                                        {mount.bulk_match.status === 'running' ? (
                                                            <><div className="status-bar-container"><div className="status-bar-fill" style={{ width: `${(mount.bulk_match.processed / (mount.bulk_match.total || 1)) * 100}%` }} /></div><span className="status-text">{mount.bulk_match.processed} / {mount.bulk_match.total}<span className="sub-text">({mount.bulk_match.errors || 0} errors, {mount.bulk_match.skipped || 0} skipped)</span></span></>
                                                        ) : (
                                                            <span className={`status-text ${mount.bulk_match.status === 'failed' ? 'text-error' : 'text-success'}`}>{mount.bulk_match.status} ({mount.bulk_match.processed} total, {mount.bulk_match.errors || 0} errors, {mount.bulk_match.skipped || 0} skipped)</span>
                                                        )}
                                                    </div>
                                                )}
                                                {isRescanVisible && (
                                                    <div className="bulk-status-row">
                                                        <span className="status-label">Rescanning:</span>
                                                        {mount.bulk_rescan.status === 'running' ? (
                                                            <><div className="status-bar-container"><div className="status-bar-fill rescan" style={{ width: `${(mount.bulk_rescan.processed / (mount.bulk_rescan.total || 1)) * 100}%` }} /></div><span className="status-text">{mount.bulk_rescan.processed} / {mount.bulk_rescan.total}<span className="sub-text">({mount.bulk_rescan.queued} queued, {mount.bulk_rescan.skipped || 0} skipped)</span></span></>
                                                        ) : (
                                                            <span className={`status-text ${mount.bulk_rescan.status === 'failed' ? 'text-error' : 'text-success'}`}>{mount.bulk_rescan.status} ({mount.bulk_rescan.processed} processed, {mount.bulk_rescan.queued} queued, {mount.bulk_rescan.skipped || 0} skipped){mount.bulk_rescan.error && <span className="sub-text text-error">{mount.bulk_rescan.error}</span>}{mount.bulk_rescan.status === 'completed' && parseInt(mount.bulk_rescan.queued) === 0 && (<span className="sub-text" style={{ color: 'var(--color-warning)', marginTop: '4px', fontSize: '0.85em', display: 'block' }}>(No images required solving. Use 'Force' to re-solve existing ones.)</span>)}</span>
                                                        )}
                                                    </div>
                                                )}
                                            </div>
                                        );
                                    })()}
                                    <div className="mount-actions-footer">
                                        <div className="action-group"><button className="btn btn-sm btn-secondary" onClick={() => handleBulkMatch(mount.path)} disabled={bulkActionLoading[mount.path] || mount.status !== 'connected'}>🔄 Recalc Matches</button></div>
                                        <div className="action-group right"><label className="checkbox-label"><input type="checkbox" checked={forceRescan[mount.path] || false} onChange={() => toggleForceRescan(mount.path)} />Force</label><button className="btn btn-sm btn-primary" onClick={() => handleBulkRescan(mount.path)} disabled={bulkActionLoading[mount.path] || mount.status !== 'connected'}>{bulkActionLoading[mount.path] === 'rescan' ? '⏳ Starting...' : '🔭 Bulk Rescan'}</button></div>
                                    </div>
                                </div>
                            ))}
                        </div>
                        <div className="mount-actions"><button className="btn btn-secondary" onClick={() => alert("To add a new mount point:\n\n1. Add the path to your .env file (IMAGE_PATH_X)\n2. Add the volume mapping in docker-compose.yml\n3. Restart the application")}>+ Add Mount Point</button><p className="text-sm text-muted mt-2">Note: Mount points are configured in docker-compose.yml</p></div>
                    </section>

                    {/* Thumbnail Cache Section */}
                    <section className="settings-section">
                        <h2 className="section-title">🖼️ Thumbnail Cache</h2>
                        <div className="cache-card">
                            <div className="cache-info">
                                <div className="cache-stat">{cacheStats ? <span className="cache-value">{cacheStats.count.toLocaleString()}</span> : <span className="cache-value">--</span>}<span className="cache-label">Cached Thumbnails</span></div>
                                <div className="cache-stat">{cacheStats ? <span className="cache-value">{cacheStats.size_mb} MB</span> : <span className="cache-value">--</span>}<span className="cache-label">Cache Size</span></div>
                            </div>
                            <div className="cache-actions"><button className="btn btn-secondary" onClick={handleClearCache} disabled={cacheActionLoading}>{cacheActionLoading ? 'Processing...' : 'Clear Cache'}</button><button className="btn btn-primary" onClick={handleRegenerateThumbnails} disabled={cacheActionLoading} style={{ marginLeft: '1rem' }}>Regenerate All</button></div>
                        </div>
                    </section>

                    {/* User Management Section */}
                    <section className="settings-section">
                        <h2 className="section-title">👥 User Management</h2>
                        <div className="card" style={{ padding: '1.5rem' }}>
                            <div style={{ marginBottom: '2rem' }}>
                                <h3 style={{ fontSize: '1.1rem', marginBottom: '1rem', color: '#e2e8f0' }}>Register New User</h3>
                                <form onSubmit={handleCreateUser} style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: '1rem', alignItems: 'flex-end' }}>
                                    <div className="form-group" style={{ margin: 0 }}>
                                        <label style={{ fontSize: '0.8rem', color: '#94a3b8' }}>Email Address</label>
                                        <input
                                            type="email"
                                            className="input"
                                            style={{ width: '100%', background: '#1e293b', border: '1px solid #334155', color: 'white', padding: '0.5rem', borderRadius: '0.4rem' }}
                                            value={newUser.email}
                                            onChange={e => setNewUser({ ...newUser, email: e.target.value })}
                                            required
                                        />
                                    </div>
                                    <div className="form-group" style={{ margin: 0 }}>
                                        <label style={{ fontSize: '0.8rem', color: '#94a3b8' }}>Password</label>
                                        <input
                                            type="password"
                                            className="input"
                                            style={{ width: '100%', background: '#1e293b', border: '1px solid #334155', color: 'white', padding: '0.5rem', borderRadius: '0.4rem' }}
                                            value={newUser.password}
                                            onChange={e => setNewUser({ ...newUser, password: e.target.value })}
                                            required
                                        />
                                    </div>
                                    <div className="form-group" style={{ margin: 0 }}>
                                        <label style={{ fontSize: '0.8rem', color: '#94a3b8' }}>Confirm Password</label>
                                        <input
                                            type="password"
                                            className="input"
                                            style={{ width: '100%', background: '#1e293b', border: '1px solid #334155', color: 'white', padding: '0.5rem', borderRadius: '0.4rem' }}
                                            value={newUser.confirmPassword}
                                            onChange={e => setNewUser({ ...newUser, confirmPassword: e.target.value })}
                                            required
                                        />
                                    </div>
                                    <button
                                        type="submit"
                                        className="btn btn-primary"
                                        disabled={userActionLoading}
                                        style={{ height: '42px' }}
                                    >
                                        {userActionLoading ? 'Creating...' : 'Add User'}
                                    </button>
                                </form>
                            </div>

                            <div className="table-wrapper">
                                <table className="admin-table">
                                    <thead>
                                        <tr>
                                            <th>Email</th>
                                            <th>Role</th>
                                            <th>Since</th>
                                            <th>Actions</th>
                                        </tr>
                                    </thead>
                                    <tbody>
                                        {users.map(u => (
                                            <tr key={u.id}>
                                                <td className="text-blue-300">{u.email}</td>
                                                <td>
                                                    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
                                                        <span style={{
                                                            padding: '2px 8px',
                                                            borderRadius: '12px',
                                                            fontSize: '0.75rem',
                                                            background: u.is_admin ? 'rgba(59, 130, 246, 0.2)' : 'rgba(148, 163, 184, 0.1)',
                                                            color: u.is_admin ? '#60a5fa' : '#94a3b8',
                                                            border: u.is_admin ? '1px solid rgba(59, 130, 246, 0.3)' : '1px solid rgba(148, 163, 184, 0.2)'
                                                        }}>
                                                            {u.is_admin ? 'Administrator' : 'General User'}
                                                        </span>
                                                        <button
                                                            className="btn btn-ghost btn-xs"
                                                            style={{ fontSize: '0.65rem', opacity: u.id === user?.id ? 0.3 : 1 }}
                                                            onClick={() => handleUpdateRole(u.id, !u.is_admin)}
                                                            disabled={u.id === user?.id}
                                                            title={u.id === user?.id ? "Cannot change your own role" : "Toggle Role"}
                                                        >
                                                            🔄 Switch
                                                        </button>
                                                    </div>
                                                </td>
                                                <td className="text-slate-500 text-xs">{new Date(u.created_at).toLocaleDateString()}</td>
                                                <td>
                                                    <button
                                                        className="btn btn-ghost btn-sm"
                                                        style={{ color: '#ef4444' }}
                                                        onClick={() => handleDeleteUser(u.id, u.email)}
                                                        disabled={u.id === user?.id}
                                                    >
                                                        Delete
                                                    </button>
                                                </td>
                                            </tr>
                                        ))}
                                    </tbody>
                                </table>
                            </div>
                        </div>
                    </section>

                    {/* About Section */}
                    <section className="settings-section">
                        <h2 className="section-title">ℹ️ About</h2>
                        <div className="about-card">
                            <div className="about-logo">🌌 AstroCat</div>
                            <div className="about-version">Version 1.0.0 (Development Build)</div>
                            <p className="about-description">Astronomical Image Database - A modern web application for cataloging, indexing, and retrieving astronomical image files with plate-solving metadata and celestial object associations.</p>
                            <div className="about-links"><a href="https://github.com/james474789/AstroCat" target="_blank" rel="noopener" className="link">GitHub Repository</a><a href="#" className="link">Documentation</a></div>
                        </div>
                    </section>
                </div>
            )}

            {/* QUEUE DETAILS MODAL */}
            {isModalOpen && (
                <div className="modal-overlay" onClick={() => setIsModalOpen(false)}>
                    <div className="modal-content admin-modal" onClick={e => e.stopPropagation()}>
                        <header className="modal-header">
                            <h2 className="text-xl font-bold">Task Queue Inspection</h2>
                            <button className="close-button" onClick={() => setIsModalOpen(false)}>×</button>
                        </header>
                        <div className="modal-body overflow-y-auto max-h-[70vh]">
                            {isQueueLoading && !queueDetails ? (
                                <div className="p-8 text-center text-slate-400">Loading queue topology...</div>
                            ) : (
                                <div className="queue-tables space-y-8">
                                    <section>
                                        <h3 className="text-blue-400 font-bold mb-2 flex items-center gap-2">
                                            <div className="w-2 h-2 rounded-full bg-blue-500 pulse" />
                                            Active Tasks ({queueDetails?.active?.length || 0})
                                        </h3>
                                        <div className="table-wrapper">
                                            <table className="admin-table">
                                                <thead><tr><th>Task</th><th>Worker</th><th>Args</th><th>Started</th></tr></thead>
                                                <tbody>
                                                    {queueDetails?.active?.length > 0 ? queueDetails.active.map(t => (
                                                        <tr key={t.id}>
                                                            <td className="font-mono text-xs text-blue-300">{t.name?.split('.').pop()}</td>
                                                            <td className="text-xs text-slate-400">{t.worker?.split('@').shift()}</td>
                                                            <td className="text-[10px] text-slate-500 truncate max-w-[200px]" title={JSON.stringify(t.args)}>{JSON.stringify(t.args)}</td>
                                                            <td className="text-xs text-slate-400">{t.time_start ? new Date(t.time_start * 1000).toLocaleTimeString() : '-'}</td>
                                                        </tr>
                                                    )) : <tr><td colSpan="4" className="text-center py-4 text-slate-600 italic text-sm">No active tasks</td></tr>}
                                                </tbody>
                                            </table>
                                        </div>
                                    </section>
                                    <section>
                                        <h3 className="text-orange-400 font-bold mb-2 flex items-center gap-2">
                                            <div className="w-2 h-2 rounded-full bg-orange-500" />
                                            Pending In Queue ({queueDetails?.pending?.length || 0})
                                        </h3>
                                        <div className="table-wrapper">
                                            <table className="admin-table">
                                                <thead><tr><th>Task</th><th>Queue</th><th>Args</th></tr></thead>
                                                <tbody>
                                                    {queueDetails?.pending?.length > 0 ? queueDetails.pending.map((t, idx) => (
                                                        <tr key={t.id || idx}>
                                                            <td className="font-mono text-xs text-orange-300">{t.name?.split('.').pop() || 'Unknown'}</td>
                                                            <td className="text-xs text-slate-400">{t.queue}</td>
                                                            <td className="text-[10px] text-slate-500 truncate max-w-[200px]" title={JSON.stringify(t.args)}>{JSON.stringify(t.args)}</td>
                                                        </tr>
                                                    )) : <tr><td colSpan="3" className="text-center py-4 text-slate-600 italic text-sm">No pending tasks</td></tr>}
                                                </tbody>
                                            </table>
                                        </div>
                                    </section>
                                    {queueDetails?.scheduled?.length > 0 && (
                                        <section>
                                            <h3 className="text-purple-400 font-bold mb-2 flex items-center gap-2">
                                                <div className="w-2 h-2 rounded-full bg-purple-500" />
                                                Scheduled Tasks ({queueDetails.scheduled.length})
                                            </h3>
                                            <div className="table-wrapper">
                                                <table className="admin-table">
                                                    <thead><tr><th>Task</th><th>ETA</th></tr></thead>
                                                    <tbody>
                                                        {queueDetails.scheduled.map(t => (
                                                            <tr key={t.id}>
                                                                <td className="font-mono text-xs text-purple-300">{t.name?.split('.').pop()}</td>
                                                                <td className="text-xs text-slate-400">{t.eta}</td>
                                                            </tr>
                                                        ))}
                                                    </tbody>
                                                </table>
                                            </div>
                                        </section>
                                    )}
                                </div>
                            )}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

export default Admin;
