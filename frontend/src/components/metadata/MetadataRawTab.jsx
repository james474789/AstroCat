import { Copy, Check, Search } from 'lucide-react';
import { useState } from 'react';
import './MetadataTab.css';

export default function MetadataRawTab({ headerData, fileName }) {
    const [searchTerm, setSearchTerm] = useState('');
    const [copiedField, setCopiedField] = useState(null);

    const copyToClipboard = (text, fieldName) => {
        navigator.clipboard.writeText(text);
        setCopiedField(fieldName);
        setTimeout(() => setCopiedField(null), 2000);
    };

    // Filter headers based on search
    const filteredHeaders = Object.entries(headerData || {})
        .filter(([key, value]) => {
            const searchLower = searchTerm.toLowerCase();
            return key.toLowerCase().includes(searchLower) ||
                String(value).toLowerCase().includes(searchLower);
        })
        .sort(([keyA], [keyB]) => keyA.localeCompare(keyB));

    if (!headerData || Object.keys(headerData).length === 0) {
        return (
            <div className="metadata-raw-tab">
                <div className="empty-state">
                    <p>No raw header data available for this image.</p>
                </div>
            </div>
        );
    }

    return (
        <div className="metadata-raw-tab">
            <div className="raw-search-container">
                <Search size={18} />
                <input
                    type="text"
                    placeholder="Search headers (key or value)..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    className="raw-search-input"
                />
                <span className="result-count">
                    {filteredHeaders.length} / {Object.keys(headerData).length}
                </span>
            </div>

            <div className="raw-headers-container">
                {filteredHeaders.length === 0 ? (
                    <div className="empty-state">
                        <p>No headers match your search.</p>
                    </div>
                ) : (
                    <table className="raw-headers-table">
                        <thead>
                            <tr>
                                <th className="th-key">Key</th>
                                <th className="th-value">Value</th>
                                <th className="th-action"></th>
                            </tr>
                        </thead>
                        <tbody>
                            {filteredHeaders.map(([key, value]) => {
                                const displayValue = typeof value === 'object'
                                    ? JSON.stringify(value, null, 2)
                                    : String(value);

                                return (
                                    <tr key={key} className="raw-header-row">
                                        <td className="cell-key">
                                            <code>{key}</code>
                                        </td>
                                        <td className="cell-value">
                                            <code title={displayValue}>{displayValue}</code>
                                        </td>
                                        <td className="cell-action">
                                            <button
                                                className="copy-btn-small"
                                                onClick={() => copyToClipboard(displayValue, key)}
                                                title="Copy value"
                                            >
                                                {copiedField === key ? (
                                                    <Check size={14} />
                                                ) : (
                                                    <Copy size={14} />
                                                )}
                                            </button>
                                        </td>
                                    </tr>
                                );
                            })}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
}
