import { useState, useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchSkyOverlay } from '../api/client';
import { SKY_CATALOGS } from '../utils/skyOverlay';

const HIDDEN_KEY = 'astrocat.skyOverlayHidden';

function readHidden() {
    try {
        const v = JSON.parse(localStorage.getItem(HIDDEN_KEY) || '[]');
        return Array.isArray(v) ? v.filter((c) => SKY_CATALOGS.includes(c)) : [];
    } catch {
        return [];
    }
}

/** Why the dynamic catalog overlay can't be shown for this image, or null. */
export function skyOverlayUnavailableReason(image) {
    if (!image?.sky_overlay_source) return 'No plate solution with a full WCS';
    return null;
}

/**
 * Dynamic catalog overlay data for an image, fetched only while `enabled`, plus the per-catalog
 * visibility chosen in the legend (remembered per browser).
 */
export default function useSkyOverlay(image, enabled) {
    const [hidden, setHidden] = useState(readHidden);
    const unavailable = skyOverlayUnavailableReason(image);
    const active = enabled && !unavailable;

    const query = useQuery({
        queryKey: ['sky-overlay', image?.id],
        queryFn: () => fetchSkyOverlay(image.id),
        enabled: active && !!image,
        staleTime: 10 * 60 * 1000,
    });

    const toggleCatalog = useCallback((catalog) => {
        setHidden((prev) => {
            const next = prev.includes(catalog) ? prev.filter((c) => c !== catalog) : [...prev, catalog];
            try {
                localStorage.setItem(HIDDEN_KEY, JSON.stringify(next));
            } catch {
                // per-browser convenience only
            }
            return next;
        });
    }, []);

    const all = useMemo(() => (active ? query.data?.objects || [] : []), [active, query.data]);
    const objects = useMemo(() => all.filter((o) => !hidden.includes(o.catalog)), [all, hidden]);
    const counts = useMemo(() => {
        const c = {};
        for (const o of all) c[o.catalog] = (c[o.catalog] || 0) + 1;
        return c;
    }, [all]);

    return {
        active,
        unavailable,
        objects,
        counts,
        hidden,
        toggleCatalog,
        warning: image?.sky_overlay_warning || query.data?.accuracy_warning || null,
        isLoading: active && query.isLoading,
        isError: active && query.isError,
    };
}
