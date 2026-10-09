import { useCallback, useRef, useState } from 'react';
import ConfirmDialog from './ConfirmDialog';
import { ConfirmContext } from './confirmContext';

// Hosts the single app-wide confirm dialog behind useConfirm().
export default function ConfirmProvider({ children }) {
    const [request, setRequest] = useState(null);
    const pendingRef = useRef(null);

    const confirm = useCallback((options = {}) => new Promise((resolve) => {
        if (import.meta.env.DEV && !options.confirmLabel && !options.hideCancel) {
            console.warn('useConfirm: pass a confirmLabel naming the action and count, e.g. "Delete 3 images".');
        }
        // A newer request replaces an unanswered one, which counts as cancelled.
        pendingRef.current?.resolve(false);
        const next = { options, resolve };
        pendingRef.current = next;
        setRequest(next);
    }), []);

    const settle = useCallback((result) => {
        pendingRef.current?.resolve(result);
        pendingRef.current = null;
        setRequest(null);
    }, []);

    const options = request?.options || {};

    return (
        <ConfirmContext.Provider value={confirm}>
            {children}
            <ConfirmDialog
                open={request !== null}
                title={options.title}
                description={options.description}
                confirmLabel={options.confirmLabel}
                cancelLabel={options.cancelLabel}
                destructive={options.destructive}
                hideCancel={options.hideCancel}
                onConfirm={() => settle(true)}
                onCancel={() => settle(false)}
            />
        </ConfirmContext.Provider>
    );
}
