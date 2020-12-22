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

from collections import deque
import errno
import functools
import itertools
import logging
import os
import random
import select
import sys
import time
import freetype
from functionfs.gadget import (
    GadgetSubprocessManager,
    ConfigFunctionFFSSubprocess,
)
import ZODB
import ZODB.FileStorage
from f_ccid import ICCDFunction
from smartcard import (
    Card,
    MASTER_FILE_IDENTIFIER,
)
from smartcard.app.openpgp import (
    OpenPGPRandomPassword,
    PINQueueConnection,
)
from smartcard.utils import transaction_manager
from framebuffer import Framebuffer
from waveshare_epaper import WaveShareEPaper

logger = logging.getLogger()

# Periodically, generate new PINs, all the time that the device is enumerated by
# a host (...and the OpenPGP application is unghostified in ZODB object cache).
# The current and previous pins are both valid.
# The correct pin appears in a certain cell.
# The correct cell can be changed by PIN change, with a specially-formatted PIN
# (others rejected).
# When card application exits: Display a power-down message
# When not enumerated/plugged: Display a "connect to host" message

DISPLAY_ROW_NAME = ('A', 'B', 'C', 'D')
DISPLAY_COLUMN_NAME = ('1', '2', '3')

class ICCDFunctionWithRandomPinDisplay(ICCDFunction):
    _column_width = 80
    _line_height = 21
    _line_height_with_margin = _line_height + 3
    _column_name_height = 18
    _column_name_x_offset = 28
    _row_name_width = 12
    _column_name_to_x = {
        '1': 0 * _column_width + 15,
        '2': 1 * _column_width + 15,
        '3': 2 * _column_width + 15,
    }
    _row_name_to_y = {
        'A': 1 * _line_height_with_margin,
        'B': 2 * _line_height_with_margin,
        'C': 3 * _line_height_with_margin,
        'D': 4 * _line_height_with_margin,
    }
    _can_generate = False

    def __init__(self, path, display, fontface, slot_count=1):
        super().__init__(path=path, slot_count=slot_count)
        self.__display = display
        self.__fontface = fontface
        self.__framebuffer = Framebuffer( # XXX: use PIL instead of custom framebuffer ?
            # Note: framebuffer and display disagree on what is height and
            # width, but are otherwise happy.
            width=display.height,
            height=display.width,
        )
        # Each item is a mapping from cell ids to contained PIN for the whole
        # generated table. Cell ids are a row name followed by a column name.
        self.pin_queue = deque([], 2)

    def updateDisplay(self, force_clean=False, wait=True):
        display = self.__display
        display.blit(
            image=self.__framebuffer.pixelbuffer,
            x=0,
            y=0,
        )
        display.swap(force_clean=force_clean, wait=wait)

    def printAt(
        self,
        x,
        y,
        text,
        width=None,
        color=Framebuffer.COLOR_XOR,
    ):
        self.__framebuffer.printLineAt(
            self.__fontface,
            x=x,
            y=y,
            text=text,
            width=width,
            height=self._line_height,
            color=color,
        )

    def displayReadyUnplugged(self):
        self.__framebuffer.blank(color=Framebuffer.COLOR_OFF)
        self.printAt(
            x=0,
            y=0, # TODO: position
            text="Ready, unplugged", # TODO: better caption
        )
        # force_clean to wipe any PIN ghosting.
        self.updateDisplay(force_clean=True, wait=False)

    def generatePinTable(self, tries_left, force=False):
        if not self._can_generate and not force:
            return
        fb = self.__framebuffer
        fb.blank(color=fb.COLOR_ON)
        fb.rect(
            0, 0,
            self._row_name_width, fb.height - 1,
            color=fb.COLOR_OFF,
            fill=True,
        )
        fb.rect(
            self._row_name_width, 0,
            fb.width - 1, self._column_name_height,
            color=fb.COLOR_OFF,
            fill=True,
        )
        for caption, x in self._column_name_to_x.items():
            self.printAt(
                x + self._column_name_x_offset, 0,
                caption,
                width=15,
                color=Framebuffer.COLOR_ON,
            )
        for caption, y in self._row_name_to_y.items():
            self.printAt(
                0, y,
                caption,
                width=15,
                color=Framebuffer.COLOR_ON,
            )
        # TODO: display tries left in top-left corner.
        pin_dict = {}
        for row_name, column_name in itertools.product(
            DISPLAY_ROW_NAME,
            DISPLAY_COLUMN_NAME,
        ):
            pin_dict[
                row_name + column_name
            ] = value = '%06i' % random.randint(0, 999999)
            self.printAt(
                self._column_name_to_x[column_name],
                self._row_name_to_y[row_name],
                value,
                width=self._column_width,
                color=Framebuffer.COLOR_OFF,
            )
        self.updateDisplay(wait=False)
        self.pin_queue.append(pin_dict)

    def __enter__(self):
        result = super().__enter__()
        self.displayReadyUnplugged()
        return result

    def __exit__(self, exc_type, exc_value, traceback):
        self.__framebuffer.blank(color=Framebuffer.COLOR_OFF)
        self.printAt(
            x=0,
            y=0, # TODO: position
            text="Exited", # TODO: better caption
        )
        self.updateDisplay(force_clean=True)
        return super().__exit__(exc_type, exc_value, traceback)

    def onUnbind(self):
        self.displayReadyUnplugged()
        self._can_generate = False
        super().onUnbind()

    def onEnable(self):
        self._can_generate = True
        super().onEnable()

    def onDisable(self):
        self.displayReadyUnplugged()
        self._can_generate = False
        super().onDisable()

class SubprocessICCD(ConfigFunctionFFSSubprocess):
    PIN_GENERATION_DELAY = 30
    # Any 2-bytes value is fine, this is not what is used to
    # select the application. We are using it internally to look it up.
    OPENPGP_FILE_IDENTIFIER = b'\x12\x34'

    def __init__(self, zodb_path, **kw):
        super().__init__(**kw)
        self.__zodb_path = zodb_path

    def run(self):
        logger.info('Initialising the database...')
        function = self.function
        class DB(ZODB.DB):
            klass = functools.partial(
                PINQueueConnection,
                openpgp_kw={
                    'pin_queue': function.pin_queue,
                    'row_name_set': DISPLAY_ROW_NAME,
                    'column_name_set': DISPLAY_COLUMN_NAME,
                },
            )
        db = DB(
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
                logger.info('Database does not contain a card, building an new one...')
                with transaction_manager:
                    card = root.card = Card(
                        name='py-openpgp'.encode('ascii'),
                    )
                    openpgp = OpenPGPRandomPassword(
                        identifier=self.OPENPGP_FILE_IDENTIFIER,
                    )
                    openpgp.activateSelf()
                    card.createFile(
                        card.traverse((MASTER_FILE_IDENTIFIER, )),
                        openpgp,
                    )
            else:
                logger.info('Card data found, using it.')
            # TODO: some way of removing/inserting multiple cards ?
            # Ex: one card per thread with a threaded tranaction manager, and some
            # UI on the gadget to let the user select the card to plug.
            logger.info('Inserting the OpenPGP card into slot 0...')
            function.slot_list[0].insert(card)
            logger.info('All ready, serving until keyboard interrupt')
            with select.epoll(1) as epoll:
                epoll.register(function.eventfd, select.EPOLLIN)
                poll = epoll.poll
                processEvents = function.processEvents
                next_pin_generation = 0
                while True:
                    now = time.time()
                    if next_pin_generation <= now or not function.pin_queue:
                        next_pin_generation = now + self.PIN_GENERATION_DELAY
                        with transaction_manager:
                            tries_left = card.traverse(
                                path=(MASTER_FILE_IDENTIFIER, self.OPENPGP_FILE_IDENTIFIER),
                            ).getPIN1TriesLeft()
                        # Note: everytime PIN1 try count changes the pin queue
                        # is flushed, so this should be sufficient.
                        function.generatePinTable(
                            tries_left=tries_left,
                        )
                    try:
                        # XXX: set a timeout, to trigger PIN changes even when
                        # there is no activity ?
                        # Drawback: on the pi zero, the UDC cannot detect bus
                        # disconnection, because the 5V rail is the USB VBUS
                        # rail, so PIN generation never stops after initial
                        # enumeration.
                        event_list = poll()
                    except OSError as exc:
                        if exc.errno != errno.EINTR:
                            raise
                    else:
                        processEvents()
        except KeyboardInterrupt:
            pass
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
        WaveShareEPaper() as display,
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
                                ICCDFunctionWithRandomPinDisplay,
                                display=display,
                                # TODO: argument, auto-detection of default...
                                fontface=freetype.Face('/usr/share/fonts/truetype/noto/NotoMono-Regular.ttf'),
                                slot_count=4,
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
