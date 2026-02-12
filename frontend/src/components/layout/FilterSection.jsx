import { useState } from 'react';
import './FilterSection.css';

/**
 * Collapsible filter section component
 * Groups related filters together with expand/collapse functionality
 */
export default function FilterSection({ 
    title, 
    icon, 
    children, 
    defaultOpen = true,
    badge = null 
}) {
    const [isOpen, setIsOpen] = useState(defaultOpen);

    return (
        <div className="filter-section">
            <button 
                className="filter-section-header"
                onClick={() => setIsOpen(!isOpen)}
                type="button"
            >
                <div className="filter-section-title">
                    {icon && <span className="filter-section-icon">{icon}</span>}
                    <span>{title}</span>
                    {badge && <span className="filter-section-badge">{badge}</span>}
                </div>
                <span className={`filter-section-toggle ${isOpen ? 'open' : ''}`}>
                    ▼
                </span>
            </button>
            
            {isOpen && (
                <div className="filter-section-content">
                    {children}
                </div>
            )}
        </div>
    );
}
