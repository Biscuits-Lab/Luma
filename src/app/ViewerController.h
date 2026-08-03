#pragma once

#include "image/DecoderRegistry.h"
#include "image/FolderImageIndex.h"
#include "image/ImageAnalysis.h"
#include "image/ImageFrame.h"

#include <filesystem>
#include <string>
#include <vector>
#include <cstdint>

#include <ui_core.h>

namespace lumina::app {

class ViewerController {
public:
    explicit ViewerController(image::DecoderRegistry& decoders);
    ~ViewerController();

    ViewerController(const ViewerController&) = delete;
    ViewerController& operator=(const ViewerController&) = delete;

    [[nodiscard]] bool createWindow();
    void show();
    void open(const std::filesystem::path& path);
    void openPrevious();
    void openNext();
    void setZoomPercent(float percent);
    void updateViewport(float zoom);

private:
    static void onKey(UiWindow win, int vkCode, void* userdata);
    static void onResize(UiWindow win, int width, int height, void* userdata);
    static void onPinClicked(UiWidget widget, void* userdata);
    static void onFullscreenClicked(UiWidget widget, void* userdata);
    static void onDetailsClicked(UiWidget widget, void* userdata);
    static void onDeleteClicked(UiWidget widget, void* userdata);
    static void onRotateClicked(UiWidget widget, void* userdata);
    static void onFavoriteClicked(UiWidget widget, void* userdata);
    static void onMoreClicked(UiWidget widget, void* userdata);
    static void onRecentClicked(UiWidget widget, void* userdata);
    static void onSliderChanged(UiWidget widget, float value, void* userdata);
    static void onViewportChanged(UiWidget widget, float zoom, float panX, float panY, void* userdata);
    static void onDrop(UiWindow win, const wchar_t* path, void* userdata);

    void buildUi();
    void buildHomeUi();
    void buildDetailsPane();
    void showHome();
    void showViewer();
    void loadRecentFiles();
    void saveRecentFiles() const;
    void recordRecentFile(const std::filesystem::path& path);
    float calculateFitZoom() const;
    void refreshFitZoom(bool preserveZoomPercent);
    float zoomPercentFromImageZoom(float zoom) const;
    float imageZoomFromZoomPercent(float percent) const;
    void toggleAlwaysOnTop();
    void toggleFullscreen();
    void updatePinButton();
    void updateFullscreenButton();
    void toggleDetailsPane();
    void updateDetailsPane(const std::filesystem::path& path, const image::ImageFrame& frame);
    void rotateCurrentImage();
    void toggleFavorite();
    void updateMetadata(const std::filesystem::path& path, const image::ImageFrame& frame);
    void setStatus(const std::wstring& text);
    void applyFrame(const image::ImageFrame& frame);

    image::DecoderRegistry& decoders_;
    image::FolderImageIndex folderIndex_;

    UiWindow window_ = UI_INVALID;
    UiWidget root_ = UI_INVALID;
    UiWidget mainArea_ = UI_INVALID;
    UiWidget contentStack_ = UI_INVALID;
    UiWidget homeView_ = UI_INVALID;
    UiWidget recentList_ = UI_INVALID;
    UiWidget imageView_ = UI_INVALID;
    UiWidget titleBar_ = UI_INVALID;
    UiWidget centeredTitleLabel_ = UI_INVALID;
    UiWidget deleteButton_ = UI_INVALID;
    UiWidget rotateButton_ = UI_INVALID;
    UiWidget favoriteButton_ = UI_INVALID;
    UiWidget moreButton_ = UI_INVALID;
    UiWidget infoLabel_ = UI_INVALID;
    UiWidget zoomSlider_ = UI_INVALID;
    UiWidget zoomLabel_ = UI_INVALID;
    UiWidget pinButton_ = UI_INVALID;
    UiWidget fullscreenButton_ = UI_INVALID;
    UiWidget detailsButton_ = UI_INVALID;
    UiWidget detailsPane_ = UI_INVALID;
    UiWidget detailsFileLabel_ = UI_INVALID;
    UiWidget detailsInfoLabel_ = UI_INVALID;
    UiWidget detailsColorLabel_ = UI_INVALID;
    UiWidget histogramLumaLabel_ = UI_INVALID;
    UiWidget histogramRedLabel_ = UI_INVALID;
    UiWidget histogramGreenLabel_ = UI_INVALID;
    UiWidget histogramBlueLabel_ = UI_INVALID;

    std::vector<std::filesystem::path> recentFiles_;
    std::vector<std::filesystem::path> recentButtonPaths_;
    std::filesystem::path currentPath_;
    bool updatingSlider_ = false;
    bool detailsVisible_ = false;
    bool favorite_ = false;
    int rotationDegrees_ = 0;
    std::uintmax_t currentFileSize_ = 0;
    float fitZoom_ = 1.0F;
    bool alwaysOnTop_ = false;
    bool fullscreen_ = false;
    bool hasFullscreenRestoreRect_ = false;
    int fullscreenRestoreLeft_ = 0;
    int fullscreenRestoreTop_ = 0;
    int fullscreenRestoreWidth_ = 0;
    int fullscreenRestoreHeight_ = 0;
};

} // namespace lumina::app



