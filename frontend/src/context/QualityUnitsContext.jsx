import { createContext, useContext, useEffect, useState, useCallback } from 'react';
import { fetchSettings } from '../api/client';

// Q1 (docs/design/20260927-Q1-star-quality.md §12.3): FWHM/HFR display units, arcsec or px.
// Each viewer's choice lives in localStorage; until they pick one, the admin
// default from Settings (quality_units) applies, else arcsec.

const STORAGE_KEY = 'astrocat.qualityUnits';
const UNITS = ['ARCSEC', 'PX'];

const QualityUnitsContext = createContext({ units: 'ARCSEC', setUnits: () => {} });

function readStored() {
    try {
        const v = localStorage.getItem(STORAGE_KEY);
        return UNITS.includes(v) ? v : null;
    } catch {
        return null;
    }
}

export function QualityUnitsProvider({ children }) {
    const [stored, setStored] = useState(readStored);
    const [serverDefault, setServerDefault] = useState('ARCSEC');

    useEffect(() => {
        let cancelled = false;
        fetchSettings()
            .then((s) => {
                if (!cancelled && UNITS.includes(s?.quality_units)) setServerDefault(s.quality_units);
            })
            .catch(() => {});
        return () => { cancelled = true; };
    }, []);

    const setUnits = useCallback((next) => {
        if (!UNITS.includes(next)) return;
        setStored(next);
        try {
            localStorage.setItem(STORAGE_KEY, next);
        } catch {
            // Private window / blocked storage: the choice lasts for this page load.
        }
    }, []);

    return (
        <QualityUnitsContext.Provider value={{ units: stored || serverDefault, setUnits }}>
            {children}
        </QualityUnitsContext.Provider>
    );
}

export function useQualityUnits() {
    return useContext(QualityUnitsContext);
}
