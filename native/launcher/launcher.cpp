// Stable installed entry point. It never selects a payload from argv/PATH/CWD,
// mutates installation files, downloads anything, invokes a shell or falls back.
#include "launcher_contract.h"
#include <windows.h>
#include <shellapi.h>
#include <bcrypt.h>
#include <array>
#include <memory>
#include <stdexcept>
#include <utility>

namespace {
class Handle {
public:
    explicit Handle(HANDLE value = INVALID_HANDLE_VALUE) : value_(value) {}
    ~Handle() { if (value_ != INVALID_HANDLE_VALUE && value_ != nullptr) CloseHandle(value_); }
    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;
    Handle(Handle&& other) noexcept : value_(std::exchange(other.value_, INVALID_HANDLE_VALUE)) {}
    HANDLE get() const { return value_; }
private: HANDLE value_;
};
struct Error { const wchar_t* message; };
[[noreturn]] void Fail(const wchar_t* message) { throw Error{message}; }
std::wstring ModulePath() {
    std::wstring path(32768, L'\0');
    const DWORD size = GetModuleFileNameW(nullptr, path.data(), static_cast<DWORD>(path.size()));
    if (!size || size >= path.size()) Fail(L"Windows could not locate this Luma launcher.");
    path.resize(size);
    if (path.compare(0, 4, L"\\\\?\\") == 0) path.erase(0, 4);
    if (path.size() < 4 || !((path[0] >= L'A' && path[0] <= L'Z') || (path[0] >= L'a' && path[0] <= L'z')) ||
        path[1] != L':' || path[2] != L'\\' || path.find(L':', 2) != std::wstring::npos || path.find(L'/') != std::wstring::npos)
        Fail(L"Luma must be installed on a local Windows drive.");
    return path;
}
Handle OpenPlain(const std::wstring& path, bool directory, DWORD sharing = FILE_SHARE_READ) {
    Handle file(CreateFileW(path.c_str(), directory ? FILE_READ_ATTRIBUTES : GENERIC_READ, sharing,
        nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT | (directory ? FILE_FLAG_BACKUP_SEMANTICS : 0), nullptr));
    if (file.get() == INVALID_HANDLE_VALUE) Fail(L"An installation file is missing, locked, or unreadable.");
    BY_HANDLE_FILE_INFORMATION info{};
    if (!GetFileInformationByHandle(file.get(), &info) || (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) ||
        bool(info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != directory || (!directory && info.nNumberOfLinks != 1))
        Fail(L"A linked or invalid installation path was rejected.");
    return file;
}
void PinDirectories(const std::wstring& path, std::vector<Handle>& pins) {
    // Pin each ancestor before opening its child. No FILE_SHARE_DELETE means an
    // inspected directory cannot be renamed/replaced by a junction during launch.
    pins.push_back(OpenPlain(path.substr(0, 3), true, FILE_SHARE_READ | FILE_SHARE_WRITE));
    std::size_t start = 3;
    while (start < path.size()) {
        auto end = path.find(L'\\', start);
        if (end == std::wstring::npos) end = path.size();
        const auto component = path.substr(start, end - start);
        if (component.empty() || component == L"." || component == L".." || component.back() == L'.' || component.back() == L' ')
            Fail(L"The installation path is not canonical.");
        pins.push_back(OpenPlain(path.substr(0, end), true, FILE_SHARE_READ | FILE_SHARE_WRITE));
        start = end + 1;
    }
}
std::string ReadBounded(HANDLE file, std::size_t maximum) {
    LARGE_INTEGER size{};
    if (!GetFileSizeEx(file, &size) || size.QuadPart <= 0 || size.QuadPart > static_cast<LONGLONG>(maximum))
        Fail(L"The installation pointer or metadata has an invalid size.");
    std::string bytes(static_cast<std::size_t>(size.QuadPart), '\0');
    DWORD read = 0;
    if (!ReadFile(file, bytes.data(), static_cast<DWORD>(bytes.size()), &read, nullptr) || read != bytes.size())
        Fail(L"The installation pointer or metadata could not be read completely.");
    return bytes;
}
std::string Sha256(HANDLE file) {
    BCRYPT_ALG_HANDLE algorithm = nullptr;
    if (BCryptOpenAlgorithmProvider(&algorithm, BCRYPT_SHA256_ALGORITHM, nullptr, 0) < 0)
        Fail(L"Windows could not initialize installation integrity verification.");
    struct Algorithm { BCRYPT_ALG_HANDLE value; ~Algorithm() { BCryptCloseAlgorithmProvider(value, 0); } } alg{algorithm};
    BCRYPT_HASH_HANDLE hash = nullptr;
    if (BCryptCreateHash(algorithm, &hash, nullptr, 0, nullptr, 0, 0) < 0) Fail(L"Windows could not create an integrity hash.");
    struct Hash { BCRYPT_HASH_HANDLE value; ~Hash() { BCryptDestroyHash(value); } } guard{hash};
    LARGE_INTEGER size{}, zero{};
    if (!GetFileSizeEx(file, &size) || size.QuadPart <= 0 || size.QuadPart > 512LL * 1024 * 1024 ||
        !SetFilePointerEx(file, zero, nullptr, FILE_BEGIN)) Fail(L"The application executable has an invalid size.");
    std::array<unsigned char, 65536> buffer{};
    LONGLONG total = 0;
    while (total < size.QuadPart) {
        DWORD read = 0;
        if (!ReadFile(file, buffer.data(), static_cast<DWORD>(buffer.size()), &read, nullptr) || !read ||
            BCryptHashData(hash, buffer.data(), read, 0) < 0) Fail(L"The application executable could not be verified.");
        total += read;
    }
    if (total != size.QuadPart) Fail(L"The application executable changed while being verified.");
    std::array<unsigned char, 32> digest{};
    if (BCryptFinishHash(hash, digest.data(), static_cast<ULONG>(digest.size()), 0) < 0)
        Fail(L"Windows could not finish installation integrity verification.");
    constexpr char hex[] = "0123456789abcdef";
    std::string result;
    for (auto byte : digest) { result.push_back(hex[byte >> 4]); result.push_back(hex[byte & 15]); }
    return result;
}
std::wstring WideAscii(const std::string& text) { return std::wstring(text.begin(), text.end()); }
int Run() {
    SetErrorMode(SEM_FAILCRITICALERRORS | SEM_NOOPENFILEERRORBOX);
    if (!SetDllDirectoryW(L"") || !SetDefaultDllDirectories(LOAD_LIBRARY_SEARCH_SYSTEM32))
        Fail(L"Windows could not secure the launcher DLL search path.");
    const std::wstring module = ModulePath();
    const std::wstring root = module.substr(0, module.find_last_of(L'\\'));
    if (root.size() < 3) Fail(L"Luma must be installed in its own folder, not a drive root.");
    std::vector<Handle> directories;
    PinDirectories(root, directories);
    auto owner = OpenPlain(root + L"\\" + luma::launcher::kOwnerName, false);
    std::string owner_hash;
    if (!luma::launcher::ParseOwner(ReadBounded(owner.get(), 128), owner_hash))
        Fail(L"The Luma launcher ownership marker is missing or invalid.");
    auto self = OpenPlain(module, false);
    if (Sha256(self.get()) != owner_hash) Fail(L"The installed Luma launcher has changed or is damaged.");
    luma::launcher::ActivePayload active;
    {
        // Writers atomically replace the pointer; this handle reads one complete
        // old or new snapshot, never a partially overwritten in-place file.
        auto pointer = OpenPlain(root + L"\\" + luma::launcher::kPointerName, false, FILE_SHARE_READ | FILE_SHARE_DELETE);
        if (!luma::launcher::ParsePointer(ReadBounded(pointer.get(), luma::launcher::kMaxPointerBytes), active))
            Fail(L"The active Luma version pointer is missing or invalid.");
    }
    const std::wstring versions = root + L"\\versions";
    const std::wstring payload = versions + L"\\" + WideAscii(active.payload_id);
    directories.push_back(OpenPlain(versions, true, FILE_SHARE_READ | FILE_SHARE_WRITE));
    directories.push_back(OpenPlain(payload, true, FILE_SHARE_READ | FILE_SHARE_WRITE));
    auto metadata = OpenPlain(payload + L"\\luma-install.json", false);
    if (!luma::launcher::ValidateMetadata(ReadBounded(metadata.get(), luma::launcher::kMaxMetadataBytes), active))
        Fail(L"The selected Luma version does not match its installer metadata.");
    const std::wstring executable = payload + L"\\Luma.exe";
    auto binary = OpenPlain(executable, false);
    if (Sha256(binary.get()) != active.executable_sha256) Fail(L"The selected Luma executable has changed or is damaged.");
    int argc = 0;
    LPWSTR* argv = CommandLineToArgvW(GetCommandLineW(), &argc);
    if (!argv) Fail(L"Windows could not read the requested image arguments.");
    std::vector<std::wstring> arguments;
    for (int index = 1; index < argc; ++index) arguments.emplace_back(argv[index]);
    LocalFree(argv);
    auto command = luma::launcher::CommandLine(executable, arguments);
    if (command.size() >= 32767) Fail(L"The requested image arguments exceed the Windows command-line limit.");
    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION child{};
    // Explicit application name avoids Windows' ambiguous executable/PATH search.
    // No inherited handles, no shell, fixed versioned working directory.
    if (!CreateProcessW(executable.c_str(), command.data(), nullptr, nullptr, FALSE,
                        CREATE_UNICODE_ENVIRONMENT, nullptr, payload.c_str(), &startup, &child))
        Fail(L"Windows could not start the verified Luma version.");
    Handle process(child.hProcess), thread(child.hThread);
    if (WaitForSingleObject(process.get(), INFINITE) != WAIT_OBJECT_0)
        Fail(L"Windows could not wait for the Luma application.");
    DWORD exit_code = 1;
    if (!GetExitCodeProcess(process.get(), &exit_code)) Fail(L"Windows could not read the Luma application result.");
    return static_cast<int>(exit_code);
}
}  // namespace
int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    try { return Run(); }
    catch (const Error& error) {
        const std::wstring message = std::wstring(error.message) +
            L"\n\nNo other version was started. Run the official Luma Setup in this installation folder to repair it.";
        MessageBoxW(nullptr, message.c_str(), L"Luma could not start", MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
    } catch (...) {
        MessageBoxW(nullptr, L"Luma could not verify its installed version. No other version was started. Run the official Luma Setup to repair this installation.",
                    L"Luma could not start", MB_OK | MB_ICONERROR | MB_SETFOREGROUND);
    }
    return 120;
}
