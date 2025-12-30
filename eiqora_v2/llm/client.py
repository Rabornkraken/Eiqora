"""
OpenRouter LLM client using AsyncOpenAI.
Provides structured JSON output with schema validation and retry logic.
"""

import json
import logging
from typing import Any, TypeVar

from openai import AsyncOpenAI
from pydantic import BaseModel, ValidationError

from eiqora_v2.config.settings import get_settings

logger = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)

_client: AsyncOpenAI | None = None


def get_llm_client() -> AsyncOpenAI:
    """Get or create the OpenRouter AsyncOpenAI client."""
    global _client
    if _client is None:
        settings = get_settings()
        _client = AsyncOpenAI(
            base_url=settings.OPENROUTER_BASE_URL,
            api_key=settings.OPENROUTER_API_KEY,
            default_headers={
                "HTTP-Referer": settings.SITE_URL,
                "X-Title": settings.SITE_NAME,
            },
            timeout=settings.LLM_TIMEOUT_SECONDS,
        )
    return _client


async def call_llm(
    prompt: str,
    schema: type[T],
    system_prompt: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> T:
    """
    Call LLM and parse response into a Pydantic model.
    
    Args:
        prompt: User prompt to send
        schema: Pydantic model class to parse response into
        system_prompt: Optional system prompt override
        model: Optional model override (defaults to settings.DEFAULT_MODEL)
        temperature: Optional temperature override
    
    Returns:
        Parsed Pydantic model instance
        
    Raises:
        ValueError: If response cannot be parsed after retries
    """
    settings = get_settings()
    client = get_llm_client()
    
    model = model or settings.DEFAULT_MODEL
    temperature = temperature if temperature is not None else settings.LLM_TEMPERATURE
    
    # Default system prompt for structured output
    if system_prompt is None:
        system_prompt = (
            "You are a financial analysis agent in a multi-agent trading system.\n"
            "CRITICAL RULES:\n"
            "1. Return ONLY valid JSON matching the provided schema. No markdown, no explanation.\n"
            "2. Never invent performance metrics (win rate, expected return).\n"
            "3. If uncertain, set quality flags; do not guess.\n"
            "4. All times are UTC. Never use data after asof_time."
        )
    
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": prompt},
    ]
    
    last_error: Exception | None = None
    
    for attempt in range(settings.LLM_MAX_RETRIES + 1):
        try:
            response = await client.chat.completions.create(
                model=model,
                messages=messages,
                response_format={"type": "json_object"},
                temperature=temperature,
            )
            
            content = response.choices[0].message.content
            if not content:
                raise ValueError("Empty response from LLM")
            
            # Parse JSON and validate against schema
            data = json.loads(content)
            result = schema.model_validate(data)
            
            logger.debug(f"LLM response parsed successfully on attempt {attempt + 1}")
            return result
            
        except json.JSONDecodeError as e:
            last_error = e
            logger.warning(f"JSON parse error on attempt {attempt + 1}: {e}")
            # Add error context to next attempt
            messages.append({
                "role": "assistant",
                "content": content if 'content' in dir() else "",
            })
            messages.append({
                "role": "user",
                "content": f"Your response was not valid JSON. Error: {e}. Please try again with valid JSON only.",
            })
            
        except ValidationError as e:
            last_error = e
            logger.warning(f"Schema validation error on attempt {attempt + 1}: {e}")
            # Add error context to next attempt
            messages.append({
                "role": "assistant",
                "content": content if 'content' in dir() else "",
            })
            messages.append({
                "role": "user",
                "content": f"Your response did not match the required schema. Errors: {e.errors()}. Please fix and try again.",
            })
            
        except Exception as e:
            last_error = e
            logger.error(f"LLM call failed on attempt {attempt + 1}: {e}")
            if attempt == settings.LLM_MAX_RETRIES:
                raise
    
    raise ValueError(f"Failed to get valid response after {settings.LLM_MAX_RETRIES + 1} attempts. Last error: {last_error}")


async def call_llm_raw(
    prompt: str,
    system_prompt: str | None = None,
    model: str | None = None,
    temperature: float | None = None,
) -> str:
    """
    Call LLM and return raw string response (no JSON parsing).
    
    Useful for narrative generation where structured output isn't needed.
    """
    settings = get_settings()
    client = get_llm_client()
    
    model = model or settings.DEFAULT_MODEL
    temperature = temperature if temperature is not None else settings.LLM_TEMPERATURE
    
    messages = [{"role": "user", "content": prompt}]
    if system_prompt:
        messages.insert(0, {"role": "system", "content": system_prompt})
    
    response = await client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    
    return response.choices[0].message.content or ""
