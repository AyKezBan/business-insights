package ru.businessinsights.backend.data;

public final class ProfileDataException extends RuntimeException {
    public enum Reason {
        COMPANY_NOT_FOUND, AMBIGUOUS_COMPANY, EXTERNAL_COMPANY, NOT_CLIENT,
        DATASET_UNAVAILABLE, DATASET_INCOMPATIBLE
    }

    private final Reason reason;

    public ProfileDataException(Reason reason, String message) {
        super(message);
        this.reason = reason;
    }

    public Reason reason() {
        return reason;
    }
}
