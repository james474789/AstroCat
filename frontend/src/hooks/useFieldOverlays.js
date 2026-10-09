import { useState, useCallback, useMemo } from 'react';
import { useQuery } from '@tanstack/react-query';
import { fetchFieldOverlaps } from '../api/client';
import { overlayUnavailableReason } from '../utils/fieldOverlay';

const STORAGE_KEY = 'astrocat.fieldOverlay';
const LEGACY_MODE_KEY = 'astrocat.fieldOverlayMode'; // the old Off/Masters/Subs/All cycle
export const OVERLAY_MODE_LABELS = { masters: 'Masters', subs: 'Subs', all: 'All' };
const DEFAULT_STATE = { on: false, masters: true, subs: true };

function readStored() {
    try {
        const v = JSON.parse(localStorage.getItem(STORAGE_KEY) || 'null');
        if (v && typeof v === 'object') {
            return { on: !!v.on, masters: v.masters !== false, subs: v.subs !== false };
        }
        const legacy = localStorage.getItem(LEGACY_MODE_KEY);
        if (legacy === 'masters') return { on: true, masters: true, subs: false };
        if (legacy === 'subs') return { on: true, masters: false, subs: true };
        if (legacy === 'all') return { on: true, masters: true, subs: true };
    } catch {
        // fall through to the default
    }
    return DEFAULT_STATE;
}

/**
 * Images-in-field overlay: one on/off switch plus Masters / Subs visibility (both = All), remembered per
 * browser, and its data. Counts per kind come from an unfiltered fetch so the legend can show them for rows
 * that are currently hidden.
 */
export default function useFieldOverlays(image) {
    const [state, setState] = useState(readStored);
    const unavailable = overlayUnavailableReason(image);

    const update = useCallback((patch) => {
        setState((s) => {
            const next = { ...s, ...(typeof patch === 'function' ? patch(s) : patch) };
            try {
                localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
            } catch {
                // per-browser convenience only
            }
            return next;
        });
    }, []);

    const toggle = useCallback(() => update((s) => ({ on: !s.on })), [update]);
    const toggleKind = useCallback((kind) => update((s) => ({ [kind]: !s[kind] })), [update]);

    const active = state.on && !unavailable;
    // Server-side mode for what is displayed; null when both kinds are hidden
    const mode = state.masters && state.subs ? 'all' : state.masters ? 'masters' : state.subs ? 'subs' : null;

    const allQuery = useQuery({
        queryKey: ['field-overlaps', image?.id, 'all'],
        queryFn: () => fetchFieldOverlaps(image.id, 'all'),
        enabled: active && !!image,
        staleTime: 5 * 60 * 1000,
    });
    const shownQuery = useQuery({
        queryKey: ['field-overlaps', image?.id, mode],
        queryFn: () => fetchFieldOverlaps(image.id, mode),
        enabled: active && !!image && !!mode && mode !== 'all',
        staleTime: 5 * 60 * 1000,
    });
    const shown = mode === 'all' ? allQuery : shownQuery;

    const groups = useMemo(() => (active && mode ? shown.data?.groups || [] : []), [active, mode, shown.data]);
    // Footprints drawn as circles have no rotation yet; count is uncapped (members is not)
    const unsolvedCount = groups.reduce((n, g) => (g.shape === 'circle' ? n + g.count : n), 0);

    const counts = useMemo(() => {
        const c = { masters: 0, subs: 0 };
        for (const g of (active ? allQuery.data?.groups : null) || []) {
            c.masters += g.master_count || 0;
            c.subs += (g.count || 0) - (g.master_count || 0);
        }
        return c;
    }, [active, allQuery.data]);

    return {
        on: state.on,
        masters: state.masters,
        subs: state.subs,
        mode,
        refetch: shown.refetch,
        unsolvedCount,
        active,
        toggle,
        toggleKind,
        unavailable,
        groups,
        counts,
        truncated: !!shown.data?.truncated,
        isLoading: active && !!mode && shown.isLoading,
        isCountsLoading: active && allQuery.isLoading,
        isError: active && (allQuery.isError || (!!mode && shown.isError)),
    };
}
