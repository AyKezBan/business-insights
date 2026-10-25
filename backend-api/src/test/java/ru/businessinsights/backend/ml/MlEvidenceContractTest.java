package ru.businessinsights.backend.ml;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import ru.businessinsights.backend.service.InsightMapper;
import ru.businessinsights.backend.store.OperationStore.Operation;
import java.time.Instant;
import java.util.List;
import java.util.UUID;
import static org.junit.jupiter.api.Assertions.*;

class MlEvidenceContractTest {
    static final ObjectMapper JSON = new ObjectMapper().findAndRegisterModules().enable(com.fasterxml.jackson.databind.DeserializationFeature.USE_BIG_DECIMAL_FOR_FLOATS).disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
    static ObjectNode fixture() throws Exception {
        return (ObjectNode) JSON.readTree(MlEvidenceContractTest.class.getResourceAsStream("/evidence-insight.json"));
    }
    static Operation operation(JsonNode result) {
        return new Operation(UUID.fromString("10000000-0000-0000-0000-000000000001"), "F_1", "U_1", "ceo", "ANALYSIS", null,
                "opaque:event:1", "LIMITED", JSON.createObjectNode(), JSON.createObjectNode(), result, null, "PENDING", List.of(),
                null, null, false, Instant.parse("2026-10-26T09:00:00Z"), Instant.parse("2026-10-26T09:00:00Z"));
    }
    @Test void allPinnedFieldsHaveTypedRecordComponents() throws Exception {
        var schema = JSON.readTree(getClass().getResourceAsStream("/ml-contracts/insight.schema.json"));
        for (Class<?> type : List.of(MlContracts.SourceRef.class, MlContracts.Claim.class, MlContracts.Impact.class, MlContracts.Insight.class)) {
            var fields = new java.util.HashSet<String>();
            for (var component : type.getRecordComponents())
                fields.add(com.fasterxml.jackson.databind.PropertyNamingStrategies.SnakeCaseStrategy.INSTANCE.translate(component.getName()));
            var modelSchema = type == MlContracts.Insight.class ? schema : schema.path("$defs").path(type.getSimpleName());
            modelSchema.path("properties").fieldNames().forEachRemaining(field -> assertTrue(fields.contains(field), type.getSimpleName() + "." + field + " must not be silently ignored"));
        }
    }

    @Test void typedRoundTripPreservesEvidenceAndRunContext() throws Exception {
        var input = fixture();
        var stored = JSON.readTree(JSON.writeValueAsString(JSON.treeToValue(input, MlContracts.Insight.class)));
        for (String field : List.of("outcome", "error_code", "request_id", "parent_run_id", "as_of", "corpus_revision", "event_content_sha256", "conflicts"))
            assertEquals(input.get(field), stored.get(field), field);
        for (int i = 0; i < input.path("sources").size(); i++) {
            var fields = input.path("sources").get(i).fields();
            while (fields.hasNext()) { var field = fields.next(); assertEquals(field.getValue(), stored.path("sources").get(i).get(field.getKey()), field.getKey()); }
        }
        assertEquals(input.get("what_happened"), stored.get("what_happened"));
        assertEquals(2, stored.path("what_happened").size());
    }
    @Test void apiMappingPreservesClaimsSourceVersionsAndContext() throws Exception {
        var view = JSON.valueToTree(new InsightMapper(JSON).insight(operation(fixture()), new ru.businessinsights.backend.store.OperationStore.Interaction(false, null, null)));
        assertEquals("claim:one", view.at("/whatHappened/0/claimId").asText());
        assertEquals("doc:opaque:1", view.at("/sources/0/docId").asText());
        assertEquals("a".repeat(64), view.at("/sources/0/contentSha256").asText());
        assertEquals("cbr", view.at("/sources/0/publisherId").asText());
        assertEquals("Europe/Moscow", view.at("/sources/0/sourceTimezone").asText());
        assertEquals(10800, view.at("/sources/0/sourceUtcOffsetSeconds").asInt());
        assertEquals("date", view.at("/sources/0/publishedPrecision").asText());
        assertEquals("2026-10-25T12:30:00Z", view.at("/sources/0/firstSeenAt").asText());
        assertEquals("2026-10-26T08:00:00Z", view.at("/sources/0/retrievedAt").asText());
        assertEquals("limited", view.path("outcome").asText());
        assertEquals("revision-context", view.path("corpusRevision").asText());
        assertEquals(1, view.path("conflicts").size());
    }
    @Test void profileMetadataAndDecimalSurviveWireRoundTrip() throws Exception {
        var input = JSON.readTree("""
                {"company_id":"U_1","revenue_rub":999.99,"revenue_year":2025,"revenue_status":"known","revenue_scope":"company",
                 "foreign_trade_data_available":false,"payment_period":"2025-Q4","profile_quality_notes":["coverage_unknown"],
                 "products":{"loans":false},"counterparties":[{"company_id":"U_2","direction":"outgoing","amount_rub":123456789012345678.12}]}
                """);
        var output = JSON.valueToTree(JSON.treeToValue(input, MlContracts.CompanyProfile.class));
        for (String field : List.of("revenue_year", "revenue_status", "revenue_scope", "foreign_trade_data_available", "payment_period", "profile_quality_notes"))
            assertEquals(input.get(field), output.get(field), field);
        assertEquals("123456789012345678.12", output.at("/counterparties/0/amount_rub").asText());
        assertEquals("999.99", output.path("revenue_rub").asText());
    }
    @Test void operationPreservesFailureReasonAndOutcome() throws Exception {
        var data = fixture(); data.put("status", "failed").put("outcome", "none").put("error_code", "CORPUS_UNAVAILABLE");
        var op = operation(data);
        op = new Operation(op.id(), op.ownerId(), op.companyId(), op.role(), op.kind(), op.parentId(), op.eventId(), "FAILED", op.payload(), op.profileSnapshot(),
                data, op.trace(), op.traceState(), op.warnings(), null, null, false, op.createdAt(), op.updatedAt());
        var output = JSON.valueToTree(new InsightMapper(JSON).operation(op));
        assertEquals("CORPUS_UNAVAILABLE", output.at("/error/code").asText());
        assertEquals("none", output.path("outcome").asText());
    }
    @Test void answerPreservesSavedContext() throws Exception {
        var input = JSON.readTree("""
                {"insight_id":"ins_1","run_id":"run_qa1","company_id":"U_1","event_id":"opaque:event:1","status":"no_answer",
                 "text":"Недостаточно оснований","claims":[],"sources":[],"missing_data":["missing corpus"],
                 "corpus_revision":"revision-context","event_content_sha256":"bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb","as_of":"2026-10-25T10:00:00Z"}
                """);
        var output = JSON.valueToTree(JSON.treeToValue(input, MlContracts.Answer.class));
        for (String field : List.of("company_id", "event_id", "corpus_revision", "event_content_sha256", "as_of"))
            assertEquals(input.get(field), output.get(field), field);
    }
}
