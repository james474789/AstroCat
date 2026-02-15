import { useState, useEffect, useMemo, useRef } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { fetchImage, updateImage, rescanImage, fetchAnnotation, regenerateImageThumbnail, formatBytes, formatExposure, formatRA, formatDec, formatDateTime, API_BASE_URL, getDownloadUrl } from '../api/client';
import { pixelToSky } from '../utils/wcs';
import './ImageDetail.css';

export default function ImageDetail() {
    const { id } = useParams();
    const navigate = useNavigate();
    const [image, setImage] = useState(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    const [imgError, setImgError] = useState(false);
    const [annotatedImageError, setAnnotatedImageError] = useState(false);
    // Navigation state
    const [navInfo, setNavInfo] = useState({ prevId: null, nextId: null, currentIndex: -1, total: 0 });
    // Cache buster timestamp
    const [pageLoadTimestamp] = useState(Date.now());

    // Reset error when ID changes
    useEffect(() => {
        setImgError(false);
        setAnnotatedImageError(false);
    }, [id]);

    // Annotations Toggle: 0=None, 1=Nova (Image Overlay)
    const [annotationMode, setAnnotationMode] = useState(0);

    // Crosshair State
    const [cursorPos, setCursorPos] = useState(null);
    const imageRef = useRef(null);
    // Rating Management
    const [ratingManuallyEdited, setRatingManuallyEdited] = useState(false);

    useEffect(() => {
        loadImage();
    }, [id]);

    async function loadImage() {
        try {
            setLoading(true);
            setImgError(false);
            const data = await fetchImage(id);
            setImage(data);
            // Initialize states
            setRatingManuallyEdited(data.rating_manually_edited || false);
        } catch (err) {
            setError('Image not found');
        } finally {
            setLoading(false);
        }
    }

    // Load search context for navigation
    useEffect(() => {
        const contextStr = sessionStorage.getItem('currentSearchContext');
        if (contextStr) {
            try {
                const context = JSON.parse(contextStr);
                const currentIdNum = parseInt(id);
                const index = context.ids.indexOf(currentIdNum);

                if (index !== -1) {
                    setNavInfo({
                        prevId: index > 0 ? context.ids[index - 1] : null,
                        nextId: index < context.ids.length - 1 ? context.ids[index + 1] : null,
                        currentIndex: (context.page - 1) * context.pageSize + index + 1,
                        total: context.total
                    });
                } else {
                    setNavInfo({ prevId: null, nextId: null, currentIndex: -1, total: 0 });
                }
            } catch (e) {
                console.error("Failed to parse search context", e);
            }
        }
    }, [id]);

    async function handleSubtypeChange(newSubtype) {
        setSaving(true);
        try {
            const updated = await updateImage(id, { subtype: newSubtype });
            setImage(updated);
        } catch (err) {
            console.error('Failed to update:', err);
        } finally {
            setSaving(false);
        }
    }

    async function handleRatingChange(newRating) {
        setSaving(true);
        try {
            const updated = await updateImage(id, { rating: newRating, rating_manually_edited: true });
            setImage(updated);
            setRatingManuallyEdited(true);
        } catch (err) {
            console.error('Failed to update rating:', err);
        } finally {
            setSaving(false);
        }
    }

    async function handleRescan() {
        try {
            setSaving(true);
            const response = await rescanImage(id);
            // Immediate update from response
            if (response.submission_id) {
                setImage(prev => ({
                    ...prev,
                    astrometry_status: 'SUBMITTED',
                    astrometry_submission_id: response.submission_id
                }));
            } else {
                // Fallback if no ID returned (shouldn't happen with new backend)
                loadImage();
            }
        } catch (e) {
            alert("Error starting rescan: " + e.message);
        } finally {
            setSaving(false);
        }
    }

    async function handleFetchAnnotation() {
        try {
            setSaving(true);
            await fetchAnnotation(id);
            // Reload image to update status
            const updated = await fetchImage(id);
            setImage(updated);
            setAnnotatedImageError(false);
            // We might need to refresh the page or update the timestamp to force reload the image
            window.location.reload();
        } catch (e) {
            alert("Error fetching annotation: " + e.message);
        } finally {
            setSaving(false);
        }
    }

    async function handleRegenerateThumbnail() {
        try {
            setSaving(true);
            await regenerateImageThumbnail(id);
            alert("Thumbnail regeneration queued. It may take a few seconds to update.");
            // Reload after short delay
            setTimeout(() => loadImage(), 3000);
        } catch (e) {
            alert("Error regenerating thumbnail: " + e.message);
        } finally {
            setSaving(false);
        }
    }

    // Polling for Astrometry Status
    useEffect(() => {
        let interval;
        if (image && ['SUBMITTED', 'PROCESSING'].includes(image.astrometry_status)) {
            interval = setInterval(() => {
                loadImage();
            }, 5000);
        }
        return () => clearInterval(interval);
    }, [image?.astrometry_status, id]);

    // Auto-refresh when solved (User Request)
    const prevStatusRef = useRef();
    useEffect(() => {
        const prev = prevStatusRef.current;
        const current = image?.astrometry_status;

        // If we transitioned from specific pending states to SOLVED, reload to show annotated image
        if (prev && ['SUBMITTED', 'PROCESSING'].includes(prev) && current === 'SOLVED') {
            window.location.reload();
        }

        if (current) {
            prevStatusRef.current = current;
        }
    }, [image?.astrometry_status]);

    // Keyboard shortcut for rating (0-5 keys) and navigation (arrows)
    useEffect(() => {
        const handleKeyPress = (e) => {
            // Ignore if in an input field
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'SELECT' || e.target.tagName === 'TEXTAREA') return;

            if (!image) return;
            const key = parseInt(e.key);
            if (key >= 0 && key <= 5) {
                e.preventDefault();
                handleRatingChange(key);
            }

            // Navigation
            if (e.key === 'ArrowLeft' && navInfo.prevId) {
                e.preventDefault();
                sessionStorage.setItem('lastClickedImageId', navInfo.prevId);
                navigate(`/images/${navInfo.prevId}`);
            } else if (e.key === 'ArrowRight' && navInfo.nextId) {
                e.preventDefault();
                sessionStorage.setItem('lastClickedImageId', navInfo.nextId);
                navigate(`/images/${navInfo.nextId}`);
            } else if (e.key.toLowerCase() === 'g') {
                e.preventDefault();
                sessionStorage.setItem('lastClickedImageId', id);

                // RESTORE SEARCH CONTEXT
                // Try to reconstruct the search URL from the stored context
                const contextStr = sessionStorage.getItem('currentSearchContext');
                if (contextStr) {
                    try {
                        const context = JSON.parse(contextStr);
                        if (context.params) {
                            const searchParams = new URLSearchParams();
                            Object.entries(context.params).forEach(([key, value]) => {
                                // Exclude internal or default parameters that shouldn't clutter the URL
                                if (key !== 'page_size' && value !== undefined && value !== null && value !== '') {
                                    searchParams.set(key, value);
                                }
                            });
                            navigate(`/search?${searchParams.toString()}`);
                            return;
                        }
                    } catch (e) {
                        console.error("Failed to parse search context for return", e);
                    }
                }

                navigate('/search');
            }
        };

        window.addEventListener('keydown', handleKeyPress);
        return () => window.removeEventListener('keydown', handleKeyPress);
    }, [image, id, navInfo, navigate]);

    // Generate placeholder background
    const getPlaceholderStyle = () => {
        const hue1 = (parseInt(id) * 37) % 360;
        const hue2 = (hue1 + 40) % 360;
        return {
            background: `linear-gradient(135deg, hsl(${hue1}, 50%, 15%) 0%, hsl(${hue2}, 60%, 8%) 100%)`,
        };
    };



    // Mouse handlers for crosshair
    const handleMouseMove = (e) => {
        if (!imageRef.current || !image) return;

        const rect = imageRef.current.getBoundingClientRect();
        const x = e.clientX - rect.left;
        const y = e.clientY - rect.top;

        // Calculate image coordinates
        const imgX = (x / rect.width) * image.width_pixels;
        const imgY = (y / rect.height) * image.height_pixels;

        // Calculate RA/Dec
        const sky = pixelToSky(
            imgX,
            imgY,
            image.width_pixels,
            image.height_pixels,
            image.ra_center_degrees,
            image.dec_center_degrees,
            image.pixel_scale_arcsec,
            image.rotation_degrees,
            image.raw_header?.astrometry_parity || 1
        );

        setCursorPos({
            x, // Screen/Div relative for crosshair lines
            y,
            imgX, // Image relative for label
            imgY,
            ra: sky?.ra,
            dec: sky?.dec
        });
    };

    const handleMouseLeave = () => {
        setCursorPos(null);
    };

    if (loading) {
        return (
            <div className="image-detail-loading">
                <div className="spinner" />
                <p>Loading image details...</p>
            </div>
        );
    }

    if (error) {
        return (
            <div className="image-detail-error">
                <h2>Image Not Found</h2>
                <p>{error}</p>
                <button className="btn btn-primary" onClick={() => navigate('/search')}>
                    Back to Search
                </button>
            </div>
        );
    }

    return (
        <div className="image-detail">
            {/* Breadcrumb & Navigation */}
            <nav className="breadcrumb">
                <div className="breadcrumb-left">
                    <Link to="/search">Images</Link>
                    <span>/</span>
                    <span>{image.file_name}</span>
                </div>
                {navInfo.currentIndex !== -1 && (
                    <div className="image-navigation">
                        <button
                            className="nav-btn"
                            onClick={() => {
                                if (navInfo.prevId) {
                                    sessionStorage.setItem('lastClickedImageId', navInfo.prevId);
                                    navigate(`/images/${navInfo.prevId}`);
                                }
                            }}
                            disabled={!navInfo.prevId}
                            title="Previous Image (Left Arrow)"
                        >
                            &larr;
                        </button>
                        <span className="nav-index">
                            Image {navInfo.currentIndex} of {navInfo.total}
                        </span>
                        <button
                            className="nav-btn"
                            onClick={() => {
                                if (navInfo.nextId) {
                                    sessionStorage.setItem('lastClickedImageId', navInfo.nextId);
                                    navigate(`/images/${navInfo.nextId}`);
                                }
                            }}
                            disabled={!navInfo.nextId}
                            title="Next Image (Right Arrow)"
                        >
                            &rarr;
                        </button>
                    </div>
                )}
            </nav>

            <div className="image-detail-layout">
                <div className="image-main-content">
                    {/* Image Preview */}
                    <div className="image-preview-section">
                        <div className="image-preview" style={imgError ? getPlaceholderStyle() : {}}>
                            {!imgError ? (
                                <div className="preview-container" style={{ position: 'relative', width: '100%', height: '100%' }}>
                                    {/* 
                                     Hierarchy:
                                     1. Stretched Overlay (Top Priority if enabled)
                                     2. Annotated Overlay (If enabled)
                                     3. Base Image (Always there)
                                    */}

                                    {/* Base Image (Always Thumbnail - Linear/Default) */}
                                    <img
                                        src={`${API_BASE_URL}/images/${id}/thumbnail?t=${image.thumbnail_generated_at ? new Date(image.thumbnail_generated_at).getTime() : ''}`}
                                        alt={image.file_name}
                                        className="real-preview-image base-layer"
                                        style={{
                                            width: '100%',
                                            height: '100%',
                                            objectFit: 'contain',
                                            position: 'absolute',
                                            top: 0,
                                            left: 0,
                                            zIndex: 1
                                        }}
                                        onError={() => setImgError(true)}
                                    />
                                    {/* Annotated Overlay Image (Nova Mode) */}
                                    {annotationMode === 1 && image.is_plate_solved && !annotatedImageError && (
                                        <img
                                            src={`${API_BASE_URL}/images/${id}/annotated?t=${pageLoadTimestamp}`}
                                            alt="Annotated Overlay"
                                            className="real-preview-image annotation-layer-img"
                                            style={{
                                                width: '100%',
                                                height: '100%',
                                                objectFit: 'contain',
                                                position: 'absolute',
                                                top: 0,
                                                left: 0,
                                                zIndex: 3 // Above base layer
                                            }}
                                            onError={(e) => {
                                                e.target.style.display = 'none';
                                                setAnnotatedImageError(true);
                                            }}
                                        />
                                    )}

                                    {/* Transparent interactive layer for crosshair (needs to be on top) */}
                                    <div
                                        style={{
                                            position: 'absolute',
                                            top: 0,
                                            left: 0,
                                            width: '100%',
                                            height: '100%',
                                            zIndex: 10,
                                            cursor: 'crosshair'
                                        }}
                                        ref={imageRef}
                                        onMouseMove={handleMouseMove}
                                        onMouseLeave={handleMouseLeave}
                                    />

                                    {cursorPos && (
                                        <>
                                            <div className="crosshair-line horizontal" style={{ top: `${cursorPos.y}px`, zIndex: 11 }} />
                                            <div className="crosshair-line vertical" style={{ left: `${cursorPos.x}px`, zIndex: 11 }} />
                                            <div
                                                className="crosshair-label"
                                                style={{
                                                    top: `${cursorPos.y}px`,
                                                    left: `${cursorPos.x}px`,
                                                    zIndex: 12
                                                }}
                                            >
                                                X: {Math.round(cursorPos.imgX)} Y: {Math.round(cursorPos.imgY)}
                                                {cursorPos.ra !== undefined && (
                                                    <div style={{ fontSize: '0.8em', marginTop: '4px', color: '#ccc' }}>
                                                        {formatRA(cursorPos.ra)}<br />
                                                        {formatDec(cursorPos.dec)}
                                                    </div>
                                                )}
                                            </div>
                                        </>
                                    )}

                                </div>
                            ) : (
                                <div className="preview-placeholder">
                                    <span className="preview-icon">🌌</span>
                                    <span className="preview-text">Preview Not Available</span>
                                </div>
                            )}
                        </div>

                        {/* Quick Actions */}
                        <div className="image-actions">
                            <a
                                href={getDownloadUrl(id, 'jpg')}
                                className="btn btn-secondary"
                                download // Hint to browser
                            >
                                📥 Download JPG
                            </a>

                            <button
                                className={`btn ${annotationMode !== 0 ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setAnnotationMode(annotationMode === 0 ? 1 : 0)}
                                title="Toggle Nova-astrometry Annotations"
                            >
                                {annotationMode === 0 ? '🚫 No Annotations' : '🔭 Nova Annotations'}
                            </button>

                            <button
                                className="btn btn-secondary"
                                onClick={handleRegenerateThumbnail}
                                disabled={saving}
                                title="Force backend to regenerate the linear thumbnail"
                            >
                                🔄 Regenerate
                            </button>
                            <button
                                className="btn btn-secondary"
                                onClick={() => navigate(`/images/${id}/metadata`)}
                            >
                                📋 View Metadata
                            </button>
                        </div>
                    </div>

                    {/* Secondary Layout Section (Below Image) */}
                    <div className="image-secondary-section">
                        {/* Left Column: Objects in Field */}
                        <div className="secondary-panel">
                            <div className="panel-header">
                                <h3 className="section-title">Objects in Field</h3>
                            </div>
                            <div className="panel-content">
                                {image.catalog_matches && image.catalog_matches.length > 0 ? (
                                    <div className="matched-objects">
                                        {image.catalog_matches.map((match, idx) => (
                                            <Link
                                                key={idx}
                                                to={`/search?object_name=${encodeURIComponent(match.catalog_designation || match.designation)}`}
                                                className="matched-object-tag"
                                            >
                                                <span className="object-designation">
                                                    {match.catalog_designation || match.designation}
                                                </span>
                                                {match.ra_degrees != null && match.dec_degrees != null && (
                                                    <span className="object-coords">
                                                        {formatRA(match.ra_degrees)} {formatDec(match.dec_degrees)}
                                                    </span>
                                                )}
                                                {/* Name might not be available in API yet */}
                                                {(match.name || match.common_name) && (
                                                    <span className="object-name">{match.name || match.common_name}</span>
                                                )}
                                            </Link>
                                        ))}
                                    </div>
                                ) : (
                                    <p className="text-muted text-sm">No objects identified yet.</p>
                                )}
                            </div>
                        </div>

                        {/* Right Column: Plate Solving */}
                        <div className="secondary-panel">
                            <div className="panel-header">
                                <h3 className="section-title">
                                    Plate Solving ({image.plate_solve_provider === 'LOCAL' ? 'Local' : (image.plate_solve_source === 'HEADER' || image.plate_solve_source === 'SIDECAR' ? 'Imported' : 'Web')})
                                </h3>
                            </div>
                            <div className="panel-content">
                                {image.subtype === 'PLANETARY' ? (
                                    <p className="text-muted text-sm">Plate solving disabled for planetary images.</p>
                                ) : (
                                    <div className="astrometry-panel">
                                        {/* Status Display */}
                                        <div className="astrometry-status-row">
                                            <div className="status-label">Source:</div>
                                            <div className="status-value text-muted" style={{ fontWeight: 'normal', marginRight: 'auto', marginLeft: '0.5rem' }}>
                                                {image.plate_solve_source === 'HEADER' ? 'File Header' : (image.plate_solve_source === 'SIDECAR' ? 'Sidecar File' : (image.plate_solve_provider === 'LOCAL' ? 'Local Server' : 'Nova Web'))}
                                            </div>
                                        </div>
                                        <div className="astrometry-status-row">
                                            <div className="status-label">Status:</div>
                                            <div className={`status-value ${image.astrometry_status === 'SOLVED' ? 'text-success' : image.astrometry_status === 'FAILED' ? 'text-error' : 'text-warning'}`}>
                                                {image.astrometry_status}
                                            </div>
                                        </div>

                                        {/* Details (IDs) */}
                                        {image.astrometry_submission_id && (
                                            <div className="astrometry-details text-xs text-slate-400 mt-1">
                                                <div>Sub ID: {image.astrometry_submission_id}</div>
                                                {image.astrometry_job_id && <div>Job ID: {image.astrometry_job_id}</div>}
                                            </div>
                                        )}

                                        {/* Actions */}
                                        <div className="astrometry-actions mt-3 flex gap-2">
                                            <button
                                                className="btn btn-primary btn-sm"
                                                onClick={handleRescan}
                                                disabled={['SUBMITTED', 'PROCESSING'].includes(image.astrometry_status) || saving}
                                            >
                                                {['SUBMITTED', 'PROCESSING'].includes(image.astrometry_status) ? '⏳ Processing...' : '🔭 Start Rescan'}
                                            </button>

                                            {image.astrometry_url && (
                                                <a
                                                    href={image.astrometry_url}
                                                    target="_blank"
                                                    rel="noopener noreferrer"
                                                    className="btn btn-secondary btn-sm"
                                                >
                                                    🚀 View Results
                                                </a>
                                            )}

                                            {image.astrometry_status === 'SOLVED' && !image.has_annotated_image && (
                                                <button
                                                    className="btn btn-primary btn-sm"
                                                    onClick={handleFetchAnnotation}
                                                    disabled={saving}
                                                >
                                                    🎨 Fetch Annotation
                                                </button>
                                            )}
                                        </div>
                                    </div>
                                )}
                            </div>
                        </div>
                    </div>
                </div>

                {/* Metadata Panel */}
                <div className="metadata-panel">
                    <div className="metadata-header">
                        <div className="title-group">
                            <div className="title-left">
                                <h1 className="image-title">{image.file_name}</h1>
                                {image.is_plate_solved && image.subtype !== 'PLANETARY' && (
                                    <span className="badge badge-success">
                                        {['HEADER', 'SIDECAR'].includes(image.plate_solve_source) ? 'Solve Imported' : 'Img Solved'}
                                    </span>
                                )}
                            </div>
                            <div className="image-rating">
                                <span className="rating-label">Rating:</span>
                                <span className="rating-stars">
                                    <span
                                        className="rating-star clear-rating"
                                        onClick={() => handleRatingChange(0)}
                                        style={{ cursor: 'pointer', opacity: image.rating ? 0.6 : 1 }}
                                        title="Clear rating (or press 0)"
                                    >
                                        ✕
                                    </span>
                                    {[...Array(5)].map((_, i) => (
                                        <span
                                            key={i}
                                            className={i < (image.rating || 0) ? 'rating-star filled' : 'rating-star'}
                                            onClick={() => handleRatingChange(i + 1)}
                                            style={{ cursor: 'pointer' }}
                                            title={`Rate ${i + 1}/5 (or press ${i + 1})`}
                                        >
                                            {i < (image.rating || 0) ? '★' : '☆'}
                                        </span>
                                    ))}
                                </span>
                                <span
                                    className="rating-value"
                                    onClick={() => handleRatingChange(0)}
                                    style={{ cursor: 'pointer' }}
                                    title="Click to clear rating (or press 0)"
                                >
                                    ({image.rating || 0}/5)
                                </span>
                                {ratingManuallyEdited && (
                                    <span className="badge badge-info" style={{ marginLeft: '0.5rem' }} title="Rating was manually edited">✏️ Edited</span>
                                )}
                            </div>
                        </div>

                        {/* Subtype Selector */}
                        <div className="subtype-selector">
                            <label className="label">Classification</label>
                            <select
                                className="input select"
                                value={image.subtype || ''}
                                onChange={(e) => handleSubtypeChange(e.target.value || null)}
                                disabled={saving}
                            >
                                <option value="">Unclassified</option>
                                <option value="SUB_FRAME">Sub Frame</option>
                                <option value="INTEGRATION_MASTER">Integration Master</option>
                                <option value="INTEGRATION_DEPRECATED">Deprecated</option>
                                <option value="PLANETARY">Planetary</option>
                            </select>
                        </div>
                    </div>

                    {/* File Info */}
                    <section className="metadata-section">
                        <h3 className="section-title">File Information</h3>
                        <dl className="metadata-grid">
                            <div className="metadata-item">
                                <dt>Path</dt>
                                <dd className="font-mono text-sm">{image.file_path}</dd>
                            </div>
                            <div className="metadata-item">
                                <dt>Format</dt>
                                <dd>{image.file_format}</dd>
                            </div>
                            <div className="metadata-item">
                                <dt>Size</dt>
                                <dd>{formatBytes(image.file_size_bytes)}</dd>
                            </div>
                            <div className="metadata-item">
                                <dt>Dimensions</dt>
                                <dd>
                                    {image.width_pixels && image.height_pixels
                                        ? `${image.width_pixels} × ${image.height_pixels} px`
                                        : 'Unknown'}
                                </dd>
                            </div>
                            <div className="metadata-item">
                                <dt>Created</dt>
                                <dd>{image.file_created ? formatDateTime(image.file_created) : 'Unknown'}</dd>
                            </div>
                            <div className="metadata-item">
                                <dt>Modified</dt>
                                <dd>{image.file_last_modified ? formatDateTime(image.file_last_modified) : 'Unknown'}</dd>
                            </div>
                            <div className="metadata-item">
                                <dt>Indexed</dt>
                                <dd>{formatDateTime(image.indexed_at)}</dd>
                            </div>
                        </dl>
                    </section>

                    {/* Plate Solve Data */}
                    {image.is_plate_solved && image.subtype !== 'PLANETARY' && (
                        <section className="metadata-section">
                            <h3 className="section-title">
                                <span className="badge badge-success">
                                    {['HEADER', 'SIDECAR'].includes(image.plate_solve_source) ? '✓ Solve Imported' : '✓ Plate Solved'}
                                </span>
                            </h3>
                            <dl className="metadata-grid">
                                <div className="metadata-item">
                                    <dt>Right Ascension</dt>
                                    <dd className="font-mono">{formatRA(image.ra_center_degrees)}</dd>
                                </div>
                                <div className="metadata-item">
                                    <dt>Declination</dt>
                                    <dd className="font-mono">{formatDec(image.dec_center_degrees)}</dd>
                                </div>
                                <div className="metadata-item">
                                    <dt>Field of View</dt>
                                    <dd>
                                        {image.width_pixels && image.height_pixels && image.pixel_scale_arcsec ? (
                                            <>
                                                {((image.width_pixels * image.pixel_scale_arcsec) / 3600).toFixed(2)}° ×
                                                {((image.height_pixels * image.pixel_scale_arcsec) / 3600).toFixed(2)}°
                                            </>
                                        ) : (
                                            <>{(image.field_radius_degrees * 2).toFixed(2)}° (Diameter)</>
                                        )}
                                    </dd>
                                </div>
                                <div className="metadata-item">
                                    <dt>Rotation</dt>
                                    <dd>{image.rotation_degrees?.toFixed(1)}°</dd>
                                </div>
                                <div className="metadata-item">
                                    <dt>Pixel Scale</dt>
                                    <dd>{image.pixel_scale_arcsec?.toFixed(2)} arcsec/px</dd>
                                </div>
                            </dl>
                        </section>
                    )}

                    {/* Exposure Data */}
                    <section className="metadata-section">
                        <h3 className="section-title">Exposure Data</h3>
                        <dl className="metadata-grid">
                            <div className="metadata-item">
                                <dt>Exposure Time</dt>
                                <dd className="exposure-value">{formatExposure(image.exposure_time_seconds || 0)}</dd>
                            </div>
                            <div className="metadata-item">
                                <dt>Capture Date</dt>
                                <dd>{image.capture_date ? formatDateTime(image.capture_date) : 'Unknown'}</dd>
                            </div>
                            {image.gain && (
                                <div className="metadata-item">
                                    <dt>Gain</dt>
                                    <dd>{image.gain}</dd>
                                </div>
                            )}
                            {image.iso_speed && (
                                <div className="metadata-item">
                                    <dt>ISO</dt>
                                    <dd>{image.iso_speed}</dd>
                                </div>
                            )}
                            {image.temperature_celsius && (
                                <div className="metadata-item">
                                    <dt>Sensor Temp</dt>
                                    <dd>{image.temperature_celsius}°C</dd>
                                </div>
                            )}
                            {image.filter_name && (
                                <div className="metadata-item">
                                    <dt>Filter</dt>
                                    <dd>{image.filter_name}</dd>
                                </div>
                            )}
                        </dl>
                    </section>

                    {/* Equipment */}
                    <section className="metadata-section">
                        <h3 className="section-title">Equipment</h3>
                        <dl className="metadata-grid">
                            <div className="metadata-item">
                                <dt>Camera</dt>
                                <dd>{image.camera_name || 'Unknown'}</dd>
                            </div>
                            <div className="metadata-item">
                                <dt>Telescope/Lens</dt>
                                <dd>{image.telescope_name || 'Unknown'}</dd>
                            </div>
                        </dl>
                    </section>




                </div>
            </div>
        </div>
    );
}
