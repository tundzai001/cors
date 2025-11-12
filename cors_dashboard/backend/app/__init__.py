#backend/app/__init__.py
from . import utils

# Khởi tạo một đối tượng NMEAParser duy nhất để toàn bộ ứng dụng sử dụng
nmea_parser = utils.NMEAParser()