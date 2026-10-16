#ifndef WIN32_LEAN_AND_MEAN
#define WIN32_LEAN_AND_MEAN
#endif
#ifndef NOMINMAX
#define NOMINMAX
#endif
#include <windows.h>

#include <array>
#include <cstdint>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <string>
#include <utility>

namespace {

constexpr std::uint32_t kMaxSize = 1024;
constexpr DWORD kMaxInputBytes = 1024;
constexpr DWORD kMaxPixelBytes = 4 * 1024 * 1024;

class Handle {
public:
    explicit Handle(HANDLE value) noexcept : value_(value) {}
    ~Handle() {
        if (valid()) {
            CloseHandle(value_);
        }
    }

    Handle(const Handle&) = delete;
    Handle& operator=(const Handle&) = delete;

    bool valid() const noexcept {
        return value_ != nullptr && value_ != INVALID_HANDLE_VALUE;
    }
    HANDLE get() const noexcept { return value_; }

private:
    HANDLE value_;
};

class MappingView {
public:
    explicit MappingView(void* value) noexcept : value_(value) {}
    ~MappingView() {
        if (value_) {
            UnmapViewOfFile(value_);
        }
    }

    MappingView(const MappingView&) = delete;
    MappingView& operator=(const MappingView&) = delete;

    void* get() const noexcept { return value_; }

private:
    void* value_;
};

int Fail(const wchar_t* message) {
    std::fwprintf(stderr, L"Luma fixture: %ls\n", message);
    return 2;
}

int WinFail(const wchar_t* operation) {
    const DWORD error = GetLastError();
    std::fwprintf(stderr, L"Luma fixture: %ls failed (%lu)\n",
                  operation, static_cast<unsigned long>(error));
    return 3;
}

bool ParseSize(const wchar_t* text, std::uint32_t& result) {
    if (!text || !*text) {
        return false;
    }
    std::uint32_t value = 0;
    for (; *text; ++text) {
        if (*text < L'0' || *text > L'9') {
            return false;
        }
        value = value * 10 + static_cast<std::uint32_t>(*text - L'0');
        if (value > kMaxSize) {
            return false;
        }
    }
    result = value;
    return value != 0;
}

bool IsWorkerKind(const wchar_t* kind) {
    return std::wcscmp(kind, L"raster") == 0 ||
           std::wcscmp(kind, L"hdr") == 0 ||
           std::wcscmp(kind, L"exr") == 0 ||
           std::wcscmp(kind, L"raw") == 0;
}

struct Protocol {
    std::array<std::string, 3> lines;
    std::size_t count = 0;
};

int ReadProtocol(HANDLE input, Protocol& protocol) {
    LARGE_INTEGER length{};
    if (!GetFileSizeEx(input, &length)) {
        return WinFail(L"GetFileSizeEx(input)");
    }
    if (length.QuadPart <= 0 || length.QuadPart > kMaxInputBytes) {
        return Fail(L"input must contain 1 to 1024 bytes");
    }

    std::array<char, kMaxInputBytes> buffer{};
    const DWORD expected = static_cast<DWORD>(length.QuadPart);
    DWORD total = 0;
    while (total < expected) {
        DWORD received = 0;
        if (!ReadFile(input, buffer.data() + total, expected - total,
                      &received, nullptr)) {
            return WinFail(L"ReadFile(input)");
        }
        if (received == 0) {
            return Fail(L"input was truncated");
        }
        total += received;
    }

    char extra = 0;
    DWORD received = 0;
    if (!ReadFile(input, &extra, 1, &received, nullptr)) {
        return WinFail(L"ReadFile(input end)");
    }
    if (received != 0) {
        return Fail(L"input changed while being read");
    }

    // Accept LF or CRLF, with an optional single final newline.
    std::size_t start = 0;
    while (start < total) {
        if (protocol.count == protocol.lines.size()) {
            return Fail(L"too many protocol lines");
        }
        std::size_t end = start;
        while (end < total && buffer[end] != '\n') {
            ++end;
        }
        std::string line(buffer.data() + start, end - start);
        if (end < total && !line.empty() && line.back() == '\r') {
            line.pop_back();
        }
        if (line.empty()) {
            return Fail(L"empty protocol line");
        }
        for (unsigned char ch : line) {
            if (ch < 0x20 || ch > 0x7e) {
                return Fail(L"protocol values must be printable ASCII");
            }
        }
        protocol.lines[protocol.count++] = std::move(line);
        start = end < total ? end + 1 : end;
    }

    static constexpr const char* scenarios[] = {
        "valid", "valid-max", "bad-magic", "zero-width",
        "oversized-dimension", "truncated-header", "truncated-pixels",
        "extra-data", "oversized-output", "exit-nonzero", "sleep"
    };

    bool known = false;
    for (const char* scenario : scenarios) {
        if (protocol.lines[0] == scenario) {
            known = true;
            break;
        }
    }
    if (!known) {
        return Fail(L"unknown scenario");
    }

    const std::size_t required = protocol.lines[0] == "sleep" ? 3 : 1;
    if (protocol.count != required) {
        return Fail(L"unexpected protocol line count");
    }
    return 0;
}

bool WriteAll(HANDLE output, const BYTE* data, DWORD bytes) {
    while (bytes != 0) {
        DWORD written = 0;
        if (!WriteFile(output, data, bytes, &written, nullptr)) {
            return false;
        }
        if (written == 0) {
            SetLastError(ERROR_WRITE_FAULT);
            return false;
        }
        data += written;
        bytes -= written;
    }
    return true;
}

bool WritePixelBytes(HANDLE output, DWORD bytes) {
    std::array<BYTE, 64 * 1024> block{};
    for (std::size_t i = 0; i < block.size(); i += 4) {
        block[i] = 255;
        block[i + 3] = 128;
    }

    while (bytes != 0) {
        const DWORD chunk = bytes < block.size()
                                ? bytes
                                : static_cast<DWORD>(block.size());
        if (!WriteAll(output, block.data(), chunk)) {
            return false;
        }
        bytes -= chunk;
    }
    return true;
}

void StoreLe32(BYTE* destination, std::uint32_t value) {
    for (unsigned i = 0; i < 4; ++i) {
        destination[i] = static_cast<BYTE>(value >> (8 * i));
    }
}

int SleepScenario(const Protocol& protocol) {
    // Protocol validation guarantees lossless ASCII-to-UTF-16 conversion.
    const std::wstring mappingName(protocol.lines[1].begin(),
                                   protocol.lines[1].end());
    const std::wstring eventName(protocol.lines[2].begin(),
                                 protocol.lines[2].end());

    Handle mapping(OpenFileMappingW(FILE_MAP_WRITE, FALSE,
                                    mappingName.c_str()));
    if (!mapping.valid()) {
        return WinFail(L"OpenFileMappingW");
    }

    MappingView view(MapViewOfFile(mapping.get(), FILE_MAP_WRITE,
                                  0, 0, sizeof(DWORD)));
    if (!view.get()) {
        return WinFail(L"MapViewOfFile");
    }

    Handle event(OpenEventW(EVENT_MODIFY_STATE, FALSE, eventName.c_str()));
    if (!event.valid()) {
        return WinFail(L"OpenEventW");
    }

    const DWORD pid = GetCurrentProcessId();
    std::memcpy(view.get(), &pid, sizeof(pid));
    MemoryBarrier();
    if (!SetEvent(event.get())) {
        return WinFail(L"SetEvent");
    }

    for (;;) {
        Sleep(INFINITE);
    }
}

int Run(int argc, wchar_t* argv[]) {
    if (argc != 6 || std::wcscmp(argv[1], L"--thumbnail-worker") != 0 ||
        !IsWorkerKind(argv[2])) {
        return Fail(L"expected --thumbnail-worker "
                    L"raster|hdr|exr|raw <input> <output> <size>");
    }

    std::uint32_t size = 0;
    if (!ParseSize(argv[5], size)) {
        return Fail(L"size must be an integer from 1 to 1024");
    }

    constexpr DWORD sharing = FILE_SHARE_READ | FILE_SHARE_WRITE;
    Handle input(CreateFileW(argv[3], GENERIC_READ, sharing, nullptr,
                             OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
    if (!input.valid()) {
        return WinFail(L"CreateFileW(input)");
    }
    if (GetFileType(input.get()) != FILE_TYPE_DISK) {
        return Fail(L"input must be a disk file");
    }

    Protocol protocol;
    const int protocolResult = ReadProtocol(input.get(), protocol);
    if (protocolResult != 0) {
        return protocolResult;
    }

    Handle output(CreateFileW(argv[4], GENERIC_WRITE, sharing, nullptr,
                              OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr));
    if (!output.valid()) {
        return WinFail(L"CreateFileW(output)");
    }
    if (GetFileType(output.get()) != FILE_TYPE_DISK) {
        return Fail(L"output must be a disk file");
    }

    const std::string& scenario = protocol.lines[0];
    if (scenario == "exit-nonzero") {
        return 17;
    }
    if (scenario == "sleep") {
        return SleepScenario(protocol);
    }

    // Preserve the existing file and its identity; never replace it.
    LARGE_INTEGER offset{};
    if (!SetFilePointerEx(output.get(), offset, nullptr, FILE_BEGIN)) {
        return WinFail(L"SetFilePointerEx(output start)");
    }
    if (!SetEndOfFile(output.get())) {
        return WinFail(L"SetEndOfFile(output truncate)");
    }

    std::array<BYTE, 16> header{
        'L', 'U', 'M', 'A', 'T', 'H', 'M', '1'
    };
    std::uint32_t width = 1;
    std::uint32_t height = 1;
    DWORD pixelBytes = 4;

    if (scenario == "valid-max") {
        width = height = size;
        pixelBytes = size * size * 4;
    } else if (scenario == "zero-width") {
        width = 0;
    } else if (scenario == "oversized-dimension") {
        width = size + 1;
    } else if (scenario == "truncated-pixels") {
        pixelBytes = 3;
    } else if (scenario == "extra-data") {
        pixelBytes = 5;
    } else if (scenario == "bad-magic") {
        header[0] = 'X';
    }

    StoreLe32(header.data() + 8, width);
    StoreLe32(header.data() + 12, height);

    const DWORD headerBytes = scenario == "truncated-header" ? 8 : 16;
    if (!WriteAll(output.get(), header.data(), headerBytes)) {
        return WinFail(L"WriteFile(header)");
    }
    if (scenario == "truncated-header") {
        return 0;
    }

    if (scenario == "oversized-output") {
        offset.QuadPart = 16LL + kMaxPixelBytes + 1;
        if (!SetFilePointerEx(output.get(), offset, nullptr, FILE_BEGIN)) {
            return WinFail(L"SetFilePointerEx(oversized output)");
        }
        if (!SetEndOfFile(output.get())) {
            return WinFail(L"SetEndOfFile(oversized output)");
        }
        return 0;
    }

    if (!WritePixelBytes(output.get(), pixelBytes)) {
        return WinFail(L"WriteFile(pixels)");
    }
    return 0;
}

} // namespace

int wmain(int argc, wchar_t* argv[]) {
    try {
        return Run(argc, argv);
    } catch (...) {
        return Fail(L"unexpected exception");
    }
}
