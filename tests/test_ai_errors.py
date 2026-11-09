"""Every way an AI call can fail must surface as a specific, readable error
code -- never a silent fallback or a partial answer. No network calls."""
from types import SimpleNamespace

import httpx2 as httpx
import openai
import pytest

from core import ai_errors
from core.ai_errors import AIExtractionError
from core.idp_extractor import parse_structured_response

_REQUEST = httpx.Request("POST", "https://api.openai.com/v1/responses")


def _status_error(cls, status, code=None):
    body = {"error": {"message": "boom", "code": code}} if code else {"error": {"message": "boom"}}
    return cls("boom", response=httpx.Response(status, request=_REQUEST), body=body)


@pytest.mark.parametrize("exc, expected_code", [
    (_status_error(openai.AuthenticationError, 401), "BAD_API_KEY"),
    (_status_error(openai.PermissionDeniedError, 403), "NO_PERMISSION"),
    (_status_error(openai.RateLimitError, 429, code="insufficient_quota"), "OUT_OF_CREDIT"),
    (_status_error(openai.RateLimitError, 429, code="rate_limit_exceeded"), "TOO_MANY_REQUESTS"),
    (_status_error(openai.NotFoundError, 404), "MODEL_NOT_FOUND"),
    (_status_error(openai.BadRequestError, 400), "FILE_REJECTED"),
    (_status_error(openai.InternalServerError, 500), "OPENAI_SERVER_ERROR"),
    (openai.APITimeoutError(request=_REQUEST), "TIMEOUT"),
    (openai.APIConnectionError(request=_REQUEST), "NO_CONNECTION"),
])
def test_openai_exceptions_map_to_specific_codes(exc, expected_code):
    err = ai_errors.from_openai_exception(exc)
    assert err.code == expected_code
    assert err.message and err.fix
    assert type(exc).__name__ in err.detail


def test_error_text_contains_code_message_and_fix():
    text = str(ai_errors.missing_api_key())
    assert "[NO_API_KEY]" in text and "What to do:" in text


def _response(status="completed", text="", reason=None, refusal=None, error=None):
    content = [SimpleNamespace(type="refusal", refusal=refusal)] if refusal else []
    return SimpleNamespace(
        status=status,
        incomplete_details=SimpleNamespace(reason=reason) if reason else None,
        error=error,
        output=[SimpleNamespace(type="message", content=content)],
        output_text=text,
    )


@pytest.mark.parametrize("response, expected_code", [
    (_response(status="incomplete", reason="max_output_tokens", text='{"partial": '), "ANSWER_CUT_OFF"),
    (_response(status="incomplete", reason="content_filter"), "CONTENT_BLOCKED"),
    (_response(status="failed", error="server_error"), "AI_FAILED"),
    (_response(refusal="I can't help with that."), "AI_REFUSED"),
    (_response(text="   "), "EMPTY_ANSWER"),
    (_response(text='{"not": "closed"'), "UNREADABLE_ANSWER"),
])
def test_bad_responses_are_rejected_with_specific_codes(response, expected_code):
    with pytest.raises(AIExtractionError) as info:
        parse_structured_response(response)
    assert info.value.code == expected_code


def test_cut_off_answer_is_rejected_even_if_its_text_happens_to_parse():
    response = _response(status="incomplete", reason="max_output_tokens", text='{"transactions": []}')
    with pytest.raises(AIExtractionError) as info:
        parse_structured_response(response)
    assert info.value.code == "ANSWER_CUT_OFF"


def test_complete_answer_is_parsed():
    assert parse_structured_response(_response(text='{"ok": true}')) == {"ok": True}
