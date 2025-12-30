"""
Metrics and observability for the trading system.
Tracks LLM costs, latency, and agent performance.
"""

import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any
from collections import defaultdict

logger = logging.getLogger(__name__)


@dataclass
class LLMCall:
    """Record of a single LLM call."""
    agent: str
    model: str
    input_tokens: int
    output_tokens: int
    latency_ms: int
    timestamp: datetime
    success: bool
    error: str | None = None


@dataclass
class AgentMetrics:
    """Metrics for a single agent."""
    calls: int = 0
    successes: int = 0
    failures: int = 0
    total_latency_ms: int = 0
    total_input_tokens: int = 0
    total_output_tokens: int = 0
    
    @property
    def success_rate(self) -> float:
        return self.successes / self.calls if self.calls > 0 else 0.0
    
    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.calls if self.calls > 0 else 0.0
    
    @property
    def total_tokens(self) -> int:
        return self.total_input_tokens + self.total_output_tokens


class MetricsCollector:
    """
    Collects and aggregates metrics for the trading system.
    
    Usage:
        metrics = MetricsCollector()
        metrics.record_llm_call(agent="chart", model="deepseek-v3.2", ...)
        summary = metrics.get_summary()
    """
    
    def __init__(self):
        self.llm_calls: list[LLMCall] = []
        self.agent_metrics: dict[str, AgentMetrics] = defaultdict(AgentMetrics)
        self.sweep_metrics: list[dict] = []
        self._start_time: datetime | None = None
    
    def start_sweep(self):
        """Mark start of a sweep."""
        self._start_time = datetime.now()
    
    def end_sweep(self, summary: dict):
        """Record end of a sweep."""
        if self._start_time:
            duration = (datetime.now() - self._start_time).total_seconds()
            self.sweep_metrics.append({
                "timestamp": self._start_time,
                "duration_seconds": duration,
                **summary,
            })
        self._start_time = None
    
    def record_llm_call(
        self,
        agent: str,
        model: str,
        input_tokens: int,
        output_tokens: int,
        latency_ms: int,
        success: bool = True,
        error: str | None = None,
    ):
        """Record an LLM call."""
        call = LLMCall(
            agent=agent,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=latency_ms,
            timestamp=datetime.now(),
            success=success,
            error=error,
        )
        self.llm_calls.append(call)
        
        # Update agent metrics
        metrics = self.agent_metrics[agent]
        metrics.calls += 1
        if success:
            metrics.successes += 1
        else:
            metrics.failures += 1
        metrics.total_latency_ms += latency_ms
        metrics.total_input_tokens += input_tokens
        metrics.total_output_tokens += output_tokens
    
    def get_summary(self, window_hours: int = 24) -> dict[str, Any]:
        """Get metrics summary for the specified window."""
        cutoff = datetime.now() - timedelta(hours=window_hours)
        recent_calls = [c for c in self.llm_calls if c.timestamp >= cutoff]
        
        if not recent_calls:
            return {
                "period_hours": window_hours,
                "total_calls": 0,
                "total_cost_usd": 0,
            }
        
        # Calculate costs (approximate for DeepSeek V3.2)
        # Input: $0.14/1M tokens, Output: $0.28/1M tokens
        total_input = sum(c.input_tokens for c in recent_calls)
        total_output = sum(c.output_tokens for c in recent_calls)
        cost_usd = (total_input * 0.14 / 1_000_000) + (total_output * 0.28 / 1_000_000)
        
        # Agent breakdown
        agent_breakdown = {}
        for agent, metrics in self.agent_metrics.items():
            agent_breakdown[agent] = {
                "calls": metrics.calls,
                "success_rate": f"{metrics.success_rate:.1%}",
                "avg_latency_ms": round(metrics.avg_latency_ms),
                "total_tokens": metrics.total_tokens,
            }
        
        return {
            "period_hours": window_hours,
            "total_calls": len(recent_calls),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_cost_usd": round(cost_usd, 4),
            "avg_latency_ms": round(sum(c.latency_ms for c in recent_calls) / len(recent_calls)),
            "success_rate": f"{sum(1 for c in recent_calls if c.success) / len(recent_calls):.1%}",
            "agent_breakdown": agent_breakdown,
        }
    
    def get_cost_by_day(self, days: int = 7) -> list[dict]:
        """Get daily cost breakdown."""
        cutoff = datetime.now() - timedelta(days=days)
        recent_calls = [c for c in self.llm_calls if c.timestamp >= cutoff]
        
        daily_costs = defaultdict(lambda: {"input_tokens": 0, "output_tokens": 0, "calls": 0})
        
        for call in recent_calls:
            day = call.timestamp.strftime("%Y-%m-%d")
            daily_costs[day]["input_tokens"] += call.input_tokens
            daily_costs[day]["output_tokens"] += call.output_tokens
            daily_costs[day]["calls"] += 1
        
        result = []
        for day, data in sorted(daily_costs.items()):
            cost = (data["input_tokens"] * 0.14 / 1_000_000) + (data["output_tokens"] * 0.28 / 1_000_000)
            result.append({
                "date": day,
                "calls": data["calls"],
                "tokens": data["input_tokens"] + data["output_tokens"],
                "cost_usd": round(cost, 4),
            })
        
        return result
    
    def print_dashboard(self):
        """Print a simple text dashboard."""
        summary = self.get_summary()
        
        print("\n" + "=" * 60)
        print("  EIQORA V2 METRICS DASHBOARD")
        print("=" * 60)
        
        print(f"\nLast {summary['period_hours']} hours:")
        print(f"  Total LLM calls: {summary.get('total_calls', 0)}")
        print(f"  Total tokens: {summary.get('total_input_tokens', 0) + summary.get('total_output_tokens', 0):,}")
        print(f"  Estimated cost: ${summary.get('total_cost_usd', 0):.4f}")
        print(f"  Avg latency: {summary.get('avg_latency_ms', 0)}ms")
        print(f"  Success rate: {summary.get('success_rate', '0%')}")
        
        if summary.get("agent_breakdown"):
            print("\nAgent breakdown:")
            for agent, stats in summary["agent_breakdown"].items():
                print(f"  {agent}: {stats['calls']} calls, {stats['avg_latency_ms']}ms avg, {stats['success_rate']} success")
        
        print("=" * 60 + "\n")


# Global metrics instance
_metrics = MetricsCollector()


def get_metrics() -> MetricsCollector:
    """Get the global metrics collector."""
    return _metrics


def reset_metrics():
    """Reset the global metrics collector."""
    global _metrics
    _metrics = MetricsCollector()
