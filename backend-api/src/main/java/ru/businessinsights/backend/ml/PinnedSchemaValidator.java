package ru.businessinsights.backend.ml;

import com.fasterxml.jackson.databind.JsonNode;
import com.fasterxml.jackson.databind.ObjectMapper;

import java.io.IOException;
import java.io.InputStream;
import java.time.LocalDate;
import java.time.OffsetDateTime;
import java.time.format.DateTimeParseException;
import java.util.HashMap;
import java.util.Map;

/**
 * Implements only the keywords used by the five immutable, locally pinned ML schemas.
 */
final class PinnedSchemaValidator {
    private final Map<String, JsonNode> schemas;

    PinnedSchemaValidator(ObjectMapper mapper) {
        Map<String, JsonNode> loaded = new HashMap<>();
        for (String name : new String[]{"insight-request", "question-request", "insight", "answer", "trace-event"}) {
            try (InputStream stream = getClass().getResourceAsStream("/ml-contracts/" + name + ".schema.json")) {
                if (stream == null) {
                    throw new IllegalStateException("Pinned ML schema not found: " + name);
                }
                loaded.put(name, mapper.readTree(stream));
            } catch (IOException ex) {
                throw new IllegalStateException("Cannot load pinned ML schema: " + name, ex);
            }
        }
        schemas = Map.copyOf(loaded);
    }

    void validate(String name, JsonNode value) {
        JsonNode schema = schemas.get(name);
        if (schema == null) {
            throw new IllegalArgumentException("Unknown pinned ML schema");
        }
        validate(schema, schema, value, "$", 0);
    }

    private void validate(JsonNode root, JsonNode schema, JsonNode value, String path, int depth) {
        if (depth > 64) {
            throw invalid(path, "schema nesting limit");
        }
        if (schema.has("$ref")) {
            String reference = schema.get("$ref").asText();
            if (!reference.startsWith("#/")) {
                throw new IllegalStateException("Non-local pinned schema reference");
            }
            JsonNode resolved = root.at(reference.substring(1));
            if (resolved.isMissingNode()) {
                throw new IllegalStateException("Unresolved pinned schema reference");
            }
            validate(root, resolved, value, path, depth + 1);
            return;
        }
        if (schema.has("anyOf")) {
            for (JsonNode alternative : schema.get("anyOf")) {
                try {
                    validate(root, alternative, value, path, depth + 1);
                    return;
                } catch (IllegalArgumentException ignored) {
                    // A nullable field is accepted only when one complete branch matches.
                }
            }
            throw invalid(path, "no allowed type matches");
        }
        if (schema.has("type") && !hasType(value, schema.get("type").asText())) {
            throw invalid(path, "expected " + schema.get("type").asText());
        }
        if (schema.has("enum")) {
            boolean found = false;
            for (JsonNode allowed : schema.get("enum")) {
                found |= allowed.equals(value);
            }
            if (!found) {
                throw invalid(path, "unknown enum value");
            }
        }
        if (value == null || value.isNull()) {
            return;
        }
        if (value.isObject()) {
            for (JsonNode required : schema.path("required")) {
                if (!value.has(required.asText())) {
                    throw invalid(path + "." + required.asText(), "required field missing");
                }
            }
            JsonNode properties = schema.path("properties");
            var fields = value.fields();
            while (fields.hasNext()) {
                var field = fields.next();
                JsonNode property = properties.get(field.getKey());
                if (property != null) {
                    validate(root, property, field.getValue(), path + "." + field.getKey(), depth + 1);
                } else if (schema.has("additionalProperties")) {
                    JsonNode additional = schema.get("additionalProperties");
                    if (additional.isBoolean() && !additional.booleanValue()) {
                        throw invalid(path, "additional property");
                    }
                    if (additional.isObject()) {
                        validate(root, additional, field.getValue(), path + ".*", depth + 1);
                    }
                }
            }
        } else if (value.isArray() && schema.has("items")) {
            for (int i = 0; i < value.size(); i++) {
                validate(root, schema.get("items"), value.get(i), path + "[" + i + "]", depth + 1);
            }
        } else if (value.isTextual()) {
            String text = value.textValue();
            int length = text.codePointCount(0, text.length());
            if (schema.has("minLength") && length < schema.get("minLength").intValue()) {
                throw invalid(path, "string too short");
            }
            if (schema.has("maxLength") && length > schema.get("maxLength").intValue()) {
                throw invalid(path, "string too long");
            }
            try {
                if ("date".equals(schema.path("format").asText())) {
                    LocalDate.parse(text);
                }
                if ("date-time".equals(schema.path("format").asText())) {
                    OffsetDateTime.parse(text);
                }
            } catch (DateTimeParseException ex) {
                throw invalid(path, "invalid date format");
            }
        }
    }

    private static boolean hasType(JsonNode value, String type) {
        if (value == null) {
            return false;
        }
        return switch (type) {
            case "object" -> value.isObject();
            case "array" -> value.isArray();
            case "string" -> value.isTextual();
            case "number" -> value.isNumber();
            case "integer" -> value.isIntegralNumber();
            case "boolean" -> value.isBoolean();
            case "null" -> value.isNull();
            default -> throw new IllegalStateException("Unsupported keyword in pinned ML schema: " + type);
        };
    }

    private static IllegalArgumentException invalid(String path, String reason) {
        return new IllegalArgumentException("ML contract " + path + ": " + reason);
    }
}
