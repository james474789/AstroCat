import ItemContent from './ItemContent';
import useRovingFocus from './useRovingFocus';
import './ui.css';

const tabId = (prefix, value) => `${prefix}-tab-${value}`;
const panelId = (prefix, value) => `${prefix}-panel-${value}`;

// WAI-ARIA tabs with roving tabindex. items: [{ value, label, icon, count, disabled, ariaLabel }].
// Pass panelIdPrefix (and wrap content in <TabPanel> with the same prefix) to link tabs and panels.
export default function Tabs({ items, value, onChange, panelIdPrefix, className = '', ...rest }) {
    const getItemProps = useRovingFocus(items, value, onChange);
    return (
        <div {...rest} role="tablist" className={`ui-tabs ${className}`.trim()}>
            {items.map((item) => {
                const selected = item.value === value;
                return (
                    <button
                        key={item.value}
                        {...getItemProps(item)}
                        role="tab"
                        id={panelIdPrefix ? tabId(panelIdPrefix, item.value) : undefined}
                        aria-controls={panelIdPrefix ? panelId(panelIdPrefix, item.value) : undefined}
                        aria-selected={selected}
                        className={`ui-tab${selected ? ' is-active' : ''}`}
                    >
                        <ItemContent item={item} />
                    </button>
                );
            })}
        </div>
    );
}

// The panel for one tab; render only the active one (or pass hidden).
export function TabPanel({ panelIdPrefix, value, className = '', children, ...rest }) {
    return (
        <div
            {...rest}
            role="tabpanel"
            id={panelId(panelIdPrefix, value)}
            aria-labelledby={tabId(panelIdPrefix, value)}
            tabIndex={0}
            className={`ui-tab-panel ${className}`.trim()}
        >
            {children}
        </div>
    );
}
