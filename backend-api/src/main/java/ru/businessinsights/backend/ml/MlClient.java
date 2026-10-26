package ru.businessinsights.backend.ml;

import java.util.List;

import static ru.businessinsights.backend.ml.MlContracts.*;

public interface MlClient {
    NewsPage news(int limit, int offset);

    Insight generate(InsightRequest request);

    Answer ask(String insightId, String question, CompanyProfile profile);

    List<TraceEvent> trace(String runId);
}
