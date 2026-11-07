# Dockerfile for Google Cloud Run
FROM python:3.11-slim

# Build timestamp to force fresh builds (no cache)
ARG BUILD_TIMESTAMP=unknown
ENV BUILD_TIMESTAMP=${BUILD_TIMESTAMP}

# Set working directory
WORKDIR /app

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Set working directory to the ORB strategy folder
WORKDIR /app

# Copy requirements first for better caching
COPY ["requirements.txt", "./requirements.txt"]

# Set Python to NEVER create bytecode files
ENV PYTHONDONTWRITEBYTECODE=1

# Install Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy BUILD_ID first to bust Docker cache when code changes
COPY ["BUILD_ID.txt", "./BUILD_ID.txt"]
RUN echo "📦 Build ID: $(cat BUILD_ID.txt) - Forces fresh layer"

# App version stamp (startup reads VERSION.txt; missing → stale-cache warning in cloud logs)
COPY ["VERSION.txt", "./VERSION.txt"]

# Copy ORB Strategy essential files (exclude historical data for smaller image)
COPY ["main.py", "./main.py"]
COPY ["cloud_run_entry.py", "./cloud_run_entry.py"]
COPY ["observability.py", "./observability.py"]
COPY ["modules", "./modules"]

# Create data directories first (Rev 00253: Ensure directories exist before copying)
RUN mkdir -p ./data/watchlist ./data/score

# Copy data directory structure (Rev 00232: Fixed to handle optional subdirectories)
# Rev 00253: Copy entire watchlist directory to ensure all files are included
# Using directory copy instead of individual files to avoid build context issues
COPY ["data/watchlist", "./data/watchlist"]
# Rev 00261: Score directory is excluded by .dockerignore (line 69) - directory already created by RUN mkdir above
# If score data is needed, remove data/score/ from .dockerignore
COPY ["data/holidays_custom.json", "./data/"]
COPY ["data/holidays_future_proof.json", "./data/"]
COPY ["configs", "./configs"]

# Exclude historical_intraday_data for production image size optimization
# This data is only needed for local backtesting, not cloud deployment

# Full Easy ORB 0DTE tree: modules, configs, package marker, build metadata (required for 0DTE + Trendline live quote fallback)
COPY ["easy0DTE", "./easy0DTE"]

# Central tech hub: chain ring and option L1. The image copies folders one by one.
COPY ["central_tech", "./central_tech"]

# Dynamic Volume: rolling volume and open-interest keys. Not built yet.
COPY ["dynamic_volume", "./dynamic_volume"]

# Easy Trendline 0DTE (main.py imports easyTrendline.*; ORB modules supply stealth engine)
COPY ["easyTrendline", "./easyTrendline"]

# Easy Breakout 0DTE (main.py imports easyBreakout.*; mid-ORB seeds → confirm → execute)
COPY ["easyBreakout", "./easyBreakout"]

# Profit optimizer — read-only flight recorder (ORB SO + ORB 0DTE premium paths; hooks in prime_trading_system)
COPY ["profit_optimizer", "./profit_optimizer"]

# Rev 00294: Copy cleanup scripts for /api/cleanup/images endpoint (revision cleanup uses Python API; image script needs gcloud for manual runs)
COPY ["scripts/cleanup_old_images.sh", "scripts/cleanup_old_revisions.sh", "./scripts/"]
RUN chmod +x ./scripts/cleanup_old_images.sh ./scripts/cleanup_old_revisions.sh

# AGGRESSIVE: Remove ALL Python cache files and verify VERSION changed
RUN find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find . -name "*.pyc" -delete 2>/dev/null || true && \
    find . -name "*.pyo" -delete 2>/dev/null || true && \
    echo "✅ Python cache cleared after code copy" && \
    if [ -f VERSION.txt ]; then echo "📦 Build version: $(cat VERSION.txt)"; else echo "⚠️ No VERSION.txt"; fi

# Create necessary directories first
RUN mkdir -p logs data data/watchlist data/score

# Verify all three trading paths are present (fail image build if incomplete)
RUN echo "=== Easy ORB Strategy (core) ===" && \
    test -f modules/prime_trading_system.py && test -f modules/prime_options_stealth_trailing_tp.py && \
    echo "✅ ORB modules: prime_trading_system, prime_options_stealth_trailing_tp" && \
    test -f observability.py && \
    echo "✅ observability.py present" && \
    echo "=== Watchlist ===" && \
    ls -lh data/watchlist/ && \
    test -f modules/watchlist_loader.py && \
    echo "✅ watchlist_loader.py present" && \
    test -f data/watchlist/0dte_list.csv && \
    echo "✅ 0dte_list.csv ($(wc -l < data/watchlist/0dte_list.csv) lines)" && \
    echo "=== easy0DTE (options path) ===" && \
    test -f easy0DTE/__init__.py && \
    test -f easy0DTE/modules/prime_0dte_strategy_manager.py && \
    test -f easy0DTE/modules/options_trading_executor.py && \
    test -f easy0DTE/modules/options_chain_manager.py && \
    test -f easy0DTE/modules/etrade_options_api.py && \
    test -f easy0DTE/modules/mock_options_executor.py && \
    test -f easy0DTE/configs/0dte.env && \
    test -f easy0DTE/modules/greedy_mined_profile_rules.csv && \
    echo "✅ easy0DTE: package + core modules + configs/0dte.env + mined profile rules" && \
    echo "=== easyTrendline (Trendline 0DTE path) ===" && \
    test -f easyTrendline/__init__.py && \
    test -f easyTrendline/trendline_signal_engine.py && \
    test -f easyTrendline/trendline_options_executor.py && \
    test -f easyTrendline/trendline_metadata_persist.py && \
    echo "✅ easyTrendline: $(ls -1 easyTrendline/*.py | wc -l) Python modules in package" && \
    echo "=== easyBreakout (Breakout 0DTE path) ===" && \
    test -f easyBreakout/__init__.py && \
    test -f easyBreakout/breakout_options_executor.py && \
    test -f easyBreakout/breakout_confirmation.py && \
    test -f easyBreakout/breakout_quality.py && \
    test -f easyBreakout/breakout_pc_oi_gate.py && \
    test -f configs/Breakout0DTE.env && \
    echo "✅ easyBreakout: $(ls -1 easyBreakout/*.py | wc -l) Python modules + Breakout0DTE.env" && \
    echo "=== profit_optimizer (exit research flight recorder) ===" && \
    test -f profit_optimizer/__init__.py && \
    test -f profit_optimizer/profit_optimizer.py && \
    test -f profit_optimizer/trading_hooks.py && \
    test -f profit_optimizer/trade_flight_recorder.py && \
    test -f profit_optimizer/shadow_path.py && \
    test -f profit_optimizer/shadow_continuity.py && \
    test -f profit_optimizer/app_identity.py && \
    test -f profit_optimizer/path_classifier.py && \
    test -f profit_optimizer/so_premium_shadow.py && \
    echo "✅ profit_optimizer package present (shadow path + dual-app labels)"

# Set environment variables (PYTHONDONTWRITEBYTECODE already set earlier)
ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1
ENV PORT=8080
ENV HOST=0.0.0.0

# Expose port
EXPOSE 8080

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD curl -f http://localhost:8080/health || exit 1

# Run the application (Cloud Run entrypoint)
# cloud_run_entry.py listens on PORT immediately, then runs main (avoids startup timeout)
CMD find . -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find . -name "*.pyc" -delete 2>/dev/null || true && \
    python -B cloud_run_entry.py