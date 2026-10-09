import { useEffect, useRef, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import axios from 'axios';
import { Microscope, Camera, Image as ImageIcon, Orbit, FileText, X } from 'lucide-react';
import { API_BASE_URL } from '../../api/client';
import { Button, Spinner, Tabs, TabPanel } from '../ui';
import MetadataSummaryTab from './MetadataSummaryTab';
import MetadataDetailsTab from './MetadataDetailsTab';
import MetadataRawTab from './MetadataRawTab';
import MetadataExportTab from './MetadataExportTab';
import './Inspector.css';

const TAB_STORAGE_KEY = 'astrocat.inspectorTab';
// Below this width the Inspector is a sheet over the page instead of a docked column (see Inspector.css)
const OVERLAY_QUERY = '(max-width: 1023px)';

const TAB_ITEMS = [
    { value: 'summary', label: 'Summary' },
    { value: 'details', label: 'Details' },
    { value: 'raw', label: 'Raw', ariaLabel: 'Raw headers' },
    { value: 'export', label: 'Export' },
];

const FILE_TYPE_INFO = {
    FITS: [Microscope, 'FITS'],
    FIT: [Microscope, 'FIT'],
    CR2: [Camera, 'Canon RAW'],
    CR3: [Camera, 'Canon RAW'],
    ARW: [Camera, 'Sony RAW'],
    NEF: [Camera, 'Nikon RAW'],
    DNG: [Camera, 'DNG'],
    JPG: [ImageIcon, 'JPEG'],
    JPEG: [ImageIcon, 'JPEG'],
    PNG: [ImageIcon, 'PNG'],
    TIFF: [ImageIcon, 'TIFF'],
    TIF: [ImageIcon, 'TIFF'],
    XISF: [Orbit, 'XISF'],
};

function readTab() {
    try {
        const stored = localStorage.getItem(TAB_STORAGE_KEY);
        return TAB_ITEMS.some((t) => t.value === stored) ? stored : 'summary';
    } catch {
        return 'summary';
    }
}

/**
 * Image Inspector: the metadata tabs (Summary, Details, Raw headers, Export) for one image,
 * docked to the right of ImageDetail on desktop and shown as a sheet on narrow screens.
 * Render it only while open; `onClose` closes it. `id` goes on the panel (for aria-controls).
 */
export default function Inspector({ image, onClose, id }) {
    const [tab, setTab] = useState(readTab);
    const panelRef = useRef(null);

    const selectTab = (value) => {
        setTab(value);
        try {
            localStorage.setItem(TAB_STORAGE_KEY, value);
        } catch {
            // per-browser convenience only
        }
    };

    // FITS/EXIF headers: needed by Raw headers and included in the JSON export
    const { data: headerData, isLoading: headerLoading } = useQuery({
        queryKey: ['fits-header', image.id],
        queryFn: async () => (await axios.get(`${API_BASE_URL}/images/${image.id}/fits`)).data,
        enabled: tab === 'raw' || tab === 'export',
    });

    // As an overlay sheet, take focus while open and hand it back on close
    useEffect(() => {
        if (!window.matchMedia?.(OVERLAY_QUERY).matches) return undefined;
        const opener = document.activeElement;
        panelRef.current?.focus();
        return () => {
            if (opener?.isConnected) opener.focus();
        };
    }, []);

    const [FileTypeIcon, fileTypeText] = FILE_TYPE_INFO[image.file_format] || [FileText, image.file_format];

    return (
        <div className="inspector">
            <div className="inspector-scrim" aria-hidden="true" onClick={onClose} />
            <aside id={id} className="inspector-panel" aria-label="Inspector" ref={panelRef} tabIndex={-1}>
                <header className="inspector-header">
                    <h2 className="inspector-title">Inspector</h2>
                    {fileTypeText && (
                        <span className="inspector-format">
                            <FileTypeIcon size={14} aria-hidden="true" /> {fileTypeText}
                        </span>
                    )}
                    <Button
                        variant="plain"
                        size="sm"
                        iconOnly
                        className="inspector-close"
                        icon={<X size={18} aria-hidden="true" />}
                        aria-label="Close inspector"
                        title="Close inspector (I)"
                        onClick={onClose}
                    />
                </header>
                <Tabs
                    className="inspector-tabs"
                    aria-label="Metadata sections"
                    items={TAB_ITEMS}
                    value={tab}
                    onChange={selectTab}
                    panelIdPrefix="inspector"
                />
                <TabPanel panelIdPrefix="inspector" value={tab} className="inspector-body">
                    {tab === 'summary' && <MetadataSummaryTab image={image} />}
                    {tab === 'details' && <MetadataDetailsTab image={image} />}
                    {tab === 'raw' && (headerLoading
                        ? <div className="inspector-loading"><Spinner label="Loading raw headers" /></div>
                        : <MetadataRawTab headerData={headerData} />)}
                    {tab === 'export' && <MetadataExportTab image={image} headerData={headerData} />}
                </TabPanel>
            </aside>
        </div>
    );
}
