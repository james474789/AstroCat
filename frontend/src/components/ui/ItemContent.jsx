import formatCount from './formatCount';

// Inner content of one Tabs / SegmentedControl item: icon, label, optional count badge.
export default function ItemContent({ item }) {
    return (
        <>
            {item.icon && <span className="ui-tab-icon" aria-hidden="true">{item.icon}</span>}
            {item.label && <span>{item.label}</span>}
            {item.count != null && <span className="ui-tab-count">{formatCount(item.count)}</span>}
        </>
    );
}
