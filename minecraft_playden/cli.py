"""Command line entry point."""
import argparse
import sys
from pathlib import Path
from .downloads import Downloader, default_cache
from .exporter import build, INSTRUCTIONS
from .validator import validate
from .versions import VersionResolver
from .sources import ClientSource
from .profiles import PROFILES, DEFAULT_PROFILE
from . import __version__
from .operations import OperationCancelled


def main() -> int:
    parser = argparse.ArgumentParser(description='Build portable Windows Minecraft packages for Playden')
    parser.add_argument('--version', action='version', version=f'Minecraft Playden Packager {__version__}')
    parser.add_argument('--cache', type=Path, default=default_cache())
    parser.add_argument('--offline', action='store_true', help='Use only installed files and verified cached downloads')
    commands = parser.add_subparsers(dest='command', required=True)
    gui = commands.add_parser('gui', help='Open the optional PySide6 desktop app (local imports only)')
    gui.add_argument('--path', type=Path, action='append', default=[], help='Additional launcher/instance folder to scan; repeatable')
    versions = commands.add_parser('versions')
    versions.add_argument('--all', action='store_true', help='Include snapshots and historical versions')
    package = commands.add_parser('build')
    package.add_argument('--source', choices=['local'], required=True,
                         help='Use an existing local Minecraft client; missing clients are never downloaded')
    package.add_argument('--version', help='Minecraft version; inferred from local metadata when possible')
    package.add_argument('--instance', type=Path, help='Existing local installation or instance directory (read-only)')
    package.add_argument('--client-jar', type=Path, help='Existing vanilla client JAR; requires --version, local mode only')
    package.add_argument('--adapter', choices=['auto', 'portable', 'official', 'prism', 'multimc'], default='auto')
    package.add_argument('--libraries-root', type=Path, help='Read-only installed Fabric library cache override (local mode)')
    package.add_argument('--java-runtime', type=Path, help='Existing matching Windows x64 Java home (never host Java)')
    package.add_argument('--allow-runtime-download', action='store_true', default=True, help='Allow missing Windows Java downloads (default); verified local/cache preferred')
    package.add_argument('--no-runtime-download', dest='allow_runtime_download', action='store_false', help='Use local/cached Windows Java only')
    package.add_argument('--offline', action='store_true', default=argparse.SUPPRESS, help='Use installed files and cached metadata/dependencies only')
    package.add_argument('--output', type=Path, required=True)
    package.add_argument('--username', default='Player')
    package.add_argument('--launch-profile', choices=PROFILES, default=DEFAULT_PROFILE,
                         help='official (default), or reference-26.3 for a separate reproduction of the supplied working launch configuration')
    package.add_argument('--prepare-fabric', action='store_true', help='Prepare Fabric remapping headlessly using its exact installed loader')
    package.add_argument('--remap-java', type=Path, help='Host-native JDK java executable for headless remapping; never downloaded')
    prepare = commands.add_parser('prepare-fabric', help='Prepare Fabric remapping in a new copy of a validated package; no game/mod launch')
    prepare.add_argument('path', type=Path)
    prepare.add_argument('--output', type=Path, required=True)
    prepare.add_argument('--remap-java', type=Path, help='Host-native JDK java executable (Linux/Windows/macOS)')
    package.add_argument('--no-controller-layout', dest='controller_layout', action='store_false',
                         help='Omit built-in keyboard/mouse controller mappings for Controlify or other controller mods')
    package.add_argument('--exclude-saves', action='store_true', help='Omit world saves; worlds are included by default')
    local_import = commands.add_parser('import-instance', help='Phase 2A: preserve instance files without claiming a verified launch')
    local_import.add_argument('--instance', type=Path, required=True)
    local_import.add_argument('--version')
    local_import.add_argument('--output', type=Path, required=True)
    local_import.add_argument('--exclude-saves', action='store_true', help='Omit world saves; worlds are included by default')
    check = commands.add_parser('validate')
    check.add_argument('path', type=Path)
    transfer = commands.add_parser('transfer-ps5', help='Send a validated package to a paired PS5 LAN receiver')
    transfer.add_argument('path', type=Path)
    transfer.add_argument('--host', required=True, help='Private PS5 IPv4 address')
    transfer.add_argument('--destination', choices=['internal', 'usb0'], default='internal')
    transfer.add_argument('--port', type=int, help='Receiver data port (configured default with --start-receiver; otherwise 9025)')
    startup = transfer.add_mutually_exclusive_group()
    startup.add_argument('--start-receiver', action='store_true', help='Deploy through the installed SDK and pair automatically using encrypted loader output')
    startup.add_argument('--pair', action='store_true', help='Pair with a management-v2 receiver using a hidden six-digit code prompt')
    transfer.add_argument('--sdk', type=Path, help='PS5 Payload SDK root override for --start-receiver')
    transfer.add_argument('--receiver-elf', type=Path, help='Receiver ELF override for --start-receiver')
    transfer.add_argument('--loader-port', type=int, help='Already-running ELF loader port override for --start-receiver')
    transfer.add_argument('--control-port', type=int, help='Management-v2 control port (configured default with --start-receiver; otherwise 9026)')
    transfer.add_argument('--name', help='Optional package name override; defaults to the current local package folder name')
    transfer.add_argument('--token-env', default='PLAYDEN_PS5_TOKEN',
                          help='Existing transfer key environment variable: authenticated reuse with --start-receiver; legacy mode otherwise prompts without echo (not used with --pair)')
    transfer.add_argument('--diagnostics', action='store_true', help='Print numeric timing counters to stderr (no token or file paths)')
    transfer.add_argument('--socket-buffer', type=int, help='Experimental socket buffer bytes, 4096..4194304; OS default if omitted')
    transfer.add_argument('--chunk-size', type=int, default=65536, help='Experimental DATA size, 4096..65536 (default 65536)')
    transfer.add_argument('--pipeline-files', action='store_true', help='Experimental: overlap each file ENTRY acknowledgement with DATA; at most two pending ACKs')
    args = parser.parse_args()
    if args.command == 'transfer-ps5' and not args.start_receiver and any(value is not None for value in (args.sdk, args.receiver_elf, args.loader_port)):
        parser.error('--sdk, --receiver-elf and --loader-port require --start-receiver')
    try:
        if args.command == 'prepare-fabric':
            from .fabric_remap import prepare_package
            prepare_package(args.path, args.output, args.remap_java)
            print(f'Fabric preparation validated: {args.output}. PS5 launch still requires physical testing.')
            return 0
        if args.command == 'transfer-ps5':
            import getpass
            import os
            from .network_transfer import NetworkRequest
            from .terminal_progress import TransferProgress
            from .operations import CancellationToken
            from .receiver_manager import Deployment, ReceiverConfiguration, pair_receiver
            from .network_protocol import pairing_key
            token = CancellationToken()
            deployment: Deployment | None = None
            interactive = sys.stderr.isatty() and sys.stdout.isatty() and 'NO_COLOR' not in os.environ
            import json
            metrics: dict[str, int | float] = {}
            try:
                with TransferProgress(interactive=interactive) as report:
                    if args.start_receiver:
                        config = ReceiverConfiguration.load()
                        deployment = Deployment()
                        existing = os.environ.get(args.token_env)
                        paired = deployment.start(args.host, args.sdk or config.sdk, args.receiver_elf or config.elf,
                                                  args.loader_port if args.loader_port is not None else config.loader_port,
                                                  args.port if args.port is not None else config.port,
                                                  args.control_port if args.control_port is not None else config.control_port,
                                                  token, existing_key=pairing_key(existing) if existing else None, progress=report)
                        pairing, port = paired.key.hex(), paired.info.port
                    elif args.pair:
                        paired = pair_receiver(args.host, getpass.getpass('PS5 receiver six-digit pairing code: '),
                                               args.control_port if args.control_port is not None else 9026, token)
                        pairing, port = paired.key.hex(), paired.info.port
                    else:
                        pairing = os.environ.get(args.token_env) or getpass.getpass('Legacy PS5 receiver pairing token: ')
                        port = args.port if args.port is not None else 9025
                    result = NetworkRequest(args.path, args.host, pairing, args.destination, port, args.name,
                                            args.socket_buffer, args.chunk_size, args.pipeline_files).run(
                        report, cancellation=token, diagnostics=metrics.update if args.diagnostics else None)
            finally:
                token.cancel()
                if deployment is not None:
                    deployment.close()  # Local SDK connection only; no remote STOP.
                if args.diagnostics:
                    print('sender_metrics ' + json.dumps(metrics, sort_keys=True), file=sys.stderr)
            if args.diagnostics and result.receiver_metrics is not None:
                print('receiver_metrics ' + json.dumps(result.receiver_metrics, sort_keys=True), file=sys.stderr)
            print(f'PS5 package received and verified: {result.destination}. Local output retained.')
            return 0
        if args.command == 'import-instance':
            from .instance_content import import_instance
            import_instance(args.instance, args.output, args.version, args.exclude_saves)
            print(f'Instance files imported: {args.output}. Launchability is unverified; loader resolution belongs to Phase 2B.')
            return 0
        if args.command == 'gui':
            from .gui import launch
            return launch(args.cache, args.path)
        if args.command == 'validate':
            errors = validate(args.path)
            if errors:
                print('\n'.join(errors), file=sys.stderr)
                return 1
            import json
            inventory = json.loads((args.path / 'package.json').read_text(encoding='utf-8'))
            if inventory.get('kind') == 'instance-import':
                print('Instance import metadata validated. Launchability is unverified (Phase 2B).')
            else:
                from .fabric_remap import package_status
                state = package_status(args.path, validated=True)
                instructions = INSTRUCTIONS
                from .java8 import COMMAND
                if (args.path / COMMAND).is_file():
                    instructions = instructions.replace('@../../playden.args', (args.path / COMMAND).read_text(encoding='utf-8'))
                if state.required or state.prepared:
                    print(state.message)
                print('Package files and portable launch arguments validated.\n' + instructions)
        else:
            if args.command == 'build' and args.source == 'local' and args.instance is not None:
                from .instance_content import inspect, check_separate
                content = inspect(args.instance, args.version)
                check_separate(content.root, args.cache)
                check_separate(content.root, args.output)
                if len(content.loaders) == 1 and content.loaders[0].name == 'Fabric':
                    from .fabric import inspect as inspect_fabric
                    installed = inspect_fabric(args.instance, args.version, args.libraries_root)
                    check_separate(installed.libraries, args.cache)
                    check_separate(installed.libraries, args.output)
                    if installed.launcher_root is not None:
                        check_separate(installed.launcher_root, args.cache)
                        check_separate(installed.launcher_root, args.output)
            downloader = Downloader(args.cache, offline=args.offline,
                                    allow_runtime_download=getattr(args, 'allow_runtime_download', False),
                                    runtime_paths=(args.java_runtime,) if getattr(args, 'java_runtime', None) else ())
            if args.command == 'versions':
                for version in VersionResolver(downloader).versions(refresh=not args.offline):
                    if args.all or version['type'] == 'release':
                        print(f'{version["id"]}\t{version["type"]}')
            else:
                source = ClientSource(args.source, args.version, args.instance, args.client_jar, args.adapter, libraries_root=args.libraries_root)
                from .discovery import discover
                installations = discover([args.instance], include_defaults=False).installations if args.instance else ()
                instance_name = installations[0].name if len(installations) == 1 else args.output.name
                build(args.output, downloader, source, args.username, launch_profile=args.launch_profile,
                      exclude_saves=args.exclude_saves, instance_name=instance_name,
                      prepare_fabric=args.prepare_fabric, remap_java=args.remap_java, controller_layout=args.controller_layout)
                instructions = INSTRUCTIONS
                from .java8 import COMMAND
                if (args.output / COMMAND).is_file():
                    instructions = instructions.replace('@../../playden.args', (args.output / COMMAND).read_text(encoding='utf-8'))
                if (args.output / 'package.json').is_file():
                    from .fabric_remap import package_status
                    state = package_status(args.output, validated=True)
                    if state.required or state.prepared:
                        print(state.message)
                print(f'Package validated: {args.output}\n' + instructions)
        return 0
    except (KeyboardInterrupt, OperationCancelled):
        print('LAN transfer interrupted; local files retained. If publication started, inspect the PS5 import folder.'
              if args.command == 'transfer-ps5' else 'Build cancelled; no package published.', file=sys.stderr)
        return 130
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f'Error: {exc}', file=sys.stderr)
        return 1
