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

logger = logging.getLogger(__name__)

class ICCDFunctionWithZODB(ICCDFunction):
    # Any 2-bytes value is fine, this is not what is used to
    # select the application.
    __OPENPGP_FILE_IDENTIFIER = b'\x12\x34'
    __connection = None
    __db = None

    def __init__(self, path, zodb_path, slot_count=1):
        super().__init__(path=path, slot_count=slot_count)
        self.__zodb_path = zodb_path

    def __enter__(self):
        try:
            result = super().__enter__()
            self.__enter()
            return result
        except Exception:
            self.__unenter()
            raise

    def __enter(self):
        logger.info('Initialising the database...')
        self.__db = db = ZODB.DB(
            storage=ZODB.FileStorage.FileStorage(
                file_name=self.__zodb_path,
            ),
            pool_size=1,
        )
        logger.info('Opening a connection to the database...')
        self.__connection = connection = db.open(
            transaction_manager=transaction_manager,
        )
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
                    identifier=self.__OPENPGP_FILE_IDENTIFIER,
                )
                openpgp.activateSelf()
                card.createFile(
                    card.traverse((MASTER_FILE_IDENTIFIER, )),
                    openpgp,
                )
        else:
            logger.info('Card data found, using it.')
        self.__card = card
        logger.info('Inserting the OpenPGP card into slot 0...')
        # TODO: some way of removing/inserting multiple cards ?
        # Ex: one card per thread with a threaded tranaction manager, and some
        # UI on the gadget to let the user select the card to plug.
        self.slot_list[0].insert(card)
        # XXX: Do slow operations now, to avoid timeouts later. Especially, the
        # DWC2 accepts receiving transfer requests while USB bus is active, but
        # rejects them at any other time - including when USB bus is suspended
        # by host. This means than once the UDC is bound to the gadget, we are
        # in a race against the HCD to submit our transfers before the USB idle
        # delay expires.
        logger.debug('Loading OpenPGP from database...')
        with transaction_manager:
            self.__card.traverse(
                path=(MASTER_FILE_IDENTIFIER, self.__OPENPGP_FILE_IDENTIFIER),
            )

    def __unenter(self):
        if self.__connection is not None:
            self.__connection.close()
            self.__connection = None
        if self.__db is not None:
            self.__db.close()
            self.__db = None

    def __exit__(self, exc_type, exc_value, traceback):
        self.__unenter()
        return super().__exit__(exc_type, exc_value, traceback)

    def processEventsForever(self):
        logger.info('All ready, serving until keyboard interrupt')
        super().processEventsForever()

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
        stream=sys.stderr,
    )
    logging.getLogger('smartcard').setLevel(
        level=args.verbose.upper(),
    )
    logger.setLevel(
        level=args.verbose.upper(),
    )
    with (
        GadgetSubprocessManager(
            args=args,
            config_list=[
                # A single configuration
                {
                    'function_list': [
                        functools.partial(
                            ConfigFunctionFFSSubprocess,
                            getFunction=functools.partial(
                                ICCDFunctionWithZODB,
                                slot_count=1,
                                zodb_path=os.path.abspath(args.filestorage),
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
