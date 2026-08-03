#pragma once

#include "image/ImageFrame.h"

#include <filesystem>
#include <string_view>

namespace lumina::image {

class IImageDecoder {
public:
    virtual ~IImageDecoder() = default;

    [[nodiscard]] virtual bool canDecodeExtension(std::wstring_view extension) const = 0;
    [[nodiscard]] virtual DecodeResult decode(const std::filesystem::path& path, const DecodeOptions& options) = 0;
};

} // namespace lumina::image
