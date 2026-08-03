#include "image/WicDecoder.h"

#include <Windows.h>
#include <wincodec.h>
#include <wrl/client.h>

#include <algorithm>
#include <comdef.h>
#include <stdexcept>

namespace lumina::image {

namespace {
using Microsoft::WRL::ComPtr;

void throwIfFailed(HRESULT hr, const char* operation) {
    if (FAILED(hr)) {
        _com_error error(hr);
        std::string message = operation;
        message += " failed: ";
        message += static_cast<const char*>(_bstr_t(error.ErrorMessage()));
        throw std::runtime_error(message);
    }
}

class ComApartment {
public:
    ComApartment() {
        hr_ = CoInitializeEx(nullptr, COINIT_APARTMENTTHREADED);
        if (hr_ == RPC_E_CHANGED_MODE) {
            hr_ = S_FALSE;
        } else if (FAILED(hr_)) {
            throwIfFailed(hr_, "CoInitializeEx");
        }
    }

    ~ComApartment() {
        if (hr_ == S_OK) {
            CoUninitialize();
        }
    }

private:
    HRESULT hr_ = S_FALSE;
};

} // namespace

bool WicDecoder::canDecodeExtension(std::wstring_view extension) const {
    return std::ranges::find(supportedExtensions_, extension) != supportedExtensions_.end();
}

DecodeResult WicDecoder::decode(const std::filesystem::path& path, const DecodeOptions& options) {
    ComApartment apartment;

    ComPtr<IWICImagingFactory> factory;
    throwIfFailed(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER, IID_PPV_ARGS(&factory)),
                  "CoCreateInstance(CLSID_WICImagingFactory)");

    ComPtr<IWICBitmapDecoder> decoder;
    throwIfFailed(factory->CreateDecoderFromFilename(path.c_str(), nullptr, GENERIC_READ, WICDecodeMetadataCacheOnDemand, &decoder),
                  "CreateDecoderFromFilename");

    ComPtr<IWICBitmapFrameDecode> sourceFrame;
    throwIfFailed(decoder->GetFrame(0, &sourceFrame), "GetFrame");

    UINT sourceWidth = 0;
    UINT sourceHeight = 0;
    throwIfFailed(sourceFrame->GetSize(&sourceWidth, &sourceHeight), "GetSize");

    ComPtr<IWICBitmapSource> bitmapSource = sourceFrame;
    UINT decodeWidth = sourceWidth;
    UINT decodeHeight = sourceHeight;

    if (options.max_width > 0 && options.max_height > 0 &&
        (sourceWidth > static_cast<UINT>(options.max_width) || sourceHeight > static_cast<UINT>(options.max_height))) {
        const double scaleX = static_cast<double>(options.max_width) / static_cast<double>(sourceWidth);
        const double scaleY = static_cast<double>(options.max_height) / static_cast<double>(sourceHeight);
        const double scale = std::min(scaleX, scaleY);
        decodeWidth = std::max<UINT>(1, static_cast<UINT>(sourceWidth * scale));
        decodeHeight = std::max<UINT>(1, static_cast<UINT>(sourceHeight * scale));

        ComPtr<IWICBitmapScaler> scaler;
        throwIfFailed(factory->CreateBitmapScaler(&scaler), "CreateBitmapScaler");
        throwIfFailed(scaler->Initialize(sourceFrame.Get(), decodeWidth, decodeHeight, WICBitmapInterpolationModeFant),
                      "BitmapScaler::Initialize");
        bitmapSource = scaler;
    }

    ComPtr<IWICFormatConverter> converter;
    throwIfFailed(factory->CreateFormatConverter(&converter), "CreateFormatConverter");
    throwIfFailed(converter->Initialize(bitmapSource.Get(), GUID_WICPixelFormat32bppBGRA, WICBitmapDitherTypeNone, nullptr, 0.0,
                                        WICBitmapPaletteTypeCustom),
                  "FormatConverter::Initialize");

    ImageFrame frame;
    frame.width = static_cast<int>(decodeWidth);
    frame.height = static_cast<int>(decodeHeight);
    frame.stride = frame.width * 4;
    frame.pixels.resize(static_cast<std::size_t>(frame.stride) * static_cast<std::size_t>(frame.height));

    throwIfFailed(converter->CopyPixels(nullptr, static_cast<UINT>(frame.stride), static_cast<UINT>(frame.pixels.size()), frame.pixels.data()),
                  "CopyPixels");

    return DecodeResult{.frame = std::move(frame), .warning = {}};
}

} // namespace lumina::image
