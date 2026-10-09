import ItemContent from './ItemContent';
import useRovingFocus from './useRovingFocus';
import './ui.css';

// Pill-style single choice for 2-4 options (Grid | List). Same item API as Tabs, but
// exposed as a radio group because it switches a view mode, not a panel.
export default function SegmentedControl({ items, value, onChange, size = 'md', className = '', ...rest }) {
    const getItemProps = useRovingFocus(items, value, onChange);
    return (
        <div {...rest} role="radiogroup" className={`ui-segmented ui-segmented-${size} ${className}`.trim()}>
            {items.map((item) => {
                const selected = item.value === value;
                return (
                    <button
                        key={item.value}
                        {...getItemProps(item)}
                        role="radio"
                        aria-checked={selected}
                        className={`ui-segment${selected ? ' is-active' : ''}`}
                    >
                        <ItemContent item={item} />
                    </button>
                );
            })}
        </div>
    );
}
