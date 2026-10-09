import { forwardRef, useEffect } from 'react';
import { Link } from 'react-router-dom';
import { Loader2 } from 'lucide-react';
import './ui.css';

// Built on the legacy .btn classes so un-migrated markup and this component look the same.
const VARIANT_CLASS = {
    filled: 'btn-primary',
    tinted: 'ui-btn-tinted',
    plain: 'btn-ghost',
    destructive: 'ui-btn-destructive',
};

const Button = forwardRef(function Button(
    {
        variant = 'tinted',
        size = 'md',
        icon,
        iconOnly = false,
        loading = false,
        as,
        to,
        type,
        disabled = false,
        className = '',
        children,
        ...rest
    },
    ref
) {
    const unlabelled = iconOnly && !rest['aria-label'] && !rest['aria-labelledby'];
    useEffect(() => {
        if (import.meta.env.DEV && unlabelled) {
            console.warn('Button: iconOnly buttons need an aria-label (or aria-labelledby).');
        }
    }, [unlabelled]);

    const Component = as || (to ? Link : 'button');
    const isNativeButton = Component === 'button';
    const inactive = disabled || loading;
    const classes = [
        'btn',
        'ui-btn',
        VARIANT_CLASS[variant] || VARIANT_CLASS.tinted,
        size === 'sm' && 'btn-sm',
        iconOnly && 'btn-icon',
        loading && 'is-loading',
        !isNativeButton && inactive && 'is-disabled',
        className,
    ].filter(Boolean).join(' ');

    const leading = loading
        ? <Loader2 className="ui-spin" size={size === 'sm' ? 14 : 16} aria-hidden="true" />
        : icon;

    return (
        <Component
            {...rest}
            ref={ref}
            to={to}
            className={classes}
            type={isNativeButton ? (type || 'button') : type}
            disabled={isNativeButton ? inactive : undefined}
            aria-disabled={!isNativeButton && inactive ? true : undefined}
            aria-busy={loading || undefined}
            title={rest.title ?? (iconOnly ? rest['aria-label'] : undefined)}
        >
            {leading}
            {!iconOnly && children}
        </Component>
    );
});

export default Button;
