import multiprocessing

# Gunicorn configuration
bind = "0.0.0.0:8080"
workers = multiprocessing.cpu_count() * 2 + 1
worker_class = "sync"  # Use sync workers since we're using SSE
worker_connections = 1000
timeout = 120
keepalive = 2

# Logging
accesslog = "logs/access.log"
errorlog = "logs/error.log"
loglevel = "info"

# SSL (uncomment and modify if using HTTPS)
# keyfile = "path/to/keyfile"
# certfile = "path/to/certfile"

# Process naming
proc_name = "ecommerce_crawler"

# Server mechanics
daemon = False
pidfile = "logs/gunicorn.pid"
user = None
group = None
umask = 0
tmp_upload_dir = None

# Server hooks
def on_starting(server):
    """Called just before the master process is initialized."""
    pass

def on_reload(server):
    """Called before code is reloaded."""
    pass

def when_ready(server):
    """Called just after the server is started."""
    pass
