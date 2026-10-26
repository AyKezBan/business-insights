package ru.businessinsights.backend.service;

import org.springframework.stereotype.Component;
import ru.businessinsights.backend.api.ApiModels.Role;
import ru.businessinsights.backend.api.ApiModels.EventVersionView;
import ru.businessinsights.backend.config.BackendProperties;
import ru.businessinsights.backend.ml.MlContracts.CompanyProfile;

import java.time.Instant;
import java.time.ZoneOffset;
import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Explainable candidate selection. Scores are rule weights, not model impact or probabilities.
 */
@Component
public class EventRelevance {
    public record Selection(int score, List<String> reasons, Instant expiresAt, String exclusion) {
    }

    private final BackendProperties properties;

    public EventRelevance(BackendProperties properties) {
        this.properties = properties;
    }

    public Selection evaluate(CompanyProfile profile, Role role, EventVersionView version, Instant now) {
        var event = version.event();
        Instant published = event.publishedAt() != null ? event.publishedAt()
                : event.source().date() != null ? event.source().date().atStartOfDay().toInstant(ZoneOffset.UTC) : version.receivedAt();
        Instant expires = expiresAt(version);
        if (published.isAfter(now.plusSeconds(300))) {
            return new Selection(0, List.of("Дата публикации находится в будущем"), expires, "FUTURE_PUBLICATION");
        }
        if (!expires.isAfter(now)) {
            return new Selection(0, List.of("Истёк срок актуальности события"), expires, "EXPIRED");
        }
        Map<String, String> channels = new LinkedHashMap<>();
        addIndustry(channels, profile.okvedCode());
        if (Boolean.TRUE.equals(profile.products().loans()) || Boolean.TRUE.equals(profile.products().leasing())) {
            channels.put("cost_of_debt", "Кредиты или лизинг компании");
            channels.put("liquidity", "Финансирование оборотного капитала");
        }
        if (Boolean.TRUE.equals(profile.products().deposits())) {
            channels.put("cost_of_debt", "Ставки влияют на размещение средств компании");
            channels.put("deposit_yield", "Депозиты компании");
        }
        if (Boolean.TRUE.equals(profile.products().payroll())) {
            channels.put("labor_costs", "Зарплатный проект компании");
        }
        if (Boolean.TRUE.equals(profile.products().factoring())) {
            channels.put("liquidity", "Факторинг компании");
        }
        if (!profile.counterparties().isEmpty()) {
            channels.put("counterparty_risk", "Платёжные контрагенты компании");
        }
        if (Boolean.TRUE.equals(profile.foreignTrade())) {
            for (String channel : List.of("fx", "sanctions", "import_costs", "export_demand")) {
                channels.put(channel, "Имеются сведения ФТС. Направление торговли требует подтверждения");
            }
        }
        var reasons = new ArrayList<String>();
        int score = 0;
        Set<String> roleTopics = switch (role) {
            case cfo, treasurer -> Set.of("cost_of_debt", "deposit_yield", "liquidity", "fx", "counterparty_risk");
            case chief_accountant -> Set.of("tax", "regulation", "labor_costs");
            case owner, ceo, other_c_level ->
                    Set.of("domestic_demand", "regulation", "commodity_prices", "input_prices");
            case other -> Set.of();
        };
        for (String topic : event.topics().stream().distinct().toList()) {
            if (channels.containsKey(topic)) {
                score += 25;
                reasons.add(topic + ": " + channels.get(topic));
                if (roleTopics.contains(topic)) {
                    score += 10;
                    reasons.add("Тема соответствует роли " + role.name());
                }
            }
        }
        String text = (event.title() + " " + event.text()).toLowerCase(Locale.ROOT);
        if (matchesWords(text, profile.okvedName()) || matchesWords(text, profile.industry())) {
            score += 30;
            reasons.add("В публикации упомянут вид деятельности компании");
        }
        if (matchesWords(text, profile.region()) || matchesWords(text, profile.city())) {
            score += 20;
            reasons.add("В публикации упомянут регион или город регистрации компании");
        }
        return new Selection(Math.min(score, 100), List.copyOf(reasons), expires, score < 20 ? "NOT_RELEVANT" : null);
    }

    public Instant expiresAt(EventVersionView version) {
        var event = version.event();
        Instant published = event.publishedAt() != null ? event.publishedAt()
                : event.source().date() != null ? event.source().date().atStartOfDay().toInstant(ZoneOffset.UTC) : version.receivedAt();
        return published.plus(properties.feed().maxAge());
    }

    private static boolean matchesWords(String text, String value) {
        if (value == null) {
            return false;
        }
        Set<String> ignored = Set.of("деятельность", "производство", "прочих", "прочие", "область", "области", "город", "край", "услуг", "изделий", "республика");
        for (String token : value.toLowerCase(Locale.ROOT).split("[^\\p{L}]+")) {
            if (token.length() >= 5 && !ignored.contains(token)
                    && Pattern.compile("(?U)\\b" + Pattern.quote(token) + "\\b").matcher(text).find()) {
                return true;
            }
        }
        return false;
    }

    private static void addIndustry(Map<String, String> channels, String code) {
        if (code == null) {
            return;
        }
        int prefix;
        try {
            prefix = Integer.parseInt(code.split("\\.")[0]);
        } catch (NumberFormatException e) {
            return;
        }
        List<String> topics;
        if (prefix >= 1 && prefix <= 9 || prefix >= 24 && prefix <= 25) {
            topics = List.of("commodity_prices", "input_prices", "fx", "export_demand");
        } else if (prefix >= 10 && prefix <= 33) {
            topics = List.of("input_prices", "domestic_demand", "import_costs", "fx");
        } else if (prefix >= 35 && prefix <= 43) {
            topics = List.of("regulation", "input_prices", "cost_of_debt");
        } else if (prefix >= 45 && prefix <= 56) {
            topics = List.of("domestic_demand", "input_prices", "labor_costs", "import_costs");
        } else if (prefix >= 58 && prefix <= 66) {
            topics = List.of("labor_costs", "regulation", "cost_of_debt");
        } else if (prefix == 68) {
            topics = List.of("cost_of_debt", "domestic_demand");
        } else if (prefix >= 69 && prefix <= 82) {
            topics = List.of("labor_costs", "domestic_demand");
        } else {
            topics = List.of();
        }
        for (String topic : topics) {
            channels.put(topic, "Канал влияния для ОКВЭД " + code);
        }
    }
}
