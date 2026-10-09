import { useState } from 'react';
import { Link } from 'react-router-dom';
import { formatExposure, formatFrameTypeBadge, API_BASE_URL } from '../../api/client';
import { MoreHorizontal, Orbit } from 'lucide-react';
import RatingStars from './RatingStars';
import './ImageCard.css';
import QualityValue from '../quality/QualityValue';

// Subtype pill labels
const subtypeBadges = {
    'SUB_FRAME': 'Sub',
    'INTEGRATION_MASTER': 'Master',
    'INTEGRATION_DEPRECATED': 'Old',
    'PLANETARY': 'Planetary',
    'ALLSKY': 'All-sky',
    'AURORA': 'Aurora',
};

export default function ImageCard({ image, onContextMenu, showQuality = false }) {
    const [imageLoaded, setImageLoaded] = useState(false);
    const [imageError, setImageError] = useState(false);

    const badge = image.subtype ? subtypeBadges[image.subtype] : null;
    // Frame type calibration badge (F1): shown for anything that isn't a LIGHT frame.
    const isCalibrationFrame = image.frame_type && image.frame_type !== 'LIGHT';

    const showSolved = image.is_plate_solved && !['PLANETARY', 'ALLSKY', 'AURORA'].includes(image.subtype);

    // Title: first catalog match, falling back to the file name (full name stays in the tooltip).
    const firstMatch = image.catalog_matches?.[0];
    const title = firstMatch?.catalog_designation || firstMatch?.designation || image.file_name;

    // One subhead line: exposure · filter · camera model · FWHM (parts skipped when missing).
    const subhead = [];
    if (image.exposure_time_seconds != null) subhead.push(formatExposure(image.exposure_time_seconds));
    if (image.filter_name) subhead.push(image.filter_name);
    if (image.camera_name) subhead.push(image.camera_name);
    if (showQuality && image.star_metrics_status === 'OK' && image.fwhm_px != null) {
        subhead.push(
            <span title={`FWHM${image.eccentricity != null ? ` · eccentricity ${image.eccentricity.toFixed(2)}` : ''}${image.star_count != null ? ` · ${image.star_count} stars` : ''}`}>
                <QualityValue px={image.fwhm_px}
                    arcsec={image.pixel_scale_arcsec > 0 ? image.fwhm_px * image.pixel_scale_arcsec : null} /> FWHM
            </span>
        );
    }

    // Generate a gradient placeholder based on image ID
    const generatePlaceholder = (id) => {
        const hue1 = (id * 37) % 360;
        const hue2 = (hue1 + 40) % 360;
        return `linear-gradient(135deg, hsl(${hue1}, 50%, 20%) 0%, hsl(${hue2}, 60%, 10%) 100%)`;
    };

    return (
        <Link
            to={`/images/${image.id}`}
            className="image-card"
            id={`image-${image.id}`}
            onClick={() => sessionStorage.setItem('lastClickedImageId', image.id)}
            onContextMenu={(e) => onContextMenu && onContextMenu(e, image)}
        >
            {onContextMenu && (
                <button
                    type="button"
                    className="image-card-more"
                    aria-label="Image actions"
                    onClick={(e) => {
                        e.preventDefault();
                        e.stopPropagation();
                        const r = e.currentTarget.getBoundingClientRect();
                        onContextMenu({ preventDefault() {}, clientX: r.left, clientY: r.bottom }, image);
                    }}
                >
                    <MoreHorizontal size={16} />
                </button>
            )}
            <div className="image-card-thumbnail">
                {(!imageLoaded || imageError) && (
                    <div
                        className="image-placeholder"
                        style={{ background: generatePlaceholder(image.id) }}
                    >
                        <div className="image-placeholder-icon"><Orbit size={48} strokeWidth={1.5} /></div>
                    </div>
                )}

                <img
                    src={`${API_BASE_URL}/images/${image.id}/thumbnail?t=${image.thumbnail_generated_at ? new Date(image.thumbnail_generated_at).getTime() : ''}`}
                    alt={image.file_name}
                    className="image-card-img"
                    style={{
                        opacity: imageLoaded ? 1 : 0,
                        position: 'absolute',
                        top: 0,
                        left: 0,
                        width: '100%',
                        height: '100%',
                        objectFit: 'cover'
                    }}
                    onLoad={() => setImageLoaded(true)}
                    onError={() => setImageError(true)}
                />

                {/* Status pills on a blurred material */}
                <div className="image-card-badges">
                    {badge && (
                        <span className="image-badge">{badge}</span>
                    )}
                    {isCalibrationFrame && (
                        <span className="image-badge">{formatFrameTypeBadge(image.frame_type)}</span>
                    )}
                    {showSolved && (
                        <span className="image-badge badge-solved">
                            <span className="image-badge-dot" aria-hidden="true" />
                            {['HEADER', 'SIDECAR'].includes(image.plate_solve_source) ? 'Solve Imported' : 'Solved'}
                        </span>
                    )}
                </div>

                {/* Rating Stars */}
                <RatingStars rating={image.rating} />
            </div>

            <div className="image-card-content">
                <h4 className="image-card-title" title={image.file_name}>
                    {title}
                </h4>
                {subhead.length > 0 && (
                    <p className="image-card-subhead">
                        {subhead.map((part, i) => (
                            <span key={i}>{i > 0 && ' · '}{part}</span>
                        ))}
                    </p>
                )}
            </div>
        </Link>
    );
}
