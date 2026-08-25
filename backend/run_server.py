"""Serve the Sentinel API and triage board.

Usage (from sentinel/backend):
    python run_server.py

Then open http://127.0.0.1:8000 for the board, or /docs for the API.
"""

from __future__ import annotations

import uvicorn


def main() -> None:
    uvicorn.run("app.api:app", host="127.0.0.1", port=8000, reload=False)


if __name__ == "__main__":
    main()
