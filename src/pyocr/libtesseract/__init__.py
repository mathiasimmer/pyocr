#!/usr/bin/env python3
'''
libtesseract/ is a wrapper for google's Tesseract-OCR C API
( http://code.google.com/p/tesseract-ocr/ ).

USAGE:
 > from PIL import Image
 > from pyocr.libtesseract import image_to_string
 > print(image_to_string(Image.open('test.png')))
 > print(image_to_string(Image.open('test-european.jpg'), lang='fra'))

COPYRIGHT:
PyOCR is released under the GPL v3.
Copyright (c) Jerome Flesch, 2011-2016
https://github.com/jflesch/pyocr#readme
'''
import itertools
from collections import OrderedDict

from .. import builders

from . import tesseract_raw


__all__ = [
    'can_detect_orientation',
    'detect_orientation',
    'get_available_builders',
    'get_available_languages',
    'get_name',
    'get_version',
    'image_to_string',
    'is_available',
    'TesseractError',
]


def can_detect_orientation():
    return True


def detect_orientation(image, lang=None):
    handle = tesseract_raw.init(lang=lang)
    try:
        tesseract_raw.set_page_seg_mode(
            handle, tesseract_raw.PageSegMode.OSD_ONLY
        )
        tesseract_raw.set_image(handle, image)
        os = tesseract_raw.detect_os(handle)
        if os['confidence'] <= 0:
            raise tesseract_raw.TesseractError(
                "no script", "no script detected"
            )
        orientation = {
            tesseract_raw.Orientation.PAGE_UP: 0,
            tesseract_raw.Orientation.PAGE_RIGHT: 90,
            tesseract_raw.Orientation.PAGE_DOWN: 180,
            tesseract_raw.Orientation.PAGE_LEFT: 270,
        }[os['orientation']]
        return {
            'angle': orientation,
            'confidence': os['confidence']
        }
    finally:
        tesseract_raw.cleanup(handle)


def get_name():
    return "Tesseract (C-API)"


def get_available_builders():
    return [
        builders.TextBuilder,
        builders.WordBoxBuilder,
    ]


def _tess_box_to_pyocr_box(box):
    return (
        (box[0], box[1]),
        (box[2], box[3]),
    )


clevel_od = OrderedDict([
    # plus a special value for everything at once ?
    ("block", tesseract_raw.PageIteratorLevel.BLOCK),
    ("para", tesseract_raw.PageIteratorLevel.PARA),
    ("line", tesseract_raw.PageIteratorLevel.TEXTLINE),
    ("word", tesseract_raw.PageIteratorLevel.WORD),
    ("symbol", tesseract_raw.PageIteratorLevel.SYMBOL),
])


class TestMLI(object):
    def __init__(self):
        self.levels = [3, 2, 1]
        self.boxers = [lambda l, c, p : l,
                       lambda l, c, p : l,
                       lambda l, c, p : l]
        self.base_level = self.levels[-1]
        self.base_boxer = self.boxers[-1]
        self.iterator = itertools.count()
        
    def get_contents(self, level):
        return (self.value, "a", "b")

    def next(self):
        self.value = next(self.iterator)
        if self.value > 100:
            raise StopIteration

    def is_at_beginning(self, level):
        return (self.value % level)

    def is_at_end(self, level, lower_level):
        return ((self.value + 1) % level)

    def nested(self):
        # this consumes the iterator
        to_box = [list() for l in self.levels]
        while True:
            try:
                self.next() # at the base level
            except StopIteration:
                break
            for i in range(len(self.levels)):
                level = self.levels[-1-i]
                boxer = self.boxers[-1-i]
                # print("i, level, boxer", i, level, boxer)
                if level == self.base_level: # same as i == 0
                    to_box[-1-i].append(boxer(*self.get_contents(level)))
                else:
                    lower_level = self.levels[-i]
                    if self.is_at_beginning(level):
                        to_box[-i] = []
                        # can we just put that line under is_at_end ?
                    if self.is_at_end(level, lower_level):
                        # should this be at start ? Any difference ?
                        t, c, p = self.get_contents(level) 
                        to_box[-1-i].append(boxer(to_box[-i], c, p))
               
        return to_box[0]
 

class MultiLevelIterator(builders.MultiLevelIterator):
    def __init__(self, levels, boxers, iterator):
        # ordered list of levels from the highest level to the lowest level
        # check that level_l is sane ? (subset of clevel_od.keys())
        # self.levels = [key for key in clevel_od.keys() if key in levels]
        # for now assume ordered
        self.levels = levels
        self.boxers = boxers
        self.base_level = self.levels[-1]
        self.base_boxer = self.boxers[-1]
        self.iterator = iterator # page or result ??

    def get_contents(self, level):
        text = tesseract_raw.result_iterator_get_utf8_text(
            self.iterator, clevel_od[level]
        )
        confidence = tesseract_raw.result_iterator_get_utf8_text(
            self.iterator, clevel_od[level]
        )
        r, box = tesseract_raw.page_iterator_bounding_box(
            self.iterator, clevel_od[level]
        )
        position = _tess_box_to_pyocr_box(box)
        return (text, confidence, position)
      
    def next(self):
        is_last = not tesseract_raw.page_iterator_next(
            self.iterator, clevel_od[self.base_level]
        )
        if is_last:
            # cleanup here ?
            raise StopIteration
    
    def is_at_beginning(self, level):
        if level not in self.levels:
            raise ValueError("Unknown level")
        else:
            return tesseract_raw.page_iterator_is_at_beginning_of(
                self.iterator, clevel_od[level]
            )

    def is_at_end(self, level, lower_level):
        if (level not in self.levels
         or lower_level not in self.levels):
            raise ValueError("Unknown level")
        else:
            return tesseract_raw.page_iterator_is_at_final_element(
                self.iterator, clevel_od[level], clevel_od[lower_level]
            )

    def nested(self):
        # this consumes the iterator
        to_box = [list() for l in self.levels]
        while True:
            for i in range(len(self.levels)):
                level = self.levels[i]
                boxer = self.boxers[i]
                if level == self.base_level: # same as i == -1
                    to_box[i].append(boxer(*self.get_contents(level)))
                else:
                    lower_level = self.levels[i+1]
                    if self.is_at_beginning(level):
                        to_box[i+1] = []
                        # can we just put that line under is_at_end ?
                    if self.is_at_end(level, lower_level):
                        # should this be at start ? Any difference ?
                        t, c, p = self.get_contents(level) 
                        to_box[i].append(boxer(to_box[i+1], c, p))
            try:
                self.next() # at the base level
            except StopIteration:
                break
               
        return to_box[0] 


def test_mli(image):
    tr = tesseract_raw
    handle = tr.init()
    try:
        tr.set_page_seg_mode(handle, 1)
        tr.set_image(handle, image)
        tr.recognize(handle)
        res_iterator = tr.get_iterator(handle)
        page_iterator = tr.result_iterator_get_page_iterator(res_iterator)
    
        levels = ["line", "word"]
        boxers = [lambda l, c, p : l, lambda t, c, p : t]

        mli = MultiLevelIterator(levels, boxers, page_iterator)
        out = mli.nested()
    finally:
        tr.cleanup(handle)

    return out
 

def image_to_string(image, lang=None, builder=None):
    if builder is None:
        builder = builders.TextBuilder()
    handle = tesseract_raw.init(lang=lang)

    try:
        tesseract_raw.set_page_seg_mode(
            handle, builder.tesseract_layout
        )

        tesseract_raw.set_image(handle, image)

        # XXX(JFlesch): PageIterator and ResultIterator are actually the
        # very same thing. If it changes, we are screwed.
        tesseract_raw.recognize(handle)
        res_iterator = tesseract_raw.get_iterator(handle)
        if res_iterator is None:
            raise tesseract_raw.TesseractError(
                "no script", "no script detected"
            )
        page_iterator = tesseract_raw.result_iterator_get_page_iterator(
            res_iterator
        )

        mli = MultiLevelIterator(
            builder.levels, builders.boxers, page_iterator
        )
        
        out = mli.nested() # where we actually consume the C iterator.
        

    finally:
        tesseract_raw.cleanup(handle)

    return out


def is_available():
    available = tesseract_raw.is_available()
    if not available:
        return False
    version = get_version()
    # C-API with Tesseract <= 3.02 segfaults sometimes
    # (seen with Debian stable + Paperwork)
    # not tested with 3.03
    if (version[0] < 3 or
            (version[0] == 3 and version[1] < 4)):
        return False
    return True


def get_available_languages():
    handle = tesseract_raw.init()
    try:
        return tesseract_raw.get_available_languages(handle)
    finally:
        tesseract_raw.cleanup(handle)


def get_version():
    version = tesseract_raw.get_version()
    version = version.split(" ", 1)[0]
    
    # cut off "dev" string if exists for proper int conversion
    index = version.find("dev")
    if index:
        version = version[:index]

    version = version.split(".")
    major = int(version[0])
    minor = int(version[1])
    upd = 0
    if len(version) >= 3:
        upd = int(version[2])
    return (major, minor, upd)
