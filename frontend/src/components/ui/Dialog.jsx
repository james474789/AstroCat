import { useEffect, useId, useRef } from 'react';
import { createPortal } from 'react-dom';
import { AlertTriangle, X } from 'lucide-react';
import './ui.css';

// Body scroll lock shared by nested dialogs: only the outermost one restores overflow.
let scrollLocks = 0;
let savedOverflow = '';

function lockBodyScroll() {
    if (scrollLocks === 0) {
        savedOverflow = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
    }
    scrollLocks += 1;
}

function unlockBodyScroll() {
    scrollLocks -= 1;
    if (scrollLocks === 0) document.body.style.overflow = savedOverflow;
}

// Modal dialog on native <dialog>.showModal(): the browser provides the focus trap,
// top-layer stacking and inert background. Initial focus goes to the first focusable
// element in the body/footer (the close button is last in DOM order on purpose).
export default function Dialog({
    open,
    onClose,
    title,
    description,
    children,
    footer,
    size = 'md',
    destructive = false,
    dismissible = true,
    closeOnBackdrop = true,
    className = '',
}) {
    const dialogRef = useRef(null);
    const pressStartedOnBackdrop = useRef(false);
    const titleId = useId();
    const descriptionId = useId();

    useEffect(() => {
        const dialog = dialogRef.current;
        if (!open || !dialog) return undefined;
        const returnFocus = document.activeElement;
        if (!dialog.open) dialog.showModal();
        lockBodyScroll();
        return () => {
            unlockBodyScroll();
            if (dialog.open) dialog.close();
            if (returnFocus instanceof HTMLElement && returnFocus.isConnected) returnFocus.focus();
        };
    }, [open]);

    if (!open) return null;

    // Esc: keep React in control of open/closed state.
    const handleCancel = (event) => {
        event.preventDefault();
        if (dismissible) onClose?.();
    };

    // The browser can still force-close (e.g. Esc pressed twice in Chrome).
    const handleNativeClose = () => {
        const dialog = dialogRef.current;
        if (!dialog || dialog.open) return;
        if (dismissible) onClose?.();
        else dialog.showModal();
    };

    // The panel fills the <dialog>, so a press that starts and ends on the dialog
    // element itself is a press on the ::backdrop.
    const handlePointerDown = (event) => {
        pressStartedOnBackdrop.current = event.target === event.currentTarget;
    };
    const handleClick = (event) => {
        if (closeOnBackdrop && pressStartedOnBackdrop.current && event.target === event.currentTarget) {
            onClose?.();
        }
    };

    return createPortal(
        <dialog
            ref={dialogRef}
            className={`ui-dialog ui-dialog-${size} ${className}`.trim()}
            aria-labelledby={titleId}
            aria-describedby={description ? descriptionId : undefined}
            onCancel={handleCancel}
            onClose={handleNativeClose}
            onPointerDown={handlePointerDown}
            onClick={handleClick}
        >
            <div className="ui-dialog-panel">
                <header className="ui-dialog-header">
                    {destructive && <AlertTriangle className="ui-dialog-icon" size={20} aria-hidden="true" />}
                    <h2 id={titleId} className="ui-dialog-title">{title}</h2>
                </header>
                {description && <p id={descriptionId} className="ui-dialog-description">{description}</p>}
                {children && <div className="ui-dialog-body">{children}</div>}
                {footer && <div className="ui-dialog-footer">{footer}</div>}
                {dismissible && (
                    <button type="button" className="ui-dialog-close" onClick={onClose} aria-label="Close">
                        <X size={18} aria-hidden="true" />
                    </button>
                )}
            </div>
        </dialog>,
        document.body
    );
}
