#pragma once

#include "app/ViewerController.h"
#include "image/DecoderRegistry.h"
#include "image/FolderImageIndex.h"

#include <filesystem>
#include <memory>

namespace lumina::app {

class Application {
public:
    Application();
    ~Application();

    Application(const Application&) = delete;
    Application& operator=(const Application&) = delete;

    [[nodiscard]] int run(const std::filesystem::path& initialPath = {});

private:
    image::DecoderRegistry decoders_;
};

} // namespace lumina::app
