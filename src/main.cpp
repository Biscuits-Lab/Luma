#include "app/Application.h"
#include "platform/WindowsStrings.h"

#include <Windows.h>

#include <filesystem>

int WINAPI wWinMain(HINSTANCE, HINSTANCE, PWSTR, int) {
    lumina::app::Application app;
    const auto initialPath = lumina::platform::commandLineArgument(1);
    return app.run(initialPath.empty() ? std::filesystem::path{} : std::filesystem::path(initialPath));
}

