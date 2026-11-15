"""Compare reference/GUI Java argument files in an isolated Wine prefix.

Requires Linux, Wine and javac. Only a diagnostic main class executes; Minecraft
is not launched. Both source packages are read-only and all Wine/JVM work files
are created in a new /tmp folder. Results are not PS5 compatibility evidence.
"""
import argparse
import base64
import json
import os
import subprocess
import tempfile
from pathlib import Path

from minecraft_playden.arguments import parse_argfile, quote

JAVA_SOURCE = r'''import java.util.*; import java.lang.management.*; import java.nio.charset.StandardCharsets;
public class PlaydenProbe {
 static void out(String key, String value) {
  System.out.println(key+"="+Base64.getEncoder().encodeToString(value.getBytes(StandardCharsets.UTF_8)));
 }
 public static void main(String[] args) throws Exception {
  out("classpath",System.getProperty("java.class.path"));
  out("nativepath",System.getProperty("java.library.path"));
  out("vmargs",String.join("\n",ManagementFactory.getRuntimeMXBean().getInputArguments()));
  out("gameargs",String.join("\n",args));
  Class<?> c=Class.forName("it.unimi.dsi.fastutil.floats.FloatList",false,ClassLoader.getSystemClassLoader());
  out("loaded",c.getName());
  out("origin",c.getProtectionDomain().getCodeSource().getLocation().toString());
 }
}
'''


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--reference', type=Path, required=True)
    parser.add_argument('--package', type=Path, required=True)
    options = parser.parse_args()
    reference, package = options.reference.resolve(), options.package.resolve()
    for candidate in (reference, package):
        tokens = parse_argfile((candidate / 'playden.args').read_text())
        # Deliberately limit this reporting tool to this project's token-free
        # exports; never dump arbitrary authenticated launcher argument files.
        if '--accessToken' not in tokens or tokens[tokens.index('--accessToken') + 1] != '0':
            parser.error('Only token-free placeholder argument files are allowed')
    workspace = Path(tempfile.mkdtemp(prefix='playden-windows-probe-', dir='/tmp'))
    classes = workspace / 'classes'
    classes.mkdir()
    source = workspace / 'PlaydenProbe.java'
    source.write_text(JAVA_SOURCE)
    subprocess.run(['javac', '--release', '17', '-d', str(classes), str(source)], check=True)
    env = os.environ.copy()
    env.update(WINEPREFIX=str(workspace / 'wine'), WINEDEBUG='-all', WINEARCH='win64')
    results = []
    cases = [('reference-original', reference, False), ('reference-quoted', reference, True),
             ('gui-original', package, True), ('gui-unquoted', package, False)]
    for name, candidate, quote_all in cases:
        root = workspace / name
        (root / 'java/bin').mkdir(parents=True)
        (root / 'libraries').symlink_to(candidate / 'libraries', target_is_directory=True)
        if (candidate / 'versions').exists():
            (root / 'versions').symlink_to(candidate / 'versions', target_is_directory=True)
        tokens = parse_argfile((candidate / 'playden.args').read_text())
        tokens[tokens.index('net.minecraft.client.main.Main')] = 'PlaydenProbe'
        # The additional directory contains only PlaydenProbe, never Fastutil.
        tokens[tokens.index('-cp') + 1] += ';Z:' + classes.as_posix()
        (root / 'probe.args').write_text('\n'.join(quote(t) if quote_all else t for t in tokens) + '\n')
        process = subprocess.run(['wine', str(package / 'java/bin/java.exe'), '@../../probe.args'],
                                 cwd=root / 'java/bin', env=env, text=True, capture_output=True, timeout=90)
        decoded = {}
        for line in process.stdout.splitlines():
            if '=' in line:
                key, value = line.split('=', 1)
                try:
                    decoded[key] = base64.b64decode(value).decode()
                except (ValueError, UnicodeError):
                    continue
        results.append({'name': name, 'exit': process.returncode, 'values': decoded, 'stderr': process.stderr[-3000:]})
        print(name, process.returncode, decoded.get('loaded', 'NOT LOADED'), flush=True)
    output = workspace / 'results.json'
    output.write_text(json.dumps(results, indent=2))
    print('Results:', output)
    return 0 if all(result['exit'] == 0 for result in results) else 1


if __name__ == '__main__':
    raise SystemExit(main())
