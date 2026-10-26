package ru.businessinsights.backend.api;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.Valid;
import jakarta.validation.constraints.*;

import java.net.URI;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import org.springframework.http.ResponseEntity;
import org.springframework.validation.annotation.Validated;
import org.springframework.web.bind.annotation.*;
import ru.businessinsights.backend.api.ApiModels.*;
import ru.businessinsights.backend.auth.DemoAuthFilter;
import ru.businessinsights.backend.service.InsightService;
import ru.businessinsights.backend.service.MaterialCatalogService;

@RestController
@Validated
public class BackendController {
    private final InsightService service;
    private final MaterialCatalogService materials;

    public BackendController(InsightService service, MaterialCatalogService materials) {
        this.service = service;
        this.materials = materials;
    }

    private String person(HttpServletRequest req) {
        return (String) req.getAttribute(DemoAuthFilter.PERSON);
    }

    @GetMapping("/health")
    public Map<String, String> health() {
        return Map.of("status", "up", "scope", "process");
    }

    @GetMapping("/api/v1/session")
    public SessionView session(HttpServletRequest req) {
        return new SessionView(person(req), "DEMO_BEARER", "Смоделированный доступ по связям ФЛ–ЮЛ; роль задаётся явно");
    }

    @GetMapping("/api/v1/companies")
    public List<CompanyView> companies(HttpServletRequest req,
                                       @RequestParam(defaultValue = "50") @Min(1) @Max(100) int limit, @RequestParam(defaultValue = "0") @Min(0) int offset) {
        return service.companies(person(req), limit, offset);
    }

    @GetMapping("/api/v1/companies/{companyId}/profile")
    public ProfileView profile(HttpServletRequest req,
                               @PathVariable @Pattern(regexp = "U_\\d+") String companyId) {
        return service.profile(person(req), companyId);
    }

    @PostMapping("/api/v1/events")
    public ResponseEntity<EventInput> event(@Valid @RequestBody EventInput input) {
        return ResponseEntity.ok(service.event(input));
    }

    @GetMapping("/api/v1/materials/events")
    public MaterialCatalogView materials(@RequestParam(defaultValue = "50") @Min(1) @Max(100) int limit,
                                         @RequestParam(defaultValue = "0") @Min(0) int offset,
                                         @RequestParam(required = false) @Pattern(regexp = "[a-f0-9]{64}") String revision) {
        return materials.events(limit, offset, revision);
    }

    @PostMapping("/api/v1/analyses")
    public ResponseEntity<OperationView> analyze(HttpServletRequest req, @Valid @RequestBody AnalysisInput input,
                                                 @RequestHeader(value = "Idempotency-Key", required = false) @Size(min = 1, max = 100) String key) {
        return accepted(service.analyze(person(req), input, key));
    }

    @GetMapping("/api/v1/operations/{id}")
    public ResponseEntity<OperationView> operation(HttpServletRequest req, @PathVariable UUID id) {
        OperationView result = service.operation(person(req), id);
        var response = ResponseEntity.ok();
        if ("QUEUED".equals(result.status()) || "RUNNING".equals(result.status())) {
            response.header("Retry-After", "2");
        }
        return response.body(result);
    }

    @GetMapping("/api/v1/companies/{companyId}/insights")
    public FeedView feed(HttpServletRequest req,
                         @PathVariable @Pattern(regexp = "U_\\d+") String companyId, @RequestParam Role role,
                         @RequestParam(defaultValue = "20") @Min(1) @Max(100) int limit, @RequestParam(defaultValue = "0") @Min(0) int offset,
                         @RequestParam(defaultValue = "false") boolean includeHidden) {
        return service.feed(person(req), companyId, role, limit, offset, includeHidden);
    }

    @GetMapping("/api/v1/insights/{id}")
    public InsightView insight(HttpServletRequest req, @PathVariable UUID id) {
        return service.insight(person(req), id);
    }

    @PostMapping("/api/v1/insights/{id}/opened")
    public ResponseEntity<Void> open(HttpServletRequest req, @PathVariable UUID id) {
        service.open(person(req), id);
        return ResponseEntity.noContent().build();
    }

    @PutMapping("/api/v1/insights/{id}/state")
    public ResponseEntity<Void> state(HttpServletRequest req, @PathVariable UUID id, @Valid @RequestBody StateInput input) {
        service.state(person(req), id, input);
        return ResponseEntity.noContent().build();
    }

    @PutMapping("/api/v1/insights/{id}/feedback")
    public ResponseEntity<Void> feedback(HttpServletRequest req, @PathVariable UUID id, @Valid @RequestBody FeedbackInput input) {
        service.feedback(person(req), id, input);
        return ResponseEntity.noContent().build();
    }

    @PostMapping("/api/v1/insights/{id}/questions")
    public ResponseEntity<OperationView> question(HttpServletRequest req, @PathVariable UUID id,
                                                  @Valid @RequestBody QuestionInput input, @RequestHeader(value = "Idempotency-Key", required = false) @Size(min = 1, max = 100) String key) {
        return accepted(service.question(person(req), id, input, key));
    }

    @GetMapping("/api/v1/answers/{id}")
    public AnswerView answer(HttpServletRequest req, @PathVariable UUID id) {
        return service.answer(person(req), id);
    }

    @GetMapping({"/api/v1/insights/{id}/trace", "/api/v1/operations/{id}/trace"})
    public TraceView trace(HttpServletRequest req, @PathVariable UUID id) {
        return service.trace(person(req), id);
    }

    @PostMapping("/api/v1/insights/{id}/actions")
    public ResponseEntity<ActionView> action(HttpServletRequest req, @PathVariable UUID id, @Valid @RequestBody ActionInput input) {
        return ResponseEntity.status(201).body(service.action(person(req), id, input));
    }

    private ResponseEntity<OperationView> accepted(OperationView op) {
        return ResponseEntity.accepted().location(URI.create("/api/v1/operations/" + op.operationId())).header("Retry-After", "2").body(op);
    }
}
