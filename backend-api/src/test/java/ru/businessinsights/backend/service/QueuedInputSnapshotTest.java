package ru.businessinsights.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.http.HttpStatus;
import ru.businessinsights.backend.api.ApiException;
import ru.businessinsights.backend.api.ApiModels.AnalysisInput;
import ru.businessinsights.backend.api.ApiModels.EventInput;
import ru.businessinsights.backend.api.ApiModels.Role;
import ru.businessinsights.backend.api.ApiModels.SourceInput;
import ru.businessinsights.backend.config.BackendProperties;
import ru.businessinsights.backend.data.CompanyProfileService;
import ru.businessinsights.backend.data.ProfileSnapshot;
import ru.businessinsights.backend.ml.MlClient;
import ru.businessinsights.backend.ml.MlContracts;
import ru.businessinsights.backend.store.OperationStore;
import ru.businessinsights.backend.testsupport.DisposablePostgres;

import java.net.URI;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/**
 * Real service/executor/JDBC boundary with a deliberately occupied sole worker.
 * Direct event SQL below simulates a trusted refresh until the catalog CAS is integrated.
 */
class QueuedInputSnapshotTest {
    private final ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    private final AtomicReference<ProfileSnapshot> currentProfile = new AtomicReference<>();
    private final AtomicReference<MlContracts.InsightRequest> queuedMlRequest = new AtomicReference<>();
    private final AtomicInteger generateCalls = new AtomicInteger();
    private final CountDownLatch firstEntered = new CountDownLatch(1);
    private final CountDownLatch releaseFirst = new CountDownLatch(1);
    private final CountDownLatch secondEntered = new CountDownLatch(1);
    private DisposablePostgres database;
    private OperationStore store;
    private InsightService service;
    private CompanyProfileService profiles;
    private MlClient ml;

    @BeforeEach
    void initializeRealQueueAndStore() throws Exception {
        database = DisposablePostgres.openOrSkip();
        store = new OperationStore(database.jdbc(), mapper);
        profiles = mock(CompanyProfileService.class);
        currentProfile.set(profile("P1", "10001.25"));
        when(profiles.hasAccess("F_1", "U_1")).thenReturn(true);
        when(profiles.loadProfile(eq("U_1"), anyMap())).thenAnswer(ignored -> currentProfile.get());
        ml = mock(MlClient.class);
        when(ml.trace(anyString())).thenReturn(List.of());
        when(ml.generate(any())).thenAnswer(invocation -> {
            MlContracts.InsightRequest request = invocation.getArgument(0);
            if (generateCalls.incrementAndGet() == 1) {
                firstEntered.countDown();
                if (!releaseFirst.await(10, TimeUnit.SECONDS)) {
                    throw new IllegalStateException("Synthetic blocking request was not released");
                }
            } else {
                queuedMlRequest.set(request);
                secondEntered.countDown();
            }
            return response(request);
        });
        var properties = new BackendProperties(
                new BackendProperties.Ml(URI.create("http://127.0.0.1:1"), Duration.ofSeconds(1), Duration.ofSeconds(2), 4096),
                new BackendProperties.Jobs(1, 4, Duration.ofSeconds(3)),
                new BackendProperties.Auth("synthetic-unused-test-token", "F_1"));
        service = new InsightService(profiles, ml, store, new InsightMapper(mapper), mapper, properties, new ru.businessinsights.backend.data.MaterialCatalogRepository(database.jdbc(), mapper));
        service.ready();
        service.event(event("blocker", "Blocker text", "blocker-source"));
        service.event(event("queued", "R1 original text", "source-R1"));
    }

    @AfterEach
    void releaseWorkerAndCloseOnlyOwnedResources() throws Exception {
        releaseFirst.countDown();
        try {
            if (service != null) {
                service.close();
            }
        } finally {
            if (database != null) {
                database.close();
            }
        }
    }

    @Test
    void queuedOperationAlreadyContainsProfileSnapshotBeforeWorkerStarts() throws Exception {
        occupyOnlyWorker();
        var queued = service.analyze("F_1", queuedInput(Role.cfo, Map.of("exposure", "P1 supplied detail")), "queued-key");
        var accepted = store.find(queued.operationId()).orElseThrow();

        assertEquals("QUEUED", accepted.status());
        assertEquals(1, generateCalls.get(), "Only the blocker may enter ML before the latch is released");
        assertNotNull(queued.requestId(), "Queued response must expose the frozen ML request ID");
        assertEquals(accepted.payload().path("request").path("request_id").asText(), queued.requestId());
        assertNotNull(accepted.profileSnapshot(), "Profile/event snapshot must be persisted at submission, before the queued worker can run");
        assertEquals("P1", accepted.profileSnapshot().path("profile").path("region").asText());
        assertEquals("R1 original text", accepted.profileSnapshot().path("event").path("text").asText());
    }

    @Test
    void queuedWorkerUsesSubmittedProfileAndEventAfterBothCurrentInputsChange() throws Exception {
        occupyOnlyWorker();
        service.analyze("F_1", queuedInput(Role.cfo, Map.of()), "queued-key");
        refreshCurrentInputs();
        releaseFirst.countDown();
        assertTrue(secondEntered.await(5, TimeUnit.SECONDS), "Queued request did not reach the ML boundary");
        var sent = queuedMlRequest.get();

        assertAll("The actual ML request must remain the accepted P1/R1 snapshot",
                () -> assertEquals("P1", sent.profile().region()),
                () -> assertEquals("10001.25", sent.profile().revenueRub().toPlainString()),
                () -> assertEquals("R1 original text", sent.event().text()),
                () -> assertEquals("source-R1", sent.event().source().sourceId()),
                () -> assertEquals(MlContracts.UserRole.cfo, sent.role()));
    }

    @Test
    void replayKeepsOriginalOperationAfterRefreshAndChangedExplicitInputConflicts() throws Exception {
        occupyOnlyWorker();
        AnalysisInput original = queuedInput(Role.cfo, Map.of("exposure", "P1 supplied detail"));
        var queued = service.analyze("F_1", original, "queued-key");
        refreshCurrentInputs();
        var replay = service.analyze("F_1", original, "queued-key");
        assertEquals(queued.operationId(), replay.operationId());

        ApiException changedRole = assertThrows(ApiException.class,
                () -> service.analyze("F_1", queuedInput(Role.ceo, original.userProvided()), "queued-key"));
        ApiException changedDetails = assertThrows(ApiException.class,
                () -> service.analyze("F_1", queuedInput(Role.cfo, Map.of("exposure", "P2 changed detail")), "queued-key"));
        assertAll(
                () -> assertEquals(HttpStatus.CONFLICT, changedRole.status()),
                () -> assertEquals("IDEMPOTENCY_CONFLICT", changedRole.code()),
                () -> assertEquals(HttpStatus.CONFLICT, changedDetails.status()),
                () -> assertEquals("IDEMPOTENCY_CONFLICT", changedDetails.code()));
        releaseFirst.countDown();
        assertTrue(secondEntered.await(5, TimeUnit.SECONDS), "Original queued request did not reach ML");
        service.close();
        service = null;
        assertEquals(2, generateCalls.get(), "The blocker and one original request may run; replay must not call ML again");
        assertEquals(1, database.jdbc().queryForObject(
                "SELECT count(*) FROM backend_api.operation WHERE owner_id='F_1' AND request_key='queued-key'", Integer.class));
    }

    @Test
    void catalogMismatchIsRejectedBeforeAnyProviderCall() throws Exception {
        var repo = new ru.businessinsights.backend.data.MaterialCatalogRepository(database.jdbc(), mapper);
        var tx = new org.springframework.transaction.support.TransactionTemplate(new org.springframework.jdbc.datasource.DataSourceTransactionManager(database.jdbc().getDataSource()));
        var r1 = new ru.businessinsights.backend.data.MaterialCatalogRepository.CatalogToken("store-one", 1, "a".repeat(64));
        var event = new MlContracts.NewsEvent("catalog", "Catalog", "R1 catalog text", Instant.parse("2026-10-20T09:00:00Z"),
                new MlContracts.SourceRef("source:catalog", MlContracts.SourceKind.news, "Source", null, null, null), List.of(), List.of());
        tx.execute(status -> repo.synchronize(null, r1, List.of(new ru.businessinsights.backend.data.MaterialCatalogRepository.MaterialEvent(event, "1".repeat(64)))));
        var wrong = new AnalysisInput("U_1", Role.cfo, "catalog", Map.of(), Instant.parse("2026-10-21T09:00:00Z"), "b".repeat(64), "1".repeat(64), "request-wrong");
        var error = assertThrows(ApiException.class, () -> service.analyze("F_1", wrong, "wrong-version"));
        assertEquals("CORPUS_CONTEXT_MISMATCH", error.code());
        assertEquals(0, generateCalls.get());
        assertEquals(0, database.jdbc().queryForObject("SELECT count(*) FROM backend_api.operation", Integer.class));
    }

    @Test
    void bankOnlyRefreshPinsOldQueueAndNewRequestToTheirOwnRevisions() throws Exception {
        var repo = new ru.businessinsights.backend.data.MaterialCatalogRepository(database.jdbc(), mapper);
        var tx = new org.springframework.transaction.support.TransactionTemplate(new org.springframework.jdbc.datasource.DataSourceTransactionManager(database.jdbc().getDataSource()));
        var r1 = new ru.businessinsights.backend.data.MaterialCatalogRepository.CatalogToken("store-one", 1, "a".repeat(64));
        var r2 = new ru.businessinsights.backend.data.MaterialCatalogRepository.CatalogToken("store-one", 2, "b".repeat(64));
        var event = new MlContracts.NewsEvent("catalog", "Catalog", "R1 catalog text", Instant.parse("2026-10-20T09:00:00Z"),
                new MlContracts.SourceRef("source:catalog", MlContracts.SourceKind.news, "Source", null, null, null), List.of(), List.of());
        var material = new ru.businessinsights.backend.data.MaterialCatalogRepository.MaterialEvent(event, "1".repeat(64));
        tx.execute(status -> repo.synchronize(null, r1, List.of(material)));
        occupyOnlyWorker();
        var original = new AnalysisInput("U_1", Role.cfo, "catalog", Map.of(), Instant.parse("2026-10-21T09:00:00Z"), r1.revision(), "1".repeat(64), "request-r1");
        var old = service.analyze("F_1", original, "catalog-old");
        tx.execute(status -> repo.synchronize(r1, r2, List.of(material)));
        currentProfile.set(profile("P2", "20002.50"));
        assertEquals(old.operationId(), service.analyze("F_1", original, "catalog-old").operationId());
        var newer = new AnalysisInput("U_1", Role.cfo, "catalog", Map.of(), Instant.parse("2026-10-22T09:00:00Z"), r2.revision(), "1".repeat(64), "request-r2");
        assertThrows(ApiException.class, () -> service.analyze("F_1", newer, "catalog-old"));
        releaseFirst.countDown();
        assertTrue(secondEntered.await(5, TimeUnit.SECONDS));
        assertEquals(r1.revision(), queuedMlRequest.get().corpusRevision());
        assertEquals("P1", queuedMlRequest.get().profile().region());
        assertEquals(original.asOf(), queuedMlRequest.get().asOf());
        assertEquals("request-r1", queuedMlRequest.get().requestId());
        var next = service.analyze("F_1", newer, "catalog-new");
        service.close(); service = null;
        assertEquals(3, generateCalls.get());
        assertEquals(r2.revision(), queuedMlRequest.get().corpusRevision());
        var restarted = new OperationStore(database.jdbc(), mapper); restarted.interruptUnfinished();
        assertEquals(r1.revision(), restarted.find(old.operationId()).orElseThrow().result().path("corpus_revision").asText());
        assertEquals(r2.revision(), restarted.find(next.operationId()).orElseThrow().result().path("corpus_revision").asText());
        assertEquals("P1", restarted.find(old.operationId()).orElseThrow().profileSnapshot().path("profile").path("region").asText());
    }

    @Test
    void workerStartsOnlyAfterAcceptedTransactionCommits() throws Exception {
        var tx = new org.springframework.transaction.support.TransactionTemplate(new org.springframework.jdbc.datasource.DataSourceTransactionManager(database.jdbc().getDataSource()));
        var accepted = tx.execute(status -> {
            var operation = service.analyze("F_1", new AnalysisInput("U_1", Role.cfo, "blocker", Map.of()), "transaction-key");
            assertEquals("QUEUED", store.find(operation.operationId()).orElseThrow().status());
            assertEquals(0, generateCalls.get());
            return operation;
        });
        assertNotNull(accepted);
        assertTrue(firstEntered.await(5, TimeUnit.SECONDS), "Worker must be enqueued after commit and observe the accepted row");
    }

    @Test
    void concurrentIdenticalSubmissionsReturnOneCommittedOperation() throws Exception {
        var barrier = new java.util.concurrent.CyclicBarrier(2);
        when(profiles.loadProfile(eq("U_1"), anyMap())).thenAnswer(ignored -> {
            barrier.await(5, TimeUnit.SECONDS);
            return currentProfile.get();
        });
        var factory = new org.springframework.aop.framework.ProxyFactory(service);
        factory.setProxyTargetClass(true);
        factory.addAdvice(new org.springframework.transaction.interceptor.TransactionInterceptor(
                new org.springframework.jdbc.datasource.DataSourceTransactionManager(database.jdbc().getDataSource()),
                new org.springframework.transaction.annotation.AnnotationTransactionAttributeSource()));
        service = (InsightService) factory.getProxy();
        try (var callers = java.util.concurrent.Executors.newFixedThreadPool(2)) {
            var first = callers.submit(() -> service.analyze("F_1", new AnalysisInput("U_1", Role.cfo, "blocker", Map.of()), "concurrent-key"));
            var second = callers.submit(() -> service.analyze("F_1", new AnalysisInput("U_1", Role.cfo, "blocker", Map.of()), "concurrent-key"));
            var one = first.get(8, TimeUnit.SECONDS);
            var two = second.get(8, TimeUnit.SECONDS);
            assertEquals(one.operationId(), two.operationId());
            assertTrue(firstEntered.await(5, TimeUnit.SECONDS));
            assertEquals(1, generateCalls.get());
            assertEquals(1, database.jdbc().queryForObject("SELECT count(*) FROM backend_api.operation WHERE request_key='concurrent-key'", Integer.class));
        }
    }

    @org.junit.jupiter.params.ParameterizedTest
    @org.junit.jupiter.params.provider.CsvSource({"company_id,U_2", "event_id,other-event", "corpus_revision,bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "as_of,2026-10-22T09:00:00Z"})
    void qaRejectsAnswersOutsideSavedParentContext(String field, String value) throws Exception {
        var event = new MlContracts.NewsEvent("queued", "Event", "R1 original text", null,
                new MlContracts.SourceRef("source-R1", MlContracts.SourceKind.news, "Source", null, null, null), List.of(), List.of());
        var request = new MlContracts.InsightRequest(currentProfile.get().profile(), MlContracts.UserRole.cfo, event,
                "parent-request", null, Instant.parse("2026-10-21T09:00:00Z"), "a".repeat(64), "1".repeat(64));
        var parent = store.create("F_1", "U_1", "cfo", "ANALYSIS", null, "queued", "qa-parent", "parent", Map.of()).operation();
        store.start(parent.id()); store.saveProfile(parent.id(), currentProfile.get()); store.finish(parent.id(), "SUCCEEDED", response(request), List.of());
        var answer = mapper.createObjectNode().put("insight_id", "ins_queue_fixture").put("run_id", "run_qa")
                .put("company_id", "U_1").put("event_id", "queued").put("status", "answered").put("text", "Saved P1 context")
                .put("corpus_revision", "a".repeat(64)).put("event_content_sha256", "1".repeat(64)).put("as_of", "2026-10-21T09:00:00Z");
        answer.put(field, value);
        when(ml.ask(anyString(), anyString(), any())).thenReturn(mapper.treeToValue(answer, MlContracts.Answer.class));
        currentProfile.set(profile("P2", "20002.50"));
        var operation = service.question("F_1", parent.id(), new ru.businessinsights.backend.api.ApiModels.QuestionInput("Что изменилось?"), "qa-key");
        service.close(); service = null;
        var completed = store.find(operation.operationId()).orElseThrow();
        assertEquals("FAILED", completed.status());
        assertEquals("ML_INVALID_RESPONSE", completed.errorCode());
        assertNull(completed.result(), "An answer from another context must never be published");
    }

    private void occupyOnlyWorker() throws Exception {
        service.analyze("F_1", new AnalysisInput("U_1", Role.cfo, "blocker", Map.of()), "blocker-key");
        assertTrue(firstEntered.await(5, TimeUnit.SECONDS), "Blocking request did not occupy the worker");
    }

    private AnalysisInput queuedInput(Role role, Map<String, String> details) {
        return new AnalysisInput("U_1", role, "queued", details);
    }

    private void refreshCurrentInputs() throws Exception {
        currentProfile.set(profile("P2", "20002.50"));
        // Test-only stand-in for a later trusted catalog publication. Legacy POST must remain immutable.
        int updated = database.jdbc().update(
                "UPDATE backend_api.event SET payload=?::jsonb,content_hash=? WHERE event_id='queued'",
                mapper.writeValueAsString(event("queued", "R2 corrected text", "source-R2")), "hash-R2");
        assertEquals(1, updated, "The test must actually change the current event before the queued worker resumes");
    }

    private ProfileSnapshot profile(String region, String amount) throws Exception {
        var json = mapper.createObjectNode().put("company_id", "U_1").put("region", region);
        json.put("revenue_rub", new java.math.BigDecimal(amount));
        json.put("group_revenue_rub", new java.math.BigDecimal("90000000"));
        return new ProfileSnapshot(mapper.treeToValue(json, MlContracts.CompanyProfile.class),
                mapper.createObjectNode().put("fixture", region), List.of(), true);
    }

    private EventInput event(String id, String text, String sourceId) {
        return new EventInput(id, "Synthetic queue event", text, Instant.parse("2026-10-20T09:00:00Z"),
                new SourceInput(sourceId, "Synthetic original", LocalDate.of(2026, 10, 20), null,
                        "https://example.test/" + sourceId), List.of(), List.of("fixture"));
    }

    private MlContracts.Insight response(MlContracts.InsightRequest request) throws Exception {
        ObjectNode json = (ObjectNode) mapper.readTree("""
                {"insight_id":"ins_queue_fixture","run_id":"run_queue_fixture","status":"complete",
                 "headline":"Synthetic queue result","what_happened":[],"why_it_matters":[],"consequences":[],
                 "risks":[],"opportunities":[],"scenarios":[],"actions":[],"bank_view":[],"model_vs_bank":[],
                 "impact_level":"low","impact_rationale":"Synthetic only","profile_features_used":[],
                 "sources":[],"missing_data":[],"self_check":{"passed":true,"issues":[],"iterations":1,"adaptations":[]},
                 "models":["synthetic-no-provider"],"generated_at":"2026-10-20T12:00:00Z"}
                """);
        json.put("event_id", request.event().eventId()).put("company_id", request.profile().companyId())
                .put("role", request.role().name());
        json.put("corpus_revision", request.corpusRevision()).put("event_content_sha256", request.eventContentSha256()).put("request_id", request.requestId());
        if (request.asOf() != null) json.put("as_of", request.asOf().toString());
        return mapper.treeToValue(json, MlContracts.Insight.class);
    }
}
