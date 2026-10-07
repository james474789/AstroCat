import { useState, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchSeenIn } from '../api/client';
import { overlayUnavailableReason } from '../utils/fieldOverlay';

const STORAGE_KEY = 'astrocat.seenInMode';
export const SEEN_IN_MODES = ['all', 'masters', 'subs'];
export const SEEN_IN_MODE_LABELS = { all: 'All', masters: 'Masters', subs: 'Subs' };

function readStored() {
    try {
        const v = localStorage.getItem(STORAGE_KEY);
        return SEEN_IN_MODES.includes(v) ? v : 'all';
    } catch {
        return 'all';
    }
}

/** "Seen in": larger images whose field covers this image, listed in a panel (filter remembered per browser). */
export default function useSeenIn(image) {
    const [open, setOpen] = useState(false);
    const [mode, setModeState] = useState(readStored);
    const unavailable = overlayUnavailableReason(image);

    const setMode = useCallback((m) => {
        setModeState(m);
        try {
            localStorage.setItem(STORAGE_KEY, m);
        } catch {
            // per-browser convenience only
        }
    }, []);

    const query = useQuery({
        queryKey: ['seen-in', image?.id, mode],
        queryFn: () => fetchSeenIn(image.id, mode),
        enabled: open && !unavailable && !!image,
        staleTime: 5 * 60 * 1000,
    });

    return {
        open,
        setOpen,
        mode,
        setMode,
        unavailable,
        groups: query.data?.groups || [],
        truncated: !!query.data?.truncated,
        loaded: !!query.data,
        isLoading: open && query.isLoading,
        isError: query.isError,
    };
}
