// Filter swatch colours, shared by Targets and the session timeline (Q1c).
export const FILTER_COLORS = {
    L: '#d0d4dc', R: '#e05050', G: '#50c070', B: '#5080e0',
    Ha: '#c8283c', OIII: '#2f80ed', SII: '#a83246', Hb: '#3cc8ff',
    Duo: '#b060c0', None: '#a0a0a0', Other: '#707070',
};

export function filterColor(name) {
    if (FILTER_COLORS[name]) return FILTER_COLORS[name];
    return FILTER_COLORS.Other;
}
