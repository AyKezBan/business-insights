package ru.businessinsights.backend.ml;

public final class MlException extends RuntimeException {
    public enum Code {
        TIMEOUT, UNAVAILABLE, RATE_LIMITED, REQUEST_REJECTED, CONTEXT_EXPIRED,
        TRACE_NOT_FOUND, INVALID_RESPONSE, INTERRUPTED
    }

    private final Code code;
    private final boolean retryable;

    public MlException(Code code, String message, boolean retryable) {
        super(message);
        this.code = code;
        this.retryable = retryable;
    }

    public MlException(Code code, String message, boolean retryable, Throwable cause) {
        super(message, cause);
        this.code = code;
        this.retryable = retryable;
    }

    public Code code() {
        return code;
    }

    /**
     * User initiated retry can be offered; the client never automatically repeats a POST.
     */
    public boolean retryable() {
        return retryable;
    }
}
