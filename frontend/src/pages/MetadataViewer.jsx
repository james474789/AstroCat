import { useState, useEffect } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { ArrowLeft } from 'lucide-react';
import { fetchImage, API_BASE_URL, formatSubtype } from '../api/client';
import MetadataSummaryTab from '../components/metadata/MetadataSummaryTab';
import MetadataDetailsTab from '../components/metadata/MetadataDetailsTab';
import MetadataRawTab from '../components/metadata/MetadataRawTab';
import MetadataExportTab from '../components/metadata/MetadataExportTab';
import './MetadataViewer.css';

export default function MetadataViewer() {
    const { id } = useParams();
    const navigate = useNavigate();
    const [activeTab, setActiveTab] = useState('summary');
    const [image, setImage] = useState(null);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    // Fetch image metadata
    useEffect(() => {
        async function loadImage() {
            try {
                setLoading(true);
                const data = await fetchImage(id);
                setImage(data);
            } catch (err) {
                setError('Failed to load image metadata');
                console.error(err);
            } finally {
                setLoading(false);
            }
        }
        loadImage();
    }, [id]);

    // Fetch raw headers
    const { data: headerData, isLoading: headerLoading } = useQuery({
        queryKey: ['fits-header', id],
        queryFn: async () => {
            const res = await axios.get(`${API_BASE_URL}/images/${id}/fits`);
            return res.data;
        },
        enabled: !!id && activeTab === 'raw',
    });

    if (loading) {
        return (
            <div className="metadata-viewer-page">
                <div className="loading-state">Loading metadata...</div>
            </div>
        );
    }

    if (error || !image) {
        return (
            <div className="metadata-viewer-page">
                <div className="error-state">{error || 'Image not found'}</div>
            </div>
        );
    }

    const fileTypeLabel = {
        'FITS': '🔬 FITS',
        'FIT': '🔬 FIT',
        'CR2': '📷 Canon RAW',
        'CR3': '📷 Canon RAW',
        'ARW': '📷 Sony RAW',
        'NEF': '📷 Nikon RAW',
        'DNG': '📷 DNG',
        'JPG': '🖼️ JPEG',
        'JPEG': '🖼️ JPEG',
        'PNG': '🖼️ PNG',
        'TIFF': '🖼️ TIFF',
        'TIF': '🖼️ TIFF',
        'XISF': '🌌 XISF',
    }[image.file_format] || `📄 ${image.file_format}`;

    return (
        <div className="metadata-viewer-page">
            {/* Header */}
            <div className="metadata-header">
                <div className="header-top">
                    <button className="btn-back" onClick={() => navigate(-1)}>
                        <ArrowLeft size={20} />
                    </button>
                    <div className="header-content">
                        <h1 className="page-title">Metadata Viewer</h1>
                        <p className="page-subtitle">Detailed metadata for: {image.file_name}</p>
                    </div>
                    <div className="file-badge">{fileTypeLabel}</div>
                </div>

                {/* Status Info */}
                <div className="header-status">
                    <div className="status-item">
                        <span className="status-label">Plate Solved:</span>
                        <span className={`status-value ${image.is_plate_solved ? 'solved' : 'unsolved'}`}>
                            {image.is_plate_solved ? '✓ Yes' : '✗ No'}
                        </span>
                    </div>
                    <div className="status-item">
                        <span className="status-label">Classification:</span>
                        <span className="status-value">{formatSubtype(image.subtype)}</span>
                    </div>
                    <div className="status-item">
                        <span className="status-label">Rating:</span>
                        <span className="status-value">
                            {image.rating ? `⭐ ${image.rating}` : '—'}
                        </span>
                    </div>
                    {image.capture_date && (
                        <div className="status-item">
                            <span className="status-label">Captured:</span>
                            <span className="status-value">
                                {new Date(image.capture_date).toLocaleDateString()}
                            </span>
                        </div>
                    )}
                </div>
            </div>

            {/* Tab Navigation */}
            <div className="tabs-container">
                <div className="tab-buttons">
                    <button
                        className={`tab-button ${activeTab === 'summary' ? 'active' : ''}`}
                        onClick={() => setActiveTab('summary')}
                    >
                        📋 Summary
                    </button>
                    <button
                        className={`tab-button ${activeTab === 'details' ? 'active' : ''}`}
                        onClick={() => setActiveTab('details')}
                    >
                        📊 Details
                    </button>
                    <button
                        className={`tab-button ${activeTab === 'raw' ? 'active' : ''}`}
                        onClick={() => setActiveTab('raw')}
                    >
                        🔍 Raw Headers
                    </button>
                    <button
                        className={`tab-button ${activeTab === 'export' ? 'active' : ''}`}
                        onClick={() => setActiveTab('export')}
                    >
                        💾 Export
                    </button>
                </div>
            </div>

            {/* Tab Content */}
            <div className="tab-content-container">
                {activeTab === 'summary' && (
                    <MetadataSummaryTab image={image} />
                )}

                {activeTab === 'details' && (
                    <MetadataDetailsTab image={image} headerData={headerData} />
                )}

                {activeTab === 'raw' && (
                    headerLoading ? (
                        <div className="loading-state">Loading raw headers...</div>
                    ) : (
                        <MetadataRawTab headerData={headerData} fileName={image.file_name} />
                    )
                )}

                {activeTab === 'export' && (
                    <MetadataExportTab image={image} headerData={headerData} fileName={image.file_name} />
                )}
            </div>

            {/* Image Info Preview */}
            <div className="metadata-footer">
                <div className="footer-item">
                    <span className="footer-label">File Size:</span>
                    <span className="footer-value">
                        {image.file_size_bytes ? `${(image.file_size_bytes / (1024 * 1024)).toFixed(2)} MB` : 'Unknown'}
                    </span>
                </div>
                <div className="footer-item">
                    <span className="footer-label">Dimensions:</span>
                    <span className="footer-value">
                        {image.width_pixels && image.height_pixels ? `${image.width_pixels} × ${image.height_pixels} px` : 'Unknown'}
                    </span>
                </div>
                {image.exposure_time_seconds && (
                    <div className="footer-item">
                        <span className="footer-label">Exposure:</span>
                        <span className="footer-value">{image.exposure_time_seconds.toFixed(2)}s</span>
                    </div>
                )}
                {image.camera_name && (
                    <div className="footer-item">
                        <span className="footer-label">Camera:</span>
                        <span className="footer-value">{image.camera_name}</span>
                    </div>
                )}
            </div>
        </div>
    );
}
