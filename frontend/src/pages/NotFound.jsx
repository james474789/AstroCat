import { Link, useLocation } from 'react-router-dom';

export default function NotFound() {
    const { pathname } = useLocation();
    return (
        <div style={{ padding: '3rem 1.5rem', textAlign: 'center' }} data-testid="not-found">
            <h1>Page not found</h1>
            <p>
                There is no page at <code>{pathname}</code>.
            </p>
            <p>
                <Link to="/">Back to the dashboard</Link>
            </p>
        </div>
    );
}
