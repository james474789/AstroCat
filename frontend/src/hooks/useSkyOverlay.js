import { useState, useCallback, useMemo } from 'react';
import { useQuery, useQueries } from '@tanstack/react-query';
import { fetchSkyOverlay, fetchOnlineCatalogs, fetchSkyOverlayOnline } from '../api/client';

const HIDDEN_KEY = 'astrocat.skyOverlayHidden';

const ONLINE_REASONS = {
    no_capture_time: 'No capture time',
    field_too_wide: 'Field too wide',
    no_wcs: 'No plate solution',
};

function readHidden() {
    // Catalog keys (local, or online ones enabled by an admin); unknown keys are harmless
    try {
        const v = JSON.parse(localStorage.getItem(HIDDEN_KEY) || '[]');
        return Array.isArray(v) ? v.filter((c) => typeof c === 'string') : [];
    } catch {
        return [];
    }
}

/** Why the dynamic catalog overlay can't be shown for this image, or null. */
export function skyOverlayUnavailableReason(image) {
    if (!image?.sky_overlay_source) return 'Not plate-solved, or rotation unknown';
    return null;
}

/**
 * Dynamic catalog overlay data for an image, fetched only while `enabled`, plus the per-catalog
 * visibility chosen in the legend (remembered per browser). Local catalogs come in one request;
 * each admin-enabled online catalog (O1) is its own request, merged in as it arrives.
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

    const catalogsQuery = useQuery({
        queryKey: ['sky-online-catalogs'],
        queryFn: fetchOnlineCatalogs,
        enabled: active,
        staleTime: 5 * 60 * 1000,
    });
    const onlineCatalogs = useMemo(() => catalogsQuery.data || [], [catalogsQuery.data]);

    const onlineQueries = useQueries({
        queries: onlineCatalogs.map((c) => ({
            queryKey: ['sky-overlay-online', image?.id, c.key],
            queryFn: () => fetchSkyOverlayOnline(image.id, c.key),
            enabled: active && !!image,
            staleTime: 60 * 60 * 1000,
            retry: false,   // the server backs off failing services itself
        })),
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

    // useQueries returns a new array every render; key the merge on the data it holds
    const onlineData = onlineQueries.map((q) => q.data);
    const onlineKey = onlineQueries.map((q, i) => `${onlineCatalogs[i]?.key}:${image?.id}:${q.dataUpdatedAt}`).join('|');

    const all = useMemo(() => {
        if (!active) return [];
        const merged = [...(query.data?.objects || [])];
        for (const d of onlineData) if (d?.objects) merged.push(...d.objects);
        return merged;
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [active, query.data, onlineKey]);
    const objects = useMemo(() => all.filter((o) => !hidden.includes(o.catalog)), [all, hidden]);
    const counts = useMemo(() => {
        const c = {};
        for (const o of all) c[o.catalog] = (c[o.catalog] || 0) + 1;
        return c;
    }, [all]);

    const online = active ? onlineCatalogs.map((c, i) => {
        const q = onlineQueries[i] || {};
        return {
            key: c.key,
            label: c.label,
            count: counts[c.key] || 0,
            loading: !!q.isLoading,
            error: q.isError ? (q.error?.message || 'Lookup failed') : null,
            note: q.data?.reason ? (ONLINE_REASONS[q.data.reason] || q.data.reason) : null,
            notice: q.data?.notice || null,
        };
    }) : [];

    return {
        active,
        unavailable,
        objects,
        counts,
        hidden,
        toggleCatalog,
        online,
        warning: image?.sky_overlay_warning || query.data?.accuracy_warning || null,
        isLoading: active && query.isLoading,
        isError: active && query.isError,
    };
}
