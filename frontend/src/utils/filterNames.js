// Display names for raw FITS/sidecar filter strings (QA-50): the same filter reads the same everywhere.
export function normalizeFilterName(name) {
    if (!name) return 'Unknown';
    const lower = name.toLowerCase().replace(/[^a-z0-9]/g, ''); // alphanumeric only

    if (['r', 'red'].includes(lower)) return 'Red';
    if (['g', 'green'].includes(lower)) return 'Green';
    if (['b', 'blue'].includes(lower)) return 'Blue';
    if (['l', 'lum', 'luminance', 'uvircut', 'lpro'].includes(lower)) return 'Luminance';
    if (['h', 'ha', 'halpha', 'h_alpha'].includes(lower)) return 'H-Alpha';
    if (['o', 'o3', 'oiii', 'oxygen3'].includes(lower)) return 'OIII';
    if (['s', 's2', 'sii', 'sulfur2'].includes(lower)) return 'SII';
    if (['dark'].includes(lower)) return 'Dark';
    if (['flat'].includes(lower)) return 'Flat';
    if (['bias', 'offset'].includes(lower)) return 'Bias';

    // Capitalize first letter for others
    return name.charAt(0).toUpperCase() + name.slice(1);
}
