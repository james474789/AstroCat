import { Search } from 'lucide-react';
import { useState } from 'react';
import { EmptyState } from '../ui';
import CopyButton from './CopyButton';

export default function MetadataRawTab({ headerData }) {
    const [searchTerm, setSearchTerm] = useState('');

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
            <div className="metadata-tab metadata-raw-tab">
                <EmptyState title="No raw header data available for this image." />
            </div>
        );
    }

    return (
        <div className="metadata-tab metadata-raw-tab">
            <div className="raw-search-container">
                <Search size={18} aria-hidden="true" />
                <input
                    type="text"
                    aria-label="Search headers"
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
                    <EmptyState title="No headers match your search." />
                ) : (
                    <table className="raw-headers-table">
                        <thead>
                            <tr>
                                <th className="th-key">Key</th>
                                <th className="th-value">Value</th>
                                <th className="th-action"><span className="ui-visually-hidden">Copy</span></th>
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
                                            <CopyButton value={displayValue} label={`${key} value`} />
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
