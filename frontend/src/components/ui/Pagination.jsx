import { useState } from 'react';
import { ChevronLeft, ChevronRight } from 'lucide-react';
import Button from './Button';
import './ui.css';

// Page numbers to render: first, last, current +/- 1; a gap of one page shows that page, larger gaps become null (ellipsis).
function buildItems(page, totalPages) {
    const keep = new Set([1, totalPages, page - 1, page, page + 1]);
    const nums = [...keep].filter((n) => n >= 1 && n <= totalPages).sort((a, b) => a - b);
    const items = [];
    nums.forEach((n, i) => {
        const prev = nums[i - 1];
        if (prev !== undefined && n - prev === 2) items.push(prev + 1);
        else if (prev !== undefined && n - prev > 2) items.push(null);
        items.push(n);
    });
    return items;
}

const JUMP_THRESHOLD = 7;

export default function Pagination({
    page,
    totalPages,
    onPageChange,
    totalItems,
    itemLabel = 'items',
    disabled = false,
    className = '',
    'aria-label': ariaLabel = 'Pagination',
}) {
    const [jump, setJump] = useState('');
    if (!totalPages || totalPages <= 1) return null;

    const go = (n) => {
        if (disabled || n === page || n < 1 || n > totalPages) return;
        onPageChange(n);
    };

    const submitJump = (e) => {
        e.preventDefault();
        const n = Number.parseInt(jump, 10);
        if (Number.isNaN(n)) return;
        setJump('');
        go(Math.min(totalPages, Math.max(1, n)));
    };

    const items = buildItems(page, totalPages);
    const summary = `Page ${page.toLocaleString()} of ${totalPages.toLocaleString()}`
        + (typeof totalItems === 'number' ? ` · ${totalItems.toLocaleString()} ${itemLabel}` : '');

    return (
        <nav className={`ui-pagination ${className}`.trim()} aria-label={ariaLabel}>
            <div className="ui-pagination-controls">
                <Button
                    variant="tinted"
                    size="sm"
                    icon={<ChevronLeft size={16} />}
                    disabled={disabled || page <= 1}
                    onClick={() => go(page - 1)}
                >
                    Previous
                </Button>

                <div className="ui-pagination-pages">
                    {items.map((n, i) => (n === null ? (
                        <span key={`gap-${i}`} className="ui-pagination-gap" aria-hidden="true">…</span>
                    ) : (
                        <Button
                            key={n}
                            variant={n === page ? 'filled' : 'plain'}
                            size="sm"
                            className={`ui-pagination-page${n === page ? ' is-current' : ''}`}
                            aria-current={n === page ? 'page' : undefined}
                            aria-label={`Page ${n}`}
                            disabled={disabled}
                            onClick={() => go(n)}
                        >
                            {n}
                        </Button>
                    )))}
                </div>

                <Button
                    variant="tinted"
                    size="sm"
                    disabled={disabled || page >= totalPages}
                    onClick={() => go(page + 1)}
                >
                    Next
                    <ChevronRight size={16} />
                </Button>
            </div>

            <div className="ui-pagination-meta">
                <span className="ui-pagination-summary">{summary}</span>
                {totalPages > JUMP_THRESHOLD && (
                    <form className="ui-pagination-jump" onSubmit={submitJump}>
                        <input
                            className="input ui-pagination-jump-input"
                            type="number"
                            inputMode="numeric"
                            min={1}
                            max={totalPages}
                            value={jump}
                            placeholder="Go to page"
                            aria-label="Go to page"
                            disabled={disabled}
                            onChange={(e) => setJump(e.target.value)}
                        />
                        <Button type="submit" variant="tinted" size="sm" disabled={disabled || jump === ''}>
                            Go
                        </Button>
                    </form>
                )}
            </div>
        </nav>
    );
}
