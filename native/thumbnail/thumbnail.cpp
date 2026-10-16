#include <windows.h>
#include <thumbcache.h>
#include <propsys.h>
#include <shlobj.h>
#include <knownfolders.h>
#include <bcrypt.h>

#include <algorithm>
#include <array>
#include <atomic>
#include <cstdint>
#include <cstring>
#include <exception>
#include <new>
#include <string>
#include <utility>
#include <vector>

// This DLL contains no image decoder, Python, or Qt.
//
// Security/lifetime contract:
// - Only bytes from the supplied IStream become the source file.
// - IStream::Stat, source filenames, and original filesystem paths are unused.
// - Directory ancestors and files are checked for unexpected types/reparse points.
// - Input/output handles remain open until cleanup; output cannot be replaced.
// - Luma.exe is the fixed sibling of this DLL, launched without a shell.
// - A suspended child must enter its limiting job before it can run.
// - No breakaway, elevation, or process-isolation override is used.
//
// The deadline bounds the helper wait and is checked around stream/file work.
// An arbitrary synchronous IStream::Read/Seek, or blocked filesystem API, cannot
// be forcibly bounded safely inside this in-process COM implementation.

namespace {

constexpr std::uint64_t kMaxSource = 256ull * 1024 * 1024;
constexpr std::uint64_t kMaxPixels = 4ull * 1024 * 1024;
constexpr ULONGLONG kDeadlineMs = 10000;
constexpr SIZE_T kMemoryLimit = 768ull * 1024 * 1024;

HMODULE g_module = nullptr;
std::atomic<long> g_objects{0};
std::atomic<long> g_serverLocks{0};

struct Mode {
    CLSID clsid;
    const wchar_t* name;
    const wchar_t* inputName;
};

const Mode kModes[] = {
    // {A1D48846-7E23-4C76-B5A7-88FE510AEB06}
    {{0xa1d48846, 0x7e23, 0x4c76,
      {0xb5, 0xa7, 0x88, 0xfe, 0x51, 0x0a, 0xeb, 0x06}},
     L"raster", L"input.png"},
    // {5C7F845B-D0D3-4CC7-8A47-E240EBD08C56}
    {{0x5c7f845b, 0xd0d3, 0x4cc7,
      {0x8a, 0x47, 0xe2, 0x40, 0xeb, 0xd0, 0x8c, 0x56}},
     L"hdr", L"input.hdr"},
    // {97144AB9-77CE-4B28-B32D-0A7A80DC57AA}
    {{0x97144ab9, 0x77ce, 0x4b28,
      {0xb3, 0x2d, 0x0a, 0x7a, 0x80, 0xdc, 0x57, 0xaa}},
     L"exr", L"input.exr"},
    // {C9B8C1F5-D343-45D7-A5BA-44AA071D1D2B}
    {{0xc9b8c1f5, 0xd343, 0x45d7,
      {0xa5, 0xba, 0x44, 0xaa, 0x07, 0x1d, 0x1d, 0x2b}},
     L"raw", L"input.nef"}
};

struct Failure {
    HRESULT hr;
};

[[noreturn]] void Fail(HRESULT hr) {
    throw Failure{hr};
}

[[noreturn]] void FailLastError() {
    const DWORD error = GetLastError();
    Fail(error ? HRESULT_FROM_WIN32(error) : E_FAIL);
}

void CheckHr(HRESULT hr) {
    if (FAILED(hr)) Fail(hr);
}

void CheckDeadline(ULONGLONG deadline) {
    if (GetTickCount64() >= deadline)
        Fail(HRESULT_FROM_WIN32(ERROR_TIMEOUT));
}

DWORD RemainingMs(ULONGLONG deadline) {
    const ULONGLONG now = GetTickCount64();
    if (now >= deadline)
        Fail(HRESULT_FROM_WIN32(ERROR_TIMEOUT));
    return static_cast<DWORD>(deadline - now);
}

class Handle {
public:
    Handle() noexcept = default;
    explicit Handle(HANDLE value) noexcept : value_(value) {}
    ~Handle() { reset(); }

    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;

    Handle(Handle&& other) noexcept : value_(other.release()) {}
    Handle& operator=(Handle&& other) noexcept {
        if (this != &other) reset(other.release());
        return *this;
    }

    explicit operator bool() const noexcept {
        return value_ && value_ != INVALID_HANDLE_VALUE;
    }

    HANDLE get() const noexcept { return value_; }

    HANDLE release() noexcept {
        const HANDLE result = value_;
        value_ = INVALID_HANDLE_VALUE;
        return result;
    }

    void reset(HANDLE value = INVALID_HANDLE_VALUE) noexcept {
        if (*this) CloseHandle(value_);
        value_ = value;
    }

private:
    HANDLE value_ = INVALID_HANDLE_VALUE;
};

void CheckDiskObject(HANDLE handle, bool directory) {
    if (GetFileType(handle) != FILE_TYPE_DISK)
        Fail(HRESULT_FROM_WIN32(ERROR_INVALID_DATA));

    BY_HANDLE_FILE_INFORMATION info{};
    if (!GetFileInformationByHandle(handle, &info)) FailLastError();

    const bool isDirectory =
        (info.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) != 0;
    if (isDirectory != directory ||
        (info.dwFileAttributes & FILE_ATTRIBUTE_REPARSE_POINT) != 0) {
        Fail(HRESULT_FROM_WIN32(ERROR_INVALID_DATA));
    }
}

// Deliberately supports only local, absolute drive paths. Rejecting UNC paths
// and all reparse-point ancestors keeps the path-pinning policy explicit.
std::wstring NormalizeLocalPath(std::wstring path) {
    if (path.compare(0, 4, L"\\\\?\\") == 0)
        path.erase(0, 4);

    const bool driveLetter = !path.empty() &&
        ((path[0] >= L'A' && path[0] <= L'Z') ||
         (path[0] >= L'a' && path[0] <= L'z'));

    if (!driveLetter || path.size() < 3 ||
        path[1] != L':' || path[2] != L'\\') {
        Fail(HRESULT_FROM_WIN32(ERROR_BAD_PATHNAME));
    }

    if (path.size() > 3 && path.back() == L'\\')
        path.pop_back();

    for (std::size_t begin = 3; begin < path.size();) {
        const std::size_t end = path.find(L'\\', begin);
        const auto component = path.substr(
            begin, end == std::wstring::npos ? end : end - begin);

        if (component.empty() || component == L"." || component == L".." ||
            component.find_first_of(L":/\"<>|?*") != std::wstring::npos) {
            Fail(HRESULT_FROM_WIN32(ERROR_BAD_PATHNAME));
        }

        if (end == std::wstring::npos) break;
        begin = end + 1;
    }
    return path;
}

std::wstring LongPath(const std::wstring& path) {
    return L"\\\\?\\" + path;
}

Handle OpenDirectory(const std::wstring& path) {
    Handle handle(CreateFileW(
        LongPath(path).c_str(), FILE_READ_ATTRIBUTES,
        FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr, OPEN_EXISTING,
        FILE_FLAG_BACKUP_SEMANTICS | FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
    if (!handle) FailLastError();
    CheckDiskObject(handle.get(), true);
    return handle;
}

class PinnedDirectories {
public:
    void open(const std::wstring& normalizedPath) {
        handles_.push_back(OpenDirectory(normalizedPath.substr(0, 3)));

        std::size_t begin = 3;
        while (begin < normalizedPath.size()) {
            const std::size_t end = normalizedPath.find(L'\\', begin);
            handles_.push_back(OpenDirectory(normalizedPath.substr(0, end)));
            if (end == std::wstring::npos) break;
            begin = end + 1;
        }
    }

private:
    // No FILE_SHARE_DELETE: checked directory ancestors cannot be renamed
    // or replaced while their handles remain open.
    std::vector<Handle> handles_;
};

std::wstring ModuleDirectory() {
    std::wstring path(32768, L'\0');
    const DWORD length = GetModuleFileNameW(
        g_module, path.data(), static_cast<DWORD>(path.size()));
    if (!length) FailLastError();
    if (length >= path.size())
        Fail(HRESULT_FROM_WIN32(ERROR_FILENAME_EXCED_RANGE));

    path.resize(length);
    path = NormalizeLocalPath(std::move(path));

    const std::size_t slash = path.find_last_of(L'\\');
    if (slash == std::wstring::npos)
        Fail(HRESULT_FROM_WIN32(ERROR_BAD_PATHNAME));

    return slash == 2 ? path.substr(0, 3) : path.substr(0, slash);
}

std::wstring Join(const std::wstring& directory, const wchar_t* leaf) {
    return directory + (directory.back() == L'\\' ? L"" : L"\\") + leaf;
}

std::wstring RandomDirectoryLeaf() {
    GUID id{};
    const NTSTATUS status = BCryptGenRandom(
        nullptr, reinterpret_cast<PUCHAR>(&id), sizeof(id),
        BCRYPT_USE_SYSTEM_PREFERRED_RNG);
    if (status < 0) Fail(E_FAIL);

    id.Data3 = static_cast<USHORT>((id.Data3 & 0x0fff) | 0x4000);
    id.Data4[0] = static_cast<UCHAR>((id.Data4[0] & 0x3f) | 0x80);

    wchar_t text[39]{};
    if (StringFromGUID2(id, text, ARRAYSIZE(text)) != ARRAYSIZE(text))
        Fail(E_FAIL);
    return L"LumaThumb-" + std::wstring(text);
}

struct TempFiles {
    PinnedDirectories parents;
    Handle directoryHandle;
    Handle inputHandle;
    Handle outputHandle;
    std::wstring directory;
    std::wstring input;
    std::wstring output;
    bool madeDirectory = false;
    bool madeInput = false;
    bool madeOutput = false;

    ~TempFiles() {
        // Delete only the exact names created by this object. Never recurse,
        // enumerate, follow links, or remove unrelated content.
        outputHandle.reset();
        inputHandle.reset();
        if (madeOutput) DeleteFileW(output.c_str());
        if (madeInput) DeleteFileW(input.c_str());
        directoryHandle.reset();
        if (madeDirectory) RemoveDirectoryW(directory.c_str());
    }

    void makeInputReadOnly(ULONGLONG deadline) {
        CheckDeadline(deadline);
        if (!FlushFileBuffers(inputHandle.get())) FailLastError();

        // LibRaw opens READ/share-READ. A retained writer would conflict.
        // Keep a no-delete read handle throughout the transition, so there is
        // never a pathname-replacement window. The final pin also denies writes.
        Handle transition(CreateFileW(
            input.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
            nullptr, OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
        if (!transition) FailLastError();
        CheckDiskObject(transition.get(), false);
        inputHandle.reset();
        Handle readOnly(CreateFileW(
            input.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr, OPEN_EXISTING,
            FILE_FLAG_OPEN_REPARSE_POINT | FILE_FLAG_SEQUENTIAL_SCAN, nullptr));
        if (!readOnly) FailLastError();
        CheckDiskObject(readOnly.get(), false);
        inputHandle = std::move(readOnly);
        CheckDeadline(deadline);
    }

    void create(const Mode& mode) {
        PWSTR knownFolder = nullptr;
        CheckHr(SHGetKnownFolderPath(
            FOLDERID_LocalAppDataLow, KF_FLAG_DEFAULT, nullptr, &knownFolder));

        std::wstring base;
        try {
            base = NormalizeLocalPath(knownFolder);
        } catch (...) {
            CoTaskMemFree(knownFolder);
            throw;
        }
        CoTaskMemFree(knownFolder);

        parents.open(base);
        const std::wstring localDirectory =
            Join(base, RandomDirectoryLeaf().c_str());

        directory = LongPath(localDirectory);
        input = LongPath(Join(localDirectory, mode.inputName));
        output = LongPath(Join(localDirectory, L"thumbnail.bin"));

        // Never accept an existing directory, even on a random-name collision.
        if (!CreateDirectoryW(directory.c_str(), nullptr)) FailLastError();
        madeDirectory = true;
        directoryHandle = OpenDirectory(localDirectory);

        inputHandle.reset(CreateFileW(
            input.c_str(), GENERIC_READ | GENERIC_WRITE, FILE_SHARE_READ,
            nullptr, CREATE_NEW,
            FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_OPEN_REPARSE_POINT |
                FILE_FLAG_SEQUENTIAL_SCAN,
            nullptr));
        if (!inputHandle) FailLastError();
        madeInput = true;
        CheckDiskObject(inputHandle.get(), false);

        outputHandle.reset(CreateFileW(
            output.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE,
            nullptr, CREATE_NEW,
            FILE_ATTRIBUTE_TEMPORARY | FILE_FLAG_OPEN_REPARSE_POINT,
            nullptr));
        if (!outputHandle) FailLastError();
        madeOutput = true;
        CheckDiskObject(outputHandle.get(), false);

        // Neither file shares DELETE, so the helper cannot replace either
        // pathname. Its output must be written/truncated in place.
    }
};

void WriteAll(HANDLE file, const BYTE* data, DWORD count,
              ULONGLONG deadline) {
    while (count) {
        CheckDeadline(deadline);
        DWORD written = 0;
        if (!WriteFile(file, data, count, &written, nullptr)) FailLastError();
        CheckDeadline(deadline);
        if (!written || written > count) Fail(STG_E_WRITEFAULT);
        data += written;
        count -= written;
    }
}

void CopyStream(IStream* stream, HANDLE destination, ULONGLONG deadline) {
    CheckDeadline(deadline);
    LARGE_INTEGER zero{};
    CheckHr(stream->Seek(zero, STREAM_SEEK_SET, nullptr));
    CheckDeadline(deadline);

    std::array<BYTE, 64 * 1024> buffer{};
    std::uint64_t total = 0;

    for (;;) {
        CheckDeadline(deadline);
        ULONG received = 0;
        // A final one-byte probe distinguishes exactly 256 MiB from excess.
        const ULONG wanted = total == kMaxSource
            ? 1u : static_cast<ULONG>(buffer.size());

        const HRESULT hr = stream->Read(buffer.data(), wanted, &received);
        CheckDeadline(deadline);
        CheckHr(hr);

        if (received > wanted) Fail(STG_E_READFAULT);
        if (received > kMaxSource - total)
            Fail(HRESULT_FROM_WIN32(ERROR_FILE_TOO_LARGE));

        if (received)
            WriteAll(destination, buffer.data(), received, deadline);
        total += received;

        if (!received || hr == S_FALSE) break;
    }

    if (!total) Fail(HRESULT_FROM_WIN32(ERROR_INVALID_DATA));
}

std::wstring QuoteArgument(const std::wstring& argument) {
    std::wstring result = L"\"";
    std::size_t backslashes = 0;

    for (const wchar_t ch : argument) {
        if (ch == L'\\') {
            ++backslashes;
        } else if (ch == L'"') {
            result.append(backslashes * 2 + 1, L'\\');
            result.push_back(ch);
            backslashes = 0;
        } else {
            result.append(backslashes, L'\\');
            result.push_back(ch);
            backslashes = 0;
        }
    }
    result.append(backslashes * 2, L'\\');
    result.push_back(L'"');
    return result;
}

struct ChildProcess {
    Handle job;
    Handle process;
    Handle thread;

    ~ChildProcess() {
        if (process &&
            WaitForSingleObject(process.get(), 0) != WAIT_OBJECT_0) {
            // Also covers failure to assign the still-suspended child.
            TerminateProcess(process.get(), ERROR_CANCELLED);
        }
        job.reset(); // KILL_ON_JOB_CLOSE is the second termination safeguard.
        if (process) WaitForSingleObject(process.get(), 100);
    }
};

void RunHelper(const Mode& mode, const TempFiles& files, UINT size,
               ULONGLONG deadline) {
    CheckDeadline(deadline);

    const std::wstring directory = ModuleDirectory();
    PinnedDirectories parents;
    parents.open(directory);

    const std::wstring executable = LongPath(Join(directory, L"Luma.exe"));
    Handle executableHandle(CreateFileW(
        executable.c_str(), GENERIC_READ, FILE_SHARE_READ, nullptr,
        OPEN_EXISTING, FILE_FLAG_OPEN_REPARSE_POINT, nullptr));
    if (!executableHandle) FailLastError();
    CheckDiskObject(executableHandle.get(), false);

    std::wstring command =
        QuoteArgument(executable) + L" --thumbnail-worker " + mode.name +
        L" " + QuoteArgument(files.input) +
        L" " + QuoteArgument(files.output) +
        L" " + std::to_wstring(size);

    ChildProcess child;
    child.job.reset(CreateJobObjectW(nullptr, nullptr));
    if (!child.job) FailLastError();

    JOBOBJECT_EXTENDED_LIMIT_INFORMATION limits{};
    limits.BasicLimitInformation.LimitFlags =
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE |
        JOB_OBJECT_LIMIT_ACTIVE_PROCESS |
        JOB_OBJECT_LIMIT_PROCESS_MEMORY |
        JOB_OBJECT_LIMIT_JOB_MEMORY;
    limits.BasicLimitInformation.ActiveProcessLimit = 1;
    limits.ProcessMemoryLimit = kMemoryLimit;
    limits.JobMemoryLimit = kMemoryLimit;

    if (!SetInformationJobObject(
            child.job.get(), JobObjectExtendedLimitInformation,
            &limits, sizeof(limits))) {
        FailLastError();
    }

    STARTUPINFOW startup{};
    startup.cb = sizeof(startup);
    PROCESS_INFORMATION information{};

    CheckDeadline(deadline);
    if (!CreateProcessW(
            executable.c_str(), command.data(), nullptr, nullptr, FALSE,
            CREATE_SUSPENDED | CREATE_NO_WINDOW, nullptr, directory.c_str(),
            &startup, &information)) {
        FailLastError();
    }

    child.process.reset(information.hProcess);
    child.thread.reset(information.hThread);

    // Fail closed. Never resume outside the job and never request breakaway.
    if (!AssignProcessToJobObject(child.job.get(), child.process.get()))
        FailLastError();

    CheckDeadline(deadline);
    const DWORD previousSuspendCount = ResumeThread(child.thread.get());
    if (previousSuspendCount == static_cast<DWORD>(-1)) FailLastError();
    if (previousSuspendCount != 1) Fail(E_UNEXPECTED);

    const DWORD waitResult =
        WaitForSingleObject(child.process.get(), RemainingMs(deadline));
    if (waitResult == WAIT_TIMEOUT)
        Fail(HRESULT_FROM_WIN32(ERROR_TIMEOUT));
    if (waitResult == WAIT_FAILED) FailLastError();
    if (waitResult != WAIT_OBJECT_0) Fail(E_UNEXPECTED);

    DWORD exitCode = 0;
    if (!GetExitCodeProcess(child.process.get(), &exitCode)) FailLastError();
    if (exitCode != 0) Fail(HRESULT_FROM_WIN32(ERROR_PROCESS_ABORTED));
    CheckDeadline(deadline);
}

void ReadExact(HANDLE file, BYTE* data, DWORD count, ULONGLONG deadline) {
    while (count) {
        CheckDeadline(deadline);
        const DWORD wanted = std::min<DWORD>(count, 64 * 1024);
        DWORD received = 0;
        if (!ReadFile(file, data, wanted, &received, nullptr)) FailLastError();
        CheckDeadline(deadline);
        if (!received || received > wanted) Fail(STG_E_READFAULT);
        data += received;
        count -= received;
    }
}

std::uint32_t ReadLE32(const BYTE* p) noexcept {
    return static_cast<std::uint32_t>(p[0]) |
        (static_cast<std::uint32_t>(p[1]) << 8) |
        (static_cast<std::uint32_t>(p[2]) << 16) |
        (static_cast<std::uint32_t>(p[3]) << 24);
}

struct Bitmap {
    HBITMAP value = nullptr;
    ~Bitmap() { if (value) DeleteObject(value); }
    HBITMAP release() noexcept {
        const HBITMAP result = value;
        value = nullptr;
        return result;
    }
};

HBITMAP ReadBitmap(HANDLE file, UINT requested, ULONGLONG deadline) {
    CheckDiskObject(file, false);

    LARGE_INTEGER fileSize{};
    if (!GetFileSizeEx(file, &fileSize)) FailLastError();
    if (fileSize.QuadPart < 16 ||
        static_cast<std::uint64_t>(fileSize.QuadPart) > 16 + kMaxPixels) {
        Fail(HRESULT_FROM_WIN32(ERROR_INVALID_DATA));
    }

    LARGE_INTEGER zero{};
    if (!SetFilePointerEx(file, zero, nullptr, FILE_BEGIN)) FailLastError();

    BYTE header[16]{};
    ReadExact(file, header, sizeof(header), deadline);
    if (std::memcmp(header, "LUMATHM1", 8) != 0)
        Fail(HRESULT_FROM_WIN32(ERROR_INVALID_DATA));

    const std::uint32_t width = ReadLE32(header + 8);
    const std::uint32_t height = ReadLE32(header + 12);
    if (!width || !height || width > requested || height > requested)
        Fail(HRESULT_FROM_WIN32(ERROR_INVALID_DATA));

    const std::uint64_t bytes =
        static_cast<std::uint64_t>(width) * height * 4;
    if (bytes > kMaxPixels ||
        static_cast<std::uint64_t>(fileSize.QuadPart) != 16 + bytes) {
        Fail(HRESULT_FROM_WIN32(ERROR_INVALID_DATA));
    }

    BITMAPINFO info{};
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = static_cast<LONG>(width);
    info.bmiHeader.biHeight = -static_cast<LONG>(height); // Top-down.
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;
    info.bmiHeader.biSizeImage = static_cast<DWORD>(bytes);

    void* bits = nullptr;
    Bitmap bitmap;
    bitmap.value = CreateDIBSection(
        nullptr, &info, DIB_RGB_COLORS, &bits, nullptr, 0);
    if (!bitmap.value || !bits) Fail(E_OUTOFMEMORY);

    auto* pixels = static_cast<BYTE*>(bits);
    ReadExact(file, pixels, static_cast<DWORD>(bytes), deadline);

    // Worker: straight RGBA8. Shell: straight BGRA8; preserve alpha.
    for (std::uint64_t offset = 0; offset < bytes; offset += 4) {
        std::swap(pixels[offset], pixels[offset + 2]);
    }

    CheckDeadline(deadline);
    return bitmap.release();
}

class ThumbnailProvider final :
    public IThumbnailProvider, public IInitializeWithStream {
public:
    explicit ThumbnailProvider(const Mode& mode) noexcept : mode_(mode) {
        ++g_objects;
    }

    IFACEMETHODIMP QueryInterface(REFIID iid, void** result) override {
        if (!result) return E_POINTER;
        *result = nullptr;
        if (IsEqualIID(iid, IID_IUnknown) ||
            IsEqualIID(iid, IID_IThumbnailProvider)) {
            *result = static_cast<IThumbnailProvider*>(this);
        } else if (IsEqualIID(iid, IID_IInitializeWithStream)) {
            *result = static_cast<IInitializeWithStream*>(this);
        } else {
            return E_NOINTERFACE;
        }
        AddRef();
        return S_OK;
    }

    IFACEMETHODIMP_(ULONG) AddRef() override { return ++references_; }

    IFACEMETHODIMP_(ULONG) Release() override {
        const ULONG count = --references_;
        if (!count) delete this;
        return count;
    }

    IFACEMETHODIMP Initialize(IStream* stream, DWORD mode) override {
        if (stream_) return HRESULT_FROM_WIN32(ERROR_ALREADY_INITIALIZED);
        if (!stream) return E_INVALIDARG;
        if ((mode & 3u) != STGM_READ) return STG_E_ACCESSDENIED;
        try {
            stream->AddRef();
            stream_ = stream;
            return S_OK;
        } catch (const std::bad_alloc&) {
            return E_OUTOFMEMORY;
        } catch (const std::exception&) {
            return E_FAIL;
        } catch (...) {
            return E_UNEXPECTED;
        }
    }

    IFACEMETHODIMP GetThumbnail(
        UINT cx, HBITMAP* bitmap, WTS_ALPHATYPE* alpha) override {
        if (bitmap) *bitmap = nullptr;
        if (alpha) *alpha = WTSAT_UNKNOWN;
        if (!bitmap || !alpha) return E_POINTER;
        if (!cx) return E_INVALIDARG;
        if (!stream_) return CO_E_NOTINITIALIZED;

        try {
            const UINT size = std::max(1u, std::min(cx, 1024u));
            const ULONGLONG deadline = GetTickCount64() + kDeadlineMs;

            TempFiles files;
            files.create(mode_);
            CopyStream(stream_, files.inputHandle.get(), deadline);
            files.makeInputReadOnly(deadline);
            RunHelper(mode_, files, size, deadline);

            *bitmap = ReadBitmap(files.outputHandle.get(), size, deadline);
            *alpha = WTSAT_ARGB;
            return S_OK;
        } catch (const Failure& failure) {
            return failure.hr;
        } catch (const std::bad_alloc&) {
            return E_OUTOFMEMORY;
        } catch (const std::exception&) {
            return E_FAIL;
        } catch (...) {
            return E_UNEXPECTED;
        }
    }

private:
    ~ThumbnailProvider() {
        if (stream_) {
            // COM implementations must not throw across ABI boundaries.
            // Still contain a broken caller-provided stream on release.
            try { stream_->Release(); } catch (...) {}
        }
        --g_objects;
    }

    std::atomic<ULONG> references_{1};
    const Mode& mode_;
    IStream* stream_ = nullptr;
};

class ClassFactory final : public IClassFactory {
public:
    explicit ClassFactory(const Mode& mode) noexcept : mode_(mode) {
        ++g_objects;
    }

    IFACEMETHODIMP QueryInterface(REFIID iid, void** result) override {
        if (!result) return E_POINTER;
        *result = nullptr;
        if (!IsEqualIID(iid, IID_IUnknown) &&
            !IsEqualIID(iid, IID_IClassFactory)) {
            return E_NOINTERFACE;
        }
        *result = static_cast<IClassFactory*>(this);
        AddRef();
        return S_OK;
    }

    IFACEMETHODIMP_(ULONG) AddRef() override { return ++references_; }

    IFACEMETHODIMP_(ULONG) Release() override {
        const ULONG count = --references_;
        if (!count) delete this;
        return count;
    }

    IFACEMETHODIMP CreateInstance(
        IUnknown* outer, REFIID iid, void** result) override {
        if (!result) return E_POINTER;
        *result = nullptr;
        if (outer) return CLASS_E_NOAGGREGATION;

        auto* provider = new (std::nothrow) ThumbnailProvider(mode_);
        if (!provider) return E_OUTOFMEMORY;
        const HRESULT hr = provider->QueryInterface(iid, result);
        provider->Release();
        return hr;
    }

    IFACEMETHODIMP LockServer(BOOL lock) override {
        if (lock) {
            ++g_serverLocks;
            return S_OK;
        }
        long current = g_serverLocks.load();
        while (current > 0) {
            if (g_serverLocks.compare_exchange_weak(current, current - 1))
                return S_OK;
        }
        return E_UNEXPECTED;
    }

private:
    ~ClassFactory() { --g_objects; }
    std::atomic<ULONG> references_{1};
    const Mode& mode_;
};

} // namespace

extern "C" BOOL WINAPI DllMain(HINSTANCE instance, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) g_module = instance;
    // Keep thread notifications compatible with the static CRT.
    return TRUE;
}

extern "C" HRESULT STDAPICALLTYPE DllCanUnloadNow() {
    return g_objects.load() == 0 && g_serverLocks.load() == 0 ? S_OK : S_FALSE;
}

extern "C" HRESULT STDAPICALLTYPE DllGetClassObject(
    REFCLSID clsid, REFIID iid, void** result) {
    if (!result) return E_POINTER;
    *result = nullptr;

    for (const Mode& mode : kModes) {
        if (!IsEqualCLSID(clsid, mode.clsid)) continue;
        auto* factory = new (std::nothrow) ClassFactory(mode);
        if (!factory) return E_OUTOFMEMORY;
        const HRESULT hr = factory->QueryInterface(iid, result);
        factory->Release();
        return hr;
    }
    return CLASS_E_CLASSNOTAVAILABLE;
}
