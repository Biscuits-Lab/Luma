#include <windows.h>
#include <thumbcache.h>
#include <propsys.h>
#include <shlwapi.h>
#include <shlobj.h>

#include <atomic>
#include <cstring>
#include <cwchar>
#include <exception>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <initializer_list>
#include <stdexcept>
#include <string>
#include <thread>
#include <vector>

#include "thumbnail_test_token.h"

namespace {

using GetClassObject =
    HRESULT (STDAPICALLTYPE*)(REFCLSID, REFIID, void**);
using CanUnloadNow = HRESULT (STDAPICALLTYPE*)();

void Require(bool condition, const char* message) {
    if (!condition) throw std::runtime_error(message);
}

// Request one explicit row order from GDI. GetObject's DIBSECTION height
// does not preserve the sign used at creation (Microsoft KB186586), so it
// cannot tell us how to interpret a raw bmBits pointer. GetDIBits normalizes
// both top-down and bottom-up bitmaps to this requested top-down layout.
std::vector<BYTE> ReadTopDownBitmap(HBITMAP bitmap, int width, int height) {
    Require(width > 0 && width <= 256 && height > 0 && height <= 256,
            "Invalid bitmap readback dimensions");
    BITMAP description{};
    Require(GetObjectW(bitmap, sizeof(description), &description) == sizeof(description) &&
                description.bmWidth == width && description.bmHeight == height &&
                description.bmBitsPixel == 32,
            "Bitmap dimensions/type do not match expected preview");
    BITMAPINFO info{};
    info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    info.bmiHeader.biWidth = width;
    info.bmiHeader.biHeight = -height;  // Explicit top-down output, not a query.
    info.bmiHeader.biPlanes = 1;
    info.bmiHeader.biBitCount = 32;
    info.bmiHeader.biCompression = BI_RGB;
    std::vector<BYTE> pixels(static_cast<size_t>(width) * height * 4);
    HDC dc = GetDC(nullptr);
    Require(dc != nullptr, "Cannot create bitmap inspection DC");
    // The source bitmap is never selected into this or any test DC.
    const int rows = GetDIBits(dc, bitmap, 0, static_cast<UINT>(height),
                             pixels.data(), &info, DIB_RGB_COLORS);
    ReleaseDC(nullptr, dc);
    Require(rows == height, "Cannot inspect bitmap pixels");
    return pixels;
}

void TestBitmapReadback() {
    constexpr LONG width = 2, height = 3;
    // Distinct rows and every alpha value differ. Flips, channel swaps, alpha
    // loss and premultiplication must all fail this exact-byte regression.
    const std::vector<BYTE> expected = {
        7, 19, 31, 0,     43, 59, 71, 64,
        83, 97, 109, 128, 127, 139, 151, 192,
        163, 179, 191, 224, 211, 229, 241, 255,
    };
    for (const LONG signedHeight : {-height, height}) {
        BITMAPINFO info{};
        info.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
        info.bmiHeader.biWidth = width;
        info.bmiHeader.biHeight = signedHeight;
        info.bmiHeader.biPlanes = 1;
        info.bmiHeader.biBitCount = 32;
        info.bmiHeader.biCompression = BI_RGB;
        void* bits = nullptr;
        HBITMAP bitmap = CreateDIBSection(nullptr, &info, DIB_RGB_COLORS,
                                         &bits, nullptr, 0);
        Require(bitmap != nullptr && bits != nullptr,
                "Cannot create bitmap readback regression fixture");
        try {
            for (LONG y = 0; y < height; ++y) {
                const LONG row = signedHeight < 0 ? y : height - 1 - y;
                std::memcpy(static_cast<BYTE*>(bits) + static_cast<size_t>(row) * width * 4,
                            expected.data() + static_cast<size_t>(y) * width * 4,
                            static_cast<size_t>(width) * 4);
            }
            const auto actual = ReadTopDownBitmap(bitmap, width, height);
            Require(actual == expected, "Bitmap readback changed orientation/color/alpha");
            DIBSECTION observed{};
            Require(GetObjectW(bitmap, sizeof(observed), &observed) == sizeof(observed),
                    "Cannot inspect regression fixture header");
            std::cout << "bitmap_readback_ok created_height=" << signedHeight
                      << " reported_height=" << observed.dsBmih.biHeight
                      << " orientation=top-down exact_bgra_alpha=true\n";
        } catch (...) {
            DeleteObject(bitmap);
            throw;
        }
        DeleteObject(bitmap);
    }
}

// Test-only expected probes, inherited by the standard-token surrogate
// client. No production provider behavior or its resource limits are changed.
void TestExpectedBitmap(HBITMAP bitmap) {
    wchar_t path[32768]{};
    const DWORD length = GetEnvironmentVariableW(
        L"LUMA_THUMBNAIL_EXPECTED", path, ARRAYSIZE(path));
    if (length == 0) return;
    Require(length < ARRAYSIZE(path), "Expected bitmap path is too long");
    std::ifstream expected{std::filesystem::path(path)};
    std::string magic;
    int width = 0, height = 0, count = 0;
    expected >> magic >> width >> height >> count;
    Require(expected.good() && magic == "LUMA_EXR_PROBES_1" &&
                width > 0 && width <= 256 && height > 0 && height <= 256 &&
                count > 0 && count <= 16,
            "Invalid expected EXR pixel probes");
    const auto pixels = ReadTopDownBitmap(bitmap, width, height);
    for (int probe = 0; probe < count; ++probe) {
        int x = 0, y = 0, bgra[4]{};
        expected >> x >> y >> bgra[0] >> bgra[1] >> bgra[2] >> bgra[3];
        Require(!expected.fail() && x >= 0 && x < width && y >= 0 && y < height,
                "Invalid EXR pixel probe coordinates");
        for (int channel = 0; channel < 4; ++channel) {
            Require(bgra[channel] >= 0 && bgra[channel] <= 255,
                    "Invalid EXR pixel probe value");
            const int actual = pixels[(static_cast<size_t>(y) * width + x) * 4 + channel];
            // The reference was decoded from the compressed file, so this
            // tolerance covers only cache rounding, not DWA source loss.
            const int difference = actual - bgra[channel];
            if (difference < -2 || difference > 2) {
                std::cerr << "EXR pixel mismatch x=" << x << " y=" << y
                          << " channel=" << channel << " actual=" << actual
                          << " expected=" << bgra[channel] << '\n';
                throw std::runtime_error("EXR color/alpha probe mismatch");
            }
        }
    }
    std::cout << "exr_pixel_probes_ok count=" << count << '\n';
}

template<class T>
struct ComPtr {
    T* p = nullptr;
    ~ComPtr() { reset(); }
    ComPtr() = default;
    ComPtr(const ComPtr&) = delete;
    ComPtr& operator=(const ComPtr&) = delete;
    void reset() {
        if (p) p->Release();
        p = nullptr;
    }
    T* operator->() const { return p; }
};

struct TestMode {
    const wchar_t* name;
    const wchar_t* text;
};

const TestMode kModes[] = {
    {L"raster", L"{A1D48846-7E23-4C76-B5A7-88FE510AEB06}"},
    {L"hdr",    L"{5C7F845B-D0D3-4CC7-8A47-E240EBD08C56}"},
    {L"exr",    L"{97144AB9-77CE-4B28-B32D-0A7A80DC57AA}"},
    {L"raw",    L"{C9B8C1F5-D343-45D7-A5BA-44AA071D1D2B}"}
};

CLSID ParseClsid(const wchar_t* text) {
    CLSID id{};
    Require(SUCCEEDED(CLSIDFromString(text, &id)), "CLSID parsing failed");
    return id;
}

enum class StreamKind { Byte, Empty, ReadFailure, BadCount, SeekFailure };

// Bounded synthetic IStream. Stat intentionally fails, making any accidental
// dependency on metadata or a filename observable.
class ProbeStream final : public IStream {
public:
    explicit ProbeStream(StreamKind kind) : kind_(kind) {}

    ULONG reads = 0;
    ULONG seeks = 0;
    ULONG stats = 0;

    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID iid, void** out) override {
        if (!out) return E_POINTER;
        *out = nullptr;
        if (!IsEqualIID(iid, IID_IUnknown) &&
            !IsEqualIID(iid, IID_ISequentialStream) &&
            !IsEqualIID(iid, IID_IStream)) return E_NOINTERFACE;
        *out = static_cast<IStream*>(this);
        AddRef();
        return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override { return ++references_; }
    ULONG STDMETHODCALLTYPE Release() override {
        const ULONG n = --references_;
        if (!n) delete this;
        return n;
    }

    HRESULT STDMETHODCALLTYPE Read(void* data, ULONG count,
                                   ULONG* received) override {
        ++reads;
        if (received) *received = 0;
        if (kind_ == StreamKind::ReadFailure) return STG_E_READFAULT;
        if (kind_ == StreamKind::BadCount) {
            if (received) *received = count + 1;
            return S_OK;
        }
        if (!count) return S_OK;
        if (!data) return STG_E_INVALIDPOINTER;
        if (kind_ == StreamKind::Empty || consumed_) return S_FALSE;
        static_cast<BYTE*>(data)[0] = 0x42;
        consumed_ = true;
        if (received) *received = 1;
        return count == 1 ? S_OK : S_FALSE;
    }

    HRESULT STDMETHODCALLTYPE Seek(LARGE_INTEGER offset, DWORD origin,
                                   ULARGE_INTEGER* position) override {
        ++seeks;
        if (kind_ == StreamKind::SeekFailure) return STG_E_SEEKERROR;
        if (origin != STREAM_SEEK_SET || offset.QuadPart != 0)
            return STG_E_INVALIDFUNCTION;
        consumed_ = false;
        if (position) position->QuadPart = 0;
        return S_OK;
    }

    HRESULT STDMETHODCALLTYPE Stat(STATSTG*, DWORD) override {
        ++stats;
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE Write(const void*, ULONG, ULONG*) override {
        return STG_E_ACCESSDENIED;
    }
    HRESULT STDMETHODCALLTYPE SetSize(ULARGE_INTEGER) override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE CopyTo(IStream*, ULARGE_INTEGER,
                                      ULARGE_INTEGER*, ULARGE_INTEGER*) override {
        return E_NOTIMPL;
    }
    HRESULT STDMETHODCALLTYPE Commit(DWORD) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE Revert() override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE LockRegion(
        ULARGE_INTEGER, ULARGE_INTEGER, DWORD) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE UnlockRegion(
        ULARGE_INTEGER, ULARGE_INTEGER, DWORD) override { return E_NOTIMPL; }
    HRESULT STDMETHODCALLTYPE Clone(IStream** result) override {
        if (result) *result = nullptr;
        return E_NOTIMPL;
    }

private:
    ~ProbeStream() = default;
    std::atomic<ULONG> references_{1};
    StreamKind kind_;
    bool consumed_ = false;
};

void GetFactory(GetClassObject get, const TestMode& mode,
                ComPtr<IClassFactory>& factory) {
    const CLSID clsid = ParseClsid(mode.text);
    Require(get(clsid, IID_IClassFactory,
                reinterpret_cast<void**>(&factory.p)) == S_OK,
            "DllGetClassObject failed");
}

void MakeProvider(GetClassObject get, const TestMode& mode,
                  ComPtr<IThumbnailProvider>& thumbnail,
                  ComPtr<IInitializeWithStream>& initialize) {
    ComPtr<IClassFactory> factory;
    GetFactory(get, mode, factory);
    Require(factory->CreateInstance(
                nullptr, IID_IThumbnailProvider,
                reinterpret_cast<void**>(&thumbnail.p)) == S_OK,
            "CreateInstance failed");
    Require(thumbnail->QueryInterface(
                IID_IInitializeWithStream,
                reinterpret_cast<void**>(&initialize.p)) == S_OK,
            "IInitializeWithStream missing");
}

void TestLifetime(GetClassObject get, CanUnloadNow canUnload) {
    Require(canUnload() == S_OK, "DLL unexpectedly busy");

    CLSID unknown{};
    void* result = reinterpret_cast<void*>(1);
    Require(get(unknown, IID_IClassFactory, &result) ==
                CLASS_E_CLASSNOTAVAILABLE && result == nullptr,
            "Unknown CLSID handling failed");

    ComPtr<IClassFactory> factory;
    GetFactory(get, kModes[0], factory);
    Require(canUnload() == S_FALSE, "Factory lifetime not counted");

    result = reinterpret_cast<void*>(1);
    Require(factory->CreateInstance(factory.p, IID_IUnknown, &result) ==
                CLASS_E_NOAGGREGATION && result == nullptr,
            "Aggregation was not rejected");

    result = reinterpret_cast<void*>(1);
    Require(factory->CreateInstance(nullptr, IID_IDispatch, &result) ==
                E_NOINTERFACE && result == nullptr,
            "Unsupported interface handling failed");

    Require(factory->LockServer(TRUE) == S_OK, "LockServer failed");
    factory.reset();
    Require(canUnload() == S_FALSE, "Server lock not counted");

    GetFactory(get, kModes[0], factory);
    Require(factory->LockServer(FALSE) == S_OK, "Unlock failed");
    factory.reset();
    Require(canUnload() == S_OK, "DLL lifetime leaked");
}

void TestModeContract(GetClassObject get, const TestMode& mode,
                      bool requireNoHelper) {
    ComPtr<IThumbnailProvider> thumbnail;
    ComPtr<IInitializeWithStream> initialize;
    MakeProvider(get, mode, thumbnail, initialize);

    HBITMAP bitmap = reinterpret_cast<HBITMAP>(1);
    WTS_ALPHATYPE alpha = WTSAT_ARGB;
    Require(thumbnail->GetThumbnail(256, &bitmap, &alpha) ==
                CO_E_NOTINITIALIZED &&
                bitmap == nullptr && alpha == WTSAT_UNKNOWN,
            "Uninitialized output contract failed");

    bitmap = reinterpret_cast<HBITMAP>(1);
    alpha = WTSAT_ARGB;
    Require(thumbnail->GetThumbnail(0, &bitmap, &alpha) == E_INVALIDARG &&
                bitmap == nullptr && alpha == WTSAT_UNKNOWN,
            "Zero-size request was not rejected");
    Require(thumbnail->GetThumbnail(256, nullptr, &alpha) == E_POINTER,
            "Null output was not rejected");
    Require(initialize->Initialize(nullptr, STGM_READ) == E_INVALIDARG,
            "Null stream was not rejected");

    ComPtr<ProbeStream> stream;
    stream.p = new ProbeStream(StreamKind::Byte);
    Require(initialize->Initialize(stream.p, STGM_WRITE) ==
                STG_E_ACCESSDENIED,
            "Write initialization was not rejected");
    Require(initialize->Initialize(stream.p, STGM_READ) == S_OK,
            "Read initialization failed");
    Require(initialize->Initialize(stream.p, STGM_READ) ==
                HRESULT_FROM_WIN32(ERROR_ALREADY_INITIALIZED),
            "Repeated initialization was not rejected");

    ComPtr<IUnknown> identity1;
    ComPtr<IUnknown> identity2;
    Require(thumbnail->QueryInterface(
                IID_IUnknown, reinterpret_cast<void**>(&identity1.p)) == S_OK,
            "Thumbnail IUnknown failed");
    Require(initialize->QueryInterface(
                IID_IUnknown, reinterpret_cast<void**>(&identity2.p)) == S_OK,
            "Initializer IUnknown failed");
    Require(identity1.p == identity2.p, "COM identity mismatch");

    if (requireNoHelper) {
        bitmap = reinterpret_cast<HBITMAP>(1);
        alpha = WTSAT_ARGB;
        const HRESULT hr = thumbnail->GetThumbnail(256, &bitmap, &alpha);
        Require(hr == HRESULT_FROM_WIN32(ERROR_FILE_NOT_FOUND),
                "No-helper request did not fail at the missing executable");
        Require(bitmap == nullptr && alpha == WTSAT_UNKNOWN,
                "Failure outputs were not cleared");
        Require(stream->reads > 0 && stream->seeks > 0,
                "Source stream was not consumed");
    }
    Require(stream->stats == 0, "IStream::Stat must never be used");
}

void TestMalformedStream(GetClassObject get, StreamKind kind,
                         HRESULT expected) {
    ComPtr<IThumbnailProvider> thumbnail;
    ComPtr<IInitializeWithStream> initialize;
    MakeProvider(get, kModes[0], thumbnail, initialize);

    ComPtr<ProbeStream> stream;
    stream.p = new ProbeStream(kind);
    Require(initialize->Initialize(stream.p, STGM_READ) == S_OK,
            "Probe initialization failed");

    HBITMAP bitmap = reinterpret_cast<HBITMAP>(1);
    WTS_ALPHATYPE alpha = WTSAT_ARGB;
    const HRESULT hr = thumbnail->GetThumbnail(256, &bitmap, &alpha);
    Require(hr == expected, "Malformed stream returned unexpected HRESULT");
    Require(bitmap == nullptr && alpha == WTSAT_UNKNOWN,
            "Malformed stream leaked output");
    Require(stream->stats == 0, "Malformed stream caused a Stat call");
}

void TestRealHelper(GetClassObject get, const wchar_t* modeName,
                    const wchar_t* inputPath, bool expectAlpha) {
    const TestMode* selected = nullptr;
    for (const auto& mode : kModes) {
        if (std::wcscmp(mode.name, modeName) == 0) selected = &mode;
    }
    Require(selected != nullptr, "Unknown mode");

    ComPtr<IStream> stream;
    Require(SUCCEEDED(SHCreateStreamOnFileEx(
                inputPath, STGM_READ | STGM_SHARE_DENY_WRITE,
                FILE_ATTRIBUTE_NORMAL, FALSE, nullptr, &stream.p)),
            "Cannot open real input stream");

    ComPtr<IThumbnailProvider> thumbnail;
    ComPtr<IInitializeWithStream> initialize;
    MakeProvider(get, *selected, thumbnail, initialize);
    Require(initialize->Initialize(stream.p, STGM_READ) == S_OK,
            "Real stream initialization failed");

    HBITMAP bitmap = nullptr;
    WTS_ALPHATYPE alpha = WTSAT_UNKNOWN;
    const HRESULT hr = thumbnail->GetThumbnail(256, &bitmap, &alpha);
    if (FAILED(hr)) {
        std::cerr << "GetThumbnail HRESULT=0x" << std::hex
                  << static_cast<unsigned long>(hr) << std::dec << '\n';
        throw std::runtime_error("Real helper request failed");
    }

    DIBSECTION dib{};
    const int count = GetObjectW(bitmap, sizeof(dib), &dib);
    const bool valid =
        count == sizeof(dib) && alpha == WTSAT_ARGB &&
        dib.dsBm.bmWidth > 0 && dib.dsBm.bmWidth <= 256 &&
        dib.dsBm.bmHeight > 0 && dib.dsBm.bmHeight <= 256 &&
        dib.dsBm.bmBitsPixel == 32 && dib.dsBm.bmBits != nullptr;

    bool exactAlpha = true;
    if (expectAlpha) {
        exactAlpha = valid && dib.dsBm.bmWidth == 1 && dib.dsBm.bmHeight == 1;
        if (exactAlpha) {
            const auto* pixel = static_cast<const BYTE*>(dib.dsBm.bmBits);
            exactAlpha = pixel[0] == 0 && pixel[1] == 0 &&
                         pixel[2] == 255 && pixel[3] == 128;
        }
    }

    const LONG width = dib.dsBm.bmWidth;
    const LONG height = dib.dsBm.bmHeight;
    if (valid) {
        try { TestExpectedBitmap(bitmap); }
        catch (...) { DeleteObject(bitmap); throw; }
    }
    DeleteObject(bitmap);

    Require(valid, "Returned bitmap dimensions/type were invalid");
    Require(exactAlpha, "Expected straight BGRA=(0,0,255,128) for alpha fixture");
    if (expectAlpha) std::cout << "alpha_fixture_ok BGRA=0,0,255,128\n";
    std::cout << "real_helper_ok host_pid=" << GetCurrentProcessId()
              << " width=" << width << " height=" << height
              << " alpha=ARGB\n";
}

struct KernelHandle {
    HANDLE value = nullptr;
    ~KernelHandle() {
        if (value && value != INVALID_HANDLE_VALUE) CloseHandle(value);
    }
};

struct MappedView {
    void* value = nullptr;
    ~MappedView() { if (value) UnmapViewOfFile(value); }
};

struct OwnedBitmap {
    HBITMAP value = nullptr;
    ~OwnedBitmap() { if (value) DeleteObject(value); }
};

HRESULT RequestFixture(GetClassObject get, const std::string& source, UINT size,
                       HBITMAP* bitmap, WTS_ALPHATYPE* alpha) {
    ComPtr<IStream> stream;
    stream.p = SHCreateMemStream(
        reinterpret_cast<const BYTE*>(source.data()),
        static_cast<UINT>(source.size()));
    Require(stream.p != nullptr, "Cannot create fixture input stream");

    ComPtr<IThumbnailProvider> thumbnail;
    ComPtr<IInitializeWithStream> initialize;
    MakeProvider(get, kModes[0], thumbnail, initialize);
    Require(initialize->Initialize(stream.p, STGM_READ) == S_OK,
            "Cannot initialize fixture provider");
    return thumbnail->GetThumbnail(size, bitmap, alpha);
}

void TestFixtureProtocol(GetClassObject get) {
    struct BadCase { const char* name; HRESULT expected; };
    const BadCase cases[] = {
        {"bad-magic", HRESULT_FROM_WIN32(ERROR_INVALID_DATA)},
        {"zero-width", HRESULT_FROM_WIN32(ERROR_INVALID_DATA)},
        {"oversized-dimension", HRESULT_FROM_WIN32(ERROR_INVALID_DATA)},
        {"truncated-header", HRESULT_FROM_WIN32(ERROR_INVALID_DATA)},
        {"truncated-pixels", HRESULT_FROM_WIN32(ERROR_INVALID_DATA)},
        {"extra-data", HRESULT_FROM_WIN32(ERROR_INVALID_DATA)},
        {"oversized-output", HRESULT_FROM_WIN32(ERROR_INVALID_DATA)},
        {"exit-nonzero", HRESULT_FROM_WIN32(ERROR_PROCESS_ABORTED)}
    };
    for (const auto& test : cases) {
        OwnedBitmap bitmap;
        WTS_ALPHATYPE alpha = WTSAT_ARGB;
        const HRESULT hr = RequestFixture(
            get, std::string(test.name) + "\n", 256, &bitmap.value, &alpha);
        if (hr != test.expected) {
            std::cerr << "fixture=" << test.name << " HRESULT=0x" << std::hex
                      << static_cast<unsigned long>(hr) << std::dec << '\n';
        }
        Require(hr == test.expected, "Malformed output returned wrong HRESULT");
        Require(!bitmap.value && alpha == WTSAT_UNKNOWN,
                "Malformed output was not rejected cleanly");
        std::cout << "fixture_rejected " << test.name << '\n';
    }

    for (const char* scenario : {"valid", "valid-max", "read-only-pin"}) {
        const std::string protocol = std::string(scenario) + "\n";
        const bool maximum = protocol == "valid-max\n";
        OwnedBitmap bitmap;
        WTS_ALPHATYPE alpha = WTSAT_UNKNOWN;
        const HRESULT hr = RequestFixture(
            get, protocol,
            maximum ? 0xffffffffu : 1u, &bitmap.value, &alpha);
        Require(hr == S_OK && bitmap.value && alpha == WTSAT_ARGB,
                "Valid fixture was rejected");
        DIBSECTION dib{};
        Require(GetObjectW(bitmap.value, sizeof(dib), &dib) == sizeof(dib),
                "Cannot inspect fixture bitmap");
        const LONG expected = maximum ? 1024 : 1;
        Require(dib.dsBm.bmWidth == expected &&
                    dib.dsBm.bmHeight == expected &&
                    dib.dsBm.bmBitsPixel == 32 && dib.dsBm.bmBits,
                "Fixture size clamp or bitmap type failed");
        const BYTE* first = static_cast<const BYTE*>(dib.dsBm.bmBits);
        const BYTE* last = first +
            (expected - 1) * dib.dsBm.bmWidthBytes + (expected - 1) * 4;
        for (const BYTE* pixel : {first, last}) {
            Require(pixel[0] == 0 && pixel[1] == 0 &&
                        pixel[2] == 255 && pixel[3] == 128,
                    "Fixture straight-alpha channel conversion failed");
        }
        std::cout << "fixture_valid scenario=" << scenario << " size=" << expected
                  << " BGRA=0,0,255,128\n";
    }
}

void TestFixtureTimeout(GetClassObject get) {
    GUID id{};
    Require(SUCCEEDED(CoCreateGuid(&id)), "Cannot create fixture identity");
    wchar_t text[39]{};
    Require(StringFromGUID2(id, text, ARRAYSIZE(text)) == ARRAYSIZE(text),
            "Cannot format fixture identity");

    const std::wstring mapName =
        std::wstring(L"Local\\LumaThumbnailFixture-") + text + L"-map";
    const std::wstring eventName =
        std::wstring(L"Local\\LumaThumbnailFixture-") + text + L"-ready";

    KernelHandle mapping;
    mapping.value = CreateFileMappingW(
        INVALID_HANDLE_VALUE, nullptr, PAGE_READWRITE, 0, sizeof(DWORD),
        mapName.c_str());
    const DWORD mappingError = GetLastError();
    Require(mapping.value && mappingError != ERROR_ALREADY_EXISTS,
            "Cannot create unique fixture mapping");
    MappedView view;
    view.value = MapViewOfFile(
        mapping.value, FILE_MAP_READ | FILE_MAP_WRITE, 0, 0, sizeof(DWORD));
    Require(view.value != nullptr, "Cannot map fixture status");
    *static_cast<DWORD*>(view.value) = 0;

    KernelHandle ready;
    ready.value = CreateEventW(nullptr, TRUE, FALSE, eventName.c_str());
    const DWORD eventError = GetLastError();
    Require(ready.value && eventError != ERROR_ALREADY_EXISTS,
            "Cannot create unique fixture event");

    // Names contain ASCII characters only; they are a bounded test protocol.
    std::string source = "sleep\n";
    source.append(mapName.begin(), mapName.end());
    source += "\n";
    source.append(eventName.begin(), eventName.end());
    source += "\n";

    KernelHandle child;
    DWORD childPid = 0;
    DWORD observerError = ERROR_SUCCESS;
    std::thread observer([&] {
        const DWORD wait = WaitForSingleObject(ready.value, 12000);
        if (wait != WAIT_OBJECT_0) {
            observerError = wait == WAIT_TIMEOUT ? ERROR_TIMEOUT : GetLastError();
            return;
        }
        childPid = *static_cast<volatile DWORD*>(view.value);
        if (!childPid) {
            observerError = ERROR_INVALID_DATA;
            return;
        }
        // Pin the process while it is alive, avoiding PID-reuse ambiguity.
        child.value = OpenProcess(
            SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, FALSE, childPid);
        if (!child.value) observerError = GetLastError();
    });

    OwnedBitmap bitmap;
    WTS_ALPHATYPE alpha = WTSAT_ARGB;
    HRESULT hr = E_FAIL;
    const ULONGLONG started = GetTickCount64();
    try {
        hr = RequestFixture(get, source, 256, &bitmap.value, &alpha);
    } catch (...) {
        observer.join();
        throw;
    }
    const ULONGLONG elapsed = GetTickCount64() - started;
    observer.join();

    Require(observerError == ERROR_SUCCESS && child.value,
            "Could not observe the live timeout helper");
    Require(hr == HRESULT_FROM_WIN32(ERROR_TIMEOUT),
            "Sleeping helper did not return timeout HRESULT");
    Require(!bitmap.value && alpha == WTSAT_UNKNOWN,
            "Timeout leaked a bitmap or alpha result");
    Require(elapsed >= 9000 && elapsed < 15000,
            "Timeout was outside the expected wall-time window");
    Require(WaitForSingleObject(child.value, 2000) == WAIT_OBJECT_0,
            "Helper survived timeout cleanup");

    DWORD exitCode = STILL_ACTIVE;
    Require(GetExitCodeProcess(child.value, &exitCode) &&
                exitCode != STILL_ACTIVE,
            "Timeout helper is still active");
    std::cout << "fixture_timeout_killed helper_pid=" << childPid
              << " elapsed_ms=" << elapsed << " exit_code=" << exitCode << '\n';
}

void PrintRegistrationDiagnostics(const wchar_t* inputPath) {
    const wchar_t* extension = PathFindExtensionW(inputPath);
    constexpr wchar_t handler[] = L"{E357FCCD-A995-4576-B01F-234630154E96}";
    wchar_t provider[64]{};
    DWORD count = ARRAYSIZE(provider);
    const HRESULT hr = AssocQueryStringW(
        ASSOCF_NONE, ASSOCSTR_SHELLEXTENSION, extension, handler, provider, &count);
    std::wcerr << L"association extension=" << extension
               << L" HRESULT=0x" << std::hex << static_cast<unsigned long>(hr)
               << std::dec << L" provider=" << (SUCCEEDED(hr) ? provider : L"")
               << L"\n";
    CLSID id{};
    if (FAILED(hr) || FAILED(CLSIDFromString(provider, &id))) return;

    const std::wstring clsid = L"CLSID\\" + std::wstring(provider) +
        L"\\InprocServer32";
    for (const bool merged : {false, true}) {
        const std::wstring key = merged ? clsid : L"Software\\Classes\\" + clsid;
        wchar_t value[32768]{};
        DWORD bytes = sizeof(value);
        const LSTATUS status = RegGetValueW(
            merged ? HKEY_CLASSES_ROOT : HKEY_CURRENT_USER, key.c_str(),
            nullptr, RRF_RT_REG_SZ | RRF_SUBKEY_WOW6464KEY, nullptr,
            value, &bytes);
        std::wcerr << (merged ? L"HKCR64 " : L"HKCU64 ") << key
                   << L" status=" << status << L" dll="
                   << (status == ERROR_SUCCESS ? value : L"") << L"\n";
    }
}

void TestSurrogate(const wchar_t* inputPath) {
    PrintRegistrationDiagnostics(inputPath);
    ComPtr<IShellItem> item;
    Require(SUCCEEDED(SHCreateItemFromParsingName(
                inputPath, nullptr, IID_IShellItem,
                reinterpret_cast<void**>(&item.p))),
            "Cannot create shell item");

    ComPtr<IThumbnailCache> cache;
    const HRESULT cacheHr = CoCreateInstance(
        CLSID_LocalThumbnailCache, nullptr, CLSCTX_INPROC_SERVER,
        IID_IThumbnailCache, reinterpret_cast<void**>(&cache.p));
    if (FAILED(cacheHr)) {
        std::cerr << "thumbnail cache activation HRESULT=0x" << std::hex
                  << static_cast<unsigned long>(cacheHr) << std::dec << '\n';
        throw std::runtime_error("Cannot create local thumbnail cache");
    }

    ComPtr<ISharedBitmap> shared;
    WTS_CACHEFLAGS cacheFlags = WTS_DEFAULT;
    WTS_THUMBNAILID thumbnailId{};
    const auto flags = static_cast<WTS_FLAGS>(
        WTS_FORCEEXTRACTION | WTS_REQUIRESURROGATE);
    const HRESULT hr = cache->GetThumbnail(
        item.p, 256, flags, &shared.p, &cacheFlags, &thumbnailId);
    if (FAILED(hr)) {
        std::cerr << "surrogate GetThumbnail HRESULT=0x" << std::hex
                  << static_cast<unsigned long>(hr) << std::dec << '\n';
        throw std::runtime_error("Required-surrogate extraction failed");
    }
    Require(shared.p != nullptr, "Cache returned no shared bitmap");

    SIZE size{};
    WTS_ALPHATYPE alpha = WTSAT_UNKNOWN;
    HBITMAP bitmap = nullptr;
    Require(SUCCEEDED(shared->GetSize(&size)), "Shared bitmap size failed");
    Require(SUCCEEDED(shared->GetFormat(&alpha)), "Shared bitmap format failed");
    Require(SUCCEEDED(shared->GetSharedBitmap(&bitmap)) && bitmap,
            "Shared bitmap handle failed");

    BITMAP description{};
    Require(GetObjectW(bitmap, sizeof(description), &description) ==
                sizeof(description),
            "Cannot inspect shared bitmap");
    Require(size.cx > 0 && size.cy > 0 && size.cx <= 256 && size.cy <= 256 &&
                description.bmWidth == size.cx &&
                description.bmHeight == size.cy &&
                description.bmBitsPixel == 32,
            "Surrogate bitmap dimensions/type were invalid");
    Require(alpha == WTSAT_ARGB, "Surrogate lost the alpha format");
    TestExpectedBitmap(bitmap);

    // The cache owns this bitmap. Do not DeleteObject it or mutate its pixels.
    // The required-surrogate flag is enforced by Windows. This host PID is
    // diagnostic evidence only, not a claimed surrogate/helper PID.
    std::cout << "surrogate_smoke_ok host_pid=" << GetCurrentProcessId()
              << " width=" << size.cx << " height=" << size.cy
              << " alpha=ARGB extraction=required-surrogate"
              << " cache_flags=" << static_cast<unsigned>(cacheFlags) << '\n';
}

std::wstring LoadedModulePath(HMODULE module) {
    std::wstring path(32768, L'\0');
    const DWORD length = GetModuleFileNameW(
        module, path.data(), static_cast<DWORD>(path.size()));
    Require(length > 0 && length < path.size(), "Module path lookup failed");
    path.resize(length);
    return path;
}

} // namespace

int wmain(int argc, wchar_t** argv) {
    const bool surrogateChild = argc == 3 &&
        std::wcscmp(argv[1], L"--surrogate-child") == 0;
    const bool surrogate = surrogateChild || (argc == 3 &&
        std::wcscmp(argv[1], L"--surrogate") == 0);
    const bool fixtureProtocol = argc == 3 &&
        std::wcscmp(argv[2], L"--fixture-protocol") == 0;
    const bool fixtureTimeout = argc == 3 &&
        std::wcscmp(argv[2], L"--fixture-timeout") == 0;
    const bool expectAlpha = argc == 5 &&
        std::wcscmp(argv[4], L"--expect-alpha") == 0;
    if (!surrogate && !fixtureProtocol && !fixtureTimeout &&
        argc != 2 && argc != 4 && !expectAlpha) {
        std::wcerr
            << L"Usage: thumbnail_tests.exe <DLL> [mode input_path [--expect-alpha]]\n"
            << L"       thumbnail_tests.exe --surrogate <input_path>\n"
            << L"       thumbnail_tests.exe <test-DLL> --fixture-protocol\n"
            << L"       thumbnail_tests.exe <test-DLL> --fixture-timeout\n"
            << L"Without mode/input, the DLL must have no sibling Luma.exe.\n"
            << L"--expect-alpha requires a 1x1 RGBA=(255,0,0,128) image.\n";
        return 2;
    }

    if (surrogate) {
        const int prepared = PrepareSurrogateClient(argv[2], surrogateChild);
        if (prepared >= 0) return prepared;
    }
    const HRESULT comResult =
        CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
    if (FAILED(comResult)) return 2;

    HMODULE module = nullptr;
    int result = 1;
    try {
        TestBitmapReadback();
        if (surrogate) {
            TestSurrogate(argv[2]);
            result = 0;
        } else {
            module = LoadLibraryExW(
                argv[1], nullptr,
                LOAD_LIBRARY_SEARCH_DLL_LOAD_DIR |
                    LOAD_LIBRARY_SEARCH_SYSTEM32);
            Require(module != nullptr, "DLL could not be loaded");

            const auto get = reinterpret_cast<GetClassObject>(
                GetProcAddress(module, "DllGetClassObject"));
            const auto canUnload = reinterpret_cast<CanUnloadNow>(
                GetProcAddress(module, "DllCanUnloadNow"));
            Require(get && canUnload, "Required COM exports are missing");

            if (fixtureProtocol) {
                TestFixtureProtocol(get);
            } else if (fixtureTimeout) {
                TestFixtureTimeout(get);
            } else {
                const bool noHelper = argc == 2;
                if (noHelper) {
                    const std::wstring path = LoadedModulePath(module);
                    const std::size_t slash = path.find_last_of(L'\\');
                    Require(slash != std::wstring::npos, "DLL directory missing");
                    const std::wstring helper =
                        path.substr(0, slash + 1) + L"Luma.exe";
                    SetLastError(ERROR_SUCCESS);
                    const DWORD attributes = GetFileAttributesW(helper.c_str());
                    const DWORD error = GetLastError();
                    Require(attributes == INVALID_FILE_ATTRIBUTES &&
                                error == ERROR_FILE_NOT_FOUND,
                            "No-helper fixture must not contain Luma.exe");
                }

                TestLifetime(get, canUnload);
                for (const auto& mode : kModes)
                    TestModeContract(get, mode, noHelper);

                TestMalformedStream(get, StreamKind::Empty,
                                    HRESULT_FROM_WIN32(ERROR_INVALID_DATA));
                TestMalformedStream(get, StreamKind::ReadFailure, STG_E_READFAULT);
                TestMalformedStream(get, StreamKind::BadCount, STG_E_READFAULT);
                TestMalformedStream(get, StreamKind::SeekFailure, STG_E_SEEKERROR);

                if (!noHelper) TestRealHelper(get, argv[2], argv[3], expectAlpha);
                std::cout << "Direct COM tests passed; shell surrogate not exercised.\n";
            }
            Require(canUnload() == S_OK, "COM objects leaked");
            result = 0;
        }
    } catch (const std::exception& error) {
        std::cerr << "FAIL: " << error.what() << '\n';
    } catch (...) {
        std::cerr << "FAIL: unexpected exception\n";
    }

    if (module) FreeLibrary(module);
    CoUninitialize();
    return result;
}
