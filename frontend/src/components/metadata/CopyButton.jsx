import { useEffect, useRef, useState } from 'react';
import { Copy, Check } from 'lucide-react';
import { Button } from '../ui';

// Icon button that copies `value` and shows a check for 2s. `label` names what is copied.
export default function CopyButton({ value, label }) {
    const [copied, setCopied] = useState(false);
    const timer = useRef(null);
    useEffect(() => () => clearTimeout(timer.current), []);

    const copy = () => {
        navigator.clipboard?.writeText(value).then(() => {
            setCopied(true);
            clearTimeout(timer.current);
            timer.current = setTimeout(() => setCopied(false), 2000);
        }, () => {});
    };

    return (
        <Button
            variant="plain"
            size="sm"
            iconOnly
            className="inspector-copy"
            aria-label={`Copy ${label}`}
            icon={copied ? <Check size={14} aria-hidden="true" /> : <Copy size={14} aria-hidden="true" />}
            onClick={copy}
        />
    );
}
