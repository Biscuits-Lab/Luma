#include "launcher_contract.h"
#include <array>
#include <map>

namespace luma::launcher {
namespace {
bool Hex(std::string_view text, std::size_t length) {
    if (text.size() != length) return false;
    for (const char c : text) if (!((c >= '0' && c <= '9') || (c >= 'a' && c <= 'f'))) return false;
    return true;
}
bool Version(std::string_view text) {
    if (text.size() > 32) return false;
    unsigned parts = 0;
    while (!text.empty()) {
        const auto dot = text.find('.');
        const auto part = text.substr(0, dot);
        if (part.empty() || (part.size() > 1 && part[0] == '0')) return false;
        for (const char c : part) if (c < '0' || c > '9') return false;
        ++parts;
        if (dot == std::string_view::npos) return parts == 3;
        text.remove_prefix(dot + 1);
    }
    return false;
}
// A deliberately narrow flat JSON reader for the five generated installer fields.
// No unknown/duplicate keys, escapes, nested values, BOM or trailing data allowed.
class MetadataReader {
public:
    explicit MetadataReader(std::string_view text) : text_(text) {}
    bool Read(std::map<std::string, std::string>& fields) {
        if (!Take('{')) return false;
        do {
            std::string key, value;
            if (!String(key) || !Take(':')) return false;
            Skip();
            if (key == "managed_install") {
                if (text_.substr(pos_, 4) != "true") return false;
                pos_ += 4;
                value = "true";
            } else if (!String(value)) return false;
            if (!fields.emplace(key, value).second || fields.size() > 5) return false;
            Skip();
            if (pos_ < text_.size() && text_[pos_] == '}') {
                ++pos_; Skip(); return pos_ == text_.size();
            }
        } while (Take(','));
        return false;
    }
private:
    void Skip() {
        while (pos_ < text_.size() && (text_[pos_] == ' ' || text_[pos_] == '\t' ||
               text_[pos_] == '\r' || text_[pos_] == '\n')) ++pos_;
    }
    bool Take(char c) { Skip(); if (pos_ == text_.size() || text_[pos_] != c) return false; ++pos_; return true; }
    bool String(std::string& value) {
        if (!Take('"')) return false;
        while (pos_ < text_.size()) {
            const unsigned char c = static_cast<unsigned char>(text_[pos_++]);
            if (c == '"') return true;
            if (c < 0x20 || c > 0x7e || c == '\\') return false;
            value.push_back(static_cast<char>(c));
        }
        return false;
    }
    std::string_view text_;
    std::size_t pos_ = 0;
};
}  // namespace

bool ParsePointer(std::string_view bytes, ActivePayload& output) {
    if (bytes.empty() || bytes.size() > kMaxPointerBytes) return false;
    std::array<std::string_view, 4> lines;
    for (auto& line : lines) {
        const auto end = bytes.find('\n');
        if (end == std::string_view::npos) return false;
        line = bytes.substr(0, end);
        bytes.remove_prefix(end + 1);
    }
    if (!bytes.empty() || lines[0] != "LUMA-ACTIVE-1" || !Hex(lines[2], 64) || !Hex(lines[3], 64)) return false;
    const auto dash = lines[1].find('-');
    if (dash == std::string_view::npos || !Version(lines[1].substr(0, dash)) ||
        lines[1].substr(dash + 1) != lines[2].substr(0, 24)) return false;
    output = {std::string(lines[1]), std::string(lines[1].substr(0, dash)), std::string(lines[2]), std::string(lines[3])};
    return true;
}
bool ValidateMetadata(std::string_view bytes, const ActivePayload& payload) {
    if (bytes.empty() || bytes.size() > kMaxMetadataBytes) return false;
    std::map<std::string, std::string> actual;
    const std::map<std::string, std::string> expected = {
        {"managed_install", "true"}, {"layout", "versions-v1"}, {"version", payload.version},
        {"payload_id", payload.payload_id}, {"bundle_sha256", payload.bundle_sha256}};
    return MetadataReader(bytes).Read(actual) && actual == expected;
}
bool ParseOwner(std::string_view bytes, std::string& sha256) {
    constexpr std::string_view prefix = "LUMA-LAUNCHER-1\n";
    if (bytes.size() != prefix.size() + 65 || bytes.substr(0, prefix.size()) != prefix || bytes.back() != '\n') return false;
    const auto digest = bytes.substr(prefix.size(), 64);
    if (!Hex(digest, 64)) return false;
    sha256 = std::string(digest);
    return true;
}
std::wstring QuoteArgument(std::wstring_view argument) {
    std::wstring result = L"\"";
    std::size_t slashes = 0;
    for (const wchar_t c : argument) {
        if (c == L'\\') { ++slashes; continue; }
        result.append(slashes * (c == L'"' ? 2 : 1), L'\\');
        slashes = 0;
        if (c == L'"') result.push_back(L'\\');
        result.push_back(c);
    }
    result.append(slashes * 2, L'\\');
    result.push_back(L'"');
    return result;
}
std::wstring CommandLine(std::wstring_view executable, const std::vector<std::wstring>& arguments) {
    std::wstring command = QuoteArgument(executable);
    for (const auto& argument : arguments) { command.push_back(L' '); command += QuoteArgument(argument); }
    return command;
}
}  // namespace luma::launcher
