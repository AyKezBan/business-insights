package ru.businessinsights.backend.service;

import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.databind.SerializationFeature;
import com.fasterxml.jackson.databind.node.ObjectNode;
import org.junit.jupiter.api.Test;
import ru.businessinsights.backend.api.ApiModels.*;
import ru.businessinsights.backend.config.BackendProperties;
import ru.businessinsights.backend.data.CompanyProfileService;
import ru.businessinsights.backend.data.ProfileSnapshot;
import ru.businessinsights.backend.ml.MlClient;
import ru.businessinsights.backend.ml.MlContracts;
import ru.businessinsights.backend.store.OperationStore;
import ru.businessinsights.backend.testsupport.DisposablePostgres;
import java.net.URI;
import java.time.Duration;
import java.util.List;
import java.util.Map;
import static org.junit.jupiter.api.Assertions.*;
import static org.mockito.Mockito.*;

class CurrentMlOutcomeTest {
    @Test void currentCompleteResultIsNotDowngradedByObsoleteMlWarnings() throws Exception {
        try (var db = DisposablePostgres.openOrSkip()) {
            var mapper = new ObjectMapper().findAndRegisterModules().disable(SerializationFeature.WRITE_DATES_AS_TIMESTAMPS);
            var store = new OperationStore(db.jdbc(), mapper);
            var profiles = mock(CompanyProfileService.class);
            when(profiles.hasAccess("F_1", "U_1")).thenReturn(true);
            var profile = mapper.readValue("""
                    {"company_id":"U_1","revenue_rub":0,"revenue_status":"known","revenue_scope":"company","revenue_year":2025}
                    """, MlContracts.CompanyProfile.class);
            when(profiles.loadProfile("U_1", Map.of())).thenReturn(new ProfileSnapshot(profile, mapper.createObjectNode(), List.of(), true));
            var ml = mock(MlClient.class);
            var result = (ObjectNode)mapper.readTree(getClass().getResourceAsStream("/evidence-insight.json"));
            result.put("status", "complete").put("outcome", "full");
            when(ml.generate(any())).thenReturn(mapper.treeToValue(result, MlContracts.Insight.class));
            when(ml.trace(anyString())).thenReturn(List.of());
            var properties = new BackendProperties(new BackendProperties.Ml(URI.create("http://127.0.0.1:1"),Duration.ofSeconds(1),Duration.ofSeconds(1),10000),
                    new BackendProperties.Jobs(1, 2, Duration.ofSeconds(1)), new BackendProperties.Auth("", "F_1"));
            var service = new InsightService(profiles, ml, store, new InsightMapper(mapper), mapper, properties);
            try {
                service.ready();
                service.event(new EventInput("opaque:event:1", "Event", "News text", null, new SourceInput("source:opaque:1", "Source", null, null, null), List.of(), List.of()));
                var submitted = service.analyze("F_1", new AnalysisInput("U_1", Role.ceo, "opaque:event:1", Map.of()), "current-ml");
                long until = System.nanoTime() + Duration.ofSeconds(3).toNanos();
                OperationStore.Operation operation;
                do { operation = store.find(submitted.operationId()).orElseThrow(); if (!List.of("QUEUED", "RUNNING").contains(operation.status())) break; Thread.sleep(10); } while (System.nanoTime() < until);
                assertEquals("SUCCEEDED", operation.status(), operation.warnings().toString());
                assertTrue(operation.warnings().isEmpty());
            } finally { service.close(); }
        }
    }
}
