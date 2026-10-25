package ru.businessinsights.backend.api;

import com.fasterxml.jackson.databind.JsonNode;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.UUID;

/**
 * Public camelCase API; intentionally independent of storage and ML wire DTOs.
 */
public final class ApiModels {
    private ApiModels() {
    }

    public enum Role {owner, ceo, cfo, chief_accountant, treasurer, other_c_level, other}

    public enum Rating {USEFUL, IRRELEVANT, UNSUPPORTED, OTHER}

    public enum Action {CONTACT_MANAGER, REQUEST_CALLBACK, REQUEST_ANALYSIS}

    public record SourceInput(@NotBlank @Size(max = 200) String sourceId,
                              @NotBlank @Size(max = 500) String title, LocalDate date,
                              @Positive Integer page,
                              @Pattern(regexp = "https?://[^\\s]+") @Size(max = 2048) String url) {
    }

    // Callers provide external news only. Bank evidence comes from the ML bank index.
    public record EventInput(@NotBlank @Size(max = 200) @Pattern(regexp = "[A-Za-z0-9_.:-]+") String eventId,
                             @NotBlank @Size(max = 500) String title,
                             @NotBlank @Size(max = 100000) String text, Instant publishedAt,
                             @NotNull @Valid SourceInput source,
                             @Size(max = 30) List<@NotNull @Valid SourceInput> relatedSources,
                             @Size(max = 30) List<@NotBlank @Size(max = 100) String> topics) {
    }

    public record AnalysisInput(@NotBlank @Pattern(regexp = "U_\\d+") String companyId,
                                @NotNull Role role, @NotBlank @Size(max = 200) String eventId,
                                @Size(max = 20) Map<@NotBlank @Size(max = 80) String, @NotBlank @Size(max = 1000) String> userProvided,
                                Instant asOf, @Pattern(regexp = "[a-f0-9]{64}") String corpusRevision,
                                @Pattern(regexp = "[a-f0-9]{64}") String eventContentSha256, @Size(min = 1, max = 128) String requestId) {
        public AnalysisInput(String companyId, Role role, String eventId, Map<String, String> userProvided) {
            this(companyId, role, eventId, userProvided, null, null, null, null);
        }
    }

    public record QuestionInput(@NotBlank @Size(max = 1000) String question) {
    }

    public record StateInput(@NotNull Boolean hidden) {
    }

    public record FeedbackInput(@NotNull Rating rating, @Size(max = 2000) String comment) {
    }

    public record ActionInput(@NotNull Action action, @Size(max = 2000) String comment) {
    }

    public record SessionView(String personId, String authentication, String authorization) {
    }

    public record CompanyView(String companyId, String profileStatus, String clientType,
                              String okvedName, String registrationCity, String registrationRegion) {
    }

    public record CompanyFacts(String companyId, String groupId, String okvedCode, String okvedName,
                               String registrationCity, String registrationRegion, BigDecimal revenueRub,
                               int revenueYear, String currency, BigDecimal consolidatedGroupRevenueRub,
                               String clientType, String employeesResolved, Boolean ftsInformationObserved,
                               List<String> ftsCountries, BigDecimal selfTransferShareOfObservedEligiblePayments,
                               String revenueStatus, String revenueScope, Boolean foreignTradeDataAvailable,
                               String paymentPeriod, List<String> profileQualityNotes) {
    }

    public record ProfileView(String companyId, CompanyFacts facts, JsonNode provenance,
                              List<String> qualityIssues, boolean sufficientForAnalysis) {
    }

    public record ProblemView(int status, String code, String message, boolean retryable, String requestId) {
    }

    public record FeedbackView(String rating, String comment) {}

    public record FailureView(String code, String message, boolean retryable) {
    }

    public record OperationView(UUID operationId, String kind, String status, Instant createdAt,
                                Instant updatedAt, FailureView error, List<String> warnings,
                                UUID insightId, UUID parentInsightId, String outcome, String requestId, String corpusRevision) {
    }

    public record SourceView(String sourceId, String kind, String title, LocalDate date, Integer page, String url,
                             String docId, String contentSha256, String excerpt, String publisherId, String adapterId,
                             String materialType, String completeness, String language, LocalDate publishedDate,
                             String publishedPrecision, Instant firstSeenAt, Instant retrievedAt, String statementType,
                             String targetPeriod, LocalDate validUntil, String rightsUrl, String translationAttribution,
                             String rawPublication, String sourceTimezone, Integer sourceUtcOffsetSeconds, String timezoneProvenance) {
    }

    public record ClaimView(String text, String kind, String origin, List<String> sourceIds,
                            List<String> profileFeatures, String level, String horizon, String claimId) {
    }

    public record ConflictView(String reason, List<String> sourceIds, boolean resolved) {}

    public record ScenarioView(String name, String trigger, String implication, String plausibility,
                               List<String> sourceIds) {
    }

    public record SelfCheckView(boolean passed, JsonNode issues, int iterations, List<String> adaptations) {
    }

    public record InsightView(UUID insightId, String companyId, Role role, String eventId,
                              String status, String mlStatus, String headline,
                              List<ClaimView> whatHappened, List<ClaimView> whyItMatters,
                              List<ClaimView> consequences, List<ClaimView> risks, List<ClaimView> opportunities,
                              List<ScenarioView> scenarios, List<ClaimView> actions, List<ClaimView> bankView,
                              List<String> modelVsBank, String impactLevel, String impactRationale,
                              List<String> profileFeaturesUsed, List<SourceView> sources, List<String> missingData,
                              SelfCheckView selfCheck, List<String> models, Instant generatedAt, String disclaimer,
                              List<String> warnings, boolean hidden, String mlInsightId, String runId, Integer version,
                              String outcome, String errorCode, String requestId, String parentRunId, boolean cacheHit,
                              Instant asOf, String corpusRevision, String eventContentSha256, List<ConflictView> conflicts, Instant openedAt, FeedbackView feedback) {
    }

    public record AnswerView(UUID operationId, UUID insightId, String status, String mlStatus, String text,
                             List<ClaimView> claims, List<SourceView> sources, List<String> missingData,
                             String disclaimer, List<String> warnings, String mlInsightId, String runId, String companyId,
                             String eventId, Instant asOf, String corpusRevision, String eventContentSha256) {
    }

    public record TraceView(UUID operationId, UUID insightId, String traceStatus, JsonNode steps,
                            JsonNode profileSnapshot,
                            List<String> warnings) {
    }

    public record FeedView(List<InsightView> items, int limit, int offset, String order, String emptyReason) {
    }

    public record ActionView(UUID actionId, UUID insightId, String action, String status, String delivery) {
    }
}
