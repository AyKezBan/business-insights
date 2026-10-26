package ru.businessinsights.backend.ml;

public final class MlException extends RuntimeException {
    public enum Code {
        TIMEOUT, UNAVAILABLE, RATE_LIMITED, REQUEST_REJECTED, CONTEXT_EXPIRED,
        TRACE_NOT_FOUND, INVALID_RESPONSE, INTERRUPTED,
        CORPUS_EVENT_MISMATCH, CORPUS_UNAVAILABLE, CATALOG_UNINITIALIZED, CATALOG_CURSOR_MISMATCH
    }

    private final Code code;
    private final boolean retryable;
    private final Integer upstreamStatus;

    public MlException(Code code, String message, boolean retryable) {
        super(message);
        this.code = code;
        this.retryable = retryable;
        this.upstreamStatus = null;
    }

    public MlException(Code code, String message, boolean retryable, Throwable cause) {
        super(message, cause);
        this.code = code;
        this.retryable = retryable;
        this.upstreamStatus = null;
    }

    /** Only known corpus code/status pairs may carry a public upstream classification. */
    public MlException(Code code, String message, boolean retryable, int upstreamStatus) {
        super(message);
        boolean allowed = switch (code) {
            case CORPUS_EVENT_MISMATCH, CATALOG_CURSOR_MISMATCH -> upstreamStatus == 409;
            case CORPUS_UNAVAILABLE -> upstreamStatus == 409 || upstreamStatus == 503;
            case CATALOG_UNINITIALIZED -> upstreamStatus == 503;
            default -> false;
        };
        if (!allowed) throw new IllegalArgumentException("Unsupported corpus error status");
        this.code = code;
        this.retryable = retryable;
        this.upstreamStatus = upstreamStatus;
    }

    public Code code() {
        return code;
    }

    public Integer upstreamStatus() { return upstreamStatus; }

    /**
     * User initiated retry can be offered; the client never automatically repeats a POST.
     */
    public boolean retryable() {
        return retryable;
    }
}
