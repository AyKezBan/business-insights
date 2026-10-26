package ru.businessinsights.backend.auth;

import com.fasterxml.jackson.databind.ObjectMapper;
import jakarta.servlet.FilterChain;
import jakarta.servlet.ServletException;
import jakarta.servlet.http.HttpServletRequest;
import jakarta.servlet.http.HttpServletResponse;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.security.MessageDigest;
import java.util.UUID;

import org.springframework.stereotype.Component;
import org.springframework.web.filter.OncePerRequestFilter;
import ru.businessinsights.backend.api.ApiModels.ProblemView;
import ru.businessinsights.backend.config.BackendProperties;

@Component
public class DemoAuthFilter extends OncePerRequestFilter {
    public static final String PERSON = "backend.person";
    public static final String REQUEST_ID = "backend.requestId";
    private final BackendProperties properties;
    private final ObjectMapper mapper;

    public DemoAuthFilter(BackendProperties properties, ObjectMapper mapper) {
        this.properties = properties;
        this.mapper = mapper;
    }

    @Override
    protected void doFilterInternal(HttpServletRequest req, HttpServletResponse res, FilterChain chain)
            throws ServletException, IOException {
        String requestId = UUID.randomUUID().toString();
        req.setAttribute(REQUEST_ID, requestId);
        res.setHeader("X-Request-ID", requestId);
        if (req.getServletPath().startsWith("/api/")) {
            res.setHeader("Cache-Control", "no-store");
            String token = properties.auth().demoToken();
            if (token.isBlank()) {
                reject(res, 503, "AUTH_NOT_CONFIGURED", "Демонстрационный доступ не настроен", requestId);
                return;
            }
            String actual = req.getHeader("Authorization");
            if (actual == null || !MessageDigest.isEqual(actual.getBytes(StandardCharsets.UTF_8),
                    ("Bearer " + token).getBytes(StandardCharsets.UTF_8))) {
                res.setHeader("WWW-Authenticate", "Bearer");
                reject(res, 401, "UNAUTHENTICATED", "Требуется авторизация", requestId);
                return;
            }
            req.setAttribute(PERSON, properties.auth().personId());
        }
        chain.doFilter(req, res);
    }

    private void reject(HttpServletResponse res, int status, String code, String message, String requestId) throws IOException {
        res.setStatus(status);
        res.setContentType("application/problem+json");
        res.setCharacterEncoding("UTF-8");
        mapper.writeValue(res.getOutputStream(), new ProblemView(status, code, message, false, requestId));
    }
}
