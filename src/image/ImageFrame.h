#pragma once

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace lumina::image {

enum class PixelFormat {
    Bgra8,
};

struct ImageFrame {
    int width = 0;
    int height = 0;
    int stride = 0;
    PixelFormat format = PixelFormat::Bgra8;
    std::vector<std::uint8_t> pixels;

    [[nodiscard]] bool empty() const noexcept;
};

struct ImageInfo {
    std::filesystem::path path;
    std::uintmax_t file_size = 0;
    int width = 0;
    int height = 0;
};

struct DecodeOptions {
    int max_width = 0;
    int max_height = 0;
};

struct DecodeResult {
    ImageFrame frame;
    std::wstring warning;
};

} // namespace lumina::image
