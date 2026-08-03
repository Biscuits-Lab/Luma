#pragma once

#include "image/IImageDecoder.h"

#include <array>

namespace lumina::image {

class LibRawDecoder final : public IImageDecoder {
public:
    [[nodiscard]] bool canDecodeExtension(std::wstring_view extension) const override;
    [[nodiscard]] DecodeResult decode(const std::filesystem::path& path, const DecodeOptions& options) override;

private:
    static constexpr std::array<std::wstring_view, 11> supportedExtensions_ = {
        L".nef", L".nrw", L".dng", L".cr2", L".cr3", L".arw", L".srf", L".sr2", L".orf", L".rw2", L".raf"
    };
};

} // namespace lumina::image
