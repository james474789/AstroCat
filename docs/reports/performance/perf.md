# Performance Test Results

## 📊 API Performance Report by Page

| Page | API Call | Status | Mean Response Time | Notes |
| :--- | :--- | :--- | :--- | :--- |
| **Dashboard** | `GET /api/stats/overview` | ⚠️ **SLOW** | **4,194 ms** | Critical bottleneck. Contains multiple heavy sequential queries. |
| **Dashboard** | `GET /api/stats/by-format` | ⚠️ **SLOW** | **3,685 ms** | Calculates distribution on the fly without caching. |
| **Dashboard** | `GET /api/stats/by-month` | ⚠️ **SLOW** | **1,767 ms** | Aggregates time-series data from full table scan. |
| **Dashboard** | `GET /api/stats/by-subtype` | ✅ OK | 945 ms | Slower than ideal but acceptable. |
| **Dashboard** | `GET /api/stats/top-objects` | ✅ OK | 378 ms | |
| **Dashboard** | `GET /api/images?limit=5` | ✅ OK | 2 ms | Recent activity widget is instant. |
| **Search** | `GET /api/images` | ✅ OK | ~1.5 ms | Listing images is extremely fast (filtered or unfiltered). |
| **Search** | `GET /api/search/coordinates` | ✅ OK | ~95 ms | Spatial search is performant (using PostGIS index). |
| **Details** | `GET /api/images/{id}` | ✅ OK | ~15 ms | Fetching single image metadata is instant. |
| **Catalogs** | `GET /api/catalogs/messier` | ⚠️ **ERRORS** | 548 ms | **Timed out** on 1/3 attempts. Likely loading too much data at once. |
| **Catalogs** | `GET /api/catalogs/ngc` | ✅ OK | 608 ms | Partial load (limit=50) is fine. |
| **Analytics** | `GET /api/stats/fits` | ✅ OK | 423 ms | Acceptable for an analytics page. |
| **Admin** | `GET /api/admin/queue` | ⚠️ **SLOW** | **4,046 ms** | Inspecting Celery/Redis is naturally slow. |
| **Admin** | `GET /api/admin/workers` | ✅ OK | 5 ms | |

## 🛑 Critical Findings

1.  **Dashboard Unusable**: The dashboard makes 3-4 parallel requests that each take 2-4 seconds. This effectively freezes the dashboard for **10+ seconds** on load.
    *   *Root Cause*: The `/stats/*` endpoints are calculating aggregates (counts, distincts) on the entire database *every time* without caching.
2.  **Database Strain**: The sequential nature of these heavy queries in `/stats/overview` locks database resources, potentially affecting other users.
3.  **Admin Queue**: Monitoring the queue is slow, which is expected for Redis inspection but should be loaded asynchronously.

## 💡 Recommendations

1.  **Cache Dashboard Stats**: Implement Redis caching for all `/api/stats/*` endpoints (create a `@cache` decorator or manual set/get). 5-minute TTL would reduce load by 99%.
2.  **Optimize Queries**: Refactor `/stats/overview` to use a single query where possible or `asyncio.gather` for parallelism.
3.  **Client-Side Optimization**: Ensure the frontend loads these widgets independently so one slow widget doesn't block the UI.
