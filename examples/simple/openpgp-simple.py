#!/usr/bin/env python3
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
import sys
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
from smartcard.app.openpgp import OpenPGP
from smartcard.utils import transaction_manager

logger = logging.getLogger()

class SubprocessICCD(ConfigFunctionFFSSubprocess):
    # Any 2-bytes value is fine, this is not what is used to
    # select the application.
    OPENPGP_FILE_IDENTIFIER = b'\x12\x34'

    def __init__(self, zodb_path, **kw):
        super().__init__(**kw)
        self.__zodb_path = zodb_path

    def run(self):
        logger.info('Initialising the database...')
        function = self.function
        db = ZODB.DB(
            storage=ZODB.FileStorage.FileStorage(
                file_name=self.__zodb_path,
            ),
            pool_size=1,
        )
        logger.info('Opening a connection to the database...')
        connection = db.open(
            transaction_manager=transaction_manager,
        )
        try:
            root = connection.root
            try:
                card = root.card
            except AttributeError:
                logger.info(
                    'Database does not contain a card, building an new one...',
                )
                with transaction_manager:
                    card = root.card = Card(
                        name='py-openpgp'.encode('ascii'),
                    )
                    openpgp = OpenPGP(
                        identifier=self.OPENPGP_FILE_IDENTIFIER,
                    )
                    openpgp.activateSelf()
                    card.createFile(
                        card.traverse((MASTER_FILE_IDENTIFIER, )),
                        openpgp,
                    )
            else:
                logger.info('Card data found, using it.')
            logger.info('Inserting the OpenPGP card into slot 0...')
            function.slot_list[0].insert(card)
            logger.info('All ready, serving until keyboard interrupt')
            super().run()
        finally:
            connection.close()
            db.close()

def main():
    parser = GadgetSubprocessManager.getArgumentParser(
        description='Emulate a reader with a smartcard inserted which '
        'contains an OpenPGP application.',
    )
    parser.add_argument(
        '--filestorage',
        required=True,
        help='Path to a ZODB FileStorage file, for smartcard persistence.',
    )
    parser.add_argument(
        '--serial',
        help='String to use as USB device serial number',
    )
    parser.add_argument(
        '--verbose',
        default='warning',
        choices=['critical', 'error', 'warning', 'info', 'debug'],
        help='Set verbosity level (default: %(default)s). '
        'WARNING: "debug" level will display all APDU-level exchanges with '
        'the host, which will include secret keys during import, PINs during '
        'verification and modification, cipher- and clear-text for '
        'en/decryption operations.',
    )
    args = parser.parse_args()
    logging.basicConfig(
        level=args.verbose.upper(),
        stream=sys.stderr,
    )
    with (
        GadgetSubprocessManager(
            args=args,
            config_list=[
                # A single configuration
                {
                    'function_list': [
                        functools.partial(
                            SubprocessICCD,
                            zodb_path=os.path.abspath(args.filestorage),
                            getFunction=functools.partial(
                                ICCDFunction,
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
                    'serialnumber': args.serial,
                    'product': 'python-smartcard-app-openpgp',
                    'manufacturer': 'Vincent Pelletier',
                },
            },
        ) as gadget
    ):
        gadget.waitForever()

if __name__ == '__main__':
    main()
