#pragma once

#include "image/IImageDecoder.h"

#include <array>

namespace lumina::image {

class WicDecoder final : public IImageDecoder {
public:
    [[nodiscard]] bool canDecodeExtension(std::wstring_view extension) const override;
    [[nodiscard]] DecodeResult decode(const std::filesystem::path& path, const DecodeOptions& options) override;

private:
    static constexpr std::array<std::wstring_view, 11> supportedExtensions_ = {
        L".jpg", L".jpeg", L".png", L".bmp", L".tif", L".tiff",
        L".gif", L".webp", L".heic", L".heif", L".avif"
    };
};

} // namespace lumina::image
