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

# TODO: get rid of this. There has to be a nicer framebuffer implementation
# somewhere. Maybe PIL (aka pillow) ?

import itertools
import freetype

class Framebuffer:
    COLOR_OFF = 0
    COLOR_ON = 1
    COLOR_XOR = -1

    def __init__(self, width, height):
        self._width = width
        self._height = height >> 3
        if height & 0x7:
            self._height += 1
        self._pixel_height = height
        self._buf = bytearray(width * self._height)
        self._buf_view = memoryview(self._buf)
        self._dirty_x_min = 0
        self._dirty_x_max = width
        self._dirty_y_min = 0
        self._dirty_y_max = height

    @property
    def width(self):
        return self._width

    @property
    def height(self):
        return self._pixel_height

    @property
    def pixelbuffer(self):
        return self._buf

    def _iterBuf(self, x, y, width, height):
        buf_view = self._buf_view
        offset = y * self._width + x
        for _ in range(height):
            yield buf_view[offset:offset + width]
            offset += self._width

    def getDirtyRect(self):
        width = self._width
        height = self._height
        x_min = max(0, self._dirty_x_min)
        x_max = min(width, self._dirty_x_max) + 1
        dirty_width = x_max - x_min
        if dirty_width <= 0:
            return (0, 0, 0, 0, b'')
        y_min = max(0, self._dirty_y_min)
        y_max = min(height, self._dirty_y_max) + 1
        dirty_height = y_max - y_min
        if dirty_width == width:
            if dirty_height == height:
                view = self._buf_view
            else:
                view = self._buf_view[y_min * width:y_max * width]
        else:
            view = itertools.chain.from_iterable(
                self._iterBuf(x_min, y_min, dirty_width, dirty_height)
            )
        self._dirty_x_min = width
        self._dirty_y_min = height
        self._dirty_x_max = self._dirty_y_max = 0
        # Note: xmax and ymax are *after* the last line/column
        return (x_min, x_max, y_min, y_max, view)

    def _dirty(self, x, y):
        self._dirty_x_min = min(x, self._dirty_x_min)
        self._dirty_x_max = max(x, self._dirty_x_max)
        self._dirty_y_min = min(y, self._dirty_y_min)
        self._dirty_y_max = max(y, self._dirty_y_max)

    @classmethod
    def _maskWord(cls, buf, offset, word, color):
        assert offset >= 0
        if color == cls.COLOR_ON:
            buf[offset] |= word
        elif color == cls.COLOR_XOR:
            buf[offset] ^= word
        elif color == cls.COLOR_OFF:
            buf[offset] &= word ^ 0xff
        else:
            raise ValueError

    def _putPixel(self, x, y, color):
        self._maskWord(
            self._buf,
            (y >> 3) + x * self._height,
            1 << (7 - (y & 0x7)),
            color,
        )

    def putPixel(self, x, y, color=COLOR_ON):
        if 0 <= x < self._width and 0 <= y < self._pixel_height:
            self._dirty(x, y >> 3)
            self._putPixel(x, y, color)

    def getPixel(self, x, y):
        width = self._width
        if 0 <= x < width and 0 <= y < self._pixel_height:
            return (self._buf[(y >> 3) * width + x + 1] >> (y & 0x7)) & 1
        return None

    def blank(self, color=COLOR_OFF):
        if color not in (self.COLOR_OFF, self.COLOR_ON):
            raise ValueError
        self._dirty(0, 0)
        self._dirty(self._width, self._height)
        if color:
            color = 0xff
        buf = self._buf
        for index in range(self._width * self._height):
            buf[index] = color

    def line(self, ax, ay, bx, by, color=COLOR_ON):
        # TODO: detect all-out-of-screen lines
        self._dirty(ax, ay >> 3)
        self._dirty(bx, by >> 3)
        self._line(ax, ay, bx, by, color)

    def _line(self, ax, ay, bx, by, color):
        height = self._height
        delta_x = bx - ax
        delta_y = by - ay
        if delta_x == delta_y == 0:
            self._putPixel(ax, ay, color)
            return
        inc_x = height if delta_x > 0 else -height
        if delta_y < 0:
            delta_y = -delta_y
            ax = bx
            ay = by
            inc_x = -inc_x
        # XXX: should find the intersections between the line and buffer borders
        # to remove tests on every iteration.
        abs_delta_x = abs(delta_x)
        abs_delta_y = delta_y
        bit = 7 - (ay & 0x7)
        offset = (ay >> 3) + ax * height
        buf = self._buf
        maskWord = self._maskWord
        err = 0
        if abs_delta_y >= abs_delta_x:
            err_delta = abs_delta_x / abs_delta_y
            word = 0
            offset_inc = 0
            for _ in range(abs_delta_y + 1):
                word |= 1 << bit
                bit -= 1
                if bit == -1:
                    offset_inc = 1
                    bit = 7
                err += err_delta
                if err > 0.5:
                    ax += inc_x
                    offset_inc += inc_x
                    err -= 1
                if offset_inc:
                    maskWord(buf, offset, word, color)
                    offset += offset_inc
                    offset_inc = 0
                    word = 0
            if word:
                maskWord(buf, offset, word, color)
        else:
            err_delta = abs_delta_y / abs_delta_x
            for _ in range(abs_delta_x + 1):
                maskWord(buf, offset, 1 << bit, color)
                ax += inc_x
                offset += inc_x
                err += err_delta
                if err > 0.5:
                    bit -= 1
                    if bit == -1:
                        offset += 1
                        bit = 7
                    err -= 1

    def rect(self, ax, ay, bx, by, color=COLOR_ON, fill=False):
        width = self._width - 1
        height = self._pixel_height - 1
        ax = min(width, max(0, ax))
        bx = min(width, max(0, bx))
        ay = min(height, max(0, ay))
        by = min(height, max(0, by))
        self._dirty(ax, ay)
        self._dirty(bx, by)
        line = self._line
        if fill:
            delta_x = bx - ax
            delta_y = by - ay
            abs_delta_x = abs(delta_x)
            abs_delta_y = abs(delta_y)
            # Note: could be accelerated (after line is) by favoring y lines
            if abs_delta_y >= abs_delta_x:
                inc = 1 if delta_x > 0 else -1
                for _ in range(abs_delta_x + 1):
                    line(ax, ay, ax, by, color)
                    ax += inc
            else:
                inc = 1 if delta_y > 0 else -1
                for _ in range(abs_delta_y + 1):
                    line(ax, ay, bx, ay, color)
                    ay += inc
        else:
            line(ax, ay, bx - 1, ay, color)
            line(bx, ay, bx, by - 1, color)
            line(bx, by, ax + 1, by, color)
            line(ax, by, ax, ay + 1, color)

    def circle(self, x, y, r, color=COLOR_ON, fill=False):
        # TODO: skip off-screen rendering
        self._dirty(x - r, y - r)
        self._dirty(x + r, y + r)
        deltax = r
        deltay = 0
        err = 0
        if fill:
            line = self._line
            def draw(bx, by, color=color):
                line(bx, y, bx, by, color)
        else:
            putPixel = self._putPixel
            def draw(bx, by, color=color):
                putPixel(bx, by, color)
        while deltax >= deltay:
            draw(x + deltax, y + deltay)
            draw(x + deltay, y + deltax)
            draw(x - deltay, y + deltax)
            draw(x - deltax, y + deltay)
            draw(x - deltax, y - deltay)
            draw(x - deltay, y - deltax)
            draw(x + deltay, y - deltax)
            draw(x + deltax, y - deltay)
            if err <= 0:
                deltay += 1
                err += 2 * deltay + 1
            if err > 0:
                deltax -= 1
                err -= 2 * deltax + 1

    def blitRowImage(self, x, y, width, data, color=COLOR_ON, packed=False, big_endian=False):
        """
        x:
            Left image border screen coordinate.
        y:
            Top image border screen coordinate.
        width:
            Image width, in pixels.
        data:
            Image data. First byte contains the leftmost 8 pixels of the
            topmost row, then the 8 following pixel of the topmost row,
            and so on until given width.
        packed:
            When False and image right border is not on a byte boundary,
            discard remaining bits, next byte starts the next line.
        big_endian:
            When True image data MSbs are leftmost pixels.
        """
        self._dirty(x, y)
        putPixel = self._putPixel
        bit_shift = list(range(8))
        if big_endian:
            bit_shift.reverse()
        dx = dy = 0
        for word in data:
            for bit in bit_shift:
                if (word >> bit) & 1:
                    putPixel(x + dx, y + dy, color)
                dx += 1
                if dx == width:
                    dx = 0
                    dy += 1
                    if not packed:
                        break
        self._dirty(x + width, y + dy)

    def blitColumnImage(self, x, y, height, data, color=COLOR_ON, packed=False, big_endian=False):
        """
        x:
            Left image border screen coordinate.
        y:
            Top image border screen coordinate.
        height:
            Image height, in pixels.
        data:
            Image data. First byte contains the topmost 8 pixels of the
            leftmost column, then the 8 following pixel of the leftmost column,
            and so on until given height.
        packed:
            When False and image right border is not on a byte boundary,
            discard remaining bits, next byte starts the next column.
        big_endian:
            When True image data MSbs are topmost pixels.
        """
        self._dirty(x, y)
        putPixel = self._putPixel
        bit_shift = list(range(8))
        if big_endian:
            bit_shift.reverse()
        dx = dy = 0
        for word in data:
            for bit in bit_shift:
                if (word >> bit) & 1:
                    putPixel(x + dx, y + dy, color)
                dy += 1
                if dy == height:
                    dy = 0
                    dx += 1
                    if not packed:
                        break
        self._dirty(x + dx, y + height)

    @staticmethod
    def _repackAndScissor(bitmap, crop_top=0, crop_bottom=0):
        # freetype bitmaps may contain all-empty bytes at end of line, which
        # are not handled by blitting, so repack when needed.
        # Also, height not being guaranteed, crop top & bottom to not escape
        # intended rendering rect.
        pitch = (7 + bitmap.width) // 8
        if pitch == bitmap.pitch:
            result = list(bitmap.buffer)
        else:
            result = []
            x = 0
            for data in bitmap.buffer:
                if x < pitch:
                    result.append(data)
                x += 1
                x %= bitmap.pitch
        if crop_top:
            result = result[crop_top * pitch:]
        if crop_bottom:
            result = result[:-crop_bottom * pitch]
        return result

    def printLineAt(self, face, x, y, text, width=None, height=12, color=COLOR_ON):
        """
        Render <text> as a single line, with top-left corner at (<x>, <y>) and
        bottom-right corner at most at (<x> + <width>, <y> + <height>),
        in <color> and using font <face> (a freetype.Face instance).
        Returns the number of chars which could fit.
        Stops printing when encountering a \n (newline) char.
        """
        if width is None:
            width = self._width - x
        baseline = int(height * 4 / 5)
        blitRowImage = self.blitRowImage
        face.set_pixel_sizes(0, height - 2)
        previous_char = None
        rendered = 0
        for char in text:
            if char == u'\n':
                rendered += 1
                break
            face.load_char(
                char,
                freetype.FT_LOAD_RENDER |
                freetype.FT_LOAD_MONOCHROME |
                freetype.FT_LOAD_TARGET_MONO,
            )
            glyph = face.glyph
            bitmap = glyph.bitmap
            char_width = max(
                glyph.advance.x // 64,
                glyph.bitmap_left + bitmap.width,
            ) + face.get_kerning(previous_char, char).x // 64
            width -= char_width
            if width < 0:
                if not rendered:
                    raise ValueError('Too narrow for first char')
                break
            y_offset = baseline - glyph.bitmap_top
            crop_top = -min(0, y_offset)
            y_offset = max(0, y_offset)
            blitRowImage(
                x + glyph.bitmap_left,
                y + y_offset,
                bitmap.width,
                self._repackAndScissor(
                    bitmap,
                    crop_top,
                    max(0, (y_offset + bitmap.rows) - height),
                ),
                color=color,
                big_endian=True,
            )
            x += char_width
            previous_char = char
            rendered += 1
        return rendered

    def printAt(self, face, x, y, text, width=None, height=None, line_height=12, color=COLOR_ON):
        """
        Render multi-line <text>, wrapping on \\n and when next char would not
        fit, with top-left corner at (<x>, <y>) and bottom-right corner at most
        at (<x> + <width>, <y> + <height>), in <color> and using font <face>
        (a freetype.Face instance), each line having a height of <line_height>.
        Returns the number of chars which could fit.
        Stop printing before reaching screen bottom.
        """
        if height is None:
            height = self._pixel_height - y
        total_printed = 0
        while text:
            y_overflow = y + line_height - height
            if y_overflow > 0:
                break
            printed = self.printLineAt(face, x, y, text, width, line_height, color)
            total_printed += printed
            text = text[printed:]
            y += line_height
        return total_printed
