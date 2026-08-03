#include "image/PlaceholderDecoder.h"

#include <algorithm>
#include <cmath>

namespace lumina::image {

bool ImageFrame::empty() const noexcept {
    return width <= 0 || height <= 0 || stride <= 0 || pixels.empty();
}

bool PlaceholderDecoder::canDecodeExtension(std::wstring_view extension) const {
    return std::ranges::find(supportedExtensions_, extension) != supportedExtensions_.end();
}

DecodeResult PlaceholderDecoder::decode(const std::filesystem::path&, const DecodeOptions& options) {
    const int width = options.max_width > 0 ? std::min(options.max_width, 1280) : 1280;
    const int height = options.max_height > 0 ? std::min(options.max_height, 800) : 800;

    ImageFrame frame;
    frame.width = width;
    frame.height = height;
    frame.stride = width * 4;
    frame.pixels.resize(static_cast<std::size_t>(frame.stride) * static_cast<std::size_t>(height));

    for (int y = 0; y < height; ++y) {
        for (int x = 0; x < width; ++x) {
            const auto offset = static_cast<std::size_t>(y * frame.stride + x * 4);
            const float fx = static_cast<float>(x) / static_cast<float>(std::max(width - 1, 1));
            const float fy = static_cast<float>(y) / static_cast<float>(std::max(height - 1, 1));
            const auto checker = ((x / 48) + (y / 48)) % 2;
            frame.pixels[offset + 0] = static_cast<std::uint8_t>(80 + 80 * fy);
            frame.pixels[offset + 1] = static_cast<std::uint8_t>(90 + 90 * fx);
            frame.pixels[offset + 2] = static_cast<std::uint8_t>(checker ? 160 : 110);
            frame.pixels[offset + 3] = 255;
        }
    }

    return DecodeResult{
        .frame = std::move(frame),
        .warning = L"Using placeholder decoder. Add WIC/LibRaw/OpenEXR backends for real image pixels."
    };
}

} // namespace lumina::image
