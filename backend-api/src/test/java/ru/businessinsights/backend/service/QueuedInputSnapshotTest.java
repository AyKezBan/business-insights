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

    @BeforeEach
    void initializeRealQueueAndStore() throws Exception {
        database = DisposablePostgres.openOrSkip();
        store = new OperationStore(database.jdbc(), mapper);
        CompanyProfileService profiles = mock(CompanyProfileService.class);
        currentProfile.set(profile("P1", "10001.25"));
        when(profiles.hasAccess("F_1", "U_1")).thenReturn(true);
        when(profiles.loadProfile(eq("U_1"), anyMap())).thenAnswer(ignored -> currentProfile.get());
        MlClient ml = mock(MlClient.class);
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
        service = new InsightService(profiles, ml, store, new InsightMapper(mapper), mapper, properties);
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
        return mapper.treeToValue(json, MlContracts.Insight.class);
    }
}
