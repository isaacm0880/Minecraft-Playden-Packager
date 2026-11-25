// Optional integration harness. Link against an unmodified Playden checkout;
// see docs/PLAYDEN-RECIPES.md. No Playden code is copied into the packager.
#include "runtime/community_rules.h"
#include "runtime/controller_layout.h"
#include "runtime/game_settings.h"
#include "runtime/prospero_profile.h"
#include "community/recipe_import.h"
#include "stores/local/library_source.h"
#include <fstream>
#include <iostream>
#include <iterator>

int main(int argc, char** argv) {
    if (argc < 2) return 2;
    bool input_map = true, long_arguments = true;
    for (int i = 2; i < argc; i++) {
        if (std::string(argv[i]) == "--no-input-map") input_map = false;
        else if (std::string(argv[i]) == "--no-long-arguments") long_arguments = false;
        else return 2;
    }
    std::ifstream file(argv[1]);
    if (!file) return 2;
    const std::string text((std::istreambuf_iterator<char>(file)), {});
    // The folder discovery path uses this same import preparation, including
    // field allowlists, size limits, serialization and community refusals.
    if (std::string(playden::stores::local::kFolderRecipeName) != "recipe.json" ||
        text.size() > playden::stores::local::kMaxFolderRecipeBytes) return 1;
    std::string import_error;
    if (!playden::community::PrepareImportedRecipe(text, &import_error)) {
        std::cerr << import_error << '\n';
        return 1;
    }
    namespace rt = playden::runtime;
    rt::GameRecipe game;
    rt::CommunityRecipe community;
    std::string reason;
    if (!rt::ParseGameRecipe(text, &game, &reason) ||
        !rt::ParseCommunityRecipe(text, &community, &reason)) {
        std::cerr << reason << '\n';
        return 1;
    }
    const auto refusals = rt::CommunityRecipeRefusals(community);
    for (const auto& refusal : refusals) std::cerr << refusal << '\n';
    if (!refusals.empty()) return 1;
    if (community.game.key != playden::NameKey(community.game_name)) return 1;
    rt::BindCommunityRecipe(&community, {}, rt::kFirstRuntimeNumber);
    rt::KitCapabilities kit;
    kit.opengl_zink = true;
    kit.input_map = input_map;
    kit.launch_arguments_4096 = long_arguments;
    rt::EffectiveSettings settings;
    rt::SettingsError error;
    if (!rt::BuildEffectiveSettings(community.recipe, nullptr, {}, kit, &settings, &error)) {
        std::cerr << error.setting_id << ": " << error.message << '\n';
        return 1;
    }
    rt::ProsperoProfile profile;
    const rt::ProfileTarget target{rt::ProsperoProfileId(rt::kFirstRuntimeNumber), "/data/playden-games/minecraft"};
    if (!rt::RenderProsperoProfile(community.recipe, settings, target, &profile, &reason)) {
        std::cerr << reason << '\n';
        return 1;
    }
    if (!settings.launch || settings.launch->executable != "java/bin/java.exe" ||
        rt::LaunchWorkingFolder(*settings.launch) != "java/bin" ||
        settings.arguments != game.launch_entries[0].arguments || game.architecture != "pe64" || game.graphics != "opengl") return 1;
    if (settings.arguments != "@../../playden.args") {
        std::vector<std::string> words;
        if (!rt::SplitProsperoArguments(settings.arguments, &words) || words.empty() ||
            words.back() != "org.minecraftplayden.Java8Launch") return 1;
        std::string roundtrip;
        for (const auto& word : words) {
            if (!roundtrip.empty()) roundtrip += " ";
            roundtrip += "\"" + word + "\"";
        }
        if (roundtrip != settings.arguments) return 1;
    }
    const auto layout = community.recipe.defaults.find("controller_layout");
    const bool mapped = layout != community.recipe.defaults.end() && !layout->second.empty();
    if (rt::ControllerLayoutInEffect(settings) != (mapped && kit.input_map)) return 1;
    if (mapped && kit.input_map) {
        if (settings.values.at("controller").value != "keyboard" ||
            profile.text.find("mode = keyboard\n") == std::string::npos ||
            profile.text.find("map = 1\n") == std::string::npos) return 1;
        rt::ControllerLayout parsed;
        if (!rt::ParseControllerLayout(layout->second, &parsed, &reason)) return 1;
        for (const auto& line : rt::ControllerLayoutLines(parsed))
            if (profile.text.find(line + "\n") == std::string::npos) return 1;
    } else if (profile.text.find("map = 1\n") != std::string::npos) return 1;
    std::cout << "ok: pe64 java/bin/java.exe " << settings.arguments << " (cwd java/bin, opengl)\n";
}
