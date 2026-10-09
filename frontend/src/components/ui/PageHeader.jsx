import formatCount from './formatCount';
import './ui.css';

// The page's single <h1>, with optional icon, count, subtitle and a toolbar slot for actions.
export default function PageHeader({ title, subtitle, count, icon, actions, children, className = '' }) {
    return (
        <header className={`ui-page-header ${className}`.trim()}>
            <div className="ui-page-header-bar">
                <div className="ui-page-heading">
                    <div className="ui-page-title-row">
                        {icon && <span className="ui-page-icon" aria-hidden="true">{icon}</span>}
                        <h1 className="ui-page-title">{title}</h1>
                        {count != null && <span className="ui-page-count">{formatCount(count)}</span>}
                    </div>
                    {subtitle && <p className="ui-page-subtitle">{subtitle}</p>}
                </div>
                {actions && <div className="ui-page-actions">{actions}</div>}
            </div>
            {children}
        </header>
    );
}
