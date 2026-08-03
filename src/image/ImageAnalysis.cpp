#include "image/ImageAnalysis.h"

#include <algorithm>

namespace lumina::image {

ImageAnalysis analyzeImage(const ImageFrame& frame) {
    ImageAnalysis analysis;
    if (frame.empty() || frame.format != PixelFormat::Bgra8) {
        return analysis;
    }

    constexpr int sampleStep = 4;
    for (int y = 0; y < frame.height; y += sampleStep) {
        const auto* row = frame.pixels.data() + static_cast<std::size_t>(y * frame.stride);
        for (int x = 0; x < frame.width; x += sampleStep) {
            const auto* pixel = row + static_cast<std::size_t>(x * 4);
            const auto blue = pixel[0];
            const auto green = pixel[1];
            const auto red = pixel[2];
            const auto luminance = static_cast<std::uint8_t>((static_cast<int>(red) * 54 + static_cast<int>(green) * 183 + static_cast<int>(blue) * 19) >> 8);

            ++analysis.red[red >> 4];
            ++analysis.green[green >> 4];
            ++analysis.blue[blue >> 4];
            ++analysis.luminance[luminance >> 4];
        }
    }

    for (const auto& bins : {analysis.red, analysis.green, analysis.blue, analysis.luminance}) {
        for (const auto value : bins) {
            analysis.max_bin = std::max(analysis.max_bin, value);
        }
    }
    return analysis;
}

std::wstring formatHistogramBars(const std::array<std::uint32_t, 16>& bins, std::uint32_t maxBin) {
    if (maxBin == 0) {
        return L"▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁▁";
    }

    static constexpr wchar_t levels[] = L"▁▂▃▄▅▆▇█";
    std::wstring result;
    result.reserve(bins.size());
    for (const auto value : bins) {
        const auto index = std::min<std::uint32_t>(7, (value * 7) / maxBin);
        result.push_back(levels[index]);
    }
    return result;
}

} // namespace lumina::image
