"""Optional real-Java regression: quoting and JVM properties do not hide classes."""
import base64
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from minecraft_playden.arguments import parse_argfile, quote

FIXTURES = Path(__file__).parent / 'fixtures'


@unittest.skipUnless(shutil.which('java') and shutil.which('javac'), 'A host JDK is required for the real argument-file smoke test')
class JavaArgumentTests(unittest.TestCase):
    def test_reference_and_gui_quoting_with_their_jvm_properties(self):
        version = subprocess.run(['javac', '-version'], capture_output=True, text=True, check=True)
        major = int((version.stdout or version.stderr).split()[1].split('.')[0])
        if major < 17:
            self.skipTest('This exporter targets Java 17+')
        with tempfile.TemporaryDirectory(prefix='Playden Probe With Spaces ') as temporary:
            root = Path(temporary)
            classes = root / 'classes'
            classes.mkdir()
            interface = root / 'FloatList.java'
            interface.write_text('package it.unimi.dsi.fastutil.floats; public interface FloatList {}')
            source = root / 'ArgProbe.java'
            source.write_text('''import java.util.Base64; import java.nio.charset.StandardCharsets;
public class ArgProbe { static void out(String key,String value) {System.out.println(key+"="+Base64.getEncoder().encodeToString(value.getBytes(StandardCharsets.UTF_8)));}
public static void main(String[] args) throws Exception {out("original",System.getProperty("probe.originalcp"));out("nativepath",System.getProperty("java.library.path"));out("game",String.join("\\n",args));out("class",Class.forName("it.unimi.dsi.fastutil.floats.FloatList",false,ClassLoader.getSystemClassLoader()).getName());}}
''')
            subprocess.run(['javac', '--release', '17', '-d', str(classes), str(interface), str(source)], capture_output=True, check=True)
            for filename in ('reference-26.3-playden.args', 'official-26.3-playden.args'):
                for quote_all in (False, True):
                    with self.subTest(filename=filename, quote_all=quote_all):
                        tokens = parse_argfile((FIXTURES / filename).read_text())
                        boundary = tokens.index('net.minecraft.client.main.Main')
                        game = tokens[boundary + 1:]
                        original_cp = tokens[tokens.index('-cp') + 1]
                        tokens[tokens.index('-cp') + 1] = str(classes).replace('\\', '/')
                        tokens[boundary] = 'ArgProbe'
                        tokens.insert(boundary, '-Dprobe.originalcp=' + original_cp)
                        # Make only host paths platform-native; the complete Windows
                        # classpath remains a JVM property to test lossless parsing.
                        args = '\n'.join(quote(token) if quote_all or any(c.isspace() or c in '\\\"\'#' for c in token) else token for token in tokens)
                        argument_file = root / 'probe.args'
                        argument_file.write_text(args + '\n')
                        result = subprocess.run(['java', '@' + str(argument_file)], cwd=root, capture_output=True, text=True, check=True, timeout=20)
                        decoded = {key: base64.b64decode(value).decode() for key, value in (line.split('=', 1) for line in result.stdout.splitlines())}
                        self.assertEqual(decoded['original'], original_cp)
                        self.assertEqual(decoded['nativepath'], '../../natives')
                        self.assertEqual(decoded['game'], '\n'.join(game))
                        self.assertEqual(decoded['class'], 'it.unimi.dsi.fastutil.floats.FloatList')
