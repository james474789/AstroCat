import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { createPortal } from 'react-dom';
import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react';
import { ToastContext } from './toastContext';
import './ui.css';

const DEFAULT_DURATION_MS = 6000;
const MAX_VISIBLE = 4;
const ICONS = { success: CheckCircle2, error: AlertCircle, info: Info };

function ToastItem({ toast, onDismiss }) {
    const { id, message, type, durationMs, action } = toast;
    const [hovered, setHovered] = useState(false);
    const [focused, setFocused] = useState(false);
    const paused = hovered || focused;

    // Auto-dismiss; hovering or focusing the toast pauses it (the timer restarts on leave).
    useEffect(() => {
        if (paused || !(durationMs > 0)) return undefined;
        const timer = setTimeout(() => onDismiss(id), durationMs);
        return () => clearTimeout(timer);
    }, [paused, durationMs, id, onDismiss]);

    const Icon = ICONS[type] || Info;
    const isError = type === 'error';

    return (
        <div
            className={`ui-toast ui-toast-${type}`}
            role={isError ? 'alert' : 'status'}
            aria-live={isError ? 'assertive' : 'polite'}
            aria-atomic="true"
            onMouseEnter={() => setHovered(true)}
            onMouseLeave={() => setHovered(false)}
            onFocus={() => setFocused(true)}
            onBlur={(event) => {
                if (!event.currentTarget.contains(event.relatedTarget)) setFocused(false);
            }}
        >
            <span className="ui-toast-icon"><Icon size={18} aria-hidden="true" /></span>
            <span className="ui-toast-message">{message}</span>
            {action && (
                <button
                    type="button"
                    className="ui-toast-action"
                    onClick={() => { action.onClick(); onDismiss(id); }}
                >
                    {action.label}
                </button>
            )}
            <button type="button" className="ui-toast-close" onClick={() => onDismiss(id)} aria-label="Dismiss notification">
                <X size={14} aria-hidden="true" />
            </button>
        </div>
    );
}

// App-wide toast stack behind useToast(). Bottom-right on desktop, bottom-centre above
// the tab bar when the bottom nav is showing.
export default function ToastProvider({ children }) {
    const [toasts, setToasts] = useState([]);
    const nextId = useRef(0);

    const dismiss = useCallback((id) => {
        setToasts((list) => list.filter((t) => t.id !== id));
    }, []);

    const show = useCallback(({ message, type = 'info', durationMs = DEFAULT_DURATION_MS, action } = {}) => {
        nextId.current += 1;
        const id = nextId.current;
        setToasts((list) => [...list, { id, message, type, durationMs, action }].slice(-MAX_VISIBLE));
        return id;
    }, []);

    const api = useMemo(() => ({
        show,
        dismiss,
        success: (message, opts) => show({ ...opts, message, type: 'success' }),
        error: (message, opts) => show({ ...opts, message, type: 'error' }),
        info: (message, opts) => show({ ...opts, message, type: 'info' }),
    }), [show, dismiss]);

    return (
        <ToastContext.Provider value={api}>
            {children}
            {createPortal(
                <section className="ui-toast-region" aria-label="Notifications">
                    {toasts.map((t) => <ToastItem key={t.id} toast={t} onDismiss={dismiss} />)}
                </section>,
                document.body
            )}
        </ToastContext.Provider>
    );
}
