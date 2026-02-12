# AstroCat Frontend Documentation

The AstroCat frontend is a modern, responsive React application built with Vite. It features a custom "Deep Space" aesthetic designed for dark environments typical of astronomical work.

## Core Technologies

- **React 18**: Component-based UI library.
- **Vite**: Ultra-fast build tool and development server.
- **TanStack Query (React Query)**: For server-state management, caching, and synchronization.
- **React Router**: For client-side routing.
- **Vanilla CSS**: Used for the design system to ensure maximum flexibility and performance.

## Design System

The application uses a comprehensive design system defined in `src/index.css`. It relies heavily on CSS variables for consistent theming and easy maintenance.

### Key Tokens (Selection)
- `--color-background`: `#0a0e17` (Deep space black)
- `--color-primary`: `#5b8dee` (Nebula blue)
- `--color-accent`: `#22d3ee` (Stellar cyan)
- `--font-family`: `Inter`, system-ui

### Visual Features
- **Glassmorphism**: Subtle translucent effects on overlays.
- **Micro-animations**: Smooth transitions for hover states and page changes.
- **Starfield Background**: An animated CSS background to reinforce the astronomical theme.
- **Interactive Elements**:
  - **Keyboard Navigation**: Left/Right arrow keys for navigating image details.
  - **Thumbnail Slider**: Adjustable thumbnail size on search results.
  - **Smart Sidebar**: Collapsible sidebar with hover expansion.

## Architecture

### Component Hierarchy
- `src/components/layout/`: Global elements like Navbar and Sidebar.
- `src/components/images/`: Image-specific components (Grid, Card, Viewer).
- `src/components/search/`: Advanced filter controls and coordinate inputs.
- `src/components/catalogs/`: Catalog browsers and list items.

### State Management
AstroCat uses **TanStack Query** to handle API interactions. This provides:
- Automatic caching of image lists and metadata.
- Background refetching to keep the data fresh.
- Easy handling of loading and error states.

### Data Fetching
The `src/api/client.js` provides a unified wrapper around the browser's `fetch` API, handling base URLs, headers, and response parsing.

### Real-time Updates (Polling)
For long-running processes like Astrometry.net plate solving, the frontend implements a polling pattern:
1.  **Immediate Feedback**: User actions (e.g., "Start Rescan") trigger an API call that returns a `submission_id` immediately.
2.  **Optimistic UI**: The local state updates instantly to "SUBMITTED" without waiting for a re-fetch.
3.  **Smart Polling**: A `useEffect` hook monitors the status and polls the API every 5 seconds only while the status is `SUBMITTED` or `PROCESSING`.
4.  **Auto-Refresh**: Once the status becomes `SOLVED`, polling stops, and the new data (coordinates, overlays) is automatically displayed.

## Pages

1. **Dashboard (`/`)**: High-level overview, statistics, and recently added images.
2. **Search (`/search`)**: The primary discovery interface with multi-criteria filtering.
3. **Image Detail (`/images/:id`)**: Full metadata view, pan/zoom image preview, and catalog matches.
4. **Catalogs (`/catalogs`)**: Browsing interface for Messier and NGC objects.
5. **Stats (`/stats`)**: Visualizations of exposure time and sky coverage.
6. **FITS Analytics (`/stats/fits`)**: In-depth FITS metadata analytics with equipment and sky coverage charts.
7. **Admin (`/admin`)**: System administration dashboard with astrometry stats, indexing controls, and job queue management.
8. **Metadata Search (`/metadata`)**: Advanced metadata search with field-specific filters and operators.
9. **FITS Explorer (`/fits/:id`)**: Detailed FITS header viewer and analyzer.
10. **Settings (`/settings`)**: Indexer controls and system configuration.
11. **Login (`/login`)**: Email/password authentication form.
12. **Setup (`/setup`)**: First-time admin account creation.

