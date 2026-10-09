import { useState, useEffect, useRef } from 'react';
import { ChevronDown, ChevronRight, Loader2, Folder, FolderOpen, Home, MoreHorizontal, Image as ImageIcon, FileText, RefreshCw } from 'lucide-react';
import TelescopeIcon from '../icons/TelescopeIcon';
import { fetchDirectoryListing, triggerBulkThumbnails, triggerBulkMetadata, triggerMountRescan, triggerFolderScan } from '../../api/client';
import { useToast } from '../ui';
import './FolderTree.css';

// Robust path-prefix check to avoid partial matches (e.g. /data matching /data2)
const isPathParent = (parent, child) => {
    if (!child || !parent) return false;
    if (!child.startsWith(parent)) return false;
    if (parent === child) return false;

    // Check if the next character in child is a path separator
    const nextChar = child[parent.length];
    return nextChar === '/' || nextChar === '\\';
};

function FolderNode({ item, level, selectedPath, onSelect, onContextMenu, showMenu }) {
    const [isExpanded, setIsExpanded] = useState(false);
    const [children, setChildren] = useState([]);
    const [loading, setLoading] = useState(false);
    const [error, setError] = useState(null);
    const itemRef = useRef(null);

    // Check if this node is part of the selected path to auto-expand
    useEffect(() => {
        if (selectedPath && isPathParent(item.path, selectedPath)) {
            if (!isExpanded && item.has_children) {
                handleExpand();
            }
        }
    }, [selectedPath]);

    const isSelected = selectedPath === item.path;

    // Scroll into view when selected
    useEffect(() => {
        if (isSelected && itemRef.current) {
            itemRef.current.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
        }
    }, [isSelected]);

    async function handleExpand() {
        if (isExpanded) {
            setIsExpanded(false);
            return;
        }

        setLoading(true);
        setError(null);
        try {
            const data = await fetchDirectoryListing(item.path);
            if (Array.isArray(data)) {
                setChildren(data.filter(i => i.type === 'directory'));
                setIsExpanded(true);
            } else {
                console.error('Invalid directory listing:', data);
                setError(data?.detail || 'Invalid data received');
            }
        } catch (err) {
            setError('Failed to load');
            console.error(err);
        } finally {
            setLoading(false);
        }
    }



    return (
        <div className="folder-node">
            <div
                ref={itemRef}
                className={`folder-item ${isSelected ? 'selected' : ''}`}
                style={{ paddingLeft: `${level * 16}px` }}
                onClick={() => onSelect(item.path)}
                onContextMenu={(e) => onContextMenu(e, item.path)}
            >
                <div
                    className="folder-toggle"
                    onClick={(e) => {
                        e.stopPropagation();
                        handleExpand();
                    }}
                >
                    {item.has_children ? (
                        <span className="toggle-icon">
                            {loading ? <Loader2 size={12} className="ui-spin" aria-hidden="true" /> : (isExpanded ? <ChevronDown size={14} aria-hidden="true" /> : <ChevronRight size={14} aria-hidden="true" />)}
                        </span>
                    ) : <span className="toggle-spacer"></span>}
                </div>
                <span className="folder-icon">{isExpanded ? <FolderOpen size={16} /> : <Folder size={16} />}</span>
                <span className="folder-name">{item.name}</span>
                {item.image_count > 0 && (
                    <span className="folder-count">
                        {item.image_count.toLocaleString()}
                    </span>
                )}
                {/* Touch has no right-click and iOS long-press is unreliable, so offer an explicit button */}
                {showMenu && <button
                    type="button"
                    className="folder-more"
                    aria-label={`Actions for ${item.name}`}
                    onClick={(e) => {
                        e.stopPropagation();
                        const r = e.currentTarget.getBoundingClientRect();
                        onContextMenu({ preventDefault() {}, clientX: r.left, clientY: r.bottom }, item.path);
                    }}
                >
                    <MoreHorizontal size={18} aria-hidden="true" />
                </button>}
            </div>
            {error && <div className="folder-error" style={{ paddingLeft: `${(level + 1) * 16}px` }}>{error}</div>}
            {isExpanded && (
                <div className="folder-children">
                    {children.map(child => (
                        <FolderNode
                            key={child.path}
                            item={child}
                            level={level + 1}
                            selectedPath={selectedPath}
                            onSelect={onSelect}
                            onContextMenu={onContextMenu}
                            showMenu={showMenu}
                        />
                    ))}
                    {children.length === 0 && !loading && (
                        <div className="folder-empty" style={{ paddingLeft: `${(level + 1) * 16}px` }}>
                            (Empty)
                        </div>
                    )}
                </div>
            )}
        </div>
    );
}

export default function FolderTree({ selectedPath, onSelect, showContextMenu = true }) {
    const [roots, setRoots] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [contextMenu, setContextMenu] = useState(null); // { x, y, path }
    const toast = useToast();

    useEffect(() => {
        loadRoots();
    }, []);

    useEffect(() => {
        const handleCloseMenu = () => setContextMenu(null);
        window.addEventListener('click', handleCloseMenu);
        window.addEventListener('scroll', handleCloseMenu, true);
        return () => {
            window.removeEventListener('click', handleCloseMenu);
            window.removeEventListener('scroll', handleCloseMenu, true);
        };
    }, []);

    async function loadRoots() {
        setLoading(true);
        setError(null);
        try {
            const data = await fetchDirectoryListing();
            if (Array.isArray(data)) {
                setRoots(data);
            } else {
                console.error('Invalid roots data:', data);
                setRoots([]);
                // If it's an object with detail, it might be an error from backend
                if (data && data.detail) {
                    setError(data.detail);
                }
            }
        } catch (err) {
            setError(err.message || 'Failed to load mount points');
            console.error(err);
        } finally {
            setLoading(false);
        }
    }

    const handleContextMenu = (e, path) => {
        if (!showContextMenu) return;
        e.preventDefault();
        setContextMenu({
            x: e.clientX,
            y: e.clientY,
            path: path
        });
    };

    const handleAction = async (action) => {
        if (!contextMenu) return;
        const path = contextMenu.path;
        setContextMenu(null);

        try {
            if (action === 'thumbnails') {
                await triggerBulkThumbnails(path);
            } else if (action === 'metadata') {
                await triggerBulkMetadata(path);
            } else if (action === 'scan') {
                const result = await triggerFolderScan(path);
                if (result?.error) {
                    throw new Error(result.error);
                }
                toast.success(`Folder scan started for ${path}${result?.task_id ? ` (task ${result.task_id})` : ''}`);
            } else if (action === 'astrometry') {
                await triggerMountRescan(path, true); // true to force rescan
            }
        } catch (err) {
            console.error(`Failed to trigger ${action}:`, err);
            toast.error(`Failed: ${err.message}`);
        }
    };

    if (loading && roots.length === 0) return <div className="p-md text-muted">Loading structure...</div>;
    if (error) return <div className="p-md" style={{ color: 'var(--color-error)' }}>{error}</div>;

    return (
        <div className="folder-tree">
            <div
                className={`folder-item root-item ${!selectedPath ? 'selected' : ''}`}
                onClick={() => onSelect('')}
            >
                <span className="folder-icon"><Home size={16} /></span>
                <span className="folder-name">All Folders</span>
            </div>
            {roots.map(root => (
                <FolderNode
                    key={root.path}
                    item={root}
                    level={0}
                    selectedPath={selectedPath}
                    onSelect={onSelect}
                    onContextMenu={handleContextMenu}
                    showMenu={showContextMenu}
                />
            ))}
            {!loading && roots.length === 0 && (
                <div className="p-md text-muted" style={{ fontSize: '0.8rem', fontStyle: 'italic' }}>
                    No directories found. Check your IMAGE_PATHS configuration.
                </div>
            )}

            {contextMenu && (
                <div
                    className="folder-context-menu"
                    style={{
                        position: 'fixed',
                        top: contextMenu.y,
                        left: contextMenu.x,
                        zIndex: 1000
                    }}
                    onClick={e => e.stopPropagation()}
                >
                    <div className="menu-item" onClick={() => handleAction('thumbnails')}>
                        <ImageIcon size={14} /> Update thumbnails
                    </div>
                    <div className="menu-item" onClick={() => handleAction('metadata')}>
                        <FileText size={14} /> Pull Metadata from files
                    </div>
                    <div className="menu-item" onClick={() => handleAction('scan')}>
                        <RefreshCw size={14} /> Rescan folder
                    </div>
                    <div className="menu-item" onClick={() => handleAction('astrometry')}>
                        <TelescopeIcon size={14} /> Bulk Astrometry
                    </div>
                </div>
            )}
        </div>
    );
}
