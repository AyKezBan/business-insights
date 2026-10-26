package ru.businessinsights.backend.service;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.AfterEach;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import ru.businessinsights.backend.api.ApiModels.*;
import ru.businessinsights.backend.config.BackendProperties;
import ru.businessinsights.backend.data.CompanyProfileService;
import ru.businessinsights.backend.data.MaterialCatalogRepository;
import ru.businessinsights.backend.data.ProfileSnapshot;
import ru.businessinsights.backend.ml.HttpMlClient;
import ru.businessinsights.backend.ml.MlContracts;
import ru.businessinsights.backend.store.OperationStore;
import ru.businessinsights.backend.store.OperationStore.Operation;
import ru.businessinsights.backend.testsupport.DisposablePostgres;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.URI;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.time.temporal.ChronoUnit;
import java.util.List;
import java.util.Map;
import java.util.UUID;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

/** Real JDBC queue and Java HTTP client; the loopback peer echoes Python datetime precision. */
class FrozenCutoffPrecisionTest {
    private static final Instant NANOSECOND_CUTOFF = Instant.parse("2026-10-26T00:45:12.123456789Z");
    private static final String MICROSECOND_CUTOFF = "2026-10-26T00:45:12.123456Z";
    private final ObjectMapper json = new ObjectMapper().registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    private final AtomicReference<JsonNode> sentRequest = new AtomicReference<>();
    private final AtomicReference<Throwable> peerFailure = new AtomicReference<>();
    private final AtomicInteger generations = new AtomicInteger();
    private volatile boolean shiftAnalysis;
    private volatile boolean shiftAnswer;
    private DisposablePostgres database;
    private HttpServer peer;
    private OperationStore store;
    private InsightService service;

    @BeforeEach
    void setUpRealBoundary() throws Exception {
        database = DisposablePostgres.openOrSkip();
        store = new OperationStore(database.jdbc(), json);
        peer = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        peer.createContext("/", this::reply);
        peer.start();
        var properties = new BackendProperties(
                new BackendProperties.Ml(URI.create("http://127.0.0.1:" + peer.getAddress().getPort()),
                        Duration.ofSeconds(1), Duration.ofSeconds(3), 65536),
                new BackendProperties.Jobs(1, 4, Duration.ofSeconds(3)),
                new BackendProperties.Auth("synthetic-unused-test-token", "F_1"));
        var profiles = mock(CompanyProfileService.class);
        when(profiles.hasAccess("F_1", "U_1")).thenReturn(true);
        var profile = json.treeToValue(json.readTree("{\"company_id\":\"U_1\",\"region\":\"Synthetic region\"}"),
                MlContracts.CompanyProfile.class);
        when(profiles.loadProfile(eq("U_1"), anyMap())).thenReturn(
                new ProfileSnapshot(profile, json.createObjectNode(), List.of(), true));
        service = new InsightService(profiles, new HttpMlClient(json, properties), store, new InsightMapper(json),
                json, properties, new MaterialCatalogRepository(database.jdbc(), json));
        service.ready();
        service.event(new EventInput("synthetic-cutoff", "Synthetic event", "Synthetic source text.",
                Instant.parse("2026-10-20T09:00:00Z"),
                new SourceInput("synthetic-source", "Synthetic source", LocalDate.of(2026, 10, 20),
                        null, "https://example.test/synthetic"), List.of(), List.of()));
    }

    @AfterEach
    void closeOwnedResources() throws Exception {
        try {
            if (service != null) service.close();
        } finally {
            if (peer != null) peer.stop(0);
            if (database != null) database.close();
        }
    }

    @Test
    void suppliedNanosecondsAreFrozenAtPythonPrecisionAndSurviveSavedQa() throws Exception {
        var input = input(NANOSECOND_CUTOFF);
        var submitted = service.analyze("F_1", input, "supplied-cutoff");
        var finished = awaitTerminal(submitted.operationId());
        assertEquals("SUCCEEDED", finished.status(), finished.errorCode());
        assertFrozenCutoff(finished);
        assertEquals(MICROSECOND_CUTOFF, finished.result().path("as_of").asText());

        var replay = service.analyze("F_1", input, "supplied-cutoff");
        assertEquals(finished.id(), replay.operationId());
        assertEquals(1, generations.get(), "Idempotent replay must not make another ML request");

        var question = service.question("F_1", finished.id(), new QuestionInput("What is the saved region?"), "question-cutoff");
        var answer = awaitTerminal(question.operationId());
        assertEquals("SUCCEEDED", answer.status(), answer.errorCode());
        assertEquals(MICROSECOND_CUTOFF, answer.payload().path("request").path("as_of").asText());
        assertEquals(MICROSECOND_CUTOFF, answer.profileSnapshot().path("asOf").asText());
        assertEquals(MICROSECOND_CUTOFF, answer.result().path("as_of").asText());
    }

    @Test
    void automaticallySelectedCutoffUsesTheSameFrozenPrecision() throws Exception {
        OperationView submitted;
        // Only the submitting thread sees this clock; JDBC, HTTP and worker processing stay real.
        try (var clock = mockStatic(Instant.class, CALLS_REAL_METHODS)) {
            clock.when(Instant::now).thenReturn(NANOSECOND_CUTOFF);
            submitted = service.analyze("F_1", input(null), "automatic-cutoff");
        }
        var finished = awaitTerminal(submitted.operationId());
        assertEquals("SUCCEEDED", finished.status(), finished.errorCode());
        assertFrozenCutoff(finished);
    }

    @Test
    void differentMicrosecondInInsightStillFailsStrictContextValidation() throws Exception {
        shiftAnalysis = true;
        var submitted = service.analyze("F_1", input(Instant.parse(MICROSECOND_CUTOFF)), "wrong-insight-cutoff");
        var finished = awaitTerminal(submitted.operationId());
        assertEquals("FAILED", finished.status());
        assertEquals("ML_INVALID_RESPONSE", finished.errorCode());
        assertNull(finished.result());
    }

    @Test
    void differentMicrosecondInSavedQaStillFailsStrictContextValidation() throws Exception {
        var submitted = service.analyze("F_1", input(Instant.parse(MICROSECOND_CUTOFF)), "qa-parent-cutoff");
        var parent = awaitTerminal(submitted.operationId());
        assertEquals("SUCCEEDED", parent.status(), parent.errorCode());
        shiftAnswer = true;
        var question = service.question("F_1", parent.id(), new QuestionInput("What is the saved region?"), "wrong-answer-cutoff");
        var finished = awaitTerminal(question.operationId());
        assertEquals("FAILED", finished.status());
        assertEquals("ML_INVALID_RESPONSE", finished.errorCode());
        assertNull(finished.result());
    }

    private AnalysisInput input(Instant cutoff) {
        return new AnalysisInput("U_1", Role.cfo, "synthetic-cutoff", Map.of(), cutoff, null, null, null);
    }

    private void assertFrozenCutoff(Operation op) {
        assertEquals(MICROSECOND_CUTOFF, sentRequest.get().path("as_of").asText());
        assertEquals(MICROSECOND_CUTOFF, op.payload().path("request").path("as_of").asText());
        assertEquals(MICROSECOND_CUTOFF, op.profileSnapshot().path("asOf").asText());
    }

    private Operation awaitTerminal(UUID id) throws Exception {
        long deadline = System.nanoTime() + Duration.ofSeconds(5).toNanos();
        do {
            var op = store.find(id).orElseThrow();
            if (!List.of("QUEUED", "RUNNING").contains(op.status())) {
                assertNull(peerFailure.get(), "Synthetic HTTP peer failed");
                return op;
            }
            Thread.sleep(10);
        } while (System.nanoTime() < deadline);
        throw new AssertionError("Operation did not finish");
    }

    private void reply(HttpExchange exchange) throws IOException {
        try (exchange) {
            JsonNode response;
            if (exchange.getRequestURI().getPath().endsWith("/trace")) {
                response = json.createArrayNode();
            } else if (exchange.getRequestURI().getPath().endsWith("/questions")) {
                exchange.getRequestBody().readAllBytes();
                response = json.readTree("""
                        {"insight_id":"ins_cutoff","run_id":"run_cutoffqa","company_id":"U_1",
                         "event_id":"synthetic-cutoff","status":"answered","text":"Synthetic region",
                         "claims":[{"text":"Synthetic region","kind":"inference","source_ids":[],"profile_features":["region"]}],
                         "sources":[],"missing_data":[]}
                        """);
                ((ObjectNode) response).put("as_of", echoedCutoff(sentRequest.get(), shiftAnswer));
            } else {
                generations.incrementAndGet();
                JsonNode request = json.readTree(exchange.getRequestBody());
                sentRequest.set(request);
                response = json.readTree("""
                        {"insight_id":"ins_cutoff","run_id":"run_cutoff","company_id":"U_1","role":"cfo",
                         "event_id":"synthetic-cutoff","status":"complete","headline":"Synthetic cutoff result",
                         "what_happened":[],"why_it_matters":[],"consequences":[],"risks":[],"opportunities":[],
                         "scenarios":[],"actions":[],"bank_view":[],"model_vs_bank":[],"impact_level":"low",
                         "impact_rationale":"Synthetic only","profile_features_used":[],"sources":[],"missing_data":[],
                         "self_check":{"passed":true,"issues":[],"iterations":1,"adaptations":[]},
                         "models":["synthetic-no-provider"],"generated_at":"2026-10-26T01:00:00Z"}
                        """);
                ((ObjectNode) response).put("request_id", request.path("request_id").asText());
                ((ObjectNode) response).put("as_of", echoedCutoff(request, shiftAnalysis));
            }
            byte[] bytes = json.writeValueAsBytes(response);
            exchange.getResponseHeaders().add("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes);
        } catch (Throwable failure) {
            peerFailure.set(failure);
        }
    }

    private String echoedCutoff(JsonNode request, boolean shift) {
        // Python datetime/Pydantic truncates digits after microseconds, rather than rounding.
        var cutoff = Instant.parse(request.path("as_of").asText()).truncatedTo(ChronoUnit.MICROS);
        return (shift ? cutoff.plus(1, ChronoUnit.MICROS) : cutoff).toString();
    }
}
