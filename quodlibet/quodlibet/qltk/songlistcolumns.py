# Copyright 2005 Joe Wreschnig
#           2012 Christoph Reiter
#      2011-2013 Nick Boultbee
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation

import time
import datetime

from gi.repository import Gtk, Pango

from quodlibet import util
from quodlibet import config
from quodlibet import const
from quodlibet.parse import Pattern
from quodlibet.qltk.views import TreeViewColumnButton
from quodlibet.util.path import fsdecode, unexpand
from quodlibet.formats._audio import FILESYSTEM_TAGS


def create_songlist_column(t):
    """Returns a SongListColumn instance for the given tag"""

    if t in ["tracknumber", "discnumber", "language"]:
        return SimpleTextColumn(t)
    elif t in ["~#added", "~#mtime", "~#lastplayed", "~#laststarted"]:
        return DateColumn(t)
    elif t in ["~length", "~#length"]:
        return LengthColumn()
    elif t == "~#filesize":
        return FilesizeColumn()
    elif t in ["~rating", "~#rating"]:
        return RatingColumn()
    elif t.startswith("~#"):
        return NumericColumn(t)
    elif t in FILESYSTEM_TAGS:
        return FSColumn(t)
    elif t.startswith("<"):
        return PatternColumn(t)
    elif "~" not in t and t != "title":
        return NonSynthTextColumn(t)
    else:
        return WideTextColumn(t)


class SongListColumn(TreeViewColumnButton):

    __last_rendered = None

    def __init__(self, tag):
        """tag e.g. 'artist'"""

        title = self._format_title(tag)
        super(SongListColumn, self).__init__(title)
        self.header_name = tag

        self.set_sizing(Gtk.TreeViewColumnSizing.FIXED)
        self.set_visible(True)
        self.set_sort_indicator(False)

    def _format_title(self, tag):
        """Format the column title based on the tag"""

        return util.tag(tag)

    def set_width(self, width):
        """Set the initial width.

        If None is passed the column will try to set a reasonable default
        """

        pass

    def _needs_update(self, value):
        """Call to check if the last passed value was the same.

        This is used to reduce formating if the input is the same
        either because of redraws or all columns have the same value
        """

        if self.__last_rendered == value:
            return False
        self.__last_rendered = value
        return True


class TextColumn(SongListColumn):
    """Base text column"""

    __label = Gtk.Label().create_pango_layout("")

    def __init__(self, tag):
        super(TextColumn, self).__init__(tag)

        self._render = Gtk.CellRendererText()
        self.pack_start(self._render, True)
        self.set_cell_data_func(self._render, self._cdf)

        self.set_resizable(True)
        self.set_clickable(True)
        self.set_min_width(self._cell_width("000"))

    def _cell_width(self, text, pad=12):
        """Returns the column width needed for the passed text"""

        cell_pad = self._render.get_property('xpad')
        self.__label.set_text(text, -1)
        return self.__label.get_pixel_size()[0] + pad + cell_pad

    def set_width(self, width):
        if width is not None:
            self.set_fixed_width(width)

    def _cdf(self, column, cell, model, iter_, user_data):
        """CellRenderer cell_data_func"""

        raise NotImplementedError


class SimpleTextColumn(TextColumn):

    def _cdf(self, column, cell, model, iter_, user_data):
        text = model.get_value(iter_).comma(self.header_name)
        if not self._needs_update(text):
            return
        cell.set_property('text', text)


class DateColumn(TextColumn):
    """The '~#' keys that are dates."""

    def set_width(self, width):
        if width is None:
            today = datetime.datetime.now().date()
            text = today.strftime('%x').decode(const.ENCODING)
            width = self._cell_width(text)

        self.set_fixed_width(width)

    def _cdf(self, column, cell, model, iter_, user_data):
        stamp = model.get_value(iter_)(self.header_name)
        if not self._needs_update(stamp):
            return

        if not stamp:
            cell.set_property('text', _("Never"))
        else:
            date = datetime.datetime.fromtimestamp(stamp).date()
            today = datetime.datetime.now().date()
            days = (today - date).days
            if days == 0:
                format_ = "%X"
            elif days < 7:
                format_ = "%A"
            else:
                format_ = "%x"
            stamp = time.localtime(stamp)
            text = time.strftime(format_, stamp).decode(const.ENCODING)
            cell.set_property('text', text)


class RatingColumn(TextColumn):
    """Render ~#rating directly

    (simplifies filtering, saves a function call).
    """

    def __init__(self, *args, **kwargs):
        super(RatingColumn, self).__init__("~#rating", *args, **kwargs)
        width = self._cell_width(util.format_rating(1.0))
        self.set_min_width(width)

    def set_width(self, width):
        pass

    def _cdf(self, column, cell, model, iter_, user_data):
        value = model.get_value(iter_).get(
            "~#rating", config.RATINGS.default)
        if not self._needs_update(value):
            return
        cell.set_property('text', util.format_rating(value))


class WideTextColumn(SimpleTextColumn):
    """Resizable and ellipsized at the end. Used for any key with
    a '~' in it, and 'title'.
    """

    def __init__(self, *args, **kwargs):
        super(WideTextColumn, self).__init__(*args, **kwargs)
        self._render.set_property('ellipsize', Pango.EllipsizeMode.END)


class NonSynthTextColumn(WideTextColumn):
    """Optimize for non-synthesized keys by grabbing them directly.
    Used for any tag without a '~' except 'title'.
    """

    def _cdf(self, column, cell, model, iter_, user_data):
        value = model.get_value(iter_).get(self.header_name, "")
        if not self._needs_update(value):
            return
        cell.set_property('text', value.replace("\n", ", "))


class FSColumn(WideTextColumn):
    """Contains text in the filesystem encoding, so needs to be
    decoded safely (and also more slowly).
    """

    def _cdf(self, column, cell, model, iter_, user_data):
        value = model.get_value(iter_).comma(self.header_name)
        if not self._needs_update(value):
            return
        cell.set_property('text', unexpand(fsdecode(value)))


class PatternColumn(WideTextColumn):

    def __init__(self, *args, **kwargs):
        super(PatternColumn, self).__init__(*args, **kwargs)

        try:
            self._pattern = Pattern(self.header_name)
        except ValueError:
            self._pattern = None

    def _format_title(self, tag):
        return util.pattern(tag)

    def _cdf(self, column, cell, model, iter_, user_data):
        song = model.get_value(iter_)
        if not self._pattern:
            return
        value = self._pattern % song
        if not self._needs_update(value):
            return
        cell.set_property('text', value)


class NumericColumn(TextColumn):
    """Any '~#' keys except dates."""

    def __init__(self, *args, **kwargs):
        super(NumericColumn, self).__init__(*args, **kwargs)
        self._render.set_property('xalign', 1.0)
        self.set_alignment(1.0)

    def set_width(self, width):
        if width is None:
            width = self._cell_width('9999')
        self.set_fixed_width(width)

    def _cdf(self, column, cell, model, iter_, user_data):
        value = model.get_value(iter_).comma(self.header_name)
        if not self._needs_update(value):
            return
        text = unicode(value)
        cell.set_property('text', text)


class LengthColumn(NumericColumn):

    def __init__(self):
        super(LengthColumn, self).__init__("~#length")

    def _cdf(self, column, cell, model, iter_, user_data):
        value = model.get_value(iter_).get("~#length", 0)
        if not self._needs_update(value):
            return
        text = util.format_time(value)
        cell.set_property('text', text)


class FilesizeColumn(NumericColumn):

    def __init__(self):
        super(FilesizeColumn, self).__init__("~#filesize")

    def _cdf(self, column, cell, model, iter_, user_data):
        value = model.get_value(iter_).get("~#filesize", 0)
        if not self._needs_update(value):
            return
        text = util.format_size(value)
        cell.set_property('text', text)