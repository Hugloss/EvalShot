"""E241/E242: single-attempt OpenAI-compatible HTTPS request boundary.

This provides a runnable, deliberately narrow provider transport owned by a
trusted external host. It does not intercept native Codex/OpenCode/Claude Code.
No retry, redirect, alternate URL, proxy fallback, or unsigned observation
can satisfy provider-submission verification. The host owns real credential
custody; this module never writes request/response contents to disk.
"""

from __future__ import annotations

import hashlib
import json
import ssl
from typing import Any, Mapping
from urllib.parse import urlsplit
from urllib.request import (
    HTTPRedirectHandler, HTTPSHandler, ProxyHandler, Request, build_opener,
)

from .host_input_attestation import canonical
from .trusted_host_capture import TrustedModelRequestCapture

MAX_BODY_BYTES = 8_388_608
MAX_RESPONSE_BYTES = 2_097_152


class _RejectRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: object, fp: object, code: object,
                         msg: object, headers: object, newurl: object) -> None:
        raise ValueError("provider-redirect-forbidden")


def _endpoint(endpoint: str, *, approved_origin: str) -> str:
    """Origin pinned by the privileged host, never returned by the agent."""
    if not isinstance(endpoint, str) or not isinstance(approved_origin, str):
        raise ValueError("invalid-provider-endpoint")
    url = urlsplit(endpoint)
    trusted = urlsplit(approved_origin)
    if (url.scheme != "https" or trusted.scheme != "https"
            or not url.hostname or not trusted.hostname
            or url.username is not None or url.password is not None
            or trusted.username is not None or trusted.password is not None
            or url.query or url.fragment or trusted.query or trusted.fragment
            or trusted.path not in ("", "/")
            or (url.hostname, url.port or 443) !=
               (trusted.hostname, trusted.port or 443)
            or url.path != "/v1/chat/completions"):
        raise ValueError("untrusted-provider-endpoint-or-path")
    return endpoint


def validate_sse_response(body: bytes) -> bool:
    """Only complete OpenAI-style SSE with a terminal [DONE] is accepted.

    A stream truncated after a syntactically valid chunk must never become
    a completed provider submission receipt.
    """
    try:
        value = body.decode("utf-8")
    except (UnicodeError, AttributeError):
        return False
    if not value.endswith(("\n\n", "\r\n\r\n")):
        return False
    chunks: list[str] = []
    for event in value.replace("\r\n", "\n").split("\n\n"):
        if not event.strip():
            continue
        rows = [
            line[5:].lstrip(" ") for line in event.split("\n")
            if line.startswith("data:")
        ]
        if len(rows) != 1:
            return False
        chunks.append(rows[0])
    if len(chunks) < 2 or chunks[-1] != "[DONE]" or "[DONE]" in chunks[:-1]:
        return False
    saw_choice = False
    for chunk in chunks[:-1]:
        try:
            event = json.loads(chunk)
        except (TypeError, ValueError):
            return False
        if not isinstance(event, dict) or not isinstance(event.get("choices"), list):
            return False
        if event["choices"]:
            saw_choice = True
    return saw_choice


def _send_verified_https_chat(
    *,
    url: str,
    serialized_model_request: bytes,
    api_key: str,
    timeout_seconds: int,
    accept_sse: bool,
) -> tuple[int, bytes]:
    """Shared single-attempt provider boundary; no retries or leaked bodies."""
    # The caller MUST mark its transport attempt before calling this helper.
    # All request and response bytes stay in the privileged host process.
    try:
        opener = build_opener(
            ProxyHandler({}), _RejectRedirect(),
            HTTPSHandler(context=ssl.create_default_context()),
        )
        req = Request(
            url, data=serialized_model_request, method="POST",
            headers={
                "Authorization": "Bearer " + api_key,
                "Content-Type": "application/json",
                "Accept": "text/event-stream" if accept_sse else "application/json",
            },
        )
        with opener.open(req, timeout=timeout_seconds) as response:
            status = response.status
            body = response.read(MAX_RESPONSE_BYTES + 1)
            headers = getattr(response, "headers", None)
            content_type = (
                headers.get("Content-Type", "") if headers is not None else ""
            )
        if (type(status) is not int or not 200 <= status < 300
                or not isinstance(body, bytes)
                or not 0 < len(body) <= MAX_RESPONSE_BYTES):
            raise ValueError("provider-response-incomplete")
        if accept_sse:
            if (not isinstance(content_type, str)
                    or not content_type.lower().startswith("text/event-stream")
                    or not validate_sse_response(body)):
                raise ValueError("incomplete-or-invalid-streaming-provider-response")
        else:
            parsed = json.loads(body)
            if (not isinstance(parsed, dict)
                    or not isinstance(parsed.get("choices"), list)
                    or not parsed["choices"]):
                raise ValueError("invalid-openai-compatible-provider-response")
        return status, body
    except Exception:
        # URLs, provider exceptions, credentials and response bodies may contain
        # secrets. Never expose them or convert partial responses into evidence.
        raise ValueError("provider-submission-unverified") from None


def _dispatch_verified_chat_request(
    capture: TrustedModelRequestCapture,
    *,
    serialized_model_request: bytes,
    endpoint: str, approved_origin: str, api_key: str,
    tool_call_id: str, returned_packet: object, request_sequence: int,
    current: Mapping[str, Any], replaced: Mapping[str, Any] | None = None,
    catalog_sha256: str, prompt_sha256: str,
    oracle_sha256: str, workspace_sha256: str,
    timeout_seconds: int = 60,
    accept_sse: bool = False,
) -> tuple[dict[str, Any], bytes]:
    """Execute exactly one validated request through an HTTPS transport.

    Validate before work; only a bounded successful response is marked
    submitted. Failures never permit retry or a finalized transport receipt
    on this capture instance. Model attention and provider consumption
    are unproven even when a 2xx JSON response is observed.
    """
    if not isinstance(capture, TrustedModelRequestCapture):
        raise ValueError("trusted-host-capture-required")
    url = _endpoint(endpoint, approved_origin=approved_origin)
    if (not isinstance(api_key, str) or not 1 <= len(api_key) <= 4096
            or any(ch in api_key for ch in ("\r", "\n"))
            or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300
            or not isinstance(serialized_model_request, bytes)
            or not 0 < len(serialized_model_request) <= MAX_BODY_BYTES):
        raise ValueError("invalid-host-provider-transport-parameters")
    if accept_sse:
        try:
            parsed_request = json.loads(serialized_model_request)
        except (TypeError, ValueError):
            raise ValueError("invalid-streaming-model-request") from None
        if not isinstance(parsed_request, dict) or parsed_request.get("stream") is not True:
            raise ValueError("streaming-response-not-requested")
    # The capture validates declared assignment, model, tool response, exact
    # model-input message and non-tool prompt identity before network work.
    observed = capture.observe_outbound_request(
        serialized_model_request=serialized_model_request,
        tool_call_id=tool_call_id, returned_packet=returned_packet,
        request_sequence=request_sequence, current=current, replaced=replaced,
        catalog_sha256=catalog_sha256, prompt_sha256=prompt_sha256,
        oracle_sha256=oracle_sha256, workspace_sha256=workspace_sha256,
    )
    capture.validate_delivery_provider_origin(
        endpoint_sha256=hashlib.sha256(url.encode("utf-8")).hexdigest(),
    )
    capture.begin_provider_transport()
    status, body = _send_verified_https_chat(
        url=url, serialized_model_request=serialized_model_request,
        api_key=api_key, timeout_seconds=timeout_seconds, accept_sse=accept_sse,
    )
    capture.record_provider_submission(
        tool_call_id=tool_call_id,
        serialized_model_request=serialized_model_request,
        endpoint_sha256=hashlib.sha256(url.encode("utf-8")).hexdigest(),
        status=status,
        response_sha256=hashlib.sha256(body).hexdigest(),
        response_bytes=len(body),
    )
    return ({
        "schema": "agentscookbook.provider-transport-submission.v1",
        "provider_submission_state": "SUBMITTED",
        "request_sha256": observed["model_request_sha256"],
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "response_bytes": len(body),
        "http_status": status,
        "provider_consumption_proven": False,
        "model_attention_proven": False,
        "causal_influence_proven": False,
    }, body)


def dispatch_verified_chat_request(**kwargs: Any) -> dict[str, Any]:
    """Compatibility interface: expose only submission metadata to caller."""
    metadata, _ = _dispatch_verified_chat_request(**kwargs)
    return metadata


def dispatch_verified_chat_response(**kwargs: Any) -> bytes:
    """Privileged host-only: relay the exact bounded upstream response bytes.

    The host must keep these bytes in memory and return them over its native
    provider connection without writing user/model content to disk.
    """
    _, body = _dispatch_verified_chat_request(**kwargs)
    return body


def dispatch_verified_chat_stream_response(**kwargs: Any) -> bytes:
    """Return exactly one validated and terminally complete SSE response."""
    _, body = _dispatch_verified_chat_request(**kwargs, accept_sse=True)
    return body


def _dispatch_verified_pre_exposure_chat_request(
    capture: TrustedModelRequestCapture,
    *,
    serialized_model_request: bytes,
    endpoint: str, approved_origin: str, api_key: str,
    request_sequence: int = 1,
    timeout_seconds: int = 60,
) -> tuple[dict[str, Any], bytes]:
    """Send the deferred arm's real tool-free provider request exactly once.

    Unlike a synthetic transport receipt, this calls the same HTTPS boundary
    as evidence delivery, then records a prior submission only after a fully
    validated 2xx response. It cannot run for any other assigned arm.
    """
    if not isinstance(capture, TrustedModelRequestCapture):
        raise ValueError("trusted-host-capture-required")
    url = _endpoint(endpoint, approved_origin=approved_origin)
    if (
        not isinstance(api_key, str) or not 1 <= len(api_key) <= 4096
        or any(ch in api_key for ch in ("\r", "\n"))
        or type(timeout_seconds) is not int or not 1 <= timeout_seconds <= 300
        or not isinstance(serialized_model_request, bytes)
        or not 0 < len(serialized_model_request) <= MAX_BODY_BYTES
    ):
        raise ValueError("invalid-host-provider-transport-parameters")
    # Validate the live serialized bytes and frozen assignment before I/O.
    prior = capture.observe_pre_exposure_request(
        serialized_model_request=serialized_model_request,
        request_sequence=request_sequence,
    )
    capture.begin_pre_exposure_transport()
    status, body = _send_verified_https_chat(
        url=url, serialized_model_request=serialized_model_request,
        api_key=api_key, timeout_seconds=timeout_seconds, accept_sse=False,
    )
    capture.record_pre_exposure_submission(
        serialized_model_request=serialized_model_request,
        endpoint_sha256=hashlib.sha256(url.encode("utf-8")).hexdigest(),
        status=status,
        response_sha256=hashlib.sha256(body).hexdigest(),
        response_bytes=len(body),
    )
    return ({
        "schema": "agentscookbook.provider-prior-submission.v1",
        "provider_submission_state": "SUBMITTED",
        "prior_request_sha256": prior["prior_request_sha256"],
        "response_sha256": hashlib.sha256(body).hexdigest(),
        "response_bytes": len(body),
        "http_status": status,
        "provider_consumption_proven": False,
        "model_attention_proven": False,
        "causal_influence_proven": False,
    }, body)


def dispatch_verified_pre_exposure_chat_request(**kwargs: Any) -> dict[str, Any]:
    """Host-only metadata for the deferred arm's prior submitted request."""
    metadata, _ = _dispatch_verified_pre_exposure_chat_request(**kwargs)
    return metadata


def dispatch_verified_pre_exposure_chat_response(**kwargs: Any) -> bytes:
    """Host-only response bytes; never written to local trial artifacts."""
    _, body = _dispatch_verified_pre_exposure_chat_request(**kwargs)
    return body
