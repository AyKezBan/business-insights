package ru.businessinsights.backend.service;

import org.springframework.stereotype.Service;
import org.springframework.http.HttpStatus;
import com.fasterxml.jackson.databind.ObjectMapper;
import com.fasterxml.jackson.core.JsonProcessingException;
import ru.businessinsights.backend.api.ApiException;
import ru.businessinsights.backend.api.ApiModels.CatalogTokenView;
import ru.businessinsights.backend.api.ApiModels.MaterialCatalogView;
import ru.businessinsights.backend.data.MaterialCatalogRepository;
import ru.businessinsights.backend.data.MaterialCatalogRepository.MaterialEvent;
import ru.businessinsights.backend.ml.MaterialCatalogClient;
import ru.businessinsights.backend.ml.MlException;
import java.util.ArrayList;
import java.util.HashSet;
import java.util.Comparator;

/** Reads the trusted ML catalog; no analysis or provider operation is triggered. */
@Service
public class MaterialCatalogService {
    private static final int PAGE_SIZE = 100;
    private static final int MAX_PAGES = 100;
    private static final long MAX_CATALOG_BYTES = 16L * 1024 * 1024;
    private final MaterialCatalogClient client;
    private final MaterialCatalogRepository repository;
    private final InsightMapper views;
    private final ObjectMapper json;

    public MaterialCatalogService(MaterialCatalogClient client, MaterialCatalogRepository repository, InsightMapper views, ObjectMapper json) {
        this.client = client;
        this.repository = repository;
        this.views = views;
        this.json = json;
    }

    public MaterialCatalogView events(int limit, int offset, String revision) {
        return catalog(limit, offset, revision, false);
    }

    /** The feed uses newest-first offset pages of the same atomically accepted catalog. */
    public MaterialCatalogView news(int limit, int offset) {
        return catalog(limit, offset, null, true);
    }

    private MaterialCatalogView catalog(int limit, int offset, String revision, boolean newestFirst) {
        if (limit < 1 || limit > 100 || offset < 0 || revision != null && !revision.matches("[a-f0-9]{64}"))
            throw new ApiException(HttpStatus.BAD_REQUEST, "INVALID_REQUEST", "Некорректные параметры каталога");
        var expected = repository.head();
        var page = client.page(null, null, PAGE_SIZE);
        var token = page.token();
        if (revision != null && !revision.equals(token.revision()))
            throw new ApiException(HttpStatus.CONFLICT, "CATALOG_CHANGED", "Каталог обновился. Обновите список и выберите материал повторно");
        var all = new ArrayList<MaterialEvent>();
        var eventIds = new HashSet<String>();
        var cursors = new HashSet<String>();
        int pages = 0;
        long bytes = 0;
        while (true) {
            if (!token.equals(page.token())) throw invalid("Publication token changed between catalog pages");
            if (++pages > MAX_PAGES) throw invalid("Catalog exceeds page limit");
            try { bytes += json.writeValueAsBytes(page.events()).length; }
            catch (JsonProcessingException e) { throw invalid("Catalog cannot be serialized"); }
            if (bytes > MAX_CATALOG_BYTES) throw invalid("Catalog exceeds aggregate size limit");
            for (var material : page.events()) {
                if (!eventIds.add(material.event().eventId())) throw invalid("Duplicate event across catalog pages");
                all.add(material);
            }
            String cursor = page.nextCursor();
            if (cursor == null) break;
            if (page.events().isEmpty() || !cursors.add(cursor) || pages >= MAX_PAGES)
                throw invalid("Catalog pagination cannot advance within its bounds");
            page = client.page(token.revision(), cursor, PAGE_SIZE);
        }
        // The only write occurs after every page passed validation. Repository's
        // transaction and complete-token CAS guard against racing newer publications.
        repository.synchronize(expected, token, all);
        if (newestFirst) {
            all.sort(Comparator.comparing((MaterialEvent material) -> material.event().publishedAt(),
                            Comparator.nullsLast(Comparator.reverseOrder()))
                    .thenComparing(material -> material.event().eventId(), Comparator.reverseOrder()));
        }
        int start = Math.min(offset, all.size());
        int end = Math.min(start + limit, all.size());
        var items = all.subList(start, end).stream().map(material -> views.material(material, token.revision())).toList();
        return new MaterialCatalogView(new CatalogTokenView(token.storeId(), token.generation(), token.revision()),
                items, limit, offset, all.size());
    }

    private static MlException invalid(String message) {
        return new MlException(MlException.Code.INVALID_RESPONSE, message, false);
    }
}
