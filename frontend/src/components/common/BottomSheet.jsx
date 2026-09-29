import { useEffect } from 'react';
import { createPortal } from 'react-dom';
import { X } from 'lucide-react';
import './BottomSheet.css';

// Slide-up sheet for mobile menus, filters and context actions.
export default function BottomSheet({ open, onClose, title, children, fullHeight = false, footer }) {
    useEffect(() => {
        if (!open) return undefined;
        const onKey = (e) => e.key === 'Escape' && onClose?.();
        document.addEventListener('keydown', onKey);
        const prev = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => {
            document.removeEventListener('keydown', onKey);
            document.body.style.overflow = prev;
        };
    }, [open, onClose]);

    if (!open) return null;

    return createPortal(
        <div className="bottom-sheet-backdrop" onClick={onClose}>
            <div
                className={`bottom-sheet ${fullHeight ? 'bottom-sheet-full' : ''}`}
                role="dialog"
                aria-modal="true"
                aria-label={title}
                onClick={(e) => e.stopPropagation()}
            >
                <div className="bottom-sheet-handle" />
                <div className="bottom-sheet-header">
                    <h3>{title}</h3>
                    <button className="bottom-sheet-close" onClick={onClose} aria-label="Close">
                        <X size={20} />
                    </button>
                </div>
                <div className="bottom-sheet-body">{children}</div>
                {footer && <div className="bottom-sheet-footer">{footer}</div>}
            </div>
        </div>,
        document.body
    );
}
