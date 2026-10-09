import { useState, useEffect, useRef } from 'react';
import { NavLink, useLocation } from 'react-router-dom';
import { useAuth } from '../../context/AuthContext';
import {
    LogOut, Crosshair, Moon, Activity, MoreHorizontal, Home, Image, BookOpen, BarChart3, Wrench, User,
} from 'lucide-react';
import BottomSheet from '../common/BottomSheet';
import TelescopeIcon from '../icons/TelescopeIcon';

import logo from '../../assets/logo.png';
import './Layout.css';
import QualityUnitsToggle from '../quality/QualityUnitsToggle';

// Pin toggle has no good Lucide equivalent
const PinIcon = ({ pinned }) => (
    <svg width="18" height="18" viewBox="0 0 24 24" fill={pinned ? 'currentColor' : 'none'} stroke="currentColor" strokeWidth="2" style={{ transform: pinned ? 'rotate(0deg)' : 'rotate(45deg)', transition: 'transform 0.2s' }}>
        <path d="M21 10V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v2a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 10z" />
        <path d="M12 15v6" />
        <path d="M7 10h10" />
    </svg>
);

// Items without a group render first, without a heading (docs/design/20261009-U1 §3.3)
const navItems = [
    { path: '/', label: 'Home', icon: Home, end: true },
    { path: '/search', label: 'Images', icon: Image, group: 'Library' },
    // F2: Targets per docs/design/README.md §4
    { path: '/targets', label: 'Targets', icon: Crosshair, group: 'Library' },
    { path: '/catalogs', label: 'Catalogs', icon: BookOpen, group: 'Library' },
    // R1: Tonight goes before Nights per docs/design/R1-recommendation-engine.md §8
    { path: '/tonight', label: 'Tonight', icon: Moon, group: 'Observe' },
    // Q1c: star quality through each observing night
    { path: '/nights', label: 'Nights', icon: Activity, group: 'Observe' },
    { path: '/stats', label: 'Statistics', icon: BarChart3, group: 'Insights' },
    // R0: Equipment (docs/design/P0-R0-equipment-sites.md §4.9)
    { path: '/equipment', label: 'Equipment', icon: TelescopeIcon, group: 'Setup' },
    { path: '/admin', label: 'Admin', icon: Wrench, group: 'Setup' },
];

const primaryPaths = ['/', '/search', '/tonight', '/targets'];

// Split items into [{ label, items }] in first-seen order; ungrouped items (null label) come first.
function groupNavItems(items) {
    const sections = [];
    items.forEach((item) => {
        const label = item.group || null;
        let section = sections.find((s) => s.label === label);
        if (!section) {
            section = { label, items: [] };
            sections.push(section);
        }
        section.items.push(item);
    });
    return sections.sort((a, b) => (a.label === null ? -1 : 0) - (b.label === null ? -1 : 0));
}

const navSections = groupNavItems(navItems);

export default function Layout({ children }) {
    const { logout, user, systemVersion } = useAuth();
    const location = useLocation();
    const [moreOpen, setMoreOpen] = useState(false);
    const [accountOpen, setAccountOpen] = useState(false);
    const accountRef = useRef(null);
    const accountButtonRef = useRef(null);

    const primaryItems = primaryPaths.map((p) => navItems.find((n) => n.path === p));
    const moreItems = navItems.filter((n) => !primaryPaths.includes(n.path));
    const moreSections = groupNavItems(moreItems);
    const moreActive = moreItems.some((n) =>
        n.end ? location.pathname === n.path : location.pathname.startsWith(n.path)
    );
    const version = `v${systemVersion?.app_version || (typeof __APP_VERSION__ !== 'undefined' ? __APP_VERSION__ : '0.1.0')}`;

    const [isPinned, setIsPinned] = useState(() => {
        const saved = localStorage.getItem('sidebar-pinned');
        return saved !== null ? JSON.parse(saved) : true;
    });

    useEffect(() => {
        localStorage.setItem('sidebar-pinned', JSON.stringify(isPinned));
    }, [isPinned]);

    // Account menu: Esc and outside click close it; Esc returns focus to the button
    useEffect(() => {
        if (!accountOpen) return undefined;
        const onPointerDown = (e) => {
            if (!accountRef.current?.contains(e.target)) setAccountOpen(false);
        };
        const onKeyDown = (e) => {
            if (e.key === 'Escape') {
                setAccountOpen(false);
                accountButtonRef.current?.focus();
            }
        };
        document.addEventListener('mousedown', onPointerDown);
        document.addEventListener('keydown', onKeyDown);
        return () => {
            document.removeEventListener('mousedown', onPointerDown);
            document.removeEventListener('keydown', onKeyDown);
        };
    }, [accountOpen]);

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

                <nav className="sidebar-nav" aria-label="Primary">
                    {navSections.map(({ label: groupLabel, items }) => (
                        <div className="nav-group" key={groupLabel || 'top'}>
                            {groupLabel && <div className="nav-group-heading">{groupLabel}</div>}
                            {items.map(({ path, label, icon: Icon, end }) => (
                                <NavLink
                                    key={path}
                                    to={path}
                                    end={end}
                                    className={({ isActive }) =>
                                        `nav-item ${isActive ? 'active' : ''}`
                                    }
                                >
                                    <Icon size={20} strokeWidth={2} />
                                    <span>{label}</span>
                                </NavLink>
                            ))}
                        </div>
                    ))}
                </nav>

                <div className="sidebar-footer" ref={accountRef}>
                    {accountOpen && (
                        <div className="account-popover" id="account-menu" role="group" aria-label="Account">
                            {/* Q1: FWHM/HFR units for every page (per viewer, remembered) */}
                            <div className="sidebar-units">
                                <span>Star sizes</span>
                                <QualityUnitsToggle compact />
                            </div>
                            <button className="account-logout" onClick={logout}>
                                <LogOut size={18} />
                                <span>Log out</span>
                            </button>
                            <span className="account-version text-muted">{version}</span>
                        </div>
                    )}
                    <button
                        ref={accountButtonRef}
                        className="nav-item account-button"
                        onClick={() => setAccountOpen((open) => !open)}
                        aria-haspopup="true"
                        aria-expanded={accountOpen}
                        aria-controls={accountOpen ? 'account-menu' : undefined}
                        title={user?.email || 'Account'}
                    >
                        <User size={20} strokeWidth={2} />
                        <span className="account-email">{user?.email || 'Account'}</span>
                    </button>
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
                        <Icon size={20} strokeWidth={2} />
                        <span>{label}</span>
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
                {moreSections.map(({ label: groupLabel, items }) => (
                    <section className="more-section" key={groupLabel || 'top'}>
                        {groupLabel && <h3 className="more-heading">{groupLabel}</h3>}
                        <div className="more-grid">
                            {items.map(({ path, label, icon: Icon, end }) => (
                                <NavLink
                                    key={path}
                                    to={path}
                                    end={end}
                                    className={({ isActive }) => `more-item ${isActive ? 'active' : ''}`}
                                    onClick={() => setMoreOpen(false)}
                                >
                                    <Icon size={20} strokeWidth={2} />
                                    <span>{label}</span>
                                </NavLink>
                            ))}
                        </div>
                    </section>
                ))}
                <div className="more-footer">
                    <div className="sidebar-units">
                        <span>Star sizes</span>
                        <QualityUnitsToggle compact />
                    </div>
                    <button className="btn btn-secondary" onClick={logout}>
                        <LogOut size={18} /> Log out ({user?.email || 'User'})
                    </button>
                    <span className="text-muted more-version">{version}</span>
                </div>
            </BottomSheet>
        </div>
    );
}
