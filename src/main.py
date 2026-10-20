"""Application entry point: python src/main.py

Serves the API on PORT (default 8000). Binds to all interfaces on Replit, where REPL_ID is set,
and to localhost elsewhere unless HOST says otherwise.
"""
import os

import uvicorn


def main():
    host = os.getenv("HOST") or ("0.0.0.0" if os.getenv("REPL_ID") else "127.0.0.1")
    port = int(os.getenv("PORT", "8000"))
    uvicorn.run("aivancin.app:app", host=host, port=port)


if __name__ == "__main__":
    main()
