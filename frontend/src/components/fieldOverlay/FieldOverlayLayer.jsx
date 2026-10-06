import { useId } from 'react';
import './FieldOverlay.css';

const LABEL_MAX = 28;

function labelFor(group) {
    const name = group.object_name || group.file_name || `#${group.id}`;
    const short = name.length > LABEL_MAX ? `${name.slice(0, LABEL_MAX - 1)}…` : name;
    const parts = [short];
    if (group.count > 1) parts.push(`×${group.count}`);
    if (group.master_count > 0 && group.count > group.master_count) {
        parts.push(`(${group.master_count} master${group.master_count > 1 ? 's' : ''})`);
    }
    if (group.shape === 'circle') parts.push('(rotation unknown)');
    return parts.join(' ');
}

function circleOf(g, toScreen) {
    const c = toScreen(g.center[0], g.center[1]);
    const e = toScreen(g.center[0] + g.radius_px, g.center[1]);
    return { c, r: Math.hypot(e.x - c.x, e.y - c.y) };
}

/**
 * Outlines of other images' footprints, coloured by type: outlines containing a master stand out,
 * sub-only outlines are fainter. Only the hovered outline is labelled, to keep busy fields readable.
 * Purely visual (no pointer events): the host page hit-tests in image pixels so its own pan/zoom
 * gestures keep working.
 *
 * toScreen(px, py) maps native image pixels to this layer's coordinates; clip is the image's
 * rectangle in the same coordinates; labelScale scales text/strokes (e.g. 1/zoom under a CSS transform).
 */
export default function FieldOverlayLayer({ groups, toScreen, clip, hoveredId, labelScale = 1 }) {
    const clipId = useId();
    if (!groups?.length || !clip) return null;
    const fontSize = 12 * labelScale;
    // Sub-only outlines underneath, masters on top; each tier keeps the largest-first order
    const ordered = [...groups.filter((g) => !(g.master_count > 0)), ...groups.filter((g) => g.master_count > 0)];
    const hovered = groups.find((g) => g.id === hoveredId) || null;
    const stroke = 1.5 * labelScale;

    return (
        <svg className="field-overlay-layer" width="100%" height="100%">
            <defs>
                <clipPath id={clipId}>
                    <rect x={clip.x} y={clip.y} width={clip.w} height={clip.h} />
                </clipPath>
            </defs>
            <g clipPath={`url(#${clipId})`}>
                {ordered.map((g) => {
                    const hovered = g.id === hoveredId;
                    const isMaster = g.master_count > 0;
                    const cls = `fo-shape ${isMaster ? 'master' : 'sub'}${hovered ? ' hovered' : ''}`;
                    const sw = (isMaster ? stroke * 1.4 : stroke) * (hovered ? 1.6 : 1);
                    if (g.shape === 'circle') {
                        const { c, r } = circleOf(g, toScreen);
                        return (
                            <circle key={g.id} cx={c.x} cy={c.y} r={r} className={cls} strokeWidth={sw}
                                strokeDasharray={`${6 * labelScale} ${4 * labelScale}`} />
                        );
                    }
                    const pts = g.corners.map(([x, y]) => toScreen(x, y));
                    return (
                        <polygon key={g.id} points={pts.map((p) => `${p.x},${p.y}`).join(' ')}
                            className={cls} strokeWidth={sw} />
                    );
                })}
                {hovered && (() => {
                    // Label just above the outline's top point, kept inside the image
                    let anchor;
                    if (hovered.shape === 'circle') {
                        const { c, r } = circleOf(hovered, toScreen);
                        anchor = { x: c.x - r * 0.7, y: c.y - r * 0.7 };
                    } else {
                        const pts = hovered.corners.map(([x, y]) => toScreen(x, y));
                        anchor = pts.reduce((a, p) => (p.y < a.y ? p : a), pts[0]);
                    }
                    const lx = Math.min(Math.max(anchor.x, clip.x + 2 * labelScale), clip.x + clip.w - 160 * labelScale);
                    const ly = Math.min(
                        Math.max(anchor.y - 4 * labelScale, clip.y + fontSize + 2 * labelScale),
                        clip.y + clip.h - 4 * labelScale,
                    );
                    return (
                        <text x={lx} y={ly} fontSize={fontSize} strokeWidth={3 * labelScale} className="fo-label">
                            {labelFor(hovered)}
                        </text>
                    );
                })()}
            </g>
        </svg>
    );
}
