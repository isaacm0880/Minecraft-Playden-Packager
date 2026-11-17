/* Headless adapter: invokes the selected loader's remapper, never Knot or Minecraft. */
import java.lang.reflect.InvocationTargetException;
import java.lang.reflect.Method;
import java.lang.reflect.Proxy;
import java.net.URL;
import java.net.URLClassLoader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.ArrayList;
import java.util.Collections;
import java.util.List;
import java.util.Map;

class FabricRemap {
    private static String json(String value) {
        return "\"" + value.replace("\\", "\\\\").replace("\"", "\\\"")
                .replace("\r", "\\r").replace("\n", "\\n") + "\"";
    }

    public static void main(String[] args) throws Exception {
        if (args.length != 7) throw new IllegalArgumentException("Expected classpath, client, game directory, receipt, mappings, source namespace, Minecraft version");
        System.setProperty("java.awt.headless", "true");
        System.setProperty("fabric.runtimeMappingNamespace", "intermediary");
        System.setProperty("fabric.mappingPath", args[4]);
        List<URL> urls = new ArrayList<>();
        for (String line : Files.readAllLines(Paths.get(args[0]), StandardCharsets.UTF_8)) {
            urls.add(Paths.get(line).toUri().toURL());
        }
        // Keep this loader alive through Fabric's log shutdown hook. The isolated
        // worker process owns its lifetime; no game/mod entry point is invoked.
        URLClassLoader cl = new URLClassLoader(urls.toArray(new URL[0]), ClassLoader.getPlatformClassLoader());
        Class<?> launcherType = cl.loadClass("net.fabricmc.loader.impl.launch.FabricLauncher");
        Object mappings = cl.loadClass("net.fabricmc.loader.impl.launch.MappingConfiguration").getConstructor().newInstance();
        Object launcher = Proxy.newProxyInstance(cl, new Class<?>[]{launcherType}, (proxy, method, values) -> {
            switch (method.getName()) {
                case "getMappingConfiguration": return mappings;
                case "isDevelopment": return false;
                case "getClassPath": return Collections.emptyList();
                default: throw new UnsupportedOperationException("Unexpected Fabric launcher call: " + method);
            }
        });
        Method setLauncher = cl.loadClass("net.fabricmc.loader.impl.launch.FabricLauncherBase")
                .getDeclaredMethod("setLauncher", launcherType);
        setLauncher.setAccessible(true); // Older loader APIs expose this initialization hook privately.
        setLauncher.invoke(null, launcher);
        Path client = Paths.get(args[1]);
        // Use the same loader's version lookup: snapshot cache keys are normalized
        // versions, not necessarily Mojang's raw version/profile identifier.
        Object version = cl.loadClass("net.fabricmc.loader.impl.game.minecraft.McVersionLookup")
                .getMethod("getVersion", List.class, String.class, String.class)
                .invoke(null, Collections.singletonList(client), null, null);
        String normalized = (String) version.getClass().getMethod("getNormalized").invoke(version);
        Class<?> helper = cl.loadClass("net.fabricmc.loader.impl.game.GameProviderHelper");
        Method remap;
        Object[] inputs;
        try {
            remap = helper.getMethod("deobfuscate", Map.class, String.class, String.class, String.class, Path.class, launcherType);
            inputs = new Object[]{Collections.singletonMap("client", client), args[5], "minecraft", normalized, Paths.get(args[2]), launcher};
        } catch (NoSuchMethodException legacy) {
            if (!args[5].equals("official")) throw new IllegalArgumentException("Legacy remapping API only supports the official namespace", legacy);
            remap = helper.getMethod("deobfuscate", Map.class, String.class, String.class, Path.class, launcherType);
            inputs = new Object[]{Collections.singletonMap("client", client), "minecraft", normalized, Paths.get(args[2]), launcher};
        }
        try {
            @SuppressWarnings("unchecked")
            Map<String, Path> result = (Map<String, Path>) remap.invoke(null, inputs);
            Path output = result.get("client").toAbsolutePath().normalize();
            Path game = Paths.get(args[2]).toAbsolutePath().normalize();
            if (!output.startsWith(game) || output.equals(client.toAbsolutePath().normalize())) {
                throw new IllegalStateException("Fabric did not produce an isolated remapping cache");
            }
            String loaderVersion = (String) cl.loadClass("net.fabricmc.loader.impl.FabricLoaderImpl").getField("VERSION").get(null);
            String receipt = "{\"normalized_version\":" + json(normalized) + ",\"loader\":" + json(loaderVersion)
                    + ",\"minecraft\":" + json(args[6]) + ",\"relative_output\":"
                    + json(game.relativize(output).toString().replace('\\', '/')) + "}";
            Files.write(Paths.get(args[3]), receipt.getBytes(StandardCharsets.UTF_8));
        } catch (InvocationTargetException ex) {
            // Retain the complete underlying cause instead of the reflection wrapper.
            ex.getCause().printStackTrace();
            System.exit(1);
        }
    }
}
