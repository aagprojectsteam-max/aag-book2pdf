# Synthetic BKF regression data

`bkf-noise.djvu` is a 40 x 40 bitonal page, generated for this project from
`random.Random(4701).randbytes(200)` as a binary PBM pixel payload and encoded
losslessly with DjVuLibre cjb2. It is 310 bytes: its coded payload crosses the
200-byte transform boundary. Tests independently know all 1,600 original bits.
No private sample images or proprietary application material are included.
The BOOK directory/wrapper and inverse transform are generated in tests, with
variable names, offsets, sentinel records and malformed counterparts.
