# D4: Acceptance checklist for the v20261028.08 downloads (D3 §5.1)

Run on **both** machines with the installers from the [release page](https://github.com/james474789/AstroCat/releases/tag/v20261028.08), not CI artifacts. Tick each box; for any failure note the step number, what you saw, and the newest file in `logs/` (tray → Open logs folder). Bugs jump the queue.

Test folder: one with a few FITS, CR2 and JPEG files (include at least one subfolder and one plate-solvable image).

## A. Install over the existing CI build (keeps your data)

- [ ] A1. Download the installer from the release page. (Windows: `AstroCat_26.1028.8_x64-setup.exe`; Mac: `AstroCat_26.1028.8_aarch64.dmg`.)
- [ ] A2. Quit AstroCat (tray → Quit), install over the old build (Windows: SmartScreen → More info → Run anyway; Mac: drag to Applications, replace, then right-click → Open or `xattr -dr com.apple.quarantine /Applications/AstroCat.app`).
- [ ] A3. Launch. Splash appears, then you're signed in automatically with no password prompt.
- [ ] A4. Your previously indexed library is still there.

## B. Clean install

- [ ] B1. Quit AstroCat. Uninstall it. Confirm the data folder survives ("Uninstall keeps data", as documented).
- [ ] B2. Delete the data folder. Windows: `%LOCALAPPDATA%\AstroCat`. Mac: `~/Library/Application Support/AstroCat` **and** `/Users/Shared/AstroCat/runtime`.
- [ ] B3. Install the release download again and launch. The first launch is slower; the splash appears, then you're signed in automatically.

## C. Indexing (on the clean install)

- [ ] C1. Tray → **Choose library folders…** → select the test folder.
- [ ] C2. Admin → Indexer → start a scan. FITS, CR2 and JPEG files are all indexed.
- [ ] C3. Thumbnails appear for all three types.
- [ ] C4. Admin → Indexer shows **non-zero per-folder counts** (D2 A1 fix).
- [ ] C5. Rescan the same folder: files are **not** all re-queued (D2 LIKE fix).
- [ ] C6. Delete one file from the folder and rescan. It disappears from the library.

## D. Features

- [ ] D1. Open the full-resolution viewer on an image.
- [ ] D2. Add an astrometry.net key under `"api_keys"` in `config.json` (see `docs/install-desktop.md`), restart via tray → Quit and reopen, then plate-solve one image. It gets a solution.
- [ ] D3. Search by object name (e.g. M31) and by coordinates. Both return results.
- [ ] D4. The Statistics page loads with charts.

## E. Lifecycle

- [ ] E1. Tray → Quit, then reopen. Data is still there.
- [ ] E2. While the app is running, sleep the computer and wake it. The app still works (pages load, search works).
- [ ] E3. After the app has run across a day boundary (or the next day), `backups/` contains a dated dump. (Tick later.)
- [ ] E4. Uninstall. The data folder is kept.

## Report back

For each machine: pass/fail per step, plus the failing step's log excerpt. Anything not listed above that felt off is worth reporting too.
