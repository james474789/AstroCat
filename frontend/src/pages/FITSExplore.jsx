import { useState, useEffect } from 'react';
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from 'react-router-dom';
import axios from 'axios';
import {
    Search, Filter, Info, ChevronLeft, ChevronRight, AlertTriangle
} from 'lucide-react';
import { API_BASE_URL, fetchImage } from '../api/client';
import { Button, Dialog, EmptyState, PageHeader, Spinner } from '../components/ui';
import './FITSExplore.css';

const FITSExplore = () => {
    const [searchParams] = useSearchParams();
    const [searchKey, setSearchKey] = useState('');
    const [searchValue, setSearchValue] = useState('');
    const [page, setPage] = useState(1);
    const [selectedImage, setSelectedImage] = useState(null);

    // Deep linking support
    useEffect(() => {
        const imageId = searchParams.get('image_id');
        if (imageId) {
            fetchImage(imageId)
                .then(img => setSelectedImage(img))
                .catch(err => console.error("Failed to load deep linked image:", err));
        }
    }, [searchParams]);

    // Fetch images with header filter
    const { data, isLoading, isError } = useQuery({
        queryKey: ['fits-images', page, searchKey, searchValue],
        queryFn: async () => {
            const params = {
                page,
                page_size: 20
            };

            if (searchKey) params.header_key = searchKey;
            if (searchValue) params.header_value = searchValue;

            const res = await axios.get(`${API_BASE_URL}/images/`, { params });
            return res.data;
        },
        keepPreviousData: true
    });

    // Fetch full header for selected image
    const { data: headerData, isLoading: headerLoading } = useQuery({
        queryKey: ['fits-header', selectedImage?.id],
        queryFn: async () => {
            if (!selectedImage) return null;
            const res = await axios.get(`${API_BASE_URL}/images/${selectedImage.id}/fits`);
            return res.data;
        },
        enabled: !!selectedImage
    });

    // Debounce search input? (Optional, simplistic for now)
    const handleSearch = (e) => {
        e.preventDefault();
        setPage(1); // Reset to first page on search
    };

    return (
        <div className="page-fits-explore">
            <PageHeader
                title="Metadata Explore"
                subtitle="Deep dive into image metadata across your entire collection"
            />

            {/* Search Bar */}
            <div className="search-container">
                <form onSubmit={handleSearch} className="search-form">
                    <div className="input-group">
                        <label>Header Key</label>
                        <div className="input-wrapper">
                            <Search size={18} />
                            <input
                                type="text"
                                placeholder="e.g. INSTRUME, TELESCOP..."
                                value={searchKey}
                                onChange={(e) => setSearchKey(e.target.value)}
                            />
                        </div>
                    </div>

                    <div className="input-group">
                        <label>Value (Optional)</label>
                        <div className="input-wrapper">
                            <Filter size={18} />
                            <input
                                type="text"
                                placeholder="Filter value..."
                                value={searchValue}
                                onChange={(e) => setSearchValue(e.target.value)}
                            />
                        </div>
                    </div>
                </form>
            </div>

            {/* Results Table */}
            <div className="results-container">
                {isLoading ? (
                    <Spinner label="Scanning database" />
                ) : isError ? (
                    <EmptyState
                        icon={<AlertTriangle size={32} aria-hidden="true" />}
                        title="Failed to load data"
                    />
                ) : (
                    <>
                        <div className="table-wrapper">
                            <table className="fits-table">
                                <thead>
                                    <tr>
                                        <th>Image Name</th>
                                        <th>Captured</th>
                                        <th>Modified</th>
                                        <th>Camera</th>
                                        <th>Object</th>
                                        <th>Actions</th>
                                    </tr>
                                </thead>
                                <tbody>
                                    {data?.items.map((img) => (
                                        <tr key={img.id} onClick={() => setSelectedImage(img)} className="clickable-row">
                                            <td className="font-medium">{img.file_name}</td>
                                            <td>
                                                {img.capture_date ? new Date(img.capture_date).toLocaleDateString() : '-'}
                                            </td>
                                            <td>
                                                {img.file_last_modified ? new Date(img.file_last_modified).toLocaleDateString() : '-'}
                                            </td>
                                            <td>{img.camera_name || '-'}</td>
                                            <td>{img.object_name || '-'}</td>
                                            <td>
                                                <Button
                                                    variant="plain"
                                                    size="sm"
                                                    iconOnly
                                                    icon={<Info size={18} />}
                                                    aria-label={`View headers for ${img.file_name}`}
                                                    onClick={(e) => {
                                                        e.stopPropagation();
                                                        setSelectedImage(img);
                                                    }}
                                                />
                                            </td>
                                        </tr>
                                    ))}
                                    {data?.items.length === 0 && (
                                        <tr>
                                            <td colSpan="5" className="text-center p-8">No matching headers found.</td>
                                        </tr>
                                    )}
                                </tbody>
                            </table>
                        </div>

                        {/* Pagination */}
                        <div className="pagination">
                            <Button
                                variant="tinted"
                                iconOnly
                                icon={<ChevronLeft size={20} />}
                                aria-label="Previous page"
                                disabled={page === 1}
                                onClick={() => setPage(p => Math.max(1, p - 1))}
                            />
                            <span>Page {page} of {data?.total_pages || 1}</span>
                            <Button
                                variant="tinted"
                                iconOnly
                                icon={<ChevronRight size={20} />}
                                aria-label="Next page"
                                disabled={page >= (data?.total_pages || 1)}
                                onClick={() => setPage(p => p + 1)}
                            />
                        </div>
                    </>
                )}
            </div>

            {/* Detail dialog (portalled to <body>, so its styles are scoped by className, not the page root) */}
            <Dialog
                open={!!selectedImage}
                onClose={() => setSelectedImage(null)}
                title={selectedImage?.file_name ?? ''}
                size="lg"
                className="dlg-fits-explore-headers"
            >
                {headerLoading ? (
                    <Spinner label="Loading headers" />
                ) : headerData ? (
                    <div className="header-grid">
                        {Object.entries(headerData)
                            .sort(([keyA], [keyB]) => keyA.localeCompare(keyB))
                            .map(([key, value]) => (
                                <div key={key} className="header-item">
                                    <span className="key">{key}</span>
                                    <span className="value" title={typeof value === 'object' ? JSON.stringify(value, null, 2) : String(value)}>
                                        {typeof value === 'object' ? JSON.stringify(value) : String(value)}
                                    </span>
                                </div>
                            ))}
                    </div>
                ) : (
                    <EmptyState title="No raw header data stored for this image." />
                )}
            </Dialog>
        </div>
    );
};

export default FITSExplore;
