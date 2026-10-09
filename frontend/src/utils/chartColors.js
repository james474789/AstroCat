// Central chart colour palette. Recharts/SVG props and canvas code cannot always use CSS
// variables, so distinct data-series colours live here as named exports (the only place,
// besides src/index.css and utils/filterColors.js, where raw hex is allowed).

// Categorical series (pie slices, multi-series bars)
export const SERIES = ['#5b8dee', '#8b5cf6', '#22c55e', '#eab308', '#ef4444', '#22d3ee'];

// Single-series fills
export const CHART_PRIMARY = '#5b8dee';
export const CHART_SECONDARY = '#8b5cf6';
export const CHART_ACCENT = '#22d3ee';
export const CHART_SUCCESS = '#10b981';

// Axes, grid and text
export const CHART_AXIS = '#94a3b8';
export const CHART_AXIS_MUTED = '#8a98ad'; // matches --color-text-muted
export const CHART_TEXT = '#f1f5f9';

// Tooltip styles
export const CHART_TOOLTIP = {
    background: '#1e293b',
    border: '1px solid #334155',
    color: CHART_TEXT,
};
export const CHART_TOOLTIP_DASHBOARD = {
    background: '#1a2435',
    border: '1px solid #2d3a4f',
    color: CHART_TEXT,
};

// Heatmap gradient stops: blue -> cyan -> green -> yellow -> red
export const HEAT = {
    blue: '#3b82f6',
    cyan: '#06b6d4',
    green: '#22c55e',
    yellow: '#eab308',
    red: '#ef4444',
};

// Frame / filter colours keyed by lower-cased filter or frame type
export const FILTER_SERIES = {
    red: '#ef4444',
    green: '#22c55e',
    blue: '#3b82f6',
    luminance: '#cbd5e1',
    'h-alpha': '#b91c1c',
    oiii: '#06b6d4',
    sii: '#be185d',
    dark: '#0f172a',
    flat: '#94a3b8',
    bias: '#475569',
    other: '#64748b',
};
