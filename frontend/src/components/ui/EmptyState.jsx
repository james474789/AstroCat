import './ui.css';

// icon: a Lucide element (sized by CSS); action: usually a <Button>.
export default function EmptyState({ icon, title, description, action, className = '' }) {
    return (
        <div className={`ui-empty-state ${className}`.trim()}>
            {icon && <span className="ui-empty-state-icon" aria-hidden="true">{icon}</span>}
            <p className="ui-empty-state-title">{title}</p>
            {description && <p className="ui-empty-state-description">{description}</p>}
            {action && <div className="ui-empty-state-action">{action}</div>}
        </div>
    );
}
