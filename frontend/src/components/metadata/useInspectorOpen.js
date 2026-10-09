import { useEffect, useState } from 'react';

const STORAGE_KEY = 'astrocat.imageInspector';

function readStored() {
    try {
        // Below the 1024px nav switch the Inspector is a full-height sheet; never restore it open.
        if (window.matchMedia('(max-width: 1023px)').matches) return false;
        return localStorage.getItem(STORAGE_KEY) === '1';
    } catch {
        return false;
    }
}

// Open/closed state of the ImageDetail Inspector, remembered per browser.
// `forceOpen` (the /images/:id/metadata deep link) opens it on arrival and whenever `id` changes.
export default function useInspectorOpen(forceOpen, id) {
    const [open, setOpen] = useState(() => forceOpen || readStored());
    const [forcedFor, setForcedFor] = useState(forceOpen ? id : null);
    const target = forceOpen ? id : null;
    if (forcedFor !== target) {
        setForcedFor(target);
        if (forceOpen) setOpen(true);
    }

    useEffect(() => {
        try {
            localStorage.setItem(STORAGE_KEY, open ? '1' : '0');
        } catch {
            // per-browser convenience only
        }
    }, [open]);

    return [open, setOpen];
}
