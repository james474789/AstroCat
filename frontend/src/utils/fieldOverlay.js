/**
 * Geometry helpers for the "images in field" overlay. Group coordinates are in the
 * viewed image's native pixels (top-left origin), as returned by /images/{id}/field-overlaps.
 */

export function pointInPolygon(x, y, corners) {
    let inside = false;
    for (let i = 0, j = corners.length - 1; i < corners.length; j = i++) {
        const [xi, yi] = corners[i];
        const [xj, yj] = corners[j];
        if ((yi > y) !== (yj > y) && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
}

export function groupContains(group, x, y) {
    if (group.shape === 'circle') {
        const [cx, cy] = group.center;
        return Math.hypot(x - cx, y - cy) <= group.radius_px;
    }
    return pointInPolygon(x, y, group.corners);
}

/** Smallest-area group containing the point, so nested footprints stay reachable. */
export function hitTest(groups, x, y) {
    let best = null;
    for (const g of groups || []) {
        if ((!best || g.area < best.area) && groupContains(g, x, y)) best = g;
    }
    return best;
}

/** The rectangle an image occupies inside a box under `object-fit: contain`. */
export function containedRect(boxW, boxH, imgW, imgH) {
    if (!boxW || !boxH || !imgW || !imgH) return null;
    const scale = Math.min(boxW / imgW, boxH / imgH);
    const w = imgW * scale;
    const h = imgH * scale;
    return { x: (boxW - w) / 2, y: (boxH - h) / 2, w, h };
}

/** Overlays need a plate solve and a known rotation to place anything on this image. */
export function overlayUnavailableReason(image) {
    if (!image?.is_plate_solved) return 'Not plate-solved';
    if (image.rotation_degrees == null) return "Rotation unknown — can't place overlays";
    return null;
}
