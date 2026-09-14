"""Catalogue-indexed TurboSun TIFF source, independent of BKC/BKF."""
DECODER_VERSION = 'turbosun-aes-tiff-1'
STRATEGY = 'TURBOSUN_CATALOGUE_AES_TIFF_V1'


class TurboSunError(ValueError):
    def __init__(self, code, message):
        self.code = code
        super().__init__(message)
