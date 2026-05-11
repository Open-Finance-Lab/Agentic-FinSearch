import os
import multiprocessing

bind = f"0.0.0.0:{os.getenv('PORT', '8000')}"

workers = int(os.getenv('GUNICORN_WORKERS', '2'))
worker_class = 'gthread'
worker_connections = int(os.getenv('GUNICORN_WORKER_CONNECTIONS', '5'))
max_requests = int(os.getenv('GUNICORN_MAX_REQUESTS', '200'))
max_requests_jitter = 50

timeout = int(os.getenv('GUNICORN_TIMEOUT', '600'))
graceful_timeout = 30
keepalive = 5

accesslog = '-'
errorlog = '-'
loglevel = os.getenv('GUNICORN_LOG_LEVEL', 'info')
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

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

