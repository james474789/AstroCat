# Installing the AstroCat desktop app

Download the file for your computer from the [latest release](https://github.com/james474789/AstroCat/releases/latest):

| Platform | File |
|---|---|
| Windows 10/11 (64-bit) | `AstroCat_<version>_x64-setup.exe` |
| macOS 12 or later, Apple Silicon (M1 and newer) | `AstroCat_<version>_aarch64.dmg` |

The app includes its own database, cache and background workers. It doesn't need Docker or an internet connection, except for online plate solving and weather features.

## Windows

1. Run `AstroCat_<version>_x64-setup.exe`. It installs for your user only and needs no admin rights.
2. The installer isn't code-signed yet, so Windows SmartScreen may say **"Windows protected your PC"**. Click **More info**, then **Run anyway**. You only need to do this once per version.
3. Start AstroCat from the Start menu.

## macOS

1. Open the `.dmg` and drag **AstroCat** into **Applications**.
2. The app isn't notarized yet, so macOS blocks the first launch. Do one of the following:
   - In Finder, **right-click** (or Control-click) AstroCat in Applications, choose **Open**, then **Open** again. On macOS 15 (Sequoia) and later, try to open it once, then go to **System Settings → Privacy & Security** and click **Open Anyway**.
   - Or, in Terminal: `xattr -dr com.apple.quarantine /Applications/AstroCat.app`
3. Later launches open normally.

## First run

- The first launch takes a little longer while AstroCat creates its database. You'll see a splash screen, then the app signs you in automatically. The desktop app has a single local user and no password.
- Use the **AstroCat icon** in the system tray (Windows) or the menu bar (macOS) → **Choose library folders…** and select every folder AstroCat should index. Then start a scan from **Admin → Indexer**.
- Closing the window quits AstroCat and stops its background services.

## Where your data lives

| | Windows | macOS |
|---|---|---|
| Database, thumbnails, settings | `%LOCALAPPDATA%\AstroCat` | `~/Library/Application Support/AstroCat` |
| Logs | `…\AstroCat\logs` (tray → **Open logs folder**) | `…/AstroCat/logs` |
| Daily database backups (last 7) | `…\AstroCat\backups` | `…/AstroCat/backups` |

On macOS the app also keeps a symlink at `/Users/Shared/AstroCat/runtime` pointing into the app, because its bundled database server must run from that path. It's recreated at every start, and you can delete it after uninstalling.

Uninstalling the app keeps this folder. Delete it yourself if you want to remove your library database too. Your image files are never modified.

## Upgrading

Install a newer version over the old one; your data folder is kept and the database is updated on the next start.

The bundled database server is PostgreSQL 18, and its data can only be opened by the same major version. If a future AstroCat ships a different major version before an automatic upgrade exists, it refuses to start with "Your library database was created by PostgreSQL …" instead of touching your data. Reinstall the previous AstroCat version in that case; the daily backups in `backups/` are also safe to keep.

## Optional API keys

Online plate solving (astrometry.net), a local astrometry server, and the Telescopius and meteoblue integrations need keys. Add them under `"api_keys"` in `config.json` in the data folder above, then restart AstroCat:

```json
"api_keys": {
  "ASTROMETRY_API_KEY": "your-nova-key",
  "LOCAL_ASTROMETRY_URL": "",
  "LOCAL_ASTROMETRY_API_KEY": "",
  "TELESCOPIUS_API_KEY": "",
  "METEOBLUE_API_KEY": ""
}
```

User environment variables with the same names also work.
