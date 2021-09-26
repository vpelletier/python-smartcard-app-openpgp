# Copyright (C) 2016-2020  Vincent Pelletier <plr.vincent@gmail.com>
#
# This file is part of python-smartcard-app-openpgp.
# python-smarcard is free software; you can redistribute it and/or
# modify it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# python-smartcard-app-openpgp is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with python-smartcard-app-openpgp.  If not, see <http://www.gnu.org/licenses/>.
from setuptools import setup, find_namespace_packages
from codecs import open
import os
import versioneer

long_description = open(
    os.path.join(os.path.dirname(__file__), 'README.rst'),
    encoding='utf8',
).read()

setup(
    name='smartcard-app-openpgp',
    description=next(x for x in long_description.splitlines() if x.strip()),
    long_description='.. contents::\n\n' + long_description,
    keywords='smartcard openpgp',
    version=versioneer.get_version(),
    cmdclass=versioneer.get_cmdclass(),
    author='Vincent Pelletier',
    author_email='plr.vincent@gmail.com',
    url='http://github.com/vpelletier/python-smartcard-app-openpgp',
    license='GPLv3+',
    platforms=['any'],
    packages=find_namespace_packages(include=['smartcard.app.*']),
    install_requires=[
        'smartcard',
        'cryptography>=3.1',
    ],
    extras_require={
        'ccid': ['usb-f-ccid'],
        'randpin': ['freetype-py', 'gpiochip2'],
    },
    entry_points={
        'console_scripts': [
            'smartcard-openpgp-testtarget-gnuk = smartcard.app.openpgp.cli.test:gnuk [ccid]',
            'smartcard-openpgp-simple = smartcard.app.openpgp.cli.simple:main [ccid]',
            'smartcard-openpgp-randpin-epaper = smartcard.app.openpgp.cli.randpin.epaper:main [ccid,randpin]',
        ],
    },
    classifiers=[
        'License :: OSI Approved :: GNU General Public License v3 or later (GPLv3+)',
        'Operating System :: OS Independent',
        'Programming Language :: Python :: 3',
        'Programming Language :: Python :: Implementation :: CPython',
        'Programming Language :: Python :: Implementation :: PyPy',
        'Topic :: Security :: Cryptography',
    ],
)
