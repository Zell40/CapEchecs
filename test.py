# -*- coding: utf-8 -*-
"""Tests Limnoria du plugin CapEchecs."""

try:
    from supybot.test import PluginTestCase
except ImportError:
    PluginTestCase = None

if PluginTestCase is not None:
    class CapEchecsTestCase(PluginTestCase):
        plugins = ("CapEchecs",)
