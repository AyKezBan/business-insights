package ru.businessinsights.backend.ml;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.CsvSource;
import org.junit.jupiter.params.provider.MethodSource;
import org.junit.jupiter.params.provider.ValueSource;
import org.springframework.mock.web.MockHttpServletRequest;
import ru.businessinsights.backend.api.ApiErrorHandler;
import ru.businessinsights.backend.api.ApiModels.*;
import ru.businessinsights.backend.config.BackendProperties;
import ru.businessinsights.backend.data.CompanyProfileService;
import ru.businessinsights.backend.data.MaterialCatalogRepository;
import ru.businessinsights.backend.data.ProfileSnapshot;
import ru.businessinsights.backend.service.InsightMapper;
import ru.businessinsights.backend.service.InsightService;
import ru.businessinsights.backend.store.OperationStore;
import ru.businessinsights.backend.testsupport.DisposablePostgres;

import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.ArgumentMatchers.*;
import static org.mockito.Mockito.*;

class HttpMlCorpusErrorTest {
    private static final ObjectMapper JSON = new ObjectMapper().registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    private static final String POISON = "UNTRUSTED upstream assertion must not be published";

    @ParameterizedTest
    @CsvSource({
            "409,corpus_event_mismatch,CORPUS_EVENT_MISMATCH,false,POST",
            "409,corpus_unavailable,CORPUS_UNAVAILABLE,false,POST",
            "409,catalog_cursor_mismatch,CATALOG_CURSOR_MISMATCH,false,GET",
            "503,corpus_unavailable,CORPUS_UNAVAILABLE,true,GET",
            "503,catalog_uninitialized,CATALOG_UNINITIALIZED,true,GET"
    })
    void recognizedFastApiCodeSurvivesTransportAndPublicHandler(int status, String upstreamCode,
            String expectedCode, boolean retryable, String method) throws Exception {
        try (var peer = new Peer(status, envelope(upstreamCode), "application/json")) {
            var client = peer.client(4096);
            MlException failure = assertThrows(MlException.class, () -> {
                if ("POST".equals(method)) client.generate(request());
                else new MaterialCatalogClient(client, JSON).page(null, null, 1);
            });
            assertEquals(expectedCode, failure.code().name());
            assertEquals(status, failure.upstreamStatus());
            assertEquals(retryable, failure.retryable());
            var response = new ApiErrorHandler().ml(failure, new MockHttpServletRequest());
            assertEquals(status, response.getStatusCode().value());
            assertEquals("ML_" + expectedCode, response.getBody().code());
            assertEquals(retryable, response.getBody().retryable());
            assertFalse(response.getBody().message().contains(POISON));
            assertFalse(failure.getMessage().contains(POISON));
            assertEquals(1, peer.calls.get(), "Errors must not retry the request");
            assertEquals(method, peer.method.get());
        }
    }

    @ParameterizedTest
    @MethodSource("unrecognizedBodies")
    void malformedOrUnrecognizedErrorsRetainGenericControlledFallback(int status, String body,
            String contentType, String expectedCode, int expectedStatus) throws Exception {
        try (var peer = new Peer(status, body, contentType)) {
            var error = assertThrows(MlException.class, () -> peer.client(4096).generate(request()));
            assertEquals(expectedCode, error.code().name());
            assertNull(error.upstreamStatus());
            var response = new ApiErrorHandler().ml(error, new MockHttpServletRequest());
            assertEquals(expectedStatus, response.getStatusCode().value());
            assertFalse(response.getBody().message().contains(POISON));
            assertEquals(1, peer.calls.get());
        }
    }

    static Stream<Arguments> unrecognizedBodies() {
        return Stream.of(
                Arguments.of(409, envelope("unknown_code"), "application/json", "REQUEST_REJECTED", 409),
                Arguments.of(409, "{\"detail\":\"" + POISON + "\"}", "application/json", "REQUEST_REJECTED", 409),
                Arguments.of(409, "{\"detail\":{\"code\":42}}", "application/json", "REQUEST_REJECTED", 409),
                Arguments.of(409, envelope("corpus_unavailable") + " trailing", "application/json", "REQUEST_REJECTED", 409),
                Arguments.of(409, "not JSON " + POISON, "application/json", "REQUEST_REJECTED", 409),
                Arguments.of(503, envelope("corpus_unavailable"), "text/plain", "UNAVAILABLE", 503),
                Arguments.of(503, envelope("corpus_event_mismatch"), "application/json", "UNAVAILABLE", 503),
                Arguments.of(409, envelope("catalog_uninitialized"), "application/json", "REQUEST_REJECTED", 409),
                Arguments.of(500, envelope("corpus_unavailable"), "application/json", "UNAVAILABLE", 503));
    }

    @Test
    void oversizedErrorUsesTheExistingBodyLimitWithoutLeakingOrRetrying() throws Exception {
        String body = "{\"detail\":{\"code\":\"corpus_unavailable\",\"message\":\"" + POISON.repeat(100) + "\"}}";
        try (var peer = new Peer(503, body, "application/json")) {
            var error = assertThrows(MlException.class, () -> peer.client(1024).generate(request()));
            assertEquals("INVALID_RESPONSE", error.code().name());
            assertFalse(error.getMessage().contains(POISON));
            assertEquals(1, peer.calls.get());
        }
    }

    @ParameterizedTest
    @ValueSource(ints = {409, 503})
    void realQueuedOperationPersistsCorpusCodeAndRetryability(int status) throws Exception {
        try (var database = DisposablePostgres.openOrSkip();
             var peer = new Peer(status, envelope("corpus_unavailable"), "application/json")) {
            var store = new OperationStore(database.jdbc(), JSON);
            var profiles = mock(CompanyProfileService.class);
            when(profiles.hasAccess("F_1", "U_1")).thenReturn(true);
            when(profiles.loadProfile(eq("U_1"), anyMap())).thenReturn(
                    new ProfileSnapshot(request().profile(), JSON.createObjectNode(), List.of(), true));
            var service = new InsightService(profiles, peer.client(4096), store, new InsightMapper(JSON), JSON,
                    peer.properties(4096), new MaterialCatalogRepository(database.jdbc(), JSON));
            try {
                service.ready();
                service.event(new EventInput("synthetic-error", "Synthetic error event", "Synthetic text.",
                        Instant.parse("2026-10-20T09:00:00Z"), new SourceInput("synthetic-source", "Source",
                        LocalDate.of(2026, 10, 20), null, "https://example.test/synthetic"), List.of(), List.of()));
                var input = new AnalysisInput("U_1", Role.cfo, "synthetic-error", Map.of());
                var submitted = service.analyze("F_1", input, "corpus-error");
                long deadline = System.nanoTime() + Duration.ofSeconds(5).toNanos();
                OperationStore.Operation finished;
                do {
                    finished = store.find(submitted.operationId()).orElseThrow();
                    if (!List.of("QUEUED", "RUNNING").contains(finished.status())) break;
                    Thread.sleep(10);
                } while (System.nanoTime() < deadline);
                assertEquals("FAILED", finished.status());
                assertEquals("ML_CORPUS_UNAVAILABLE", finished.errorCode());
                assertEquals(status == 503, finished.retryable());
                assertNull(finished.result());
                assertFalse(finished.errorMessage().contains(POISON));
                var publicState = service.operation("F_1", finished.id());
                assertEquals("ML_CORPUS_UNAVAILABLE", publicState.error().code());
                assertEquals(status == 503, publicState.error().retryable());
                assertEquals(finished.id(), service.analyze("F_1", input, "corpus-error").operationId());
                assertEquals(1, peer.calls.get(), "A failed operation replay must not repeat its POST");
            } finally {
                service.close();
            }
        }
    }

    private static String envelope(String code) {
        return "{\"detail\":{\"code\":\"" + code + "\",\"message\":\"" + POISON + "\"}}";
    }

    private static MlContracts.InsightRequest request() throws Exception {
        return JSON.readValue("""
                {"profile":{"company_id":"U_1","region":"Synthetic region"},"role":"cfo",
                 "event":{"event_id":"synthetic-error","title":"Synthetic event","text":"Synthetic text.",
                 "source":{"source_id":"synthetic-source","kind":"news","title":"Synthetic source"}}}
                """, MlContracts.InsightRequest.class);
    }

    private static final class Peer implements AutoCloseable {
        private final HttpServer server;
        private final AtomicInteger calls = new AtomicInteger();
        private final AtomicReference<String> method = new AtomicReference<>();

        Peer(int status, String body, String contentType) throws Exception {
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", exchange -> {
                try (exchange) {
                    calls.incrementAndGet();
                    method.set(exchange.getRequestMethod());
                    exchange.getRequestBody().readAllBytes();
                    byte[] bytes = body.getBytes(StandardCharsets.UTF_8);
                    exchange.getResponseHeaders().add("Content-Type", contentType);
                    exchange.sendResponseHeaders(status, bytes.length);
                    exchange.getResponseBody().write(bytes);
                }
            });
            server.start();
        }

        BackendProperties properties(int maxBytes) {
            return new BackendProperties(new BackendProperties.Ml(URI.create("http://127.0.0.1:" + server.getAddress().getPort()),
                    Duration.ofSeconds(1), Duration.ofSeconds(3), maxBytes),
                    new BackendProperties.Jobs(1, 4, Duration.ofSeconds(3)),
                    new BackendProperties.Auth("synthetic-unused-test-token", "F_1"));
        }

        HttpMlClient client(int maxBytes) { return new HttpMlClient(JSON, properties(maxBytes)); }
        @Override public void close() { server.stop(0); }
    }
}
