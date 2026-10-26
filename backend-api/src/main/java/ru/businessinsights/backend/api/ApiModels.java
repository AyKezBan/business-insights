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
                              @Pattern(regexp = "https?://[^\\s]+") @Size(max = 2048) String url,
                              @Size(max = 200) String docId,
                              @Pattern(regexp = "[a-f0-9]{64}") String contentSha256,
                              @Size(max = 100000) String excerpt) {
        public SourceInput(String sourceId, String title, LocalDate date, Integer page, String url) {
            this(sourceId, title, date, page, url, null, null, null);
        }
    }

    public record SourceConflictInput(@NotBlank @Size(max = 2000) String reason,
                                      @NotEmpty @Size(max = 31) List<@NotBlank @Size(max = 200) String> sourceIds,
                                      boolean resolved) {
    }

    // Callers provide external news only. Bank evidence comes from the ML bank index.
    public record EventInput(@NotBlank @Size(max = 200) @Pattern(regexp = "[A-Za-z0-9_.:-]+") String eventId,
                             @NotBlank @Size(max = 500) String title,
                             @NotBlank @Size(max = 100000) String text, Instant publishedAt,
                             @NotNull @Valid SourceInput source,
                             @Size(max = 30) List<@NotNull @Valid SourceInput> relatedSources,
                             @Size(max = 30) List<@NotBlank @Size(max = 100) String> topics,
                             @Size(max = 30) List<@NotNull @Valid SourceConflictInput> conflicts) {
        public EventInput(String eventId, String title, String text, Instant publishedAt,
                          SourceInput source, List<SourceInput> relatedSources, List<String> topics) {
            this(eventId, title, text, publishedAt, source, relatedSources, topics, List.of());
        }
        public EventInput {
            relatedSources = relatedSources == null ? List.of() : List.copyOf(relatedSources);
            topics = topics == null ? List.of() : List.copyOf(topics);
            conflicts = conflicts == null ? List.of() : List.copyOf(conflicts);
        }
    }

    public record EventRevisionInput(@NotNull @Positive Long expectedVersion, @NotNull @Valid EventInput event) {
    }

    public record EventVersionView(String eventId, long version, String contentHash, Instant receivedAt,
                                   EventInput event) {
    }

    public record FeedRefreshInput(@NotNull Role role, @Min(1) @Max(100) Integer sourceLimit,
                                   @Min(0) @Max(1000000) Integer sourceOffset, @Min(1) @Max(16) Integer maxAnalyses,
                                   @Size(max = 20) Map<@NotBlank @Size(max = 80) String, @NotBlank @Size(max = 1000) String> userProvided) {
    }

    public record FeedCandidateView(String eventId, long eventVersion, int relevanceScore, List<String> reasons,
                                    Instant expiresAt, String decision, OperationView operation) {
    }

    public record FeedRefreshView(String corpusRevision, Integer nextOffset, List<FeedCandidateView> candidates) {
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

    public record EvidenceSpanView(String sourceId, String contentSha256, long start, long end) {}

    public record ClaimView(String text, String kind, String origin, List<String> sourceIds,
                            List<String> profileFeatures, String level, String horizon, String claimId,
                            List<EvidenceSpanView> evidenceSpans) {
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
                              Instant asOf, String corpusRevision, String eventContentSha256,
                              List<ConflictView> conflicts, Instant openedAt, FeedbackView feedback,
                              long eventVersion, boolean stale, Instant expiresAt, int relevanceScore,
                              List<String> relevanceReasons, List<SourceConflictInput> sourceConflicts) {
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

    public record CatalogTokenView(String storeId, long generation, String revision) {}

    public record MaterialEventView(String eventId, String title, String text, Instant publishedAt,
                                    LocalDate publishedDate, String publishedPrecision, SourceView source,
                                    List<SourceView> relatedSources, List<String> topics, List<ConflictView> conflicts,
                                    String eventContentSha256, String corpusRevision) {}

    public record MaterialCatalogView(CatalogTokenView catalogToken, List<MaterialEventView> items,
                                      int limit, int offset, int total) {}

    public record ActionView(UUID actionId, UUID insightId, String action, String status, String delivery) {
    }
}
