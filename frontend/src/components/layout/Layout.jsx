import { useState, useEffect } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import { LogOut, Crosshair, Moon, Activity, MoreHorizontal } from 'lucide-react';
import BottomSheet from '../common/BottomSheet';
import TelescopeIcon from '../icons/TelescopeIcon';

import logo from '../../assets/logo.png';
import './Layout.css';
import QualityUnitsToggle from '../quality/QualityUnitsToggle';

// Icons as simple SVG components
const DashboardIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <rect x="3" y="3" width="7" height="7" rx="1" />
        <rect x="14" y="3" width="7" height="7" rx="1" />
        <rect x="3" y="14" width="7" height="7" rx="1" />
        <rect x="14" y="14" width="7" height="7" rx="1" />
    </svg>
);

const SearchIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="11" cy="11" r="8" />
        <path d="M21 21l-4.35-4.35" />
    </svg>
);

const CatalogIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <circle cx="12" cy="12" r="3" />
        <circle cx="12" cy="12" r="8" strokeDasharray="4 2" />
        <path d="M12 2v2M12 20v2M2 12h2M20 12h2" />
    </svg>
);


const FileTextIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
        <polyline points="14 2 14 8 20 8" />
        <line x1="16" y1="13" x2="8" y2="13" />
        <line x1="16" y1="17" x2="8" y2="17" />
        <polyline points="10 9 9 9 8 9" />
    </svg>
);

const AnalyticsIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M12 20V10" strokeLinecap="round" />
        <path d="M18 20V4" strokeLinecap="round" />
        <path d="M6 20v-4" strokeLinecap="round" />
    </svg>
);

const AdminIcon = () => (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2">
        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91a6 6 0 0 1 7.94-7.94l-3.76 3.76z" />
    </svg>
);

const PinIcon = ({ pinned }) => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill={pinned ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" style={{ transform: pinned ? 'rotate(0deg)' : 'rotate(45deg)', transition: 'transform 0.2s' }}>
        <path d="M21 10V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v2a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 10z" />
        <path d="M12 15v6" />
        <path d="M7 10h10" />
    </svg>
);

const TargetsIcon = () => <Crosshair size={20} strokeWidth={2} />;
const EquipmentIcon = () => <TelescopeIcon size={20} strokeWidth={2} />;
const TonightIcon = () => <Moon size={20} strokeWidth={2} />;
const NightsIcon = () => <Activity size={20} strokeWidth={2} />;

const navItems = [
    { path: '/', label: 'Dashboard', icon: DashboardIcon, end: true },
    { path: '/search', label: 'Search', icon: SearchIcon },
    { path: '/metadata-search', label: 'Metadata Search', icon: FileTextIcon },
    { path: '/catalogs', label: 'Catalogs', icon: CatalogIcon },
    // R1: Tonight goes before Targets per docs/design/R1-recommendation-engine.md §8
    { path: '/tonight', label: 'Tonight', icon: TonightIcon },
    // F2: inserted after Catalogs per docs/design/README.md §4 (Targets, Sessions, Mosaics order)
    { path: '/targets', label: 'Targets', icon: TargetsIcon },
    // Q1c: star quality through each observing night
    { path: '/nights', label: 'Nights', icon: NightsIcon },
    // R0: Equipment goes after Targets (docs/design/P0-R0-equipment-sites.md §4.9)
    { path: '/equipment', label: 'Equipment', icon: EquipmentIcon },
    { path: '/stats', label: 'Statistics', icon: AnalyticsIcon },
    { path: '/admin', label: 'Admin', icon: AdminIcon },
];

const primaryPaths = ['/', '/search', '/targets', '/tonight'];

export default function Layout({ children }) {
    const { logout, user, systemVersion } = useAuth();
    const location = useLocation();
    const [moreOpen, setMoreOpen] = useState(false);

    const primaryItems = primaryPaths.map((p) => navItems.find((n) => n.path === p));
    const moreItems = navItems.filter((n) => !primaryPaths.includes(n.path));
    const moreActive = moreItems.some((n) =>
        n.end ? location.pathname === n.path : location.pathname.startsWith(n.path)
    );


    const [isPinned, setIsPinned] = useState(() => {
        const saved = localStorage.getItem('sidebar-pinned');
        return saved !== null ? JSON.parse(saved) : true;
    });

    useEffect(() => {
        localStorage.setItem('sidebar-pinned', JSON.stringify(isPinned));
    }, [isPinned]);

    const togglePin = () => setIsPinned(!isPinned);

    return (
        <div className={`layout ${isPinned ? 'is-pinned' : 'is-collapsed'}`}>
            {/* Sidebar Navigation */}
            <aside className="sidebar">
                <div className="sidebar-header">
                    <div className="logo">
                        <div className="logo-icon">
                            <img src={logo} alt={`${import.meta.env.VITE_LOGO_TITLE || 'AstroCat'} Logo`} />
                        </div>
                        <div className="logo-text">
                            <span className="logo-title">{import.meta.env.VITE_LOGO_TITLE || 'AstroCat'}</span>
                            <span className="logo-subtitle">Image Database</span>
                        </div>
                    </div>
                    <button
                        className="sidebar-toggle"
                        onClick={togglePin}
                        title={isPinned ? "Enable Auto-hide" : "Pin Sidebar"}
                    >
                        <PinIcon pinned={isPinned} />
                    </button>
                </div>

                <nav className="sidebar-nav">
                    {navItems.map(({ path, label, icon: Icon, end }) => (
                        <NavLink
                            key={path}
                            to={path}
                            end={end}
                            className={({ isActive }) =>
                                `nav-item ${isActive ? 'active' : ''}`
                            }
                        >
                            <Icon />
                            <span>{label}</span>
                        </NavLink>
                    ))}

                    <button className="nav-item logout-button" onClick={logout} title="Logout">
                        <LogOut size={20} />
                        <span>Logout ({user?.email || 'User'})</span>
                    </button>
                </nav>



                <div className="sidebar-footer">
                    {/* Q1: FWHM/HFR units for every page (per viewer, remembered) */}
                    <div className="sidebar-units">
                        <span>Star sizes</span>
                        <QualityUnitsToggle compact />
                    </div>
                    <div className="version-info">
                        <span>v{systemVersion?.app_version || (typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.1.0')}</span>
                        <span className="text-muted">AstroCat</span>
                    </div>
                </div>
            </aside>

            {/* Main Content Area */}
            <main className="main-content">
                <div className="content-wrapper">
                    {children}
                </div>
            </main>

            {/* Mobile bottom tab bar (shown below 1024px via CSS) */}
            <nav className="bottom-nav" aria-label="Primary">
                {primaryItems.map(({ path, label, icon: Icon, end }) => (
                    <NavLink
                        key={path}
                        to={path}
                        end={end}
                        className={({ isActive }) => `bottom-nav-item ${isActive ? 'active' : ''}`}
                    >
                        <Icon />
                        <span>{label === 'Dashboard' ? 'Home' : label}</span>
                    </NavLink>
                ))}
                <button
                    className={`bottom-nav-item ${moreActive || moreOpen ? 'active' : ''}`}
                    onClick={() => setMoreOpen(true)}
                >
                    <MoreHorizontal size={20} strokeWidth={2} />
                    <span>More</span>
                </button>
            </nav>

            <BottomSheet open={moreOpen} onClose={() => setMoreOpen(false)} title="Menu">
                <div className="more-grid">
                    {moreItems.map(({ path, label, icon: Icon, end }) => (
                        <NavLink
                            key={path}
                            to={path}
                            end={end}
                            className={({ isActive }) => `more-item ${isActive ? 'active' : ''}`}
                            onClick={() => setMoreOpen(false)}
                        >
                            <Icon />
                            <span>{label}</span>
                        </NavLink>
                    ))}
                </div>
                <div className="more-footer">
                    <div className="sidebar-units">
                        <span>Star sizes</span>
                        <QualityUnitsToggle compact />
                    </div>
                    <button className="btn btn-secondary" onClick={logout}>
                        <LogOut size={18} /> Logout ({user?.email || 'User'})
                    </button>
                    <span className="text-muted more-version">
                        v{systemVersion?.app_version || (typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.1.0')}
                    </span>
                </div>
            </BottomSheet>
        </div>
    );
}
