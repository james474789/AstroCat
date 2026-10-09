import './ui.css';

const toCss = (size) => (typeof size === 'number' ? `${size}px` : size);

// Decorative placeholder (aria-hidden); announce loading elsewhere, e.g. aria-busy on the container.
// width/height/radius: number (px) or any CSS length. lines > 1 renders a paragraph (last line 60%).
export default function Skeleton({ width, height, radius, lines = 1, className = '' }) {
    const lineStyle = { height: toCss(height), borderRadius: toCss(radius) };
    if (lines > 1) {
        return (
            <span className={`ui-skeleton-lines ${className}`.trim()} style={{ width: toCss(width) }} aria-hidden="true">
                {Array.from({ length: lines }, (_, i) => <span key={i} className="ui-skeleton" style={lineStyle} />)}
            </span>
        );
    }
    return (
        <span
            className={`ui-skeleton ${className}`.trim()}
            style={{ ...lineStyle, width: toCss(width) }}
            aria-hidden="true"
        />
    );
}
