from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


PRICING_SOURCE = "https://cloud.google.com/vertex-ai/generative-ai/pricing"
PRICING_CHECKED_AT = "2026-08-24"


def _value(source: Any, name: str, default: int = 0) -> int:
    if source is None:
        return default
    value = source.get(name) if isinstance(source, dict) else getattr(source, name, None)
    return int(value or default)


def extract_google_usage(response: Any) -> dict[str, int]:
    metadata = getattr(response, "usage_metadata", None)
    return {
        "prompt_tokens": _value(metadata, "prompt_token_count"),
        "output_tokens": _value(metadata, "candidates_token_count"),
        "thoughts_tokens": _value(metadata, "thoughts_token_count"),
        "cached_tokens": _value(metadata, "cached_content_token_count"),
        "total_tokens": _value(metadata, "total_token_count"),
    }


def extract_openai_usage(response_json: dict[str, Any]) -> dict[str, int | float]:
    usage = response_json.get("usage") or {}
    result: dict[str, int | float] = {
        "prompt_tokens": _value(usage, "prompt_tokens"),
        "output_tokens": _value(usage, "completion_tokens"),
        "thoughts_tokens": 0,
        "cached_tokens": 0,
        "total_tokens": _value(usage, "total_tokens"),
    }
    if usage.get("cost") is not None:
        result["reported_cost_usd"] = float(usage["cost"])
    return result


def estimate_cost_usd(
    *,
    provider: str,
    model: str,
    operation: str,
    usage: dict[str, int | float],
    resolution: str | None = None,
) -> float | None:
    reported_cost = usage.get("reported_cost_usd")
    if reported_cost is not None:
        return float(reported_cost)
    if provider not in {"google", "vertex"}:
        return None

    prompt_tokens = int(usage.get("prompt_tokens", 0))
    cached_tokens = min(prompt_tokens, int(usage.get("cached_tokens", 0)))
    uncached_tokens = prompt_tokens - cached_tokens
    output_tokens = int(usage.get("output_tokens", 0))
    thoughts_tokens = int(usage.get("thoughts_tokens", 0))

    if model.startswith("gemini-2.5-flash") and "image" not in model:
        return (
            uncached_tokens * 0.30
            + cached_tokens * 0.03
            + (output_tokens + thoughts_tokens) * 2.50
        ) / 1_000_000

    if model.startswith("gemini-3-pro-image") and operation == "image_generation":
        image_cost = 0.24 if (resolution or "").upper() == "4K" else 0.134
        return image_cost + (uncached_tokens * 2.00 + cached_tokens * 0.20) / 1_000_000

    return None


class UsageLedger:
    def __init__(
        self,
        *,
        usage_dir: Path,
        command_id: str,
        progress: Callable[[str], None] | None = None,
        official_cost_lookup: Callable[[str, str], dict[str, Any]] | None = None,
        started_at: str | None = None,
    ) -> None:
        self.usage_dir = usage_dir
        self.command_id = command_id
        self.progress = progress
        self.official_cost_lookup = official_cost_lookup
        self.started_at = started_at or datetime.now(timezone.utc).isoformat()
        self.events: list[dict[str, Any]] = []

    def record(
        self,
        *,
        asset_id: str,
        operation: str,
        provider: str,
        model: str,
        usage: dict[str, int | float],
        resolution: str | None = None,
    ) -> dict[str, Any]:
        estimated_cost = estimate_cost_usd(
            provider=provider,
            model=model,
            operation=operation,
            usage=usage,
            resolution=resolution,
        )
        event = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "command_id": self.command_id,
            "asset_id": asset_id,
            "operation": operation,
            "provider": provider,
            "model": model,
            "resolution": resolution,
            "usage": usage,
            "estimated_cost_usd": estimated_cost,
            "pricing": {
                "currency": "USD",
                "estimate": True,
                "source": PRICING_SOURCE,
                "checked_at": PRICING_CHECKED_AT,
            },
        }
        self.events.append(event)
        self.usage_dir.mkdir(parents=True, exist_ok=True)
        with (self.usage_dir / "ai_usage.jsonl").open("a", encoding="utf-8") as ledger:
            ledger.write(json.dumps(event, sort_keys=True) + "\n")
        if self.progress is not None:
            cost_text = "unavailable" if estimated_cost is None else f"${estimated_cost:.6f}"
            self.progress(
                f"[AI USAGE] [{asset_id}] {operation} model={model} "
                f"input_tokens={int(usage.get('prompt_tokens', 0))} "
                f"output_tokens={int(usage.get('output_tokens', 0))} "
                f"estimated_cost_usd={cost_text}"
            )
        return event

    def finalize(self, *, ended_at: str | None = None) -> dict[str, Any]:
        ended_at = ended_at or datetime.now(timezone.utc).isoformat()
        cost_by_operation: dict[str, float] = {}
        for event in self.events:
            cost = event["estimated_cost_usd"]
            if cost is not None:
                operation = event["operation"]
                cost_by_operation[operation] = cost_by_operation.get(operation, 0.0) + cost
        summary = {
            "command_id": self.command_id,
            "started_at": self.started_at,
            "ended_at": ended_at,
            "request_count": len(self.events),
            "prompt_tokens": sum(int(event["usage"].get("prompt_tokens", 0)) for event in self.events),
            "output_tokens": sum(int(event["usage"].get("output_tokens", 0)) for event in self.events),
            "estimated_cost_usd": sum(cost_by_operation.values()),
            "estimated_cost_by_operation_usd": cost_by_operation,
            "unpriced_request_count": sum(
                event["estimated_cost_usd"] is None for event in self.events
            ),
            "pricing": {
                "currency": "USD",
                "estimate": True,
                "source": PRICING_SOURCE,
                "checked_at": PRICING_CHECKED_AT,
            },
        }
        if self.official_cost_lookup is not None and self.events:
            try:
                summary["official_billing"] = self.official_cost_lookup(
                    self.started_at, ended_at
                )
            except Exception as exc:
                summary["official_billing"] = {
                    "status": "unavailable",
                    "reason": str(exc),
                }
        elif self.official_cost_lookup is not None:
            summary["official_billing"] = {
                "status": "not_applicable",
                "reason": "command made no AI API requests",
            }
        command_dir = self.usage_dir / "commands"
        command_dir.mkdir(parents=True, exist_ok=True)
        (command_dir / f"{self.command_id}.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        if self.progress is not None:
            for operation, cost in sorted(cost_by_operation.items()):
                self.progress(
                    f"[AI COST] {operation} total estimated_cost_usd=${cost:.6f}"
                )
            self.progress(
                f"[AI COST] command total requests={summary['request_count']} "
                f"input_tokens={summary['prompt_tokens']} output_tokens={summary['output_tokens']} "
                f"estimated_cost_usd=${summary['estimated_cost_usd']:.6f} "
                f"unpriced_requests={summary['unpriced_request_count']}"
            )
            official = summary.get("official_billing")
            if official and official["status"] == "available":
                currency = official["currency"]
                self.progress(
                    f"[AI COST] official gross={currency} {official['gross_cost']:.6f} "
                    f"credits={currency} {official['credits']:.6f}"
                )
                self.progress(
                    f"[AI COST] official net={currency} {official['net_cost']:.6f} "
                    f"scope={official['scope']}"
                )
            elif official:
                self.progress(
                    f"[AI COST] official status={official['status']} "
                    f"reason={official['reason']}"
                )
            self.progress(f"[AI COST] saved={command_dir / f'{self.command_id}.json'}")
        return summary
