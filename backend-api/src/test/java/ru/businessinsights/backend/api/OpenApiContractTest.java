package ru.businessinsights.backend.api;

import org.junit.jupiter.api.Test;
import org.yaml.snakeyaml.LoaderOptions;
import org.yaml.snakeyaml.Yaml;
import org.yaml.snakeyaml.constructor.SafeConstructor;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.Set;
import java.util.stream.Collectors;
import static org.junit.jupiter.api.Assertions.*;

class OpenApiContractTest {
    @Test void batchRefreshReturnsASelectionWithoutAnAggregatePollingLocation() throws Exception {
        var service = org.mockito.Mockito.mock(ru.businessinsights.backend.service.InsightService.class);
        var input = new ApiModels.FeedRefreshInput(ApiModels.Role.cfo, null, null, null, null);
        org.mockito.Mockito.when(service.refresh("F_1", "U_1", input))
                .thenReturn(new ApiModels.FeedRefreshView("a".repeat(64), null, List.of()));
        var mvc = org.springframework.test.web.servlet.setup.MockMvcBuilders
                .standaloneSetup(new BackendController(service, null)).build();
        var response = mvc.perform(org.springframework.test.web.servlet.request.MockMvcRequestBuilders
                .post("/api/v1/companies/U_1/insights/refresh")
                .requestAttr(ru.businessinsights.backend.auth.DemoAuthFilter.PERSON, "F_1")
                .contentType(org.springframework.http.MediaType.APPLICATION_JSON).content("{\"role\":\"cfo\"}"))
                .andReturn().getResponse();
        assertEquals(200, response.getStatus());
        assertNull(response.getHeader("Location"));
        assertTrue(response.getContentAsString().contains("corpusRevision"));
        org.mockito.Mockito.verify(service).refresh("F_1", "U_1", input);
    }

    @Test void combinedApiHasAllDtoFieldsAndResolvableReferencesWithoutDuplicateKeys() throws Exception {
        var options = new LoaderOptions();
        options.setAllowDuplicateKeys(false);
        Map<?, ?> root;
        try (var input = getClass().getResourceAsStream("/static/openapi.yaml")) {
            assertNotNull(input);
            root = new Yaml(new SafeConstructor(options)).load(input);
        }
        visit(root, root);
        var schemas = (Map<?, ?>) ((Map<?, ?>) root.get("components")).get("schemas");
        Map<Class<?>, String> mappings = Map.ofEntries(
                Map.entry(ApiModels.SourceInput.class, "SourceInput"),
                Map.entry(ApiModels.SourceConflictInput.class, "SourceConflict"),
                Map.entry(ApiModels.EventInput.class, "Event"),
                Map.entry(ApiModels.EventVersionView.class, "EventVersion"),
                Map.entry(ApiModels.FeedRefreshView.class, "FeedRefresh"),
                Map.entry(ApiModels.SourceView.class, "Source"),
                Map.entry(ApiModels.InsightView.class, "Insight"),
                Map.entry(ApiModels.OperationView.class, "Operation"),
                Map.entry(ApiModels.AnswerView.class, "Answer"),
                Map.entry(ApiModels.ClaimView.class, "Claim"),
                Map.entry(ApiModels.AnalysisInput.class, "AnalysisInput"),
                Map.entry(ApiModels.CompanyFacts.class, "CompanyFacts"));
        for (var entry : mappings.entrySet()) {
            var schema = (Map<?, ?>) schemas.get(entry.getValue());
            assertNotNull(schema, entry.getValue());
            Set<String> fields = Arrays.stream(entry.getKey().getRecordComponents())
                    .map(java.lang.reflect.RecordComponent::getName).collect(Collectors.toSet());
            assertEquals(fields, ((Map<?, ?>) schema.get("properties")).keySet(), entry.getValue());
        }
        var paths = (Map<?, ?>) root.get("paths");
        assertTrue(paths.containsKey("/api/v1/materials/events"));
        assertTrue(paths.containsKey("/api/v1/events/{eventId}/versions"));
        var refresh = (Map<?, ?>) ((Map<?, ?>) paths.get("/api/v1/companies/{companyId}/insights/refresh")).get("post");
        assertTrue(((Map<?, ?>) refresh.get("responses")).containsKey("200"));
    }

    private static void visit(Object value, Map<?, ?> root) {
        if (value instanceof Map<?, ?> map) {
            if (map.get("$ref") instanceof String ref) {
                assertTrue(ref.startsWith("#/"), ref);
                Object target = root;
                for (String part : ref.substring(2).split("/")) {
                    assertInstanceOf(Map.class, target, ref);
                    target = ((Map<?, ?>) target).get(part.replace("~1", "/").replace("~0", "~"));
                    assertNotNull(target, ref);
                }
            }
            map.values().forEach(child -> visit(child, root));
        } else if (value instanceof List<?> list) list.forEach(child -> visit(child, root));
    }
}
