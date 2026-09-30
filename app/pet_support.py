"""Bounded sprite cache and portable, validated preferences for Whale Pet."""
import json
import math
import os
from collections import OrderedDict

from PySide6.QtGui import QImage, QPainter, QPixmap, QFont, QFontDatabase


def configure_fonts(app):
    # The offscreen Qt backend has no system font database on some Windows builds.
    if os.name == 'nt' and 'Microsoft YaHei UI' not in QFontDatabase.families():
        folder = os.path.join(os.environ.get('WINDIR', 'C:\\Windows'), 'Fonts')
        for name in ('msyh.ttc', 'msyhbd.ttc'):
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                QFontDatabase.addApplicationFont(path)
    font = QFont()
    font.setFamilies(['Microsoft YaHei UI', 'PingFang SC', 'Noto Sans CJK SC', 'sans-serif'])
    font.setPointSize(9)
    app.setFont(font)


class FrameCache:
    """Decode each atlas page once and keep at most 64 composed frames.

    Individual animations alternate between pages. Retaining their source pages
    avoids PNG decoding in the animation timer and keeps playback smooth.
    """
    def __init__(self, folder, pages, cell, limit=64):
        self.folder, self.pages, self.cell = folder, pages, cell
        self.limit = limit
        self.frames = OrderedDict()
        self.page_images = []
        for page in pages:
            image = QImage(os.path.join(folder, page['image']))
            if image.isNull():
                raise RuntimeError('Cannot read atlas: ' + page['image'])
            self.page_images.append(image)

    def frame(self, record):
        key = tuple(record.get(k, 0) for k in
                    ('page', 'x', 'y', 'w', 'h', 'offset_x', 'offset_y', 'flip_x'))
        if key in self.frames:
            self.frames.move_to_end(key)
            return self.frames[key]
        page = record.get('page', 0)
        image = QImage(self.cell, self.cell, QImage.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        try:
            if record.get('flip_x'):
                painter.translate(record['offset_x'] + record['w'], record['offset_y'])
                painter.scale(-1, 1)
                x, y = 0, 0
            else:
                x, y = record['offset_x'], record['offset_y']
            painter.drawImage(x, y, self.page_images[page], record['x'], record['y'], record['w'], record['h'])
        finally:
            painter.end()
        pixmap = QPixmap.fromImage(image)
        self.frames[key] = pixmap
        if len(self.frames) > self.limit:
            self.frames.popitem(last=False)
        return pixmap

    def png_frame(self, path):
        key = ('png', path)
        if key not in self.frames:
            pixmap = QPixmap(path)
            if pixmap.isNull():
                raise RuntimeError('Cannot read animation frame: ' + path)
            self.frames[key] = pixmap
            if len(self.frames) > self.limit:
                self.frames.popitem(last=False)
        self.frames.move_to_end(key)
        return self.frames[key]


class LazyFrames:
    def __init__(self, cache, records):
        self.cache, self.records = cache, records

    def __len__(self):
        return len(self.records)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return [self.cache.frame(record) for record in self.records[index]]
        return self.cache.frame(self.records[index])


class PngFrames:
    def __init__(self, cache, paths):
        self.cache, self.paths = cache, paths

    def __len__(self):
        return len(self.paths)

    def __getitem__(self, index):
        if isinstance(index, slice):
            return PngFrames(self.cache, self.paths[index])
        return self.cache.png_frame(self.paths[index])


def finite_number(value, default, lower, upper):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return default
    return min(upper, max(lower, float(value)))


class Preferences:
    """Atomic JSON writes; broken or old preferences never prevent startup."""
    def __init__(self, path):
        self.path = path
        self.error = None

    def load(self):
        try:
            with open(self.path, encoding='utf-8') as source:
                data = json.load(source)
            if not isinstance(data, dict):
                return {}
        except (OSError, ValueError):
            return {}
        clean = {'scale': finite_number(data.get('scale'), 1.0, 0.5, 2.0),
                 'hunger': finite_number(data.get('hunger'), 1.0, 0.0, 1.0)}
        for key in ('bubbles', 'quiet', 'walking', 'dsh_auto_follow'):
            if isinstance(data.get(key), bool):
                clean[key] = data[key]
        if data.get('pet_mode') in ('standalone', 'dsh'):
            clean['pet_mode'] = data['pet_mode']
        for key in ('dsh_home', 'dsh_project', 'dsh_session'):
            if isinstance(data.get(key), str):
                clean[key] = data[key][:4096]
        if isinstance(data.get('screen'), str):
            clean['screen'] = data['screen']
        if isinstance(data.get('x'), int) and not isinstance(data['x'], bool):
            clean['x'] = int(finite_number(data['x'], 0, -1000000, 1000000))
        stamp = data.get('fed_at')
        if isinstance(stamp, (int, float)) and not isinstance(stamp, bool) and math.isfinite(stamp):
            clean['fed_at'] = stamp
        meal = data.get('meal_done')
        if isinstance(meal, list) and len(meal) == 2 and isinstance(meal[0], str) and type(meal[1]) is int:
            clean['meal_done'] = tuple(meal)
        return clean

    def save(self, data):
        temp = self.path + '.tmp'
        try:
            os.makedirs(os.path.dirname(self.path), exist_ok=True)
            with open(temp, 'w', encoding='utf-8') as target:
                json.dump(dict(data, version=1), target, ensure_ascii=False, indent=2, allow_nan=False)
                target.write('\n')
            os.replace(temp, self.path)
            self.error = None
            return True
        except (OSError, ValueError) as error:
            self.error = str(error)
            return False
