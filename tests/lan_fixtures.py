"""Offline exporter fixture shared by tests and the development LAN benchmark."""
import io
from dataclasses import replace
import json
from unittest.mock import patch
from minecraft_playden.desktop import LocalBuildRequest
from minecraft_playden.discovery import discover
from minecraft_playden.validator import validate
from test_fabric_launch import installed, archive, save


def build_package(tmp_path):
    instance, metadata, _ = installed(tmp_path / 'source', '26.3', '0.20.1')
    payloads = {
        'https://fixture/netty': archive({'io/netty/channel/unix/Errors.class': b'class'}),
        'https://fixture/fastutil': archive({'it/unimi/dsi/fastutil/floats/FloatList.class': b'class'}),
        'https://fixture/native': archive({'lwjgl.dll': b'MZnative'}),
        'https://fixture/index': b'{"objects":{}}',
        'https://fixture/base': json.dumps(metadata).encode(),
    }
    def request(req, **kwargs):
        url = req.full_url
        if url.endswith('version_manifest_v2.json'):
            return io.BytesIO(json.dumps({'versions': [{'id': metadata['id'], 'url': 'https://fixture/base'}]}).encode())
        return io.BytesIO(payloads[url])
    def runtime(metadata, downloader, java):
        for path in ['bin/java.exe', 'bin/server/jvm.dll', 'release']:
            save(java, path, b'Windows-x64-fixture')
        return {'fixture': 'windows x64'}
    source = discover([instance], include_defaults=False).installations[0]
    output = tmp_path / 'Minecraft Package'
    with patch('urllib.request.urlopen', side_effect=request), patch('minecraft_playden.runtime.install', side_effect=runtime):
        LocalBuildRequest(replace(source, name=output.name), output, tmp_path / 'cache').run()
    assert validate(output) == []
    return output

