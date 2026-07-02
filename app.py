"""ADB Hub — Flask application entry point."""

import logging

from flask import Flask, render_template
from flask_sock import Sock

from config import HOST, PORT, DEBUG
from routes.api import api_bp
from routes.ws import register_ws_routes

# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = Flask(__name__)
app.register_blueprint(api_bp)

# WebSocket
sock = Sock(app)
register_ws_routes(sock)

# Logging
logging.basicConfig(
    level=logging.DEBUG if DEBUG else logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

# ---------------------------------------------------------------------------
# Page route — status dashboard only
# ---------------------------------------------------------------------------


@app.route("/")
def index():
    """The only page: device status dashboard."""
    return render_template("index.html")


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    print(f"ADB Hub starting on http://{HOST}:{PORT}")
    app.run(host=HOST, port=PORT, debug=DEBUG)
