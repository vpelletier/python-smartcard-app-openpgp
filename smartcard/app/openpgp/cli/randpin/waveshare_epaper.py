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

"""
Python userland driver for WaveShare e-Paper 2.13 inches display, version 1
( !! v2 is incompatible !! ), for use with the Raspberry PI (SPI bus and GPIO
pins hard-coded).

https://www.waveshare.com/wiki/2.13inch_e-Paper_HAT
"""
import fcntl
import os
import select
import time
from gpiochip2 import (
    GPIO_V2_LINE_FLAG,
    GPIOChip,
)

class WaveShareEPaper:
    """
    Driver for WaveShare e-Paper v1
    Which is based on the IL3895/SSD1780 driver chip.
    XXX: Assumes RaspberryPi GPIOs pinout, requires spi0 enabled
    """
    _reset_file = None
    _busy_file = None
    _dc_file = None
    _spi_file = None
    _gpio_list = (
        (
            17,
            True,
            GPIO_V2_LINE_FLAG.ACTIVE_LOW | GPIO_V2_LINE_FLAG.OUTPUT,
            '_reset_file',
        ),
        (
            24,
            False,
            (
                GPIO_V2_LINE_FLAG.EDGE_FALLING |
                GPIO_V2_LINE_FLAG.EDGE_RISING |
                GPIO_V2_LINE_FLAG.INPUT |
                GPIO_V2_LINE_FLAG.BIAS_PULL_UP
            ),
            '_busy_file',
        ),
        (
            25,
            False,
            GPIO_V2_LINE_FLAG.OUTPUT,
            '_dc_file',
        ),
    )
    _width = 122
    _height = 250
    _line_length, remainder = divmod(_width, 8)
    if remainder:
        _line_length += 1
    del remainder
    _update_count = None
    _window_ymin = None
    _window_ymax = None
    _window_xmin = None
    _window_xmax = None
    _DRIVER_OUTPUT_CONTROL = b'\x01'
    _SLEEP = b'\x10'
    _SLEEP_WAKE = b'\x00' # Reset seems require to wake from sleep
    _SLEEP_SLEEP = b'\x01'
    _DATA_ENTRY_MODE_SETTING = b'\x11'
    _DATA_ENTRY_MODE_SETTING_X_INCREMENT = 0x01
    _DATA_ENTRY_MODE_SETTING_Y_INCREMENT = 0x02
    _DATA_ENTRY_MODE_SETTING_COLUMN_MODE = 0x04
    _WRITE_RAM = b'\x24'
    _WRITE_VCOM_REGISTER = b'\x2c'
    _WRITE_VCOM_VOLTS_TO_VALUE = lambda self, volts: int( # +0.1V..-5V
        volts / -0.02 + 5
    ).to_bytes(1, 'little')
    _WRITE_LUT_REGISTER = b'\x32'
    # LUT format:
    # - entries 0x00..0x09: waveform levels for each bit transition combination
    #   Packing order: 0bll_lh_hl_hh
    #   Values for each level:
    #   - 00 = VSS (== eliminate charges)
    #   - 01 = VSH (== drive high)
    #   - 10 = VSL (== drive low)
    # - entries 0x0a..0x0f: constant 0x00
    # - entries 0x10..0x19: waveform level hold durations and repetitions
    #   Packing order: 0bRRRR_ddddd 0brrr_ddddd
    #   Repeats: 0bRRRrrr + 1 times
    #   Duration: ddddd clock cycles (0 = phase skipped)
    # - entries 0x1a..0x1c: constant 0x00
    # - value set by command 0x3a (DUMMY_LINE_PERIOD, default: 0x06)
    # - value set by command 0x04 (source driving voltage control, default: 0x19)
    # - value set by command 0x3b (gate line width, default: 0x0b)
    _LUT_FULL_UPDATE = (
        b'\x22\x55\xAA\x55\xAA\x55\xAA\x11'
        b'\x00\x00\x00\x00\x00\x00\x00\x00'
        b'\x1E\x1E\x1E\x1E\x1E\x1E\x1E\x1E'
        b'\x01\x00\x00\x00\x00\x00'
    )
    _LUT_PARTIAL_UPDATE = (
        b'\x18\x00\x00\x00\x00\x00\x00\x00'
        b'\x00\x00\x00\x00\x00\x00\x00\x00'
        b'\x0F\x01\x00\x00\x00\x00\x00\x00'
        b'\x00\x00\x00\x00\x00\x00'
    )
    _LUT_CUSTOM = bytes((
        0b01_10_01_10,
        0b10_01_10_01,
        0x00,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,

        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, # constant

        0b000_00111,
        0b000_01111,
        0b000_00001,
        0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,

        0x00, 0x00, 0x00, # constant

        0x06,
        0x19,
        0x0b,
    ))
    assert len(_LUT_CUSTOM) == 32, len(_LUT_CUSTOM)
    _SET_DUMMY_LINE_PERIOD = b'\x3a'
    _SET_GATE_TIME = b'\x3b'
    _BORDER_WAVEFORM_CONTROL = b'\x3c'
    # Pick *one* of the following constants
    _BORDER_WAVEFORM_CONTROL_FOLLOW_SOURCE =       b'\x80'
    _BORDER_WAVEFORM_CONTROL_FIX_LEVEL_HIZ =       b'\x70'
    _BORDER_WAVEFORM_CONTROL_FIX_LEVEL_VSL =       b'\x60' # border: grey
    _BORDER_WAVEFORM_CONTROL_FIX_LEVEL_VSH =       b'\x50' # border: black
    _BORDER_WAVEFORM_CONTROL_FIX_LEVEL_VSS =       b'\x40'
    _BORDER_WAVEFORM_CONTROL_VAR_LEVEL_GSC1_GSD1 = b'\x03' # XXX: guess
    _BORDER_WAVEFORM_CONTROL_VAR_LEVEL_GSC1_GSD0 = b'\x02' # XXX: guess
    _BORDER_WAVEFORM_CONTROL_VAR_LEVEL_GSC0_GSD1 = b'\x01'
    _BORDER_WAVEFORM_CONTROL_VAR_LEVEL_GSC0_GSD0 = b'\x00' # XXX: guess
    _MASTER_ACTIVATION = b'\x20'
    _DISPLAY_UPDATE_CONTROL_2 = b'\x22'
    _DISPLAY_UPDATE_CONTROL_2_STAGE_ENABLE_CLOCK    = 0x80 # XXX: guess
    _DISPLAY_UPDATE_CONTROL_2_STAGE_ENABLE_ANALOG   = 0x40 # XXX: guess
    _DISPLAY_UPDATE_CONTROL_2_STAGE_UNK1            = 0x20 # XXX: guess
    _DISPLAY_UPDATE_CONTROL_2_STAGE_LOAD_LUT        = 0x10 # XXX: guess
    _DISPLAY_UPDATE_CONTROL_2_STAGE_INIT_DISPLAY    = 0x08 # XXX: guess
    _DISPLAY_UPDATE_CONTROL_2_STAGE_PATTERN_DISPLAY = 0x04 # XXX: guess
    _DISPLAY_UPDATE_CONTROL_2_STAGE_DISABLE_ANALOG  = 0x02 # XXX: guess
    _DISPLAY_UPDATE_CONTROL_2_STAGE_DISABLE_CLOCK   = 0x01 # XXX: guess
    _SET_RAM_X_WINDOW = b'\x44'
    _SET_RAM_Y_WINDOW = b'\x45'
    _SET_RAM_X_COUNTER = b'\x4e'
    _SET_RAM_Y_COUNTER = b'\x4f'

    @property
    def width(self):
        return self._width

    @property
    def bytewidth(self):
        return self._line_length

    @property
    def height(self):
        return self._height

    def __init__(self):
        self.__poll = select.poll()

    def __enter__(self):
        try:
            prefix = self.__class__.__name__ + '.'
            self._spi_file = open('/dev/spidev0.0', 'r+b', buffering=0)
            with GPIOChip('/dev/gpiochip0', 'r+b') as gpio_chip:
                for line, default_value, flags, file_attr in self._gpio_list:
                    setattr(self, file_attr, gpio_chip.openLines(
                        line_list=[line],
                        flags=flags,
                        consumer=(prefix + file_attr).encode('ascii'),
                        default_dict={0: default_value},
                    ))
            fcntl.fcntl(
                self._busy_file,
                fcntl.F_SETFL,
                fcntl.fcntl(self._busy_file, fcntl.F_GETFL) | os.O_NONBLOCK,
            )
            self.__poll.register(
                self._busy_file,
                select.POLLIN | select.POLLPRI,
            )
            # Reset
            self._reset_file |= 1
            self.wait(state=1)
            self._reset_file &= 0
            self.wait()
            # Init
            y_max = self._height - 1
            self._command(
                self._DRIVER_OUTPUT_CONTROL,
                # 250 gates, scanned in order 0, 1, 2, ... 249
                y_max.to_bytes(1, 'little') + b'\x00',
            )
            self._command(
                self._BORDER_WAVEFORM_CONTROL,
                self._BORDER_WAVEFORM_CONTROL_FIX_LEVEL_VSH,
            )
            self._command(
                self._DATA_ENTRY_MODE_SETTING,
                (
                    self._DATA_ENTRY_MODE_SETTING_X_INCREMENT
                ).to_bytes(1, 'little'),
            )
            self.setWindow(0, 0, self._width - 1, y_max)
            self._command(self._WRITE_LUT_REGISTER, self._LUT_CUSTOM)
        except:
            self.__unenter()
            raise
        return self

    def __exit__(self, exc_type, exc_value, tb):
        self._command(self._SLEEP, self._SLEEP_SLEEP)
        self.__unenter()

    def __unenter(self):
        if self._spi_file is not None:
            self._spi_file.close()
            self._spi_file = None
        self.__poll.unregister(self._busy_file)
        for _, _, _, file_attr in self._gpio_list:
            gpio_file = getattr(self, file_attr)
            if gpio_file is not None:
                gpio_file.close()
                setattr(self, file_attr, None)

    def _command(self, command, data=b''):
        self._dc_file &= 0
        self._spi_file.write(command)
        if data:
            self._dc_file |= 1
            self._spi_file.write(data)

    def wait(self, state=0, timeout=10):
        self._busy_file.read() # discard result
        if timeout is not None:
            deadline = time.time() + timeout
        while self._busy_file.value != state and (
            timeout is None or
            time.time() < deadline
        ):
            try:
                event_list = self.__poll.poll(
                    None
                    if timeout is None else
                    (deadline - time.time()) * 1000
                )
            except IOError as exc:
                if exc.errno != EINTR:
                    raise
            if not event_list:
                raise ValueError('Display is stuck')
            self._busy_file.read() # discard result


    def swap(self, wait=True):
        self._command(
            self._DISPLAY_UPDATE_CONTROL_2,
            (
                self._DISPLAY_UPDATE_CONTROL_2_STAGE_ENABLE_CLOCK |
                self._DISPLAY_UPDATE_CONTROL_2_STAGE_ENABLE_ANALOG |
                self._DISPLAY_UPDATE_CONTROL_2_STAGE_PATTERN_DISPLAY |
                self._DISPLAY_UPDATE_CONTROL_2_STAGE_DISABLE_ANALOG
                # seems to require a reset to recover from
                # self._DISPLAY_UPDATE_CONTROL_2_STAGE_DISABLE_CLOCK
            ).to_bytes(1, 'little'), # XXX: value 0xd4 not in spec
        )
        self._command(self._MASTER_ACTIVATION)
        if wait:
            self.wait()

    def setWindow(self, x_start, y_start, x_end, y_end):
        self.wait()
        self._window_ymin = y_start
        self._window_ymax = y_end
        self._window_xmin = x_start
        self._window_xmax = x_end
        self._command(self._SET_RAM_X_WINDOW, (x_start >> 3).to_bytes(1, 'little') + (x_end >> 3).to_bytes(1, 'little'))
        self._command(self._SET_RAM_Y_WINDOW, y_start.to_bytes(2, 'little') + y_end.to_bytes(2, 'little'))

    def setCursor(self, x, y):
        self.wait()
        self._command(self._SET_RAM_X_COUNTER, (x >> 3).to_bytes(1, 'little'))
        self._command(self._SET_RAM_Y_COUNTER, y.to_bytes(2, 'little'))

    def blit(self, image, x, y):
        self.wait()
        if y > (self._window_ymax - self._window_ymin):
            raise ValueError('out of window')
        self.setCursor(x, self._window_ymax - y)
        self._command(self._WRITE_RAM, image)

    def clear(self):
        self.wait()
        line_data = b'\xff' * (self._window_xmax - self._window_xmin + 1)
        self.setCursor(0, self._window_ymax)
        for _ in range(self._window_ymax):
            self._command(self._WRITE_RAM, line_data)
        self.swap()
