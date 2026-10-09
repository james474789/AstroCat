import { useRef } from 'react';

// Roving tabindex shared by Tabs and SegmentedControl: one tab stop for the group,
// arrow keys / Home / End move focus and select (automatic activation), disabled items are skipped.
export default function useRovingFocus(items, value, onChange) {
    const nodes = useRef(new Map());
    const enabled = items.filter((item) => !item.disabled);
    const tabStop = enabled.some((item) => item.value === value) ? value : enabled[0]?.value;

    function select(item) {
        onChange?.(item.value);
        nodes.current.get(item.value)?.focus();
    }

    function handleKeyDown(event, item) {
        const index = enabled.findIndex((candidate) => candidate.value === item.value);
        const last = enabled.length - 1;
        let target;
        switch (event.key) {
            case 'ArrowRight':
            case 'ArrowDown':
                target = enabled[index >= last ? 0 : index + 1];
                break;
            case 'ArrowLeft':
            case 'ArrowUp':
                target = enabled[index <= 0 ? last : index - 1];
                break;
            case 'Home':
                target = enabled[0];
                break;
            case 'End':
                target = enabled[last];
                break;
            default:
                return;
        }
        event.preventDefault();
        if (target) select(target);
    }

    // Props for the <button> of one item; the caller adds role/aria-selected/aria-checked.
    function getItemProps(item) {
        return {
            ref: (node) => {
                if (node) nodes.current.set(item.value, node);
                else nodes.current.delete(item.value);
            },
            type: 'button',
            tabIndex: item.value === tabStop ? 0 : -1,
            disabled: item.disabled,
            'aria-label': item.ariaLabel,
            onClick: () => onChange?.(item.value),
            onKeyDown: (event) => handleKeyDown(event, item),
        };
    }

    return getItemProps;
}
