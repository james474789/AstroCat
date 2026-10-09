import { createContext, useContext } from 'react';

export const ToastContext = createContext(null);

// const toast = useToast();
// toast.success('Saved'); toast.error(`Failed: ${err.message}`);
// toast.show({ message: 'Hidden M31', action: { label: 'Undo', onClick: undo } });
export function useToast() {
    const toast = useContext(ToastContext);
    if (!toast) throw new Error('useToast must be used inside <ToastProvider>');
    return toast;
}
