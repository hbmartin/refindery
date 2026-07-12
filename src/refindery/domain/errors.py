"""Domain error hierarchy.

Messages are built inside each exception class (keeps call sites clean and
satisfies structured error handling at the API layer).
"""


class RefinderyError(Exception):
    """Base class for all domain errors."""


class ConfigurationError(RefinderyError):
    """The application cannot start with the supplied configuration."""


class BlacklistedError(RefinderyError):
    """The URL matches a blacklist rule."""

    def __init__(self, pattern: str) -> None:
        self.pattern = pattern
        super().__init__(f"url matches blacklist pattern {pattern!r}")


class ModelBudgetError(RefinderyError):
    """A model's token budget is below the canonical chunk hard max."""

    def __init__(self, *, model_id: str, max_input_tokens: int, hard_max: int) -> None:
        self.model_id = model_id
        self.max_input_tokens = max_input_tokens
        self.hard_max = hard_max
        super().__init__(
            f"model {model_id!r} accepts {max_input_tokens} tokens, below the "
            f"canonical chunk hard max of {hard_max}; registering it would force "
            f"a re-chunk and invalidate every other model's index"
        )


class PageNotFoundError(RefinderyError):
    """No page with the given id exists."""

    def __init__(self, page_id: str) -> None:
        self.page_id = page_id
        super().__init__(f"page {page_id!r} not found")


class PageHasNoBodyError(RefinderyError):
    """The page reached the indexing pipeline without a resolved body."""

    def __init__(self, page_id: str) -> None:
        self.page_id = page_id
        super().__init__(f"page {page_id!r} has no body to index")


class JobNotFoundError(RefinderyError):
    """No job with the given id exists."""

    def __init__(self, job_id: str) -> None:
        self.job_id = job_id
        super().__init__(f"job {job_id!r} not found")


class WatchNotFoundError(RefinderyError):
    """No watch with the given id exists."""

    def __init__(self, watch_id: str) -> None:
        self.watch_id = watch_id
        super().__init__(f"watch {watch_id!r} not found")


class WatchSourceUnavailableError(RefinderyError):
    """The watch's kind has no wired source (its extra is not installed)."""

    def __init__(self, *, kind: str) -> None:
        self.kind = kind
        super().__init__(f"no source available for watch kind {kind!r}")


class WatchFanOutError(RefinderyError):
    """None of a watch poll's discovered items could be ingested."""

    def __init__(self, *, watch_id: str, item_count: int) -> None:
        self.watch_id = watch_id
        self.item_count = item_count
        super().__init__(
            f"watch {watch_id!r} failed to ingest all {item_count} discovered items"
        )


class ModelNotFoundError(RefinderyError):
    """No embedding model with the given id is registered."""

    def __init__(self, model_id: str) -> None:
        self.model_id = model_id
        super().__init__(f"embedding model {model_id!r} not registered")


class NoActiveModelError(RefinderyError):
    """No embedding model is currently active."""

    def __init__(self) -> None:
        super().__init__("no active embedding model; register and activate one")


class BodyConflictError(RefinderyError):
    """Both body_extracted and body_html were supplied."""

    def __init__(self) -> None:
        super().__init__("body_extracted and body_html are mutually exclusive")


class FeatureUnavailableError(RefinderyError):
    """A requested capability lands in a later milestone."""

    def __init__(self, *, feature: str, milestone: str) -> None:
        self.feature = feature
        self.milestone = milestone
        super().__init__(f"{feature} is not available yet (arrives in {milestone})")


class FetchFailedError(RefinderyError):
    """Fetching the URL failed (network error or non-2xx status)."""

    def __init__(self, *, url: str, detail: str) -> None:
        self.url = url
        self.detail = detail
        super().__init__(f"fetching {url!r} failed: {detail}")


class ProviderUnavailableError(RefinderyError):
    """An external provider's circuit breaker is open; the call was not attempted."""

    def __init__(self, *, provider: str, retry_after_s: float) -> None:
        self.provider = provider
        self.retry_after_s = retry_after_s
        super().__init__(
            f"provider {provider!r} unavailable (circuit open); "
            f"retry in ~{retry_after_s:.0f}s"
        )


class UnsupportedContentTypeError(RefinderyError):
    """No extractor is registered for the fetched content type."""

    def __init__(self, content_type: str) -> None:
        self.content_type = content_type
        super().__init__(f"no extractor for content type {content_type!r}")


class ExtractionUnavailableError(RefinderyError):
    """The extractor for this content type needs an optional extra installed."""

    def __init__(self, *, content_type: str, extra: str) -> None:
        self.content_type = content_type
        self.extra = extra
        super().__init__(
            f"extracting {content_type!r} requires the {extra!r} extra; "
            f"install with: uv add 'refindery[{extra}]'"
        )
