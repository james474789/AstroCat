import time
import httpx
import statistics
import json
import os
from typing import List, Dict, Optional

# Try to get backend URL from env, else use standard docker name or localhost
BASE_URL = os.getenv("BACKEND_URL", "http://localhost:8089")
EMAIL = os.getenv("PERF_TEST_EMAIL", "perf_test@astrocat.com")
PASSWORD = os.getenv("PERF_TEST_PASSWORD", "perf_test_123")

class UserJourneyTester:
    def __init__(self):
        self.client = httpx.Client(base_url=BASE_URL, timeout=60.0)
        self.token = None
        self.sample_image_id = None

    def login(self):
        print(f"Logging in as {EMAIL}...")
        try:
            response = self.client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})
            response.raise_for_status()
            print("Login successful.")
        except Exception as e:
            print(f"Login failed: {e}")
            exit(1)

    def measure(self, method: str, path: str, name: str, iterations: int = 5, payload: dict = None, page: str = "General") -> Dict:
        print(f"[{page}] Testing {name}...")
        times = []
        errors = 0
        notes = "OK"

        for i in range(iterations):
            start = time.perf_counter()
            try:
                if method == "GET":
                    response = self.client.get(path)
                elif method == "POST":
                    response = self.client.post(path, json=payload)
                
                if response.status_code >= 400:
                    errors += 1
                    notes = f"Result: {response.status_code}"
                else:
                    duration = (time.perf_counter() - start) * 1000
                    times.append(duration)
            except httpx.ReadTimeout:
                errors += 1
                notes = "Timeout (>60s)"
            except Exception as e:
                errors += 1
                notes = str(e)

        if not times:
            return {
                "page": page,
                "api_call": f"{method} {path}",
                "status": "❌ FAILED" if errors > 0 else "SKIPPED",
                "mean_ms": "-",
                "notes": notes
            }

        mean_time = statistics.mean(times)
        status = "✅ OK"
        if mean_time > 1000:
            status = "⚠️ SLOW"
        if errors > 0:
            status = f"⚠️ ERRORS ({errors}/{iterations})"

        return {
            "page": page,
            "api_call": f"{method} {path}",
            "status": status,
            "mean_ms": f"{mean_time:.2f}",
            "notes": notes if errors > 0 else ""
        }

    def fetch_sample_image_id(self):
        try:
            response = self.client.get("/api/images?limit=1")
            data = response.json()
            if data['items']:
                self.sample_image_id = data['items'][0]['id']
                print(f"Found sample image ID: {self.sample_image_id}")
            else:
                print("No images found in database. Skipping details tests.")
        except Exception as e:
            print(f"Failed to fetch sample image: {e}")

    def run(self):
        self.login()
        self.fetch_sample_image_id()
        
        results = []

        # 1. Dashboard
        page = "Dashboard"
        results.append(self.measure("GET", "/api/stats/overview", "Overview Stats", page=page, iterations=1)) # Single run for slow ones
        results.append(self.measure("GET", "/api/stats/by-month", "Monthly Stats", page=page))
        results.append(self.measure("GET", "/api/stats/by-subtype", "Subtype Stats", page=page))
        results.append(self.measure("GET", "/api/stats/by-format", "Format Stats", page=page))
        results.append(self.measure("GET", "/api/stats/top-objects", "Top Objects", page=page))
        results.append(self.measure("GET", "/api/images?limit=5", "Recent Activity Widget", page=page))

        # 2. Search
        page = "Search"
        results.append(self.measure("GET", "/api/images?limit=20", "Load Default Grid", page=page))
        results.append(self.measure("GET", "/api/images?is_plate_solved=solved&limit=20", "Filter: Solved", page=page))
        results.append(self.measure("GET", "/api/images?search=M31&limit=20", "Text Search 'M31'", page=page))
        # 11. Coordinate Search (PostGIS)
        ra, dec = 10.68, 41.27  # M31
        results.append(self.measure("GET", f"/api/search/coordinates?ra={ra}&dec={dec}&radius=2.0", "Coordinate Search (M31)", page=page))

        # 3. Metadata (Image Details)
        page = "Details"
        if self.sample_image_id:
            results.append(self.measure("GET", f"/api/images/{self.sample_image_id}", "Image Details", page=page))
            results.append(self.measure("GET", f"/api/images/{self.sample_image_id}/thumbnail", "Thumbnail Fetch", page=page))
        
        # 4. Catalogs
        page = "Catalogs"
        results.append(self.measure("GET", "/api/catalogs/messier", "Messier Catalog", page=page, iterations=3))
        results.append(self.measure("GET", "/api/catalogs/ngc?limit=50", "NGC Catalog (Partial)", page=page, iterations=3))

        # 5. FITS Analytics
        page = "Analytics"
        results.append(self.measure("GET", "/api/stats/fits/", "FITS Stats", page=page))

        # 6. Admin
        page = "Admin"
        results.append(self.measure("GET", "/api/admin/stats", "System Stats (Quick)", page=page))
        results.append(self.measure("GET", "/api/admin/workers", "Worker Stats (Slow)", page=page))
        results.append(self.measure("GET", "/api/admin/queue", "Queue Details", page=page))

        # Report Generation
        self.generate_report(results)

    def generate_report(self, results):
        print("\n\n")
        header = f"{'Page':<12} | {'API Call':<45} | {'Status':<15} | {'Mean (ms)':<10} | {'Notes'}"
        divider = "-" * len(header)
        
        report_lines = [header, divider]
        for r in results:
            line = f"{r['page']:<12} | {r['api_call']:<45} | {r['status']:<15} | {r['mean_ms']:<10} | {r['notes']}"
            report_lines.append(line)

        report_content = "\n".join(report_lines)
        print(report_content)
        
        with open("performance_report_full.txt", "w", encoding="utf-8") as f:
            f.write(report_content)

if __name__ == "__main__":
    tester = UserJourneyTester()
    tester.run()
