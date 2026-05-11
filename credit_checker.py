"""
Real credit balance checker for AI providers.

Supports two strategies (combined):
  1. Pre-call: Query provider API for current balance
  2. Post-call: Detect insufficient credit from API error responses

Each provider has its own API endpoint format:
  - MuleRun:  GET {api_base}/v1/dashboard/billing/credit_grants  (Authorization: Bearer <key>)
  - ChatGPT (OpenAI):  GET {api_base}/v1/dashboard/billing/credit_grants  or /v1/organizations
  - Qwen (DashScope):  GET {api_base}/api/v1/users/me  or billing endpoint

The checker gracefully degrades: if the API query fails, it falls back to
the locally stored credit_balance in the database.
"""
import logging
from typing import Optional, Tuple

import requests

log = logging.getLogger(__name__)

# HTTP timeout for credit queries (seconds)
_QUERY_TIMEOUT = 10

# Error codes / messages that indicate insufficient credits
_INSUFFICIENT_CREDIT_INDICATORS = {
    "insufficient_quota",
    "insufficient_balance",
    "billing_hard_limit_reached",
    "rate_limit_exceeded",
    "quota_exceeded",
    "InsufficientBalance",
    "insufficient_credit",
    "credit_limit",
    "exceeded your current quota",
    "You exceeded your current quota",
    "account_deactivated",
    "balance not enough",
}


class CreditCheckResult:
    """Result of a credit balance query."""
    __slots__ = ("success", "balance", "currency", "error")

    def __init__(self, success: bool, balance: float = 0.0,
                 currency: str = "", error: str = ""):
        self.success = success
        self.balance = balance
        self.currency = currency
        self.error = error

    def __repr__(self):
        if self.success:
            return f"CreditCheckResult(balance={self.balance}, currency={self.currency})"
        return f"CreditCheckResult(error={self.error})"


def query_credit_balance(service_type: str, api_key: str,
                         api_base: str = "", extra: dict = None) -> CreditCheckResult:
    """
    Query the real credit balance from the provider's API.

    Args:
        service_type: "mulerun" | "chatgpt" | "qwen"
        api_key: The decrypted API key / token
        api_base: Base URL override (from credential.extra.api_base)
        extra: Additional config from credential.extra

    Returns:
        CreditCheckResult with balance if successful, error message if not.
    """
    extra = extra or {}

    try:
        if service_type == "mulerun":
            return _query_mulerun(api_key, api_base or extra.get("api_base", ""))
        elif service_type == "chatgpt":
            return _query_openai(api_key, api_base or extra.get("api_base", ""))
        elif service_type == "qwen":
            return _query_qwen(api_key, api_base or extra.get("api_base", ""))
        else:
            return CreditCheckResult(False, error=f"不支持的服务类型: {service_type}")
    except Exception as e:
        log.warning("Credit query failed for %s: %s", service_type, e)
        return CreditCheckResult(False, error=str(e))


def check_error_is_credit_exhausted(status_code: int, response_body: str) -> bool:
    """
    Post-call check: determine if an API error response indicates
    that credits are exhausted.

    Args:
        status_code: HTTP status code from the AI API call
        response_body: Response body text

    Returns:
        True if the error indicates insufficient credits.
    """
    # 402 Payment Required is a clear signal
    if status_code == 402:
        return True

    # 429 with specific messages about quota (not just rate limiting)
    if status_code == 429:
        body_lower = response_body.lower()
        if any(indicator.lower() in body_lower for indicator in [
            "exceeded your current quota",
            "insufficient_quota",
            "billing_hard_limit_reached",
            "quota_exceeded",
        ]):
            return True

    # 403 with billing-related messages
    if status_code == 403:
        body_lower = response_body.lower()
        if any(indicator.lower() in body_lower for indicator in [
            "insufficient_balance",
            "account_deactivated",
            "billing",
            "credit",
        ]):
            return True

    # Generic check across all status codes
    body_lower = response_body.lower()
    for indicator in _INSUFFICIENT_CREDIT_INDICATORS:
        if indicator.lower() in body_lower:
            return True

    return False


# ---------------------------------------------------------------------------
# Provider-specific implementations
# ---------------------------------------------------------------------------

def _headers(api_key: str) -> dict:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _query_mulerun(api_key: str, api_base: str) -> CreditCheckResult:
    """Query MuleRun credit balance.

    MuleRun API follows OpenAI-compatible format.
    Try multiple known endpoints in order.
    """
    base = (api_base or "https://api.mulerun.com").rstrip("/")
    endpoints = [
        f"{base}/v1/dashboard/billing/credit_grants",
        f"{base}/v1/billing/credit_grants",
        f"{base}/v1/user/balance",
    ]

    for url in endpoints:
        try:
            resp = requests.get(url, headers=_headers(api_key), timeout=_QUERY_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                # Try common response formats
                balance = (
                    data.get("total_available", None)
                    or data.get("balance", None)
                    or data.get("data", {}).get("balance", None)
                    or data.get("data", {}).get("total_available", None)
                    or data.get("credits", {}).get("remaining", None)
                )
                if balance is not None:
                    return CreditCheckResult(
                        True, float(balance),
                        data.get("currency", data.get("data", {}).get("currency", "credits")))

            elif resp.status_code == 404:
                continue  # Try next endpoint
            else:
                # Non-404 error - check if it's a credit issue
                if check_error_is_credit_exhausted(resp.status_code, resp.text):
                    return CreditCheckResult(True, 0.0, "credits",
                                             "API 返回积分不足")
                continue
        except requests.exceptions.RequestException:
            continue

    return CreditCheckResult(False, error="无法查询 MuleRun 积分余额 (所有端点均失败)")


def _query_openai(api_key: str, api_base: str) -> CreditCheckResult:
    """Query OpenAI / ChatGPT credit balance."""
    base = (api_base or "https://api.openai.com").rstrip("/")
    endpoints = [
        f"{base}/v1/dashboard/billing/credit_grants",
        f"{base}/v1/billing/credit_grants",
    ]

    for url in endpoints:
        try:
            resp = requests.get(url, headers=_headers(api_key), timeout=_QUERY_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                balance = (
                    data.get("total_available", None)
                    or data.get("total_remaining", None)
                )
                if balance is not None:
                    return CreditCheckResult(True, float(balance), "USD")
            elif resp.status_code == 404:
                continue
            else:
                if check_error_is_credit_exhausted(resp.status_code, resp.text):
                    return CreditCheckResult(True, 0.0, "USD", "API 返回积分不足")
                continue
        except requests.exceptions.RequestException:
            continue

    # OpenAI may not expose billing to API keys - fall back
    # Try a minimal completion to see if the key works
    try:
        test_url = f"{base}/v1/chat/completions"
        test_payload = {
            "model": "gpt-3.5-turbo",
            "messages": [{"role": "user", "content": "hi"}],
            "max_tokens": 1,
        }
        resp = requests.post(test_url, headers=_headers(api_key),
                             json=test_payload, timeout=_QUERY_TIMEOUT)
        if resp.status_code == 200:
            return CreditCheckResult(False, error="余额查询不可用，但 API 可正常调用")
        if check_error_is_credit_exhausted(resp.status_code, resp.text):
            return CreditCheckResult(True, 0.0, "USD", "API 调用返回积分不足")
        return CreditCheckResult(False, error=f"API 返回 {resp.status_code}")
    except requests.exceptions.RequestException as e:
        return CreditCheckResult(False, error=str(e))


def _query_qwen(api_key: str, api_base: str) -> CreditCheckResult:
    """Query Alibaba DashScope (Qwen) credit balance."""
    base = (api_base or "https://dashscope.aliyuncs.com").rstrip("/")
    endpoints = [
        f"{base}/api/v1/users/me",
        f"{base}/api/v1/billing/credit",
    ]

    for url in endpoints:
        try:
            resp = requests.get(url, headers=_headers(api_key), timeout=_QUERY_TIMEOUT)
            if resp.status_code == 200:
                data = resp.json()
                # DashScope nests under "data" or "output"
                inner = data.get("data", data.get("output", data))
                balance = (
                    inner.get("balance", None)
                    or inner.get("remaining_quota", None)
                    or inner.get("credit_balance", None)
                )
                if balance is not None:
                    return CreditCheckResult(True, float(balance),
                                             inner.get("currency", "CNY"))
            elif resp.status_code == 404:
                continue
            else:
                if check_error_is_credit_exhausted(resp.status_code, resp.text):
                    return CreditCheckResult(True, 0.0, "CNY", "API 返回积分不足")
                continue
        except requests.exceptions.RequestException:
            continue

    return CreditCheckResult(False, error="无法查询通义千问积分余额")
