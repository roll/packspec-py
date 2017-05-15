# -*- coding: utf-8 -*-
from __future__ import division
from __future__ import print_function
from __future__ import absolute_import
from __future__ import unicode_literals

import io
import os
import re
import six
import copy
import json
import yaml
import click
import importlib
from emoji import emojize
from collections import OrderedDict


# Module API

@click.command()
@click.argument('path', default='.')
def cli(path):
    specs = parse_specs(path)
    success = test_specs(specs)
    if not success:
        exit(1)


# Helpers

def parse_specs(path):

    # Specs
    specmap = {}
    for root, dirnames, filenames in os.walk(path):
        for filename in filenames:
            if filename.endswith('.yml'):
                filepath = os.path.join(root, filename)
                filecont = io.open(filepath, encoding='utf-8').read()
                spec = parse_spec(filecont)
                if not spec:
                    continue
                if spec['package'] not in specmap:
                    specmap[spec['package']] = spec
                else:
                    specmap[spec['package']]['features'].extend(spec['features'])

    # Hooks
    hookmap = {}
    for root, dirnames, filenames in os.walk(path):
        for filename in filenames:
            if filename == 'packspec.py':
                filepath = os.path.join(root, filename)
                filecont = io.open(filepath, encoding='utf-8').read()
                scope = {}
                exec(filecont, scope)
                for name, attr in scope.items():
                    if name.startswith('_'):
                        continue
                    hookmap[name] = attr

    # Result
    specs = [specmap[package] for package in sorted(specmap)]
    for spec in specs:
        spec['scope'].update(hookmap)

    return specs


def parse_spec(spec):

    # Package
    contents = yaml.load(spec)
    try:
        feature = parse_feature(contents[0])
        package = feature['result']
        assert feature['assign'] == 'PACKAGE'
        assert not feature['skip']
    except Exception:
        return None

    # Features
    features = []
    for feature in contents:
        feature = parse_feature(feature)
        features.append(feature)

    # Scope
    scope = {}
    module = importlib.import_module(package)
    for name in dir(module):
        if name.startswith('_'):
            continue
        scope[name] = getattr(module, name)

    return {
        'package': package,
        'features': features,
        'scope': scope,
    }


def parse_feature(feature):
    if isinstance(feature, six.string_types):
        return {'comment': feature}
    left, right = list(feature.items())[0]

    # Left side
    call = False
    match = re.match(r'^(?:(.*):)?(?:([^=]*)=)?([^=].*)?$', left)
    skip, assign, property = match.groups()
    if skip:
        filters = skip.split(':')
        skip = (filters[0] == 'not') == ('py' in filters)
    if not assign and not property:
        raise Exception('Non-valid feature')
    if property:
        call = True
        if property.endswith('=='):
            property = property[:-2]
            call = False

    # Right side
    args = []
    kwargs = OrderedDict()
    result = right
    if call:
        result = None
        for item in right:
            if isinstance(item, dict) and len(item) == 1:
                item_left, item_right = list(item.items())[0]
                if item_left == '==':
                    result = item_right
                    continue
                if item_left.endswith('='):
                    kwargs[item_left[:-1]] = item_right
                    continue
            args.append(item)

    # Text repr
    text = property
    if assign:
        text = '%s = %s' % (assign, property or json.dumps(result, ensure_ascii=False))
    if call:
        items = []
        for item in args:
            items.append(json.dumps(item, ensure_ascii=False))
        for name, item in kwargs.items():
            items.append('%s=%s' % (name, json.dumps(item, ensure_ascii=False)))
        text = '%s(%s)' % (text, ', '.join(items))
    if result and not assign:
        text = '%s == %s' % (text, json.dumps(result, ensure_ascii=False))
    text = re.sub(r'{"([^{}]*?)": null}', r'\1', text)

    return {
        'comment': None,
        'skip': skip,
        'call': call,
        'assign': assign,
        'property': property,
        'args': args,
        'kwargs': kwargs,
        'result': result,
        'text': text,
    }


def test_specs(specs):
    success = True
    message = click.style(emojize('\n #  ', use_aliases=True))
    message += click.style('Python\n', bold=True)
    click.echo(message)
    for spec in specs:
        spec_success = test_spec(spec)
        success = success and spec_success
    return success


def test_spec(spec):
    passed = 0
    amount = len(spec['features'])
    message = click.style(emojize(':heavy_minus_sign:'*3 + '\n', use_aliases=True))
    click.echo(message)
    for feature in spec['features']:
        passed += test_feature(feature, spec['scope'])
    success = (passed == amount)
    color = 'green'
    message = click.style(emojize('\n :heavy_check_mark:  ', use_aliases=True), fg='green', bold=True)
    if not success:
        color = 'red'
        message = click.style(emojize('\n :x:  ', use_aliases=True), fg='red', bold=True)
    message += click.style('%s: %s/%s\n' % (spec['package'], passed, amount), bold=True, fg=color)
    click.echo(message)
    return success


def test_feature(feature, scope):

    # Comment
    if feature['comment']:
        message = click.style(emojize('\n #  ', use_aliases=True))
        message += click.style('%s\n' % feature['comment'], bold=True)
        click.echo(message)
        return True

    # Skip
    if feature['skip']:
        message = click.style(emojize(' :heavy_minus_sign:  ', use_aliases=True), fg='yellow')
        message += click.style('%s' % feature['text'])
        click.echo(message)
        return True

    # Execute
    feature = dereference_feature(feature, scope)
    result = feature['result']
    if feature['property']:
        try:
            property = scope
            for name in feature['property'].split('.'):
                property = get_property(property, name)
            if feature['call']:
                result = property(*feature['args'], **feature['kwargs'])
            else:
                result = property
        except Exception:
            result = 'ERROR'

    # Assign
    if feature['assign']:
        owner = scope
        names = feature['assign'].split('.')
        for name in names[:-1]:
            owner = get_property(owner, name)
        if get_property(owner, names[-1]) is not None and names[-1].isupper():
            raise Exception('Can\'t update the constant "%s"' % names[-1])
        set_property(owner, names[-1], result)

    # Compare
    result = isoformat_value(result)
    success = result == feature['result'] if feature['result'] is not None else result != 'ERROR'
    if success:
        message = click.style(emojize(' :heavy_check_mark:  ', use_aliases=True), fg='green')
        message += click.style('%s' % feature['text'])
        click.echo(message)
    else:
        try:
            result_text = json.dumps(result)
        except TypeError:
            result_text = repr(result)
        message = click.style(emojize(' :x:  ', use_aliases=True), fg='red')
        message += click.style('%s # %s' % (feature['text'], result_text))
        click.echo(message)

    return success


def dereference_feature(feature, scope):
    feature = copy.deepcopy(feature)
    if feature['call']:
        feature['args'] = dereference_value(feature['args'], scope)
        feature['kwargs'] = dereference_value(feature['kwargs'], scope)
    feature['result'] = dereference_value(feature['result'], scope)
    return feature


def dereference_value(value, scope):
    value = copy.deepcopy(value)
    if isinstance(value, dict) and len(value) == 1 and list(value.values())[0] is None:
        result = scope
        for name in list(value.keys())[0].split('.'):
            result = get_property(result, name)
        value = result
    elif isinstance(value, dict):
        for key, item in list(value.items()):
            value[key] = dereference_value(item, scope)
    elif isinstance(value, list):
        for index, item in enumerate(list(value)):
            value[index] = dereference_value(item, scope)
    return value


def isoformat_value(value):
    isoformat = getattr(value, 'isoformat', None)
    if isoformat is not None:
        value = isoformat()
    elif isinstance(value, dict):
        for key, item in list(value.items()):
            value[key] = isoformat_value(item)
    elif isinstance(value, list):
        for index, item in enumerate(list(value)):
            value[index] = isoformat_value(item)
    return value


def get_property(owner, name):
    if isinstance(owner, dict):
        return owner.get(name)
    return getattr(owner, name, None)


def set_property(owner, name, value):
    if isinstance(owner, dict):
        owner[name] = value
        return
    return setattr(owner, name, value)


# Main program

if __name__ == '__main__':
    cli()
