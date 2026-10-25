package ru.businessinsights.backend.testsupport;

import com.fasterxml.jackson.databind.DeserializationFeature;
import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.datatype.jsr310.JavaTimeModule;
import org.junit.jupiter.api.AfterAll;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.TestInstance;
import ru.businessinsights.backend.api.ApiException;
import ru.businessinsights.backend.api.ApiModels.EventInput;
import ru.businessinsights.backend.api.ApiModels.SourceInput;
import ru.businessinsights.backend.service.InsightMapper;
import ru.businessinsights.backend.store.OperationStore;

import java.math.BigDecimal;
import java.time.Instant;
import java.time.LocalDate;
import java.util.List;
import java.util.UUID;

import static org.junit.jupiter.api.Assertions.*;

/** Actual PostgreSQL/JSONB checks; an absent BI_JDBC_TEST_URL is explicitly NOT_RUN. */
@TestInstance(TestInstance.Lifecycle.PER_CLASS)
class OperationStorePersistenceTest {
    private final ObjectMapper mapper = new ObjectMapper().registerModule(new JavaTimeModule())
            .disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS)
            .enable(DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS);
    private DisposablePostgres database;
    private OperationStore store;

    @BeforeEach
    void createOwnedDatabase() throws Exception {
        if (database == null) {
            database = DisposablePostgres.openOrSkip();
            store = new OperationStore(database.jdbc(), mapper);
        }
    }

    @AfterAll
    void removeOnlyOwnedDatabase() throws Exception {
        if (database != null) {
            database.close();
        }
    }

    @Test
    void completedEvidenceAndInteractionStateSurviveStoreReconstruction() throws Exception {
        String eventId = registerEvent("persist");
        var submission = store.create("F_1", "U_1", "cfo", "ANALYSIS", null, eventId,
                "persistence-key", "immutable-request-hash", mapper.readTree("""
                    {"companyId":"U_1","role":"cfo","eventId":"persist","asOf":"2026-10-20T12:00:00Z"}
                    """));
        UUID id = submission.operation().id();
        assertTrue(store.start(id));
        store.saveProfile(id, mapper.readTree("""
                {"profile":{"company_id":"U_1","revenue_rub":999.125,"revenue_year":2025,
                  "revenue_status":"known","foreign_trade_data_available":false},
                 "corpus_revision":"revision-one","event_content_sha256":"semantic-hash-one"}
                """));
        store.finish(id, "SUCCEEDED", evidenceResult(eventId, "U_1", "cfo"), List.of());
        store.trace(id, mapper.readTree("""
                [{"run_id":"run_fixture","seq":1,"data":{"corpus_revision":"revision-one","source_id":"bank:opaque:fragment"}}]
                """), "AVAILABLE", List.of());
        store.open(id, "F_1");
        store.state(id, "F_1", true);
        store.feedback(id, "F_1", "USEFUL", "Synthetic persistence fixture");
        UUID action = store.action(id, "F_1", "REQUEST_ANALYSIS", "Synthetic local intent");

        // Reconstruct the repository and execute the real startup recovery path.
        var restarted = new OperationStore(database.jdbc(), mapper);
        restarted.interruptUnfinished();
        var saved = restarted.find(id).orElseThrow();
        assertEquals("SUCCEEDED", saved.status());
        assertEquals("AVAILABLE", saved.traceState());
        assertEquals("revision-one", saved.profileSnapshot().path("corpus_revision").asText());
        assertEquals(0, new BigDecimal("999.125").compareTo(saved.profileSnapshot().path("profile").path("revenue_rub").decimalValue()));
        assertFalse(saved.profileSnapshot().path("profile").path("foreign_trade_data_available").asBoolean(true));
        assertEquals("semantic-hash-one", saved.result().path("event_content_sha256").asText());
        assertEquals("claim-second", saved.result().path("what_happened").get(1).path("claim_id").asText());
        assertEquals("quoted fragment with condition", saved.result().path("sources").get(1).path("excerpt").asText());
        assertEquals("revision-one", saved.trace().get(0).path("data").path("corpus_revision").asText());
        assertEquals("2026-10-20T12:00:00Z", saved.payload().path("asOf").asText());

        var view = new InsightMapper(mapper).insight(saved, restarted.interaction(id, "F_1"));
        assertEquals(id, view.insightId());
        assertEquals(2, view.whatHappened().size());
        assertEquals(List.of("First independent assertion", "Second independent assertion"),
                view.whatHappened().stream().map(c -> c.text()).toList());
        assertEquals("https://example.test/v1/materials/documents/bank-document?revision=revision-one#page=7",
                view.sources().get(1).url());
        assertTrue(view.hidden());
        assertTrue(restarted.feed("F_1", "U_1", "cfo", 20, 0, false).isEmpty());
        assertEquals(List.of(id), restarted.feed("F_1", "U_1", "cfo", 20, 0, true).stream().map(OperationStore.Operation::id).toList());
        assertEquals("USEFUL", database.jdbc().queryForObject(
                "SELECT rating FROM backend_api.feedback WHERE operation_id=? AND owner_id='F_1'", String.class, id));
        assertEquals(Boolean.TRUE, database.jdbc().queryForObject(
                "SELECT opened_at IS NOT NULL FROM backend_api.insight_state WHERE operation_id=? AND owner_id='F_1'", Boolean.class, id));
        assertEquals("RECORDED", database.jdbc().queryForObject(
                "SELECT status FROM backend_api.action_intent WHERE id=?", String.class, action));
    }

    @Test
    void ownershipCompanyAndRoleFiltersDoNotExposeOtherPreparedInsights() throws Exception {
        String eventId = registerEvent("isolation");
        var owner = store.create("F_2", "U_2", "treasurer", "ANALYSIS", null, eventId,
                "shared-key", "request-two", mapper.createObjectNode());
        assertTrue(store.start(owner.operation().id()));
        store.finish(owner.operation().id(), "SUCCEEDED", evidenceResult(eventId, "U_2", "treasurer"), List.of());
        var other = store.create("F_3", "U_3", "ceo", "ANALYSIS", null, eventId,
                "shared-key", "request-three", mapper.createObjectNode());
        assertNotEquals(owner.operation().id(), other.operation().id());
        assertTrue(store.owned(owner.operation().id(), "F_3").isEmpty());
        assertTrue(store.feed("F_3", "U_2", "treasurer", 20, 0, true).isEmpty());
        assertTrue(store.feed("F_2", "U_3", "treasurer", 20, 0, true).isEmpty());
        assertTrue(store.feed("F_2", "U_2", "ceo", 20, 0, true).isEmpty());
        assertEquals(List.of(owner.operation().id()), store.feed("F_2", "U_2", "treasurer", 20, 0, true)
                .stream().map(OperationStore.Operation::id).toList());
    }

    @Test
    void repeatedKeyAfterRestartKeepsCompletedResultAndConflictsOnChangedInput() throws Exception {
        String eventId = registerEvent("idempotent");
        JsonNode payload = mapper.readTree("{\"requestId\":\"request-one\",\"corpusRevision\":\"revision-one\"}");
        var first = store.create("F_4", "U_4", "cfo", "ANALYSIS", null, eventId,
                "retry-key", "frozen-hash-one", payload);
        UUID id = first.operation().id();
        assertTrue(store.start(id));
        store.finish(id, "SUCCEEDED", evidenceResult(eventId, "U_4", "cfo"), List.of());
        var restarted = new OperationStore(database.jdbc(), mapper);
        restarted.interruptUnfinished();
        var replay = restarted.create("F_4", "U_4", "cfo", "ANALYSIS", null, eventId,
                "retry-key", "frozen-hash-one", payload);
        assertFalse(replay.created());
        assertEquals(id, replay.operation().id());
        assertEquals("SUCCEEDED", replay.operation().status());
        assertEquals("revision-one", replay.operation().result().path("corpus_revision").asText());
        ApiException conflict = assertThrows(ApiException.class, () -> restarted.create("F_4", "U_4", "ceo",
                "ANALYSIS", null, eventId, "retry-key", "changed-role-and-context", mapper.createObjectNode()));
        assertEquals("IDEMPOTENCY_CONFLICT", conflict.code());
        assertEquals(1, database.jdbc().queryForObject(
                "SELECT count(*) FROM backend_api.operation WHERE owner_id='F_4' AND request_key='retry-key'", Integer.class));
    }

    private String registerEvent(String suffix) {
        String eventId = "event-" + suffix;
        store.registerEvent(new EventInput(eventId, "Synthetic test event", "Synthetic text only",
                Instant.parse("2026-10-20T09:00:00Z"),
                new SourceInput("external:opaque:source", "Synthetic original", LocalDate.of(2026, 10, 20),
                        null, "https://example.test/original"), List.of(), List.of("fixture")), "hash-" + suffix);
        return eventId;
    }

    private JsonNode evidenceResult(String eventId, String company, String role) throws Exception {
        var fixture = (com.fasterxml.jackson.databind.node.ObjectNode) mapper.readTree("""
                {"insight_id":"ins_fixture","run_id":"run_fixture","event_id":"logical-event",
                 "company_id":"U_1","role":"cfo","status":"complete","outcome":"full",
                 "headline":"Synthetic evidence fixture","corpus_revision":"revision-one",
                 "event_content_sha256":"semantic-hash-one","impact_level":"medium","impact_rationale":"Synthetic only",
                 "what_happened":[
                   {"claim_id":"claim-first","text":"First independent assertion","kind":"fact","source_ids":["external:opaque:source"],"profile_features":[]},
                   {"claim_id":"claim-second","text":"Second independent assertion","kind":"fact","source_ids":["external:opaque:source"],"profile_features":[]}],
                 "why_it_matters":[],"consequences":[],"risks":[],"opportunities":[],"scenarios":[],"actions":[],"bank_view":[],
                 "model_vs_bank":[],"profile_features_used":[],"missing_data":[],"models":["synthetic-no-provider"],
                 "self_check":{"passed":true,"issues":[],"iterations":1,"adaptations":[]},
                 "generated_at":"2026-10-20T12:05:00Z",
                 "sources":[
                   {"source_id":"external:opaque:source","kind":"news","title":"Synthetic original","date":"2026-10-20","url":"https://example.test/original"},
                   {"source_id":"bank:opaque:fragment","kind":"bank","title":"Synthetic bank page","date":"2026-10-19","page":7,
                    "doc_id":"bank-document","content_sha256":"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                    "excerpt":"quoted fragment with condition",
                    "url":"https://example.test/v1/materials/documents/bank-document?revision=revision-one#page=7"}]}
                """);
        return fixture.put("event_id", eventId).put("company_id", company).put("role", role);
    }
}
