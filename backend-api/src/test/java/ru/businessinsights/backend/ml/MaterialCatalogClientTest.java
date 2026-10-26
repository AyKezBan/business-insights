package ru.businessinsights.backend.ml;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.node.ObjectNode;
import com.fasterxml.jackson.databind.node.ArrayNode;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import com.sun.net.httpserver.HttpServer;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import ru.businessinsights.backend.config.BackendProperties;

import java.net.InetSocketAddress;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.time.Duration;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.concurrent.atomic.AtomicReference;
import java.util.function.Consumer;
import java.util.stream.Stream;

import static org.junit.jupiter.api.Assertions.*;

class MaterialCatalogClientTest {
    private static final ObjectMapper JSON = new ObjectMapper().registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    private static final String REVISION = "a".repeat(64);

    @Test
    void pinnedNewsSchemaRejectsMalformedSourceDigestBeforeDtoDecoding() throws Exception {
        JsonNode request = JSON.readTree("""
                {"profile":{"company_id":"U_1"},"role":"cfo",
                 "event":{"event_id":"event:opaque","title":"Synthetic event","text":"Synthetic source only",
                   "source":{"source_id":"source:opaque","kind":"news","title":"Source","content_sha256":"not-a-sha"}}}
                """);
        assertThrows(IllegalArgumentException.class, () -> new PinnedSchemaValidator(JSON).validate("insight-request", request));
    }

    @Test
    void pageUsesExistingGetTransportAndPreservesAllDeclaredSourceMetadata() throws Exception {
        ObjectNode body = envelope();
        try (var server = new Stub(body)) {
            var result = server.client(100000).page(REVISION, "opaque+/=&?", 2);
            assertEquals("GET", server.method.get());
            assertEquals("/v1/materials/events", server.request.get().getRawPath());
            assertEquals("revision=" + REVISION + "&cursor=opaque%2B%2F%3D%26%3F&limit=2", server.request.get().getRawQuery());
            assertEquals("synthetic-store", result.token().storeId());
            assertEquals(7, result.token().generation());
            assertEquals(REVISION, result.token().revision());
            assertEquals("next+opaque/=", result.nextCursor());
            assertEquals(1, result.events().size());
            assertEquals("b".repeat(64), result.events().getFirst().eventContentSha256());
            JsonNode actual = JSON.valueToTree(result.events().getFirst().event());
            source(body).fields().forEachRemaining(field -> assertEquals(field.getValue(), actual.path("source").get(field.getKey()), field.getKey()));
            assertEquals(body.at("/events/0/event/conflicts"), actual.path("conflicts"));
            assertEquals("source:second", result.events().getFirst().event().relatedSources().getFirst().sourceId());
            assertThrows(UnsupportedOperationException.class, () -> result.events().clear());
            assertEquals(1, server.calls.get());
        }
    }

    @Test
    void singleEventTreatsItsIdAsOneEncodedOpaquePathSegment() throws Exception {
        ObjectNode body = envelope();
        String eventId = "opaque:event/ю?x=1#frag";
        event(body).put("event_id", eventId);
        body.putNull("next_cursor");
        try (var server = new Stub(body)) {
            var result = server.client(100000).event(eventId, REVISION);
            assertEquals("/v1/materials/events/opaque%3Aevent%2F%D1%8E%3Fx%3D1%23frag", server.request.get().getRawPath());
            assertEquals("revision=" + REVISION, server.request.get().getRawQuery());
            assertEquals(eventId, result.events().getFirst().event().eventId());
            assertNull(result.nextCursor());
        }
    }

    @Test
    void unpinnedRequestAndExplicitHistoricalGenerationZeroRemainReadable() throws Exception {
        ObjectNode body = envelope();
        token(body).put("generation", 0);
        body.putNull("next_cursor");
        try (var server = new Stub(body)) {
            var client = server.client(100000);
            assertEquals(0, client.page(REVISION, null, 100).token().generation());
            client.page(null, null, 50);
            assertEquals("limit=50", server.request.get().getRawQuery());
        }
    }

    @Test
    void rejectsRequestedRevisionDriftWithoutReturningMixedContext() throws Exception {
        try (var server = new Stub(envelope())) {
            var error = assertThrows(MlException.class, () -> server.client(100000).page("c".repeat(64), null, 10));
            assertEquals(MlException.Code.INVALID_RESPONSE, error.code());
            assertEquals(1, server.calls.get());
        }
    }

    @Test
    void boundsAndValidatesInputsBeforeAnyHttpCall() throws Exception {
        try (var server = new Stub(envelope())) {
            var client = server.client(100000);
            assertAll(
                    () -> rejectedRequest(() -> client.page("bad", null, 1)),
                    () -> rejectedRequest(() -> client.page(null, null, 0)),
                    () -> rejectedRequest(() -> client.page(null, null, 101)),
                    () -> rejectedRequest(() -> client.page(null, "", 1)),
                    () -> rejectedRequest(() -> client.page(null, "x".repeat(4097), 1)),
                    () -> rejectedRequest(() -> client.page(null, "bad\nheader", 1)),
                    () -> rejectedRequest(() -> client.event(null, null)),
                    () -> rejectedRequest(() -> client.event(" ", null)),
                    () -> rejectedRequest(() -> client.event("x".repeat(201), null)),
                    () -> rejectedRequest(() -> client.event("event", "bad")));
            assertEquals(0, server.calls.get());
        }
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("malformedPages")
    void rejectsMalformedCatalogEnvelopeAndFullNewsSchema(String label, Consumer<ObjectNode> mutation) throws Exception {
        ObjectNode body = envelope(); mutation.accept(body);
        try (var server = new Stub(body)) {
            var error = assertThrows(MlException.class, () -> server.client(100000).page(REVISION, null, 2), label);
            assertEquals(MlException.Code.INVALID_RESPONSE, error.code(), label);
            assertEquals(1, server.calls.get());
        }
    }

    static Stream<Arguments> malformedPages() {
        return Stream.of(
                bad("missing envelope token", root -> root.remove("catalog_token")),
                bad("wrong envelope events type", root -> root.put("events", "wrong")),
                bad("missing explicit cursor", root -> root.remove("next_cursor")),
                bad("wrong cursor type", root -> root.put("next_cursor", 12)),
                bad("blank cursor", root -> root.put("next_cursor", "")),
                bad("oversized cursor", root -> root.put("next_cursor", "x".repeat(4097))),
                bad("token extra fields", root -> token(root).put("other", true)),
                bad("numeric store ID", root -> token(root).put("store_id", 12)),
                bad("blank store ID", root -> token(root).put("store_id", " ")),
                bad("fractional generation", root -> token(root).put("generation", 7.5)),
                bad("string generation", root -> token(root).put("generation", "7")),
                bad("negative generation", root -> token(root).put("generation", -1)),
                bad("generation overflow", root -> token(root).put("generation", new java.math.BigInteger("9223372036854775808"))),
                bad("malformed token revision", root -> token(root).put("revision", "bad")),
                bad("malformed semantic hash", root -> ((ObjectNode)root.at("/events/0")).put("event_content_sha256", "bad")),
                bad("missing news text", root -> event(root).remove("text")),
                bad("malformed source hash", root -> source(root).put("content_sha256", "bad")),
                bad("missing source title", root -> source(root).remove("title")),
                bad("non-news primary", root -> source(root).put("kind", "bank")),
                bad("non-news related source", root -> ((ObjectNode)event(root).path("related_sources").get(0)).put("kind", "profile")),
                bad("invalid physical page", root -> source(root).put("page", 0)),
                bad("invalid precision enum", root -> source(root).put("published_precision", "instant")),
                bad("naive observed timestamp", root -> source(root).put("first_seen_at", "2026-10-20T09:00:00")),
                bad("offset outside one day", root -> source(root).put("source_utc_offset_seconds", 86400)),
                bad("duplicate event", root -> ((ArrayNode)root.get("events")).add(root.at("/events/0").deepCopy())),
                bad("duplicate source", root -> ((ObjectNode)event(root).path("related_sources").get(0)).put("source_id", "source:opaque")),
                bad("conflict references missing source", root -> ((ObjectNode)event(root).path("conflicts").get(0)).putArray("source_ids").add("missing")),
                bad("empty page has continuation", root -> root.putArray("events")));
    }

    @Test
    void rejectsPageLargerThanRequestedLimitAndNonAdvancingCursor() throws Exception {
        ObjectNode body = envelope();
        ObjectNode another = body.at("/events/0").deepCopy();
        ((ObjectNode)another.get("event")).put("event_id", "second-event");
        ((ArrayNode)body.get("events")).add(another);
        try (var server = new Stub(body)) {
            var client = server.client(100000);
            assertEquals(MlException.Code.INVALID_RESPONSE,
                    assertThrows(MlException.class, () -> client.page(REVISION, null, 1)).code());
            assertEquals(MlException.Code.INVALID_RESPONSE,
                    assertThrows(MlException.class, () -> client.page(REVISION, "next+opaque/=", 2)).code());
        }
    }

    @ParameterizedTest(name = "single event: {0}")
    @MethodSource("invalidSingleEvents")
    void singleEndpointRequiresExactlyMatchingEventAndNoContinuation(String label, Consumer<ObjectNode> mutation) throws Exception {
        ObjectNode body = envelope(); body.putNull("next_cursor"); mutation.accept(body);
        try (var server = new Stub(body)) {
            assertEquals(MlException.Code.INVALID_RESPONSE,
                    assertThrows(MlException.class, () -> server.client(100000).event("event:opaque", REVISION)).code());
        }
    }

    static Stream<Arguments> invalidSingleEvents() {
        return Stream.of(bad("wrong identity", root -> event(root).put("event_id", "other")),
                bad("empty list", root -> root.putArray("events")),
                bad("multiple items", root -> ((ArrayNode)root.get("events")).add(root.at("/events/0").deepCopy())),
                bad("non-null cursor", root -> root.put("next_cursor", "next")));
    }

    @Test
    void inheritedTransportBoundsBytesRejectsRedirectsAndDoesNotRetryUpstreamFailures() throws Exception {
        try (var server = new Stub(envelope())) {
            assertEquals(MlException.Code.INVALID_RESPONSE,
                    assertThrows(MlException.class, () -> server.client(1024).page(null, null, 1)).code());
            server.status = 302;
            assertEquals(MlException.Code.INVALID_RESPONSE,
                    assertThrows(MlException.class, () -> server.client(100000).page(null, null, 1)).code());
            server.status = 503;
            assertEquals(MlException.Code.UNAVAILABLE,
                    assertThrows(MlException.class, () -> server.client(100000).page(null, null, 1)).code());
            server.status = 409;
            assertEquals(MlException.Code.REQUEST_REJECTED,
                    assertThrows(MlException.class, () -> server.client(100000).page(null, null, 1)).code());
            server.status = 200; server.contentType = "text/html";
            assertEquals(MlException.Code.INVALID_RESPONSE,
                    assertThrows(MlException.class, () -> server.client(100000).page(null, null, 1)).code());
            assertEquals(5, server.calls.get(), "Each call must issue exactly one GET, with no redirect/retry");
        }
    }

    private static void rejectedRequest(org.junit.jupiter.api.function.Executable call) {
        assertEquals(MlException.Code.REQUEST_REJECTED, assertThrows(MlException.class, call).code());
    }

    private static Arguments bad(String label, Consumer<ObjectNode> mutation) { return Arguments.of(label, mutation); }
    private static ObjectNode token(ObjectNode root) { return (ObjectNode)root.get("catalog_token"); }
    private static ObjectNode event(ObjectNode root) { return (ObjectNode)root.at("/events/0/event"); }
    private static ObjectNode source(ObjectNode root) { return (ObjectNode)root.at("/events/0/event/source"); }

    private static ObjectNode envelope() throws Exception {
        ObjectNode root = (ObjectNode)JSON.readTree("""
                {"catalog_token":{"store_id":"synthetic-store","generation":7,"revision":""},
                 "events":[{"event_content_sha256":"","event":{"event_id":"event:opaque","title":"Synthetic event","text":"Synthetic source only",
                   "published_at":"2026-10-20T09:00:00Z","published_date":"2026-10-20","published_precision":"timestamp",
                   "source":{"source_id":"source:opaque","kind":"news","title":"Synthetic original","date":"2026-10-20","page":3,
                     "url":"https://example.invalid/original?article=opaque","doc_id":"document:opaque","content_sha256":"","excerpt":"Quoted synthetic source",
                     "publisher_id":"SYNTHETIC","adapter_id":"synthetic-v1","material_type":"public_analysis","completeness":"full","language":"en",
                     "published_date":"2026-10-20","published_precision":"timestamp","first_seen_at":"2026-10-20T10:00:00Z",
                     "retrieved_at":"2026-10-21T10:00:00Z","statement_type":"forecast","target_period":"2027","valid_until":"2027-12-31",
                     "rights_url":"https://example.invalid/rights","translation_attribution":"Synthetic attribution","raw_publication":"20 October 2026",
                     "source_timezone":"UTC","source_utc_offset_seconds":0,"timezone_provenance":"explicit_offset"},
                   "related_sources":[{"source_id":"source:second","kind":"news","title":"Other synthetic source"}],
                   "topics":["synthetic"],"conflicts":[{"reason":"source_disagreement","source_ids":["source:opaque","source:second"],"resolved":false}]}}],
                 "next_cursor":"next+opaque/="}
                """);
        token(root).put("revision", REVISION);
        ((ObjectNode)root.at("/events/0")).put("event_content_sha256", "b".repeat(64));
        source(root).put("content_sha256", "c".repeat(64));
        return root;
    }

    private static final class Stub implements AutoCloseable {
        final HttpServer server;
        final AtomicInteger calls = new AtomicInteger();
        final AtomicReference<URI> request = new AtomicReference<>();
        final AtomicReference<String> method = new AtomicReference<>();
        volatile int status = 200;
        volatile String contentType = "application/json";

        Stub(ObjectNode body) throws Exception {
            byte[] bytes = JSON.writeValueAsString(body).getBytes(StandardCharsets.UTF_8);
            server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
            server.createContext("/", exchange -> {
                calls.incrementAndGet(); request.set(exchange.getRequestURI()); method.set(exchange.getRequestMethod());
                exchange.getRequestBody().readAllBytes();
                exchange.getResponseHeaders().set("Content-Type", contentType);
                if (status == 302) exchange.getResponseHeaders().set("Location", "/redirected");
                exchange.sendResponseHeaders(status, bytes.length);
                try { exchange.getResponseBody().write(bytes); } finally { exchange.close(); }
            });
            server.start();
        }

        MaterialCatalogClient client(int maxBytes) {
            var properties = new BackendProperties(new BackendProperties.Ml(
                    URI.create("http://127.0.0.1:" + server.getAddress().getPort()), Duration.ofSeconds(1), Duration.ofSeconds(2), maxBytes),
                    new BackendProperties.Jobs(1, 2, Duration.ZERO), new BackendProperties.Auth("", "F_1"));
            return new MaterialCatalogClient(new HttpMlClient(JSON, properties), JSON);
        }

        @Override public void close() { server.stop(0); }
    }
}
