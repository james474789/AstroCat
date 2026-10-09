import { Loader2 } from 'lucide-react';
import './ui.css';

// Announces `label` to screen readers (role=status) while showing only the spinning icon.
export default function Spinner({ size = 24, label = 'Loading', className = '' }) {
    return (
        <span role="status" className={`ui-spinner ${className}`.trim()}>
            <Loader2 className="ui-spin" size={size} aria-hidden="true" />
            <span className="ui-visually-hidden">{label}</span>
        </span>
    );
}
