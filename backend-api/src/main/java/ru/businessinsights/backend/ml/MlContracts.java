package ru.businessinsights.backend.ml;

import com.fasterxml.jackson.annotation.JsonIgnoreProperties;
import com.fasterxml.jackson.databind.PropertyNamingStrategies;
import com.fasterxml.jackson.databind.annotation.JsonNaming;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;

/**
 * Wire types for the pinned ML contract.
 */
public final class MlContracts {
    public static final String CONTRACT_COMMIT = "7cbd690892addc495fbf9bee144c48931c464170";
    public static final String DISCLAIMER = "Аналитика носит информационный характер и не является инвестиционной рекомендацией. "
            + "Выводы с пометкой «вывод модели» и «гипотеза» сформированы AI и не являются позицией Альфа-Банка.";

    private MlContracts() {
    }

    public enum UserRole {owner, ceo, cfo, chief_accountant, treasurer, other_c_level, other}

    public enum SourceKind {news, bank, profile}

    public enum ClaimKind {fact, assessment, inference, hypothesis, recommendation, unavailable}

    public enum Level {low, medium, high}

    public enum InsightStatus {complete, limited, failed}

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Products(Boolean settlement, Boolean guarantees, Boolean loans, Boolean leasing,
                           Boolean deposits, Boolean payroll, Boolean factoring) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Counterparty(String companyId, String direction, BigDecimal amountRub,
                               BigDecimal shareOfFlow, String okvedCode, String okvedName,
                               String region, Boolean sameGroup) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CompanyProfile(String companyId, String groupId, String okvedCode, String okvedName,
                                 String industry, BigDecimal revenueRub, BigDecimal groupRevenueRub,
                                 String employees, String companyAge, String businessStage,
                                 String ownership, String region, String city, String clientType,
                                 Products products, Boolean foreignTrade, List<String> foreignTradeCountries,
                                 List<Counterparty> counterparties, List<String> groupMemberOkveds,
                                 BigDecimal selfTransferShare, Map<String, String> userProvided,
                                 List<String> missingFields) {
        public CompanyProfile {
            products = products == null ? new Products(null, null, null, null, null, null, null) : products;
            foreignTradeCountries = immutable(foreignTradeCountries);
            counterparties = immutable(counterparties);
            groupMemberOkveds = immutable(groupMemberOkveds);
            userProvided = userProvided == null ? Map.of() : Map.copyOf(userProvided);
            missingFields = immutable(missingFields);
        }
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SourceRef(String sourceId, SourceKind kind, String title, LocalDate date,
                            Integer page, String url) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record NewsEvent(String eventId, String title, String text, Instant publishedAt,
                            SourceRef source, List<SourceRef> relatedSources, List<String> topics) {
        public NewsEvent {
            relatedSources = immutable(relatedSources);
            topics = immutable(topics);
        }
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record InsightRequest(CompanyProfile profile, UserRole role, NewsEvent event) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record QuestionRequest(String question) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Claim(String text, ClaimKind kind, List<String> sourceIds, List<String> profileFeatures) {
        public Claim {
            sourceIds = immutable(sourceIds);
            profileFeatures = immutable(profileFeatures);
        }
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Impact(String text, ClaimKind kind, List<String> sourceIds, List<String> profileFeatures,
                         Level level, String horizon) {
        public Impact {
            sourceIds = immutable(sourceIds);
            profileFeatures = immutable(profileFeatures);
        }
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Scenario(String name, String trigger, String implication, Level plausibility,
                           List<String> sourceIds) {
        public Scenario {
            sourceIds = immutable(sourceIds);
        }
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record CheckIssue(String code, String severity, String message, String location) {
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record SelfCheckReport(boolean passed, List<CheckIssue> issues, Integer iterations,
                                  List<String> adaptations) {
        public SelfCheckReport {
            issues = immutable(issues);
            iterations = iterations == null ? 1 : iterations;
            adaptations = immutable(adaptations);
        }
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Insight(String insightId, String runId, String eventId, String companyId, UserRole role,
                          InsightStatus status, String headline, List<Claim> whatHappened,
                          List<Claim> whyItMatters, List<Claim> consequences, List<Impact> risks,
                          List<Impact> opportunities, List<Scenario> scenarios, List<Claim> actions,
                          List<Claim> bankView, List<String> modelVsBank, Level impactLevel,
                          String impactRationale, List<String> profileFeaturesUsed, List<SourceRef> sources,
                          List<String> missingData, SelfCheckReport selfCheck, List<String> models,
                          Instant generatedAt, String disclaimer) {
        public Insight {
            whatHappened = immutable(whatHappened);
            whyItMatters = immutable(whyItMatters);
            consequences = immutable(consequences);
            risks = immutable(risks);
            opportunities = immutable(opportunities);
            scenarios = immutable(scenarios);
            actions = immutable(actions);
            bankView = immutable(bankView);
            modelVsBank = immutable(modelVsBank);
            profileFeaturesUsed = immutable(profileFeaturesUsed);
            sources = immutable(sources);
            missingData = immutable(missingData);
            models = immutable(models);
            disclaimer = disclaimer == null ? DISCLAIMER : disclaimer;
        }
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record Answer(String insightId, String runId, String status, String text, List<Claim> claims,
                         List<SourceRef> sources, List<String> missingData, String disclaimer) {
        public Answer {
            claims = immutable(claims);
            sources = immutable(sources);
            missingData = immutable(missingData);
            disclaimer = disclaimer == null ? DISCLAIMER : disclaimer;
        }
    }

    @JsonNaming(PropertyNamingStrategies.SnakeCaseStrategy.class)
    @JsonIgnoreProperties(ignoreUnknown = true)
    public record TraceEvent(String runId, int seq, Instant ts, String type, String step,
                             Map<String, Object> data) {
        public TraceEvent {
            data = data == null ? Map.of() : java.util.Collections.unmodifiableMap(new java.util.LinkedHashMap<>(data));
        }
    }

    private static <T> List<T> immutable(List<T> values) {
        return values == null ? List.of() : List.copyOf(values);
    }
}
