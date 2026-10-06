import { useState, useCallback } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchFieldOverlaps } from '../api/client';
import { overlayUnavailableReason } from '../utils/fieldOverlay';

const STORAGE_KEY = 'astrocat.fieldOverlayMode';
export const OVERLAY_MODES = ['off', 'masters', 'subs', 'all'];
export const OVERLAY_MODE_LABELS = { off: 'Off', masters: 'Masters', subs: 'Subs', all: 'All' };

function readStored() {
    try {
        const v = localStorage.getItem(STORAGE_KEY);
        return OVERLAY_MODES.includes(v) ? v : 'off';
    } catch {
        return 'off';
    }
}

/** Images-in-field overlay mode (cycled Off -> Masters -> Subs -> All, remembered per browser) and its data. */
export default function useFieldOverlays(image) {
    const [mode, setMode] = useState(readStored);
    const unavailable = overlayUnavailableReason(image);

    const cycle = useCallback(() => {
        setMode((m) => {
            const next = OVERLAY_MODES[(OVERLAY_MODES.indexOf(m) + 1) % OVERLAY_MODES.length];
            try {
                localStorage.setItem(STORAGE_KEY, next);
            } catch {
                // per-browser convenience only
            }
            return next;
        });
    }, []);

    const active = mode !== 'off' && !unavailable;
    const query = useQuery({
        queryKey: ['field-overlaps', image?.id, mode],
        queryFn: () => fetchFieldOverlaps(image.id, mode),
        enabled: active && !!image,
        staleTime: 5 * 60 * 1000,
    });

    const groups = active ? query.data?.groups || [] : [];
    // Footprints drawn as circles have no rotation yet; count is uncapped (members is not)
    const unsolvedCount = groups.reduce((n, g) => (g.shape === 'circle' ? n + g.count : n), 0);

    return {
        mode,
        refetch: query.refetch,
        unsolvedCount,
        active,
        cycle,
        unavailable,
        groups,
        truncated: !!query.data?.truncated,
        isLoading: active && query.isLoading,
    };
}
