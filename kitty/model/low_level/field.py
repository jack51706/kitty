# Copyright (C) 2016 Cisco Systems, Inc. and/or its affiliates. All rights reserved.
#
# This file is part of Kitty.
#
# Kitty is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 2 of the License, or
# (at your option) any later version.
#
# Kitty is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with Kitty.  If not, see <http://www.gnu.org/licenses/>.

'''
This module is the "Heart" of the data model.
It contains all the basic building blocks for a Template.
Each "field" type is a discrete component in the full Template.
'''
from random import Random
import copy
import os
import logging
from bitstring import Bits
from kitty.core import KittyObject, KittyException, kassert, khash
from kitty.model.low_level.encoder import ENC_STR_DEFAULT, StrEncoder
from kitty.model.low_level.encoder import ENC_INT_DEFAULT, BitFieldEncoder
from kitty.model.low_level.encoder import ENC_BITS_DEFAULT, BitsEncoder


empty_bits = Bits()


class BaseField(KittyObject):
    '''
    Basic type for all fields and containers, it contains the common logic.
    This class should never be used directly.
    '''

    _encoder_type_ = None

    def __init__(self, value, encoder=ENC_BITS_DEFAULT, fuzzable=True, name=None):
        '''
        :param value: default value
        :type encoder: :class:`~kitty.model.low_levele.encoder.BaseEncoder`
        :param encoder: encoder for the field
        :param fuzzable: is field fuzzable (default: True)
        :param name: name of the object (default: None)
        '''
        super(BaseField, self).__init__(name, logger=logging.getLogger('DataModel'))
        kassert.is_of_types(encoder, self.__class__._encoder_type_)
        self._encoder = encoder
        self._num_mutations = 0
        self._fuzzable = fuzzable
        self._default_value = value
        self._default_rendered = self._encode_value(self._default_value)
        self._current_value = value
        self._current_rendered = self._default_rendered
        self._current_index = -1
        self._enclosing = None
        self._offset = None
        self._initialized = False
        self._hash = None

    def set_offset(self, offset, ctx=None):
        '''
        Set the offset of the field

        :param offset: the offset to set
        :param ctx: rendering context in which the method was called
        :return: the length of the container
        '''
        self._initialize()
        self._offset = offset
        return self.get_length(ctx)

    def _mutating(self):
        return self._current_index != -1

    def get_offset(self):
        '''
        Get the offset of the field

        :return: the length of the container
        '''
        return self._offset

    def get_length(self, ctx):
        '''
        :param ctx: rendering context in which the method was called
        :return: the length of the field
        '''
        return len(self.render(ctx))

    def set_current_value(self, value):
        '''
        Sets the current value of the field

        :param value: value to set
        :return: rendered value
        '''
        self._current_value = value
        self._current_rendered = self._encode_value(self._current_value)
        return self._current_rendered

    def get_current_value(self):
        '''
        :return: current value
        '''
        return self._current_value

    def _last_index(self):
        '''
        :return: last mutation index of this field
        '''
        return self.num_mutations() - 1

    def num_mutations(self):
        '''
        :return: number of mutation in this field
        '''
        return self._num_mutations if self._fuzzable else 0

    def _exhausted(self):
        '''
        :return: True if field exhusted, False otherwise
        '''
        return self._current_index >= self._last_index()

    def skip(self, count):
        '''
        Skip up to [count] cases, default behavior is to just mutate [count] times

        :count: number of cases to skip
        :rtype: int
        :return: number of cases skipped
        '''
        skipped = 0
        for _ in range(count):
            if self.mutate():
                skipped += 1
            else:
                break
        return skipped

    def mutate(self):
        '''
        Mutate the field

        :rtype: boolean
        :return: True if field the mutated
        '''
        self._initialize()
        if self._exhausted():
            return False
        self._current_index += 1
        self._mutate()
        return True

    def _initialize(self):
        if self._initialized:
            return
        self._init()
        self._initialized = True
        self._hash = self.hash()

    def _init(self):
        self.reset()

    def render(self, ctx=None):
        '''
        Render the current value of the field

        :rtype: Bits
        :return: rendered value
        '''
        self._initialize()
        if not self.is_default():
            self._current_rendered = self._encode_value(self._current_value)
        return self._current_rendered

    def reset(self):
        '''
        Reset the field to its default state
        '''
        self._current_index = -1
        self._current_value = self._default_value
        self._current_rendered = self._default_rendered
        self._offset = None if self._enclosing else 0

    def _mutate(self):
        '''
        Perform the actual mutation. The default behavior is to do nothing.
        '''
        pass

    def _get_enclosing_list(self):
        '''
        :return: list of fields from the top of the path to current field
        '''
        alist = [self]
        if self._enclosing:
            alist = self._enclosing._get_enclosing_list() + alist
        return alist

    def get_structure(self):
        return self.get_info()

    def get_info(self):
        '''
        :rtype: dictionary
        :return: field information
        '''
        info = {}
        info['name'] = self.name if self.name else '<no name>'
        info['path'] = info['name']
        info['field type'] = type(self).__name__
        info['value/raw'] = repr(self._current_value)
        info['value/rendered/hex'] = self._current_rendered.tobytes().encode('hex')
        info['value/rendered/base64'] = self._current_rendered.tobytes().encode('base64')[:-1]
        info['value/rendered/length/bits'] = len(self._current_rendered)
        info['value/rendered/length/bytes'] = len(self._current_rendered.tobytes())
        info['value/default'] = repr(self._default_value)
        info['value/offset'] = self.get_offset()
        info['mutation/total number'] = self._num_mutations
        info['mutation/current index'] = self._current_index
        info['mutation/mutating'] = self._mutating()
        info['mutation/fuzzable'] = self._fuzzable
        return info

    def _encode_value(self, value):
        return self._encoder.encode(value)

    def resolve_field(self, field):
        '''
        Resolve a field from name

        :param field: name of the field to resolve
        :rtype: BaseField
        :return: the resolved field
        :raises: KittyException if field could not be resolved
        '''
        if isinstance(field, BaseField):
            return field
        resolved_field = self.scan_for_field(field)
        if not resolved_field:
            container = self._enclosing
            if container:
                resolved_field = container.resolve_field(field)
        if not resolved_field:
            raise Exception('Could not resolve field %s' % field)
        return resolved_field

    def _set_enclosing(self, container):
        '''
        Set the enclosing field of this field
        '''
        self._enclosing = container

    def copy(self):
        '''
        :return: a copy of the field
        '''
        return copy.copy(self)

    def scan_for_field(self, field_name):
        '''
        Scan for field field with given name

        :param field_name: field name to look for
        :return: None
        '''
        if self.get_name() == field_name:
            return self
        else:
            return None

    def get_rendered_fields(self, ctx=None):
        '''
        :return: ordered list of the fields that will be rendered
        '''
        if len(self.render(ctx)):
            return [self]
        return []

    def is_default(self):
        '''
        Checks if the field is in its default form

        :return: True if field is in default form
        '''
        return not self._mutating()

    def __str__(self):
        data = []
        # data.append(hex(id(self)))
        data.append(self.get_name() if self.get_name() else '<no name>')
        data.append(type(self).__name__)
        if self._default_value:
            data.append('default:%s' % self._default_value)
        if self._fuzzable:
            if self._mutating():
                data.append('%s/%s' % (self._current_index, self.num_mutations()))
                data.append('+')
        return '|'.join(data)

    def hash(self):
        '''
        :rtype: int
        :return: hash of the field
        '''
        if self._hash is None:
            self._initialize()
            self._hash = khash(type(self).__name__, self._default_value, self._fuzzable)
        return self._hash

    def _initialize_default_buffer(self):
        self.set_current_value(self._default_value)
        return self._default_rendered


class _MultiListAccessor(object):
    '''
    Wrapper for multiple lists to be accessed as a single list
    Allows skipping of indices
    '''

    def __init__(self):
        self._lists = []
        self._size = 0
        self._to_skip = set([])

    def add_list(self, l):
        self._lists.append(l)
        self._size += len(l)

    def skip_index(self, idx):
        self._to_skip.add(idx)

    def size(self):
        return self._size - len(self._to_skip)

    def get(self, idx):
        if (idx < 0) or (idx >= self.size()):
            raise KittyException('index out of range: %d list length: %d' % (idx, self.size()))
        old_idx = -1
        while old_idx != idx:
            new_idx = idx + len([x for x in self._to_skip if ((old_idx < x) and (x <= idx))])
            old_idx = idx
            idx = new_idx
        for i in range(len(self._lists)):
            if idx < len(self._lists[i]):
                return self._lists[i][idx]
            idx -= len(self._lists[i])


class _LibraryField(BaseField):
    '''
    Base class for a field with mutations from a library.
    there are two libraries for each instance:
    1. Shared library between all instances
    2. Instance library with mutations that are specific for this instance
    '''

    def __init__(self, value, encoder, fuzzable=True, name=None):
        super(_LibraryField, self).__init__(value, encoder, fuzzable, name)
        self._lib = None
        self._initialize()

    def skip(self, count):
        '''
        skip up to [count] cases

        :param count: number of cases to skip
        :rtype: int
        :return: number of cases skipped
        '''
        self._initialize()
        skipped = 0
        if not self._exhausted():
            skipped = min(count, self._last_index() - self._current_index)
            self._current_index += skipped
        return skipped

    def _mutate(self):
        value = self._lib.get(self._current_index)
        self._current_value = value

    def _init(self):
        lib = _MultiListAccessor()
        lib.add_list(self._get_local_lib())
        lib.add_list(self._wrap_get_class_lib())
        self._lib = lib
        self._filter_lib()
        self._num_mutations = self._lib.size()

    def _filter_lib(self):
        pass

    def _get_local_lib(self):
        '''
        :rtype: list
        :return: list of local lib
        '''
        self.not_implemented('_get_local_lib')

    def _wrap_get_class_lib(self):
        if self.__class__.lib:
            return self.__class__.lib
        else:
            self.__class__.lib = self._get_class_lib()
            return self.__class__.lib

    def _get_class_lib(self):
        '''
        :rtype: list
        :return: list of class lib
        '''
        self.not_implemented('_get_class_lib')


class Static(BaseField):
    '''
    A static field does not mutate. It is used for constant parts of the model
    '''
    _encoder_type_ = StrEncoder

    def __init__(self, value, encoder=ENC_STR_DEFAULT, name=None):
        '''
        :type value: str
        :param value: default value
        :type encoder: :class:`~kitty.model.low_levele.encoder.StrEncoder`
        :param encoder: encoder for the field (default: ENC_STR_DEFAULT)
        :param name: name of the object (default: None)

        :example:

            ::

                Static('this will never change')
        '''
        super(Static, self).__init__(value=value, encoder=encoder, fuzzable=False, name=name)


def gen_power_list(val, min_power=0, max_power=10):
    return [val * (2 ** i) for i in range(min_power, max_power + 1)]


class String(_LibraryField):
    '''
    Represent a string, the mutation target common string-related vulnerabilities
    '''

    _encoder_type_ = StrEncoder
    lib = None

    def __init__(self, value, max_size=None, encoder=ENC_STR_DEFAULT, fuzzable=True, name=None):
        '''
        :type value: str
        :param value: default value
        :param max_size: maximal size of the string before encoding (default: None)
        :type encoder: :class:`~kitty.model.low_levele.encoder.StrEncoder`
        :param encoder: encoder for the field
        :param fuzzable: is field fuzzable (default: True)
        :param name: name of the object (default: None)

        :example:

            ::

                String('this is the default value', max_size=5)
        '''
        self._max_size = None if max_size is None else max_size
        if isinstance(value, unicode):
            value = value.encode('utf-8')
        super(String, self).__init__(value=value, encoder=encoder, fuzzable=fuzzable, name=name)

    def _get_local_lib(self):
        lib = []
        for i in [2, 10, 100]:
            lib.append(self._default_value * i)
            lib.append(self._default_value * i + '\xfe')
        lib.append('\x00' + self._default_value)
        lib.append(self._default_value + '\x00')
        return lib

    def _get_class_lib(self):
        lib = []
        lib.append('')
        # format strings
        for s in ['%s', '%%s', '"%s"', '%n', '%%n', '"%n"', '\r\n', '\n']:
            lib.extend(gen_power_list(s, max_power=10))
        for s in ['\x00', '\xde\xad\xbe\xef']:
            lib.extend(gen_power_list(s, max_power=13))
        # *nix command injection
        lib.append('|touch /tmp/KITTY')
        lib.append(';touch /tmp/KITTY;')
        lib.append(';ls>/tmp/KITTY')
        lib.append('";ls>/tmp/KITTY;ls>"/dev/null"')
        lib.append('\';ls>/tmp/KITTY;ls>\'/dev/null\'')
        # windows command injection
        lib.append('|notepad')
        lib.append(';notepad;')
        lib.append('\nnotepad\n')
        # sql injection
        lib.append('1;SELECT%20*')
        lib.append('\'sqlattempt1')
        lib.append('(sqlattempt2)')
        lib.append('OR%201=1')
        # paths and path traversal
        lib.append('/.:/' + 'A' * 5000 + '\x00\x00')
        lib.append('/.../' + 'A' * 5000 + '\x00\x00')
        lib.append('/.../.../.../.../.../.../.../.../.../.../')
        lib.append('/../../../../../../../../../../../../etc/passwd')
        lib.append('/../../../../../../../../../../../../boot.ini')
        lib.append('..:..:..:..:..:..:..:..:..:..:..:..:..:')
        lib.append('\\\\*')
        lib.append('\\\\?\\')
        lib.extend(gen_power_list('/\\', max_power=9))
        lib.extend(gen_power_list('/.', max_power=9))
        lib.append('!@#$%%^#$%#$@#$%$$@#$%^^**(()')
        lib.append('%01%02%03%04%0a%0d%0aADSF')
        lib.append('%01%02%03@%04%0a%0d%0aADSF')
        lib.append('/%00/')
        lib.append('%00/')
        lib.append('%00')
        lib.append('%u0000')
        lib.append('%\xfe\xf0%\x00\xff')
        lib.extend(gen_power_list('%\xfe\xf0%\x01\xff', max_power=5))
        lib.extend(self._add_strings_from_file('./kitty_strings.txt'))
        return lib

    def _filter_lib(self):
        if self._max_size is not None:
            for i in range(self._lib.size(), 0, -1):
                i -= 1
                val = self._lib.get(i)
                if len(val) > self._max_size:
                    self._lib.skip_index(i)
            self._num_mutations = self._lib.size()

    def _add_strings_from_file(self, file_name):
        res = []
        if os.path.exists(file_name):
            try:
                with open(file_name, 'rb') as f:
                    for line in f:
                        if line.endswith('\n'):
                            line = line[:-1]
                        res.append(line)
            except Exception as e:
                self.logger.warning('Could not read strings from file %s: %s' % (file_name, e))
        else:
            self.logger.info('No strings file [%s]' % file_name)
        return res

    def hash(self):
        '''
        :rtype: int
        :return: hash of the field
        '''
        hashed = super(String, self).hash()
        return khash(hashed, self._max_size)


class Delimiter(String):
    '''
    Represent a text delimiter, the mutations target common delimiter-related vulnerabilities
    '''
    _encoder_type_ = StrEncoder
    lib = None

    def __init__(self, value, max_size=None, fuzzable=True, name=None):
        '''
        :type value: str
        :param value: default value
        :param max_size: maximal size of the string before encoding (default: None)
        :type encoder: :class:`~kitty.model.low_levele.encoder.StrEncoder`
        :param encoder: encoder for the field (default: ENC_STR_DEFAULT)
        :param fuzzable: is field fuzzable (default: True)
        :param name: name of the object (default: None)

        :example:

            ::

                Delimiter('=', max_size=30, encoder=ENC_STR_BASE64)
        '''
        super(Delimiter, self).__init__(value=value, max_size=max_size, fuzzable=fuzzable, name=name)

    def _get_class_lib(self):
        lib = []
        delims = ' \t!@#$%^&*()-_+=:;\'"/\\?<>.,\r\n'
        for delim in delims:
            lib.extend(gen_power_list(delim, max_power=2))
        lib.extend(gen_power_list('\r\n', max_power=3))
        lib.extend(gen_power_list('\t\r\n', max_power=3))
        lib.append('')
        return lib


class BitField(_LibraryField):
    '''
    Represents a fixed-length sequence of bits, the mutations target common integer related vulnerabilities

        .. note::

            Since BitField is frequently used in binary format, multiple aliases were created for it. See aliases.py for more details.
    '''
    _encoder_type_ = BitFieldEncoder
    lib = None

    def __init__(self, value, length, signed=False, min_value=None, max_value=None, encoder=ENC_INT_DEFAULT, fuzzable=True, name=None):
        '''
        :type value: int
        :param value: default value
        :type length: positive int
        :param length: length of field in bits
        :param signed: are the values signed (default: False)
        :param min_value: minimal allowed value (default: None)
        :param max_value: maximal allowed value (default: None)
        :type encoder: :class:`~kitty.model.low_levele.encoder.BitFieldEncoder`
        :param encoder: encoder for the field
        :param fuzzable: is field fuzzable (default: True)
        :param name: name of the object (default: None)

        :examples:

            ::

                BitField(123, length=15, signed=True, max_value=1000)
                UInt8(17, encoder=ENC_INT_DEC)
        '''
        self._length = length
        self._signed = signed
        self._min_value = None
        self._max_value = None
        self._max_min_diff = None
        self._calc_bounds(value, min_value, max_value)
        super(BitField, self).__init__(value=value, encoder=encoder, fuzzable=fuzzable, name=name)

    def _calc_bounds(self, value, minv, maxv):
        if self._length <= 0:
            raise KittyException('length (%d) <= 0' % (self._length))
        max_possible = 2 ** self._length - 1
        if self._signed:
            self._min_value = ~(max_possible >> 1)
        else:
            self._min_value = 0
        self._max_value = max_possible + self._min_value
        self._max_min_diff = max_possible
        if maxv is not None:
            if maxv > self._max_value:
                raise KittyException('max_value is too big %d > %d' % (maxv, self._max_value))
            self._max_value = maxv
        if minv is not None:
            if minv < self._min_value:
                raise KittyException('min_value is too small %d < %d' % (minv, self._min_value))
            self._min_value = minv
        if self._min_value > self._max_value:
            raise KittyException('min_value (%d) > max_value (%d)' % (self._min_value, self._max_value))
        if (value < self._min_value) or (value > self._max_value):
            raise KittyException('default value (%d) not in range (min=%d, max=%d)' % (value, self._min_value, self._max_value))

    def _get_local_lib(self):
        lib = []
        for i in range(self._length):
            lib.append(lambda x, i=i: x._default_value ^ (1 << i))
        return lib

    def _get_class_lib(self):
        '''
        If the range is from a to b, we try a few numbers around the arrows
        a                                           b
        +-------------------------------------------+
        ^          ^          ^          ^          ^
        '''
        lib = []
        num_sections = 4
        for i in range(5):
            lib.append(lambda x, i=i: x._min_value + i)
            lib.append(lambda x, i=i: x._max_value - i)
            for s in range(1, num_sections):
                lib.append(lambda x, i=i, s=s: x._max_value - (x._max_min_diff / num_sections) * s + i)
                lib.append(lambda x, i=i, s=s: x._max_value - (x._max_min_diff / num_sections) * s - i)
        # off-by-N
        for i in range(1, 5):
            lib.append(lambda x, i=i: x._default_value + i)
            lib.append(lambda x, i=i: x._default_value - i)
        self._add_ints_from_file('./kitty_integers.txt')
        return lib

    def _mutate(self):
        func = self._lib.get(self._current_index)
        self._current_value = func(self)

    def _encode_value(self, value):
        return self._encoder.encode(value, self._length, self._signed)

    def _filter_lib(self):
        vals = []
        for i in range(self._lib.size(), 0, -1):
            i -= 1
            func = self._lib.get(i)
            res = func(self)
            if res in vals:
                self._lib.skip_index(i)
            elif (res < self._min_value) or (res > self._max_value):
                self._lib.skip_index(i)
            else:
                vals.append(res)
        self._num_mutations = self._lib.size()

    def _add_ints_from_file(self, file_name):
        res = []
        if os.path.exists(file_name):
            try:
                with open(file_name, 'rb') as f:
                    for line in f:
                        if line.endswith('\n'):
                            line = line[:-1]

                        def func(_, i=int(line, 0)):
                            return i
                        res.append(func)
            except Exception as e:
                self.logger.warning('Could not read integers from file %s: %s' % (file_name, e))
        else:
            self.logger.info('No integers file [%s]' % file_name)
        return res

    def hash(self):
        '''
        :rtype: int
        :return: hash of the field
        '''
        hashed = super(BitField, self).hash()
        return khash(hashed, self._length, self._signed, self._min_value, self._max_value)


class Group(_LibraryField):
    '''
    A field with fixed set of possible mutations
    '''
    _encoder_type_ = StrEncoder
    lib = None

    def __init__(self, values, encoder=ENC_STR_DEFAULT, fuzzable=True, name=None):
        '''
        :type values: list of strings
        :param values: possible values for the field
        :type encoder: :class:`~kitty.model.low_levele.encoder.StrEncoder`
        :param encoder: encoder for the field (default: ENC_STR_DEFAULT)
        :param fuzzable: is field fuzzable (default: True)
        :param name: name of the object (default: None)

        :example:

            ::

                Group(['GET', 'PUT', 'POST'], name='http methods')
        '''
        self._values = values
        super(Group, self).__init__(values[0], encoder, fuzzable, name)

    def _get_local_lib(self):
        return self._values[:]

    def _get_class_lib(self):
        return []

    def hash(self):
        '''
        :rtype: int
        :return: hash of the field
        '''
        hashed = super(Group, self).hash()
        return khash(hashed, frozenset(self._values))


class Dynamic(BaseField):
    '''
    A field that gets its value from the fuzzer at runtime
    '''
    _encoder_type_ = StrEncoder

    def __init__(self, key, default_value, length=None, encoder=ENC_STR_DEFAULT, fuzzable=False, name=None):
        '''
        :type key: str
        :param key: key for the data in the session_data dictionary
        :type default_value: str
        :param default_value: default value of the field
        :param length: length of the field in bytes. must be set if fuzzable=True (default: None)
        :type encoder: :class:`~kitty.model.low_levele.encoder.StrEncoder`
        :param encoder: encoder for the field (default: ENC_STR_DEFAULT)
        :param fuzzable: is field fuzzable (default: False)
        :param name: name of the object (default: None)

        :examples:

            ::

                Dynamic(key='session id', default_value='\x01\x02\x03\x04')
                Dynamic(key='session id', default_value='\x01\x02\x03\x04', length=4, fuzzable=True)
        '''
        self._key = key
        super(Dynamic, self).__init__(value=default_value, encoder=encoder, fuzzable=fuzzable, name=name)
        self._length = length
        if self._fuzzable:
            kassert.is_int(self._length)
            self._num_mutations = self._length * 8
        self._last_value = default_value

    def render(self, ctx=None):
        self._initialize()
        if self._mutating():
            xor_bits = Bits(uint=1 << self._current_index, length=self._length * 8)
            self._current_rendered = self._current_rendered ^ xor_bits
        return self._current_rendered

    def skip(self, count):
        self._initialize()
        skipped = 0
        if not self._exhausted():
            skipped = min(count, self.num_mutations() - self._current_index - 1)
            self._current_index += skipped
        return skipped

    def set_session_data(self, session_data):
        if self._key in session_data:
            self.set_current_value(session_data[self._key])
            return True
        return False

    def hash(self):
        '''
        :rtype: int
        :return: hash of the field
        '''
        hashed = super(Dynamic, self).hash()
        return khash(hashed, self._key, self._length)

    def is_default(self):
        '''
        Checks if the field is in its default form

        :return: True if field is in default form
        '''
        return False


class RandomBits(BaseField):
    '''
    A random sequence of bits.
    The length of the sequence is between *min_length* and *max_length*,
    and decided either randomally (if *step* is *None*)
    or starts from *min_length* and inreased by *step* bits (if *step* has a value).
    '''
    _encoder_type_ = BitsEncoder

    def __init__(
            self, value, min_length, max_length, unused_bits=0,
            seed=1235, num_mutations=25, step=None, encoder=ENC_BITS_DEFAULT,
            fuzzable=True, name=None):
        '''
        :type value: str
        :param value: default value, the last *unsused_bits* will be removed from the value
        :param min_length: minimal length of the field (in bits)
        :param max_length: maximal length of the field (in bits)
        :param unused_bits: how many bits from the value are not used (default: 0)
        :param seed: seed for the random number generator, to allow consistency between runs (default: 1235)
        :param num_mutations: number of mutations to perform (if step is None) (default:25)
        :type step: int
        :param step: step between lengths of each mutation (default: None)
        :type encoder: :class:`~kitty.model.low_levele.encoder.BitsEncoder`
        :param encoder: encoder for the field (default: ENC_BITS_DEFAULT)
        :param fuzzable: is field fuzzable (default: True)
        :param name: name of the object (default: None)

        :examples:

            ::

                RandomBits(value='1234', min_length=0, max_length=75, unused_bits=0, step=15)
                RandomBits(value='1234', min_length=0, max_length=75, unused_bits=3, num_mutations=80)
        '''
        if unused_bits not in range(8):
            raise KittyException('unused bits (%d) is not between 0-7' % unused_bits)
        value = Bits(bytes=value)
        if unused_bits:
            value = value[:-unused_bits]
        super(RandomBits, self).__init__(value=value, encoder=encoder, fuzzable=fuzzable, name=name)
        self._validate_lengths(min_length, max_length)
        self._min_length = min_length
        self._max_length = max_length
        self._num_mutations = num_mutations
        self._step = step
        self._random = Random()
        self._seed = seed
        self._random.seed(self._seed)
        if self._step:
            if self._step < 0:
                raise KittyException('step (%d) < 0' % (step))
            self._num_mutations = (self._max_length - self._min_length) / self._step

    def _validate_lengths(self, min_length, max_length):
        kassert.is_int(min_length)
        kassert.is_int(max_length)
        if min_length > max_length:
            raise KittyException('min_length(%d) > max_length(%d)' % (min_length, max_length))
        elif min_length < 0:
            raise KittyException('min_length(%d) < 0' % (min_length))
        elif max_length <= 0:
            raise KittyException('max_length(%d) < 0' % (max_length))

    def reset(self):
        super(RandomBits, self).reset()
        self._random.seed(self._seed)

    def _mutate(self):
        if self._step:
            length = self._min_length + self._step * self._current_index
        else:
            length = self._random.randint(self._min_length, self._max_length)
        current_bytes = ''
        for i in range(length / 8 + 1):
            current_bytes += chr(self._random.randint(0, 255))
        self._current_value = Bits(bytes=current_bytes)[:length]

    def hash(self):
        '''
        :rtype: int
        :return: hash of the field
        '''
        hashed = super(RandomBits, self).hash()
        return khash(hashed, self._min_length, self._max_length, self._num_mutations, self._step, self._seed)


class RandomBytes(BaseField):
    '''
    A random sequence of bytes The length of the sequence is between *min_length* and *max_length*,
    and decided either randomally (if *step* is *None*) or starts from *min_length* and inreased by
    *step* bytes (if *step* has a value).
    '''
    _encoder_type_ = StrEncoder

    def __init__(self, value, min_length, max_length, seed=1234, num_mutations=25, step=None, encoder=ENC_STR_DEFAULT, fuzzable=True, name=None):
        '''
        :type value: str
        :param value: default value
        :param min_length: minimal length of the field (in bytes)
        :param max_length: maximal length of the field (in bytes)
        :param seed: seed for the random number generator, to allow consistency between runs (default: 1234)
        :param num_mutations: number of mutations to perform (if step is None) (default:25)
        :type step: int
        :param step: step between lengths of each mutation (default: None)
        :type encoder: :class:`~kitty.model.low_levele.encoder.StrEncoder`
        :param encoder: encoder for the field (default: ENC_STR_DEFAULT)
        :param fuzzable: is field fuzzable (default: True)
        :param name: name of the object (default: None)

        :examples:

            ::

                RandomBytes(value='1234', min_length=0, max_length=75, step=15)
                RandomBytes(value='1234', min_length=0, max_length=75, num_mutations=80)
        '''
        super(RandomBytes, self).__init__(value=value, encoder=encoder, fuzzable=fuzzable, name=name)
        self._validate_lengths(min_length, max_length)
        self._min_length = min_length
        self._max_length = max_length
        self._num_mutations = num_mutations
        self._step = step
        self._random = Random()
        self._seed = seed
        self._random.seed(self._seed)
        if self._step:
            if self._step < 0:
                raise KittyException('step (%d) < 0' % (step))
            self._num_mutations = (self._max_length - self._min_length) / self._step

    def _validate_lengths(self, min_length, max_length):
        kassert.is_int(min_length)
        kassert.is_int(max_length)
        if min_length > max_length:
            raise KittyException('min_length(%d) > max_length(%d)' % (min_length, max_length))
        elif min_length < 0:
            raise KittyException('min_length(%d) < 0' % (min_length))
        elif max_length <= 0:
            raise KittyException('max_length(%d) < 0' % (max_length))

    def reset(self):
        super(RandomBytes, self).reset()
        self._random.seed(self._seed)

    def _mutate(self):
        if self._step:
            length = self._min_length + self._step * self._current_index
        else:
            length = self._random.randint(self._min_length, self._max_length)
        current = ''
        for i in range(length):
            current += chr(self._random.randint(0, 255))
        self._current_value = current

    def hash(self):
        '''
        :rtype: int
        :return: hash of the field
        '''
        hashed = super(RandomBytes, self).hash()
        return khash(hashed, self._min_length, self._max_length, self._num_mutations, self._step, self._seed)
