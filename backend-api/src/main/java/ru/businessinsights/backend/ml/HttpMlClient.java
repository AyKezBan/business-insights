package ru.businessinsights.backend.ml;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import org.springframework.stereotype.Component;
import ru.businessinsights.backend.config.BackendProperties;

import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.net.http.HttpTimeoutException;
import java.nio.ByteBuffer;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.List;
import java.util.Set;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.ExecutionException;
import java.util.concurrent.Flow;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.TimeoutException;
import java.util.regex.Matcher;
import java.util.regex.Pattern;

import static ru.businessinsights.backend.ml.MlContracts.*;
import static ru.businessinsights.backend.ml.MlException.Code.*;

/**
 * A single bounded call per user operation; generating an insight is never automatically retried.
 */
@Component
public final class HttpMlClient implements MlClient {
    private static final Pattern COUNTERPARTY_FEATURE = Pattern.compile("counterparties\\[(\\d+)]");
    private static final Set<String> PROFILE_SIGNALS = Set.of("okved_code", "group_revenue_rub", "revenue_rub",
            "foreign_trade", "group_member_okveds", "region");
    private static final Set<String> PRODUCT_FIELDS = Set.of("settlement", "guarantees", "loans", "leasing", "deposits", "payroll", "factoring");

    private final ObjectMapper mapper;
    private final PinnedSchemaValidator schemas;
    private final HttpClient client;
    private final String baseUrl;
    private final Duration requestTimeout;
    private final int maxResponseBytes;

    public HttpMlClient(ObjectMapper mapper, BackendProperties properties) {
        this.mapper = mapper.copy().disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
                .enable(DeserializationFeature.FAIL_ON_TRAILING_TOKENS)
                .disable(DeserializationFeature.ACCEPT_FLOAT_AS_INT);
        this.schemas = new PinnedSchemaValidator(this.mapper);
        var settings = properties.ml();
        URI base = settings.baseUrl();
        if (base == null || !("http".equals(base.getScheme()) || "https".equals(base.getScheme()))
                || base.getHost() == null || base.getUserInfo() != null || base.getQuery() != null || base.getFragment() != null) {
            throw new IllegalArgumentException("backend.ml.base-url must be an absolute HTTP(S) URL without credentials, query or fragment");
        }
        baseUrl = base.toString().replaceAll("/+$", "");
        requestTimeout = positive(settings.requestTimeout(), "backend.ml.request-timeout");
        maxResponseBytes = settings.maxResponseBytes();
        if (maxResponseBytes < 1) {
            throw new IllegalArgumentException("backend.ml.max-response-bytes must be positive");
        }
        client = HttpClient.newBuilder().connectTimeout(positive(settings.connectTimeout(), "backend.ml.connect-timeout"))
                .followRedirects(HttpClient.Redirect.NEVER).build();
    }

    @Override
    public Insight generate(InsightRequest request) {
        if (request == null || request.profile() == null || request.role() == null || request.event() == null) {
            throw new MlException(REQUEST_REJECTED, "ML request must contain profile, explicit role and event", false);
        }
        JsonNode body = toTree(request);
        validateRequest("insight-request", body);
        JsonNode response = exchange("POST", "/v1/insights", body, null);
        validateResponse("insight", response);
        if (!request.profile().companyId().equals(response.path("company_id").asText())
                || !request.event().eventId().equals(response.path("event_id").asText())
                || !request.role().name().equals(response.path("role").asText())) {
            throw invalid("ML insight identity differs from the request");
        }
        for (String field : List.of("corpus_revision", "event_content_sha256", "request_id", "parent_run_id")) {
            if (body.hasNonNull(field) && !body.get(field).equals(response.get(field)))
                throw invalid("ML insight " + field + " differs from the pinned request");
        }
        if (request.asOf() != null && !request.asOf().equals(mapper.convertValue(response.get("as_of"), java.time.Instant.class)))
            throw invalid("ML insight cutoff differs from the pinned request");
        requireText(response, "insight_id");
        requireText(response, "run_id");
        requireText(response, "headline");
        validateReferences(response, body.path("profile"));
        return convert(response, Insight.class);
    }

    @Override
    public Answer ask(String insightId, String question, CompanyProfile profile) {
        if (insightId == null || insightId.isBlank()) {
            throw new MlException(REQUEST_REJECTED, "Insight ID is required", false);
        }
        if (profile == null) {
            throw new MlException(REQUEST_REJECTED, "Saved company profile is required for ML question validation", false);
        }
        JsonNode body = toTree(new QuestionRequest(question));
        validateRequest("question-request", body);
        JsonNode response = exchange("POST", "/v1/insights/" + segment(insightId) + "/questions", body, CONTEXT_EXPIRED);
        validateResponse("answer", response);
        if (!insightId.equals(response.path("insight_id").asText())) {
            throw invalid("ML answer belongs to another insight");
        }
        requireText(response, "run_id");
        requireText(response, "status");
        requireText(response, "text");
        String status = response.get("status").asText();
        if (!("answered".equals(status) || "no_answer".equals(status))) {
            throw invalid("Unknown ML answer status");
        }
        validateReferences(response, toTree(profile));
        return convert(response, Answer.class);
    }

    @Override
    public List<TraceEvent> trace(String runId) {
        if (runId == null || !runId.matches("run_[A-Za-z0-9]+")) {
            throw new MlException(REQUEST_REJECTED, "Invalid ML run ID", false);
        }
        JsonNode response = exchange("GET", "/v1/runs/" + segment(runId) + "/trace", null, TRACE_NOT_FOUND);
        if (!response.isArray()) {
            throw invalid("ML trace must be an array");
        }
        List<TraceEvent> result = new ArrayList<>(response.size());
        int previous = 0;
        for (JsonNode event : response) {
            validateResponse("trace-event", event);
            if (!runId.equals(event.path("run_id").asText()) || !event.path("seq").canConvertToInt()
                    || event.path("seq").intValue() <= previous) {
                throw invalid("ML trace has inconsistent run ID or sequence");
            }
            previous = event.get("seq").intValue();
            result.add(convert(event, TraceEvent.class));
        }
        return List.copyOf(result);
    }

    JsonNode getJson(String path) {
        return exchange("GET", path, null, null);
    }

    private JsonNode exchange(String method, String path, JsonNode payload, MlException.Code notFound) {
        HttpRequest.Builder builder = HttpRequest.newBuilder(URI.create(baseUrl + path))
                .timeout(requestTimeout).header("Accept", "application/json");
        CompletableFuture<HttpResponse<byte[]>> pending = null;
        try {
            if (payload == null) {
                builder.GET();
            } else {
                builder.header("Content-Type", "application/json").method(method,
                        HttpRequest.BodyPublishers.ofByteArray(mapper.writeValueAsBytes(payload)));
            }
            pending = client.sendAsync(builder.build(), ignored -> new BoundedBodySubscriber(maxResponseBytes));
            // The future completes after the bounded subscriber receives the entire body.
            // Keep this independent deadline as well as HttpRequest's transport timeout.
            HttpResponse<byte[]> response = pending.get(Math.max(1, requestTimeout.toMillis()), TimeUnit.MILLISECONDS);
            int status = response.statusCode();
            String contentType = response.headers().firstValue("Content-Type").orElse("").split(";", 2)[0].trim().toLowerCase(java.util.Locale.ROOT);
            if (status == 409 || status == 503) {
                var corpusFailure = corpusFailure(status, contentType, response.body());
                if (corpusFailure != null) throw corpusFailure;
            }
            if (status == 404 && notFound != null) {
                throw new MlException(notFound, notFound == CONTEXT_EXPIRED
                        ? "ML insight context is missing or expired" : "ML trace is not available", false);
            }
            if (status == 408 || status == 504) {
                throw new MlException(TIMEOUT, "ML request timed out", true);
            }
            if (status == 429) {
                throw new MlException(RATE_LIMITED, "ML request limit reached", true);
            }
            if (status >= 500) {
                throw new MlException(UNAVAILABLE, "ML service is unavailable", true);
            }
            if (status >= 400) {
                throw new MlException(REQUEST_REJECTED, "ML service rejected the request (HTTP " + status + ")", false);
            }
            if (status < 200 || status >= 300) {
                throw invalid("Unexpected ML HTTP status " + status);
            }
            if (!("application/json".equals(contentType) || contentType.endsWith("+json"))) {
                throw invalid("ML response must use a JSON content type");
            }
            try {
                JsonNode tree = mapper.readTree(response.body());
                if (tree == null || tree.isNull()) {
                    throw invalid("ML response is empty");
                }
                return tree;
            } catch (IOException ex) {
                throw new MlException(INVALID_RESPONSE, "ML response is not valid JSON", false, ex);
            }
        } catch (TimeoutException ex) {
            if (pending != null) {
                pending.cancel(true);
            }
            throw new MlException(TIMEOUT, "ML request timed out; completion is unknown", true, ex);
        } catch (InterruptedException ex) {
            if (pending != null) {
                pending.cancel(true);
            }
            Thread.currentThread().interrupt();
            throw new MlException(INTERRUPTED, "ML request interrupted; completion is unknown", true, ex);
        } catch (ExecutionException ex) {
            throw transportFailure(ex.getCause());
        } catch (IOException ex) {
            throw transportFailure(ex);
        }
    }

    private MlException corpusFailure(int status, String contentType, byte[] body) {
        if (!("application/json".equals(contentType) || contentType.endsWith("+json"))) return null;
        try {
            JsonNode root = mapper.readTree(body);
            JsonNode code = root == null ? null : root.path("detail").path("code");
            if (code == null || !code.isTextual()) return null;
            // The body is already size-bounded. Read only the allowlisted code, never the upstream message.
            return switch (code.textValue()) {
                case "corpus_event_mismatch" -> status == 409
                        ? new MlException(CORPUS_EVENT_MISMATCH, "Событие не соответствует выбранной версии материалов.", false, status) : null;
                case "corpus_unavailable" -> new MlException(CORPUS_UNAVAILABLE,
                        status == 409 ? "Исходная версия материалов недоступна." : "Материалы временно недоступны.", status == 503, status);
                case "catalog_uninitialized" -> status == 503
                        ? new MlException(CATALOG_UNINITIALIZED, "Каталог материалов ещё не опубликован.", true, status) : null;
                case "catalog_cursor_mismatch" -> status == 409
                        ? new MlException(CATALOG_CURSOR_MISMATCH, "Страница каталога относится к другой публикации. Обновите список материалов.", false, status) : null;
                default -> null;
            };
        } catch (IOException malformed) {
            return null;
        }
    }

    private static MlException transportFailure(Throwable failure) {
        for (Throwable cause = failure; cause != null; cause = cause.getCause()) {
            if (cause instanceof ResponseTooLargeException) {
                return new MlException(INVALID_RESPONSE, "ML response exceeds configured size limit", false);
            }
            if (cause instanceof HttpTimeoutException) {
                return new MlException(TIMEOUT, "ML request timed out; completion is unknown", true, failure);
            }
        }
        return new MlException(UNAVAILABLE, "Cannot communicate with ML service; completion is unknown", true, failure);
    }

    private JsonNode toTree(Object value) {
        try {
            return mapper.valueToTree(value);
        } catch (IllegalArgumentException ex) {
            throw new MlException(REQUEST_REJECTED, "Cannot serialize the ML request", false, ex);
        }
    }

    private void validateRequest(String schema, JsonNode value) {
        try {
            schemas.validate(schema, value);
        } catch (IllegalArgumentException ex) {
            throw new MlException(REQUEST_REJECTED, ex.getMessage(), false);
        }
    }

    private void validateResponse(String schema, JsonNode value) {
        try {
            schemas.validate(schema, value);
        } catch (IllegalArgumentException ex) {
            throw new MlException(INVALID_RESPONSE, ex.getMessage(), false);
        }
    }

    private <T> T convert(JsonNode value, Class<T> type) {
        try {
            return mapper.treeToValue(value, type);
        } catch (JsonProcessingException | IllegalArgumentException ex) {
            throw new MlException(INVALID_RESPONSE, "ML response cannot be decoded using the pinned contract", false, ex);
        }
    }

    private void validateReferences(JsonNode result, JsonNode profile) {
        Set<String> sourceIds = new HashSet<>();
        var sources = new java.util.HashMap<String, JsonNode>();
        for (JsonNode source : result.path("sources")) {
            String id = source.path("source_id").asText();
            if (id.isBlank() || !sourceIds.add(id)) {
                throw invalid("ML response has empty or duplicate source IDs");
            }
            sources.put(id, source);
        }
        for (JsonNode conflict : result.path("conflicts")) {
            for (JsonNode ref : conflict.path("source_ids")) {
                if (!sourceIds.contains(ref.asText())) throw invalid("ML conflict references an absent source");
            }
        }
        for (String section : List.of("what_happened", "why_it_matters", "consequences", "risks", "opportunities", "actions", "bank_view", "claims", "scenarios")) {
            for (JsonNode claim : result.path(section)) {
                Set<String> cited = new HashSet<>();
                for (JsonNode ref : claim.path("source_ids")) {
                    if (!sourceIds.contains(ref.asText())) {
                        throw invalid("ML assertion references an absent source");
                    }
                    cited.add(ref.asText());
                }
                for (JsonNode span : claim.path("evidence_spans")) {
                    JsonNode source = sources.get(span.path("source_id").asText());
                    if (source == null || !cited.contains(span.path("source_id").asText()))
                        throw invalid("ML evidence span references an uncited source");
                    if (source.hasNonNull("content_sha256") && !source.get("content_sha256").equals(span.get("content_sha256")))
                        throw invalid("ML evidence span differs from its source version");
                }
                if (profile != null) {
                    for (JsonNode feature : claim.path("profile_features")) {
                        if (!knownFeature(profile, feature.asText())) {
                            throw invalid("ML assertion references an unavailable profile feature");
                        }
                    }
                }
                String kind = claim.path("kind").asText();
                boolean hasSources = !claim.path("source_ids").isEmpty();
                boolean hasFeatures = !claim.path("profile_features").isEmpty();
                if (("fact".equals(kind) || "assessment".equals(kind)) && !hasSources) {
                    throw invalid("ML fact or bank assessment has no source");
                }
                if (("inference".equals(kind) || "recommendation".equals(kind)) && !hasSources && !hasFeatures) {
                    throw invalid("ML conclusion or recommendation has no stated basis");
                }
                if ("bank_view".equals(section) && !("assessment".equals(kind) || "unavailable".equals(kind))) {
                    throw invalid("ML bank view contains a claim that is not a bank assessment or unavailable marker");
                }
                if ("assessment".equals(claim.path("kind").asText())) {
                    if (claim.path("source_ids").isEmpty()) {
                        throw invalid("ML bank assessment has no bank source");
                    }
                    for (JsonNode ref : claim.path("source_ids")) {
                        boolean bank = false;
                        for (JsonNode source : result.path("sources")) {
                            if (ref.asText().equals(source.path("source_id").asText()) && "bank".equals(source.path("kind").asText())) {
                                bank = true;
                            }
                        }
                        if (!bank) {
                            throw invalid("ML bank assessment cites a non-bank source");
                        }
                    }
                }
            }
        }
        if (profile != null) {
            for (JsonNode feature : result.path("profile_features_used")) {
                if (!knownFeature(profile, feature.asText())) {
                    throw invalid("ML response lists an unavailable profile feature");
                }
            }
        }
    }

    private boolean knownFeature(JsonNode profile, String feature) {
        Matcher cp = COUNTERPARTY_FEATURE.matcher(feature);
        if (cp.matches()) {
            try {
                return Integer.parseInt(cp.group(1)) < profile.path("counterparties").size();
            } catch (NumberFormatException ignored) {
                return false;
            }
        }
        JsonNode value;
        if (feature.startsWith("products.") && PRODUCT_FIELDS.contains(feature.substring("products.".length()))) {
            value = profile.path("products").path(feature.substring("products.".length()));
            return value.isBoolean() && value.booleanValue();
        }
        if (feature.startsWith("user_provided.")) {
            value = profile.path("user_provided").path(feature.substring("user_provided.".length()));
        } else if (PROFILE_SIGNALS.contains(feature)) {
            value = profile.path(feature);
        } else {
            return false;
        }
        return !value.isMissingNode() && !value.isNull() && !(value.isTextual() && value.textValue().isBlank())
                && !(value.isArray() && value.isEmpty()) && !(value.isBoolean() && !value.booleanValue());
    }

    private static void requireText(JsonNode result, String field) {
        if (result.path(field).asText().isBlank()) {
            throw invalid("ML response has an empty " + field);
        }
    }

    private static MlException invalid(String message) {
        return new MlException(INVALID_RESPONSE, message, false);
    }

    private static Duration positive(Duration duration, String property) {
        if (duration == null || duration.isZero() || duration.isNegative()) {
            throw new IllegalArgumentException(property + " must be positive");
        }
        return duration;
    }

    static String segment(String value) {
        StringBuilder encoded = new StringBuilder();
        for (byte b : value.getBytes(StandardCharsets.UTF_8)) {
            int c = b & 0xff;
            if (c >= 'a' && c <= 'z' || c >= 'A' && c <= 'Z' || c >= '0' && c <= '9' || c == '-' || c == '_' || c == '.' || c == '~') {
                encoded.append((char) c);
            } else {
                encoded.append('%').append("0123456789ABCDEF".charAt(c >> 4)).append("0123456789ABCDEF".charAt(c & 15));
            }
        }
        return encoded.toString();
    }

    private static final class ResponseTooLargeException extends IOException {
    }

    /**
     * Limits buffering; getBody completes only after the complete response body arrives.
     */
    private static final class BoundedBodySubscriber implements HttpResponse.BodySubscriber<byte[]> {
        private final int limit;
        private final ByteArrayOutputStream bytes = new ByteArrayOutputStream();
        private final CompletableFuture<byte[]> body = new CompletableFuture<>();
        private Flow.Subscription subscription;

        private BoundedBodySubscriber(int limit) {
            this.limit = limit;
        }

        @Override
        public CompletionStage<byte[]> getBody() {
            return body;
        }

        @Override
        public void onSubscribe(Flow.Subscription subscription) {
            if (this.subscription != null) {
                subscription.cancel();
                return;
            }
            this.subscription = subscription;
            subscription.request(1);
        }

        @Override
        public void onNext(List<ByteBuffer> buffers) {
            for (ByteBuffer buffer : buffers) {
                if (buffer.remaining() > limit - bytes.size()) {
                    subscription.cancel();
                    body.completeExceptionally(new ResponseTooLargeException());
                    return;
                }
                byte[] chunk = new byte[buffer.remaining()];
                buffer.get(chunk);
                bytes.writeBytes(chunk);
            }
            subscription.request(1);
        }

        @Override
        public void onError(Throwable throwable) {
            body.completeExceptionally(throwable);
        }

        @Override
        public void onComplete() {
            body.complete(bytes.toByteArray());
        }
    }
}
