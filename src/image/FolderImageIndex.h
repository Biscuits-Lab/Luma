#pragma once

#include "image/DecoderRegistry.h"

#include <filesystem>
#include <optional>
#include <vector>

namespace lumina::image {

class FolderImageIndex {
public:
    explicit FolderImageIndex(const DecoderRegistry& decoders);

    void loadAround(const std::filesystem::path& imagePath);

    [[nodiscard]] const std::vector<std::filesystem::path>& files() const noexcept;
    [[nodiscard]] std::optional<std::filesystem::path> current() const;
    [[nodiscard]] std::optional<std::filesystem::path> previous() const;
    [[nodiscard]] std::optional<std::filesystem::path> next() const;
    [[nodiscard]] std::size_t currentIndex() const noexcept;
    [[nodiscard]] bool empty() const noexcept;

private:
    const DecoderRegistry& decoders_;
    std::vector<std::filesystem::path> files_;
    std::size_t currentIndex_ = 0;
};

} // namespace lumina::image
