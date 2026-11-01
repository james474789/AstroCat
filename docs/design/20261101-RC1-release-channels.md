# RC1: Beta and stable release channels

**Status:** implemented, not yet rolled out (§4) · **Created:** 2026-11-01
**Code:** step 1 (desktop toggle, `main.rs`) in PR 35; beta publishing (`release.yml`), `promote.yml` and `desktop/scripts/semver_newer.py` in the stacked PR on `claude/rc1-beta-publish`. · **Base commit:** `a6c11d2` (`main`, tag `v20261101.13`)
**Builds on:** [D2](20261028-D2-desktop-parity-and-release.md) §7 (first release) and §9 E1 (auto-updater).

This is a handover document. It says what to build, in what order, how to check it, and where to stop and ask the user. If something on the ground contradicts it, stop and ask; don't improvise.

---

## 0. Problem

Every `v*` tag is a full public release. `release.yml` pushes `astrocat-backend:latest` and `astrocat-frontend:latest`, publishes a normal GitHub Release, and that release becomes `releases/latest`, which is the desktop updater's endpoint (`tauri.conf.json:56`). Every desktop user gets an "Update available" dialog, and every Docker user who follows `:latest` gets the new images.

The QA loop tags after each batch of merged fixes so the Mac tester can retest through the updater. That is the right pace for QA and the wrong pace for users: 6 releases on 2026-11-01 and 20 between 2026-10-28 and 2026-11-01.

The tester and end users share one channel. RC1 splits it in two.

## 1. Design in one paragraph

Every tag still builds everything once, exactly as now, but publishes as a **beta**: a GitHub *prerelease*, Docker tag `:beta`, and a beta updater manifest. **Stable** is a separate, manual **promotion** of a beta that QA has passed: it flips that same GitHub Release to a normal "latest" release and re-points Docker `:latest` at the already-built images. Nothing is rebuilt, so stable users get the exact bytes the tester checked. There is no stable branch; everything still comes from `main`.

```
merge to main ──► tag vYYYYMMDD.NN ──► release.yml (unchanged build)
                                         │
                                         ├─ GitHub prerelease        ──► beta updater manifest
                                         └─ Docker :VERSION + :beta
                                                     │
                     owner: Actions > Promote (tag) ─┘
                                         │
                                         ├─ same release → normal + latest ──► releases/latest/…/latest.json
                                         └─ Docker :latest → :VERSION (retag, no rebuild)
```

### Why not a stable branch

A long-lived `stable` branch means every fix is either cherry-picked or back-merged, and CI and the QA loop have to know which branch they are on. That only pays off when a stable fix has to ship while `main` holds unreleased risky work. That hasn't happened yet. §7 describes the escape hatch for when it does.

### Why no `-beta` suffix on tags or versions

`release.yml` requires the tag to equal `v$(cat VERSION)` (`version.py --check-tag`), and the desktop version is `YY.MMDD.NN`, with no semver prerelease part. A suffix would sort *below* the matching release in Tauri's semver comparison, add a case to `version.py` and the Windows version resource, and mean a promoted build has a different version from the one the tester ran. Keeping one tag per build, with the channel recorded on the GitHub Release rather than in the version, avoids all of that.

## 2. Channel definitions

| | Beta | Stable |
|---|---|---|
| Made by | every `v*` tag (QA loop, as now) | `promote.yml`, run by the owner |
| GitHub Release | `prerelease: true`, not latest | `prerelease: false`, `--latest` |
| Docker | `:VERSION`, `:beta` | `:VERSION`, `:latest` |
| Updater manifest URL | `https://github.com/james474789/AstroCat/releases/download/beta/latest.json` | `https://github.com/james474789/AstroCat/releases/latest/download/latest.json` (unchanged) |
| `docker-compose-example.yml` | — | `:latest` (unchanged) |
| Who | the tester, opted-in users, optionally prod (Q2) | everyone else |

The beta manifest lives on a rolling GitHub prerelease with tag `beta` (it doesn't start with `v`, so it never triggers `release.yml`). Only its `latest.json` asset matters; its git tag can stay where it was created. It must stay a prerelease so it can never become `releases/latest`.

Beta always holds the newest build, because every build is a beta first. So promotion never has to touch the beta manifest.

**Switching channels.** Desktop versions only go up. A user who moves from beta back to stable stays on their newer beta build until a stable release passes it; the updater just says "up to date". There are no downgrades. `install-desktop.md` says so.

## 3. Changes

### 3.1 `release.yml` (publish job)

1. `softprops/action-gh-release`: add `prerelease: true` and `make_latest: false`. Name the release `AstroCat ${version} (beta)`, and add a line to the body: "Beta build. Stable releases are marked Latest."
2. After the release step, publish the beta manifest:
   create the `beta` prerelease if it's missing, download its current `latest.json`, and upload the new one with `--clobber` only when `desktop/scripts/semver_newer.py NEW CURRENT` says it's newer. That check stops an older run that finishes late from overwriting a newer manifest. It compares the semver parts as integers, not as strings.
3. Add `concurrency: { group: release-publish, cancel-in-progress: false }` to the publish job, so two publishes never upload at the same time.
4. Docker job: replace the `:latest` tag with `:beta`.

`ci-gate`, the build matrix and `updater_manifest.py`'s manifest format don't change. Each versioned release still carries its own `latest.json`; that is what makes `releases/latest/download/latest.json` work after promotion.

### 3.2 New `promote.yml`

`workflow_dispatch` with input `tag` (e.g. `v20261101.13`) and an optional `force` boolean. Permissions: `contents: write`. Secrets: `DOCKERHUB_USERNAME`, `DOCKERHUB_TOKEN`.

1. **Checks** (fail with a clear message):
   - the release exists, is a prerelease, and has `latest.json`, the `.exe` and the `.dmg`;
   - Docker Hub has `astrocat-{backend,frontend}:${VERSION}`;
   - the tag is newer than the current stable (`gh release view --json tagName` with no tag gives latest), unless `force` is set (a deliberate rollback).
2. **Notes** (only when promoting a beta; a rollback keeps the old release's notes): generate release notes from the previous stable tag to this one (`gh api repos/{repo}/releases/generate-notes -f tag_name=… -f previous_tag_name=…`), so the stable notes cover every beta since the last stable. Keep the existing "Downloads" block on top: `release.yml` puts a `<!-- notes -->` marker after it, and promotion keeps everything above the marker (minus the beta banner) and replaces everything below it.
3. **Flip:** `gh release edit "$TAG" --prerelease=false --latest --title "AstroCat $VERSION" --notes-file notes.md`.
4. **Docker:** `docker buildx imagetools create -t ns/astrocat-$img:latest ns/astrocat-$img:$VERSION` for both images. This retags the manifest list; it doesn't rebuild or re-push layers.
5. **Verify:** fetch `releases/latest/download/latest.json` (follow redirects) until `.version` equals `to_semver(VERSION)`, with a timeout of about 2 minutes. Fetch Docker Hub's `tags/latest` digest and compare it with `tags/$VERSION`.

Order matters: GitHub first, Docker second. If the Docker step fails, rerunning `promote` with the same tag must work, so the checks in step 1 accept a tag that is already stable when it is also the current latest and only Docker is behind.

### 3.3 Desktop app: update channel setting (`desktop/src-tauri/src/main.rs`)

- **Setting:** `update_channel` in the data dir's `config.json`, `"stable"` (default when missing) or `"beta"`. Check that `backend/app/desktop/paths.py` tolerates an unknown key; it should, since `image_paths` is already written from Rust.
- **Tray:** add a `CheckMenuItem` "Get beta updates" next to "Check for updates…". Toggling it writes `config.json` (same tmp-then-rename pattern as `add_image_paths`) and runs a manual check right away.
- **Check:** in `check_for_update`, build the updater per call with the channel's endpoint instead of `app.updater()`:
  ```rust
  let url = match read_channel(app) { Channel::Beta => BETA_ENDPOINT, Channel::Stable => STABLE_ENDPOINT };
  let updater = app.updater_builder()
      .endpoints(vec![url.parse().map_err(|e: url::ParseError| e.to_string())?])
      .map_err(|e| e.to_string())?
      .build()
      .map_err(|e| e.to_string())?;
  ```
  `endpoints` returns a `Result` in `tauri-plugin-updater` 2.x; check the locked version in `Cargo.lock`. Leave the stable URL in `tauri.conf.json` as the fallback.
- **Dialog text:** when on beta, the "Update available" dialog says "AstroCat {version} (beta) is available."
- No other updater behaviour changes: it still never installs without the user's click.

### 3.4 Docs

- `docs/install-desktop.md`: a short "Beta updates" section (what beta is, how to switch, no downgrades).
- `docs/infrastructure/` (or wherever the release process is documented; search for `git tag v` first): the release flow, promotion, and the criteria from Q1.
- `release.yml` header comment: the beta/promote flow in three lines.

### 3.5 Outside this repo (owner approval needed)

- **astrocat-qa** `docs/PROTOCOL.md`, `DEV.md` and `TESTER.md`: `needs-retest` after the **beta** manifest shows the version. The tester turns on "Get beta updates" once.
- **Claude memory:** update `feedback_batch_releases.md` and `project_qa_workflow.md` (the loop tags beta builds only; stable is the owner's call; verify `releases/download/beta/latest.json`, not `releases/latest/…`).
- **Prod deploy runbook** (`reference_deploy_to_prod.md`): today it builds locally and pushes `:latest`, which would bypass the channels. Change it to deploy a published tag (`:VERSION`, or `:beta`/`:latest` per Q2) and stop pushing `:latest` from local builds.

## 4. Rollout (order matters)

The tester's installed app has no channel setting, and the moment tags become prereleases it stops seeing new builds. So:

1. **Land 3.3 (desktop channel setting) first**, on its own, released the old way (a normal stable release). The tester updates to it, then turns on "Get beta updates". Until 3.1 lands, the beta endpoint is a 404 and the background check stays silent, which is harmless.
2. **Create the `beta` rolling release** by hand, uploading that release's `latest.json`, so beta users have a manifest straight away.
3. **Land 3.1 + 3.2 + 3.4** together. From the next tag on, builds are beta.
4. **Dry run:** the next QA tag should appear as a prerelease; the tester's app offers it; a stable install doesn't. Then run `promote` on it and check that the stable install now offers it and Docker `:latest` moved.
5. **Update 3.5** (QA protocol, memories, prod runbook) once step 4 passes.

## 5. Verification (before any push)

- `python -m pytest desktop/runtime -q`: add tests for the `--newer` comparison (`26.1101.13 > 26.1101.9`, which string comparison gets wrong) and keep the manifest tests passing.
- `cargo check` and `cargo clippy` in `desktop/src-tauri`.
- `actionlint` on `release.yml` and `promote.yml` if available; otherwise run `release.yml` with `workflow_dispatch` (its existing dry-run mode publishes nothing).
- The first real run of `promote.yml` is step 4 of the rollout, with the owner watching.
- No UI testing by the agent: the tester and owner check the tray toggle and dialogs.

## 6. Decisions (answered by the owner, 2026-11-01)

| # | Question | Decision |
|---|---|---|
| Q1 | When does a beta become stable? | Owner-triggered, roughly weekly. Criteria: the build has been on beta for ≥ 3 days, the tester has retested its fixes, and there's no open `sev:1`/`sev:2` against it in astrocat-qa. A `sev:1`/`sev:2` fix may be promoted as soon as it passes retest. |
| Q2 | Does prod (Portainer stack 6) run beta or stable? | Beta (`:beta` or a pinned `:VERSION`): the owner dogfoods ahead of users. |
| Q3 | Should betas keep coming from the QA loop, or from a nightly cron? | Keep the loop, with today's batching. The user-facing noise was the problem, and that goes away with channels; a cron would slow the tester down. Revisit if CI minutes matter. |
| Q4 | May the promote workflow be run by the dev agent, or only by the owner? | Owner only. That is the point of stable. Add to the QA memory that the agent never runs it. |

## 7. Out of scope / later

- **Hotfix branch.** If a stable fix must ship while `main` has unreleased risky work: branch `release/<stable-version>` from the stable tag, cherry-pick, bump VERSION, tag, promote. This needs `ci.yml` to run on pushes to `release/**`, because `ci-gate` waits for a push-event CI run on the tagged commit. Add it the first time it's needed, not now.
- **Release notifications.** GitHub "Watch → Releases" also notifies on prereleases. Not worth fixing.
- **Code signing, smaller dmg:** unchanged, see D2 §9.

## 8. Estimates and recommended models

| Stage | Effort | Tokens (approx.) | Recommended model | Notes |
|---|---|---|---|---|
| Read RC1, `release.yml`, `main.rs` updater code | 15 min | 30k | — | once per session |
| 3.3 desktop channel setting + tray toggle | 2–3 h | 70k | Opus 5.5 | Rust, updater builder API; first in rollout |
| 3.1 `release.yml` beta publishing + `--newer` + tests | 1.5 h | 50k | Opus 5.5 | race and ordering details |
| 3.2 `promote.yml` | 2 h | 60k | Opus 5.5 | rerun-safety, Docker retag |
| 3.4 docs | 30 min | 15k | Sonnet 5.5 | |
| CI round(s) and dry runs (rollout steps 1–4) | 1–2 h wall | 30k | same session | mostly waiting on Release runs |
| 3.5 QA protocol, memories, prod runbook | 30 min | 15k | Sonnet 5.5 | apply the §6 decisions |
| **Total** | **≈1 day** | **≈270k** | one Opus 5.5 session; Sonnet 5.5 for 3.4/3.5 if it can switch | |

**Single-model recommendation:** Opus 5.5. Most of the risk is in workflow ordering and the updater, where one mistake costs a broken release for real users.
