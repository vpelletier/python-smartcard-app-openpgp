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
import fcntl
import functools
import itertools
import logging
import os
import random
import select
import sys
import threading
import time
import freetype
from functionfs.gadget import (
    GadgetSubprocessManager,
    ConfigFunctionFFSSubprocess,
)
from gpiochip2 import (
    EXPECT_PRECONFIGURED,
    GPIO_V2_LINE_FLAG,
    GPIOChip,
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
from .framebuffer import Framebuffer
from .waveshare_epaper import WaveShareEPaper

logger = logging.getLogger(__name__)

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
    _column_width = 75
    _line_height = 21
    _line_height_with_margin = _line_height + 3
    _column_name_height = 18
    _column_name_x_offset = 28
    _row_name_width = 16
    _column_x_offset = 25
    _column_name_to_x = {
        '1': 0 * _column_width + _column_x_offset,
        '2': 1 * _column_width + _column_x_offset,
        '3': 2 * _column_width + _column_x_offset,
    }
    _row_name_to_y = {
        'A': 1 * _line_height_with_margin,
        'B': 2 * _line_height_with_margin,
        'C': 3 * _line_height_with_margin,
        'D': 4 * _line_height_with_margin,
    }
    _can_generate = False
    __connection = None
    __db = None
    # Any 2-bytes value is fine, this is not what is used to
    # select the application. We are using it internally to look it up.
    __OPENPGP_FILE_IDENTIFIER = b'\x12\x34'
    __PIN_GENERATION_DELAY = 30
    __next_pin_generation = 0

    __battery_capacity_path = '/sys/class/power_supply/battery/capacity'
    __battery_x = 218
    __battery_y = 4
    __battery_width = 32
    __battery_height = 11
    __battery_thread_can_run = True
    __battery_last_state = (None, None)
    __battery_pipe_read = None
    __battery_pipe_write = None

    def __init__(
        self,
        path,
        zodb_path,
        display,
        fontface,
        slot_count=1,
        gpiochip=None,
    ):
        super().__init__(path=path, slot_count=slot_count)
        self.__zodb_path = zodb_path
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
        self.__pin_queue = deque([], 2)
        # Probe GPIO pin 4 state before opening it.
        battery_gpio_info = gpiochip.getLineInfo(4)
        self.__has_battery = has_battery = (
            os.path.exists(self.__battery_capacity_path) and
            battery_gpio_info['flags'] & GPIO_V2_LINE_FLAG.INPUT and
            not battery_gpio_info['flags'] & GPIO_V2_LINE_FLAG.USED
        )
        if has_battery:
            self.__battery_poll = battery_poll = select.poll()
            self.__battery_thread = threading.Thread(
                target=self.__batteryUpdateThread,
                name='battery-monitor',
                daemon=True,
            )
            self.__battery_gpio = battery_gpio = gpiochip.openLines(
                line_list=[4],
                flags=(
                    GPIO_V2_LINE_FLAG.INPUT |
                    GPIO_V2_LINE_FLAG.EDGE_RISING |
                    GPIO_V2_LINE_FLAG.EDGE_FALLING |
                    GPIO_V2_LINE_FLAG.BIAS_DISABLED
                ),
                consumer='python-smartcard'.encode('ascii'),
                expect_preconfigured=EXPECT_PRECONFIGURED.DIRECTION,
            )
            fcntl.fcntl(
                battery_gpio,
                fcntl.F_SETFL,
                fcntl.fcntl(battery_gpio, fcntl.F_GETFL) | os.O_NONBLOCK,
            )
            battery_poll.register(battery_gpio, select.POLLIN | select.POLLPRI)

    def __batteryUpdateThread(self):
        poll = self.__battery_poll.poll
        while True:
            # Check battery level every minute, react to events immediately
            poll(60000)
            # purge all available gpio events
            self.__battery_gpio.read()
            if not self.__battery_thread_can_run:
                break
            self.displayBattery()
            self.updateDisplay(wait=False)

    def updateDisplay(self, wait=True):
        display = self.__display
        display.blit(
            image=self.__framebuffer.pixelbuffer,
            x=0,
            y=0,
        )
        display.swap(wait=wait)

    def _blank(self, color):
        # Battery need a redraw
        self.__battery_last_state = (None, None)
        self.__framebuffer.blank(color=color)

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
        self._blank(color=Framebuffer.COLOR_OFF)
        self.printAt(
            x=35,
            y=45,
            text="Ready, unplugged",
        )
        self.displayBattery()
        self.updateDisplay(wait=False)

    def displayBattery(self):
        if not self.__has_battery:
            return
        with open(self.__battery_capacity_path, 'rb') as capacity:
            capacity = int(capacity.read().decode('ascii'))
        for level, threshold in enumerate((10, 25, 50, 75, 90, 100)):
            if capacity < threshold:
                break
        charging = self.__battery_gpio.value
        state = (level, charging)
        if state == self.__battery_last_state:
            return
        self.__battery_last_state = state
        fb = self.__framebuffer
        battery_middle, battery_middle_remainder = divmod(self.__battery_height, 2)
        fb.rect( # battery bump
            self.__battery_x,
            self.__battery_y + battery_middle - 2,
            self.__battery_x + 2,
            self.__battery_y + battery_middle + 2 + battery_middle_remainder,
            color=fb.COLOR_ON,
            fill=True,
        )
        fb.rect( # battery body and draw area
            self.__battery_x + 2,
            self.__battery_y,
            self.__battery_x - 2 + self.__battery_width,
            self.__battery_y + self.__battery_height,
            color=fb.COLOR_ON,
            fill=True,
        )
        if level == 0:
            if not charging:
                fb.printLineAt(
                    self.__fontface,
                    x=self.__battery_x + self.__battery_width // 2 - 2,
                    y=self.__battery_y + 1,
                    text="!",
                    width=16,
                    height=self.__battery_height,
                    color=Framebuffer.COLOR_OFF,
                )
            fb.rect(
                self.__battery_x + self.__battery_width - 5,
                self.__battery_y + 1,
                self.__battery_x + self.__battery_width - 3,
                self.__battery_y - 1 + self.__battery_height,
                color=fb.COLOR_OFF,
                fill=True,
            )
        else:
            bar_width = (self.__battery_width - 4) // 4
            for index in range(4):
                fb.rect(
                    self.__battery_x + 2 + index       * bar_width + 1,
                    self.__battery_y + 1,
                    self.__battery_x + 2 + (index + 1) * bar_width - 1,
                    self.__battery_y - 1 + self.__battery_height,
                    color=fb.COLOR_OFF,
                    fill=level > (4 - index),
                )
        if charging:
            # Draw the litning bolt as two filled triangles
            left_x = self.__battery_x + 4
            left_y = self.__battery_y + 6
            right_x = self.__battery_x + self.__battery_width - 4
            right_y = self.__battery_y + 6
            middle_x = left_x + (right_x - left_x) // 2
            middle_top = self.__battery_y + 2
            middle_half_width = 4
            line = fb.line
            for offset in range(middle_half_width + 1):
                line(
                    left_x, left_y,
                    middle_x, middle_top + offset,
                    color=Framebuffer.COLOR_ON,
                )
            for offset in range(middle_half_width):
                line(
                    middle_x, middle_top + middle_half_width + offset,
                    right_x, right_y,
                    color=Framebuffer.COLOR_ON,
                )
            # top left outline
            line(
                left_x, left_y,
                middle_x, middle_top,
                color=Framebuffer.COLOR_OFF,
            )
            # center left outline
            line(
                left_x, left_y,
                middle_x - 1, middle_top + middle_half_width,
                color=Framebuffer.COLOR_OFF,
            )
            # bottom left outline
            line(
                middle_x - 1, middle_top + middle_half_width,
                middle_x, middle_top + middle_half_width + middle_half_width,
                color=Framebuffer.COLOR_OFF,
            )
            # top right outline
            line(
                middle_x, middle_top,
                middle_x + 1, middle_top + middle_half_width,
                color=Framebuffer.COLOR_OFF,
            )
            # center right outline
            line(
                middle_x + 1, middle_top + middle_half_width,
                right_x, right_y,
                color=Framebuffer.COLOR_OFF,
            )
            # bottom right outline
            line(
                middle_x, middle_top + middle_half_width + middle_half_width,
                right_x, right_y,
                color=Framebuffer.COLOR_OFF,
            )

    def __generatePinTable(self, tries_left, force=False):
        if not self._can_generate and not force:
            return
        fb = self.__framebuffer
        self._blank(color=fb.COLOR_ON)
        # Column headers background
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
        # Rounded corners
        fb.rect(
            self._row_name_width + 1, self._column_name_height + 1,
            self._row_name_width + 1 + 8, self._column_name_height + 1 + 8,
            color=fb.COLOR_OFF,
            fill=True,
        )
        fb.circle(
            self._row_name_width + 1 + 8, self._column_name_height + 1 + 8,
            8,
            color=fb.COLOR_ON,
            fill=True,
        )
        fb.rect(
            fb.width - 1 - 8, self._column_name_height + 1,
            fb.width - 1, self._column_name_height + 1 + 8,
            color=fb.COLOR_OFF,
            fill=True,
        )
        fb.circle(
            fb.width - 1 - 8, self._column_name_height + 1 + 8,
            8,
            color=fb.COLOR_ON,
            fill=True,
        )
        fb.rect(
            self._row_name_width + 1, fb.height - 1 - 8,
            self._row_name_width + 1 + 8, fb.height - 1,
            color=fb.COLOR_OFF,
            fill=True,
        )
        fb.circle(
            self._row_name_width + 1 + 8, fb.height - 1 - 8,
            8,
            color=fb.COLOR_ON,
            fill=True,
        )
        fb.rect(
            fb.width - 1 - 8, fb.height - 1 - 8,
            fb.width - 1, fb.height - 1,
            color=fb.COLOR_OFF,
            fill=True,
        )
        fb.circle(
            fb.width - 1 - 8, fb.height - 1 - 8,
            8,
            color=fb.COLOR_ON,
            fill=True,
        )
        # Column headers captions
        for caption, x in self._column_name_to_x.items():
            self.printAt(
                x + self._column_name_x_offset, 0,
                caption,
                width=15,
                color=Framebuffer.COLOR_ON,
            )
        for caption, y in self._row_name_to_y.items():
            self.printAt(
                3, y,
                caption,
                width=15,
                color=Framebuffer.COLOR_ON,
            )
        # 3-tries background
        fb.circle(
            8, 8, 8,
            color=Framebuffer.COLOR_ON,
            fill=True,
        )
        fb.circle(
            34, 8, 8,
            color=Framebuffer.COLOR_ON,
            fill=True,
        )
        fb.rect(
            8, 0,
            34, 16,
            color=Framebuffer.COLOR_ON,
            fill=True,
        )
        for index, center_x in ((0, 8), (1, 21), (2, 34)):
            if tries_left <= index:
                # Try failed: cross
                fb.line(
                    center_x - 4, 4,
                    center_x + 4, 12,
                    color=Framebuffer.COLOR_OFF,
                )
                fb.line(
                    center_x - 3, 4,
                    center_x + 4, 11,
                    color=Framebuffer.COLOR_OFF,
                )
                fb.line(
                    center_x - 4, 5,
                    center_x + 3, 12,
                    color=Framebuffer.COLOR_OFF,
                )
                fb.line(
                    center_x + 4, 4,
                    center_x - 4, 12,
                    color=Framebuffer.COLOR_OFF,
                )
                fb.line(
                    center_x + 3, 4,
                    center_x - 4, 11,
                    color=Framebuffer.COLOR_OFF,
                )
                fb.line(
                    center_x + 4, 5,
                    center_x - 3, 12,
                    color=Framebuffer.COLOR_OFF,
                )
            else:
                # Try still available: circle
                fb.circle(
                    center_x, 8, 5,
                    color=Framebuffer.COLOR_OFF,
                    fill=False,
                )
        # Generate random PINs and display them
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
        self.displayBattery()
        self.updateDisplay(wait=False)
        self.__pin_queue.append(pin_dict)

    def __enter__(self):
        try:
            result = super().__enter__()
            self.__enter()
            return result
        except Exception:
            self.__unenter()
            raise

    def __enter(self):
        self.displayReadyUnplugged()
        logger.info('Initialising the database...')
        # Note: access __pin_queue outside of DB declaration to get the
        # intended __ mangling.
        pin_queue = self.__pin_queue
        class DB(ZODB.DB):
            klass = functools.partial(
                PINQueueConnection,
                openpgp_kw={
                    'pin_queue': pin_queue,
                    'row_name_set': DISPLAY_ROW_NAME,
                    'column_name_set': DISPLAY_COLUMN_NAME,
                },
            )
        self.__db = db = DB(
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
                openpgp = OpenPGPRandomPassword(
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
            ).getPIN1TriesLeft()
        if self.__has_battery:
            battery_pipe_read, battery_pipe_write = os.pipe()
            self.__battery_pipe_read = battery_pipe_read = os.fdopen(battery_pipe_read, 'rb', buffering=0)
            self.__battery_pipe_write = battery_pipe_write = os.fdopen(battery_pipe_write, 'wb', buffering=0)
            self.__battery_poll.register(battery_pipe_read, select.POLLIN)
            self.__battery_thread_can_run = True
            self.__battery_thread.start()
        logger.debug('Waiting for screen to be ready...')
        self.__display.wait()

    def __unenter(self):
        if self.__has_battery and self.__battery_pipe_write is not None:
            self.__battery_thread_can_run = False
            self.__battery_pipe_write.write(b'\x00') # XXX: is closing the write end enough ?
            self.__battery_thread.join()
            self.__battery_poll.unregister(self.__battery_pipe_read)
            self.__battery_pipe_write.close()
            self.__battery_pipe_read.close()
        if self.__connection is not None:
            self.__connection.close()
            self.__connection = None
        if self.__db is not None:
            self.__db.close()
            self.__db = None
        self._blank(color=Framebuffer.COLOR_OFF)
        self.printAt(
            x=85,
            y=45,
            text="Exited",
        )
        self.updateDisplay()

    def __exit__(self, exc_type, exc_value, traceback):
        self.__unenter()
        return super().__exit__(exc_type, exc_value, traceback)

    def processEventsForever(self):
        logger.info('All ready, serving until keyboard interrupt')
        super().processEventsForever()

    def processEvents(self):
        super().processEvents()
        now = time.time()
        if self.__next_pin_generation <= now or not self.__pin_queue:
            self.__next_pin_generation = now + self.__PIN_GENERATION_DELAY
            with transaction_manager:
                tries_left = self.__card.traverse(
                    path=(MASTER_FILE_IDENTIFIER, self.__OPENPGP_FILE_IDENTIFIER),
                ).getPIN1TriesLeft()
            self.__generatePinTable(
                tries_left=tries_left,
            )

    def onUnbind(self):
        self.displayReadyUnplugged()
        self._can_generate = False
        super().onUnbind()

    def onEnable(self):
        self._can_generate = True
        self.__next_pin_generation = 0
        super().onEnable()

    def onDisable(self):
        self.displayReadyUnplugged()
        self._can_generate = False
        super().onDisable()

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
        WaveShareEPaper() as display,
        GPIOChip('/dev/gpiochip0', 'w+b') as gpiochip,
        GadgetSubprocessManager(
            args=args,
            config_list=[
                # A single configuration
                {
                    'function_list': [
                        functools.partial(
                            ConfigFunctionFFSSubprocess,
                            getFunction=functools.partial(
                                ICCDFunctionWithRandomPinDisplay,
                                display=display,
                                # TODO: argument, auto-detection of default...
                                fontface=freetype.Face('/usr/share/fonts/truetype/noto/NotoMono-Regular.ttf'),
                                slot_count=1,
                                zodb_path=os.path.abspath(args.filestorage),
                                gpiochip=gpiochip,
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
