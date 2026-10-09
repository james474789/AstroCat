import { Copy, Check, FileText, MapPin, BarChart3, Video, Star } from 'lucide-react';
import TelescopeIcon from '../icons/TelescopeIcon';
import { useState } from 'react';
import { formatSubtype } from '../../api/client';
import './MetadataTab.css';

export default function MetadataSummaryTab({ image }) {
    const [copiedField, setCopiedField] = useState(null);

    const copyToClipboard = (text, fieldName) => {
        navigator.clipboard.writeText(text);
        setCopiedField(fieldName);
        setTimeout(() => setCopiedField(null), 2000);
    };

    const formatValue = (value) => {
        if (value === null || value === undefined || value === '') return '—';
        if (typeof value === 'boolean') return value ? 'Yes' : 'No';
        if (typeof value === 'number') return value.toLocaleString();
        return String(value);
    };

    // Curated list of important fields in display order
    const summaryGroups = [
        {
            icon: FileText, title: 'File Information',
            fields: [
                { label: 'File Name', value: image.file_name },
                { label: 'Format', value: image.file_format },
                { label: 'Dimensions', value: image.width_pixels && image.height_pixels ? `${image.width_pixels} × ${image.height_pixels} px` : null },
                { label: 'Size', value: image.file_size_bytes ? `${(image.file_size_bytes / (1024 * 1024)).toFixed(2)} MB` : null },
                { label: 'Created', value: image.file_created ? new Date(image.file_created).toLocaleString() : null },
                { label: 'Modified', value: image.file_last_modified ? new Date(image.file_last_modified).toLocaleString() : null },
                { label: 'Location', value: image.file_path },
            ]
        },
        {
            icon: TelescopeIcon, title: 'Observational Data',
            fields: [
                { label: 'Object Name', value: image.object_name },
                { label: 'Observer', value: image.observer_name },
                { label: 'Site', value: image.site_name },
                { label: 'Capture Date', value: image.capture_date ? new Date(image.capture_date).toLocaleString() : null },
            ]
        },
        {
            icon: MapPin, title: 'Plate Solving',
            fields: [
                { label: 'Status', value: image.is_plate_solved ? 'Solved' : 'Not Solved', highlight: image.is_plate_solved },
                { label: 'Provider', value: image.plate_solve_provider === 'LOCAL' ? 'Local Server' : image.plate_solve_provider === 'NOVA' ? 'Astrometry.net (Nova)' : null },
                { label: 'Right Ascension', value: image.ra_center_degrees ? `${image.ra_center_degrees.toFixed(4)}°` : null },
                { label: 'Declination', value: image.dec_center_degrees ? `${image.dec_center_degrees.toFixed(4)}°` : null },
                { label: 'Field of View', value: image.field_radius_degrees ? `${(image.field_radius_degrees * 2).toFixed(2)}° (diameter)` : null },
                { label: 'Pixel Scale', value: image.pixel_scale_arcsec ? `${image.pixel_scale_arcsec.toFixed(2)} arcsec/px` : null },
                { label: 'Rotation', value: image.rotation_degrees ? `${image.rotation_degrees.toFixed(1)}°` : null },
            ]
        },
        {
            icon: BarChart3, title: 'Exposure Settings',
            fields: [
                { label: 'Exposure Time', value: image.exposure_time_seconds ? `${image.exposure_time_seconds.toFixed(2)}s` : null },
                { label: 'Gain', value: image.gain },
                { label: 'ISO', value: image.iso_speed },
                { label: 'Binning', value: image.binning },
                { label: 'Temperature', value: image.temperature_celsius ? `${image.temperature_celsius}°C` : null },
            ]
        },
        {
            icon: Video, title: 'Equipment',
            fields: [
                { label: 'Camera', value: image.camera_name },
                { label: 'Telescope/Lens', value: image.telescope_name },
                { label: 'Filter', value: image.filter_name },
                { label: 'Focal Length', value: image.focal_length ? `${image.focal_length}mm` : null },
                { label: 'Aperture', value: image.aperture ? `f/${image.aperture}` : null },
            ]
        },
        {
            icon: Star, title: 'Quality & Classification',
            fields: [
                { label: 'Rating', value: image.rating ? `${image.rating} stars` : null },
                { label: 'Classification', value: formatSubtype(image.subtype) },
            ]
        }
    ];

    return (
        <div className="metadata-summary-tab">
            {summaryGroups.map((group, idx) => {
                const filteredFields = group.fields.filter(f => f.value !== null && f.value !== '—');
                if (filteredFields.length === 0) return null;

                return (
                    <div key={idx} className="metadata-group">
                        <h3 className="group-title"><group.icon size={14} />{group.title}</h3>
                        <div className="metadata-items">
                            {filteredFields.map((field, fidx) => (
                                <div key={fidx} className={`metadata-row ${field.highlight ? 'highlight' : ''}`}>
                                    <div className="metadata-label">{field.label}</div>
                                    <div className="metadata-value-container">
                                        <div className="metadata-value">{formatValue(field.value)}</div>
                                        <button
                                            className="copy-btn"
                                            onClick={() => copyToClipboard(String(field.value), field.label)}
                                            title="Copy to clipboard"
                                        >
                                            {copiedField === field.label ? (
                                                <Check size={16} />
                                            ) : (
                                                <Copy size={16} />
                                            )}
                                        </button>
                                    </div>
                                </div>
                            ))}
                        </div>
                    </div>
                );
            })}
        </div>
    );
}
