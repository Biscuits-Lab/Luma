#include "platform/WindowsStrings.h"

#include <Windows.h>
#include <shellapi.h>

#include <array>
#include <iomanip>
#include <sstream>
#include <system_error>

namespace lumina::platform {

std::wstring formatFileSize(std::uintmax_t bytes) {
    static constexpr std::array<const wchar_t*, 5> units = {L"B", L"KB", L"MB", L"GB", L"TB"};
    auto value = static_cast<double>(bytes);
    std::size_t unitIndex = 0;
    while (value >= 1024.0 && unitIndex + 1 < units.size()) {
        value /= 1024.0;
        ++unitIndex;
    }

    std::wostringstream stream;
    if (unitIndex == 0) {
        stream << bytes << L' ' << units[unitIndex];
    } else {
        stream << std::fixed << std::setprecision(value >= 100.0 ? 0 : 1) << value << L' ' << units[unitIndex];
    }
    return stream.str();
}

std::uintmax_t fileSizeOrZero(const std::filesystem::path& path) {
    std::error_code error;
    const auto size = std::filesystem::file_size(path, error);
    return error ? 0 : size;
}

std::wstring commandLineArgument(int index) {
    int argc = 0;
    wchar_t** argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) {
        return {};
    }

    std::wstring value;
    if (index >= 0 && index < argc) {
        value = argv[index];
    }
    LocalFree(argv);
    return value;
}

std::filesystem::path executableDirectory() {
    std::wstring buffer(MAX_PATH, L'\0');
    DWORD length = 0;
    while (true) {
        length = GetModuleFileNameW(nullptr, buffer.data(), static_cast<DWORD>(buffer.size()));
        if (length == 0) {
            return std::filesystem::current_path();
        }
        if (length < buffer.size() - 1) {
            buffer.resize(length);
            return std::filesystem::path(buffer).parent_path();
        }
        buffer.resize(buffer.size() * 2);
    }
}

} // namespace lumina::platform
