# O1: Online catalog overlays

Status: implemented 2026-10-09 (VERSION 20261009.09)

## Why
The Annotations overlay (A1, `20261007-A1-dynamic-sky-overlay.md`) draws the local catalogs: Messier, Caldwell, NGC/IC, Sh2 and named stars. This work asks whether catalogs looked up live, for the image being viewed, would add anything worthwhile.

## Research conclusion
| Candidate | Verdict | Why |
|---|---|---|
| **Asteroids & comets (IMCCE SkyBoT)** | **Added** | Positions depend on the capture time, so they can never be bundled. Moving dots in subs are a common "what is that?" question. |
| **Galaxies (HyperLeda PGC, VizieR VII/237)** | **Added** | About 1M galaxies; wide fields show many that OpenNGC lacks. Too big to bundle. |
| **Small deep-sky lists via VizieR** | **Added** | Each is tiny, and one generic adapter serves them all. The lists are LDN (VII/7A), LBN (VII/9), Barnard (VII/220A), vdB (VII/21), Strasbourg-ESO PNe including the Abell PNe (V/84), Abell/ACO clusters (VII/110A) and Arp (VII/192). |
| Variable stars (AAVSO VSX) | Declined | User choice. It could reuse the same adapter later (B/vsx). |
| Gaia / Tycho / UCAC stars | Rejected | Thousands of unlabeled points: noise, not information. |
| SIMBAD as an overlay | Rejected | Too dense and heterogeneous. Better as a future "identify at cursor" feature. |
| TNS supernovae | Rejected | Needs an API key and is niche. |

## Design
- **Off by default.** Admin > Online Catalogs switches each catalog on or off and sets its limit:
  - SkyBoT: faintest V, default 18.
  - PGC: minimum diameter. By default this is about 8 px at the image's plate scale.
  - Abell: faintest m10.

  The setting is stored in `system_settings.online_catalogs` and read through `quality_settings.runtime_settings()`. Only enabled catalogs appear in the viewer legend.
- **One request per layer:** `GET /api/images/{id}/sky-overlay/online/{key}`. Local annotations render immediately. Each online layer has its own spinner or error in the legend under "Online".
- **Backend package** `app/services/online_catalogs/`:
  - `registry.py` holds the specs and column mappers.
  - `vizier.py` queries the ASU `viz-bin/votable` interface. TAPVizieR was unavailable during development, and ASU computes J2000 for B1950/B1875 catalogs. The CfA mirror is the fallback.
  - `skybot.py` holds the SkyBoT adapter.
  - `service.py` handles planning, the Redis cache, back-off and de-duplication.
- **Projection** reuses `sky_overlay.resolve_frame` and `build_objects`. Unknown catalogs rank after the local ones (`catalog_rank`).
- **Cache:** Redis `skyonline:v1:{key}:{hash(query)}`, 30 days. The key is the query itself (cone, constraints, epoch), so it is shared by images of the same field. A failed query is backed off for 5 minutes (503 to the viewer), and the admin Test button bypasses both.
- **De-duplication:** PGC, Arp, LBN and PN objects already shown by local catalogs are dropped. A match is a shared designation or alias (for example `NGC224` in PGC's alternate names), or a non-star local object within 0.5′.
- **SkyBoT details:**
  - The epoch is `capture_date_utc` plus half the exposure. The observer is geocentric (code 500); topocentric parallax only matters for close NEOs.
  - Fields with a radius over 5° are refused: a 3° cone already takes about 30 s.
  - No capture time gives the legend note "No capture time".
  - Stacks and file-date capture times get a warning.
  - nginx `proxy_read_timeout` is raised to 120 s for these slow lookups.
- **Clicking an online object** opens its external page in a new tab: HyperLeda, SIMBAD or JPL SBDB. Local objects still search the library.

## Data credits
CDS VizieR (Strasbourg astronomical Data Center) and IMCCE SkyBoT (Paris Observatory), as acknowledged in the Admin section.

## Tests
`backend/tests/test_online_catalogs.py` runs on VOTable and JSON fixtures recorded from the live services (`tests/fixtures/online/`), with no network.
