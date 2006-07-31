# Copyright 2006 Joe Wreschnig <piman@sacredchao.net>
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.
#
# $Id$

"""Read and write M4A files.

This module will read MPEG-4 audio information and metadata,
as found in Apple's M4A (aka MP4, M4B, M4P) files.

There is no official specification for this format. The source code
for TagLib, FAAD, and various MPEG specifications at
http://developer.apple.com/documentation/QuickTime/QTFF/,
http://www.geocities.com/xhelmboyx/quicktime/formats/mp4-layout.txt,
and http://wiki.multimedia.cx/index.php?title=Apple_QuickTime were all
consulted.

This module does not support 64 bit atom sizes, and so will not
work on files over 4GB.
"""

import struct

from cStringIO import StringIO

from mutagen import FileType, Metadata
from mutagen._constants import GENRES
from mutagen._util import cdata, DictMixin

class error(IOError): pass
class M4AMetadataError(error): pass
class M4AStreamInfoError(error): pass

# This is not an exhaustive list of container atoms, but just the
# ones this module needs to peek inside.
_CONTAINERS = ["moov", "udta", "trak", "mdia", "meta", "ilst"]
_SKIP_SIZE = { "meta": 4 }

class Atom(DictMixin):
    def __init__(self, fileobj):
        self.offset = fileobj.tell()
        self.length, self.name = struct.unpack(">I4s", fileobj.read(8))
        if self.length == 1:
            raise error("64 bit atom sizes are not supported")
        self.children = None

        if self.name in _CONTAINERS:
            self.children = []
            fileobj.seek(_SKIP_SIZE.get(self.name, 0), 1)
            while fileobj.tell() < self.offset + self.length:
                self.children.append(Atom(fileobj))
        else:
            fileobj.seek(self.offset + self.length, 0)

    def __getitem__(self, remaining):
        if not remaining:
            return self
        elif self.children is None:
            raise KeyError("atom is not a container")
        for child in self.children:
            if child.name == remaining[0]:
                return child[remaining[1:]]
        else:
            raise KeyError("unable to resolve %r" % remaining)

    def keys(self):
        if not self.children:
            return []
        else:
            keys = []
            for child in self.children:
                if child.children is None:
                    keys.append((child.name,))
                else:
                    keys.extend(child.keys())
            return map((self.name,).__add__, keys)

    def __repr__(self):
        klass = self.__class__.__name__
        if self.children is None:
            return "<%s name=%r length=%r offset=%r>" % (
                klass, self.name, self.length, self.offset)
        else:
            children = "\n".join([" " + line for child in self.children
                                  for line in repr(child).splitlines()])
            return "<%s name=%r length=%r offset=%r\n%s>" % (
                klass, self.name, self.length, self.offset, children)

class Atoms(DictMixin):
    def __init__(self, fileobj):
        self.atoms = []
        fileobj.seek(0, 2)
        end = fileobj.tell()
        fileobj.seek(0)
        while fileobj.tell() < end:
            self.atoms.append(Atom(fileobj))

    def __getitem__(self, names):
        if isinstance(names, basestring):
            names = names.split(".")
        for child in self.atoms:
            if child.name == names[0]:
                return child[names[1:]]
        else:
            raise KeyError("unable to resolve %r" % names[0])

    def keys(self):
        return sum([atom.keys() for atom in self.atoms], [])

    def __repr__(self):
        return "\n".join(repr(child) for child in self.atoms)

class M4ATags(Metadata):
    def __init__(self, atoms, fileobj):
        for atom in atoms["moov.udta.meta.ilst"].children:
            last = atom.name
            fileobj.seek(atom.offset + 8)
            data = fileobj.read(atom.length - 8)
            self.atoms.get(last, (M4ATags.__parse_text,))[0](self, atom, data)

    def __render(self):
        values = []
        for key, value in self.iteritems():
            render = self.atoms.get(key[:4], (None, M4ATags.__render_text))[1]
            values.append(render(self, key, value))
        return "".join(values)

    def __render_data(self, key, flags, data):
        data = struct.pack(">2I", flags, 0) + data
        data = cdata.to_uint_be(len(data) + 8) + "data" + data
        return cdata.to_uint_be(len(data) + 8) + key + data

    def __parse_freeform(self, atom, data):
        fileobj = StringIO(data)
        mean_length = cdata.uint_be(fileobj.read(4))
        # skip over 8 bytes of atom name, flags
        mean = fileobj.read(mean_length - 4)[8:]
        name_length = cdata.uint_be(fileobj.read(4))
        name = fileobj.read(name_length - 4)[8:]
        value_length = cdata.uint_be(fileobj.read(4))
        # Name, flags, and reserved bytes
        value = fileobj.read(value_length - 4)[12:]
        self["%s:%s:%s" % (atom.name, mean, name)] = value
    def __render_freeform(self, key, value):
        dummy, mean, name = key.split(":", 2)
        mean = struct.pack(">I4sI", len(mean) + 12, "mean", 0) + mean
        name = struct.pack(">I4sI", len(name) + 12, "name", 0) + name
        value = struct.pack(">I4s2I", len(value) + 16, "data", 0x1, 0) + value
        final = mean + name + value
        return struct.pack(">I4s", len(final) + 8, "----") + final

    def __parse_pair(self, atom, data):
        self[atom.name] = struct.unpack(">2H", data[18:22])
    def __render_pair(self, key, value):
        track, total = value
        # Type flag, reserved, empty, track, total, empty.
        data = struct.pack(">4H", 0, 0, 0, track, total, 0)
        return self.__render_data(key, 0, data)

    def __parse_genre(self, atom, data):
        # Translate to a freeform genre.
        genre = cdata.short_be(data[16:18])
        if "\xa9gen" not in self:
            try: self["\xa9gen"] = GENRES[genre - 1]
            except IndexError: pass

    def __parse_tempo(self, atom, data):
        self[atom.name] = cdata.short_be(data[16:18])
    def __render_tempo(self, key, value):
        return self.__render_data(key, 0x15, cdata.to_ushort_be(value))

    def __parse_compilation(self, atom, data):
        try: self[atom.name] = bool(ord(data[16:17]))
        except TypeError: self[atom.name] = False
    def __render_compilation(self, key, value):
        if value:            
            return self.__render_data(key, 0x15, "\x01")
        else: return ""

    def __parse_cover(self, atom, data):
        self[atom.name] = data[16:]
    def __render_cover(self, key, value):
        return self.__render_data(key, 0xD, value)

    def __parse_text(self, atom, data):
        self[atom.name] = data[16:].decode('utf-8', 'replace')
    def __render_text(self, key, value):
        return self.__render_data(key, 0x1, value.encode('utf-8'))

    atoms = {
        "----": (__parse_freeform, __render_freeform),
        "trkn": (__parse_pair, __render_pair),
        "disk": (__parse_pair, __render_pair),
        "gnre": (__parse_genre, None),
        "tmpo": (__parse_tempo, __render_tempo),
        "cpil": (__parse_compilation, __render_compilation),
        "covr": (__parse_cover, __render_cover),
        }

    def pprint(self):
        return "\n".join(["%s=%s" % (key.decode('latin1'), value)
                          for (key, value) in self.iteritems()])

class M4AInfo(object):
    def __init__(self, atoms, fileobj):
        atom = atoms["moov.trak.mdia.mdhd"]
        fileobj.seek(atom.offset)
        data = fileobj.read(atom.length)
        if ord(data[9]) == 0:
            offset = 20
            format = ">2I"
        else:
            offset = 28
            format = ">IQ"
        end = offset + struct.calcsize(format)
        unit, length = struct.unpack(format, data[offset:end])
        self.length = float(length) / unit

    def pprint(self):
        return "MPEG-4 AAC, %.2f seconds" % (self.length)

class M4A(FileType):
    def __init__(self, filename):
        self.filename = filename
        fileobj = file(filename, "rb")
        atoms = Atoms(fileobj)
        self.info = M4AInfo(atoms, fileobj)
        self.tags = M4ATags(atoms, fileobj)

    def score(filename, fileobj, header):
        return ("ftyp" in header) + ("mp4" in header)
    score = staticmethod(score)
