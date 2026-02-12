import { Download, Check } from 'lucide-react';
import { useState } from 'react';
import './MetadataTab.css';

export default function MetadataExportTab({ image, headerData, fileName }) {
    const [copied, setCopied] = useState(false);
    const [exportFormat, setExportFormat] = useState('json');

    const generateJSON = () => {
        const data = {
            file: {
                name: image.file_name,
                format: image.file_format,
                size: image.file_size_bytes,
                path: image.file_path,
                created: image.file_created,
                modified: image.file_last_modified,
                hash: image.file_hash,
                indexed: image.indexed_at,
                updated: image.updated_at,
                sidecar: image.sidecar_path,
            },
            observation: {
                object: image.object_name,
                observer: image.observer_name,
                site: image.site_name,
                captureDate: image.capture_date,
            },
            plateSolving: {
                solved: image.is_plate_solved,
                provider: image.plate_solve_provider,
                ra: image.ra_center_degrees,
                dec: image.dec_center_degrees,
                fieldOfView: image.field_radius_degrees,
                pixelScale: image.pixel_scale_arcsec,
                rotation: image.rotation_degrees,
            },
            exposure: {
                exposureTime: image.exposure_time_seconds,
                gain: image.gain,
                iso: image.iso_speed,
                binning: image.binning,
                temperature: image.temperature_celsius,
                filter: image.filter_name,
            },
            equipment: {
                camera: image.camera_name,
                telescope: image.telescope_name,
                focalLength: image.focal_length,
                aperture: image.aperture,
            },
            classification: {
                rating: image.rating,
                subtype: image.subtype,
            },
            rawHeaders: headerData || {},
        };
        return JSON.stringify(data, null, 2);
    };

    const generateCSV = () => {
        let csv = 'Field,Value\n';

        const rows = [
            ['File Name', image.file_name],
            ['File Format', image.file_format],
            ['File Size (MB)', image.file_size_bytes ? (image.file_size_bytes / (1024 * 1024)).toFixed(2) : ''],
            ['File Path', image.file_path],
            ['File Created', image.file_created || ''],
            ['File Modified', image.file_last_modified || ''],
            ['Indexed At', image.indexed_at || ''],
            ['Last Updated', image.updated_at || ''],
            ['', ''],
            ['Object Name', image.object_name || ''],
            ['Observer', image.observer_name || ''],
            ['Site', image.site_name || ''],
            ['Capture Date', image.capture_date || ''],
            ['', ''],
            ['Plate Solved', image.is_plate_solved ? 'Yes' : 'No'],
            ['Plate Solve Provider', image.plate_solve_provider || ''],
            ['Right Ascension', image.ra_center_degrees || ''],
            ['Declination', image.dec_center_degrees || ''],
            ['Field of View', image.field_radius_degrees || ''],
            ['Pixel Scale', image.pixel_scale_arcsec || ''],
            ['Rotation', image.rotation_degrees || ''],
            ['', ''],
            ['Exposure Time (s)', image.exposure_time_seconds || ''],
            ['Gain', image.gain || ''],
            ['ISO', image.iso_speed || ''],
            ['Binning', image.binning || ''],
            ['Temperature (°C)', image.temperature_celsius || ''],
            ['Filter', image.filter_name || ''],
            ['', ''],
            ['Camera', image.camera_name || ''],
            ['Telescope', image.telescope_name || ''],
            ['Focal Length (mm)', image.focal_length || ''],
            ['Aperture (f/)', image.aperture || ''],
            ['', ''],
            ['Rating', image.rating || ''],
            ['Classification', image.subtype || ''],
        ];

        rows.forEach(([field, value]) => {
            const escapedValue = String(value || '').includes(',') || String(value || '').includes('"')
                ? `"${String(value).replace(/"/g, '""')}"`
                : value;
            csv += `${field},"${escapedValue}"\n`;
        });

        return csv;
    };

    const generateText = () => {
        let text = `METADATA EXPORT: ${image.file_name}\n`;
        text += `${'='.repeat(80)}\n\n`;

        text += `FILE INFORMATION\n${'-'.repeat(40)}\n`;
        text += `Name:     ${image.file_name}\n`;
        text += `Format:   ${image.file_format}\n`;
        text += `Size:     ${image.file_size_bytes ? (image.file_size_bytes / (1024 * 1024)).toFixed(2) : 'N/A'} MB\n`;
        text += `Path:     ${image.file_path}\n`;
        text += `Created:  ${image.file_created || 'N/A'}\n`;
        text += `Modified: ${image.file_last_modified || 'N/A'}\n`;
        text += `Indexed:  ${image.indexed_at || 'N/A'}\n`;
        text += `Updated:  ${image.updated_at || 'N/A'}\n\n`;

        text += `OBSERVATIONAL DATA\n${'-'.repeat(40)}\n`;
        text += `Object:   ${image.object_name || 'N/A'}\n`;
        text += `Observer: ${image.observer_name || 'N/A'}\n`;
        text += `Site:     ${image.site_name || 'N/A'}\n`;
        text += `Date:     ${image.capture_date || 'N/A'}\n\n`;

        text += `PLATE SOLVING\n${'-'.repeat(40)}\n`;
        text += `Status:   ${image.is_plate_solved ? 'SOLVED' : 'NOT SOLVED'}\n`;
        text += `Provider: ${image.plate_solve_provider || 'N/A'}\n`;
        text += `RA:       ${image.ra_center_degrees?.toFixed(4) || 'N/A'}°\n`;
        text += `Dec:      ${image.dec_center_degrees?.toFixed(4) || 'N/A'}°\n`;
        text += `FOV:      ${image.field_radius_degrees?.toFixed(4) || 'N/A'}° (radius)\n`;
        text += `Scale:    ${image.pixel_scale_arcsec?.toFixed(4) || 'N/A'} arcsec/px\n`;
        text += `Rotation: ${image.rotation_degrees?.toFixed(2) || 'N/A'}°\n\n`;

        text += `EXPOSURE SETTINGS\n${'-'.repeat(40)}\n`;
        text += `Time:     ${image.exposure_time_seconds ? image.exposure_time_seconds.toFixed(3) : 'N/A'}s\n`;
        text += `Gain:     ${image.gain || 'N/A'}\n`;
        text += `ISO:      ${image.iso_speed || 'N/A'}\n`;
        text += `Binning:  ${image.binning || 'N/A'}\n`;
        text += `Temp:     ${image.temperature_celsius || 'N/A'}°C\n`;
        text += `Filter:   ${image.filter_name || 'N/A'}\n\n`;

        text += `EQUIPMENT\n${'-'.repeat(40)}\n`;
        text += `Camera:   ${image.camera_name || 'N/A'}\n`;
        text += `Telescope:${image.telescope_name || 'N/A'}\n`;
        text += `Focal Len:${image.focal_length || 'N/A'} mm\n`;
        text += `Aperture: f/${image.aperture || 'N/A'}\n\n`;

        text += `QUALITY\n${'-'.repeat(40)}\n`;
        text += `Rating:   ${image.rating || 'N/A'} stars\n`;
        text += `Class:    ${image.subtype || 'N/A'}\n`;

        return text;
    };

    const exportData = {
        json: { name: 'metadata.json', data: generateJSON(), mime: 'application/json' },
        csv: { name: `${image.file_name.replace(/\.[^.]*$/, '')}_metadata.csv`, data: generateCSV(), mime: 'text/csv' },
        text: { name: `${image.file_name.replace(/\.[^.]*$/, '')}_metadata.txt`, data: generateText(), mime: 'text/plain' },
    };

    const handleDownload = (format) => {
        const { name, data, mime } = exportData[format];
        const blob = new Blob([data], { type: mime });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = name;
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);
    };

    const handleCopyToClipboard = (format) => {
        const data = exportData[format].data;
        navigator.clipboard.writeText(data);
        setCopied(true);
        setTimeout(() => setCopied(false), 2000);
    };

    return (
        <div className="metadata-export-tab">
            <div className="export-container">
                <h3>Export Metadata</h3>
                <p className="export-description">
                    Download or copy metadata in your preferred format
                </p>

                <div className="export-options">
                    {['json', 'csv', 'text'].map(format => (
                        <div key={format} className="export-card">
                            <div className="format-info">
                                <h4>{format.toUpperCase()}</h4>
                                <p className="format-desc">
                                    {format === 'json' && 'Structured format, ideal for APIs and data interchange'}
                                    {format === 'csv' && 'Spreadsheet format, easy to analyze and compare'}
                                    {format === 'text' && 'Human-readable format, good for documentation'}
                                </p>
                            </div>
                            <div className="format-actions">
                                <button
                                    className="btn btn-secondary btn-sm"
                                    onClick={() => handleDownload(format)}
                                >
                                    <Download size={16} />
                                    Download
                                </button>
                                <button
                                    className="btn btn-secondary btn-sm"
                                    onClick={() => handleCopyToClipboard(format)}
                                >
                                    {copied ? <Check size={16} /> : 'Copy'}
                                </button>
                            </div>
                        </div>
                    ))}
                </div>

                <div className="export-preview">
                    <h4>JSON Preview</h4>
                    <pre className="preview-code">
                        {JSON.stringify(
                            {
                                file: { name: image.file_name, format: image.file_format },
                                observation: { object: image.object_name },
                                plateSolving: { solved: image.is_plate_solved, ra: image.ra_center_degrees },
                                '...': '(truncated for preview)'
                            },
                            null,
                            2
                        )}
                    </pre>
                </div>
            </div>
        </div>
    );
}
