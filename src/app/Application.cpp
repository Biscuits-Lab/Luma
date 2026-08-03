#include "app/Application.h"

#include <ui_core.h>

namespace lumina::app {

Application::Application()
    : decoders_(image::DecoderRegistry::createDefault()) {}

Application::~Application() = default;

int Application::run(const std::filesystem::path& initialPath) {
    if (ui_init_with_theme(UI_THEME_DARK) != 0) {
        return 1;
    }

    int exitCode = 0;
    {
        ViewerController controller(decoders_);
        if (!controller.createWindow()) {
            ui_shutdown();
            return 2;
        }

        if (!initialPath.empty()) {
            controller.open(initialPath);
        }

        controller.show();
        exitCode = ui_run();
    }

    ui_shutdown();
    return exitCode;
}

} // namespace lumina::app
