import { Copy, Check } from 'lucide-react';
import { useState } from 'react';
import './MetadataTab.css';

export default function MetadataDetailsTab({ image, headerData }) {
    const [copiedField, setCopiedField] = useState(null);
    const [expandedSection, setExpandedSection] = useState('file');

    const copyToClipboard = (text, fieldName) => {
        navigator.clipboard.writeText(text);
        setCopiedField(fieldName);
        setTimeout(() => setCopiedField(null), 2000);
    };

    const formatValue = (value) => {
        if (value === null || value === undefined || value === '') return '—';
        if (typeof value === 'boolean') return value ? 'Yes' : 'No';
        if (typeof value === 'object') return JSON.stringify(value, null, 2);
        if (typeof value === 'number') return value.toLocaleString();
        return String(value);
    };

    const renderField = (label, value) => {
        if (value === null || value === undefined || value === '') return null;

        return (
            <div key={label} className="detail-row">
                <div className="detail-label">{label}</div>
                <div className="detail-value-container">
                    <div className="detail-value" title={String(value)}>
                        {formatValue(value)}
                    </div>
                    <button
                        className="copy-btn"
                        onClick={() => copyToClipboard(String(value), label)}
                        title="Copy to clipboard"
                    >
                        {copiedField === label ? <Check size={14} /> : <Copy size={14} />}
                    </button>
                </div>
            </div>
        );
    };

    const detailSections = [
        {
            id: 'file',
            title: '📄 File Information',
            fields: [
                ['File Name', image.file_name],
                ['Format', image.file_format],
                ['Size', image.file_size_bytes ? `${(image.file_size_bytes / (1024 * 1024)).toFixed(2)} MB` : null],
                ['Path', image.file_path],
                ['Hash', image.file_hash],
                ['Indexed', image.indexed_at ? new Date(image.indexed_at).toLocaleString() : null],
                ['Last Updated', image.updated_at ? new Date(image.updated_at).toLocaleString() : null],
            ]
        },
        {
            id: 'observation',
            title: '🔭 Observational Data',
            fields: [
                ['Object Name', image.object_name],
                ['Observer', image.observer_name],
                ['Site', image.site_name],
                ['Site Latitude', image.site_latitude],
                ['Site Longitude', image.site_longitude],
                ['Capture Date/Time', image.capture_date ? new Date(image.capture_date).toLocaleString() : null],
            ]
        },
        {
            id: 'plate',
            title: '📍 Plate Solving & WCS',
            fields: [
                ['Plate Solved', image.is_plate_solved ? 'Yes' : 'No'],
                ['Provider', image.plate_solve_provider === 'LOCAL' ? 'Local' : image.plate_solve_provider || null],
                ['Source', image.plate_solve_source],
                ['Right Ascension', image.ra_center_degrees ? `${image.ra_center_degrees.toFixed(6)}°` : null],
                ['Declination', image.dec_center_degrees ? `${image.dec_center_degrees.toFixed(6)}°` : null],
                ['Field Radius', image.field_radius_degrees ? `${image.field_radius_degrees.toFixed(4)}°` : null],
                ['Pixel Scale', image.pixel_scale_arcsec ? `${image.pixel_scale_arcsec.toFixed(4)} arcsec/px` : null],
                ['Rotation (PA)', image.rotation_degrees ? `${image.rotation_degrees.toFixed(2)}°` : null],
                ['Image Dimensions', image.width_pixels && image.height_pixels ? `${image.width_pixels} × ${image.height_pixels} px` : null],
            ]
        },
        {
            id: 'exposure',
            title: '📊 Exposure & Imaging Settings',
            fields: [
                ['Exposure Time', image.exposure_time_seconds ? `${image.exposure_time_seconds.toFixed(3)}s` : null],
                ['Gain', image.gain],
                ['ISO', image.iso_speed],
                ['Binning', image.binning],
                ['Temperature', image.temperature_celsius ? `${image.temperature_celsius}°C` : null],
                ['Filter', image.filter_name],
            ]
        },
        {
            id: 'equipment',
            title: '🎥 Equipment',
            fields: [
                ['Camera', image.camera_name],
                ['Telescope/Lens', image.telescope_name],
                ['Lens Model', image.lens_model],
                ['Focal Length', image.focal_length ? `${image.focal_length}mm` : null],
                ['Focal Length (35mm eq.)', image.focal_length_35mm ? `${image.focal_length_35mm}mm` : null],
                ['Aperture', image.aperture ? `f/${image.aperture}` : null],
            ]
        },
        {
            id: 'quality',
            title: '⭐ Quality & Classification',
            fields: [
                ['Classification', image.subtype],
                ['Rating', image.rating ? `${image.rating} stars` : null],
                ['White Balance', image.white_balance],
                ['Metering Mode', image.metering_mode],
                ['Flash Fired', image.flash_fired !== null ? (image.flash_fired ? 'Yes' : 'No') : null],
            ]
        },
        {
            id: 'astrometry',
            title: '🌐 Astrometry.net Status',
            fields: [
                ['Status', image.astrometry_status],
                ['Submission ID', image.astrometry_submission_id],
                ['Job ID', image.astrometry_job_id],
                ['Results URL', image.astrometry_url],
            ]
        }
    ];

    return (
        <div className="metadata-details-tab">
            <div className="sections-container">
                {detailSections.map(section => {
                    const fields = section.fields.filter(([_, val]) => val !== null);
                    if (fields.length === 0) return null;

                    return (
                        <div key={section.id} className="detail-section">
                            <button
                                className={`section-header ${expandedSection === section.id ? 'expanded' : ''}`}
                                onClick={() => setExpandedSection(expandedSection === section.id ? null : section.id)}
                            >
                                <span className="section-title">{section.title}</span>
                                <span className="expand-icon">▼</span>
                            </button>
                            {expandedSection === section.id && (
                                <div className="section-content">
                                    {fields.map(([label, value]) => renderField(label, value))}
                                </div>
                            )}
                        </div>
                    );
                })}
            </div>
        </div>
    );
}
