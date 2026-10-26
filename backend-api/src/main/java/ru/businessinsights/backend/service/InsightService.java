package ru.businessinsights.backend.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.node.ObjectNode;
import jakarta.annotation.PreDestroy;

import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.util.ArrayList;
import java.util.HexFormat;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.time.Instant;
import java.util.Comparator;
import java.util.concurrent.ArrayBlockingQueue;
import java.util.concurrent.ThreadPoolExecutor;
import java.util.concurrent.TimeUnit;

import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.boot.context.event.ApplicationReadyEvent;
import org.springframework.context.event.EventListener;
import org.springframework.http.HttpStatus;
import org.springframework.stereotype.Service;
import ru.businessinsights.backend.api.ApiException;
import ru.businessinsights.backend.api.ApiModels.*;
import ru.businessinsights.backend.config.BackendProperties;
import ru.businessinsights.backend.data.CompanyProfileService;
import ru.businessinsights.backend.ml.MlClient;
import ru.businessinsights.backend.ml.MlContracts;
import ru.businessinsights.backend.ml.MlException;
import ru.businessinsights.backend.store.OperationStore;
import ru.businessinsights.backend.store.OperationStore.Operation;

@Service
public class InsightService {
    private static final Logger LOG = LoggerFactory.getLogger(InsightService.class);
    private static final String DATA_COMMIT = "f44b69f0d124e8e4c33e5301b629413618090fa5";
    private final CompanyProfileService profiles;
    private final MlClient ml;
    private final OperationStore store;
    private final InsightMapper views;
    private final ObjectMapper mapper;
    private final BackendProperties properties;
    private final EventRelevance relevance;
    private final jakarta.validation.Validator validator;
    private final ThreadPoolExecutor executor;
    private volatile boolean ready;

    public InsightService(CompanyProfileService profiles, MlClient ml, OperationStore store, InsightMapper views,
                          ObjectMapper mapper, BackendProperties properties, EventRelevance relevance, jakarta.validation.Validator validator) {
        this.profiles = profiles;
        this.ml = ml;
        this.store = store;
        this.views = views;
        this.mapper = mapper;
        this.properties = properties;
        this.relevance = relevance;
        this.validator = validator;
        var config = properties.jobs();
        this.executor = new ThreadPoolExecutor(config.workers(), config.workers(), 0, TimeUnit.MILLISECONDS,
                new ArrayBlockingQueue<>(config.queueCapacity()), Thread.ofPlatform().name("insight-job-", 0).factory(), new ThreadPoolExecutor.AbortPolicy());
    }

    @EventListener(ApplicationReadyEvent.class)
    public void ready() {
        store.interruptUnfinished();
        ready = true;
    }

    @PreDestroy
    public void close() throws InterruptedException {
        ready = false;
        executor.shutdown();
        if (!executor.awaitTermination(properties.jobs().shutdownWait().toMillis(), TimeUnit.MILLISECONDS)) {
            executor.shutdownNow();
        }
    }

    public List<CompanyView> companies(String person, int limit, int offset) {
        return profiles.listCompanies(person, limit, offset).stream()
                .map(c -> new CompanyView(c.companyId(), c.profileStatus(), c.clientType(), c.okvedName(), c.city(), c.region())).toList();
    }

    public ProfileView profile(String person, String company) {
        requireAccess(person, company);
        var snapshot = profiles.loadProfile(company, Map.of());
        var p = snapshot.profile();
        var facts = new CompanyFacts(p.companyId(), p.groupId(), p.okvedCode(), p.okvedName(), p.city(), p.region(),
                p.revenueRub(), 2025, "RUB", null, p.clientType(), p.employees(), p.foreignTrade(), p.foreignTradeCountries(), p.selfTransferShare());
        return new ProfileView(company, facts, snapshot.provenance(), snapshot.qualityIssues(), snapshot.sufficient());
    }

    public EventInput event(EventInput input) {
        // Treat source URLs as references: this service never follows caller-supplied URLs.
        var normalized = validateEvent(input);
        return store.registerEvent(normalized, hash(store.tree(normalized)));
    }

    public EventVersionView currentEvent(String id) {
        return store.currentEvent(id);
    }

    public List<EventVersionView> eventHistory(String id, int limit, int offset) {
        return store.eventHistory(id, limit, offset);
    }

    public EventVersionView reviseEvent(String id, EventRevisionInput input) {
        if (!id.equals(input.event().eventId())) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "EVENT_ID_MISMATCH", "eventId должен совпадать с адресом события");
        }
        EventInput event = validateEvent(input.event());
        return store.reviseEvent(event, hash(store.tree(event)), input.expectedVersion());
    }

    private EventInput validateEvent(EventInput normalized) {
        if (!validator.validate(normalized).isEmpty()) {
            throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_EVENT", "Событие не соответствует ограничениям API");
        }
        var ids = new java.util.HashSet<String>();
        ids.add(normalized.source().sourceId());
        for (var s : normalized.relatedSources()) {
            if (!ids.add(s.sourceId())) {
                throw new ApiException(HttpStatus.BAD_REQUEST, "DUPLICATE_SOURCE_ID", "Идентификаторы источников события должны различаться");
            }
        }
        for (var conflict : normalized.conflicts()) {
            if (!ids.containsAll(conflict.sourceIds()) || new java.util.HashSet<>(conflict.sourceIds()).size() != conflict.sourceIds().size()) {
                throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_CONFLICT_SOURCES", "Конфликт должен ссылаться на различные источники этого события");
            }
        }
        return normalized;
    }

    public OperationView analyze(String person, AnalysisInput input, String key) {
        requireReady();
        requireAccess(person, input.companyId());
        var event = store.currentEvent(input.eventId());
        var normalized = new AnalysisInput(input.companyId(), input.role(), input.eventId(), input.userProvided() == null ? Map.of() : input.userProvided());
        var selection = new EventRelevance.Selection(0, List.of("Явно выбранное событие"), relevance.expiresAt(event), null);
        return submitAnalysis(person, normalized, event, selection, key);
    }

    private OperationView submitAnalysis(String person, AnalysisInput input, EventVersionView event, EventRelevance.Selection selection, String key) {
        var submission = store.create(person, input.companyId(), input.role().name(), "ANALYSIS", null, input.eventId(),
                event.version(), selection.score(), selection.reasons(), selection.expiresAt(), key, hash(store.tree(input)), input);
        if (submission.created()) {
            enqueue(submission.operation().id());
        }
        return operation(person, submission.operation().id());
    }

    public FeedRefreshView refresh(String person, String company, FeedRefreshInput input) {
        requireAvailable();
        requireAccess(person, company);
        Map<String, String> supplied = input.userProvided() == null ? Map.of() : input.userProvided();
        var snapshot = profiles.loadProfile(company, supplied);
        if (!snapshot.sufficient()) {
            throw new ApiException(HttpStatus.UNPROCESSABLE_ENTITY, "INSUFFICIENT_PROFILE", "Нет пригодных бизнес-признаков для отбора событий");
        }
        MlContracts.NewsPage page;
        try {
            page = ml.news(input.sourceLimit() == null ? 20 : input.sourceLimit(), input.sourceOffset() == null ? 0 : input.sourceOffset());
        } catch (MlException e) {
            throw new ApiException(HttpStatus.BAD_GATEWAY, "ML_" + e.code().name(), e.getMessage(), e.retryable());
        }
        record Candidate(EventVersionView event, EventRelevance.Selection selection) {
        }
        var candidates = new ArrayList<Candidate>();
        Instant now = Instant.now();
        var incomingEvents = new ArrayList<EventInput>();
        var eventIds = new java.util.HashSet<String>();
        for (var incoming : page.items()) {
            try {
                var event = validateEvent(apiEvent(incoming));
                if (!eventIds.add(event.eventId())) {
                    throw new ApiException(HttpStatus.BAD_REQUEST, "DUPLICATE_EVENT_ID", "Корпус повторяет идентификатор события");
                }
                incomingEvents.add(event);
            } catch (ApiException e) {
                throw new ApiException(HttpStatus.BAD_GATEWAY, "INVALID_CORPUS_EVENT", e.getMessage());
            }
        }
        for (var event : incomingEvents) {
            var version = store.synchronizeEvent(event, hash(store.tree(event)));
            candidates.add(new Candidate(version, relevance.evaluate(snapshot.profile(), input.role(), version, now)));
        }
        candidates.sort(Comparator.comparingInt((Candidate c) -> c.selection().score()).reversed()
                .thenComparing(c -> c.event().eventId()));
        int remaining = input.maxAnalyses() == null ? 8 : input.maxAnalyses();
        var results = new ArrayList<FeedCandidateView>();
        for (var candidate : candidates) {
            var event = candidate.event();
            var selection = candidate.selection();
            String decision = selection.exclusion();
            OperationView operation = null;
            if (decision == null) {
                // Stable across refresh retries; changed profile, role or event revision gets a new key.
                var identity = store.tree(snapshot.profile()).deepCopy();
                ((ObjectNode) identity).put("eventId", event.eventId()).put("eventVersion", event.version()).put("role", input.role().name());
                String key = "feed-" + hash(identity);
                var existing = store.byKey(person, "ANALYSIS", key);
                if (existing.isPresent()) {
                    operation = views.operation(existing.get());
                    decision = "EXISTING_OPERATION";
                } else if (remaining <= 0) {
                    decision = "DEFERRED_LIMIT";
                } else {
                    try {
                        requireReady();
                        operation = submitAnalysis(person, new AnalysisInput(company, input.role(), event.eventId(), supplied), event, selection, key);
                        decision = "ANALYSIS_OPERATION";
                        remaining--;
                    } catch (ApiException e) {
                        if (!"QUEUE_FULL".equals(e.code())) {
                            throw e;
                        }
                        decision = "DEFERRED_QUEUE_FULL";
                    }
                }
            }
            results.add(new FeedCandidateView(event.eventId(), event.version(), selection.score(), selection.reasons(), selection.expiresAt(), decision, operation));
        }
        return new FeedRefreshView(page.corpusRevision(), page.nextOffset(), List.copyOf(results));
    }

    public OperationView question(String person, UUID insightId, QuestionInput input, String key) {
        requireReady();
        Operation parent = requireInsight(person, insightId);
        // Include parent ID in the fingerprint; a key cannot silently move a question to another insight.
        var payload = mapper.createObjectNode().put("question", input.question()).put("parentId", parent.id().toString());
        var submission = store.create(person, parent.companyId(), parent.role(), "QUESTION", parent.id(), parent.eventId(),
                parent.eventVersion(), parent.relevanceScore(), parent.relevanceReasons(), parent.expiresAt(), key, hash(payload), payload);
        if (submission.created()) {
            enqueue(submission.operation().id());
        }
        return operation(person, submission.operation().id());
    }

    public OperationView operation(String person, UUID id) {
        return views.operation(owned(person, id));
    }

    public FeedView feed(String person, String company, Role role, int limit, int offset, boolean includeHidden) {
        requireAccess(person, company);
        List<InsightView> items = store.feed(person, company, role.name(), limit, offset, includeHidden).stream()
                .map(o -> views.insight(o, store.hidden(o.id(), person))).toList();
        return new FeedView(items, limit, offset, "relevanceScore DESC, impactLevel DESC, createdAt DESC, insightId DESC",
                items.isEmpty() ? "NO_MATCHING_INSIGHTS" : null);
    }

    public InsightView insight(String person, UUID id) {
        var op = requireInsight(person, id);
        return views.insight(op, store.hidden(id, person));
    }

    public AnswerView answer(String person, UUID id) {
        Operation op = owned(person, id);
        if (!"QUESTION".equals(op.kind())) {
            throw new ApiException(HttpStatus.NOT_FOUND, "ANSWER_NOT_FOUND", "Операция не является уточняющим вопросом");
        }
        if (op.result() == null) {
            throw new ApiException(HttpStatus.CONFLICT, "RESULT_NOT_READY", "Ответ отсутствует; проверьте состояние операции");
        }
        return views.answer(op);
    }

    public TraceView trace(String person, UUID id) {
        Operation op = owned(person, id);
        return new TraceView(id, "ANALYSIS".equals(op.kind()) ? id : op.parentId(), op.traceState(), op.trace(), op.profileSnapshot(), op.warnings());
    }

    public void state(String person, UUID id, StateInput input) {
        requireInsight(person, id);
        store.state(id, person, input.hidden());
    }

    public void open(String person, UUID id) {
        requireInsight(person, id);
        store.open(id, person);
    }

    public void feedback(String person, UUID id, FeedbackInput input) {
        requireInsight(person, id);
        store.feedback(id, person, input.rating().name(), input.comment());
    }

    public ActionView action(String person, UUID id, ActionInput input) {
        requireInsight(person, id);
        UUID actionId = store.action(id, person, input.action().name(), input.comment());
        return new ActionView(actionId, id, input.action().name(), "RECORDED", "LOCAL_ONLY_NO_CRM_DELIVERY");
    }

    private void requireReady() {
        requireAvailable();
        if (executor.getActiveCount() >= executor.getMaximumPoolSize() && executor.getQueue().remainingCapacity() == 0) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "QUEUE_FULL", "Очередь анализа заполнена", true);
        }
    }

    private void requireAvailable() {
        if (!ready) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "BACKEND_STARTING", "Сервис запускается или останавливается", true);
        }
    }

    private void requireAccess(String person, String company) {
        if (!profiles.hasAccess(person, company)) {
            throw new ApiException(HttpStatus.FORBIDDEN, "COMPANY_ACCESS_DENIED", "Компания недоступна этому пользователю");
        }
    }

    private Operation owned(String person, UUID id) {
        var op = store.owned(id, person).orElseThrow(() -> new ApiException(HttpStatus.NOT_FOUND, "OPERATION_NOT_FOUND", "Операция не найдена"));
        requireAccess(person, op.companyId());
        return op;
    }

    private Operation requireInsight(String person, UUID id) {
        Operation op = owned(person, id);
        if (!"ANALYSIS".equals(op.kind())) {
            throw new ApiException(HttpStatus.NOT_FOUND, "INSIGHT_NOT_FOUND", "Инсайт не найден");
        }
        if (op.result() == null || !("SUCCEEDED".equals(op.status()) || "LIMITED".equals(op.status()))) {
            throw new ApiException(HttpStatus.CONFLICT, "RESULT_NOT_AVAILABLE", "Инсайт отсутствует; проверьте состояние операции");
        }
        return op;
    }

    private void enqueue(UUID id) {
        try {
            executor.execute(() -> process(id));
        } catch (java.util.concurrent.RejectedExecutionException e) {
            store.fail(id, "QUEUE_FULL", "Очередь анализа заполнена", true);
        }
    }

    private void process(UUID id) {
        try {
            if (!store.start(id)) {
                return;
            }
            Operation op = store.find(id).orElseThrow();
            requireAccess(op.ownerId(), op.companyId());
            if ("ANALYSIS".equals(op.kind())) {
                processAnalysis(op);
            } else {
                processQuestion(op);
            }
        } catch (MlException e) {
            LOG.warn("ML operation {} failed: {}", id, e.code());
            store.fail(id, "ML_" + e.code().name(), e.getMessage(), e.retryable());
        } catch (ru.businessinsights.backend.data.ProfileDataException e) {
            store.fail(id, e.reason().name(), e.getMessage(), false);
        } catch (ApiException e) {
            store.fail(id, e.code(), e.getMessage(), e.retryable());
        } catch (Exception e) {
            // Do not log profile, news, question, upstream body, SQL parameters or credentials.
            LOG.error("Operation {} failed ({})", id, e.getClass().getSimpleName());
            store.fail(id, "PROCESSING_ERROR", "Не удалось завершить операцию", false);
        }
    }

    private void processAnalysis(Operation op) {
        if (op.stale() || store.currentEvent(op.eventId()).version() != op.eventVersion()) {
            var warnings = List.of("EVENT_SUPERSEDED: событие исправлено до начала анализа. Обновите ленту или запросите анализ новой версии");
            store.finish(op.id(), "NO_RESULT", null, warnings);
            store.trace(op.id(), null, "UNAVAILABLE", warnings);
            return;
        }
        var input = store.convert(op.payload(), AnalysisInput.class);
        var snapshot = profiles.loadProfile(op.companyId(), input.userProvided());
        var version = store.eventVersion(op.eventId(), op.eventVersion());
        var event = version.event();
        var selection = relevance.evaluate(snapshot.profile(), Role.valueOf(op.role()), version, Instant.now());
        store.selection(op.id(), selection.score(), selection.reasons(), selection.expiresAt());
        ObjectNode saved = store.tree(snapshot).deepCopy();
        saved.put("mlContractVersion", MlContracts.CONTRACT_VERSION).put("importContractCommit", DATA_COMMIT)
                .put("role", op.role()).put("eventId", op.eventId()).put("eventVersion", op.eventVersion());
        saved.set("event", store.tree(event));
        store.saveProfile(op.id(), saved);
        var warnings = new ArrayList<String>(snapshot.qualityIssues());
        if (!snapshot.sufficient()) {
            warnings.add("INSUFFICIENT_PROFILE: отсутствуют пригодные бизнес-признаки; персонализированная интерпретация не запрашивалась");
            store.finish(op.id(), "INSUFFICIENT_DATA", null, warnings);
            store.trace(op.id(), null, "UNAVAILABLE", warnings);
            return;
        }
        var profile = snapshot.profile();
        warnings.addAll(compatibilityWarnings(profile));
        var result = ml.generate(new MlContracts.InsightRequest(profile, MlContracts.UserRole.valueOf(input.role().name()), mlEvent(event)));
        String status = switch (result.status()) {
            case failed -> "FAILED";
            case limited -> "LIMITED";
            case complete -> warnings.isEmpty() ? "SUCCEEDED" : "LIMITED";
        };
        if (result.status() == MlContracts.InsightStatus.failed) {
            warnings.add("ML_RETURNED_FAILED: результата персонализированного анализа нет");
        }
        store.finish(op.id(), status, result, List.copyOf(warnings));
        try {
            store.trace(op.id(), ml.trace(result.runId()), "AVAILABLE", List.copyOf(warnings));
        } catch (MlException e) {
            warnings.add("ML_TRACE_" + e.code().name() + ": результат сохранён, трасса ML недоступна");
            store.trace(op.id(), null, "UNAVAILABLE", List.copyOf(warnings));
        }
    }

    private void processQuestion(Operation op) {
        Operation parent = store.find(op.parentId()).orElseThrow();
        store.saveProfile(op.id(), parent.profileSnapshot());
        var insight = store.convert(parent.result(), MlContracts.Insight.class);
        var profile = store.convert(parent.profileSnapshot().path("profile"), MlContracts.CompanyProfile.class);
        var answer = ml.ask(insight.insightId(), op.payload().path("question").asText(), profile);
        List<String> warnings = new ArrayList<>(parent.warnings());
        String status = "no_answer".equals(answer.status()) ? "NO_RESULT" : warnings.isEmpty() ? "SUCCEEDED" : "LIMITED";
        store.finish(op.id(), status, answer, List.copyOf(warnings));
        try {
            store.trace(op.id(), ml.trace(answer.runId()), "AVAILABLE", List.copyOf(warnings));
        } catch (MlException e) {
            warnings.add("ML_TRACE_" + e.code().name() + ": ответ сохранён, трасса уточняющего вопроса недоступна");
            store.trace(op.id(), null, "UNAVAILABLE", List.copyOf(warnings));
        }
    }

    private MlContracts.NewsEvent mlEvent(EventInput event) {
        return new MlContracts.NewsEvent(event.eventId(), event.title(), event.text(), event.publishedAt(), mlSource(event.source()),
                event.relatedSources().stream().map(this::mlSource).toList(), event.topics(), event.conflicts().stream()
                .map(c -> new MlContracts.SourceConflict(c.reason(), c.sourceIds(), c.resolved())).toList());
    }

    private MlContracts.SourceRef mlSource(SourceInput source) {
        return new MlContracts.SourceRef(source.sourceId(), MlContracts.SourceKind.news, source.title(), source.date(), source.page(), source.url(),
                source.docId(), source.contentSha256(), source.excerpt());
    }

    private EventInput apiEvent(MlContracts.NewsEvent event) {
        return new EventInput(event.eventId(), event.title(), event.text(), event.publishedAt(), apiSource(event.source()),
                event.relatedSources().stream().map(this::apiSource).toList(), event.topics(), event.conflicts().stream()
                .map(c -> new SourceConflictInput(c.reason(), c.sourceIds(), c.resolved())).toList());
    }

    private SourceInput apiSource(MlContracts.SourceRef source) {
        if (source.kind() != MlContracts.SourceKind.news) {
            throw new ApiException(HttpStatus.BAD_GATEWAY, "INVALID_CORPUS_SOURCE", "Корпус новостей содержит источник другого типа");
        }
        return new SourceInput(source.sourceId(), source.title(), source.date(), source.page(), source.url(), source.docId(), source.contentSha256(), source.excerpt());
    }

    private List<String> compatibilityWarnings(MlContracts.CompanyProfile p) {
        var warnings = new ArrayList<String>();
        if (!p.counterparties().isEmpty()) {
            warnings.add("ML_COUNTERPARTY_REFERENCE: ML сортирует контрагентов и меняет индексы признаков; ссылки counterparties[i] требуют согласования");
        }
        if (Boolean.TRUE.equals(p.foreignTrade()) || !p.foreignTradeCountries().isEmpty()) {
            warnings.add("ML_FTS_DIRECTION: сведения ФТС не устанавливают направление импорта или экспорта; соответствующие выводы ML требуют подтверждения");
        }
        return warnings;
    }

    private String hash(JsonNode node) {
        try {
            return HexFormat.of().formatHex(MessageDigest.getInstance("SHA-256").digest(canonical(node).getBytes(StandardCharsets.UTF_8)));
        } catch (NoSuchAlgorithmException e) {
            throw new IllegalStateException(e);
        }
    }

    private String canonical(JsonNode node) {
        if (node.isObject()) {
            var names = new ArrayList<String>();
            node.fieldNames().forEachRemaining(names::add);
            java.util.Collections.sort(names);
            return "{" + names.stream().map(n -> store.tree(n).toString() + ":" + canonical(node.get(n))).collect(java.util.stream.Collectors.joining(",")) + "}";
        }
        if (node.isArray()) {
            var values = new ArrayList<String>();
            node.forEach(n -> values.add(canonical(n)));
            return "[" + String.join(",", values) + "]";
        }
        return node.toString();
    }
}
