import requests
import time
import json
import os
import statistics
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any

class APIPerformanceTester:
    def __init__(self, base_url: str, username: str, password: str):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.session = requests.Session()
        self.access_token = None
        self.results = []
        
    def login(self) -> Dict[str, Any]:
        """Test login endpoint and store access token"""
        url = f"{self.base_url}/api/auth/login"
        start_time = time.time()
        try:
            response = self.session.post(
                url,
                json={"email": self.username, "password": self.password}
            )
            elapsed = time.time() - start_time
            result = {
                "endpoint": "/api/auth/login",
                "method": "POST",
                "status_code": response.status_code,
                "response_time_ms": round(elapsed * 1000, 2),
                "success": response.status_code == 200,
                "error": None,
                "response_size_bytes": len(response.content)
            }
            if response.status_code == 200:
                self.access_token = response.json().get('access_token')
            return result
        except Exception as e:
            return {"endpoint": "/api/auth/login", "success": False, "error": str(e), "response_time_ms": 0}

    def test_get_endpoint(self, endpoint: str, requires_auth: bool = True, 
                         params: Optional[Dict] = None, description: str = "") -> Dict[str, Any]:
        url = f"{self.base_url}{endpoint}"
        headers = {"Authorization": f"Bearer {self.access_token}"} if requires_auth and self.access_token else {}
        
        start_time = time.time()
        try:
            response = self.session.get(url, headers=headers, params=params or {}, timeout=10)
            elapsed = time.time() - start_time
            
            return {
                "endpoint": endpoint,
                "description": description,
                "status_code": response.status_code,
                "response_time_ms": round(elapsed * 1000, 2),
                "success": 200 <= response.status_code < 300,
                "response_size_bytes": len(response.content)
            }
        except Exception as e:
            return {"endpoint": endpoint, "success": False, "error": str(e), "response_time_ms": 0}

    def test_multiple_times(self, endpoint: str, iterations: int = 3, **kwargs) -> Dict[str, Any]:
        iter_results = []
        for _ in range(iterations):
            iter_results.append(self.test_get_endpoint(endpoint, **kwargs))
            time.sleep(0.1)
        
        successes = [r for r in iter_results if r.get("success")]
        times = [r["response_time_ms"] for r in successes]
        
        aggregated = {
            "endpoint": endpoint,
            "description": kwargs.get("description", ""),
            "iterations": iterations,
            "success_count": len(successes),
            "success_rate": round((len(successes) / iterations) * 100, 2),
        }
        if times:
            aggregated["avg_ms"] = round(statistics.mean(times), 2)
            aggregated["max_ms"] = max(times)
        else:
            aggregated["avg_ms"] = "N/A"
            aggregated["error"] = iter_results[0].get("error", "Unknown Error")
        return aggregated

    def generate_markdown(self) -> str:
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        md = [
            f"# API Performance Report",
            f"- **Run Date:** {now}",
            f"- **Base URL:** `{self.base_url}`",
            "\n## Results Summary",
            "| Endpoint | Description | Iterations | Success Rate | Avg Time |",
            "| :--- | :--- | :--- | :--- | :--- |"
        ]
        for r in self.results:
            avg = f"{r.get('avg_ms', r.get('response_time_ms'))}ms"
            rate = f"{r.get('success_rate', 100)}%"
            md.append(f"| `{r['endpoint']}` | {r.get('description', 'N/A')} | {r.get('iterations', 1)} | {rate} | {avg} |")
        
        return "\n".join(md)

    def run_all_tests(self):
        login_res = self.login()
        self.results.append(login_res)
        if not login_res["success"]: 
            print("❌ Login failed, skipping authenticated tests.")
            return

        # Try to get a sample image ID for detail tests
        image_id = None
        print("Fetching sample image for detail tests...")
        img_resp = self.session.get(f"{self.base_url}/api/images/", 
                                   headers={"Authorization": f"Bearer {self.access_token}"},
                                   params={"page_size": 1})
        if img_resp.status_code == 200:
            data = img_resp.json()
            if data.get("items") and len(data["items"]) > 0:
                image_id = data["items"][0].get("id")
                print(f"✅ Found sample image ID: {image_id}")

        endpoints = [
            # Root & Health
            ("/", False, None, "Root (API Info)"),
            ("/api/health", False, None, "General Health"),
            ("/api/health/db", False, None, "Database Health"),
            ("/api/health/redis", False, None, "Redis Health"),
            
            # Auth & Users
            ("/api/auth/me", True, None, "Current User Profile"),
            ("/api/auth/setup-status", False, None, "Initial Setup Status"),
            ("/api/users/", True, None, "List Users (Admin)"),
            
            # Images
            ("/api/images/", True, {"page": 1, "page_size": 10}, "List Images (Paginated)"),
            ("/api/images/export_csv", True, {
                "rating": 5,
                "max_exposure_exclusive": "false",
                "pixel_scale_max_exclusive": "false",
                "sort_by": "capture_date",
                "sort_order": "desc"
            }, "Export Images CSV"),
            
            # Catalogs
            ("/api/catalogs/messier", True, {"page": 1, "page_size": 20}, "List Messier Objects"),
            ("/api/catalogs/messier/M31", True, None, "Messier M31 Detail"),
            ("/api/catalogs/ngc", True, {"page": 1, "page_size": 20}, "List NGC Objects"),
            ("/api/catalogs/named_stars", True, {"page": 1, "page_size": 20}, "List Named Stars"),
            
            # Search
            ("/api/search/coordinates", True, {"ra": 10.68, "dec": 41.26, "radius": 1.0}, "Search by Coordinates (M31)"),
            ("/api/catalogs/messier", True, {
                "page": 1,
                "page_size": 110,
                "q": "M42",
                "has_images": "false",
                "sort_by": "default",
                "sort_order": "asc"
            }, "Search Messier Catalog (M42)"),
            
            # Statistics
            ("/api/stats/overview", True, None, "Stats: Overview"),
            ("/api/stats/by-month", True, None, "Stats: By Month"),
            ("/api/stats/by-subtype", True, None, "Stats: By Subtype"),
            ("/api/stats/by-format", True, None, "Stats: By Format"),
            ("/api/stats/top-objects", True, None, "Stats: Top Objects"),
            ("/api/stats/fits/", True, None, "Stats: FITS Header Stats"),
            
            # Indexer
            ("/api/indexer/status", True, None, "Indexer: Status"),
            ("/api/indexer/thumbnails/stats", True, None, "Indexer: Thumbnail Stats"),
            
            # Admin
            ("/api/admin/stats", True, None, "Admin: System Stats"),
            ("/api/admin/workers", True, None, "Admin: Worker Status"),
            ("/api/admin/queue", True, None, "Admin: Queue Status"),
            
            # Settings & Filesystem
            ("/api/settings/", True, None, "System Settings"),
            ("/api/filesystem/list", True, None, "Filesystem: List"),
        ]

        # Add image detail tests if we found an ID
        if image_id:
            endpoints.extend([
                (f"/api/images/{image_id}", True, None, "Image: Detail"),
                (f"/api/images/{image_id}/thumbnail", True, None, "Image: Thumbnail"),
                (f"/api/images/954/annotated", True, None, "Image: Annotated (954)"),
                (f"/api/images/{image_id}/fits", True, None, "Image: FITS Header (JSON)"),
                (f"/api/images/{image_id}/download", True, None, "Image: Download (Binary)"),
            ])
        
        for endpoint, auth, params, desc in endpoints:
            print(f"Testing {endpoint}...")
            res = self.test_multiple_times(endpoint, iterations=3, requires_auth=auth, params=params, description=desc)
            self.results.append(res)

def main():
    # Path logic: scripts/ -> project_root / docs/
    script_dir = Path(__file__).resolve().parent
    docs_dir = script_dir.parent / "docs"
    docs_dir.mkdir(exist_ok=True)

    # Read credentials from environment variables
    base_url = os.getenv("DEMO_URL")
    username = os.getenv("DEMO_USER")
    password = os.getenv("DEMO_PASSWORD")
    
    tester = APIPerformanceTester(base_url, username, password)
    tester.run_all_tests()

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"perf_{timestamp}.md"
    target_path = docs_dir / filename

    with open(target_path, "w", encoding="utf-8") as f:
        f.write(tester.generate_markdown())

    print(f"\n✅ Report generated at: {target_path}")

    # Add this at the end of your main() function
    latest_path = docs_dir / "perf_latest.md"
    with open(latest_path, "w", encoding="utf-8") as f:
        f.write(tester.generate_markdown())    

if __name__ == "__main__":
    main()