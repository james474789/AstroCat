import Button from './Button';
import Dialog from './Dialog';

// confirmLabel must name the action and its count ("Delete 3 images"), never "OK".
// The Cancel button comes first in DOM order, so it gets initial focus.
export default function ConfirmDialog({
    open,
    title,
    description,
    children,
    confirmLabel,
    cancelLabel = 'Cancel',
    destructive = false,
    hideCancel = false,
    onConfirm,
    onCancel,
}) {
    const footer = (
        <>
            {!hideCancel && <Button variant="plain" onClick={onCancel}>{cancelLabel}</Button>}
            <Button variant={destructive ? 'destructive' : 'filled'} onClick={onConfirm}>
                {confirmLabel || (hideCancel ? 'Close' : 'Confirm')}
            </Button>
        </>
    );

    return (
        <Dialog
            open={open}
            onClose={onCancel}
            title={title}
            description={description}
            footer={footer}
            size="sm"
            destructive={destructive}
        >
            {children}
        </Dialog>
    );
}
