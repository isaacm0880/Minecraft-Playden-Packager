// Optional integration harness. Link against an unmodified Playden checkout;
// see docs/PLAYDEN-RECIPES.md. No Playden code is copied into the packager.
#include "runtime/community_rules.h"
#include "runtime/game_settings.h"
#include "runtime/prospero_profile.h"
#include <fstream>
#include <iostream>
#include <iterator>

int main(int argc, char** argv) {
    if (argc != 2) return 2;
    std::ifstream file(argv[1]);
    if (!file) return 2;
    const std::string text((std::istreambuf_iterator<char>(file)), {});
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
        settings.arguments != "@../../playden.args" || game.architecture != "pe64" || game.graphics != "opengl") return 1;
    std::cout << "ok: pe64 java/bin/java.exe @../../playden.args (cwd java/bin, opengl)\n";
}
