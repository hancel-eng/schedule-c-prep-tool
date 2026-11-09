"""Turns every way an AI call can fail into one clear, fixable error.

Each failure gets a short code (stable, searchable), a plain-English
message a non-technical user can understand, a concrete next step, and
the raw technical detail for whoever has to fix it.
"""
from typing import Any, Dict, Optional


class AIExtractionError(Exception):
    def __init__(self, code: str, message: str, fix: str, detail: str = ""):
        self.code = code
        self.message = message
        self.fix = fix
        self.detail = detail
        super().__init__(str(self))

    def __str__(self) -> str:
        text = f"[{self.code}] {self.message} What to do: {self.fix}"
        if self.detail:
            text += f" (Technical detail: {self.detail})"
        return text

    def to_dict(self) -> Dict[str, str]:
        return {"code": self.code, "message": self.message, "fix": self.fix, "detail": self.detail}


def missing_api_key() -> AIExtractionError:
    return AIExtractionError(
        "NO_API_KEY",
        "The app has no AI key, so it can't read files.",
        "Add OPENAI_API_KEY in the app's Streamlit Secrets, then reload the page.",
    )


def _error_code_of(exc: Exception) -> Optional[str]:
    body: Any = getattr(exc, "body", None)
    if isinstance(body, dict):
        inner = body.get("error") if isinstance(body.get("error"), dict) else body
        return inner.get("code") or inner.get("type")
    return getattr(exc, "code", None)


def from_openai_exception(exc: Exception) -> AIExtractionError:
    """Maps an exception raised by the openai SDK to an AIExtractionError."""
    import openai

    detail = f"{type(exc).__name__}: {exc}"
    code = _error_code_of(exc)

    if isinstance(exc, openai.AuthenticationError):
        return AIExtractionError(
            "BAD_API_KEY", "OpenAI rejected the AI key.",
            "Check that OPENAI_API_KEY in Streamlit Secrets is copied exactly and hasn't been deleted in your OpenAI account.",
            detail)
    if isinstance(exc, openai.PermissionDeniedError):
        return AIExtractionError(
            "NO_PERMISSION", "This AI key isn't allowed to do this.",
            "In your OpenAI account, check the key's permissions and that the project can use this model.",
            detail)
    if isinstance(exc, openai.RateLimitError):
        if code == "insufficient_quota":
            return AIExtractionError(
                "OUT_OF_CREDIT", "The OpenAI account has run out of credit.",
                "Add credit or a payment method in your OpenAI account's Billing page, then try again.",
                detail)
        return AIExtractionError(
            "TOO_MANY_REQUESTS", "OpenAI is getting too many requests right now.",
            "Wait one minute, then try this file again.", detail)
    if isinstance(exc, openai.NotFoundError):
        return AIExtractionError(
            "MODEL_NOT_FOUND", "The AI model this app uses isn't available on this OpenAI account.",
            "Check the model name in core/idp_extractor.py against the models your OpenAI account can use.",
            detail)
    if isinstance(exc, (openai.BadRequestError, openai.UnprocessableEntityError)):
        return AIExtractionError(
            "FILE_REJECTED", "OpenAI couldn't accept this file.",
            "The file may be too large, damaged, password-protected, or a type the AI can't open. Read the technical detail for the exact reason.",
            detail)
    if isinstance(exc, openai.APITimeoutError):
        return AIExtractionError(
            "TIMEOUT", "The AI took too long to answer.",
            "Try this file again. If it keeps happening, the file may be too long to read in one go.",
            detail)
    if isinstance(exc, openai.APIConnectionError):
        return AIExtractionError(
            "NO_CONNECTION", "The app couldn't reach OpenAI.",
            "Check the internet connection, then try again.", detail)
    if isinstance(exc, openai.InternalServerError):
        return AIExtractionError(
            "OPENAI_SERVER_ERROR", "OpenAI had a problem on its side.",
            "Wait a few minutes and try again.", detail)
    if isinstance(exc, openai.LengthFinishReasonError):
        return answer_cut_off(detail)
    if isinstance(exc, openai.ContentFilterFinishReasonError):
        return content_blocked(detail)
    return AIExtractionError(
        "UNKNOWN_AI_ERROR", "Something unexpected went wrong while the AI was reading this file.",
        "Send the technical detail below to whoever maintains the app.", detail)


def answer_cut_off(detail: str = "") -> AIExtractionError:
    return AIExtractionError(
        "ANSWER_CUT_OFF", "The AI's answer was cut off before it finished, so some data would be missing.",
        "Nothing from this answer was used. Try again; if it repeats, the file needs to be read in smaller parts.",
        detail)


def content_blocked(detail: str = "") -> AIExtractionError:
    return AIExtractionError(
        "CONTENT_BLOCKED", "OpenAI's safety filter stopped the answer.",
        "Check that the file is a real financial document, then try again.", detail)


def ai_refused(detail: str = "") -> AIExtractionError:
    return AIExtractionError(
        "AI_REFUSED", "The AI declined to read this file.",
        "Check that the file is a real financial document and is readable, then try again.", detail)


def empty_answer(detail: str = "") -> AIExtractionError:
    return AIExtractionError(
        "EMPTY_ANSWER", "The AI answered with nothing.",
        "Try this file again.", detail)


def unreadable_answer(detail: str = "") -> AIExtractionError:
    return AIExtractionError(
        "UNREADABLE_ANSWER", "The AI's answer wasn't in the format the app expects.",
        "Try this file again. If it repeats, send the technical detail to whoever maintains the app.",
        detail)


def response_failed(detail: str = "") -> AIExtractionError:
    return AIExtractionError(
        "AI_FAILED", "OpenAI reported that it couldn't finish reading this file.",
        "Try again in a few minutes.", detail)
