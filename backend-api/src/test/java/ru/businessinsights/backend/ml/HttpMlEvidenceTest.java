package ru.businessinsights.backend.ml;

import com.sun.net.httpserver.HttpServer;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import ru.businessinsights.backend.config.BackendProperties;
import java.net.InetSocketAddress;
import java.net.URI;
import java.time.Duration;
import java.util.function.Consumer;
import java.util.stream.Stream;
import org.junit.jupiter.params.ParameterizedTest;
import org.junit.jupiter.params.provider.Arguments;
import org.junit.jupiter.params.provider.MethodSource;
import static org.junit.jupiter.api.Assertions.*;

class HttpMlEvidenceTest {
    private MlContracts.Insight generate(Consumer<ObjectNode> change) throws Exception {
        return generate(change, r -> {});
    }
    private MlContracts.Insight generate(Consumer<ObjectNode> change, Consumer<ObjectNode> requestChange) throws Exception {
        var response = MlEvidenceContractTest.fixture(); change.accept(response);
        var server = HttpServer.create(new InetSocketAddress("127.0.0.1", 0), 0);
        server.createContext("/v1/insights", exchange -> {
            exchange.getRequestBody().readAllBytes();
            byte[] bytes = MlEvidenceContractTest.JSON.writeValueAsBytes(response);
            exchange.getResponseHeaders().set("Content-Type", "application/json");
            exchange.sendResponseHeaders(200, bytes.length);
            exchange.getResponseBody().write(bytes); exchange.close();
        });
        server.start();
        try {
            var properties = new BackendProperties(new BackendProperties.Ml(URI.create("http://127.0.0.1:" + server.getAddress().getPort()),
                    Duration.ofSeconds(1), Duration.ofSeconds(2), 100000), new BackendProperties.Jobs(1, 2, Duration.ZERO),
                    new BackendProperties.Auth("", "F_1"));
            var client = new HttpMlClient(MlEvidenceContractTest.JSON, properties);
            var requestJson = (ObjectNode)MlEvidenceContractTest.JSON.readTree("""
                    {"profile":{"company_id":"U_1","products":{"loans":false}},"role":"ceo",
                     "event":{"event_id":"opaque:event:1","title":"Ставка","text":"Ставка не изменилась.",
                     "source":{"source_id":"source:opaque:1","kind":"news","title":"Источник"}}}
                    """);
            requestChange.accept(requestJson);
            return client.generate(MlEvidenceContractTest.JSON.treeToValue(requestJson, MlContracts.InsightRequest.class));
        } finally { server.stop(0); }
    }
    @Test void acceptsCurrentOutcomeWithoutLosingClaimIds() throws Exception {
        var response = generate(r -> {});
        assertEquals("limited", MlEvidenceContractTest.JSON.valueToTree(response).path("outcome").asText());
        assertEquals("claim:one", MlEvidenceContractTest.JSON.valueToTree(response).at("/what_happened/0/claim_id").asText());
    }
    @Test void rejectsResponseFromAnotherPinnedCorpus() {
        var error = assertThrows(MlException.class, () -> generate(r -> {}, r -> r.put("corpus_revision", "d".repeat(64))));
        assertEquals(MlException.Code.INVALID_RESPONSE, error.code());
    }
    @Test void rejectsAbsentEvidenceReference() {
        var error = assertThrows(MlException.class, () -> generate(r -> ((ObjectNode)r.path("what_happened").get(0)).putArray("source_ids").add("absent")));
        assertEquals(MlException.Code.INVALID_RESPONSE, error.code());
    }
    @Test void rejectsFalseProfileFlagAsSupport() {
        var error = assertThrows(MlException.class, () -> generate(r -> ((ObjectNode)r.path("what_happened").get(0)).putArray("profile_features").add("products.loans")));
        assertEquals(MlException.Code.INVALID_RESPONSE, error.code());
    }
    @Test void rejectsConflictWithAbsentEvidence() {
        var error = assertThrows(MlException.class, () -> generate(r -> ((ObjectNode)r.path("conflicts").get(0)).putArray("source_ids").add("absent")));
        assertEquals(MlException.Code.INVALID_RESPONSE, error.code());
    }
    @Test void rejectsMalformedSourceHash() {
        var error = assertThrows(MlException.class, () -> generate(r -> ((ObjectNode)r.path("sources").get(0)).put("content_sha256", "not-a-hash")));
        assertEquals(MlException.Code.INVALID_RESPONSE, error.code());
    }
    @Test void rejectsInvalidPhysicalPage() {
        var error = assertThrows(MlException.class, () -> generate(r -> ((ObjectNode)r.path("sources").get(0)).put("page", 0)));
        assertEquals(MlException.Code.INVALID_RESPONSE, error.code());
    }
    @Test void rejectsWrongSchemaType() {
        assertThrows(MlException.class, () -> generate(r -> r.put("what_happened", "not an array")));
    }

    @Test void preservesExplicitEvidenceSpanWithoutReslicingClaimText() throws Exception {
        var response = generate(r -> span(r));
        var claim = MlEvidenceContractTest.JSON.valueToTree(response).at("/what_happened/0");
        assertEquals("source:opaque:1", claim.at("/evidence_spans/0/source_id").asText());
        assertEquals("a".repeat(64), claim.at("/evidence_spans/0/content_sha256").asText());
        assertEquals(2, claim.at("/evidence_spans/0/start").asInt());
        assertEquals(23, claim.at("/evidence_spans/0/end").asInt());
        assertEquals("Ставка не изменилась.", claim.path("text").asText());
    }

    @Test void acceptsLegacySourceWithoutDigestWhenClaimCarriesExplicitSpanDigest() throws Exception {
        var response = generate(r -> { span(r); ((ObjectNode) r.path("sources").get(0)).remove("content_sha256"); });
        assertEquals("a".repeat(64), MlEvidenceContractTest.JSON.valueToTree(response).at("/what_happened/0/evidence_spans/0/content_sha256").asText());
    }

    @ParameterizedTest(name = "{0}")
    @MethodSource("invalidSpans")
    void rejectsInvalidEvidenceSpansBeforeReturningTypedInsight(String label, Consumer<ObjectNode> mutation) {
        var failure = assertThrows(MlException.class, () -> generate(r -> mutation.accept(span(r))), label);
        assertEquals(MlException.Code.INVALID_RESPONSE, failure.code());
    }

    static Stream<Arguments> invalidSpans() {
        return Stream.of(
                Arguments.of("negative start", (Consumer<ObjectNode>) s -> s.put("start", -1)),
                Arguments.of("zero end", (Consumer<ObjectNode>) s -> s.put("end", 0)),
                Arguments.of("empty span", (Consumer<ObjectNode>) s -> s.put("end", 2)),
                Arguments.of("reversed bounds", (Consumer<ObjectNode>) s -> s.put("end", 1)),
                Arguments.of("fractional start", (Consumer<ObjectNode>) s -> s.put("start", 2.5)),
                Arguments.of("string offset", (Consumer<ObjectNode>) s -> s.put("start", "2")),
                Arguments.of("boolean offset", (Consumer<ObjectNode>) s -> s.put("end", true)),
                Arguments.of("missing offset", (Consumer<ObjectNode>) s -> s.remove("start")),
                Arguments.of("unknown field", (Consumer<ObjectNode>) s -> s.put("page", 7)),
                Arguments.of("malformed hash", (Consumer<ObjectNode>) s -> s.put("content_sha256", "wrong")),
                Arguments.of("hash mismatch", (Consumer<ObjectNode>) s -> s.put("content_sha256", "d".repeat(64))),
                Arguments.of("empty source", (Consumer<ObjectNode>) s -> s.put("source_id", "")),
                Arguments.of("non-string source", (Consumer<ObjectNode>) s -> s.put("source_id", 7)),
                Arguments.of("foreign source", (Consumer<ObjectNode>) s -> s.put("source_id", "absent")),
                Arguments.of("uncited source", (Consumer<ObjectNode>) s -> s.put("source_id", "source:opaque:2")));
    }

    private static ObjectNode span(ObjectNode response) {
        return ((ObjectNode) response.path("what_happened").get(0)).putArray("evidence_spans").addObject()
                .put("source_id", "source:opaque:1").put("content_sha256", "a".repeat(64)).put("start", 2).put("end", 23);
    }
}
