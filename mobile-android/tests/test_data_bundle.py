import hashlib
import json
from pathlib import Path
import shutil
import zipfile

import pytest
from test_mobile_engine import BASE, private_session
import data_bundle

def test_local_transfer_validated_base(tmp_path):
    dest = tmp_path / 'private-base'
    shutil.copytree(BASE, dest)
    packet = tmp_path / 'base.zip'
    data_bundle.create(BASE, packet)
    data_bundle.install(packet, dest)
    assert (dest / 'manifest.json').exists()
    for name in data_bundle.FILES:
        assert (dest / name).read_bytes() == (BASE / name).read_bytes()
    assert not dest.with_name(dest.name + '.backup').exists()

@pytest.mark.parametrize('attack', ['traversal', 'hash', 'duplicate'])
def test_rejects_invalid_packet_without_changing_base(tmp_path, attack):
    dest = tmp_path / 'private-base'
    shutil.copytree(BASE, dest)
    before = (dest / 'manifest.json').read_bytes()
    packet = tmp_path / 'base.zip'
    data_bundle.create(BASE, packet)
    if attack in ('traversal', 'duplicate'):
        with zipfile.ZipFile(packet, 'a') as z:
            z.writestr('../outside' if attack == 'traversal' else 'manifest.json', '{}')
    else:
        with zipfile.ZipFile(packet) as z:
            content = {name: z.read(name) for name in z.namelist()}
        content['config/fiscais.json'] = b'{}'
        with zipfile.ZipFile(packet, 'w') as z:
            for name, value in content.items():
                z.writestr(name, value)
    with pytest.raises(ValueError):
        data_bundle.install(packet, dest)
    assert (dest / 'manifest.json').read_bytes() == before
    assert not (tmp_path / 'outside').exists()
