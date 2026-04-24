"""
LLM Client for CloudByte

Wrapper around litellm for multi-provider LLM support with error handling
and retry logic.
"""

import time
from typing import Any, Dict, Optional

from src.common.logging import get_logger
from src.integrations.llm.config import merge_endpoint_config


logger = get_logger(__name__)


class LLMError(Exception):
    """Base exception for LLM-related errors."""
    pass


class LLMAuthenticationError(LLMError):
    """Raised when API key is invalid or authentication fails."""
    pass


class LLMRateLimitError(LLMError):
    """Raised when rate limit is exceeded."""
    pass


class LLMConnectionError(LLMError):
    """Raised when connection to LLM provider fails."""
    pass


class LLMResponseError(LLMError):
    """Raised when LLM response is invalid or cannot be parsed."""
    pass


class LLMClient:
    """
    Client for interacting with LLM providers via litellm.

    Handles:
    - Multiple provider support (OpenAI, Anthropic, etc.)
    - Error handling and retries
    - Logging
    """

    def __init__(self, endpoint_config: Dict[str, Any]):
        """
        Initialize LLM client with endpoint configuration.

        Args:
            endpoint_config: Dictionary containing:
                - provider: litellm provider name (e.g., "openai", "anthropic")
                - model: Model name (e.g., "gpt-4o", "claude-3-5-sonnet-20241022")
                - api_key: API key for the provider
                - temperature: Sampling temperature (optional)
                - max_tokens: Maximum tokens to generate (optional)
                - base_url: Custom base URL (optional)
        """
        self.provider = endpoint_config.get("provider", "openai")
        self.model = endpoint_config.get("model", "gpt-4o")
        self.api_key = endpoint_config.get("api_key", "")
        self.temperature = endpoint_config.get("temperature", 0.7)
        self.max_tokens = endpoint_config.get("max_tokens", 2000)
        self.base_url = endpoint_config.get("base_url", None)

        if not self.api_key:
            logger.warning(f"LLMClient initialized without API key for provider '{self.provider}'")

    def _build_litellm_params(self, **kwargs) -> Dict[str, Any]:
        """
        Build parameters for litellm.completion() call.

        Args:
            **kwargs: Additional parameters to override defaults

        Returns:
            dict: Parameters for litellm call
        """
        # Merge with runtime overrides
        config = merge_endpoint_config(
            {
                "provider": self.provider,
                "model": self.model,
                "temperature": self.temperature,
                "max_tokens": self.max_tokens,
            },
            **kwargs
        )

        # Build model string with provider prefix if needed
        # litellm expects format like "provider/model" or just model with provider param
        provider = config.get("provider", "")
        model = config.get("model", "")

        # Check if model already includes provider prefix
        if "/" in model and not model.startswith("http"):
            # Model already has provider prefix (e.g., "gemini/gemini-2.0-flash-exp")
            model_for_litellm = model
            provider_for_litellm = None
        else:
            # Need to add provider prefix
            # For "gemini/gemini-api" style providers, extract the base provider
            if "/" in provider:
                # Extract base provider (e.g., "gemini" from "gemini/gemini-api")
                base_provider = provider.split("/")[0]
                model_for_litellm = f"{base_provider}/{model}"
            else:
                model_for_litellm = f"{provider}/{model}" if provider else model
            provider_for_litellm = None

        params = {
            "model": model_for_litellm,
            "temperature": config.get("temperature", self.temperature),
            "max_tokens": config.get("max_tokens", self.max_tokens),
        }

        # Add API key
        params["api_key"] = self.api_key

        # Add base_url if specified
        if self.base_url:
            params["base_url"] = self.base_url

        return params

    def complete(self, prompt: str, **kwargs) -> str:
        """
        Generate a completion for the given prompt.

        Args:
            prompt: The prompt to send to the LLM
            **kwargs: Additional parameters to override defaults

        Returns:
            str: The generated text response

        Raises:
            LLMAuthenticationError: If API key is invalid
            LLMRateLimitError: If rate limit is exceeded
            LLMConnectionError: If connection fails
            LLMResponseError: If response is invalid
        """
        try:
            from litellm import completion

            params = self._build_litellm_params(**kwargs)

            logger.debug(f"Calling LLM: provider={self.provider}, model={params['model']}")

            response = completion(
                messages=[{"role": "user", "content": prompt}],
                **params
            )

            # Extract text from response
            if hasattr(response, "choices") and len(response.choices) > 0:
                text = response.choices[0].message.content
                logger.debug(f"LLM response received: {len(text)} chars")
                return text
            else:
                raise LLMResponseError("Invalid response format from LLM")

        except ImportError as e:
            logger.error(f"litellm not installed: {e}")
            raise LLMError("litellm package is not installed. Run: pip install litellm")

        except Exception as e:
            error_str = str(e).lower()

            # Categorize errors
            if "authentication" in error_str or "unauthorized" in error_str or "invalid api key" in error_str:
                logger.error(f"LLM authentication error: {e}")
                raise LLMAuthenticationError(f"Authentication failed: {e}")

            elif "rate limit" in error_str or "rate_limit" in error_str or "too many requests" in error_str:
                logger.error(f"LLM rate limit error: {e}")
                raise LLMRateLimitError(f"Rate limit exceeded: {e}")

            elif "connection" in error_str or "network" in error_str or "timeout" in error_str:
                logger.error(f"LLM connection error: {e}")
                raise LLMConnectionError(f"Connection failed: {e}")

            else:
                logger.error(f"LLM error: {e}")
                raise LLMError(f"LLM call failed: {e}")

    def complete_with_retry(
        self,
        prompt: str,
        max_retries: int = 3,
        base_delay: float = 1.0,
        **kwargs
    ) -> str:
        """
        Generate a completion with retry logic for transient failures.

        Args:
            prompt: The prompt to send to the LLM
            max_retries: Maximum number of retry attempts
            base_delay: Base delay between retries (exponential backoff)
            **kwargs: Additional parameters to override defaults

        Returns:
            str: The generated text response

        Raises:
            LLMError: If all retries are exhausted
        """
        last_error = None

        for attempt in range(max_retries + 1):
            try:
                return self.complete(prompt, **kwargs)

            except (LLMRateLimitError, LLMConnectionError) as e:
                last_error = e

                if attempt < max_retries:
                    # Exponential backoff with jitter
                    delay = base_delay * (2 ** attempt) + (0.1 * attempt)
                    logger.warning(f"Retry {attempt + 1}/{max_retries} after {delay:.1f}s delay")
                    time.sleep(delay)
                else:
                    logger.error(f"All {max_retries} retries exhausted")

            except (LLMAuthenticationError, LLMResponseError) as e:
                # Don't retry auth or response errors
                raise e

        # If we get here, all retries failed
        raise LLMError(f"Failed after {max_retries} retries: {last_error}")

    def complete_json(self, prompt: str, **kwargs) -> Dict[str, Any]:
        """
        Generate a completion and parse as JSON.

        Args:
            prompt: The prompt to send to the LLM
            **kwargs: Additional parameters to override defaults

        Returns:
            dict: Parsed JSON response

        Raises:
            LLMResponseError: If response is not valid JSON
        """
        import json

        response_text = self.complete(prompt, **kwargs)

        try:
            # Try to parse as JSON
            return json.loads(response_text)
        except json.JSONDecodeError:
            # Try to extract JSON from markdown code blocks
            import re

            # Look for ```json...``` or ```...``` blocks
            json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?```", response_text, re.DOTALL)
            if json_match:
                try:
                    return json.loads(json_match.group(1))
                except json.JSONDecodeError:
                    pass

            raise LLMResponseError("LLM response is not valid JSON")
