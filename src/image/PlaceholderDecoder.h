#pragma once

#include "image/IImageDecoder.h"

#include <array>

namespace lumina::image {

class PlaceholderDecoder final : public IImageDecoder {
public:
    [[nodiscard]] bool canDecodeExtension(std::wstring_view extension) const override;
    [[nodiscard]] DecodeResult decode(const std::filesystem::path& path, const DecodeOptions& options) override;

private:
    static constexpr std::array<std::wstring_view, 20> supportedExtensions_ = {
        L".jpg", L".jpeg", L".png", L".bmp", L".tif", L".tiff", L".webp", L".gif",
        L".heic", L".heif", L".avif", L".nef", L".cr2", L".cr3", L".arw", L".dng",
        L".orf", L".rw2", L".exr", L".hdr"
    };
};

} // namespace lumina::image
