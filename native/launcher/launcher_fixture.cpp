// Synthetic payload for native launcher tests only. Never packaged/installed.
#include <windows.h>
#include <shellapi.h>
#include <tlhelp32.h>
#include <string>
#include <vector>

namespace {
std::string Utf8(const std::wstring& text) {
    if (text.empty()) return {};
    const int count = WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    std::string result(count, '\0');
    WideCharToMultiByte(CP_UTF8, WC_ERR_INVALID_CHARS, text.data(), static_cast<int>(text.size()), result.data(), count, nullptr, nullptr);
    return result;
}
std::string Json(const std::wstring& text) {
    std::string result = "\"";
    for (const unsigned char c : Utf8(text)) {
        if (c == '\\' || c == '"') { result.push_back('\\'); result.push_back(static_cast<char>(c)); }
        else if (c < 0x20) {
            constexpr char hex[] = "0123456789abcdef";
            result += "\\u00"; result.push_back(hex[c >> 4]); result.push_back(hex[c & 15]);
        } else result.push_back(static_cast<char>(c));
    }
    return result + '"';
}
DWORD ParentPid() {
    const HANDLE snapshot = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0);
    if (snapshot == INVALID_HANDLE_VALUE) return 0;
    PROCESSENTRY32W entry{};
    entry.dwSize = sizeof(entry);
    DWORD parent = 0;
    if (Process32FirstW(snapshot, &entry)) do {
        if (entry.th32ProcessID == GetCurrentProcessId()) { parent = entry.th32ParentProcessID; break; }
    } while (Process32NextW(snapshot, &entry));
    CloseHandle(snapshot);
    return parent;
}
}
int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    int count = 0;
    LPWSTR* raw = CommandLineToArgvW(GetCommandLineW(), &count);
    if (!raw) return 71;
    std::vector<std::wstring> args;
    for (int i = 0; i < count; ++i) args.emplace_back(raw[i]);
    LocalFree(raw);
    if (args.size() < 5 || args[1] != L"--launcher-test-report" || args[3] != L"--launcher-test-event") return 72;
    const HANDLE event = OpenEventW(SYNCHRONIZE, FALSE, args[4].c_str());
    if (!event) return 73;
    std::wstring image(32768, L'\0'), cwd(32768, L'\0');
    const DWORD image_size = GetModuleFileNameW(nullptr, image.data(), static_cast<DWORD>(image.size()));
    const DWORD cwd_size = GetCurrentDirectoryW(static_cast<DWORD>(cwd.size()), cwd.data());
    if (!image_size || image_size >= image.size() || !cwd_size || cwd_size >= cwd.size()) { CloseHandle(event); return 74; }
    image.resize(image_size); cwd.resize(cwd_size);
    std::string json = "{\"pid\":" + std::to_string(GetCurrentProcessId()) + ",\"parent_pid\":" + std::to_string(ParentPid()) +
        ",\"image\":" + Json(image) + ",\"cwd\":" + Json(cwd) + ",\"argv\":[";
    for (std::size_t i = 0; i < args.size(); ++i) { if (i) json += ','; json += Json(args[i]); }
    json += "]}";
    const HANDLE file = CreateFileW(args[2].c_str(), GENERIC_WRITE, FILE_SHARE_READ, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (file == INVALID_HANDLE_VALUE) { CloseHandle(event); return 75; }
    DWORD written = 0;
    const bool saved = WriteFile(file, json.data(), static_cast<DWORD>(json.size()), &written, nullptr) && written == json.size() && FlushFileBuffers(file);
    CloseHandle(file);
    const DWORD result = saved ? WaitForSingleObject(event, 15000) : WAIT_FAILED;
    CloseHandle(event);
    return saved && result == WAIT_OBJECT_0 ? 0 : 76;
}
