package ru.businessinsights.backend.api;

import jakarta.servlet.http.HttpServletRequest;
import jakarta.validation.ConstraintViolationException;
import org.slf4j.Logger;
import org.slf4j.LoggerFactory;
import org.springframework.dao.DataAccessException;
import org.springframework.http.HttpStatus;
import org.springframework.http.ResponseEntity;
import org.springframework.http.converter.HttpMessageNotReadableException;
import org.springframework.web.bind.MethodArgumentNotValidException;
import org.springframework.web.bind.MissingServletRequestParameterException;
import org.springframework.web.HttpRequestMethodNotSupportedException;
import org.springframework.web.HttpMediaTypeNotSupportedException;
import org.springframework.web.servlet.resource.NoResourceFoundException;
import org.springframework.web.bind.annotation.ExceptionHandler;
import org.springframework.web.bind.annotation.RestControllerAdvice;
import org.springframework.web.method.annotation.HandlerMethodValidationException;
import org.springframework.web.method.annotation.MethodArgumentTypeMismatchException;
import ru.businessinsights.backend.api.ApiModels.ProblemView;
import ru.businessinsights.backend.auth.DemoAuthFilter;
import ru.businessinsights.backend.data.ProfileDataException;

@RestControllerAdvice
public class ApiErrorHandler {
    private static final Logger LOG = LoggerFactory.getLogger(ApiErrorHandler.class);

    @ExceptionHandler(ApiException.class)
    public ResponseEntity<ProblemView> api(ApiException e, HttpServletRequest req) {
        return problem(e.status(), e.code(), e.getMessage(), e.retryable(), req);
    }

    @ExceptionHandler(ProfileDataException.class)
    public ResponseEntity<ProblemView> profile(ProfileDataException e, HttpServletRequest req) {
        HttpStatus status = switch (e.reason()) {
            case COMPANY_NOT_FOUND -> HttpStatus.NOT_FOUND;
            case AMBIGUOUS_COMPANY, EXTERNAL_COMPANY -> HttpStatus.UNPROCESSABLE_ENTITY;
            case NOT_CLIENT -> HttpStatus.FORBIDDEN;
            case DATASET_UNAVAILABLE, DATASET_INCOMPATIBLE -> HttpStatus.SERVICE_UNAVAILABLE;
        };
        return problem(status, e.reason().name(), e.getMessage(), false, req);
    }

    @ExceptionHandler({ConstraintViolationException.class, MethodArgumentNotValidException.class, HttpMessageNotReadableException.class,
            MethodArgumentTypeMismatchException.class, MissingServletRequestParameterException.class, HandlerMethodValidationException.class})
    public ResponseEntity<ProblemView> invalid(Exception e, HttpServletRequest req) {
        return problem(HttpStatus.BAD_REQUEST, "INVALID_REQUEST", "Проверьте обязательные поля, форматы и допустимые значения запроса", false, req);
    }

    @ExceptionHandler(NoResourceFoundException.class)
    public ResponseEntity<ProblemView> missing(NoResourceFoundException e, HttpServletRequest req) {
        return problem(HttpStatus.NOT_FOUND, "NOT_FOUND", "Ресурс не найден", false, req);
    }

    @ExceptionHandler(HttpRequestMethodNotSupportedException.class)
    public ResponseEntity<ProblemView> method(HttpRequestMethodNotSupportedException e, HttpServletRequest req) {
        return problem(HttpStatus.METHOD_NOT_ALLOWED, "METHOD_NOT_ALLOWED", "Метод не поддерживается для этого ресурса", false, req);
    }

    @ExceptionHandler(HttpMediaTypeNotSupportedException.class)
    public ResponseEntity<ProblemView> media(HttpMediaTypeNotSupportedException e, HttpServletRequest req) {
        return problem(HttpStatus.UNSUPPORTED_MEDIA_TYPE, "UNSUPPORTED_MEDIA_TYPE", "Ожидается JSON", false, req);
    }

    @ExceptionHandler(DataAccessException.class)
    public ResponseEntity<ProblemView> database(DataAccessException e, HttpServletRequest req) {
        LOG.warn("Database request {} failed ({})", req.getAttribute(DemoAuthFilter.REQUEST_ID), e.getClass().getSimpleName());
        return problem(HttpStatus.SERVICE_UNAVAILABLE, "DATABASE_UNAVAILABLE", "Хранилище временно недоступно", true, req);
    }

    @ExceptionHandler(Exception.class)
    public ResponseEntity<ProblemView> unexpected(Exception e, HttpServletRequest req) {
        LOG.error("Request {} failed ({})", req.getAttribute(DemoAuthFilter.REQUEST_ID), e.getClass().getSimpleName());
        return problem(HttpStatus.INTERNAL_SERVER_ERROR, "INTERNAL_ERROR", "Не удалось обработать запрос", false, req);
    }

    private ResponseEntity<ProblemView> problem(HttpStatus status, String code, String message, boolean retryable, HttpServletRequest req) {
        return ResponseEntity.status(status).contentType(org.springframework.http.MediaType.APPLICATION_PROBLEM_JSON)
                .body(new ProblemView(status.value(), code, message, retryable, (String) req.getAttribute(DemoAuthFilter.REQUEST_ID)));
    }
}
