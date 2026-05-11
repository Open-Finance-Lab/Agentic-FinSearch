#!/usr/bin/env python3
"""
Deployment Verification Script
Checks for memory leaks and resource cleanup in development mode.
"""

import os
import sys
import time
import psutil
import subprocess
from pathlib import Path

# Add backend to path
backend_dir = Path(__file__).resolve().parent
sys.path.insert(0, str(backend_dir))

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'django_config.settings')


def check_memory_baseline():
    """Get baseline memory usage"""
    process = psutil.Process()
    memory_mb = process.memory_info().rss / 1024 / 1024
    print(f"Baseline memory: {memory_mb:.2f} MB")
    return memory_mb


def check_gunicorn_config():
    """
    Verify Gunicorn has worker recycling configured.

    Settings may live in either Procfile/Dockerfile CMD as CLI flags
    (--max-requests, --max-requests-jitter) OR in gunicorn.conf.py as
    Python assignments (max_requests = ..., max_requests_jitter = ...).
    Both are equivalent — accept either.
    """
    procfile_path = backend_dir / 'Procfile'
    config_path = backend_dir / 'gunicorn.conf.py'

    procfile_text = procfile_path.read_text() if procfile_path.exists() else ''
    config_text = config_path.read_text() if config_path.exists() else ''

    has_max_requests = (
        '--max-requests' in procfile_text
        or 'max_requests' in config_text
    )
    has_jitter = (
        '--max-requests-jitter' in procfile_text
        or 'max_requests_jitter' in config_text
    )

    print("\nGunicorn Configuration:")
    print(f"  Worker recycling (max_requests): {'OK' if has_max_requests else 'MISSING'}")
    print(f"  Jitter (max_requests_jitter): {'OK' if has_jitter else 'MISSING'}")

    return has_max_requests and has_jitter


def check_mcp_cleanup():
    """Verify MCP manager has proper cleanup"""
    mcp_manager_path = backend_dir / 'mcp_client' / 'mcp_manager.py'
    with open(mcp_manager_path) as f:
        content = f.read()

    has_exit_stack_close = 'exit_stack.aclose()' in content

    print("\nMCP Manager Cleanup:")
    print(f"  Exit stack cleanup: {'OK' if has_exit_stack_close else 'MISSING'}")

    return has_exit_stack_close


def check_session_cleanup():
    """Verify session cleanup is enabled"""
    context_manager_path = backend_dir / 'datascraper' / 'unified_context_manager.py'
    with open(context_manager_path) as f:
        content = f.read()

    has_cache_backend = 'django.core.cache' in content
    has_ttl = 'session_ttl' in content

    print("\nSession Cleanup:")
    print(f"  Cache-backed sessions: {'OK' if has_cache_backend else 'MISSING'}")
    print(f"  TTL configured: {'OK' if has_ttl else 'MISSING'}")

    return has_cache_backend and has_ttl


def check_monitoring_middleware():
    """Verify memory monitoring middleware is enabled"""
    settings_path = backend_dir / 'django_config' / 'settings.py'
    with open(settings_path) as f:
        content = f.read()

    has_middleware = 'MemoryTrackerMiddleware' in content

    print("\nMonitoring Middleware:")
    print(f"  Memory tracker: {'OK' if has_middleware else 'MISSING'}")

    return has_middleware


def check_psutil_dependency():
    """Verify psutil is installed"""
    try:
        import psutil
        print("\nDependencies:")
        print(f"  psutil: OK (version {psutil.__version__})")
        return True
    except ImportError:
        print("\nDependencies:")
        print("  psutil: MISSING - Run: uv add psutil")
        return False


def run_verification():
    """Run all verification checks"""
    print("=" * 60)
    print("DEPLOYMENT VERIFICATION - Memory Leak Prevention")
    print("=" * 60)

    checks = [
        ("Gunicorn Config", check_gunicorn_config),
        ("MCP Cleanup", check_mcp_cleanup),
        ("Session Cleanup", check_session_cleanup),
        ("Monitoring Middleware", check_monitoring_middleware),
        ("Dependencies", check_psutil_dependency),
    ]

    results = []
    for name, check_func in checks:
        try:
            result = check_func()
            results.append((name, result))
        except Exception as e:
            print(f"\nERROR in {name}: {e}")
            results.append((name, False))

    print("\n" + "=" * 60)
    print("VERIFICATION SUMMARY")
    print("=" * 60)

    all_passed = True
    for name, passed in results:
        status = "PASS" if passed else "FAIL"
        print(f"{name:.<40} {status}")
        if not passed:
            all_passed = False

    print("=" * 60)

    if all_passed:
        print("All checks passed! Ready for deployment.")
        return 0
    else:
        print("Some checks failed. Fix issues before deploying.")
        return 1


if __name__ == "__main__":
    sys.exit(run_verification())
