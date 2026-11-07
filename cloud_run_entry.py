#!/usr/bin/env python3
"""
Cloud Run entrypoint: run the full app so the aiohttp server handles all routes.

Previously this started a minimal HTTP server (GET-only) and set CLOUD_RUN_SERVER_ALREADY=1
so main.py skipped starting its server. That caused POST /api/alerts/market-open (and all
other POST endpoints) to return 501 Not Implemented. We now let main.py start the aiohttp
server so Good Morning, EOD, OAuth webhooks, and other POST endpoints work.
"""
import os
import sys


def main():
    # Do NOT set CLOUD_RUN_SERVER_ALREADY so main.py starts the full aiohttp server
    sys.argv = ["main.py", "--cloud-mode"]
    import main as app_main
    import asyncio
    asyncio.run(app_main.main())


if __name__ == "__main__":
    main()
