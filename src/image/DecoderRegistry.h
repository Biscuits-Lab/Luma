#pragma once

#include "image/IImageDecoder.h"

#include <memory>
#include <vector>

namespace lumina::image {

class DecoderRegistry {
public:
    void add(std::unique_ptr<IImageDecoder> decoder);

    [[nodiscard]] IImageDecoder* find(std::wstring_view extension) const;
    [[nodiscard]] DecodeResult decode(const std::filesystem::path& path, const DecodeOptions& options) const;
    [[nodiscard]] bool isSupportedExtension(std::wstring_view extension) const;

    static DecoderRegistry createDefault();

private:
    std::vector<std::unique_ptr<IImageDecoder>> decoders_;
};

} // namespace lumina::image
