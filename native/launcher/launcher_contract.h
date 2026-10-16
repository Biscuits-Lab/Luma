#pragma once
#include <string>
#include <string_view>
#include <vector>

namespace luma::launcher {
constexpr std::size_t kMaxPointerBytes = 512;
constexpr std::size_t kMaxMetadataBytes = 4096;
constexpr wchar_t kPointerName[] = L"active-payload.txt";
constexpr wchar_t kOwnerName[] = L"launcher-owner.txt";
struct ActivePayload {
    std::string payload_id;
    std::string version;
    std::string bundle_sha256;
    std::string executable_sha256;
};
bool ParsePointer(std::string_view bytes, ActivePayload& output);
bool ValidateMetadata(std::string_view bytes, const ActivePayload& payload);
bool ParseOwner(std::string_view bytes, std::string& sha256);
std::wstring QuoteArgument(std::wstring_view argument);
std::wstring CommandLine(std::wstring_view executable, const std::vector<std::wstring>& arguments);
}  // namespace luma::launcher
