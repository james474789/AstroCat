import { useState, useEffect, useCallback } from 'react';
import { useNavigate } from 'react-router-dom';

const defaultRoute = (imageId) => `/images/${imageId}`;

/**
 * Previous/next navigation through the current search results, plus "back to the search".
 *
 * Reads the context the Search page stores in sessionStorage ('currentSearchContext').
 * `routeFor(imageId)` says where an image lives, so ImageDetail and the full-resolution
 * viewer can page through the same list on their own routes.
 */
export default function useImageNav(id, routeFor = defaultRoute) {
    const navigate = useNavigate();
    const [navInfo, setNavInfo] = useState({ prevId: null, nextId: null, currentIndex: -1, total: 0 });

    useEffect(() => {
        const contextStr = sessionStorage.getItem('currentSearchContext');
        if (contextStr) {
            try {
                const context = JSON.parse(contextStr);
                const currentIdNum = parseInt(id);
                const index = context.ids.indexOf(currentIdNum);

                if (index !== -1) {
                    setNavInfo({
                        prevId: index > 0 ? context.ids[index - 1] : null,
                        nextId: index < context.ids.length - 1 ? context.ids[index + 1] : null,
                        currentIndex: (context.page - 1) * context.pageSize + index + 1,
                        total: context.total
                    });
                } else {
                    setNavInfo({ prevId: null, nextId: null, currentIndex: -1, total: 0 });
                }
            } catch (e) {
                console.error("Failed to parse search context", e);
            }
        }
    }, [id]);

    const goToImage = useCallback((targetId) => {
        if (!targetId) return;
        sessionStorage.setItem('lastClickedImageId', targetId);
        navigate(routeFor(targetId));
    }, [navigate, routeFor]);

    const goPrev = useCallback(() => goToImage(navInfo.prevId), [goToImage, navInfo.prevId]);
    const goNext = useCallback(() => goToImage(navInfo.nextId), [goToImage, navInfo.nextId]);

    // Back to the search results this image was opened from (restores its filters from the stored context)
    const returnToSearch = useCallback(() => {
        sessionStorage.setItem('lastClickedImageId', id);
        const contextStr = sessionStorage.getItem('currentSearchContext');
        if (contextStr) {
            try {
                const context = JSON.parse(contextStr);
                if (context.params) {
                    const searchParams = new URLSearchParams();
                    Object.entries(context.params).forEach(([key, value]) => {
                        // Exclude internal or default parameters that shouldn't clutter the URL
                        if (key !== 'page_size' && value !== undefined && value !== null && value !== '') {
                            searchParams.set(key, value);
                        }
                    });
                    navigate(`/search?${searchParams.toString()}`);
                    return;
                }
            } catch (e) {
                console.error("Failed to parse search context for return", e);
            }
        }
        navigate('/search');
    }, [id, navigate]);

    return { navInfo, goToImage, goPrev, goNext, returnToSearch };
}
