# -*- coding: utf-8 -*-
# Copyright (C) 2005-2006  Joe Wreschnig
# Copyright (C) 2006-2007  Lukas Lalinsky
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License version 2 as
# published by the Free Software Foundation.

import struct

from mutagen._util import cdata
from mutagen._compat import text_type, xrange

from ._util import _GUID, _GUID_STR, CODECS, ASFError
from ._attrs import _attribute_types, ASFUnicodeAttribute


class BaseObject(object):
    """Base ASF object."""

    GUID = None
    _TYPES = {}

    def parse(self, asf, data, fileobj, size):
        self.data = data

    def render(self, asf):
        data = self.GUID + struct.pack("<Q", len(self.data) + 24) + self.data
        return data

    @classmethod
    def _register(cls, other):
        cls._TYPES[other.GUID] = other
        return other

    def __repr__(self):
        return "<%s GUID=%s>" % (type(self).__name__, _GUID_STR(self.GUID))


class HeaderObject(object):
    """ASF header."""

    GUID = _GUID("75B22630-668E-11CF-A6D9-00AA0062CE6C")


class UnknownObject(BaseObject):
    """Unknown ASF object."""

    def __init__(self, guid):
        assert isinstance(guid, bytes)
        self.GUID = guid


@BaseObject._register
class ContentDescriptionObject(BaseObject):
    """Content description."""

    GUID = _GUID("75B22633-668E-11CF-A6D9-00AA0062CE6C")

    NAMES = [
        u"Title",
        u"Author",
        u"Copyright",
        u"Description",
        u"Rating",
    ]

    def parse(self, asf, data, fileobj, size):
        super(ContentDescriptionObject, self).parse(asf, data, fileobj, size)
        asf.content_description_obj = self
        lengths = struct.unpack("<HHHHH", data[:10])
        texts = []
        pos = 10
        for length in lengths:
            end = pos + length
            if length > 0:
                texts.append(data[pos:end].decode("utf-16-le").strip(u"\x00"))
            else:
                texts.append(None)
            pos = end

        for key, value in zip(self.NAMES, texts):
            if value is not None:
                value = ASFUnicodeAttribute(value=value)
                asf._tags.setdefault(self.GUID, []).append((key, value))

    def render(self, asf):
        def render_text(name):
            value = asf.to_content_description.get(name)
            if value is not None:
                return text_type(value).encode("utf-16-le") + b"\x00\x00"
            else:
                return b""

        texts = [render_text(x) for x in self.NAMES]
        data = struct.pack("<HHHHH", *map(len, texts)) + b"".join(texts)
        return self.GUID + struct.pack("<Q", 24 + len(data)) + data


@BaseObject._register
class ExtendedContentDescriptionObject(BaseObject):
    """Extended content description."""

    GUID = _GUID("D2D0A440-E307-11D2-97F0-00A0C95EA850")

    def parse(self, asf, data, fileobj, size):
        super(ExtendedContentDescriptionObject, self).parse(
            asf, data, fileobj, size)
        asf.extended_content_description_obj = self
        num_attributes, = struct.unpack("<H", data[0:2])
        pos = 2
        for i in xrange(num_attributes):
            name_length, = struct.unpack("<H", data[pos:pos + 2])
            pos += 2
            name = data[pos:pos + name_length]
            name = name.decode("utf-16-le").strip("\x00")
            pos += name_length
            value_type, value_length = struct.unpack("<HH", data[pos:pos + 4])
            pos += 4
            value = data[pos:pos + value_length]
            pos += value_length
            attr = _attribute_types[value_type](data=value)
            asf._tags.setdefault(self.GUID, []).append((name, attr))

    def render(self, asf):
        attrs = asf.to_extended_content_description.items()
        data = b"".join(attr.render(name) for (name, attr) in attrs)
        data = struct.pack("<QH", 26 + len(data), len(attrs)) + data
        return self.GUID + data


@BaseObject._register
class FilePropertiesObject(BaseObject):
    """File properties."""

    GUID = _GUID("8CABDCA1-A947-11CF-8EE4-00C00C205365")

    def parse(self, asf, data, fileobj, size):
        super(FilePropertiesObject, self).parse(asf, data, fileobj, size)
        length, _, preroll = struct.unpack("<QQQ", data[40:64])
        asf.info.length = (length / 10000000.0) - (preroll / 1000.0)


@BaseObject._register
class StreamPropertiesObject(BaseObject):
    """Stream properties."""

    GUID = _GUID("B7DC0791-A9B7-11CF-8EE6-00C00C205365")

    def parse(self, asf, data, fileobj, size):
        super(StreamPropertiesObject, self).parse(asf, data, fileobj, size)
        channels, sample_rate, bitrate = struct.unpack("<HII", data[56:66])
        asf.info.channels = channels
        asf.info.sample_rate = sample_rate
        asf.info.bitrate = bitrate * 8


@BaseObject._register
class CodecListObject(BaseObject):
    """Codec List"""

    GUID = _GUID("86D15240-311D-11D0-A3A4-00A0C90348F6")

    def _parse_entry(self, data, offset):
        """can raise cdata.error"""

        type_, offset = cdata.uint16_le_from(data, offset)

        units, offset = cdata.uint16_le_from(data, offset)
        # utf-16 code units, not characters..
        next_offset = offset + units * 2
        try:
            name = data[offset:next_offset].decode("utf-16-le").strip("\x00")
        except UnicodeDecodeError:
            name = u""
        offset = next_offset

        units, offset = cdata.uint16_le_from(data, offset)
        next_offset = offset + units * 2
        try:
            desc = data[offset:next_offset].decode("utf-16-le").strip("\x00")
        except UnicodeDecodeError:
            desc = u""
        offset = next_offset

        bytes_, offset = cdata.uint16_le_from(data, offset)
        next_offset = offset + bytes_
        codec = u""
        if bytes_ == 2:
            codec_id = cdata.uint16_le_from(data, offset)[0]
            if codec_id in CODECS:
                codec = CODECS[codec_id]
        offset = next_offset

        return offset, type_, name, desc, codec

    def parse(self, asf, data, fileobj, size):
        super(CodecListObject, self).parse(asf, data, fileobj, size)

        offset = 16
        count, offset = cdata.uint32_le_from(data, offset)
        for i in xrange(count):
            try:
                offset, type_, name, desc, codec = \
                    self._parse_entry(data, offset)
            except cdata.error:
                raise ASFError("invalid codec entry")

            # go with the first audio entry
            if type_ == 2:
                name = name.strip()
                desc = desc.strip()
                asf.info.codec_type = codec
                asf.info.codec_name = name
                asf.info.codec_description = desc
                return


@BaseObject._register
class PaddingObject(BaseObject):
    """Padding object"""

    GUID = _GUID("1806D474-CADF-4509-A4BA-9AABCB96AAE8")


@BaseObject._register
class StreamBitratePropertiesObject(BaseObject):
    """Stream bitrate properties"""

    GUID = _GUID("7BF875CE-468D-11D1-8D82-006097C9A2B2")


@BaseObject._register
class ContentEncryptionObject(BaseObject):
    """Content encryption"""

    GUID = _GUID("2211B3FB-BD23-11D2-B4B7-00A0C955FC6E")


@BaseObject._register
class ExtendedContentEncryptionObject(BaseObject):
    """Extended content encryption"""

    GUID = _GUID("298AE614-2622-4C17-B935-DAE07EE9289C")


@BaseObject._register
class HeaderExtensionObject(BaseObject):
    """Header extension."""

    GUID = _GUID("5FBF03B5-A92E-11CF-8EE3-00C00C205365")

    def parse(self, asf, data, fileobj, size):
        super(HeaderExtensionObject, self).parse(asf, data, fileobj, size)
        asf.header_extension_obj = self
        datasize, = struct.unpack("<I", data[18:22])
        datapos = 0
        self.objects = []
        while datapos < datasize:
            guid, size = struct.unpack(
                "<16sQ", data[22 + datapos:22 + datapos + 24])
            if guid in self._TYPES:
                obj = self._TYPES[guid]()
            else:
                obj = UnknownObject(guid)
            obj.parse(asf, data[22 + datapos + 24:22 + datapos + size],
                      fileobj, size)
            self.objects.append(obj)
            datapos += size

    def render(self, asf):
        data = b"".join(obj.render(asf) for obj in self.objects)
        return (self.GUID + struct.pack("<Q", 24 + 16 + 6 + len(data)) +
                b"\x11\xD2\xD3\xAB\xBA\xA9\xcf\x11" +
                b"\x8E\xE6\x00\xC0\x0C\x20\x53\x65" +
                b"\x06\x00" + struct.pack("<I", len(data)) + data)


@BaseObject._register
class MetadataObject(BaseObject):
    """Metadata description."""

    GUID = _GUID("C5F8CBEA-5BAF-4877-8467-AA8C44FA4CCA")

    def parse(self, asf, data, fileobj, size):
        super(MetadataObject, self).parse(asf, data, fileobj, size)
        asf.metadata_obj = self
        num_attributes, = struct.unpack("<H", data[0:2])
        pos = 2
        for i in xrange(num_attributes):
            (reserved, stream, name_length, value_type,
             value_length) = struct.unpack("<HHHHI", data[pos:pos + 12])
            pos += 12
            name = data[pos:pos + name_length]
            name = name.decode("utf-16-le").strip("\x00")
            pos += name_length
            value = data[pos:pos + value_length]
            pos += value_length
            args = {'data': value, 'stream': stream}
            if value_type == 2:
                args['dword'] = False
            attr = _attribute_types[value_type](**args)
            asf._tags.setdefault(self.GUID, []).append((name, attr))

    def render(self, asf):
        attrs = asf.to_metadata.items()
        data = b"".join([attr.render_m(name) for (name, attr) in attrs])
        return (self.GUID + struct.pack("<QH", 26 + len(data), len(attrs)) +
                data)


@BaseObject._register
class MetadataLibraryObject(BaseObject):
    """Metadata library description."""

    GUID = _GUID("44231C94-9498-49D1-A141-1D134E457054")

    def parse(self, asf, data, fileobj, size):
        super(MetadataLibraryObject, self).parse(asf, data, fileobj, size)
        asf.metadata_library_obj = self
        num_attributes, = struct.unpack("<H", data[0:2])
        pos = 2
        for i in xrange(num_attributes):
            (language, stream, name_length, value_type,
             value_length) = struct.unpack("<HHHHI", data[pos:pos + 12])
            pos += 12
            name = data[pos:pos + name_length]
            name = name.decode("utf-16-le").strip("\x00")
            pos += name_length
            value = data[pos:pos + value_length]
            pos += value_length
            args = {'data': value, 'language': language, 'stream': stream}
            if value_type == 2:
                args['dword'] = False
            attr = _attribute_types[value_type](**args)
            asf._tags.setdefault(self.GUID, []).append((name, attr))

    def render(self, asf):
        attrs = asf.to_metadata_library
        data = b"".join([attr.render_ml(name) for (name, attr) in attrs])
        return (self.GUID + struct.pack("<QH", 26 + len(data), len(attrs)) +
                data)
