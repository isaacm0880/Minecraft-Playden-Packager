"""Automatic JVM policy must retain the provenance of installed additions."""
import copy
import json
from pathlib import Path

import pytest

from minecraft_playden import arguments, fabric, profiles

FIXTURES = Path(__file__).parent / 'fixtures'


def metadata():
    return json.loads((FIXTURES / '26.3.json').read_text())


@pytest.mark.parametrize('major', [17, 21, 24, 25])
def test_defaults_match_runtime_without_zgc(major):
    doc = metadata()
    doc['javaVersion']['majorVersion'] = major
    before = copy.deepcopy(doc)
    words = arguments.parse_argfile(arguments.generate(doc, ['versions/client.jar'], '../../'))
    assert '-XX:+UseZGC' not in words
    for option in ['-Xms2G', '-Xmx4G', '-XX:+AlwaysPreTouch', '-XX:+UseStringDeduplication']:
        assert option in words
    assert ('-XX:+UseCompactObjectHeaders' in words) == (major >= 25)
    assert doc == before


def test_java24_compact_headers_require_prior_experimental_unlock():
    doc = metadata()
    doc['javaVersion']['majorVersion'] = 24
    doc['arguments']['default-user-jvm'].insert(0, '-XX:+UnlockExperimentalVMOptions')
    assert '-XX:+UseCompactObjectHeaders' in arguments.default_jvm(doc)


@pytest.mark.parametrize('conditional', [False, True])
def test_explicit_jvm_arguments_are_preserved(conditional):
    doc = metadata()
    explicit = ['-XX:+UseZGC', '-Xms3G', '-XX:MaxGCPauseMillis=42']
    doc['arguments']['jvm'] += ([{'value': explicit}] if conditional else explicit)
    words = arguments.parse_argfile(arguments.generate(doc, ['versions/client.jar'], '../../'))
    for option in explicit:
        assert words.count(option) == 1


def test_fabric_keeps_automatic_and_explicit_arguments_separate():
    base = metadata()
    profile = json.loads((FIXTURES / 'fabric/official-26.3-0.20.1.json').read_text())
    profile['arguments']['default-user-jvm'] = ['-XX:+UseZGC', '-XX:+AlwaysPreTouch']
    profile['+jvmArgs'] = ['-XX:+UseZGC', '-Xms3G']
    plan = {'schema': 1, 'minecraft': '26.3', 'loader': '0.20.1', 'profiles': [profile]}
    before = copy.deepcopy((base, plan))
    effective = fabric.derive(base, plan)
    words = arguments.parse_argfile(arguments.generate(effective, ['versions/client.jar'], '../../'))
    assert words.count('-XX:+UseZGC') == 1  # Only the explicit +jvmArgs survives.
    assert '-Xms3G' in words
    assert '-XX:+UseCompactObjectHeaders' in words
    assert (base, plan) == before
    profile['+jvmArgs'] = []
    effective = fabric.derive(base, plan)
    assert '-XX:+UseZGC' not in arguments.parse_argfile(arguments.generate(effective, [], '../../'))


def test_reference_profile_still_has_its_original_defaults():
    words = arguments.parse_argfile(arguments.generate(metadata(), [], '../../', profile=profiles.REFERENCE_PROFILE))
    assert words[0] == '-Xmx4G'
    assert '-XX:+UseZGC' not in words
