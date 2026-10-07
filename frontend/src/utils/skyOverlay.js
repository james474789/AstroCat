/**
 * Helpers for the dynamic catalog overlay ("AstroCat annotations"). Object coordinates and
 * ellipses are in the image's native pixels (top-left origin), as returned by
 * /images/{id}/sky-overlay. Layout runs in the overlay layer's own units, where one screen
 * pixel is `labelScale` units (1/zoom under a CSS transform, 1 in the full-res viewer).
 */

export const SKY_CATALOGS = ['MESSIER', 'CALDWELL', 'NGC', 'IC', 'SH2', 'NAMED_STAR'];
export const SKY_CATALOG_LABELS = {
    MESSIER: 'Messier',
    CALDWELL: 'Caldwell',
    NGC: 'NGC',
    IC: 'IC',
    SH2: 'Sharpless',
    NAMED_STAR: 'Stars',
};

// Screen-pixel sizes
export const MARKER_PX = 6;        // point marker radius; also the minimum drawn ellipse
const STAR_PX = 4;
const LABEL_FONT_PX = 12;
const LABEL_GAP_PX = 3;
const HIT_TOLERANCE_PX = 10;
const CELL_PX = 80;                // spatial hash cell for label collisions

/** Screen units per native pixel for an affine, uniformly scaled toScreen. */
function unitsPerNative(toScreen) {
    const a = toScreen(0, 0);
    const b = toScreen(1000, 0);
    return Math.hypot(b.x - a.x, b.y - a.y) / 1000;
}

/**
 * Drawable geometry for each visible object in layer units:
 * { obj, cx, cy, rx, ry, angle, kind: 'star' | 'ellipse' | 'point', dashed }.
 * Objects whose marker is entirely outside `view` (layer-unit rect) are skipped.
 */
export function skyGeometry(objects, toScreen, labelScale = 1, view = null) {
    const k = unitsPerNative(toScreen);
    const minR = MARKER_PX * labelScale;
    const out = [];
    for (const obj of objects || []) {
        const c = toScreen(obj.x, obj.y);
        let kind = 'point';
        let rx = minR;
        let ry = minR;
        let angle = 0;
        let dashed = false;
        if (obj.catalog === 'NAMED_STAR') {
            kind = 'star';
            rx = ry = STAR_PX * labelScale;
        } else if (obj.ellipse) {
            const erx = obj.ellipse.rx * k;
            const ery = obj.ellipse.ry * k;
            if (Math.max(erx, ery) >= minR) {
                kind = 'ellipse';
                rx = Math.max(erx, 1.5 * labelScale);
                ry = Math.max(ery, 1.5 * labelScale);
                angle = obj.ellipse.angle_deg;
                dashed = !obj.ellipse.pa_known;
            }
        }
        const reach = Math.max(rx, ry);
        if (view && (c.x + reach < view.x || c.x - reach > view.x + view.w
            || c.y + reach < view.y || c.y - reach > view.y + view.h)) continue;
        out.push({ obj, cx: c.x, cy: c.y, rx, ry, angle, kind, dashed });
    }
    return out;
}

function textWidth(text, fontSize) {
    return text.length * fontSize * 0.58;
}

/**
 * Greedy label placement: geometry is in priority order (the API sorts by catalog then
 * brightness); each label tries right, left, above, below its marker and is dropped if it
 * would overlap an earlier label, a small marker, or leave `clip`. Returns Map(key -> {x, y, anchor}).
 */
export function placeLabels(geometry, labelScale = 1, clip = null) {
    const fontSize = LABEL_FONT_PX * labelScale;
    const gap = LABEL_GAP_PX * labelScale;
    const cell = CELL_PX * labelScale;
    const grid = new Map();
    const boxes = [];

    const cellsOf = (b) => {
        const keys = [];
        for (let i = Math.floor(b.x0 / cell); i <= Math.floor(b.x1 / cell); i++) {
            for (let j = Math.floor(b.y0 / cell); j <= Math.floor(b.y1 / cell); j++) keys.push(`${i},${j}`);
        }
        return keys;
    };
    const collides = (b) => {
        for (const key of cellsOf(b)) {
            for (const idx of grid.get(key) || []) {
                const o = boxes[idx];
                if (b.x0 < o.x1 && b.x1 > o.x0 && b.y0 < o.y1 && b.y1 > o.y0) return true;
            }
        }
        return false;
    };
    const add = (b) => {
        const idx = boxes.push(b) - 1;
        for (const key of cellsOf(b)) {
            if (!grid.has(key)) grid.set(key, []);
            grid.get(key).push(idx);
        }
    };

    // Small markers are obstacles so labels don't hide other objects; big ellipses aren't
    for (const g of geometry) {
        if (g.kind !== 'ellipse') add({ x0: g.cx - g.rx, y0: g.cy - g.ry, x1: g.cx + g.rx, y1: g.cy + g.ry });
    }

    const placed = new Map();
    for (const g of geometry) {
        const text = g.obj.label;
        const w = textWidth(text, fontSize);
        const h = fontSize;
        const r = Math.max(g.rx, g.ry);
        // Big ellipses are labelled at their top edge rather than beside the centre
        const top = g.kind === 'ellipse' ? g.cy - Math.max(g.rx, g.ry) : g.cy;
        const candidates = g.kind === 'ellipse'
            ? [
                { x: g.cx - w / 2, y: top - gap - h, anchor: 'start' },
                { x: g.cx - w / 2, y: g.cy - h / 2, anchor: 'start' },
                { x: g.cx + gap, y: g.cy + gap, anchor: 'start' },
            ]
            : [
                { x: g.cx + r + gap, y: g.cy - h / 2, anchor: 'start' },
                { x: g.cx - r - gap - w, y: g.cy - h / 2, anchor: 'start' },
                { x: g.cx - w / 2, y: g.cy - r - gap - h, anchor: 'start' },
                { x: g.cx - w / 2, y: g.cy + r + gap, anchor: 'start' },
            ];
        for (const c of candidates) {
            const b = { x0: c.x, y0: c.y, x1: c.x + w, y1: c.y + h };
            if (clip && (b.x0 < clip.x || b.y0 < clip.y || b.x1 > clip.x + clip.w || b.y1 > clip.y + clip.h)) continue;
            if (collides(b)) continue;
            add(b);
            // SVG text y is the baseline
            placed.set(g.obj.key, { x: c.x, y: c.y + h * 0.82, anchor: c.anchor });
            break;
        }
    }
    return placed;
}

/**
 * The object under a native-pixel point: point markers within a screen tolerance first
 * (nearest), else the smallest ellipse containing the point. nativePerScreen = native px per screen px.
 */
export function hitTestSky(objects, px, py, nativePerScreen) {
    const tol = HIT_TOLERANCE_PX * nativePerScreen;
    let bestPoint = null;
    let bestPointDist = Infinity;
    let bestEllipse = null;
    let bestArea = Infinity;
    for (const obj of objects || []) {
        const dx = px - obj.x;
        const dy = py - obj.y;
        const e = obj.ellipse;
        const big = e && obj.catalog !== 'NAMED_STAR' && Math.max(e.rx, e.ry) >= MARKER_PX * nativePerScreen;
        if (big) {
            const t = (-e.angle_deg * Math.PI) / 180;
            const u = dx * Math.cos(t) - dy * Math.sin(t);
            const v = dx * Math.sin(t) + dy * Math.cos(t);
            const rx = Math.max(e.rx, tol);
            const ry = Math.max(e.ry, tol);
            if ((u * u) / (rx * rx) + (v * v) / (ry * ry) <= 1) {
                const area = rx * ry;
                if (area < bestArea) {
                    bestArea = area;
                    bestEllipse = obj;
                }
            }
        }
        const d = Math.hypot(dx, dy);
        if (d <= tol && d < bestPointDist) {
            bestPointDist = d;
            bestPoint = obj;
        }
    }
    return bestPoint || bestEllipse;
}

/** Hover text: designations ("M42 · NGC 1976"), and name / type / magnitude. Stars lead with their name. */
export function describeSkyObject(obj) {
    const designations = obj.designations.join(' · ');
    const named = obj.label !== obj.designations[0];
    const parts = [];
    if (obj.common_name && !named) parts.push(obj.common_name);
    if (obj.object_type) parts.push(obj.object_type);
    if (obj.magnitude != null) parts.push(`mag ${obj.magnitude.toFixed(1)}`);
    return { title: named ? `${obj.label} (${designations})` : designations, detail: parts.join(' · ') };
}

/** Search query for clicking an object: its designation as the catalogs store it. */
export function searchNameFor(obj) {
    return obj.search_name || (obj.designations[0] || obj.label).replace(/\s+/g, '');
}
