import os
import multiprocessing

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

workers = int(os.getenv('GUNICORN_WORKERS', '2'))
worker_class = 'gthread'
threads = int(os.getenv('GUNICORN_THREADS', '4'))
worker_connections = int(os.getenv('GUNICORN_WORKER_CONNECTIONS', '5'))
max_requests = int(os.getenv('GUNICORN_MAX_REQUESTS', '200'))
max_requests_jitter = 50

timeout = int(os.getenv('GUNICORN_TIMEOUT', '120'))
graceful_timeout = 30
keepalive = 5

accesslog = '-'
errorlog = '-'
loglevel = os.getenv('GUNICORN_LOG_LEVEL', 'info')
# %(m)s %(U)s %(H)s instead of %(r)s: the raw request line embeds the query string, so
# any credential passed there (the old /api/debug/memory/?token=... pattern, signed URLs,
# etc.) would be written verbatim to stdout and shipped to log aggregation. Logging
# method + path + protocol keeps the combined-log shape for downstream parsers while
# dropping the query string entirely. Pinned by tests/test_gunicorn_conf.py.
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(m)s %(U)s %(H)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

proc_name = 'fingpt-backend'

limit_request_line = 4096
limit_request_fields = 100
limit_request_field_size = 8190

daemon = False
pidfile = None
umask = 0
user = None
group = None
tmp_upload_dir = None

# gunicorn 25.1+ opens a Unix control socket (default: ./gunicorn.ctl in WORKDIR /app,
# which is root-owned) for the `gunicornc` management CLI we don't use. As the non-root
# runtime user (uid 1001) that path is unwritable, so the arbiter logs a recurring
# "Failed to start control socket: [Errno 13] Permission denied" warning on every start
# and reload. Disable it: removes the noise and the unused socket's small attack surface.
control_socket_disable = True


# ── Memory monitoring hooks ───────────────────────────────────────

def post_request(worker, req, environ, resp):
    """
    Feed RSS measurement into the per-worker LeakDetector after every response.
    This is the primary data source for trend analysis — more reliable than
    middleware because it fires even on middleware errors.
    """
    try:
        import psutil
        rss_mb = psutil.Process().memory_info().rss / 1024 / 1024
        from api.utils.leak_detector import get_worker_detector
        detector = get_worker_detector()
        result = detector.record(rss_mb=rss_mb)
        if result:
            worker.log.warning(
                f"[gunicorn] {result['status']}: "
                f"pid={worker.pid} rss={rss_mb:.1f}MB "
                f"{result}"
            )
    except Exception:
        pass  # Never crash the request pipeline for monitoring

