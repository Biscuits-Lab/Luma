#include "image/FolderImageIndex.h"

#include <algorithm>
#include <stdexcept>

namespace lumina::image {

namespace {
std::filesystem::path canonicalish(const std::filesystem::path& path) {
    std::error_code error;
    auto absolute = std::filesystem::absolute(path, error);
    if (error) {
        return path;
    }
    auto normalized = std::filesystem::weakly_canonical(absolute, error);
    return error ? absolute.lexically_normal() : normalized;
}
} // namespace

FolderImageIndex::FolderImageIndex(const DecoderRegistry& decoders)
    : decoders_(decoders) {}

void FolderImageIndex::loadAround(const std::filesystem::path& imagePath) {
    const auto target = canonicalish(imagePath);
    const auto folder = target.parent_path();

    files_.clear();
    currentIndex_ = 0;

    if (folder.empty() || !std::filesystem::exists(folder)) {
        files_.push_back(target);
        return;
    }

    for (const auto& entry : std::filesystem::directory_iterator(folder)) {
        if (!entry.is_regular_file()) {
            continue;
        }
        const auto path = canonicalish(entry.path());
        if (decoders_.isSupportedExtension(path.extension().wstring())) {
            files_.push_back(path);
        }
    }

    std::ranges::sort(files_, [](const auto& left, const auto& right) {
        return left.filename().wstring() < right.filename().wstring();
    });

    const auto it = std::ranges::find(files_, target);
    if (it == files_.end()) {
        files_.push_back(target);
        currentIndex_ = files_.size() - 1;
        return;
    }

    currentIndex_ = static_cast<std::size_t>(std::distance(files_.begin(), it));
}

const std::vector<std::filesystem::path>& FolderImageIndex::files() const noexcept {
    return files_;
}

std::optional<std::filesystem::path> FolderImageIndex::current() const {
    if (files_.empty() || currentIndex_ >= files_.size()) {
        return std::nullopt;
    }
    return files_[currentIndex_];
}

std::optional<std::filesystem::path> FolderImageIndex::previous() const {
    if (files_.empty() || currentIndex_ == 0) {
        return std::nullopt;
    }
    return files_[currentIndex_ - 1];
}

std::optional<std::filesystem::path> FolderImageIndex::next() const {
    if (files_.empty() || currentIndex_ + 1 >= files_.size()) {
        return std::nullopt;
    }
    return files_[currentIndex_ + 1];
}

std::size_t FolderImageIndex::currentIndex() const noexcept {
    return currentIndex_;
}

bool FolderImageIndex::empty() const noexcept {
    return files_.empty();
}

} // namespace lumina::image
