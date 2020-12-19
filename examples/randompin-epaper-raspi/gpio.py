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
A pure-pyton gpio implemtation which does not suck.

/sys/class/gpio/* are for shell scripts and hacks.
Real programs should use real APIs. This uses the proper linux GPIO ioctls.

It does not suck, but it is not complete (also, there is a better ioctl API
comming up in next kernel release...).
"""

import ctypes
import enum
from fcntl import ioctl
import io
from ioctl_opt import IOR, IOWR

class gpiochip_info(ctypes.Structure):
    _fields_ = (
        ('name', ctypes.c_char * 32),
        ('label', ctypes.c_char * 32),
        ('lines', ctypes.c_uint32),
    )

GPIOLINE_FLAG_KERNEL         = 1 << 0
GPIOLINE_FLAG_IS_OUT         = 1 << 1
GPIOLINE_FLAG_ACTIVE_LOW     = 1 << 2
GPIOLINE_FLAG_OPEN_DRAIN     = 1 << 3
GPIOLINE_FLAG_OPEN_SOURCE    = 1 << 4
GPIOLINE_FLAG_BIAS_PULL_UP   = 1 << 5
GPIOLINE_FLAG_BIAS_PULL_DOWN = 1 << 6
GPIOLINE_FLAG_BIAS_DISABLE   = 1 << 7

class gpioline_info(ctypes.Structure):
    _fields_ = (
        ('line_offset', ctypes.c_uint32),
        ('flags', ctypes.c_uint32),
        ('name', ctypes.c_char * 32),
        ('consumer', ctypes.c_char * 32),
    )

GPIOHANDLES_MAX = 64

@enum.unique
class GPIOLINE_CHANGED(enum.IntEnum):
    REQUESTED = 1
    RELEASED = enum.auto()
    CONFIG = enum.auto()

GPIOLINE_CHANGED_REQUESTED = GPIOLINE_CHANGED.REQUESTED
GPIOLINE_CHANGED_RELEASED  = GPIOLINE_CHANGED.RELEASED
GPIOLINE_CHANGED_CONFIG    = GPIOLINE_CHANGED.CONFIG

class gpioline_info_changed(ctypes.Structure):
    _fields_ = (
        ('info', gpioline_info),
        ('timestamp', ctypes.c_uint64),
        ('event_type', ctypes.c_uint32),
        ('padding', ctypes.c_uint32 * 5),
    )

GPIOHANDLE_REQUEST_INPUT          = 1 << 0
GPIOHANDLE_REQUEST_OUTPUT         = 1 << 1
GPIOHANDLE_REQUEST_ACTIVE_LOW     = 1 << 2
GPIOHANDLE_REQUEST_OPEN_DRAIN     = 1 << 3
GPIOHANDLE_REQUEST_OPEN_SOURCE    = 1 << 4
GPIOHANDLE_REQUEST_BIAS_PULL_UP   = 1 << 5
GPIOHANDLE_REQUEST_BIAS_PULL_DOWN = 1 << 6
GPIOHANDLE_REQUEST_BIAS_DISABLE   = 1 << 7

class gpiohandle_request(ctypes.Structure):
    _fields_ = (
        ('lineoffsets', ctypes.c_uint32 * GPIOHANDLES_MAX),
        ('flags', ctypes.c_uint32),
        ('default_values', ctypes.c_uint8 * GPIOHANDLES_MAX),
        ('consumer_label', ctypes.c_char * 32),
        ('lines', ctypes.c_uint32),
        ('fd', ctypes.c_int),
    )

class gpiohandle_config(ctypes.Structure):
    _fields_ = (
        ('flags', ctypes.c_uint32),
        ('default_values', ctypes.c_uint8 * GPIOHANDLES_MAX),
        ('padding', ctypes.c_uint32 * 4),
    )

GPIOHANDLE_SET_CONFIG_IOCTL = IOWR(0xB4, 0x0a, gpiohandle_config)

class gpiohandle_data(ctypes.Structure):
    _fields_ = (
        ('values', ctypes.c_uint8 * GPIOHANDLES_MAX),
    )

GPIOHANDLE_GET_LINE_VALUES_IOCTL = IOWR(0xB4, 0x08, gpiohandle_data)
GPIOHANDLE_SET_LINE_VALUES_IOCTL = IOWR(0xB4, 0x09, gpiohandle_data)

GPIOEVENT_REQUEST_RISING_EDGE   = 1 << 0
GPIOEVENT_REQUEST_FALLING_EDGE  = 1 << 1
GPIOEVENT_REQUEST_BOTH_EDGES    = (1 << 0) | (1 << 1)

class gpioevent_request(ctypes.Structure):
    _fields_ = (
        ('lineoffset', ctypes.c_uint32),
        ('handleflags', ctypes.c_uint32),
        ('eventflags', ctypes.c_uint32),
        ('consumer_label', ctypes.c_char * 32),
        ('fd', ctypes.c_int),
    )

GPIOEVENT_EVENT_RISING_EDGE = 0x01
GPIOEVENT_EVENT_FALLING_EDGE = 0x02

class gpioevent_data(ctypes.Structure):
    _fields_ = (
        ('timestamp', ctypes.c_uint64),
        ('id', ctypes.c_uint32),
    )

GPIO_GET_CHIPINFO_IOCTL = IOR(0xB4, 0x01, gpiochip_info)
GPIO_GET_LINEINFO_IOCTL = IOWR(0xB4, 0x02, gpioline_info)
GPIO_GET_LINEINFO_WATCH_IOCTL = IOWR(0xB4, 0x0b, gpioline_info)
GPIO_GET_LINEINFO_UNWATCH_IOCTL = IOWR(0xB4, 0x0c, ctypes.c_uint32)
GPIO_GET_LINEHANDLE_IOCTL = IOWR(0xB4, 0x03, gpiohandle_request)
GPIO_GET_LINEEVENT_IOCTL = IOWR(0xB4, 0x04, gpioevent_request)

class IOCTLFileIO(io.FileIO):
    def ioctl(self, request, arg=0):
        status = ioctl(self.fileno(), request, arg)
        if status == -1:
            raise OSError

class GPIOPins(IOCTLFileIO):
    """
    Wrapper for GPIO pin set file handles.
    Implements ioctl calls in a pytonic way.
    """
    def __init__(self, *args, **kw):
        """
        Do not call this directly.
        Use GPIOChip.openGPIO to get an instance of this class.
        """
        self._gpio_count = kw.pop('gpio_count')
        super().__init__(*args, **kw)

    def setGPIOMode(self, mode, default_list=()):
        """
        Change the mode of all pins in this pin set.
        """
        if mode & GPIOHANDLE_REQUEST_OUTPUT and (
            default_list is None or
            len(default_list) != self._gpio_count
        ):
            raise ValueError
        self.ioctl(GPIOHANDLE_SET_CONFIG_IOCTL, gpiohandle_config(
            flags=mode,
            default_values=default_list,
        ))

    def write(self, value):
        """
        Set the value for all pins in this pin set.

        value (vector of int)
            Must have one item per pin in this set.
        """
        if len(value) != self._gpio_count:
            raise ValueError
        self.ioctl(GPIOHANDLE_SET_LINE_VALUES_IOCTL, gpiohandle_data(values=tuple(value)))

    def read(self, size=-1):
        """
        Read the current state of all pins in this pin set.

        Returns a list of integers.
        """
        if size != -1:
            raise ValueError
        result = gpiohandle_data()
        self.ioctl(GPIOHANDLE_GET_LINE_VALUES_IOCTL, result)
        return result.values[:self._gpio_count]

class GPIOChip(IOCTLFileIO):
    """
    Wrapper for the /dev/gpiochip* device class.
    Implements ioctl calls in a pythonic way.
    """
    def openGPIO(self, pin_list, mode, consumer, default_list=()):
        """
        Request a list of gpio pins.

        Returns an instance of GPIOPins.

        pin_list (list of int)
            The pin indexes (relative to the current gpio chip) to request
            access to.
        mode (int)
            Bitmask of GPIOHANDLE_REQUEST_* constants.
        consumer (bytes)
            Nice name so a human checking gpio usage can identify what is using
            which pin.
        default_list (list of int)
            For pins requesed for output, the value to set them to.
        """
        gpio_count = len(pin_list)
        if mode & GPIOHANDLE_REQUEST_OUTPUT and (
            default_list is None or
            len(default_list) != gpio_count
        ):
            raise ValueError
        line_request = gpiohandle_request(
            lineoffsets=pin_list,
            lines=gpio_count,
            flags=mode,
            consumer_label=consumer,
            default_values=default_list,
        )
        self.ioctl(GPIO_GET_LINEHANDLE_IOCTL, line_request)
        return GPIOPins(line_request.fd, 'w+b', 0, gpio_count=gpio_count)

    def getChipInfo(self):
        result = gpiochip_info()
        self.ioctl(GPIO_GET_CHIPINFO_IOCTL, result)
        return {
            'name': result.name.value,
            'label': result.label.value,
            'lines': result.lines.value,
        }

    def getGPIOInfo(self, pin):
        result = gpioline_info(
            line_offset=pin,
        )
        self.ioctl(GPIO_GET_LINEINFO_IOCTL, result)
        return {
            'flags': result.flags.value,
            'name': result.name.value,
            'consumer': result.consumer.value,
        }
