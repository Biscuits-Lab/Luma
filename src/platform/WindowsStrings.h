#pragma once

#include <cstdint>
#include <filesystem>
#include <string>

namespace lumina::platform {

[[nodiscard]] std::wstring formatFileSize(std::uintmax_t bytes);
[[nodiscard]] std::uintmax_t fileSizeOrZero(const std::filesystem::path& path);
[[nodiscard]] std::wstring commandLineArgument(int index);
[[nodiscard]] std::filesystem::path executableDirectory();

} // namespace lumina::platform
