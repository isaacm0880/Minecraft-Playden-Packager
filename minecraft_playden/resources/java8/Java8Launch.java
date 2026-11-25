/* Minecraft Playden Packager Java 8 adapter. SPDX-License-Identifier: MIT
 * Independently implemented; no launcher or game code is included.
 */
package org.minecraftplayden;

import java.io.DataInputStream;
import java.io.IOException;
import java.lang.reflect.InvocationTargetException;
import java.net.URI;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.Paths;
import java.util.jar.JarFile;

public final class Java8Launch {
    private static String readString(DataInputStream input) throws IOException {
        int size = input.readInt();
        if (size < 0 || size > 1048576) throw new IOException("Invalid launch string length");
        byte[] bytes = new byte[size];
        input.readFully(bytes);
        return new String(bytes, StandardCharsets.UTF_8);
    }

    public static void main(String[] ignored) throws Throwable {
        if (!System.getProperty("java.specification.version").equals("1.8")) {
            throw new IOException("This adapter requires Java 8; runtime substitution is refused");
        }
        Path archive = Paths.get(Java8Launch.class.getProtectionDomain().getCodeSource().getLocation().toURI()).toRealPath();
        Path root = archive.getParent();
        String main;
        String[] arguments;
        try (JarFile jar = new JarFile(archive.toFile())) {
            StringBuilder classpath = new StringBuilder();
            String entries = jar.getManifest().getMainAttributes().getValue("Class-Path");
            for (String entry : entries.split(" ")) {
                URI uri = archive.toUri().resolve(entry);
                if (!"file".equals(uri.getScheme())) throw new IOException("Nonlocal classpath URL");
                Path path = Paths.get(uri).toRealPath();
                if (!path.startsWith(root) || !Files.isRegularFile(path)) throw new IOException("Unsafe classpath entry");
                if (classpath.length() > 0) classpath.append(java.io.File.pathSeparator);
                classpath.append(path);
            }
            // Knot discovers dependencies through this property, not just the system loader.
            System.setProperty("java.class.path", classpath.toString());
            try (DataInputStream input = new DataInputStream(jar.getInputStream(jar.getJarEntry("playden-launch.bin")))) {
                if (input.readInt() != 0x50444a38) throw new IOException("Invalid launch plan");
                main = readString(input);
                int count = input.readInt();
                if (count < 0 || count > 10000) throw new IOException("Invalid argument count");
                arguments = new String[count];
                for (int i = 0; i < count; i++) arguments[i] = readString(input);
                if (input.read() != -1) throw new IOException("Trailing launch plan data");
            }
        }
        try {
            Class.forName(main).getMethod("main", String[].class).invoke(null, (Object) arguments);
        } catch (InvocationTargetException failure) {
            throw failure.getCause();
        }
    }
}
