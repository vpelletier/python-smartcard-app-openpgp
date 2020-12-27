# -*- coding: utf-8 -*-
# Copyright (C) 2018-2020  Vincent Pelletier <plr.vincent@gmail.com>
#
# This file is part of python-smartcard-app-openpgp.
# python-smartcard-app-openpgp is free software; you can redistribute it and/or
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

import functools
import logging
import os
import shutil
import sys
import tempfile
import time
from functionfs.gadget import (
    GadgetSubprocessManager,
    ConfigFunctionFFSSubprocess,
)
import ZODB.DB
import ZODB.FileStorage
from f_ccid import ICCDFunction
from smartcard import (
    Card,
    MASTER_FILE_IDENTIFIER,
)
from smartcard.asn1 import CodecBER
from smartcard.app.openpgp import OpenPGP
from smartcard.app.openpgp.tag import (
    AlgorithmAttributesSignature,
    AlgorithmAttributesDecryption,
    AlgorithmAttributesAuthentication,
)
from smartcard.utils import transaction_manager

logger = logging.getLogger(__name__)

DEFAULT_ALGORITHM_ATTRIBUTES_SIGNATURE = AlgorithmAttributesSignature.encode(
    value={
        'algorithm': AlgorithmAttributesSignature.RSA,
        'parameter_dict': {
            'public_exponent_bit_length': 32,
            'modulus_bit_length': 2048,
            'import_format': AlgorithmAttributesSignature.RSA.IMPORT_FORMAT_STANDARD,
        },
    },
    codec=CodecBER,
)
DEFAULT_ALGORITHM_ATTRIBUTES_DECRYPTION = AlgorithmAttributesDecryption.encode(
    value={
        'algorithm': AlgorithmAttributesDecryption.RSA,
        'parameter_dict': {
            'public_exponent_bit_length': 32,
            'modulus_bit_length': 2048,
            'import_format': AlgorithmAttributesDecryption.RSA.IMPORT_FORMAT_STANDARD,
        },
    },
    codec=CodecBER,
)
DEFAULT_ALGORITHM_ATTRIBUTES_AUTHENTICATION = AlgorithmAttributesAuthentication.encode(
    value={
        'algorithm': AlgorithmAttributesAuthentication.RSA,
        'parameter_dict': {
            'public_exponent_bit_length': 32,
            'modulus_bit_length': 2048,
            'import_format': AlgorithmAttributesAuthentication.RSA.IMPORT_FORMAT_STANDARD,
        },
    },
    codec=CodecBER,
)
assert DEFAULT_ALGORITHM_ATTRIBUTES_SIGNATURE == b'\x01\x08\x00\x00\x20\x00', DEFAULT_ALGORITHM_ATTRIBUTES_SIGNATURE.hex()
assert DEFAULT_ALGORITHM_ATTRIBUTES_DECRYPTION == b'\x01\x08\x00\x00\x20\x00', DEFAULT_ALGORITHM_ATTRIBUTES_DECRYPTION.hex()
assert DEFAULT_ALGORITHM_ATTRIBUTES_AUTHENTICATION == b'\x01\x08\x00\x00\x20\x00', DEFAULT_ALGORITHM_ATTRIBUTES_AUTHENTICATION.hex()

class GnukTestOpenPGP(OpenPGP):
    def __init__(self, *args, **kw):
        super().__init__(*args, **kw)
        self.__blank()

    def blank(self):
        super().blank()
        self.__blank()

    def __blank(self):
        # gnuk tests expect rsa2048 default key attributes.
        self.putData(
            tag=AlgorithmAttributesSignature,
            value=DEFAULT_ALGORITHM_ATTRIBUTES_SIGNATURE,
            encode=False,
            index=0,
        )
        self.putData(
            tag=AlgorithmAttributesDecryption,
            value=DEFAULT_ALGORITHM_ATTRIBUTES_DECRYPTION,
            encode=False,
            index=0,
        )
        self.putData(
            tag=AlgorithmAttributesAuthentication,
            value=DEFAULT_ALGORITHM_ATTRIBUTES_AUTHENTICATION,
            encode=False,
            index=0,
        )

    def _getKeygenPrivateKey(self, index):
        # gnuk is (understandably) more aggressive at requesting new keys than
        # a normal user, and triggers errors when the key is not ready yet.
        # Make it wait a bit (may require extending the test suite timeout...).
        while True:
            private_key = self._v_s_keygen_key_list[index]
            if private_key is not None:
                break
            time.sleep(0.1)
        return private_key

class ICCDFunctionWithZODB(ICCDFunction):
    # Any 2-bytes value is fine, this is not what is used to
    # select the application.
    __OPENPGP_FILE_IDENTIFIER = b'\x12\x34'
    __tmpdir = None

    def __enter__(self):
        try:
            result = super().__enter__()
            self.__enter()
            return result
        except Exception:
            self.__unenter()
            raise

    def __enter(self):
        self.__tmpdir = tmpdir = tempfile.mkdtemp(
            prefix='python-smartcard-gnuk-',
        )
        self.__db = db = ZODB.DB(
            storage=ZODB.FileStorage.FileStorage(
                file_name=os.path.join(tmpdir, 'gnuk.fs'),
            ),
            pool_size=1,
        )
        self.__connection = connection = db.open(
            transaction_manager=transaction_manager,
        )
        root = connection.root
        with transaction_manager:
            card = root.card = Card(
                name='py-openpgp'.encode('ascii'),
            )
            openpgp = GnukTestOpenPGP(
                identifier=self.__OPENPGP_FILE_IDENTIFIER,
            )
            openpgp.activateSelf()
            card.createFile(
                card.traverse((MASTER_FILE_IDENTIFIER, )),
                openpgp,
            )
        self.slot_list[0].insert(card)

    def __unenter(self):
        if self.__connection is not None:
            self.__connection.close()
            self.__connection = None
        if self.__db is not None:
            self.__db.close()
            self.__db = None
        if self.__tmpdir is not None:
            shutil.rmtree(self.__tmpdir)
            self.__tmpdir = None

    def __exit__(self, exc_type, exc_value, traceback):
        self.__unenter()
        return super().__exit__(exc_type, exc_value, traceback)

    def processEventsForever(self):
        logger.info('All ready, serving until keyboard interrupt')
        super().processEventsForever()

def gnuk():
    """
    Command to run gnuk tests with.

    Gnuk test suite assumes rsa2048 default key format, but I would very much
    like to keep the default on Curve25519.
    """
    parser = GadgetSubprocessManager.getArgumentParser(
        description='ONLY for running gnuk tests.',
    )
    args = parser.parse_args()
    logging.basicConfig(stream=sys.stderr)
    logging.getLogger('smartcard').setLevel(level='DEBUG')
    logger.setLevel(level='DEBUG')
    with (
        GadgetSubprocessManager(
            args=args,
            config_list=[
                {
                    'function_list': [
                        functools.partial(
                            ConfigFunctionFFSSubprocess,
                            getFunction=functools.partial(
                                ICCDFunctionWithZODB,
                                slot_count=1,
                            ),
                        ),
                    ],
                    'MaxPower': 500,
                    'lang_dict': {
                        0x409: {
                            'configuration': 'python-usb-f-iccd reader with openpgp card',
                        },
                    },
                }
            ],
            idVendor=0x1d6b,
            idProduct=0x0104,
            lang_dict={
                0x409: {
                    'product': 'python-smartcard-app-openpgp',
                    'manufacturer': 'Vincent Pelletier',
                },
            },
        ) as gadget
    ):
        gadget.waitForever()
