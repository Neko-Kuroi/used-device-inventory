"""Entry point for trying it out: python run.py (127.0.0.1:5000).

For day-to-day use from other devices over Tailscale, serve it with a
production WSGI server bound to the Tailscale address; see README.md.
"""
from device_app import create_app

app = create_app()

if __name__ == "__main__":
    app.run(host="127.0.0.1", port=5000)
