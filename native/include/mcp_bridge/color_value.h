#pragma once
#include <array>
#include <cmath>
#include <locale>
#include <sstream>
#include <string>
#include <vector>

namespace HandlerHelpers {

// MAXScript Color channels use 0..255; PB2 Color/AColor channels use 0..1.
// Bracketed/comma-separated vectors are already in native units (including HDR).
inline bool ParseColorValue(const std::string& input, std::array<float, 4>& rgba) {
    const auto begin = input.find_first_not_of(" \t\r\n");
    if (begin == std::string::npos) return false;
    std::string value = input.substr(begin, input.find_last_not_of(" \t\r\n") - begin + 1);
    bool parens = value.front() == '(';
    if (parens) {
        if (value.back() != ')') return false;
        value = value.substr(1, value.size() - 2);
    }
    std::istringstream probe(value);
    std::string token;
    probe >> token;
    const bool color = token == "color";
    if (parens && !color) return false;
    if (color) {
        std::getline(probe, value);
    } else {
        if (value.front() == '[') {
            if (value.back() != ']') return false;
            value = value.substr(1, value.size() - 2);
        }
        // Require commas for native vector notation; reject missing components.
        if (value.find(',') == std::string::npos) return false;
    }
    std::vector<float> channels;
    if (color) {
        std::istringstream stream(value);
        stream.imbue(std::locale::classic());
        std::string component;
        while (stream >> component) {
            std::istringstream item(component);
            item.imbue(std::locale::classic());
            float channel;
            if (!(item >> channel) || !item.eof()) return false;
            channels.push_back(channel);
        }
    } else {
        std::istringstream stream(value);
        std::string component;
        if (value.empty() || value.back() == ',') return false;
        while (std::getline(stream, component, ',')) {
            std::istringstream item(component);
            item.imbue(std::locale::classic());
            float channel;
            if (!(item >> channel)) return false;
            item >> std::ws;
            if (!item.eof()) return false;
            channels.push_back(channel);
        }
    }
    if (channels.size() != 3 && channels.size() != 4) return false;
    std::array<float, 4> parsed{0, 0, 0, 1};
    for (size_t i = 0; i < channels.size(); ++i) {
        if (!std::isfinite(channels[i])) return false;
        parsed[i] = color ? channels[i] / 255.0f : channels[i];
    }
    rgba = parsed;
    return true;
}

} // namespace HandlerHelpers
