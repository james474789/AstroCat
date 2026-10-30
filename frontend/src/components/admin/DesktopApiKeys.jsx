import { useEffect, useState, useCallback } from 'react';
import { KeyRound } from 'lucide-react';
import { fetchDesktopKeys, saveDesktopKeys, waitForApiRestart } from '../../api/client';
import { Button, useToast } from '../ui';

// Desktop app only (D3 5.3): the integration keys Docker users put in .env. They are saved to
// the data folder's config.json; the launcher notices and restarts the app's services, so this
// screen waits for the API to go down and come back before it reloads.

const KEYS = [
    { name: 'ASTROMETRY_API_KEY', label: 'astrometry.net API key', help: 'Online plate solving (nova.astrometry.net).' },
    { name: 'LOCAL_ASTROMETRY_URL', label: 'Local astrometry server URL', help: 'Optional. For a self-hosted astrometry.net API.', plain: true },
    { name: 'LOCAL_ASTROMETRY_API_KEY', label: 'Local astrometry server key', help: 'Optional. Needed with the server URL.' },
    { name: 'TELESCOPIUS_API_KEY', label: 'Telescopius API key', help: 'Target planning data.' },
    { name: 'METEOBLUE_API_KEY', label: 'meteoblue API key', help: 'Planetary seeing forecasts.' },
];

export default function DesktopApiKeys() {
    const toast = useToast();
    const [keys, setKeys] = useState(null);
    const [drafts, setDrafts] = useState({});
    const [removed, setRemoved] = useState({});
    const [status, setStatus] = useState('idle'); // idle | saving | restarting

    const load = useCallback(() => fetchDesktopKeys().then(setKeys)
        .catch((err) => toast.error('Failed to load API keys: ' + err.message)), [toast]);
    useEffect(() => { load(); }, [load]);

    const changes = {};
    for (const { name } of KEYS) {
        const draft = (drafts[name] ?? '').trim();
        if (removed[name]) changes[name] = null;
        else if (draft !== '' && draft !== keys?.[name]?.value) changes[name] = draft;
    }
    const dirty = Object.keys(changes).length > 0;

    async function save() {
        setStatus('saving');
        try {
            await saveDesktopKeys(changes);
        } catch (err) {
            toast.error('Failed to save API keys: ' + err.message);
            setStatus('idle');
            return;
        }
        setStatus('restarting');
        const back = await waitForApiRestart();
        setDrafts({});
        setRemoved({});
        setStatus('idle');
        if (back) {
            await load();
            toast.success('API keys saved. Services restarted.');
        } else {
            toast.error('Saved, but the services did not come back within 2 minutes. Check the logs folder.');
        }
    }

    const busy = status !== 'idle';

    return (
        <section className="settings-section">
            <h2 className="section-title"><KeyRound size={18} aria-hidden="true" style={{ verticalAlign: '-3px', marginRight: '0.4rem' }} /> API Keys</h2>
            <p className="text-muted text-sm" style={{ margin: '0 0 1rem' }}>
                Keys for optional online services, stored in this computer&apos;s AstroCat data folder. Saving restarts
                AstroCat&apos;s background services for a few seconds. Stored keys are never shown again; leave a field empty to keep it.
            </p>
            {!keys && <div className="card text-muted text-sm" style={{ padding: '1rem' }}>Loading…</div>}
            {keys && (
                <div className="card">
                    {KEYS.map(({ name, label, help, plain }, i) => {
                        const entry = keys[name] || {};
                        const isRemoved = !!removed[name];
                        return (
                            <div key={name} style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '1rem', padding: '1rem', flexWrap: 'wrap', borderTop: i ? '1px solid var(--color-border)' : 'none' }}>
                                <div style={{ flex: '1 1 260px', minWidth: 0 }}>
                                    <div style={{ fontWeight: 'bold' }}>
                                        {label}
                                        {entry.set && !isRemoved && <span className="text-sm" style={{ marginLeft: '0.6rem', color: 'var(--color-success)' }}>set {entry.hint}</span>}
                                        {isRemoved && <span className="text-sm text-muted" style={{ marginLeft: '0.6rem' }}>will be removed</span>}
                                    </div>
                                    <div className="text-muted text-sm" style={{ marginTop: '0.25rem' }}>{help}</div>
                                </div>
                                <div style={{ display: 'flex', alignItems: 'center', gap: '0.5rem', flexWrap: 'wrap' }}>
                                    <input
                                        type={plain ? 'text' : 'password'}
                                        className="input"
                                        aria-label={label}
                                        autoComplete="off"
                                        spellCheck={false}
                                        placeholder={entry.set ? (plain ? entry.value : 'Replace stored key') : 'Not set'}
                                        value={drafts[name] ?? ''}
                                        disabled={busy || isRemoved}
                                        onChange={(e) => setDrafts((d) => ({ ...d, [name]: e.target.value }))}
                                        style={{ width: '260px', background: 'var(--color-border)', border: '1px solid var(--color-border-light)', color: 'var(--color-text-primary)', padding: '0.25rem 0.5rem', borderRadius: '0.25rem' }}
                                    />
                                    {entry.set && (
                                        <Button size="sm" variant="plain" disabled={busy}
                                            onClick={() => setRemoved((r) => ({ ...r, [name]: !r[name] }))}>
                                            {isRemoved ? 'Undo' : 'Remove'}
                                        </Button>
                                    )}
                                </div>
                            </div>
                        );
                    })}
                </div>
            )}
            <div style={{ marginTop: '1rem', display: 'flex', alignItems: 'center', gap: '1rem' }}>
                <Button onClick={save} disabled={!dirty || busy || !keys}>{status === 'saving' ? 'Saving…' : 'Save'}</Button>
                {status === 'restarting' && <span className="text-muted text-sm">Restarting services…</span>}
            </div>
        </section>
    );
}
