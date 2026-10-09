import { useEffect, useId, useRef, useState } from 'react';
import { Check } from 'lucide-react';
import { Button } from '../ui';
import './ActionMenu.css';

const ITEM_SELECTOR = '[role^="menuitem"]:not(:disabled)';

// A button that opens a small popover menu (WAI-ARIA menu button pattern).
// items: [{ id, label, icon, hint, onSelect, disabled, checked }] plus
// { type: 'separator' } and { type: 'heading', label }. An item with a boolean
// `checked` renders as menuitemradio. Arrow keys / Home / End move focus, Enter/Space
// choose, Esc closes and returns focus to the trigger; clicking outside or tabbing
// away closes too. Other props (variant, icon, iconOnly, aria-label, disabled, title,
// children) go to the trigger <Button>. `align` = 'start' | 'end' (popover edge).
export default function ActionMenu({ items, align = 'start', menuLabel, className = '', children, ...triggerProps }) {
    const [open, setOpen] = useState(false);
    const rootRef = useRef(null);
    const triggerRef = useRef(null);
    const menuRef = useRef(null);
    const menuId = useId();

    function menuItems() {
        return Array.from(menuRef.current?.querySelectorAll(ITEM_SELECTOR) || []);
    }

    function focusItem(index) {
        const nodes = menuItems();
        if (!nodes.length) return;
        nodes[((index % nodes.length) + nodes.length) % nodes.length].focus();
    }

    // On open, focus the checked item (radio menus) or the first one.
    useEffect(() => {
        if (!open || !menuRef.current) return;
        const nodes = Array.from(menuRef.current.querySelectorAll(ITEM_SELECTOR));
        (nodes.find((node) => node.getAttribute('aria-checked') === 'true') || nodes[0])?.focus();
    }, [open]);

    useEffect(() => {
        if (!open) return undefined;
        const onPointerDown = (event) => {
            if (!rootRef.current?.contains(event.target)) setOpen(false);
        };
        document.addEventListener('pointerdown', onPointerDown);
        return () => document.removeEventListener('pointerdown', onPointerDown);
    }, [open]);

    function close(returnFocus) {
        setOpen(false);
        if (returnFocus) triggerRef.current?.focus();
    }

    function onTriggerKeyDown(event) {
        if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
            event.preventDefault();
            setOpen(true);
        }
    }

    function onMenuKeyDown(event) {
        const index = menuItems().indexOf(document.activeElement);
        switch (event.key) {
            case 'ArrowDown': focusItem(index + 1); break;
            case 'ArrowUp': focusItem(index - 1); break;
            case 'Home': focusItem(0); break;
            case 'End': focusItem(menuItems().length - 1); break;
            case 'Escape': close(true); break;
            case 'Tab': close(false); return;
            default: return;
        }
        event.preventDefault();
        event.stopPropagation();
    }

    function choose(item) {
        // Focus returns to the trigger first, so a Dialog opened by onSelect hands focus back there.
        close(true);
        item.onSelect?.();
    }

    return (
        <div ref={rootRef} className={`action-menu ${className}`.trim()}>
            <Button
                {...triggerProps}
                ref={triggerRef}
                aria-haspopup="menu"
                aria-expanded={open}
                aria-controls={open ? menuId : undefined}
                onClick={() => setOpen((v) => !v)}
                onKeyDown={onTriggerKeyDown}
            >
                {children}
            </Button>
            {open && (
                <div
                    ref={menuRef}
                    id={menuId}
                    role="menu"
                    aria-label={menuLabel || triggerProps['aria-label'] || (typeof children === 'string' ? children : undefined)}
                    className={`action-menu-popover action-menu-${align}`}
                    onKeyDown={onMenuKeyDown}
                >
                    {items.map((item, i) => {
                        if (item.type === 'separator') {
                            return <div key={`sep-${i}`} role="separator" className="action-menu-separator" />;
                        }
                        if (item.type === 'heading') {
                            return <div key={`head-${i}`} role="presentation" className="action-menu-heading">{item.label}</div>;
                        }
                        const radio = typeof item.checked === 'boolean';
                        return (
                            <button
                                key={item.id}
                                type="button"
                                role={radio ? 'menuitemradio' : 'menuitem'}
                                aria-checked={radio ? item.checked : undefined}
                                disabled={item.disabled}
                                tabIndex={-1}
                                className="action-menu-item"
                                onClick={() => choose(item)}
                            >
                                <span className="action-menu-icon" aria-hidden="true">
                                    {radio ? (item.checked && <Check size={14} />) : item.icon}
                                </span>
                                <span className="action-menu-text">
                                    <span className="action-menu-label">{item.label}</span>
                                    {item.hint && <span className="action-menu-hint">{item.hint}</span>}
                                </span>
                            </button>
                        );
                    })}
                </div>
            )}
        </div>
    );
}
