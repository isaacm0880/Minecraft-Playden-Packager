// Integration harness: link unmodified Playden sources, not shipped with the app.
#include "homebrew/folder_art.hpp"
#include "homebrew/artwork_backend.hpp"
#include "stores/local/library_source.h"
#include <filesystem>
#include <iostream>

int main(int argc, char** argv) {
    namespace hb = playden::homebrew;
    if (argc != 2) return 2;
    if (std::string(hb::kFolderArtDirectory) != "playden" ||
        std::string(playden::stores::local::kGameFolderDirectory) != "playden" ||
        std::string(hb::kFolderIconName) != "icon.png" ||
        std::string(hb::kFolderBackgroundName) != "background.png" ||
        hb::kMaxPlayerArtSide != 8192 || hb::kMaxPlayerArtPixels != 3840ULL*2160 ||
        hb::kMaxPlayerArtBytes != 32u*1024u*1024u) return 1;
    for (const auto* name : {hb::kFolderIconName, hb::kFolderBackgroundName})
        if (!std::filesystem::is_regular_file(std::filesystem::path(argv[1]) / hb::kFolderArtDirectory / name)) return 1;
    const auto problems = hb::CheckFolderArt(argv[1]);
    for (const auto& problem : problems) std::cerr << problem << '\n';
    if (!problems.empty()) return 1;
    std::cout << "ok: Playden playden/icon.png and playden/background.png fully decoded\n";
}
