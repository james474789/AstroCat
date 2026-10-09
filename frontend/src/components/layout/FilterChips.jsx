import { X } from 'lucide-react';
import './FilterChips.css';

// R0c: Unassigned bucket key `{cam_key|none}|b{bin}|{focal:.1f or -}` as a chip label.
// The camera key may itself contain '|', so parse from the right.
function formatRigBucket(key) {
    const parts = String(key).split('|');
    if (parts.length < 3) return key;
    const focal = parts.pop();
    const bin = parts.pop().replace(/^b/, '');
    const cam = parts.join('|');
    const camLabel = cam === 'none' ? 'Unknown camera' : cam;
    const focalLabel = focal === '-' ? 'focal unknown' : `~${Math.round(Number(focal))} mm`;
    return `${camLabel} · bin ${bin} · ${focalLabel}`;
}

/**
 * Displays applied filters as dismissible chips
 * Allows users to see and remove active filters at a glance
 */
export default function FilterChips({ filters, onRemove, rigNames = {} }) {
    // Map filter keys to display labels
    const filterLabels = {
        subtype: { label: 'Type', format: (v) => v === 'SUB_FRAME' ? 'Sub Frames' : v === 'INTEGRATION_MASTER' ? 'Masters' : v === 'PLANETARY' ? 'Planetary' : v === 'ALLSKY' ? 'All-sky' : v === 'AURORA' ? 'Aurora' : 'Deprecated' },
        format: { label: 'Format', format: (v) => v },
        rating: {
            label: 'Min Rating', format: (v) => {
                return v ? `${v}+ stars` : v;
            }
        },
        object_name: { label: 'Object', format: (v) => v },
        exposure_min: { label: 'Min Exposure', format: (v) => `${v}s` },
        exposure_max: { label: 'Max Exposure', format: (v) => `${v}s` },
        rotation_min: { label: 'Min Rotation', format: (v) => `${v}°` },
        rotation_max: { label: 'Max Rotation', format: (v) => `${v}°` },
        camera: { label: 'Camera', format: (v) => v },
        filter: { label: 'Filter', format: (v) => v },
        pixel_scale_min: { label: 'Min Scale', format: (v) => `${v}"` },
        pixel_scale_max: { label: 'Max Scale', format: (v) => `${v}"` },
        ra: { label: 'RA', format: (v) => `${v}°` },
        dec: { label: 'Dec', format: (v) => `${v}°` },
        radius: { label: 'Radius', format: (v) => `${v}°` },
        is_plate_solved: { label: 'Plate Solved', format: (v) => v === 'true' ? 'Solved Only' : 'Unsolved Only' },
        start_date: { label: 'From', format: (v) => v },
        end_date: { label: 'Until', format: (v) => v },
        frame_type: {
            label: 'Frame Type', format: (v) => {
                const names = { DARK: 'Darks', FLAT: 'Flats', BIAS: 'Bias', DARK_FLAT: 'Dark Flats' };
                return names[v] || v;
            }
        },
        target_key: { label: 'Target', format: (v) => v === '__none__' ? 'Unassigned' : v },
        rig_id: { label: 'Rig', format: (v) => v === 'none' ? 'Unassigned' : (rigNames[v] || `#${v}`) },
        rig_bucket: { label: 'Bucket', format: formatRigBucket },
        // Q1d star quality
        fwhm_min: { label: 'Min FWHM', format: (v) => v },
        fwhm_max: { label: 'Max FWHM', format: (v) => v },
        hfr_max: { label: 'Max HFR', format: (v) => v },
        eccentricity_max: { label: 'Max Ecc.', format: (v) => v },
        star_count_min: { label: 'Min Stars', format: (v) => v },
        quality_flag: {
            label: 'Suspect', format: (v) => ({ ANY: 'Any reason', SOFT: 'Soft', CLOUD: 'Few stars', TRAILED: 'Elongated' }[v] || v),
        },
        star_metrics_status: { label: 'Measurement', format: (v) => v.replace(/,/g, ' / ').toLowerCase().replace(/_/g, ' ') },
    };

    // Get active filters (frame_type is handled separately below: unlike
    // other filters, its default '' state still shows a "Lights only" chip)
    const activeFilters = Object.entries(filters)
        .filter(([key, value]) => value && key !== 'sort_by' && key !== 'sort_order' && key !== 'frame_type' && key !== 'quality_units')
        .map(([key, value]) => ({
            key,
            label: filterLabels[key]?.label || key,
            display: filterLabels[key]?.format(value) || value,
        }));

    // F1: the Lights default is a normal (removable) filter state. "ALL"
    // means no filter, so it gets no chip.
    if (!filters.frame_type || filters.frame_type === 'LIGHT') {
        activeFilters.unshift({ key: 'frame_type', label: 'Frame Type', display: 'Lights only' });
    } else if (filters.frame_type !== 'ALL') {
        activeFilters.unshift({
            key: 'frame_type',
            label: filterLabels.frame_type.label,
            display: filterLabels.frame_type.format(filters.frame_type),
        });
    }

    if (activeFilters.length === 0) return null;

    return (
        <div className="filter-chips-container">
            <div className="filter-chips-label">Active Filters:</div>
            <div className="filter-chips">
                {activeFilters.map(({ key, label, display }) => (
                    <div key={key} className="filter-chip">
                        <span className="filter-chip-text">
                            <span className="filter-chip-label">{label}:</span>
                            <span className="filter-chip-value">{display}</span>
                        </span>
                        <button
                            className="filter-chip-remove"
                            onClick={() => onRemove(key)}
                            title="Remove filter"
                            aria-label="Remove filter"
                            type="button"
                        >
                            <X size={10} aria-hidden="true" />
                        </button>
                    </div>
                ))}
            </div>
        </div>
    );
}
