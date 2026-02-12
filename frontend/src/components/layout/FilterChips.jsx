import './FilterChips.css';

/**
 * Displays applied filters as dismissible chips
 * Allows users to see and remove active filters at a glance
 */
export default function FilterChips({ filters, onRemove }) {
    // Map filter keys to display labels
    const filterLabels = {
        subtype: { label: 'Type', format: (v) => v === 'SUB_FRAME' ? 'Sub Frames' : v === 'INTEGRATION_MASTER' ? 'Masters' : v === 'PLANETARY' ? 'Planetary' : 'Deprecated' },
        format: { label: 'Format', format: (v) => v },
        rating: {
            label: 'Min Rating', format: (v) => {
                const stars = ['', '★', '★★', '★★★', '★★★★', '★★★★★'];
                return stars[v] || v;
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
    };

    // Get active filters
    const activeFilters = Object.entries(filters)
        .filter(([key, value]) => value && key !== 'sort_by' && key !== 'sort_order')
        .map(([key, value]) => ({
            key,
            label: filterLabels[key]?.label || key,
            display: filterLabels[key]?.format(value) || value,
        }));

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
                            type="button"
                        >
                            ✕
                        </button>
                    </div>
                ))}
            </div>
        </div>
    );
}
