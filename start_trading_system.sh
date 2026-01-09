#!/bin/bash

# Eiqora Trading System - Startup Script
# Starts all components with proper logging

set -e  # Exit on error

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
LOG_DIR="$SCRIPT_DIR/logs"
PID_DIR="$SCRIPT_DIR/.pids"

# Create directories
mkdir -p "$LOG_DIR"
mkdir -p "$PID_DIR"

# Colors for output
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo -e "${BLUE}  Eiqora Trading System - Startup${NC}"
echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
echo ""

# Function to check if a service is running using pgrep
check_status() {
    local service=$1
    local pid_file="$PID_DIR/${service}.pid"
    local pattern=""
    
    if [ "$service" == "data_collector" ]; then
        pattern="data_collection.scheduler"
    elif [ "$service" == "live_pipeline" ]; then
        pattern="eiqora_v2.live.pipeline"
    elif [ "$service" == "candidate_selector" ]; then
        pattern="eiqora_v2.live.candidate_selector"
    fi
    
    # Check if process is running via pgrep
    local pid=""
    if [ -n "$pattern" ]; then
        pid=$(pgrep -f "$pattern" | head -n 1)
    fi
    
    if [ -n "$pid" ]; then
        if [ ! -f "$pid_file" ] || [ "$(cat "$pid_file")" != "$pid" ]; then
             echo "$pid" > "$pid_file"
        fi
        return 0
    else
        [ -f "$pid_file" ] && rm "$pid_file"
        return 1
    fi
}

# Function to start a service
start_service() {
    local name=$1
    local command=$2
    local log_file="$LOG_DIR/${name}.log"
    local pid_file="$PID_DIR/${name}.pid"
    
    echo -e "${YELLOW}Starting $name...${NC}"
    
    if check_status "$name"; then
        local pid=$(cat "$pid_file")
        echo -e "${GREEN}  ✓ Already running (PID: $pid)${NC}"
        return 0
    fi
    
    # Start the service
    local out_log="$log_file"
    if [ "$name" == "live_pipeline" ]; then
        out_log="$LOG_DIR/${name}.stdout.log"
    fi

    nohup bash -c "cd $SCRIPT_DIR && $command" > "$out_log" 2>&1 &
    local new_pid=$!
    echo $new_pid > "$pid_file"
    
    sleep 2
    if check_status "$name"; then
        local pid=$(cat "$pid_file")
        echo -e "${GREEN}  ✓ Started (PID: $pid)${NC}"
        echo -e "  Log: $out_log"
        if [ "$name" == "live_pipeline" ]; then
            echo -e "  Live log: $LOG_DIR/live_pipeline.log"
        fi
    else
        echo -e "${RED}  ✗ Failed to start${NC}"
        echo -e "  Check log: $log_file"
        return 1
    fi
}

# Function to stop a service
stop_service() {
    local name=$1
    local pid_file="$PID_DIR/${name}.pid"
    local pattern=""
    
    if [ "$name" == "live_pipeline" ]; then
        pattern="eiqora_v2.live.pipeline"
    elif [ "$name" == "data_collector" ]; then
        pattern="data_collection.scheduler"
    fi
    
    if check_status "$name"; then
        local pid=$(cat "$pid_file")
        echo -e "${YELLOW}Stopping $name (PID: $pid)...${NC}"
        kill "$pid" 2>/dev/null
        
        local count=0
        while check_status "$name" && [ $count -lt 5 ]; do
            sleep 1
            count=$((count+1))
        done
        
        if check_status "$name"; then
            echo -e "${RED}  Force killing...${NC}"
            if [ -n "$pattern" ]; then
                pkill -f "$pattern"
            else
                kill -9 "$pid" 2>/dev/null
            fi
        fi
        
        rm -f "$pid_file"
        echo -e "${GREEN}  ✓ Stopped${NC}"
    else
        echo -e "${RED}$name is not running${NC}"
        rm -f "$pid_file"
        return 0
    fi
}

# Function to show status
show_status() {
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo -e "${BLUE}  System Status${NC}"
    echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
    echo ""
    
    for service in data_collector candidate_selector live_pipeline; do
        local pid_file="$PID_DIR/${service}.pid"
        if check_status "$service"; then
            pid=$(cat "$pid_file")
            echo -e "${GREEN}✓ $service${NC} (PID: $pid)"
        else
            echo -e "${RED}✗ $service${NC} (not running)"
        fi
    done
    echo ""
}

# Function to tail logs
tail_logs() {
    echo -e "${BLUE}Tailing logs (Ctrl+C to stop)...${NC}"
    echo ""
    tail -f "$LOG_DIR"/*.log
}

# Main command handling
case "${1:-start}" in
    start)
        echo -e "${GREEN}Starting all services...${NC}"
        echo ""
        
        # 1. Data Collection Scheduler
        start_service "data_collector" \
            "source .venv/bin/activate && python -m data_collection.scheduler"
        
        echo ""
        
        # 2. Candidate Selector (runs once, then daily via scheduler)
        # We'll run it once on startup, then it runs daily at configured time
        echo -e "${YELLOW}Running initial candidate selection...${NC}"
        source .venv/bin/activate && python -c "
from eiqora_v2.live.candidate_selector import CandidateSelector
from datetime import datetime, timezone
import asyncio

async def run():
    selector = CandidateSelector(threshold=0.70)
    watchlist = await selector.build_watchlist(datetime.now(timezone.utc))
    await selector.save_watchlist(watchlist, datetime.now(timezone.utc).date())
    print(f'Built watchlist with {len(watchlist)} candidates')

asyncio.run(run())
"
        echo -e "${GREEN}  ✓ Initial candidates built${NC}"
        
        echo ""
        
        # 3. Live Trading Pipeline
        start_service "live_pipeline" \
            "source .venv/bin/activate && python -m eiqora_v2.live.pipeline"
        
        echo ""
        echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
        echo -e "${GREEN}All services started successfully!${NC}"
        echo -e "${BLUE}════════════════════════════════════════════════════════════${NC}"
        echo ""
        echo "Logs are available in: $LOG_DIR"
        echo ""
        echo "Commands:"
        echo "  ./start_trading_system.sh status    - Check service status"
        echo "  ./start_trading_system.sh logs      - Tail all logs"
        echo "  ./start_trading_system.sh stop      - Stop all services"
        echo ""
        ;;
        
    stop)
        echo -e "${YELLOW}Stopping all services...${NC}"
        echo ""
        stop_service "live_pipeline"
        stop_service "data_collector"
        echo ""
        echo -e "${GREEN}All services stopped${NC}"
        ;;
        
    restart)
        $0 stop
        sleep 2
        $0 start
        ;;
        
    status)
        show_status
        ;;
        
    logs)
        tail_logs
        ;;
        
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
