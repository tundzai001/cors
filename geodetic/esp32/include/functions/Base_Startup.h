#pragma once
#include <cstring>
#include <cstdlib>
#include <cmath>

enum class BaseStartupAction { Survey, ResumeSurvey, Fixed, Preserve, Blocked };

inline bool baseNumber(const char *text, double &value) {
    if (!text || !*text) return false;
    char *end = nullptr;
    value = std::strtod(text, &end);
    return end != text && *end == '\0' && std::isfinite(value);
}

inline BaseStartupAction baseStartupAction(const char *method, bool receiverSurvey, bool locked) {
    if (locked) return BaseStartupAction::Blocked;
    if (!method || !*method || std::strcmp(method, "SURVEY_IN") == 0)
        return receiverSurvey ? BaseStartupAction::ResumeSurvey : BaseStartupAction::Survey;
    if (std::strcmp(method, "FIXED_LLA") == 0) return BaseStartupAction::Fixed;
    if (std::strcmp(method, "AUTO_CORS") == 0) return BaseStartupAction::Preserve;
    return BaseStartupAction::Blocked;
}
