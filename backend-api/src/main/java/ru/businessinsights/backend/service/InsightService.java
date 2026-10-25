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
import ru.businessinsights.backend.data.MaterialCatalogRepository;
import org.springframework.transaction.annotation.Transactional;
import org.springframework.transaction.annotation.Isolation;
import org.springframework.transaction.support.TransactionSynchronization;
import org.springframework.transaction.support.TransactionSynchronizationManager;
import ru.businessinsights.backend.ml.MlClient;
import ru.businessinsights.backend.ml.MlContracts;
import ru.businessinsights.backend.ml.MlException;
import ru.businessinsights.backend.store.OperationStore;
import ru.businessinsights.backend.store.OperationStore.Operation;

@Service
public class InsightService {
    private static final Logger LOG = LoggerFactory.getLogger(InsightService.class);
    private static final String ML_COMMIT = MlContracts.CONTRACT_COMMIT;
    private static final String DATA_COMMIT = "f44b69f0d124e8e4c33e5301b629413618090fa5";
    private final CompanyProfileService profiles;
    private final MlClient ml;
    private final MaterialCatalogRepository catalog;
    private final OperationStore store;
    private final InsightMapper views;
    private final ObjectMapper mapper;
    private final BackendProperties properties;
    private final ThreadPoolExecutor executor;
    private volatile boolean ready;

    public InsightService(CompanyProfileService profiles, MlClient ml, OperationStore store, InsightMapper views,
                          ObjectMapper mapper, BackendProperties properties, MaterialCatalogRepository catalog) {
        this.profiles = profiles;
        this.ml = ml;
        this.catalog = catalog;
        this.store = store;
        this.views = views;
        this.mapper = mapper;
        this.properties = properties;
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
                p.revenueRub(), 2025, "RUB", null, p.clientType(), p.employees(), p.foreignTrade(), p.foreignTradeCountries(), p.selfTransferShare(), p.revenueStatus(), p.revenueScope(),
                p.foreignTradeDataAvailable(), p.paymentPeriod(), p.profileQualityNotes());
        return new ProfileView(company, facts, snapshot.provenance(), snapshot.qualityIssues(), snapshot.sufficient());
    }

    public EventInput event(EventInput input) {
        // Treat source URLs as references: this service never follows caller-supplied URLs.
        var normalized = new EventInput(input.eventId(), input.title(), input.text(), input.publishedAt(), input.source(),
                input.relatedSources() == null ? List.of() : input.relatedSources(), input.topics() == null ? List.of() : input.topics());
        var ids = new java.util.HashSet<String>();
        ids.add(normalized.source().sourceId());
        for (var s : normalized.relatedSources()) {
            if (!ids.add(s.sourceId())) {
                throw new ApiException(HttpStatus.BAD_REQUEST, "DUPLICATE_SOURCE_ID", "Идентификаторы источников события должны различаться");
            }
        }
        return store.registerEvent(normalized, hash(store.tree(normalized)));
    }

    // Profile loader holds importer table locks; READ_COMMITTED lets a racing idempotent
    // INSERT observe the winner after ON CONFLICT waits. The entire accepted payload is one insert.
    @Transactional(isolation = Isolation.READ_COMMITTED)
    public OperationView analyze(String person, AnalysisInput input, String key) {
        requireAccess(person, input.companyId());
        var normalized = new AnalysisInput(input.companyId(), input.role(), input.eventId(), input.userProvided() == null ? Map.of() : input.userProvided(),
                input.asOf(), input.corpusRevision(), input.eventContentSha256(), input.requestId());
        String fingerprint = hash(store.tree(normalized));
        var replay = store.replay(person, "ANALYSIS", key, fingerprint);
        if (replay.isPresent()) return views.operation(replay.get());
        requireReady();
        var snapshot = profiles.loadProfile(input.companyId(), normalized.userProvided());
        var material = catalog.findCurrent(input.eventId());
        var event = material.isPresent() ? material.get().event() : mlEvent(store.event(input.eventId()));
        String revision = material.map(m -> m.token().revision()).orElse(null);
        String eventHash = material.map(MaterialCatalogRepository.CurrentEvent::eventContentSha256).orElse(null);
        if (material.isPresent() && !material.get().token().equals(catalog.head()))
            throw new ApiException(HttpStatus.CONFLICT, "MATERIAL_EVENT_STALE", "Материал нужно синхронизировать с текущей публикацией каталога");
        if ((input.corpusRevision() != null && !input.corpusRevision().equals(revision))
                || (input.eventContentSha256() != null && !input.eventContentSha256().equals(eventHash)))
            throw new ApiException(HttpStatus.CONFLICT, "CORPUS_CONTEXT_MISMATCH", "Запрошенная версия материала отличается от принятого каталога");
        var asOf = input.asOf() == null ? java.time.Instant.now() : input.asOf();
        if (event.publishedAt() != null && event.publishedAt().isAfter(asOf))
            throw new ApiException(HttpStatus.BAD_REQUEST, "EVENT_AFTER_CUTOFF", "Дата события позже выбранной границы публикаций");
        String requestId = input.requestId() == null ? "request_" + UUID.randomUUID().toString().replace("-", "") : input.requestId();
        var request = new MlContracts.InsightRequest(snapshot.profile(), MlContracts.UserRole.valueOf(input.role().name()), event,
                requestId, null, asOf, revision, eventHash);
        ObjectNode saved = store.tree(snapshot).deepCopy();
        saved.put("mlContractCommit", ML_COMMIT).put("importContractCommit", DATA_COMMIT).put("role", input.role().name()).put("eventId", input.eventId());
        saved.set("event", store.tree(event));
        saved.put("asOf", asOf.toString()).put("requestId", requestId).put("corpusRevision", revision).put("eventContentSha256", eventHash);
        var payload = mapper.createObjectNode();
        payload.set("input", store.tree(normalized)); payload.set("request", store.tree(request));
        var submission = store.createFrozen(person, input.companyId(), input.role().name(), "ANALYSIS", null, input.eventId(), key, fingerprint, payload, saved);
        if (submission.created()) enqueueAfterCommit(submission.operation().id());
        return views.operation(submission.operation());
    }

    public OperationView question(String person, UUID insightId, QuestionInput input, String key) {
        requireReady();
        Operation parent = requireInsight(person, insightId);
        // Include parent ID in the fingerprint; a key cannot silently move a question to another insight.
        var payload = mapper.createObjectNode().put("question", input.question()).put("parentId", parent.id().toString());
        var context = mapper.createObjectNode();
        for (String field : List.of("corpus_revision", "event_content_sha256", "as_of")) {
            if (parent.result().has(field)) context.set(field, parent.result().get(field));
        }
        payload.set("request", context);
        var submission = store.createFrozen(person, parent.companyId(), parent.role(), "QUESTION", parent.id(), parent.eventId(), key, hash(payload), payload, parent.profileSnapshot());
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
                .map(o -> views.insight(o, store.interaction(o.id(), person))).toList();
        return new FeedView(items, limit, offset, "impactLevel DESC, createdAt DESC, insightId DESC",
                items.isEmpty() ? "NO_MATCHING_INSIGHTS" : null);
    }

    public InsightView insight(String person, UUID id) {
        var op = requireInsight(person, id);
        return views.insight(op, store.interaction(id, person));
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
        if (!ready) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "BACKEND_STARTING", "Сервис запускается или останавливается", true);
        }
        if (executor.getActiveCount() >= executor.getMaximumPoolSize() && executor.getQueue().remainingCapacity() == 0) {
            throw new ApiException(HttpStatus.SERVICE_UNAVAILABLE, "QUEUE_FULL", "Очередь анализа заполнена", true);
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

    private void enqueueAfterCommit(UUID id) {
        if (TransactionSynchronizationManager.isSynchronizationActive()) {
            TransactionSynchronizationManager.registerSynchronization(new TransactionSynchronization() {
                @Override public void afterCommit() { enqueue(id); }
            });
        } else enqueue(id);
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
        var request = store.convert(op.payload().path("request"), MlContracts.InsightRequest.class);
        var saved = op.profileSnapshot();
        var snapshot = new ru.businessinsights.backend.data.ProfileSnapshot(request.profile(), saved.path("provenance"),
                mapper.convertValue(saved.path("qualityIssues"), new com.fasterxml.jackson.core.type.TypeReference<List<String>>() {}), saved.path("sufficient").asBoolean());
        var warnings = new ArrayList<String>(snapshot.qualityIssues());
        if (!snapshot.sufficient()) {
            warnings.add("INSUFFICIENT_PROFILE: отсутствуют пригодные бизнес-признаки; персонализированная интерпретация не запрашивалась");
            store.finish(op.id(), "INSUFFICIENT_DATA", null, warnings);
            store.trace(op.id(), null, "UNAVAILABLE", warnings);
            return;
        }
        var result = ml.generate(request);
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
        if ((answer.companyId() != null && !parent.companyId().equals(answer.companyId()))
                || (answer.eventId() != null && !parent.eventId().equals(answer.eventId()))
                || (insight.corpusRevision() != null && !insight.corpusRevision().equals(answer.corpusRevision()))
                || (insight.eventContentSha256() != null && !insight.eventContentSha256().equals(answer.eventContentSha256()))
                || (insight.asOf() != null && !insight.asOf().equals(answer.asOf())))
            throw new MlException(MlException.Code.INVALID_RESPONSE, "ML answer differs from the saved insight context", false);
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
                event.relatedSources().stream().map(this::mlSource).toList(), event.topics());
    }

    private MlContracts.SourceRef mlSource(SourceInput source) {
        return new MlContracts.SourceRef(source.sourceId(), MlContracts.SourceKind.news, source.title(), source.date(), source.page(), source.url());
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
