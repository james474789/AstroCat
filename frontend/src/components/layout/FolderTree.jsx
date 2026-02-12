import { useState, useEffect, useRef } from 'react';
import { fetchDirectoryListing, triggerBulkThumbnails, triggerBulkMetadata, triggerMountRescan } from '../../api/client';
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

function FolderNode({ item, level, selectedPath, onSelect, onContextMenu }) {
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
                            {loading ? '⏳' : (isExpanded ? '▼' : '▶')}
                        </span>
                    ) : <span className="toggle-spacer"></span>}
                </div>
                <span className="folder-icon">{isExpanded ? '📂' : '📁'}</span>
                <span className="folder-name">{item.name}</span>
                {item.image_count > 0 && (
                    <span className="folder-count">
                        {item.image_count.toLocaleString()}
                    </span>
                )}
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

export default function FolderTree({ selectedPath, onSelect }) {
    const [roots, setRoots] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);
    const [contextMenu, setContextMenu] = useState(null); // { x, y, path }

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
            } else if (action === 'astrometry') {
                await triggerMountRescan(path, true); // true to force rescan
            }
        } catch (err) {
            console.error(`Failed to trigger ${action}:`, err);
            alert(`Failed: ${err.message}`);
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
                <span className="folder-icon">🏠</span>
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
                        🖼️ Update thumbnails
                    </div>
                    <div className="menu-item" onClick={() => handleAction('metadata')}>
                        📄 Pull Metadata from files
                    </div>
                    <div className="menu-item" onClick={() => handleAction('astrometry')}>
                        🔭 Bulk Astrometry
                    </div>
                </div>
            )}
        </div>
    );
}
