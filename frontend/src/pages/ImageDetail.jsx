import { useState, useEffect, useMemo, useRef, useCallback } from 'react';
import { useParams, Link, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import { fetchImage, updateImage, rescanImage, solveFieldOverlaps, regenerateImageThumbnail, fetchEquipment, formatBytes, formatExposure, formatRA, formatDec, formatDateTime, API_BASE_URL, getDownloadUrl } from '../api/client';
import { pixelToSky } from '../utils/wcs';
import useImageNav from '../hooks/useImageNav';
import useFieldOverlays, { OVERLAY_MODE_LABELS } from '../hooks/useFieldOverlays';
import { hitTest, containedRect } from '../utils/fieldOverlay';
import FieldOverlayLayer from '../components/fieldOverlay/FieldOverlayLayer';
import FieldOverlayPopover from '../components/fieldOverlay/FieldOverlayPopover';
import useSkyOverlay from '../hooks/useSkyOverlay';
import { hitTestSky, searchNameFor } from '../utils/skyOverlay';
import SkyOverlayLayer from '../components/skyOverlay/SkyOverlayLayer';
import SkyOverlayLegend from '../components/skyOverlay/SkyOverlayLegend';
import useSeenIn from '../hooks/useSeenIn';
import SeenInPanel from '../components/seenIn/SeenInPanel';
import { Maximize2, Layers, Crosshair, AlertTriangle, Frame } from 'lucide-react';
import StarQualityCard from '../components/quality/StarQualityCard';
import './ImageDetail.css';

export default function ImageDetail() {
    const { id } = useParams();
    const navigate = useNavigate();
    const [image, setImage] = useState(null);
    const [loading, setLoading] = useState(true);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState(null);
    const [imgError, setImgError] = useState(false);
    // Navigation through the search results this image was opened from
    const { navInfo, goToImage, goPrev, goNext, returnToSearch } = useImageNav(id);
    // Touch zoom/pan: view = {s: scale, x, y translate in px, origin top-left of the preview}
    const [view, setView] = useState({ s: 1, x: 0, y: 0 });
    const viewRef = useRef(view);
    viewRef.current = view;
    const previewRef = useRef(null);
    const pointersRef = useRef(new Map());
    const gestureRef = useRef(null);
    const lastTapRef = useRef(0);

    // Reset error when ID changes
    useEffect(() => {
        setImgError(false);
        setView({ s: 1, x: 0, y: 0 });
        setCursorPos(null);
        setHoveredOverlayId(null);
        setHoveredSkyKey(null);
        setOverlayPopover(null);
    }, [id]);

    // AstroCat annotations: catalog objects drawn from the image's own plate solution
    const [annotationsOn, setAnnotationsOn] = useState(false);

    // Crosshair State
    const [cursorPos, setCursorPos] = useState(null);
    const imageRef = useRef(null);
    // Rating Management
    const [ratingManuallyEdited, setRatingManuallyEdited] = useState(false);
    // Target editing (F2)
    const [editingTarget, setEditingTarget] = useState(false);
    const [targetInput, setTargetInput] = useState('');

    useEffect(() => {
        loadImage();
    }, [id]);

    // R0: rig list for the Equipment section's override select
    const rigsQuery = useQuery({
        queryKey: ['equipment'],
        queryFn: fetchEquipment,
        staleTime: 60 * 1000,
    });
    const rigs = rigsQuery.data?.rigs || [];

    // Images-in-field overlay: other images' footprints, hit-tested in native image pixels
    const overlays = useFieldOverlays(image);
    const seenIn = useSeenIn(image);
    const { cycle: cycleOverlays, unavailable: overlaysUnavailable } = overlays;
    const [hoveredOverlayId, setHoveredOverlayId] = useState(null);
    // Dynamic catalog overlay (AstroCat annotations), drawn from the image's own plate solution
    const sky = useSkyOverlay(image, annotationsOn);
    const [hoveredSkyKey, setHoveredSkyKey] = useState(null);
    const [overlayPopover, setOverlayPopover] = useState(null);
    const mouseDownRef = useRef(null);
    const [boxSize, setBoxSize] = useState(null);
    useEffect(() => {
        const el = previewRef.current;
        if (!el) return undefined;
        const ro = new ResizeObserver(([entry]) => {
            const { width, height } = entry.contentRect;
            setBoxSize({ w: width, h: height });
        });
        ro.observe(el);
        return () => ro.disconnect();
    }, [loading, imgError]);
    const imgRect = image && boxSize ? containedRect(boxSize.w, boxSize.h, image.width_pixels, image.height_pixels) : null;
    const overlayToScreen = (px, py) => ({
        x: imgRect.x + (px / image.width_pixels) * imgRect.w,
        y: imgRect.y + (py / image.height_pixels) * imgRect.h,
    });
    const closeOverlayPopover = useCallback(() => setOverlayPopover(null), []);

    // Client point -> native image pixels, accounting for the letterboxed (object-fit: contain) image
    const overlayHit = (clientX, clientY) => {
        if (!overlays.groups.length || !imageRef.current || !imgRect) return null;
        const rect = imageRef.current.getBoundingClientRect();
        const s = viewRef.current.s;
        const lx = (clientX - rect.left) / s;
        const ly = (clientY - rect.top) / s;
        const px = ((lx - imgRect.x) / imgRect.w) * image.width_pixels;
        const py = ((ly - imgRect.y) / imgRect.h) * image.height_pixels;
        if (px < 0 || py < 0 || px > image.width_pixels || py > image.height_pixels) return null;
        return hitTest(overlays.groups, px, py);
    };

    // Client point -> catalog object under it (point markers within a screen tolerance, else smallest ellipse)
    const skyHit = (clientX, clientY) => {
        if (!sky.objects.length || !imageRef.current || !imgRect) return null;
        const rect = imageRef.current.getBoundingClientRect();
        const s = viewRef.current.s;
        const lx = (clientX - rect.left) / s;
        const ly = (clientY - rect.top) / s;
        const px = ((lx - imgRect.x) / imgRect.w) * image.width_pixels;
        const py = ((ly - imgRect.y) / imgRect.h) * image.height_pixels;
        if (px < 0 || py < 0 || px > image.width_pixels || py > image.height_pixels) return null;
        return hitTestSky(sky.objects, px, py, image.width_pixels / (imgRect.w * s));
    };
    const openSkyObject = (obj) => navigate(`/search?object_name=${encodeURIComponent(searchNameFor(obj))}`);

    const openOverlay = (group, clientX, clientY) => {
        if (group.count > 1) setOverlayPopover({ group, x: clientX, y: clientY });
        else navigate(`/images/${group.id}`);
    };

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

    async function handleFrameTypeChange(newFrameType) {
        setSaving(true);
        try {
            const updated = await updateImage(id, { frame_type: newFrameType });
            setImage(updated);
        } catch (err) {
            console.error('Failed to update frame type:', err);
        } finally {
            setSaving(false);
        }
    }

    // R0: rig override (docs/design/P0-R0-equipment-sites.md §4.9)
    async function handleRigChange(newRigId) {
        setSaving(true);
        try {
            const updated = await updateImage(id, { rig_id: newRigId === '' ? null : Number(newRigId) });
            setImage(updated);
        } catch (err) {
            console.error('Failed to update rig:', err);
            alert('Failed to update rig: ' + err.message);
        } finally {
            setSaving(false);
        }
    }

    async function handleTargetSave() {
        setSaving(true);
        try {
            const updated = await updateImage(id, { target_key: targetInput });
            setImage(updated);
            setEditingTarget(false);
        } catch (err) {
            console.error('Failed to update target:', err);
            alert('Failed to update target: ' + err.message);
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

    async function handleSolveOverlays() {
        const n = overlays.unsolvedCount;
        const capped = overlays.truncated ? ' (the list is capped; some may be left out)' : '';
        if (!window.confirm(`Submit ${n} unsolved image${n === 1 ? '' : 's'} in view (${OVERLAY_MODE_LABELS[overlays.mode]}) for astrometry?${capped}`)) return;
        try {
            setSaving(true);
            const res = await solveFieldOverlaps(id, overlays.mode);
            alert(`Queued ${res.queued} for astrometry` + (res.skipped ? `, skipped ${res.skipped} (already submitted or solved)` : ''));
            setTimeout(() => overlays.refetch(), 5000);
        } catch (e) {
            alert('Error submitting for astrometry: ' + e.message);
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
                goPrev();
            } else if (e.key === 'ArrowRight' && navInfo.nextId) {
                e.preventDefault();
                goNext();
            } else if (e.key.toLowerCase() === 'g') {
                e.preventDefault();
                returnToSearch();
            } else if (e.key.toLowerCase() === 'f' && !e.ctrlKey && !e.metaKey && !e.altKey) {
                e.preventDefault();
                navigate(`/images/${id}/view`);
            } else if (e.key.toLowerCase() === 'o' && !e.ctrlKey && !e.metaKey && !e.altKey && !overlaysUnavailable) {
                e.preventDefault();
                cycleOverlays();
            }
        };

        window.addEventListener('keydown', handleKeyPress);
        return () => window.removeEventListener('keydown', handleKeyPress);
    }, [image, id, navInfo, navigate, goPrev, goNext, returnToSearch, overlaysUnavailable, cycleOverlays]);

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

        // The overlay sits inside the zoomed container, so its rect is already scaled.
        const rect = imageRef.current.getBoundingClientRect();
        const scale = viewRef.current.s;
        const x = (e.clientX - rect.left) / scale; // local (unscaled) px for crosshair lines
        const y = (e.clientY - rect.top) / scale;

        // Calculate image coordinates
        const imgX = ((e.clientX - rect.left) / rect.width) * image.width_pixels;
        const imgY = ((e.clientY - rect.top) / rect.height) * image.height_pixels;

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

        if (e.pointerType === 'mouse') {
            // Catalog objects take priority over the footprints they sit in
            const skyObj = skyHit(e.clientX, e.clientY);
            setHoveredSkyKey(skyObj ? skyObj.key : null);
            const hit = skyObj ? null : overlayHit(e.clientX, e.clientY);
            setHoveredOverlayId(hit ? hit.id : null);
        }

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
        setHoveredOverlayId(null);
        setHoveredSkyKey(null);
    };

    // ---- Pointer handling: mouse hover = crosshair; touch = tap to read, pinch/pan to zoom ----
    const clampView = (v) => {
        const box = previewRef.current?.getBoundingClientRect();
        const sc = Math.min(8, Math.max(1, v.s));
        if (!box) return { s: sc, x: 0, y: 0 };
        return {
            s: sc,
            x: Math.min(0, Math.max(box.width * (1 - sc), v.x)),
            y: Math.min(0, Math.max(box.height * (1 - sc), v.y)),
        };
    };

    // Zoom by factor around a screen point, keeping that point fixed
    const zoomAround = (v, factor, cx, cy) => {
        const box = previewRef.current.getBoundingClientRect();
        const px = cx - box.left;
        const py = cy - box.top;
        const ns = Math.min(8, Math.max(1, v.s * factor));
        const k = ns / v.s;
        return clampView({ s: ns, x: px - (px - v.x) * k, y: py - (py - v.y) * k });
    };

    const handlePointerDown = (e) => {
        if (e.pointerType === 'mouse') {
            mouseDownRef.current = { x: e.clientX, y: e.clientY };
            return;
        }
        e.currentTarget.setPointerCapture?.(e.pointerId);
        pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const pts = [...pointersRef.current.values()];
        gestureRef.current = {
            startView: { ...viewRef.current },
            start: pts.map((p) => ({ ...p })),
            moved: false,
            multi: pts.length > 1,
            t0: Date.now(),
        };
    };

    const handlePointerMove = (e) => {
        if (e.pointerType === 'mouse') {
            handleMouseMove(e);
            return;
        }
        if (!pointersRef.current.has(e.pointerId) || !gestureRef.current) return;
        pointersRef.current.set(e.pointerId, { x: e.clientX, y: e.clientY });
        const g = gestureRef.current;
        const pts = [...pointersRef.current.values()];
        if (pts.length >= 2 && g.start.length >= 2) {
            g.moved = true;
            g.multi = true;
            const d0 = Math.hypot(g.start[0].x - g.start[1].x, g.start[0].y - g.start[1].y) || 1;
            const d1 = Math.hypot(pts[0].x - pts[1].x, pts[0].y - pts[1].y);
            const m0 = { x: (g.start[0].x + g.start[1].x) / 2, y: (g.start[0].y + g.start[1].y) / 2 };
            const m1 = { x: (pts[0].x + pts[1].x) / 2, y: (pts[0].y + pts[1].y) / 2 };
            const zoomed = zoomAround(g.startView, d1 / d0, m0.x, m0.y);
            setView(clampView({ ...zoomed, x: zoomed.x + (m1.x - m0.x), y: zoomed.y + (m1.y - m0.y) }));
        } else if (pts.length === 1 && !g.multi) {
            const dx = pts[0].x - g.start[0].x;
            const dy = pts[0].y - g.start[0].y;
            if (Math.hypot(dx, dy) > 8) g.moved = true;
            if (g.moved && g.startView.s > 1) {
                setView(clampView({ s: g.startView.s, x: g.startView.x + dx, y: g.startView.y + dy }));
            }
        }
    };

    const handlePointerUp = (e) => {
        if (e.pointerType === 'mouse') {
            // A click (not a drag) on a catalog object searches for it; on a footprint opens it
            const down = mouseDownRef.current;
            mouseDownRef.current = null;
            if (e.type === 'pointerup' && e.button === 0 && down && Math.hypot(e.clientX - down.x, e.clientY - down.y) < 5) {
                const skyObj = skyHit(e.clientX, e.clientY);
                const hit = skyObj ? null : overlayHit(e.clientX, e.clientY);
                if (skyObj) openSkyObject(skyObj);
                else if (hit) openOverlay(hit, e.clientX, e.clientY);
            }
            return;
        }
        const g = gestureRef.current;
        pointersRef.current.delete(e.pointerId);
        if (!g) return;
        if (pointersRef.current.size === 0) {
            const wasTap = !g.moved && !g.multi && Date.now() - g.t0 < 500;
            const dx = e.clientX - g.start[0].x;
            const dy = e.clientY - g.start[0].y;
            if (wasTap) {
                const now = Date.now();
                if (now - lastTapRef.current < 300) {
                    // double-tap: reset if zoomed, else zoom in at the tap
                    setView(viewRef.current.s > 1 ? { s: 1, x: 0, y: 0 } : zoomAround(viewRef.current, 2.5, e.clientX, e.clientY));
                    lastTapRef.current = 0;
                } else {
                    lastTapRef.current = now;
                    // Tap on a catalog object highlights it (a second tap searches); on a footprint opens it
                    const skyObj = skyHit(e.clientX, e.clientY);
                    const hit = skyObj ? null : overlayHit(e.clientX, e.clientY);
                    if (skyObj && skyObj.key === hoveredSkyKey) openSkyObject(skyObj);
                    else if (skyObj) setHoveredSkyKey(skyObj.key);
                    else if (hit) openOverlay(hit, e.clientX, e.clientY);
                    else {
                        setHoveredSkyKey(null);
                        handleMouseMove(e); // tap places the crosshair + RA/Dec readout
                    }
                }
            } else if (e.type === 'pointerup' && !g.multi && g.startView.s === 1 && Math.abs(dx) > 70 && Math.abs(dy) < 50) {
                // swipe (not zoomed) navigates prev/next
                goToImage(dx < 0 ? navInfo.nextId : navInfo.prevId);
            }
            gestureRef.current = null;
        }
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
            {overlayPopover && (
                <FieldOverlayPopover
                    group={overlayPopover.group}
                    x={overlayPopover.x}
                    y={overlayPopover.y}
                    onClose={closeOverlayPopover}
                />
            )}
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
                            onClick={goPrev}
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
                            onClick={goNext}
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
                        <div className="image-preview" ref={previewRef} style={imgError ? getPlaceholderStyle() : {}}>
                            {!imgError ? (
                                <div
                                    className="preview-container"
                                    style={{
                                        position: 'relative',
                                        width: '100%',
                                        height: '100%',
                                        transformOrigin: '0 0',
                                        transform: `translate(${view.x}px, ${view.y}px) scale(${view.s})`,
                                        '--inv-zoom': 1 / view.s,
                                    }}
                                >
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
                                    {/* AstroCat annotations: catalog objects from the plate solution (visual only) */}
                                    {sky.active && sky.objects.length > 0 && imgRect && (
                                        <div style={{ position: 'absolute', inset: 0, zIndex: 8, pointerEvents: 'none' }}>
                                            <SkyOverlayLayer
                                                objects={sky.objects}
                                                toScreen={overlayToScreen}
                                                clip={imgRect}
                                                hoveredKey={hoveredSkyKey}
                                                labelScale={1 / view.s}
                                            />
                                        </div>
                                    )}

                                    {/* Images-in-field footprints (visual only; hit-tested by the interaction layer) */}
                                    {overlays.groups.length > 0 && imgRect && (
                                        <div style={{ position: 'absolute', inset: 0, zIndex: 9, pointerEvents: 'none' }}>
                                            <FieldOverlayLayer
                                                groups={overlays.groups}
                                                toScreen={overlayToScreen}
                                                clip={imgRect}
                                                hoveredId={hoveredOverlayId}
                                                labelScale={1 / view.s}
                                            />
                                        </div>
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
                                            cursor: hoveredOverlayId != null || hoveredSkyKey != null ? 'pointer' : 'crosshair',
                                            touchAction: 'none'
                                        }}
                                        ref={imageRef}
                                        onPointerDown={handlePointerDown}
                                        onPointerMove={handlePointerMove}
                                        onPointerUp={handlePointerUp}
                                        onPointerCancel={handlePointerUp}
                                        onPointerLeave={(e) => e.pointerType === 'mouse' && handleMouseLeave()}
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
                            {sky.active && !imgError && (
                                <SkyOverlayLegend
                                    className="image-sky-legend"
                                    counts={sky.counts}
                                    hidden={sky.hidden}
                                    onToggle={sky.toggleCatalog}
                                    warning={sky.warning}
                                    isLoading={sky.isLoading}
                                    isError={sky.isError}
                                />
                            )}
                        </div>

                        {view.s > 1 && (
                            <button className="btn btn-secondary btn-sm zoom-reset" onClick={() => setView({ s: 1, x: 0, y: 0 })}>
                                Reset zoom ({view.s.toFixed(1)}x)
                            </button>
                        )}

                        {/* Quick Actions */}
                        <div className="image-actions">
                            <Link to={`/images/${id}/view`} className="btn btn-secondary" title="Open at full resolution (F)">
                                <Maximize2 size={14} style={{ verticalAlign: '-2px', marginRight: 6 }} />
                                Full resolution
                            </Link>
                            <a
                                href={getDownloadUrl(id, 'jpg')}
                                className="btn btn-secondary"
                                download // Hint to browser
                            >
                                📥 Download JPG
                            </a>

                            {/* AstroCat annotations on/off */}
                            <button
                                className={`btn ${sky.active ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => setAnnotationsOn((on) => !on)}
                                disabled={!!sky.unavailable}
                                title={sky.unavailable
                                    || (sky.active && sky.warning
                                        ? `Annotations — approximate: ${sky.warning}`
                                        : 'Annotations: catalog objects from the plate solution')}
                            >
                                ✨ Annotations: {sky.active ? 'On' : 'Off'}
                                {sky.active && sky.warning && <AlertTriangle size={14} className="annotation-warning-icon" />}
                            </button>

                            <button
                                className={`btn ${overlays.active ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={overlays.cycle}
                                disabled={!!overlays.unavailable}
                                title={overlays.unavailable || `Images in this field: ${OVERLAY_MODE_LABELS[overlays.mode]} — click to cycle Off / Masters / Subs / All (O)`}
                            >
                                <Layers size={14} style={{ verticalAlign: '-2px', marginRight: 6 }} />
                                In field: {overlays.unavailable ? 'Off' : OVERLAY_MODE_LABELS[overlays.mode]}
                                {overlays.active && (
                                    overlays.isLoading ? ' …' : ` (${overlays.groups.length}${overlays.truncated ? '+' : ''})`
                                )}
                            </button>

                            <button
                                className={`btn ${seenIn.open ? 'btn-primary' : 'btn-secondary'}`}
                                onClick={() => seenIn.setOpen(true)}
                                disabled={!!seenIn.unavailable}
                                title={seenIn.unavailable || 'Larger images whose field covers this image'}
                            >
                                <Frame size={14} style={{ verticalAlign: '-2px', marginRight: 6 }} />
                                Seen in{seenIn.loaded ? ` (${seenIn.groups.length}${seenIn.truncated ? '+' : ''})` : ''}
                            </button>

                            {overlays.active && overlays.unsolvedCount > 0 && (
                                <button
                                    className="btn btn-secondary"
                                    onClick={handleSolveOverlays}
                                    disabled={saving}
                                    title="Submit the dashed-circle footprints currently shown (no rotation yet) for astrometry"
                                >
                                    <Crosshair size={14} style={{ verticalAlign: '-2px', marginRight: 6 }} />
                                    Solve {overlays.unsolvedCount} unsolved
                                </button>
                            )}

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

                    {seenIn.open && <SeenInPanel seenIn={seenIn} onClose={() => seenIn.setOpen(false)} />}

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
                                {['PLANETARY', 'ALLSKY', 'AURORA'].includes(image.subtype) ? (
                                    <p className="text-muted text-sm">Plate solving disabled for {({ ALLSKY: 'all-sky', AURORA: 'aurora' })[image.subtype] || 'planetary'} images.</p>
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
                                {image.is_plate_solved && image.subtype !== 'PLANETARY' && image.subtype !== 'ALLSKY' && image.subtype !== 'AURORA' && (
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
                                <option value="ALLSKY">All-sky</option>
                                <option value="AURORA">Aurora</option>
                            </select>
                        </div>

                        {/* Frame Type Selector (F1) */}
                        <div className="subtype-selector">
                            <label className="label">
                                Frame Type
                                {image.frame_type_source && (
                                    <span className="text-xs text-muted" style={{ marginLeft: '0.5rem', fontWeight: 'normal' }}>
                                        {image.frame_type_source === 'MANUAL'
                                            ? '(manual)'
                                            : `(auto: ${image.frame_type_source.toLowerCase()})`}
                                    </span>
                                )}
                            </label>
                            <select
                                className="input select"
                                value={image.frame_type || 'LIGHT'}
                                onChange={(e) => handleFrameTypeChange(e.target.value)}
                                disabled={saving}
                            >
                                <option value="LIGHT">Light</option>
                                <option value="DARK">Dark</option>
                                <option value="FLAT">Flat</option>
                                <option value="BIAS">Bias</option>
                                <option value="DARK_FLAT">Dark Flat</option>
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
                            <div className="metadata-item">
                                <dt>Target</dt>
                                <dd>
                                    {editingTarget ? (
                                        <span style={{ display: 'flex', gap: '0.4rem', alignItems: 'center' }}>
                                            <input
                                                type="text"
                                                className="input"
                                                style={{ maxWidth: '180px' }}
                                                value={targetInput}
                                                onChange={(e) => setTargetInput(e.target.value)}
                                                placeholder="e.g. M31 (empty clears)"
                                                autoFocus
                                            />
                                            <button className="btn btn-primary btn-sm" onClick={handleTargetSave} disabled={saving}>Save</button>
                                            <button className="btn btn-secondary btn-sm" onClick={() => setEditingTarget(false)} disabled={saving}>Cancel</button>
                                        </span>
                                    ) : (
                                        <span style={{ display: 'flex', gap: '0.5rem', alignItems: 'center' }}>
                                            {image.target_key ? (
                                                <Link to={`/targets/${encodeURIComponent(image.target_key)}`}>{image.target_key}</Link>
                                            ) : (
                                                <span className="text-muted">Unassigned</span>
                                            )}
                                            {image.target_source && image.target_source !== 'NONE' && (
                                                <span className="text-muted text-xs">
                                                    ({image.target_source === 'MANUAL' ? 'manual' : `auto: ${image.target_source.toLowerCase()}`})
                                                </span>
                                            )}
                                            <button
                                                className="btn btn-ghost btn-sm"
                                                onClick={() => { setTargetInput(image.target_key || ''); setEditingTarget(true); }}
                                                title="Edit target"
                                            >
                                                ✏️
                                            </button>
                                        </span>
                                    )}
                                </dd>
                            </div>
                        </dl>
                    </section>

                    {/* Plate Solve Data */}
                    {image.is_plate_solved && image.subtype !== 'PLANETARY' && image.subtype !== 'ALLSKY' && image.subtype !== 'AURORA' && (
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

                    {/* Star quality (Q1): Light subs and masters only */}
                    {image.frame_type === 'LIGHT' && ['SUB_FRAME', 'INTEGRATION_MASTER'].includes(image.subtype) && (
                        <StarQualityCard image={image} onImageUpdated={setImage} />
                    )}

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
                            {/* R0: rig, with an inline override select (docs/design/P0-R0-equipment-sites.md §4.9) */}
                            <div className="metadata-item">
                                <dt>Rig</dt>
                                <dd>
                                    <span style={{ display: 'flex', gap: '0.5rem', alignItems: 'center', flexWrap: 'wrap' }}>
                                        {image.rig_name ? (
                                            <span>
                                                {image.rig_name}
                                                <span className="text-muted text-xs" style={{ marginLeft: '0.4rem' }}>
                                                    ({image.rig_source === 'MANUAL' ? 'manual' : 'auto'})
                                                </span>
                                            </span>
                                        ) : (
                                            <span className="text-muted">Unassigned</span>
                                        )}
                                        <select
                                            className="input"
                                            style={{ maxWidth: '200px' }}
                                            value={image.rig_id ?? ''}
                                            onChange={(e) => handleRigChange(e.target.value)}
                                            disabled={saving}
                                        >
                                            <option value="">None / auto</option>
                                            {rigs.map((r) => (
                                                <option key={r.id} value={r.id}>{r.name}</option>
                                            ))}
                                        </select>
                                    </span>
                                </dd>
                            </div>
                        </dl>
                    </section>




                </div>
            </div>
        </div>
    );
}
