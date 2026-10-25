package ru.businessinsights.backend.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.List;
import java.util.Map;

import org.springframework.stereotype.Component;
import ru.businessinsights.backend.api.ApiModels.*;
import ru.businessinsights.backend.store.OperationStore.Operation;

/**
 * Presentation labels are based on claim kind AND evidence; bank references do not turn AI into bank opinion.
 */
@Component
public class InsightMapper {
    public static final String DISCLAIMER = "Аналитика носит информационный характер и не является инвестиционной рекомендацией. Выводы модели и гипотезы сформированы AI и не являются официальной позицией Альфа-Банка.";
    private final ObjectMapper mapper;

    public InsightMapper(ObjectMapper mapper) {
        this.mapper = mapper;
    }

    public OperationView operation(Operation op) {
        FailureView error = op.errorCode() == null ? null : new FailureView(op.errorCode(), op.errorMessage(), op.retryable());
        if (error == null && "FAILED".equals(op.status())) {
            error = new FailureView(op.result() != null && text(op.result(), "error_code") != null ? text(op.result(), "error_code") : "ML_ANALYSIS_FAILED", "ML завершил анализ без пригодного результата", false);
        }
        boolean published = "SUCCEEDED".equals(op.status()) || "LIMITED".equals(op.status());
        return new OperationView(op.id(), op.kind(), op.status(), op.createdAt(), op.updatedAt(), error, op.warnings(),
                "ANALYSIS".equals(op.kind()) && published && op.result() != null ? op.id() : null, op.parentId(),
                op.result() == null ? null : text(op.result(), "outcome"), context(op, "request_id"), context(op, "corpus_revision"));
    }

    public InsightView insight(Operation op, ru.businessinsights.backend.store.OperationStore.Interaction interaction) {
        JsonNode r = op.result();
        Map<String, String> sources = sourceKinds(r);
        return new InsightView(op.id(), op.companyId(), Role.valueOf(op.role()), op.eventId(), op.status(), text(r, "status"), text(r, "headline"),
                claims(r, "what_happened", sources), claims(r, "why_it_matters", sources), claims(r, "consequences", sources),
                claims(r, "risks", sources), claims(r, "opportunities", sources), scenarios(r), claims(r, "actions", sources),
                claims(r, "bank_view", sources), strings(r.path("model_vs_bank")), text(r, "impact_level"), text(r, "impact_rationale"),
                strings(r.path("profile_features_used")), sources(r), strings(r.path("missing_data")),
                new SelfCheckView(r.path("self_check").path("passed").asBoolean(), r.path("self_check").path("issues"),
                        r.path("self_check").path("iterations").asInt(1), strings(r.path("self_check").path("adaptations"))),
                strings(r.path("models")), mapper.convertValue(r.get("generated_at"), java.time.Instant.class), DISCLAIMER, op.warnings(), interaction.hidden(),
                text(r, "insight_id"), text(r, "run_id"), r.path("version").asInt(1), text(r, "outcome"),
                text(r, "error_code"), text(r, "request_id"), text(r, "parent_run_id"), r.path("cache_hit").asBoolean(),
                mapper.convertValue(r.get("as_of"), java.time.Instant.class), text(r, "corpus_revision"), text(r, "event_content_sha256"),
                conflicts(r), interaction.openedAt(), interaction.feedback());
    }

    public AnswerView answer(Operation op) {
        JsonNode r = op.result();
        return new AnswerView(op.id(), op.parentId(), op.status(), text(r, "status"), text(r, "text"),
                claims(r, "claims", sourceKinds(r)), sources(r), strings(r.path("missing_data")), DISCLAIMER, op.warnings(),
                text(r, "insight_id"), text(r, "run_id"), text(r, "company_id"), text(r, "event_id"),
                mapper.convertValue(r.get("as_of"), java.time.Instant.class), text(r, "corpus_revision"), text(r, "event_content_sha256"));
    }

    private List<ClaimView> claims(JsonNode r, String section, Map<String, String> sources) {
        var result = new ArrayList<ClaimView>();
        for (JsonNode c : r.path(section)) {
            String kind = text(c, "kind");
            List<String> ids = strings(c.path("source_ids"));
            boolean onlyBank = !ids.isEmpty() && ids.stream().allMatch(id -> "bank".equals(sources.get(id)));
            String origin = switch (kind) {
                case "fact" -> "FACT";
                case "assessment" -> onlyBank ? "BANK_ANALYSIS" : "EXTERNAL_ASSESSMENT";
                case "hypothesis" -> "HYPOTHESIS";
                case "recommendation" -> "MODEL_RECOMMENDATION";
                case "unavailable" -> "UNAVAILABLE";
                default -> "MODEL_INFERENCE";
            };
            result.add(new ClaimView(text(c, "text"), kind, origin, ids, strings(c.path("profile_features")), text(c, "level"), text(c, "horizon"), text(c, "claim_id")));
        }
        return result;
    }

    private List<SourceView> sources(JsonNode r) {
        var result = new ArrayList<SourceView>();
        for (JsonNode s : r.path("sources")) {
            result.add(new SourceView(text(s, "source_id"), text(s, "kind"), text(s, "title"),
                    mapper.convertValue(s.get("date"), java.time.LocalDate.class), s.path("page").isIntegralNumber() ? s.path("page").intValue() : null, safeUrl(text(s, "url")),
                    text(s, "doc_id"), text(s, "content_sha256"), text(s, "excerpt"), text(s, "publisher_id"), text(s, "adapter_id"),
                    text(s, "material_type"), text(s, "completeness"), text(s, "language"),
                    mapper.convertValue(s.get("published_date"), java.time.LocalDate.class), text(s, "published_precision"),
                    mapper.convertValue(s.get("first_seen_at"), java.time.Instant.class), mapper.convertValue(s.get("retrieved_at"), java.time.Instant.class),
                    text(s, "statement_type"), text(s, "target_period"), mapper.convertValue(s.get("valid_until"), java.time.LocalDate.class),
                    safeUrl(text(s, "rights_url")), text(s, "translation_attribution"), text(s, "raw_publication"), text(s, "source_timezone"),
                    s.path("source_utc_offset_seconds").isIntegralNumber() ? s.path("source_utc_offset_seconds").intValue() : null, text(s, "timezone_provenance")));
        }
        return result;
    }

    private List<ConflictView> conflicts(JsonNode r) {
        var conflicts = new ArrayList<ConflictView>();
        for (JsonNode c : r.path("conflicts")) conflicts.add(new ConflictView(text(c, "reason"), strings(c.path("source_ids")), c.path("resolved").asBoolean()));
        return conflicts;
    }

    private Map<String, String> sourceKinds(JsonNode r) {
        var result = new HashMap<String, String>();
        for (JsonNode s : r.path("sources")) {
            result.put(text(s, "source_id"), text(s, "kind"));
        }
        return result;
    }

    private List<ScenarioView> scenarios(JsonNode r) {
        var result = new ArrayList<ScenarioView>();
        for (JsonNode s : r.path("scenarios")) {
            result.add(new ScenarioView(text(s, "name"), text(s, "trigger"), text(s, "implication"), text(s, "plausibility"), strings(s.path("source_ids"))));
        }
        return result;
    }

    private static String safeUrl(String raw) {
        if (raw == null) {
            return null;
        }
        try {
            var uri = java.net.URI.create(raw);
            return uri.getHost() != null && ("https".equals(uri.getScheme()) || "http".equals(uri.getScheme())) && uri.getUserInfo() == null ? raw : null;
        } catch (IllegalArgumentException e) {
            return null;
        }
    }

    private static String context(Operation op, String field) {
        String result = op.result() == null ? null : text(op.result(), field);
        return result == null && op.payload() != null ? text(op.payload().path("request"), field) : result;
    }

    private static String text(JsonNode node, String field) {
        JsonNode value = node.get(field);
        return value == null || value.isNull() ? null : value.asText();
    }

    private static List<String> strings(JsonNode node) {
        var list = new ArrayList<String>();
        for (JsonNode s : node) {
            list.add(s.asText());
        }
        return list;
    }
}
