#!/usr/bin/env python3
"""
Concurrent Performance Test Script for AstroCat API

Usage: python api_concurrent_test.py <max_concurrent_users>

Gradually increases concurrent users and tests API endpoints to find 
performance thresholds. Stops early if aggregate success rate or latency 
becomes unacceptable.
"""

import argparse
import os
import random
import statistics
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Any, Optional

import requests


# Configuration
BASE_URL = os.getenv("DEMO_URL")
USERNAME = os.getenv("DEMO_USER")
PASSWORD = os.getenv("DEMO_PASSWORD")

# Thresholds (aggregated across all requests per level)
SUCCESS_RATE_THRESHOLD = 95.0  # Stop if aggregate success rate < 95%
AVG_LATENCY_THRESHOLD_MS = 5000  # Stop if aggregate avg latency > 5s

# Concurrency ramp: levels to test (will also always include the max_users specified)
CONCURRENCY_LEVELS = [1, 5, 10, 20, 30, 40, 50, 75, 100, 150, 200, 250, 300, 400, 500]

# Duration per level (seconds)
TEST_DURATION_PER_LEVEL = 15

# Endpoints to test (endpoint, requires_auth, params, description)
ENDPOINTS = [
    ("/", False, None, "Root (API Info)"),
    ("/api/health", False, None, "General Health"),
    ("/api/health/db", False, None, "Database Health"),
    ("/api/health/redis", False, None, "Redis Health"),
    ("/api/auth/me", True, None, "Current User Profile"),
    ("/api/auth/setup-status", False, None, "Initial Setup Status"),
    ("/api/users/", True, None, "List Users (Admin)"),
    ("/api/images/", True, {"page": 1, "page_size": 10}, "List Images"),
    ("/api/catalogs/messier", True, {"page": 1, "page_size": 20}, "Messier Catalog"),
    ("/api/catalogs/ngc", True, {"page": 1, "page_size": 20}, "NGC Catalog"),
    ("/api/stats/overview", True, None, "Stats Overview"),
    ("/api/stats/by-month", True, None, "Stats By Month"),
    ("/api/stats/fits/", True, None, "FITS Stats"),
    ("/api/indexer/status", True, None, "Indexer Status"),
    ("/api/admin/stats", True, None, "Admin Stats"),
    ("/api/settings/", True, None, "System Settings"),
    ("/api/filesystem/list", True, None, "Filesystem List"),
]


class ConcurrentTester:
    def __init__(self, base_url: str, username: str, password: str, max_users: int):
        self.base_url = base_url.rstrip('/')
        self.username = username
        self.password = password
        self.max_users = max_users
        self.session = requests.Session()  # Thread-safe session with cookies
        self.results_by_level: Dict[int, Dict[str, Any]] = {}
        self.breaking_point: Optional[int] = None
        self.break_reason: Optional[str] = None

    def login(self, max_retries: int = 3) -> bool:
        """Authenticate and store session cookies with retry logic for rate limits."""
        for attempt in range(max_retries):
            try:
                resp = self.session.post(
                    f"{self.base_url}/api/auth/login",
                    json={"email": self.username, "password": self.password},
                    timeout=10
                )
                if resp.status_code == 200:
                    print(f"✅ Logged in as {self.username}")
                    return True
                elif resp.status_code == 429:
                    wait_time = (attempt + 1) * 30  # Wait 30, 60, 90 seconds
                    print(f"⚠️  Login rate limited (429). Waiting {wait_time}s before retry {attempt + 1}/{max_retries}...")
                    time.sleep(wait_time)
                else:
                    print(f"❌ Login failed: {resp.status_code}")
                    return False
            except Exception as e:
                print(f"❌ Login error: {e}")
                return False
        
        print("\n❌ Login consistently rate limited. Please try again in 15 minutes.")
        print("💡 TIP: You can temporarily relax rate limits in `backend/app/api/auth.py` by changing `@limiter.limit('5/15minutes')` on the login endpoint.")
        return False

    def make_request(self, endpoint: str, requires_auth: bool, params: Optional[Dict]) -> Dict[str, Any]:
        """Execute a single GET request and return timing/status info."""
        url = f"{self.base_url}{endpoint}"
        
        start = time.perf_counter()
        try:
            # Use session for all requests (session handles cookies automatically)
            resp = self.session.get(url, params=params or {}, timeout=10)
            elapsed_ms = (time.perf_counter() - start) * 1000
            return {
                "success": 200 <= resp.status_code < 300,
                "status_code": resp.status_code,
                "latency_ms": elapsed_ms,
                "endpoint": endpoint
            }
        except Exception as e:
            elapsed_ms = (time.perf_counter() - start) * 1000
            return {
                "success": False,
                "status_code": 0,
                "latency_ms": elapsed_ms,
                "endpoint": endpoint,
                "error": str(e)
            }


    def run_concurrent_test(self, concurrency: int) -> Dict[str, Any]:
        """Run requests at a given concurrency level for the test duration."""
        print(f"\n🔄 Testing with {concurrency} concurrent users...")
        
        results: List[Dict[str, Any]] = []
        start_time = time.time()
        
        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            while time.time() - start_time < TEST_DURATION_PER_LEVEL:
                # Submit a batch of random requests
                futures = []
                for _ in range(concurrency):
                    endpoint, auth, params, _ = random.choice(ENDPOINTS)
                    futures.append(executor.submit(self.make_request, endpoint, auth, params))
                
                # Collect results
                for future in as_completed(futures):
                    results.append(future.result())
                
                # Small pause to avoid overwhelming
                time.sleep(0.05)
        
        # Calculate aggregated metrics
        total = len(results)
        successes = sum(1 for r in results if r["success"])
        latencies = [r["latency_ms"] for r in results]
        
        success_rate = (successes / total * 100) if total > 0 else 0
        avg_latency = statistics.mean(latencies) if latencies else 0
        p95_latency = sorted(latencies)[int(len(latencies) * 0.95)] if len(latencies) > 1 else avg_latency
        max_latency = max(latencies) if latencies else 0
        
        level_result = {
            "concurrency": concurrency,
            "total_requests": total,
            "successes": successes,
            "success_rate": round(success_rate, 2),
            "avg_latency_ms": round(avg_latency, 2),
            "p95_latency_ms": round(p95_latency, 2),
            "max_latency_ms": round(max_latency, 2),
        }
        
        print(f"   ✓ {total} requests | {success_rate:.1f}% success | Avg: {avg_latency:.0f}ms | P95: {p95_latency:.0f}ms")
        
        return level_result

    def run_all_levels(self):
        """Run tests at increasing concurrency levels until max or failure."""
        if not self.login():
            print("❌ Cannot proceed without authentication.")
            return
        
        print(f"\n🚀 Starting concurrent performance test (max: {self.max_users} users)")
        print(f"   Duration per level: {TEST_DURATION_PER_LEVEL}s")
        print(f"   Thresholds: Success ≥ {SUCCESS_RATE_THRESHOLD}%, Avg Latency ≤ {AVG_LATENCY_THRESHOLD_MS}ms")
        
        # Filter levels based on max_users
        levels_to_test = [l for l in CONCURRENCY_LEVELS if l <= self.max_users]
        if self.max_users not in levels_to_test:
            levels_to_test.append(self.max_users)
        levels_to_test = sorted(set(levels_to_test))
        
        for level in levels_to_test:
            result = self.run_concurrent_test(level)
            self.results_by_level[level] = result
            
            # Check thresholds (aggregated across all requests at this level)
            if result["success_rate"] < SUCCESS_RATE_THRESHOLD:
                self.breaking_point = level
                self.break_reason = f"Success rate dropped to {result['success_rate']}%"
                print(f"\n⚠️  Breaking point reached: {self.break_reason}")
                break
            
            if result["avg_latency_ms"] > AVG_LATENCY_THRESHOLD_MS:
                self.breaking_point = level
                self.break_reason = f"Avg latency exceeded {AVG_LATENCY_THRESHOLD_MS}ms ({result['avg_latency_ms']:.0f}ms)"
                print(f"\n⚠️  Breaking point reached: {self.break_reason}")
                break
        
        if not self.breaking_point:
            print(f"\n✅ All levels passed up to {self.max_users} concurrent users!")

    def generate_report(self) -> str:
        """Generate a Markdown report."""
        now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        max_tested = max(self.results_by_level.keys()) if self.results_by_level else 0
        
        # Header
        lines = [
            "# Concurrent Performance Test Report",
            "",
            f"**Run Date:** {now}",
            f"**Base URL:** `{self.base_url}`",
            f"**Max Concurrent Users Requested:** {self.max_users}",
            f"**Max Concurrent Users Tested:** {max_tested}",
            f"**Test Duration Per Level:** {TEST_DURATION_PER_LEVEL}s",
            "",
        ]
        
        # Breaking point
        if self.breaking_point:
            lines.extend([
                "> [!WARNING]",
                f"> **Breaking Point:** {self.breaking_point} concurrent users",
                f"> **Reason:** {self.break_reason}",
                "",
            ])
        else:
            lines.extend([
                "> [!TIP]",
                f"> All tested concurrency levels passed. Consider testing with higher values.",
                "",
            ])
        
        # Summary table
        lines.extend([
            "## Performance by Concurrency Level",
            "",
            "| Concurrent Users | Requests | Success Rate | Avg Latency | P95 Latency | Max Latency |",
            "| :---: | :---: | :---: | :---: | :---: | :---: |",
        ])
        
        for level in sorted(self.results_by_level.keys()):
            r = self.results_by_level[level]
            status = "⚠️" if level == self.breaking_point else "✅"
            lines.append(
                f"| {status} {r['concurrency']} | {r['total_requests']} | "
                f"{r['success_rate']}% | {r['avg_latency_ms']}ms | "
                f"{r['p95_latency_ms']}ms | {r['max_latency_ms']}ms |"
            )
        
        # Findings
        lines.extend([
            "",
            "## Findings",
            "",
        ])
        
        if self.breaking_point:
            prev_level = None
            for level in sorted(self.results_by_level.keys()):
                if level == self.breaking_point:
                    break
                prev_level = level
            
            if prev_level:
                lines.append(f"- **Acceptable performance** up to **{prev_level} concurrent users**")
            lines.append(f"- **Performance degradation** detected at **{self.breaking_point} concurrent users**")
            lines.append(f"- **Cause:** {self.break_reason}")
        else:
            lines.append(f"- System handled all tested levels up to **{max_tested} concurrent users**")
            lines.append(f"- No performance degradation detected within tested range")
        
        # Recommendations
        lines.extend([
            "",
            "## Recommendations",
            "",
        ])
        
        if self.breaking_point:
            lines.extend([
                f"1. Investigate bottlenecks when handling {self.breaking_point}+ concurrent users",
                "2. Consider adding caching for frequently accessed endpoints",
                "3. Review database query performance and connection pooling",
                "4. Monitor server resource usage (CPU, memory, I/O) during peak load",
            ])
        else:
            lines.extend([
                f"1. System appears stable at {max_tested} concurrent users",
                f"2. Consider running with higher `--max-users` to find limits",
                "3. Monitor production metrics to validate real-world performance",
            ])
        
        # Tested endpoints
        lines.extend([
            "",
            "## Tested Endpoints",
            "",
        ])
        for ep, auth, _, desc in ENDPOINTS:
            auth_badge = "🔒" if auth else "🌐"
            lines.append(f"- {auth_badge} `{ep}` - {desc}")
        
        return "\n".join(lines)

    def save_report(self) -> Path:
        """Save report to docs/reports/performance/."""
        script_dir = Path(__file__).resolve().parent
        reports_dir = script_dir.parent / "docs" / "reports" / "performance"
        reports_dir.mkdir(parents=True, exist_ok=True)
        
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = f"PerfCon_{timestamp}.md"
        filepath = reports_dir / filename
        
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(self.generate_report())
        
        return filepath


def main():
    parser = argparse.ArgumentParser(
        description="Concurrent performance test for AstroCat API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python api_concurrent_test.py 20      # Test up to 20 concurrent users
  python api_concurrent_test.py 100     # Test up to 100 concurrent users
        """
    )
    parser.add_argument(
        "max_users",
        type=int,
        help="Maximum number of concurrent users to simulate"
    )
    
    args = parser.parse_args()
    
    if args.max_users < 1:
        print("❌ max_users must be at least 1")
        return
    
    tester = ConcurrentTester(BASE_URL, USERNAME, PASSWORD, args.max_users)
    tester.run_all_levels()
    
    filepath = tester.save_report()
    print(f"\n📊 Report saved: {filepath}")


if __name__ == "__main__":
    main()
