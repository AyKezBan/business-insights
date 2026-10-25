package ru.businessinsights.backend.data;

import com.fasterxml.jackson.databind.JsonNode;
import ru.businessinsights.backend.ml.MlContracts.CompanyProfile;

import java.util.List;

public record ProfileSnapshot(CompanyProfile profile, JsonNode provenance,
                              List<String> qualityIssues, boolean sufficient) {
    public ProfileSnapshot {
        qualityIssues = List.copyOf(qualityIssues);
    }
}
