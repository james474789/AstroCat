import re
import statistics
import subprocess
import sys
from collections import defaultdict

def get_logs_from_docker(container_name="AstroCat-celery"):
    """Fetch logs directly from the docker container."""
    try:
        result = subprocess.run(
            ["docker", "logs", container_name],
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='ignore'
        )
        if result.returncode != 0:
            print(f"Error fetching logs from {container_name}: {result.stderr}")
            return None
        return result.stdout
    except Exception as e:
        print(f"Failed to run docker logs: {e}")
        return None

def analyze_log_content(content):
    """Parse log content and extract performance metrics."""
    concurrency_pattern = re.compile(r"concurrency:\s+(\d+)\s+\(prefork\)")
    success_pattern = re.compile(r"succeeded in\s+([0-9.]+)\s*s")
    
    current_concurrency = "Unknown"
    stats = defaultdict(lambda: defaultdict(list))
    
    lines = content.splitlines()
    for line in lines:
        m_conc = concurrency_pattern.search(line)
        if m_conc:
            current_concurrency = m_conc.group(1)
            continue
            
        m_succ = success_pattern.search(line)
        if m_succ:
            # Try to identify task name, fallback to 'Unknown'
            task_match = re.search(r"Task ([\w\.]+)", line)
            task_name = task_match.group(1).split('.')[-1] if task_match else "generic_task"
            duration = float(m_succ.group(1))
            stats[current_concurrency][task_name].append(duration)
    return stats

def print_report(stats):
    """Print a formatted performance report."""
    if not stats:
        print("No performance data found in logs.")
        return

    print("\n" + "="*80)
    print(f"{'CELERY PERFORMANCE SUMMARY':^80}")
    print("="*80)
    
    # Sort by concurrency (numeric)
    sorted_concurrencies = sorted(stats.keys(), key=lambda x: int(x) if x.isdigit() else 999)
    
    for conc in sorted_concurrencies:
        print(f"\n[ Concurrency: {conc} ]")
        print(f"{'Task Name':<20} | {'Count':<6} | {'Avg (s)':<10} | {'Med (s)':<10} | {'Min/Max (s)':<18} | {'Tasks/min':<10}")
        print("-" * 88)
        
        for task, times in stats[conc].items():
            count = len(times)
            avg = statistics.mean(times)
            med = statistics.median(times)
            t_min = min(times)
            t_max = max(times)
            # Throughput: (count / total_estimated_time) is tricky, 
            # so we'll do (60 / avg * concurrency) as a theoretical max capacity
            throughput = (60.0 / avg * int(conc)) if avg > 0 and conc.isdigit() else 0
            
            print(f"{task:<20} | {count:<6} | {avg:<10.2f} | {med:<10.2f} | {t_min:>6.1f}/{t_max:<6.1f} | {throughput:<10.1f}")
    print("="*80 + "\n")

def analyze_log_file(file_path):
    """Read file with multiple encoding attempts."""
    raw = open(file_path, 'rb').read()
    # Try common encodings
    for enc in ['utf-16', 'utf-8', 'cp1252']:
        try:
            return raw.decode(enc)
        except UnicodeError:
            continue
    return raw.decode('utf-8', errors='ignore')

if __name__ == "__main__":
    content = None
    
    if len(sys.argv) > 1:
        file_path = sys.argv[1]
        print(f"Analyzing from file: {file_path}")
        content = analyze_log_file(file_path)
        if content:
            print(f"Read {len(content)} characters. First 100: {repr(content[:100])}")
    else:
        print("No file provided. Attempting to fetch logs from 'AstroCat-celery' container...")
        # Capture both stdout and stderr for Celery
        try:
            # We use shell=True on Windows to handle redirection if needed, 
            # or just call docker directly and capture stdout/err.
            # Using --tail to get a good sample without being overwhelmed.
            proc = subprocess.Popen(
                ["docker", "logs", "--tail", "20000", "AstroCat-celery"],
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                errors='ignore'
            )
            content, _ = proc.communicate(timeout=30)
        except Exception as e:
            print(f"Error fetching docker logs: {e}")
        
    if content:
        performance_stats = analyze_log_content(content)
        print_report(performance_stats)
    else:
        print("Could not retrieve log content.")
