"""Entry point for the web dashboard server."""

import os

import uvicorn


def main():
    is_production = os.getenv("PRODUCTION", "false").lower() in ("true", "1", "yes")
    uvicorn.run(
        "dd_log_analyzer.webapp.server:app",
        host="0.0.0.0",
        port=8000,
        reload=not is_production,
    )


if __name__ == "__main__":
    main()
