import { useState, useEffect, useRef, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import OpenSeadragon from 'openseadragon';
import { ArrowLeft, ChevronLeft, ChevronRight, Maximize, Minimize, ScanSearch, Frame, Loader2 } from 'lucide-react';
import {
    fetchImage, fetchFullRes, getFullResDziUrl, getFullResSourceUrl,
    API_BASE_URL, formatRA, formatDec,
} from '../api/client';
import { pixelToSky } from '../utils/wcs';
import useImageNav from '../hooks/useImageNav';
import './FullResViewer.css';

const PRESET_LABELS = {
    linear: 'Linear',
    auto: 'Auto-STF',
    strong: 'Strong STF',
    unlinked: 'Unlinked colour',
};
const STATE_LABELS = {
    queued: 'Queued',
    loading: 'Loading',
    debayering: 'Debayering',
    stretching: 'Stretching',
    tiling: 'Tiling',
};
const POLL_FAST_MS = 1000;
const POLL_SLOW_MS = 3000;
const POLL_BACKOFF_AFTER_MS = 30000;
const PREFETCH_MAX_PIXELS = 60e6;
const BAR_HIDE_MS = 2000;
const KEY_ZOOM = 1.4;
const KEY_PAN = 0.15;

const viewerRoute = (imageId) => `/images/${imageId}/view`;
const sleep = (ms, signal) => new Promise((resolve) => {
    const t = setTimeout(resolve, ms);
    signal?.addEventListener('abort', () => { clearTimeout(t); resolve(); }, { once: true });
});

export default function FullResViewer() {
    const { id } = useParams();
    const navigate = useNavigate();
    const { navInfo, goPrev, goNext, returnToSearch } = useImageNav(id, viewerRoute);

    const stageRef = useRef(null);
    const osdRef = useRef(null);
    const viewerRef = useRef(null);
    const hideTimerRef = useRef(null);
    const barHoverRef = useRef(false);
    const openedIdRef = useRef(null);
    // Last viewport + image size shown, so same-size neighbours keep the view (blinking between subs)
    const lastViewRef = useRef(null);
    const swipeRef = useRef(null);
    const curDimsRef = useRef(null);       // DB pixel size of the image being shown
    const retryRef = useRef(false);
    const [attempt, setAttempt] = useState(0);

    // null = the image's default preset (chosen by the server)
    const [preset, setPreset] = useState(null);
    const [fullres, setFullres] = useState(null);       // last server envelope: presets, preset, manifest...
    const [phase, setPhase] = useState({ kind: 'loading' }); // loading | building | ready | error | missing
    const [zoomPct, setZoomPct] = useState(null);
    const [cursor, setCursor] = useState(null);
    const [barVisible, setBarVisible] = useState(true);
    const [isFullscreen, setIsFullscreen] = useState(false);

    const imageQuery = useQuery({ queryKey: ['image', id], queryFn: () => fetchImage(id), staleTime: 60 * 1000 });
    const image = imageQuery.data && String(imageQuery.data.id) === String(id) ? imageQuery.data : null;

    const manifest = fullres?.manifest;
    const nativeSize = useCallback(() => {
        const w = manifest?.native_width ?? image?.width_pixels;
        const h = manifest?.native_height ?? image?.height_pixels;
        return w && h ? { w, h } : null;
    }, [manifest, image]);
    const nativeRef = useRef(null);
    useEffect(() => { nativeRef.current = nativeSize(); }, [nativeSize]);

    // ---- OpenSeadragon lifecycle -------------------------------------------------------------
    useEffect(() => {
        const viewer = OpenSeadragon({
            element: osdRef.current,
            showNavigationControl: false,
            showNavigator: true,
            navigatorPosition: 'BOTTOM_RIGHT',
            navigatorSizeRatio: 0.18,
            maxZoomPixelRatio: 4,
            minZoomImageRatio: 0.8,
            visibilityRatio: 0.5,
            immediateRender: false,
            crossOriginPolicy: false,
            ajaxWithCredentials: true,
            animationTime: 0.35,
            gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true },
            gestureSettingsTouch: { clickToZoom: false, dblClickToZoom: true, pinchToZoom: true },
        });
        // We own the keyboard (OSD would pan on the arrow keys we use for prev/next)
        viewer.innerTracker.keyDownHandler = null;
        viewer.innerTracker.keyHandler = null;
        viewerRef.current = viewer;

        let raf = 0;
        const sync = () => {
            cancelAnimationFrame(raf);
            raf = requestAnimationFrame(() => {
                const vp = viewer.viewport;
                const native = nativeRef.current;
                if (!vp || !native || !viewer.world.getItemCount()) return;
                const zoom = vp.getZoom(true);
                setZoomPct((vp.getContainerSize().x * zoom) / native.w * 100);
                const c = vp.getCenter(true);
                lastViewRef.current = { cx: c.x, cy: c.y, zoom, ...curDimsRef.current };
            });
        };
        viewer.addHandler('animation', sync);
        viewer.addHandler('open', sync);
        return () => {
            cancelAnimationFrame(raf);
            viewer.destroy();
            viewerRef.current = null;
        };
    }, []);

    // ---- Load: thumbnail first, then poll for the tile pyramid and swap it in ------------------
    useEffect(() => {
        const viewer = viewerRef.current;
        if (!viewer || !image) return undefined;
        const ac = new AbortController();
        const { signal } = ac;
        const imageId = id;
        const newImage = openedIdRef.current !== imageId;

        const openSource = (source, { restore }) => {
            const vp = viewer.viewport;
            const hasView = viewer.world.getItemCount() > 0 && vp;
            const keep = restore !== undefined ? restore : (hasView ? { cx: vp.getCenter(true).x, cy: vp.getCenter(true).y, zoom: vp.getZoom(true) } : null);
            viewer.addOnceHandler('open', () => {
                if (keep) {
                    viewer.viewport.zoomTo(keep.zoom, null, true);
                    viewer.viewport.panTo(new OpenSeadragon.Point(keep.cx, keep.cy), true);
                    viewer.viewport.applyConstraints(true);
                }
            });
            viewer.open(source);
        };

        (async () => {
            if (newImage) {
                setFullres(null);
                setCursor(null);
                setPhase({ kind: 'loading' });
                // Same-size neighbour: keep the normalised centre and zoom; otherwise the viewer starts at Fit.
                const last = lastViewRef.current;
                const sticky = last && last.w === image.width_pixels && last.h === image.height_pixels
                    && last.cx != null ? { cx: last.cx, cy: last.cy, zoom: last.zoom } : null;
                lastViewRef.current = null;
                openedIdRef.current = imageId;
                curDimsRef.current = { w: image.width_pixels, h: image.height_pixels };
                openSource({ type: 'image', url: `${API_BASE_URL}/images/${imageId}/thumbnail`, buildPyramid: false }, { restore: sticky });
            }

            let waited = 0;
            let retry = retryRef.current;
            retryRef.current = false;
            while (!signal.aborted) {
                let res;
                try {
                    res = await fetchFullRes(imageId, preset, { retry, signal });
                } catch (err) {
                    if (signal.aborted) return;
                    if (err.status === 409 && preset) {      // preset not valid for this image: use its default
                        setPreset(null);
                        return;
                    }
                    setPhase(err.status === 404
                        ? { kind: 'missing' }
                        : { kind: 'error', message: err.message || 'Could not open this image at full resolution' });
                    return;
                }
                retry = false;
                if (signal.aborted) return;
                setFullres(res);
                if (res.httpStatus === 200) {
                    const m = res.manifest;
                    nativeRef.current = { w: m.native_width, h: m.native_height };
                    const source = m.type === 'image'
                        ? { type: 'image', url: getFullResSourceUrl(imageId), buildPyramid: false }
                        : getFullResDziUrl(imageId, res.key);
                    openSource(source, {});
                    setPhase({ kind: 'ready' });
                    return;
                }
                if (res.state === 'error') {
                    setPhase({ kind: 'error', message: res.error || 'The full-resolution render failed' });
                    return;
                }
                setPhase({ kind: 'building', state: res.state, pct: res.pct });
                const delay = waited >= POLL_BACKOFF_AFTER_MS ? POLL_SLOW_MS : POLL_FAST_MS;
                await sleep(delay, signal);
                waited += delay;
            }
        })();

        return () => ac.abort();
    }, [id, preset, image, attempt]);

    // ---- Prefetch: start the next image's build once this one is ready ------------------------
    useEffect(() => {
        if (phase.kind !== 'ready' || !navInfo.nextId) return undefined;
        const ac = new AbortController();
        const timer = setTimeout(async () => {
            try {
                const next = await fetchImage(navInfo.nextId);
                if (ac.signal.aborted || !next.width_pixels || !next.height_pixels
                    || next.width_pixels * next.height_pixels > PREFETCH_MAX_PIXELS) return;
                await fetchFullRes(navInfo.nextId, preset, { signal: ac.signal });
            } catch {
                // best effort
            }
        }, 800);
        return () => { clearTimeout(timer); ac.abort(); };
    }, [phase.kind, navInfo.nextId, preset]);

    // ---- Readout + crosshair ----------------------------------------------------------------
    const updateCursor = useCallback((clientX, clientY) => {
        const viewer = viewerRef.current;
        const stage = stageRef.current;
        const native = nativeRef.current;
        if (!viewer || !stage || !native || !viewer.world.getItemCount() || !image) return;
        const rect = stage.getBoundingClientRect();
        const x = clientX - rect.left;
        const y = clientY - rect.top;
        const vp = viewer.viewport.pointFromPixel(new OpenSeadragon.Point(x, y), true);
        const home = viewer.world.getHomeBounds();
        const fx = vp.x;
        const fy = vp.y / home.height;
        if (fx < 0 || fy < 0 || fx >= 1 || fy >= 1) {
            setCursor(null);
            return;
        }
        const sky = pixelToSky(
            fx * image.width_pixels, fy * image.height_pixels, image.width_pixels, image.height_pixels,
            image.ra_center_degrees, image.dec_center_degrees, image.pixel_scale_arcsec, image.rotation_degrees,
            image.raw_header?.astrometry_parity || 1,
        );
        setCursor({ x, y, px: Math.floor(fx * native.w), py: Math.floor(fy * native.h), ra: sky?.ra, dec: sky?.dec });
    }, [image]);

    // ---- Controls -----------------------------------------------------------------------------
    const fit = useCallback(() => viewerRef.current?.viewport.goHome(), []);
    const oneToOne = useCallback(() => {
        const viewer = viewerRef.current;
        const native = nativeRef.current;
        if (!viewer || !native) return;
        viewer.viewport.zoomTo(native.w / viewer.viewport.getContainerSize().x);
        viewer.viewport.applyConstraints();
    }, []);
    const zoomBy = useCallback((factor) => {
        viewerRef.current?.viewport.zoomBy(factor);
        viewerRef.current?.viewport.applyConstraints();
    }, []);
    const panBy = useCallback((dx, dy) => {
        const viewer = viewerRef.current;
        if (!viewer) return;
        const b = viewer.viewport.getBounds();
        viewer.viewport.panBy(new OpenSeadragon.Point(dx * b.width, dy * b.width));
        viewer.viewport.applyConstraints();
    }, []);
    const toggleFullscreen = useCallback(() => {
        if (document.fullscreenElement) document.exitFullscreen?.();
        else document.documentElement.requestFullscreen?.();
    }, []);
    const cyclePreset = useCallback(() => {
        const presets = fullres?.presets;
        if (!presets?.length) return;
        const current = fullres.preset;
        setPreset(presets[(presets.indexOf(current) + 1) % presets.length]);
    }, [fullres]);
    const back = useCallback(() => navigate(`/images/${id}`), [navigate, id]);

    useEffect(() => {
        const onKey = (e) => {
            if (e.target.tagName === 'INPUT' || e.target.tagName === 'TEXTAREA') return;
            if (e.ctrlKey || e.metaKey || e.altKey) return;
            const k = e.key.toLowerCase();
            const handled = {
                '+': () => zoomBy(KEY_ZOOM), '=': () => zoomBy(KEY_ZOOM), '-': () => zoomBy(1 / KEY_ZOOM), _: () => zoomBy(1 / KEY_ZOOM),
                w: () => panBy(0, -KEY_PAN), s: () => panBy(0, KEY_PAN), a: () => panBy(-KEY_PAN, 0), d: () => panBy(KEY_PAN, 0),
                0: fit, 1: oneToOne,
                arrowleft: goPrev, arrowright: goNext,
                f: toggleFullscreen, p: cyclePreset,
                escape: () => { if (!document.fullscreenElement) back(); },
                g: returnToSearch,
            }[k];
            if (handled) {
                e.preventDefault();
                handled();
            }
        };
        window.addEventListener('keydown', onKey);
        return () => window.removeEventListener('keydown', onKey);
    }, [zoomBy, panBy, fit, oneToOne, goPrev, goNext, toggleFullscreen, cyclePreset, back, returnToSearch]);

    useEffect(() => {
        const onChange = () => setIsFullscreen(!!document.fullscreenElement);
        document.addEventListener('fullscreenchange', onChange);
        return () => document.removeEventListener('fullscreenchange', onChange);
    }, []);

    // ---- Auto-hiding top bar -----------------------------------------------------------------
    const showBar = useCallback(() => {
        setBarVisible(true);
        clearTimeout(hideTimerRef.current);
        hideTimerRef.current = setTimeout(() => {
            if (!barHoverRef.current) setBarVisible(false);
        }, BAR_HIDE_MS);
    }, []);
    useEffect(() => {
        hideTimerRef.current = setTimeout(() => {
            if (!barHoverRef.current) setBarVisible(false);
        }, BAR_HIDE_MS);
        return () => clearTimeout(hideTimerRef.current);
    }, []);

    // ---- Pointer handling on the stage -----------------------------------------------------
    const onPointerMove = (e) => {
        showBar();
        if (e.pointerType === 'mouse') updateCursor(e.clientX, e.clientY);
    };
    const onPointerDown = (e) => {
        showBar();
        if (e.pointerType !== 'mouse') swipeRef.current = { x: e.clientX, y: e.clientY, t: Date.now() };
    };
    const onPointerUp = (e) => {
        if (e.pointerType === 'mouse') return;
        const start = swipeRef.current;
        swipeRef.current = null;
        if (!start) return;
        const dx = e.clientX - start.x;
        const dy = e.clientY - start.y;
        const viewer = viewerRef.current;
        const atFit = viewer && viewer.viewport.getZoom(true) <= viewer.viewport.getHomeZoom() * 1.02;
        if (Math.hypot(dx, dy) < 8 && Date.now() - start.t < 500) {
            updateCursor(e.clientX, e.clientY);                // tap places the crosshair + readout
        } else if (atFit && Math.abs(dx) > 70 && Math.abs(dy) < 50) {
            (dx < 0 ? goNext : goPrev)();                       // swipe at Fit pages through the results
        }
    };

    // ---- Render ----------------------------------------------------------------------------
    const presets = fullres?.presets || [];
    const activePreset = fullres?.preset || preset;
    const scale = manifest?.scale || 1;

    if (phase.kind === 'missing') {
        return (
            <div className="fullres-viewer fullres-message">
                <h2>Source file missing</h2>
                <p>The original file for this image can&apos;t be found, so it can&apos;t be opened at full resolution.</p>
                <button className="btn btn-primary" onClick={back}>Back</button>
            </div>
        );
    }

    return (
        <div className="fullres-viewer">
            <div
                className={`fullres-bar ${barVisible ? '' : 'hidden'}`}
                onMouseEnter={() => { barHoverRef.current = true; setBarVisible(true); }}
                onMouseLeave={() => { barHoverRef.current = false; showBar(); }}
            >
                <button className="fr-btn" onClick={back} title="Back to image (Esc)">
                    <ArrowLeft size={16} /> Back
                </button>
                <span className="fr-title" title={image?.file_name}>{image?.file_name || '…'}</span>
                {navInfo.currentIndex !== -1 && (
                    <span className="fr-nav">
                        <button className="fr-btn icon" onClick={goPrev} disabled={!navInfo.prevId} title="Previous (←)">
                            <ChevronLeft size={16} />
                        </button>
                        <span>{navInfo.currentIndex} / {navInfo.total}</span>
                        <button className="fr-btn icon" onClick={goNext} disabled={!navInfo.nextId} title="Next (→)">
                            <ChevronRight size={16} />
                        </button>
                    </span>
                )}
                <select
                    className="fr-select"
                    value={activePreset || ''}
                    disabled={presets.length === 0}
                    onChange={(e) => setPreset(e.target.value)}
                    title="Stretch (P)"
                >
                    {presets.length === 0 && <option value="">Stretch</option>}
                    {presets.map((p) => <option key={p} value={p}>{PRESET_LABELS[p] || p}</option>)}
                </select>
                <button className="fr-btn" onClick={fit} title="Fit (0)"><Frame size={16} /> Fit</button>
                <button className="fr-btn" onClick={oneToOne} title="100% native pixels (1)"><ScanSearch size={16} /> 1:1</button>
                <span className="fr-zoom">{zoomPct != null ? `${zoomPct < 10 ? zoomPct.toFixed(1) : Math.round(zoomPct)}%` : ''}</span>
                <button className="fr-btn icon" onClick={toggleFullscreen} title="Fullscreen (F)">
                    {isFullscreen ? <Minimize size={16} /> : <Maximize size={16} />}
                </button>
            </div>

            <div
                className="fullres-stage"
                ref={stageRef}
                onPointerMove={onPointerMove}
                onPointerDown={onPointerDown}
                onPointerUp={onPointerUp}
                onPointerLeave={(e) => e.pointerType === 'mouse' && setCursor(null)}
            >
                <div className="fullres-osd" ref={osdRef} />
                {cursor && (
                    <>
                        <div className="fr-crosshair horizontal" style={{ top: cursor.y }} />
                        <div className="fr-crosshair vertical" style={{ left: cursor.x }} />
                    </>
                )}
            </div>

            {phase.kind === 'building' && (
                <div className="fullres-chip">
                    <Loader2 size={14} className="spin" />
                    Rendering full resolution… {STATE_LABELS[phase.state] || phase.state}
                    {phase.pct != null ? ` ${phase.pct}%` : ''}
                </div>
            )}
            {phase.kind === 'loading' && (
                <div className="fullres-chip"><Loader2 size={14} className="spin" /> Opening…</div>
            )}
            {phase.kind === 'error' && (
                <div className="fullres-chip error">
                    {phase.message}
                    <button className="fr-btn" onClick={() => { retryRef.current = true; setAttempt((n) => n + 1); }}>Retry</button>
                </div>
            )}
            {phase.kind === 'ready' && scale > 1 && (
                <div className="fullres-chip subtle">Rendered at 1/{scale} resolution</div>
            )}

            {cursor && (
                <div className="fullres-readout">
                    X {cursor.px.toLocaleString()} &nbsp; Y {cursor.py.toLocaleString()}
                    {cursor.ra !== undefined && <> &nbsp;·&nbsp; RA {formatRA(cursor.ra)} &nbsp; Dec {formatDec(cursor.dec)}</>}
                </div>
            )}
        </div>
    );
}
