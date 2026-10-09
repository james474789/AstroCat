import { Navigate, useLocation } from 'react-router-dom';

// U1 P1b: Metadata Search was merged into Images. Old links land on the list view,
// keeping any query params they carried.
export default function MetadataSearchRedirect() {
    const { search } = useLocation();
    const params = new URLSearchParams(search);
    params.set('view', 'list');
    return <Navigate to={`/search?${params.toString()}`} replace />;
}
