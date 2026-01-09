#!/bin/bash
# Simple, working initialization script

cd "$(dirname "$0")"
source .venv/bin/activate

echo "════════════════════════════════════════════════════════════"
echo "  Eiqora System Initialization"
echo "════════════════════════════════════════════════════════════"
echo ""

echo "[Starting Data Collector]"
nohup python -m data_collection.scheduler > logs/data_collector.log 2>&1 &
DATA_PID=$!
echo "✅ Data collector started (PID: $DATA_PID)"

echo ""
echo "System is running!"
echo ""
echo "Monitor logs:"
echo "  tail -f logs/data_collector.log"
echo ""
echo "Check status anytime:"
echo "  python -c 'from data_collection.db.connection import get_connection; ...'"
echo ""
echo "Stop data collector:"
echo "  kill $DATA_PID"
echo ""
