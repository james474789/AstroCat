import { useState } from 'react';
import { Link } from 'react-router-dom';
import { formatExposure, formatBytes, formatDate, API_BASE_URL } from '../../api/client';
import RatingStars from './RatingStars';
import './ImageCard.css';

// Subtype badge colors
const subtypeBadges = {
    'SUB_FRAME': { label: 'Sub', className: 'badge-sub' },
    'INTEGRATION_MASTER': { label: 'Master', className: 'badge-master' },
    'INTEGRATION_DEPRECATED': { label: 'Old', className: 'badge-deprecated' },
    'PLANETARY': { label: 'Planetary', className: 'badge-planetary' },
};

export default function ImageCard({ image, onContextMenu }) {
    const [imageLoaded, setImageLoaded] = useState(false);
    const [imageError, setImageError] = useState(false);

    const badge = image.subtype ? subtypeBadges[image.subtype] : null;

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
            <div className="image-card-thumbnail">
                {(!imageLoaded || imageError) && (
                    <div
                        className="image-placeholder"
                        style={{ background: generatePlaceholder(image.id) }}
                    >
                        <div className="image-placeholder-icon">🌌</div>
                    </div>
                )}

                <img
                    src={`${API_BASE_URL}/images/${image.id}/thumbnail`}
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

                {/* Overlay with info */}
                <div className="image-card-overlay">
                    <div className="image-card-badges">
                        {badge && (
                            <span className={`image-badge ${badge.className}`}>
                                {badge.label}
                            </span>
                        )}
                        {image.is_plate_solved && image.subtype !== 'PLANETARY' && (
                            <span className="image-badge badge-solved">
                                {['HEADER', 'SIDECAR'].includes(image.plate_solve_source) ? '✓ Solve Imported' : '✓ Solved'}
                            </span>
                        )}
                    </div>
                </div>

                {/* Rating Stars */}
                <RatingStars rating={image.rating} />
            </div>

            <div className="image-card-content">
                <h4 className="image-card-title" title={image.file_name}>
                    {image.file_name}
                </h4>

                <div className="image-card-meta">
                    <span className="meta-item">
                        <span className="meta-icon">⏱</span>
                        {formatExposure(image.exposure_time_seconds || 0)}
                    </span>
                    <span className="meta-item">
                        <span className="meta-icon">📷</span>
                        {image.camera?.split(' ')[0] || 'Unknown'}
                    </span>
                </div>

                {image.catalog_matches && image.catalog_matches.length > 0 && (
                    <div className="image-card-objects">
                        {image.catalog_matches.slice(0, 3).map((match, idx) => (
                            <span key={idx} className="object-tag">
                                {match.catalog_designation || match.designation}
                            </span>
                        ))}
                        {image.catalog_matches.length > 3 && (
                            <span className="object-tag object-tag-more">
                                +{image.catalog_matches.length - 3}
                            </span>
                        )}
                    </div>
                )}

                <div className="image-card-footer">
                    <span className="meta-date" title={`Modified: ${formatDate(image.file_last_modified)}`}>
                        {formatDate(image.capture_date || image.file_last_modified)}
                    </span>
                    <span className="meta-format">{image.file_format}</span>
                </div>
            </div>
        </Link>
    );
}
