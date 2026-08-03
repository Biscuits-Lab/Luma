#pragma once

#include "image/ImageFrame.h"

#include <array>
#include <cstdint>
#include <string>

namespace lumina::image {

struct ImageAnalysis {
    std::array<std::uint32_t, 16> red{};
    std::array<std::uint32_t, 16> green{};
    std::array<std::uint32_t, 16> blue{};
    std::array<std::uint32_t, 16> luminance{};
    std::uint32_t max_bin = 0;
};

[[nodiscard]] ImageAnalysis analyzeImage(const ImageFrame& frame);
[[nodiscard]] std::wstring formatHistogramBars(const std::array<std::uint32_t, 16>& bins, std::uint32_t maxBin);

} // namespace lumina::image
