import { useState, useEffect } from 'react';

// Subscribes to a CSS media query; e.g. useMediaQuery('(max-width: 1024px)')
export default function useMediaQuery(query) {
    const get = () => (typeof window !== 'undefined' && window.matchMedia ? window.matchMedia(query).matches : false);
    const [matches, setMatches] = useState(get);

    useEffect(() => {
        const mq = window.matchMedia(query);
        const onChange = () => setMatches(mq.matches);
        onChange();
        mq.addEventListener('change', onChange);
        return () => mq.removeEventListener('change', onChange);
    }, [query]);

    return matches;
}

export const useIsMobile = () => useMediaQuery('(max-width: 1024px)');
export const useIsPhone = () => useMediaQuery('(max-width: 640px)');
// Touch tablets (iPad, portrait and landscape)
export const useIsTablet = () => useMediaQuery('(min-width: 768px) and (max-width: 1194px) and (pointer: coarse)');
// Bottom tab bar instead of the sidebar; mirrors the media query in Layout.css
export const useIsTouchNav = () => useMediaQuery('(max-width: 1024px), (max-width: 1194px) and (pointer: coarse)');
