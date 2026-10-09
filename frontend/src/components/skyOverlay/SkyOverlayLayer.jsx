import { useId, useMemo } from 'react';
import { skyGeometry, placeLabels, describeSkyObject } from '../../utils/skyOverlay';
import './SkyOverlay.css';

/**
 * Catalog objects drawn from the image's own plate solution, coloured by catalog: ellipses at
 * catalog size and position angle (dashed when the angle is unknown), point markers for small or
 * sizeless objects, a ring for named stars, a diamond for asteroids and comets (online SkyBoT
 * layer). Labels are placed greedily without overlaps; the hovered object always gets its full
 * description. Purely visual (no pointer events): the host
 * page hit-tests in image pixels so its own pan/zoom gestures keep working.
 *
 * toScreen(px, py) maps native image pixels to this layer's coordinates (affine, uniform scale);
 * clip is the image's rectangle in the same coordinates; labelScale is layer units per screen
 * pixel (e.g. 1/zoom under a CSS transform); view (optional) is the visible part, for culling.
 */
export default function SkyOverlayLayer({ objects, toScreen, clip, hoveredKey, labelScale = 1, view = null }) {
    const clipId = useId();
    // Hosts rebuild toScreen on every render; key the layout on the mapping itself
    const o = toScreen(0, 0);
    const u = toScreen(1000, 1000);
    const viewKey = view ? `${view.x},${view.y},${view.w},${view.h}` : '';
    const clipKey = clip ? `${clip.x},${clip.y},${clip.w},${clip.h}` : '';

    const { geometry, labels } = useMemo(() => {
        const geo = skyGeometry(objects, toScreen, labelScale, view);
        return { geometry: geo, labels: placeLabels(geo, labelScale, clip) };
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [objects, o.x, o.y, u.x, u.y, labelScale, viewKey, clipKey]);

    if (!objects?.length || !clip) return null;
    const stroke = 1.5 * labelScale;
    const fontSize = 12 * labelScale;
    const hovered = geometry.find((g) => g.obj.key === hoveredKey) || null;

    return (
        <svg className="sky-overlay-layer" width="100%" height="100%">
            <defs>
                <clipPath id={clipId}>
                    <rect x={clip.x} y={clip.y} width={clip.w} height={clip.h} />
                </clipPath>
            </defs>
            <g clipPath={`url(#${clipId})`}>
                {geometry.map((g) => {
                    const isHovered = g.obj.key === hoveredKey;
                    const cls = `so-shape so-cat-${g.obj.catalog}${isHovered ? ' hovered' : ''}`;
                    const sw = stroke * (isHovered ? 1.8 : 1);
                    if (g.kind === 'ellipse') {
                        return (
                            <ellipse key={g.obj.key} className={cls} cx={g.cx} cy={g.cy} rx={g.rx} ry={g.ry}
                                transform={`rotate(${g.angle} ${g.cx} ${g.cy})`} strokeWidth={sw}
                                strokeDasharray={g.dashed ? `${5 * labelScale} ${4 * labelScale}` : undefined} />
                        );
                    }
                    if (g.kind === 'star') {
                        const tick = g.rx * 0.9;
                        return (
                            <g key={g.obj.key} className={cls} strokeWidth={sw}>
                                <circle cx={g.cx} cy={g.cy} r={g.rx} />
                                <line x1={g.cx + g.rx} y1={g.cy} x2={g.cx + g.rx + tick} y2={g.cy} />
                                <line x1={g.cx - g.rx} y1={g.cy} x2={g.cx - g.rx - tick} y2={g.cy} />
                                <line x1={g.cx} y1={g.cy + g.rx} x2={g.cx} y2={g.cy + g.rx + tick} />
                                <line x1={g.cx} y1={g.cy - g.rx} x2={g.cx} y2={g.cy - g.rx - tick} />
                            </g>
                        );
                    }
                    if (g.kind === 'diamond') {
                        const r = g.rx * 1.25;
                        return (
                            <path key={g.obj.key} className={`${cls}${g.obj.is_comet ? ' so-comet' : ''}`} strokeWidth={sw}
                                d={`M ${g.cx} ${g.cy - r} L ${g.cx + r} ${g.cy} L ${g.cx} ${g.cy + r} L ${g.cx - r} ${g.cy} Z`} />
                        );
                    }
                    return <circle key={g.obj.key} className={cls} cx={g.cx} cy={g.cy} r={g.rx} strokeWidth={sw} />;
                })}
                {geometry.map((g) => {
                    const pos = labels.get(g.obj.key);
                    if (!pos || g.obj.key === hoveredKey) return null;
                    return (
                        <text key={`l-${g.obj.key}`} x={pos.x} y={pos.y} fontSize={fontSize}
                            strokeWidth={3 * labelScale} className={`so-label so-cat-${g.obj.catalog}${g.obj.is_comet ? ' so-comet' : ''}`}>
                            {g.obj.label}
                        </text>
                    );
                })}
                {hovered && (() => {
                    const { title, detail } = describeSkyObject(hovered.obj);
                    const reach = Math.max(hovered.rx, hovered.ry);
                    const width = Math.max(title.length, detail.length) * fontSize * 0.58;
                    const lx = Math.min(Math.max(hovered.cx + reach + 6 * labelScale, clip.x + 2 * labelScale),
                        clip.x + clip.w - width - 2 * labelScale);
                    const ly = Math.min(Math.max(hovered.cy, clip.y + fontSize + 2 * labelScale),
                        clip.y + clip.h - fontSize * 1.4 - 4 * labelScale);
                    return (
                        <text x={lx} y={ly} fontSize={fontSize} strokeWidth={3 * labelScale}
                            className={`so-label so-hover so-cat-${hovered.obj.catalog}`}>
                            <tspan x={lx}>{title}</tspan>
                            {detail && <tspan x={lx} dy={fontSize * 1.3} className="so-detail">{detail}</tspan>}
                        </text>
                    );
                })()}
            </g>
        </svg>
    );
}
