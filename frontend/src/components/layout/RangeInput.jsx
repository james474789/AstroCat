import './RangeInput.css';

/**
 * Improved range input component with better UX
 * Allows users to set min and max values with visual feedback
 */
export default function RangeInput({
    label,
    minLabel = 'Min',
    maxLabel = 'Max',
    minValue = '',
    maxValue = '',
    onMinChange,
    onMaxChange,
    placeholder = { min: 'Min', max: 'Max' },
    unit = '',
    step = '1',
    type = 'number'
}) {
    return (
        <div className="range-input-group">
            {label && <label className="label">{label}</label>}
            
            <div className="range-input-container">
                <div className="range-input-field">
                    <input
                        type={type}
                        className="input range-field"
                        placeholder={placeholder.min}
                        value={minValue}
                        onChange={(e) => onMinChange(e.target.value)}
                        step={step}
                    />
                    {minLabel && <span className="range-field-label">{minLabel}</span>}
                </div>

                <span className="range-separator">→</span>

                <div className="range-input-field">
                    <input
                        type={type}
                        className="input range-field"
                        placeholder={placeholder.max}
                        value={maxValue}
                        onChange={(e) => onMaxChange(e.target.value)}
                        step={step}
                    />
                    {maxLabel && <span className="range-field-label">{maxLabel}</span>}
                </div>

                {unit && <span className="range-unit">{unit}</span>}
            </div>
        </div>
    );
}
