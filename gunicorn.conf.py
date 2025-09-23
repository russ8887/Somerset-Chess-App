"""
Gunicorn configuration for Somerset Chess Scheduler
Optimized for long-running slot finder operations
"""

import os

# Server socket
bind = f"0.0.0.0:{os.environ.get('PORT', '10000')}"
backlog = 2048

# Worker processes
workers = int(os.environ.get('WEB_CONCURRENCY', 2))
worker_class = 'sync'
worker_connections = 1000
timeout = 600  # 10 minutes - matches slot finder API timeout
keepalive = 2

# Restart workers after this many requests, to help prevent memory leaks
max_requests = 1000
max_requests_jitter = 100

# Logging
accesslog = '-'
errorlog = '-'
loglevel = 'info'
access_log_format = '%(h)s %(l)s %(u)s %(t)s "%(r)s" %(s)s %(b)s "%(f)s" "%(a)s" %(D)s'

# Process naming
proc_name = 'somerset_chess_scheduler'

# Server mechanics
preload_app = True
daemon = False
pidfile = None
user = None
group = None
tmp_upload_dir = None

# SSL (if needed in future)
keyfile = None
certfile = None

# Performance tuning
worker_tmp_dir = '/dev/shm'  # Use memory for worker temp files if available

# Graceful timeout for worker shutdown
graceful_timeout = 30

# Environment variables for Django
raw_env = [
    'DJANGO_SETTINGS_MODULE=somerset_project.settings',
]

def when_ready(server):
    """Called just after the server is started."""
    server.log.info("Somerset Chess Scheduler server is ready. Timeout set to 600 seconds for slot finder operations.")

def worker_int(worker):
    """Called just after a worker exited on SIGINT or SIGQUIT."""
    worker.log.info("Worker received INT or QUIT signal")

def pre_fork(server, worker):
    """Called just before a worker is forked."""
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def post_fork(server, worker):
    """Called just after a worker has been forked."""
    server.log.info("Worker spawned (pid: %s)", worker.pid)

def worker_abort(worker):
    """Called when a worker receives the SIGABRT signal."""
    worker.log.info("Worker received SIGABRT signal")
