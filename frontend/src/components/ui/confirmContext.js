import { createContext, useContext } from 'react';

export const ConfirmContext = createContext(null);

// const confirm = useConfirm();
// if (await confirm({ title: 'Delete 3 images?', confirmLabel: 'Delete 3 images', destructive: true })) { ... }
export function useConfirm() {
    const confirm = useContext(ConfirmContext);
    if (!confirm) throw new Error('useConfirm must be used inside <ConfirmProvider>');
    return confirm;
}
