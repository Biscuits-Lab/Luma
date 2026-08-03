#include "image/DecoderRegistry.h"

#include "image/PlaceholderDecoder.h"
#include "image/WicDecoder.h"

#include <algorithm>
#include <cwctype>
#include <stdexcept>

namespace lumina::image {

namespace {
std::wstring normalizeExtension(std::wstring_view extension) {
    std::wstring normalized(extension);
    std::ranges::transform(normalized, normalized.begin(), [](wchar_t ch) {
        return static_cast<wchar_t>(std::towlower(ch));
    });
    return normalized;
}
} // namespace

void DecoderRegistry::add(std::unique_ptr<IImageDecoder> decoder) {
    if (decoder) {
        decoders_.push_back(std::move(decoder));
    }
}

IImageDecoder* DecoderRegistry::find(std::wstring_view extension) const {
    const auto normalized = normalizeExtension(extension);
    for (const auto& decoder : decoders_) {
        if (decoder->canDecodeExtension(normalized)) {
            return decoder.get();
        }
    }
    return nullptr;
}

DecodeResult DecoderRegistry::decode(const std::filesystem::path& path, const DecodeOptions& options) const {
    auto* decoder = find(path.extension().wstring());
    if (!decoder) {
        throw std::runtime_error("No decoder registered for this image format.");
    }
    return decoder->decode(path, options);
}

bool DecoderRegistry::isSupportedExtension(std::wstring_view extension) const {
    return find(extension) != nullptr;
}

DecoderRegistry DecoderRegistry::createDefault() {
    DecoderRegistry registry;
    registry.add(std::make_unique<WicDecoder>());
    registry.add(std::make_unique<PlaceholderDecoder>());
    return registry;
}

} // namespace lumina::image


